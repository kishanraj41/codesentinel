# CodeSentinel HTTP API

Contract version **1.0.0**. The web client should code against this document.
`contract_version` in every `/audit` and `/health` payload is the compatibility key.
Additive fields may appear later. Existing fields, enums, and routes below stay stable.

CodeSentinel is a clinical-coding integrity service. It is not a code-security scanner.
Rules propose candidate ICD-10-CM gaps. NVIDIA Nemotron on Nebius Token Factory
cross-examines them. A deterministic verifier drops any supporting quote that is not
literally in the chart (or an exact structured lab/medication line). Tavily supplies
official ICD-10-CM/CMS guideline excerpts for combination-code and specificity
decisions. If that evidence is missing, those decisions cannot be `accept`.

Synthetic charts only. Do not send real PHI.

## Run

```bash
pip install -r requirements.txt
python mcp_server.py
```

The server listens on `PORT` (default `8000`).

Keys are optional. When `NEBIUS_API_KEY` or `TAVILY_API_KEY` is unset, that step
uses a recorded mock/fixture client. The response says so in `mode` and
`guideline_mode`. No network call is made for a missing key.

| Env | Purpose |
|---|---|
| `NEBIUS_API_KEY` | Token Factory bearer token. Unset → mock Nemotron. |
| `NEBIUS_BASE_URL` | Default `https://api.tokenfactory.nebius.com/v1/`. |
| `TAVILY_API_KEY` | Tavily key. Unset → recorded fixtures. |
| `FHIR_ALLOWED_HOSTS` | Comma-separated extra FHIR hosts. |
| `FHIR_ALLOW_ANY` | `1` allows any public host. Loopback, link-local, and private addresses stay blocked. |
| `CODESENTINEL_VERIFY` | `0` skips Nemotron and returns the rules-only pipeline. Default `1`. |

## GET /health

```json
{
  "status": "ok",
  "service": "codesentinel",
  "version": "2.0.0",
  "contract_version": "1.0.0",
  "nemotron": "mock",
  "tavily": "mock",
  "base_url": "https://api.tokenfactory.nebius.com/v1/"
}
```

`nemotron` and `tavily` are `configured` when the corresponding env key is set,
and `mock` when it is not. This does not prove the key is valid.

## GET /patients

Bundled synthetic charts the auditor can run with no FHIR server.
Ids are `pt-001` through `pt-010` (see `test_dataset.py`).

```json
{
  "patients": [
    {
      "id": "pt-001",
      "name": "Maria Elena Rodriguez",
      "hook": "Coded E11.9; HbA1c 8.4% and CKD are documented in the plan"
    }
  ]
}
```

## POST /audit

Runs rules, then the Nemotron cascade, and returns the coder-facing audit.

### Request body

```json
{
  "patient_id": "pt-001",
  "profile": null,
  "fhir_server_url": null,
  "include_rules_only": true
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `patient_id` | string | no | Synthetic id (`pt-001`…`pt-010`) or the FHIR Patient id when fetching. |
| `profile` | object | no | Inline chart. When present, it is used and FHIR is not called. |
| `fhir_server_url` | string | no | FHIR R4 base URL. Must pass the allowlist. Overrides `X-FHIR-Server-URL`. |
| `include_rules_only` | boolean | no | Default `true`. Include the pre-Nemotron buckets. |

Resolution order:

1. `profile` if it is a non-empty object.
2. Else a bundled synthetic chart when `patient_id` is `pt-001`…`pt-010`.
3. Else a FHIR fetch when a base URL is present (body or `X-FHIR-Server-URL`).
4. Else **404**.

Optional headers, same names as the MCP server:

- `X-FHIR-Server-URL`
- `X-FHIR-Access-Token`
- `X-Patient-ID` (used when `patient_id` is omitted)

### Inline profile shape

```json
{
  "fhir_patient_id": "demo-1",
  "demographics": {"full_name": "Alex Example", "found": true},
  "clinical_notes": [{"text": "ASSESSMENT AND PLAN:\nType 2 diabetes mellitus with hyperglycemia. HbA1c 8.4%."}],
  "observations": [{"observation_id": "obs-1", "loinc": "4548-4", "name": "HbA1c", "value": 8.4, "unit": "%", "date": "2026-01-15"}],
  "medications": [{"medication_id": "med-1", "name": "Metformin 1000mg", "status": "active"}],
  "conditions": [{"condition_id": "cond-1", "icd10_code": "E11.9", "display": "Type 2 DM unspecified", "clinical_status": "active"}],
  "encounters": [{"encounter_id": "enc-1", "type": "Office visit", "start_date": "2026-01-15"}]
}
```

Unknown extra keys are ignored by the rules pipeline. Notes, labs, meds, and conditions are the fields that change decisions.

### PHI guard

The raw JSON body is scanned for SSN-like (`ddd-dd-dddd`), MRN-like, email, and US phone patterns.
A match returns **422** with `{"detail": "Possible PHI detected. This endpoint accepts synthetic data only."}`.
The body is not written to the log.

### FHIR allowlist

`fhir_server_url` and `X-FHIR-Server-URL` are rejected with **403** unless the host is loopback or listed in `FHIR_ALLOWED_HOSTS` (default also allows `hapi.fhir.org`).
Link-local, metadata, and private addresses are always rejected.
Redirects and FHIR bundle `next` links must stay on the same host.
`FHIR_ALLOW_ANY=1` relaxes the hostname list only. It does not allow private or link-local targets.

### Success response — 200

```json
{
  "contract_version": "1.0.0",
  "audit_id": "0f1e2d3c-4b5a-6978-90ab-cdef12345678",
  "patient_id": "pt-001",
  "patient_name": "Maria Elena Rodriguez",
  "mode": "mock",
  "guideline_mode": "fixture",
  "degraded_reasons": [],
  "summary": {
    "candidates_generated": 8,
    "rules_approved": 4,
    "rules_review": 3,
    "rules_rejected": 1,
    "worklist_count": 5,
    "accept_count": 3,
    "review_count": 1,
    "abstain_count": 1,
    "specificity_upgrades": 1,
    "combination_codes": 0,
    "dropped_count": 3
  },
  "worklist": [],
  "dropped": [],
  "timeline": [],
  "receipt": {},
  "citations": [],
  "rules_only": {}
}
```

`rules_only` is `null` when `include_rules_only` is false. Otherwise it is the rules pipeline object: `approved`, `review`, `rejected`, `revenue_summary`.

#### mode

| Value | Meaning |
|---|---|
| `mock` | `NEBIUS_API_KEY` unset. Every model step used the recorded client. |
| `live` | Token Factory was called and no step fell back. |
| `degraded` | A key was set, but at least one model call failed and that step used the recorded client. |

#### guideline_mode

| Value | Meaning |
|---|---|
| `fixture` | No Tavily key. Recorded fixtures only. Fixture quotes are labeled and are not live official excerpts. |
| `live` | Every citation in this response was retrieved from Tavily on this request. |
| `cache` | Every citation was served from the on-disk cache. |
| `mixed` | More than one of live, cache, and fixture. |
| `none` | No guideline lookup returned a citation. |

#### summary counts

- `candidates_generated` — rules `approved` + rules `review`. Both are sent to Nemotron. Rules `rejected` are not sent to a model.
- `worklist_count` — `accept` + `review` + `abstain`.
- `specificity_upgrades` / `combination_codes` — counts on the worklist, not on dropped rows.
- `dropped_count` — length of `dropped`.

### worklist[]

Shown to the coder. Ordered `accept`, then `review`, then `abstain`, and by descending confidence inside each group.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `"{patient_id}:{icd10_code}"`. |
| `icd10_code` | string | Proposed code. |
| `code_display` | string | Short name. |
| `gap_type` | enum | `missing`, `specificity_upgrade`, `combination`. |
| `upgrade_from` | string or null | Code being replaced, when this is an upgrade or combination. |
| `decision` | enum | `accept`, `review`, `abstain`. |
| `confidence` | number | 0–1 from triage. |
| `assertion_status` | enum | See below. Worklist rows are `present` unless a later step abstains. |
| `section` | string | Note section the quote was taken from, or `structured` for a lab/med line. |
| `supporting_quotes` | string[] | Quotes the model (or mock) offered. |
| `verified_quotes` | string[] | Subset that passed the deterministic verifier. Non-empty on every worklist row. |
| `meat` | object | `{monitor, evaluate, assess, treat}` each a string quote or null. |
| `lab` | object or null | `{name, loinc, value, unit, date, observation_id}` when the candidate is lab-backed. |
| `hcc_category` | string | CMS-HCC label from the rules table, or `Non-HCC` / `Unknown`. |
| `hcc_description` | string | |
| `raf_value` | number | |
| `annual_revenue_impact` | number | Illustrative RAF × base rate from the rules table. Not a billing recommendation. |
| `rationale` | string | At most 60 words. |
| `physician_query_needed` | boolean | |
| `physician_query` | string or null | Non-leading query drafted by Ultra when needed. It asks for status clarification and does not tell the clinician to add a code. |
| `decided_by` | string | Model id that made the final decision. |
| `tier` | string | Rules tier (`TIER_1_STRUCTURED`, `TIER_2_PHENOTYPE`, `TIER_3_NOTE_NER`, `SPECIFICITY_UPGRADE`, `COMBINATION_CODE`, …). |
| `guideline_citations` | Citation[] | Official-source citations attached to this row. |
| `evidence_sources` | string[] | Original rules evidence strings. |

`assertion_status` enum:

`present`, `negated`, `historical_resolved`, `family_history`, `screening_or_rule_out`, `hypothetical_plan`, `copied_forward_unclear`.

### dropped[]

Candidates removed before the worklist.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `"{patient_id}:{icd10_code}"`. |
| `icd10_code` | string | |
| `code_display` | string | |
| `gap_type` | enum | `missing`, `specificity_upgrade`, `combination`. |
| `tier` | string | |
| `reason` | enum | `unsupported`, `negated`, `historical_resolved`, `family_history`, `screening_or_rule_out`, `hypothetical_plan`, `copied_forward_unclear`, `rejected`. |
| `reason_detail` | string | Short human explanation. Safe to show in a drawer. |
| `supporting_quotes` | string[] | Quotes offered, including ones the verifier refused. |
| `dropped_by` | enum | `triage`, `verifier`, `adjudicator`, `escalation`. |
| `model` | string or null | Model id for that step. |

`unsupported` means the deterministic verifier found no verbatim quote in the note text and no exact structured lab/medication line. `rejected` means the adjudicator or escalation model refused the candidate.

### Citation

| Field | Type | Meaning |
|---|---|---|
| `url` | string | Page the excerpt came from. |
| `quote` | string | Excerpt. Fixture quotes say they are recorded offline fixtures, not live guideline text. |
| `source` | string | Hostname (`cms.gov`, `cdc.gov`, …). |
| `title` | string | |
| `retrieved` | enum | `live`, `cache`, `fixture`. |
| `cached_at` | string or null | ISO-8601 timestamp when `retrieved` is `cache` or `fixture`. |
| `code_family` | string | First three characters of the ICD-10-CM code (`E11`, `I12`, …). |
| `issue` | string | Stable issue key, for example `combination-code-diabetes-ckd` or `specificity-hyperglycemia`. |

Top-level `citations` is the de-duplicated union of worklist citations, in first-seen order.

Combination-code and specificity-upgrade rows follow this gate, in live and mock mode:

- A guideline citation is required for `decision: "accept"`.
- If none is returned, the decision is forced to `review` and `rationale` explains that guideline evidence was missing.
- That override is applied in code after the model returns. The model cannot skip it.

### timeline[]

Always these seven steps, in this order:

| `step` | `label` |
|---|---|
| `rules` | Rules |
| `triage` | Lightning triage |
| `verifier` | Quote verifier |
| `guidelines` | Tavily guidelines |
| `adjudication` | Super adjudication |
| `escalation` | Ultra escalation |
| `physician_query` | Physician query |

| Field | Type |
|---|---|
| `step` | string, enum above |
| `label` | string |
| `model` | string or null. Null for `rules` and `verifier`. |
| `status` | `ok`, `skipped`, `error` |
| `latency_ms` | integer |
| `detail` | string |
| `mock` | boolean |

Skipped means the step did not need to run (for example Ultra when nothing was escalated).

Model ids, when used:

| Role | Id |
|---|---|
| Triage | `nvidia/Nemotron-3_5-Lightning` |
| Triage fallback | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (only if a live `models.list()` shows Lightning is absent) |
| Adjudication | `nvidia/nemotron-3-super-120b-a12b` |
| Escalation and physician query | `nvidia/Nemotron-3-Ultra-550b-a55b` |

`decided_by` and timeline `model` use these ids in mock mode too, so the UI can render the routing graph without a key. `mock: true` marks recorded steps.

### receipt

```json
{
  "steps": [
    {
      "step": "triage",
      "model": "nvidia/Nemotron-3_5-Lightning",
      "calls": 8,
      "input_tokens": 3200,
      "output_tokens": 900,
      "latency_ms": 40,
      "cost_usd": 0.000408,
      "mock": true,
      "usage_source": "estimated",
      "tavily_credits": 0,
      "cache_hits": 0
    }
  ],
  "totals": {
    "input_tokens": 3200,
    "output_tokens": 900,
    "latency_ms": 40,
    "cost_usd": 0.000408,
    "tavily_credits": 0,
    "tavily_cache_hits": 0,
    "calls": 8
  },
  "models_used": ["nvidia/Nemotron-3_5-Lightning"],
  "catalog_prices_per_million": {
    "nvidia/Nemotron-3_5-Lightning": {"input": 0.06, "output": 0.24},
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": {"input": 0.06, "output": 0.24},
    "nvidia/nemotron-3-super-120b-a12b": {"input": 0.30, "output": 0.90},
    "nvidia/Nemotron-3-Ultra-550b-a55b": {"input": 1.00, "output": 3.00}
  }
}
```

| `usage_source` | Meaning |
|---|---|
| `provider` | Token counts came from the Token Factory or Tavily usage payload. |
| `estimated` | Mock or missing usage. Tokens estimated as characters ÷ 4. Cost still uses the catalog prices. |
| `none` | Step made no priced call. |

`cost_usd` is `input_tokens / 1e6 * input_price + output_tokens / 1e6 * output_price`.
Tavily credits are counted on the `guidelines` step. A cache hit costs 0 credits and increments `cache_hits`.
Advanced search is counted as 2 credits when the provider does not return a usage object. Extract of one URL is counted as 1.

### Errors

| Status | When |
|---|---|
| 404 | No profile, unknown patient id, and no FHIR URL. `{"detail": "Patient not found. Pass a pt-001…pt-010 id, an inline profile, or an allowlisted FHIR URL."}` |
| 403 | FHIR URL failed the allowlist. `{"detail": "<reason>"}` |
| 422 | Request schema invalid, or the PHI guard tripped. |
| 502 | FHIR fetch failed after the URL was allowed. |

### Cascade (normative)

1. **Rules.** Existing tiers 1–4 in `gap_pipeline.py`. Approved and review candidates are both sent on. Rules-rejected candidates are not sent to a model.
2. **Triage.** One structured JSON call per candidate, in parallel (cap 8). Reasoning off. Temperature 0. Output schema: `assertion_status`, `supporting_quotes`, `section`, `meat`, `confidence`.
3. **Verifier.** Outside the model. A quote counts only if its whitespace-normalized form (length ≥ 12) is a substring of the note text, or it equals a full structured lab or medication line, or it equals a structured record id. No surviving quote → drop `unsupported`. `assertion_status` other than `present` → drop with that status as `reason`.
4. **Guidelines.** Tavily search (`search_depth=advanced`, `include_domains=["cms.gov","cdc.gov"]`, `chunks_per_source=3`, `max_results=5`) plus extract on the top official URL, per code family and issue. Cached on disk by `(code_family, issue)`. Combination and specificity candidates always request a lookup.
5. **Adjudication.** Super, reasoning on, for medium-confidence survivors (confidence < 0.8) and for every specificity upgrade and combination code. High-confidence simple `missing` rows can be accepted from triage + verifier without Super. JSON schema: `decision`, `icd10_code`, `code_display`, `guideline_citations`, `rationale`, `physician_query_needed`. Parse failure → one repair retry → else `review`.
6. **Guideline gate.** Combination or specificity with `decision: accept` and zero citations is rewritten to `review`.
7. **Escalation.** Ultra when triage and Super disagree, or the row is a high-value HCC upgrade (`raf_value` ≥ 0.3 and gap type is `specificity_upgrade` or `combination`). Thin or conflicting evidence becomes `abstain`, which stays on the worklist for a human.
8. **Physician query.** Ultra drafts a non-leading query when `physician_query_needed` is true. Queries that tell the clinician to add a code are discarded and replaced with the standard non-leading template.

Every run fills `timeline` and `receipt`, including mock runs.

## POST /mcp

Unchanged JSON-RPC shape (`initialize`, `tools/list`, `tools/call`). SHARP headers still apply.
`detect_coding_gaps` runs the same verified pipeline. Its payload keeps the legacy keys
`summary`, `approved_gaps`, `review_candidates`, and `revenue_impact`, and adds `audit`
set to the `/audit` object (without duplicating `rules_only` inside `audit` — `rules_only` is included).

`approved_gaps` are worklist rows with `decision: "accept"`.
`review_candidates` are worklist rows with `decision` `review` or `abstain`.

There is no `/debug/patient` route.

## Model call

Live calls are `POST {NEBIUS_BASE_URL}/chat/completions` with `response_format.type = json_schema`
and the same schema repeated in the system prompt. Triage sends
`chat_template_kwargs.enable_thinking = false` and `temperature = 0`.
Super and Ultra send `enable_thinking = true` and `reasoning_effort = "low"`.
HTTP 429 retries up to three times with exponential backoff, honoring `Retry-After` when present.
When a key exists, the run calls `GET {NEBIUS_BASE_URL}/models` once and falls back from Lightning to Nano if Lightning is absent.
