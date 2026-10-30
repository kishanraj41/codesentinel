# Hosting the CodeSentinel demo

The public demo is one container: `Dockerfile.web` at the repo root. It serves
the judge UI (`web/`) and runs the audit cascade in the same process, so a
judge opens one URL and gets a live Token Factory run with a receipt.

- Live when `NEBIUS_API_KEY` and `TAVILY_API_KEY` are set at runtime. Never bake keys into the image.
- `GET /health` for health checks, `GET /api/status` for budget and limits.
- Spend guards (env, defaults in the image):
  - `MAX_DAILY_USD=3`: daily Token Factory cap. Over it, bundled charts show the labeled cached run.
  - `LIVE_RATE_PER_HOUR=40`: live runs per visitor IP per hour.
  - `LIVE_MAX_CONCURRENT=3`: live cascades at once; extra visitors get the cached run.
- Whenever a response comes from the cache, the UI shows an amber banner above the worklist (for example "Showing a cached result: live capacity reached, try again shortly."), so a cached run is never mistaken for a live one.
  - `TRUSTED_PROXY_HOPS=1`: which `X-Forwarded-For` entry (from the right) is the visitor, so a forged header can't reset the IP limit.
- One live Margaret Whitmore audit measured about $0.013–0.016 of Token Factory spend, 15 calls, ~20–26 s.

## Image

`.github/workflows/web-image.yml` tests, builds and pushes
`ghcr.io/kishanraj41/codesentinel-web:latest` (and `:<sha>`) on every push to
`main` or `feat/live-fixes`. No local Docker needed.

GHCR packages start private. Either make it public once
(github.com/users/kishanraj41/packages/container/codesentinel-web/settings >
Change visibility > Public) or pass registry credentials to the host.

## Render (free tier)

The demo is hosted on Render's free tier. Inference runs on Nebius Token Factory; Render only
serves the container.

`render.yaml` is a Render Blueprint. Sign in to https://render.com with GitHub, New > Blueprint,
pick this repo, and enter `NEBIUS_API_KEY` and `TAVILY_API_KEY` when prompted (they are stored as
Render secrets, not in the repo). Render builds `Dockerfile.web` from `main` and checks
`/health`.

Free instances sleep after 15 minutes idle and take about a minute to wake. Demo URL:
[codesentinel-bno0.onrender.com](https://codesentinel-bno0.onrender.com).

## Keep-alive

`.github/workflows/keepalive.yml` runs every 10 minutes (and on demand from the Actions tab,
`workflow_dispatch`). It reads the repository variable `DEMO_URL` and curls `<DEMO_URL>/health`,
falling back to `<DEMO_URL>/`. If `DEMO_URL` is unset, it exits 0 without making a request.

After deploying, Kishan sets the repository variable: GitHub > Settings > Secrets and variables >
Actions > Variables > New repository variable, name `DEMO_URL`, value the Render URL
`https://codesentinel-bno0.onrender.com` (no trailing path). It is a variable, not a secret; it holds no key.
Scheduled workflows run from the default branch (`main`), so the keep-alive starts once this file is
on `main`. GitHub may delay scheduled runs by a few minutes under load, and Render's free tier has a
monthly instance-hour allowance, so a 24/7 keep-alive uses most of it; disable the workflow after judging.
