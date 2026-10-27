#!/usr/bin/env bash
# Deploy the CodeSentinel web demo as a Nebius Serverless AI endpoint (CPU).
#
# Prereqs (one time):
#   curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash
#   nebius profile create            # browser sign-in to the Nebius AI Cloud console
#   export NEBIUS_API_KEY=... TAVILY_API_KEY=...   # Token Factory + Tavily keys
#
# The image comes from GitHub Actions (.github/workflows/web-image.yml).
# If the GHCR package is private, also export GHCR_USER and GHCR_TOKEN
# (a GitHub PAT with read:packages) and they are passed as registry creds.
#
# Keys are stored in Nebius MysteryBox and injected with --env-secret; they are
# never printed. The endpoint has no auth token so a judge can open it cold;
# spend is bounded by MAX_DAILY_USD, LIVE_RATE_PER_HOUR and LIVE_MAX_CONCURRENT.
set -euo pipefail

NAME="${NAME:-codesentinel-web}"
IMAGE="${IMAGE:-ghcr.io/kishanraj41/codesentinel-web:latest}"
SECRET_NAME="${SECRET_NAME:-codesentinel-keys}"
PLATFORM="${PLATFORM:-cpu-d3}"
PRESET="${PRESET:-2vcpu-8gb}"   # ~$0.066/h; use 4vcpu-16gb if 2vcpu is rejected
DISK="${DISK:-20Gi}"
MAX_DAILY_USD="${MAX_DAILY_USD:-3}"
LIVE_RATE_PER_HOUR="${LIVE_RATE_PER_HOUR:-10}"
LIVE_MAX_CONCURRENT="${LIVE_MAX_CONCURRENT:-3}"
TRUSTED_PROXY_HOPS="${TRUSTED_PROXY_HOPS:-1}"

: "${NEBIUS_API_KEY:?export NEBIUS_API_KEY first}"
: "${TAVILY_API_KEY:?export TAVILY_API_KEY first}"
command -v nebius >/dev/null || { echo "Install the nebius CLI first" >&2; exit 1; }
PARENT_ID="${PARENT_ID:-$(nebius config get parent-id 2>/dev/null || true)}"
: "${PARENT_ID:?set PARENT_ID to your Nebius project ID (project-...)}"

if nebius mysterybox secret get-by-name --parent-id "$PARENT_ID" --name "$SECRET_NAME" >/dev/null 2>&1; then
  echo "MysteryBox secret $SECRET_NAME exists; reusing it."
else
  echo "Creating MysteryBox secret $SECRET_NAME (values not shown)."
  payload_file="$(mktemp)"
  trap 'rm -f "$payload_file"' EXIT
  python3 - "$payload_file" <<'PY'
import json, os, sys
with open(sys.argv[1], "w") as fh:
    json.dump([
        {"key": "NEBIUS_API_KEY", "string_value": os.environ["NEBIUS_API_KEY"]},
        {"key": "TAVILY_API_KEY", "string_value": os.environ["TAVILY_API_KEY"]},
    ], fh)
PY
  nebius mysterybox secret create --parent-id "$PARENT_ID" --name "$SECRET_NAME" \
    --secret-version-payload "$(cat "$payload_file")" >/dev/null
fi

registry_args=()
if [[ -n "${GHCR_USER:-}" && -n "${GHCR_TOKEN:-}" ]]; then
  registry_args=(--registry-username "$GHCR_USER" --registry-password "$GHCR_TOKEN")
fi

echo "Creating endpoint $NAME from $IMAGE on $PLATFORM/$PRESET (takes ~5 min)."
nebius ai endpoint create \
  --parent-id "$PARENT_ID" \
  --name "$NAME" \
  --image "$IMAGE" \
  ${registry_args[@]+"${registry_args[@]}"} \
  --container-port 8080 \
  --env "PORT=8080" \
  --env "MAX_DAILY_USD=$MAX_DAILY_USD" \
  --env "LIVE_RATE_PER_HOUR=$LIVE_RATE_PER_HOUR" \
  --env "LIVE_MAX_CONCURRENT=$LIVE_MAX_CONCURRENT" \
  --env "TRUSTED_PROXY_HOPS=$TRUSTED_PROXY_HOPS" \
  --env-secret "NEBIUS_API_KEY=$SECRET_NAME" \
  --env-secret "TAVILY_API_KEY=$SECRET_NAME" \
  --platform "$PLATFORM" \
  --preset "$PRESET" \
  --disk-size "$DISK" \
  --on-demand >/dev/null

id="$(nebius ai endpoint get-by-name --parent-id "$PARENT_ID" --name "$NAME" --format jsonpath='{.metadata.id}')"
echo "Endpoint id: $id"
for _ in $(seq 1 60); do
  url="$(nebius ai endpoint get "$id" --format json | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(next((u for u in d.get("status", {}).get("public_endpoints", []) if u.startswith("https://")), ""))')"
  if [[ -n "$url" ]] && curl -fsS "$url/health" >/dev/null 2>&1; then
    echo "Live: $url"
    curl -fsS "$url/api/status"; echo
    exit 0
  fi
  sleep 10
done
echo "Endpoint not healthy yet. Check: nebius ai endpoint get $id" >&2
exit 1
