# CodeSentinel web demo

Judge path: open the app, choose **Margaret Whitmore**, click **Audit chart**. No login.

The worklist is the action. Evidence opens under each code. **Dropped by Nemotron** explains the refusals. The timeline names the model for each step, and the receipt shows tokens, latency, and catalog cost.

Audits follow contract **1.0.0**. **Audit chart** POSTs to `/audit`. With no `AUDIT_API_BASE`, that call stays in this process and the cascade runs in mock mode (no Token Factory key). Set `AUDIT_API_BASE` to point at a separate audit service. **Cached** mode still serves the labeled cache. If a live call fails, the daily dollar cap is spent, or this IP is over the hourly live limit, a bundled chart falls back to that cache.

Pasted notes are refused when they look like a Social Security number, MRN, phone, email, or date of birth. A cleared note is not stored. It is sent to `POST /audit`.

## Run locally

From this directory:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

On macOS or Linux, activate with `source .venv/bin/activate`.

Open http://localhost:8080

Optional:

```bash
set AUDIT_API_BASE=http://127.0.0.1:8000
set MAX_DAILY_USD=3
set LIVE_RATE_PER_HOUR=10
```

`AUDIT_API_BASE` is optional. Leave it empty to call the in-process `POST /audit`. Set it to `http://127.0.0.1:8000` when the audit service is already running on that port.

## Tests

```bash
pip install pytest
pytest
```

## Cloud Run

From this directory:

```bash
gcloud run deploy codesentinel-web --source . --region us-central1 --allow-unauthenticated --port 8080
```

Set `AUDIT_API_BASE`, `MAX_DAILY_USD`, and `LIVE_RATE_PER_HOUR` on the service when the live audit is ready. The container listens on `PORT`.

The Eval tab reads `EVAL_RESULTS_DIR` or `../eval/results` when that folder exists. Until the harness writes JSON, the tab shows the rules-only MTSamples queue counts (1,188 review, 279 approved, 2,227 notes) and labels them as queue counts, not precision or recall.
