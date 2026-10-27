# Hosting the CodeSentinel demo

The public demo is one container: `Dockerfile.web` at the repo root. It serves
the judge UI (`web/`) and runs the audit cascade in the same process, so a
judge opens one URL and gets a live Token Factory run with a receipt.

- Live when `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set at runtime. Never bake keys into the image.
- `GET /health` for health checks, `GET /api/status` for budget and limits.
- Spend guards (env, defaults in the image):
  - `MAX_DAILY_USD=3`: daily Token Factory cap. Over it, bundled charts show the labeled cached run.
  - `LIVE_RATE_PER_HOUR=10`: live runs per visitor IP per hour.
  - `LIVE_MAX_CONCURRENT=3`: live cascades at once; extra visitors get the cached run.
  - `TRUSTED_PROXY_HOPS=1`: which `X-Forwarded-For` entry (from the right) is the visitor, so a forged header can't reset the IP limit.
- One live Margaret Whitmore audit measured about $0.013–0.016 of Token Factory spend, 15 calls, ~20–26 s.

## Image

`.github/workflows/web-image.yml` tests, builds and pushes
`ghcr.io/kishanraj41/codesentinel-web:latest` (and `:<sha>`) on every push to
`main` or `feat/live-fixes`. No local Docker needed.

GHCR packages start private. Either make it public once
(github.com/users/kishanraj41/packages/container/codesentinel-web/settings >
Change visibility > Public) or pass registry credentials to the host.

## Nebius AI Cloud: Serverless AI endpoint (recommended)

1. Sign in at https://console.nebius.com with Google, GitHub or Microsoft and add billing
   (a card; Nebius charges $25 and credits it to the balance). AI Cloud is a separate account and
   balance from Token Factory.
2. Install and log in to the CLI:
   ```bash
   curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash && exec -l $SHELL
   nebius profile create      # opens a browser to sign in; pick tenant + project
   ```
3. Deploy (keys come from your shell, go into MysteryBox, and are never printed):
   ```bash
   export NEBIUS_API_KEY=... TAVILY_API_KEY=...
   ./scripts/deploy_nebius.sh
   ```
   This creates a CPU endpoint (`cpu-d3`, `2vcpu-8gb`, 20 GiB disk, no auth token so judges can open it)
   and prints the managed `https://` URL once `/health` answers.

Console alternative (no CLI): Serverless AI > Endpoints > Create endpoint > Custom.
Image `ghcr.io/kishanraj41/codesentinel-web:latest`, port `8080`, secret env vars
`NEBIUS_API_KEY` and `TAVILY_API_KEY`, authentication off, no GPU, `cpu-d3` / `2vcpu-8gb`,
disk 20 GiB, then copy the `https://` public endpoint.

Update: push to the branch, wait for the workflow, then `nebius ai endpoint restart --id <id>`
(the endpoint re-pulls `:latest`, same URL).

Cost: cpu-d3 is $0.015 per vCPU-hour + $0.0045 per GiB-hour (from Oct 1, 2026), so `2vcpu-8gb`
is about $0.066/h (~$1.60/day) plus ~$1.42/month for a 20 GiB disk. A stopped endpoint is not
billed and keeps its URL (`nebius ai endpoint stop|start --id <id>`).

## Free fallback: Render

`render.yaml` is a Render Blueprint. Sign in to https://render.com with GitHub, New > Blueprint,
pick this repo, enter the two keys when prompted. Free instances sleep after 15 minutes idle and
take about a minute to wake, so open the URL before a judging session.
