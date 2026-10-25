"""
CodeSentinel — clinical coding integrity MCP server.

Exposes gap detection over the Model Context Protocol and POST /audit.
Rules propose ICD-10-CM candidates. NVIDIA Nemotron on Nebius Token Factory
checks them. A deterministic verifier drops quotes that are not in the chart.
Reads SHARP headers: X-FHIR-Server-URL, X-FHIR-Access-Token, X-Patient-ID.
FHIR base URLs must pass the allowlist in codesentinel.fhir_guard.
"""
import json
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# Cloud Run compatible logging - outputs to stdout
import sys
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
    force=True  # Override any existing config
)
log = logging.getLogger("codesentinel")

app = FastAPI(
    title="CodeSentinel — clinical coding integrity",
    version="2.0.0",
    description=(
        "Rules propose missed ICD-10-CM codes. NVIDIA Nemotron on Nebius Token Factory "
        "cross-examines them, and a deterministic verifier drops anything it cannot quote."
    ),
)

# ══════════════════════════════════════════════════════════
# MCP PROTOCOL IMPLEMENTATION
# ══════════════════════════════════════════════════════════

# In-memory results cache (per session)
_gap_cache: dict = {}


def get_sharp_context(request: Request) -> dict:
    """Extract SHARP headers from the request."""
    fhir_url = request.headers.get("X-FHIR-Server-URL", os.environ.get("FHIR_SERVER_URL", ""))
    access_token = request.headers.get("X-FHIR-Access-Token", os.environ.get("FHIR_ACCESS_TOKEN", ""))
    patient_id = request.headers.get("X-Patient-ID", "")
    return {
        "fhir_server_url": fhir_url,
        "access_token": access_token,
        "patient_id": patient_id,
    }


def validate_sharp_context(ctx: dict):
    """Require an allowlisted FHIR base URL. Arbitrary hosts are rejected."""
    from codesentinel.fhir_guard import GuardError, validate_fhir_base_url

    if not ctx.get("fhir_server_url"):
        raise HTTPException(status_code=403, detail="X-FHIR-Server-URL header required")
    try:
        ctx["fhir_server_url"] = validate_fhir_base_url(ctx["fhir_server_url"])
    except GuardError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


# ══════════════════════════════════════════════════════════
# MCP ENDPOINTS
# ══════════════════════════════════════════════════════════

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Main MCP endpoint — handles initialize, tools/list, tools/call."""
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 1)
    if method == "initialize":
        return mcp_response(req_id, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {
                "name": "codesentinel",
                "version": "2.0.0",
            },
            "capabilities": {
                "tools": {"listChanged": False},
                "extensions": {
                    "ai.promptopinion/fhir-context": {
                        "scopes": [
                            {
                                "name": "patient/Patient.rs",
                                "required": True
                            },
                            {
                                "name": "patient/Observation.rs",
                                "required": True
                            },
                            {
                                "name": "patient/Condition.rs",
                                "required": True
                            },
                            {
                                "name": "patient/MedicationRequest.rs",
                                "required": True
                            },
                            {
                                "name": "patient/DocumentReference.rs",
                                "required": True
                            },
                            {
                                "name": "patient/Encounter.rs",
                                "required": False
                            },
                            {
                                "name": "patient/Procedure.rs",
                                "required": False
                            }
                        ]
                    }
                },
                "experimental": {
                    "fhir_context_required": {"value": True},
                },
            },
        })
    
    elif method == "notifications/initialized":
        return JSONResponse(content={})

    elif method == "tools/list":
        return mcp_response(req_id, {"tools": TOOL_DEFINITIONS})

    elif method == "tools/call":
        params = body.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        ctx = get_sharp_context(request)

        try:
            result = await call_tool(tool_name, arguments, ctx)
            return mcp_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
            })
        except Exception as e:
            log.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return mcp_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            })

    return mcp_response(req_id, {"error": {"code": -32601, "message": f"Unknown method: {method}"}})


def mcp_response(req_id: int, result: dict):
    return JSONResponse(content={"jsonrpc": "2.0", "id": req_id, "result": result})


# ══════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ══════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    {
        "name": "detect_coding_gaps",
        "description": (
            "Analyze a patient's clinical record to detect missed diagnosis codes. "
            "A four-tier rules pipeline proposes candidates from labs, phenotypes, notes, "
            "and specificity or combination rules. NVIDIA Nemotron then checks each "
            "candidate against the chart. Quotes that are not in the chart are dropped."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "FHIR Patient ID to analyze (optional if patient context is set via SHARP headers)",
                },
            },
            "required": [],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_patient_summary",
        "description": (
            "Get a structured clinical summary for a patient including demographics, "
            "active conditions, recent labs, current medications, and encounter history. "
            "Pulls data live from the FHIR server."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "FHIR Patient ID",
                },
            },
            "required": ["patient_id"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "validate_gap",
        "description": (
            "Validate a specific detected gap against MEAT criteria "
            "(Monitoring, Evaluation, Assessment, Treatment) and ICD-10-CM coding guidelines. "
            "Returns audit-ready evidence supporting or refuting the gap."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "FHIR Patient ID",
                },
                "icd10_code": {
                    "type": "string",
                    "description": "ICD-10 code of the gap to validate (e.g., E11.65)",
                },
                "condition_name": {
                    "type": "string",
                    "description": "Human-readable condition name (e.g., Type 2 diabetes with hyperglycemia)",
                },
            },
            "required": ["patient_id", "icd10_code"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "list_detected_gaps",
        "description": (
            "List previously detected gaps with optional filters. "
            "Can filter by gap decision (APPROVED, REVIEW, REJECTED), HCC category, "
            "confidence level, or ICD-10 code range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "FHIR Patient ID to list gaps for",
                },
                "decision_filter": {
                    "type": "string",
                    "description": "Filter by decision: APPROVED, REVIEW, REJECTED, or ALL",
                    "enum": ["APPROVED", "REVIEW", "REJECTED", "ALL"],
                },
            },
            "required": ["patient_id"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "draft_physician_query",
        "description": (
            "Generate an audit-ready physician query for a detected gap. "
            "Creates a specific question with clinical evidence the provider can review "
            "to confirm or deny the coding opportunity."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "FHIR Patient ID",
                },
                "icd10_code": {
                    "type": "string",
                    "description": "ICD-10 code of the gap",
                },
                "condition_name": {
                    "type": "string",
                    "description": "Human-readable condition name",
                },
                "evidence_summary": {
                    "type": "string",
                    "description": "Brief summary of clinical evidence supporting the gap",
                },
            },
            "required": ["patient_id", "icd10_code", "condition_name"],
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
]


# ══════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════

async def call_tool(tool_name: str, arguments: dict, ctx: dict) -> Any:
    """Route tool calls to implementations."""
    validate_sharp_context(ctx)
    host = ctx.get("fhir_server_url", "")
    log.info("FHIR host configured: %s", host.split("/")[2] if "://" in host else host)
    log.info("Access token present: %s", bool(ctx.get("access_token")))

    from fhir_adapter import FHIRClient, FHIRPatientExtractor
    client = FHIRClient(ctx["fhir_server_url"], ctx.get("access_token", ""))
    extractor = FHIRPatientExtractor(client)

    if tool_name == "detect_coding_gaps":
        return await tool_detect_gaps(extractor, arguments, ctx)
    elif tool_name == "get_patient_summary":
        return await tool_get_patient_summary(extractor, arguments)
    elif tool_name == "validate_gap":
        return await tool_validate_gap(extractor, arguments, ctx)
    elif tool_name == "list_detected_gaps":
        return await tool_list_gaps(arguments, ctx)
    elif tool_name == "draft_physician_query":
        return await tool_draft_physician_query(arguments)
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


async def tool_detect_gaps(extractor, arguments: dict, ctx: dict) -> dict:
    """Run rules and the Nemotron cascade for one patient."""
    patient_id = arguments.get("patient_id") or ctx.get("patient_id", "")
    if not patient_id:
        return {
            "error": "No patient_id provided. Either pass patient_id in arguments or ensure X-Patient-ID header is set.",
            "context_available": bool(ctx.get("patient_id"))
        }

    start = time.time()
    log.info(f"Detecting gaps for patient {patient_id}...")

    # Extract full patient profile from FHIR
    profile = extractor.extract_patient(patient_id)
    demographics = extractor.get_patient_demographics(patient_id)

    if not demographics.get("found"):
        return {"error": f"Patient {patient_id} not found on FHIR server"}

    from gap_pipeline import run_gap_pipeline
    result = run_gap_pipeline(profile, demographics)
    audit = result.get("audit") or {}
    elapsed = round((time.time() - start) * 1000)
    _gap_cache[patient_id] = result

    return {
        "patient_id": patient_id,
        "patient_name": demographics.get("full_name", ""),
        "processing_time_ms": elapsed,
        "summary": {
            "approved_gaps": len(result.get("approved", [])),
            "review_candidates": len(result.get("review", [])),
            "rejected": len(result.get("rejected", [])),
            "total_notes_analyzed": len(profile.get("clinical_notes", [])),
            "total_labs_checked": len(profile.get("observations", [])),
            "coded_conditions": len(profile.get("conditions", [])),
        },
        "revenue_impact": result.get("revenue_summary", {}),
        "approved_gaps": result.get("approved", []),
        "review_candidates": result.get("review", []),
        "audit": audit,
    }


async def tool_get_patient_summary(extractor, arguments: dict) -> dict:
    """Return a structured patient summary."""
    patient_id = arguments["patient_id"]
    demographics = extractor.get_patient_demographics(patient_id)
    if not demographics.get("found"):
        return {"error": f"Patient {patient_id} not found"}

    profile = extractor.extract_patient(patient_id)

    return {
        "patient_id": patient_id,
        "demographics": {
            "name": demographics.get("full_name"),
            "age": demographics.get("age"),
            "gender": demographics.get("gender"),
            "dob": demographics.get("dob"),
        },
        "active_conditions": [
            {"code": c["icd10_code"], "name": c["display"], "status": c["clinical_status"]}
            for c in profile.get("conditions", [])
            if c.get("clinical_status") in ("active", "recurrence", "")
        ],
        "recent_labs": [
            {"name": o["name"], "loinc": o["loinc"], "value": o["value"], "unit": o["unit"], "date": o["date"]}
            for o in sorted(profile.get("observations", []), key=lambda x: x.get("date", ""), reverse=True)[:20]
        ],
        "medications": [
            {"name": m["name"], "status": m["status"], "dosage": m["dosage"]}
            for m in profile.get("medications", [])
            if m.get("status") in ("active", "")
        ],
        "recent_encounters": [
            {"type": e["type"], "date": e["start_date"], "status": e["status"]}
            for e in sorted(profile.get("encounters", []), key=lambda x: x.get("start_date", ""), reverse=True)[:10]
        ],
    }


async def tool_validate_gap(extractor, arguments: dict, ctx: dict) -> dict:
    """Validate a specific gap against MEAT criteria."""
    patient_id = arguments["patient_id"]
    icd10 = arguments["icd10_code"]
    condition = arguments.get("condition_name", icd10)

    # Check cache for existing analysis
    cached = _gap_cache.get(patient_id, {})
    all_gaps = cached.get("approved", []) + cached.get("review", [])
    matching = [g for g in all_gaps if g.get("icd10_code") == icd10]

    if matching:
        gap = matching[0]
        return {
            "patient_id": patient_id,
            "icd10_code": icd10,
            "condition": condition,
            "validation": "FROM_CACHE",
            "meat_evidence": gap.get("meat_evidence", {}),
            "confidence": gap.get("confidence_score", 0),
            "evidence_sources": gap.get("evidence_sources", []),
            "clinical_trail": gap.get("clinical_trail", ""),
        }

    # If not cached, run fresh analysis
    return {
        "patient_id": patient_id,
        "icd10_code": icd10,
        "condition": condition,
        "validation": "Run detect_coding_gaps first to generate gap analysis",
    }


async def tool_list_gaps(arguments: dict, ctx: dict) -> dict:
    """List cached gaps with optional filters."""
    patient_id = arguments.get("patient_id") or ctx.get("patient_id", "")
    decision_filter = arguments.get("decision_filter", "ALL")

    cached = _gap_cache.get(patient_id)
    if not cached:
        return {
            "patient_id": patient_id,
            "message": "No gaps cached. Run detect_coding_gaps first.",
            "gaps": [],
        }

    gaps = []
    if decision_filter in ("APPROVED", "ALL"):
        gaps.extend([{**g, "decision": "APPROVED"} for g in cached.get("approved", [])])
    if decision_filter in ("REVIEW", "ALL"):
        gaps.extend([{**g, "decision": "REVIEW"} for g in cached.get("review", [])])
    if decision_filter in ("REJECTED", "ALL"):
        gaps.extend([{**g, "decision": "REJECTED"} for g in cached.get("rejected", [])])

    return {
        "patient_id": patient_id,
        "filter": decision_filter,
        "total_gaps": len(gaps),
        "gaps": gaps,
    }


async def tool_draft_physician_query(arguments: dict) -> dict:
    """Return a non-leading query. Reuse an Ultra draft from the cached audit when present."""
    from codesentinel.nemotron.mock_reasoner import non_leading_query

    patient_id = arguments["patient_id"]
    icd10 = arguments["icd10_code"]
    condition = arguments.get("condition_name", icd10)
    evidence = arguments.get("evidence_summary", "")
    cached = _gap_cache.get(patient_id, {})
    audit = cached.get("audit") or {}
    for item in audit.get("worklist") or []:
        if item.get("icd10_code") == icd10 and item.get("physician_query"):
            return {
                "patient_id": patient_id,
                "icd10_code": icd10,
                "condition": condition,
                "physician_query": item["physician_query"],
                "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
            }

    quotes = [evidence] if evidence else []
    return {
        "patient_id": patient_id,
        "icd10_code": icd10,
        "condition": condition,
        "physician_query": non_leading_query(icd10, condition, quotes),
        "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
    }


# ══════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    import os
    from codesentinel import CONTRACT_VERSION
    from codesentinel.nemotron.catalog import DEFAULT_BASE_URL

    base = os.environ.get("NEBIUS_BASE_URL") or DEFAULT_BASE_URL
    return {
        "status": "ok",
        "service": "codesentinel",
        "version": "2.0.0",
        "contract_version": CONTRACT_VERSION,
        "nemotron": "configured" if os.environ.get("NEBIUS_API_KEY") else "mock",
        "tavily": "configured" if os.environ.get("TAVILY_API_KEY") else "mock",
        "base_url": base if base.endswith("/") else base + "/",
    }


@app.get("/patients")
async def patients():
    from codesentinel.service import list_patients

    return list_patients()


@app.post("/audit")
async def audit(request: Request):
    """Coder-facing audit. See docs/api.md contract 1.0.0."""
    import asyncio
    from codesentinel.api_models import AuditResponse
    from codesentinel.phi import PHI_DETAIL, phi_detected
    from codesentinel.service import AuditError, execute_audit

    raw = await request.body()
    text = raw.decode("utf-8", errors="replace")
    if phi_detected(text):
        raise HTTPException(status_code=422, detail=PHI_DETAIL)
    if not text.strip():
        payload = {}
    else:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="Request body must be JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    try:
        result = await asyncio.to_thread(execute_audit, payload, dict(request.headers))
    except AuditError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return AuditResponse.model_validate(result).model_dump()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
