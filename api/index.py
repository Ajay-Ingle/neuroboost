"""
NeuroBoost clinical insight service.

Deployed as a Python serverless function on Vercel, routed via the
/api/(.*) rewrite in vercel.json so it shares an origin with the
Next.js app. Same domain means no CORS, no second host, no proxy.

Architectural position: this is the FIXED PIPELINE half of the
service tier. One endpoint, one prompt, decided at build time. The
MCP server (Day 4) is the opposite model — a capability surface
where the client's model decides what runs at runtime.
"""

from __future__ import annotations

import os
import statistics
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends

from pydantic import BaseModel, Field
from supabase import create_client

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Fail at import, not per-request. A service that boots and then
# returns 502 to every caller is worse than one that refuses to start.
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError(
        "SUPABASE_URL and SUPABASE_ANON_KEY must be set. "
        "Check .env locally, or Vercel project settings in production."
    )

# Below this, there is no trend to describe — only noise. Returning
# guidance text is more honest than asking a model to manufacture a
# clinical narrative from two data points.
MIN_SESSIONS_FOR_INSIGHT = 3

# Raw sessions passed verbatim to the model. Everything older is
# represented by aggregates instead. This is the token budget.
RAW_SESSIONS_IN_CONTEXT = 3

app = FastAPI(title="NeuroBoost Clinical Insight", version="2.0.0")
bearer_scheme = HTTPBearer(auto_error=False)

class PatientRequest(BaseModel):
    """
    Runtime validation, unlike a TypeScript interface which is erased
    at compile time. FastAPI rejects a malformed body with 422 before
    the handler runs, so the function never sees bad input.
    """
    user_id: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=50)


# ─────────────────────────────────────────────────────────────────
# Phase 1 — secure ingestion
# ─────────────────────────────────────────────────────────────────

def _bearer_token(request: Request) -> str:
    """Extract the JWT. Missing or malformed means 401, not a guess."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")
    return token


def _scoped_client(token: str):
    """
    Build a Supabase client scoped to the CALLER, not to the service.

    The client is created with the ANON key — the same public key the
    browser holds — then postgrest.auth(token) binds the caller's JWT
    so every query executes under that user's RLS policies.

    The service_role key is deliberately absent from this file and
    from the Vercel environment. A service key bypasses RLS entirely,
    which would make this endpoint an IDOR: pass any user_id in the
    body and receive that patient's clinical record. With JWT binding,
    a tampered user_id returns an empty result set.

    The database refuses. Not this code.
    """
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.postgrest.auth(token)
    return client


# ─────────────────────────────────────────────────────────────────
# Phase 2 — the Context Builder
# ─────────────────────────────────────────────────────────────────

def _mean(values: list[Any]) -> float | None:
    """None-tolerant mean. Derived metrics are nullable by design —
    a session with too few reaction times yields null, not zero, and
    averaging nulls as zeros would silently corrupt the trend."""
    clean = [float(v) for v in values if v is not None]
    return round(statistics.fmean(clean), 2) if clean else None


def build_context(profile: dict, logs: list[dict]) -> dict:
    """
    Turn rows into a compact, model-ready object.

    This is what separates the service from a naive LLM wrapper.
    Three things happen here:

      AGGREGATE  means computed in Python, not by the model. LLMs are
                 unreliable arithmetic engines; a standard deviation
                 belongs in deterministic, testable code. The model's
                 job is narrative interpretation of finished numbers.

      JOIN       demographics give the metrics meaning. A 480ms mean
                 reads differently for a 22-year-old than a 68-year-old.

      TRUNCATE   only the newest few sessions go in verbatim. Bounded
                 tokens means bounded latency and bounded cost.
    """
    return {
        "patient_demographics": {
            "age": profile.get("age"),
            "cohort": profile.get("primary_cohort"),
            "medical_conditions": profile.get("medical_conditions", []),
            "average_sleep_hours": profile.get("sleep_average_hours"),
            "baseline_notes": profile.get("baseline_notes"),
        },
        "cognitive_trends": {
            "sessions_analysed": len(logs),
            # < 1.0 indicates within-session attention decay
            "fatigue_ratio_avg": _mean([l.get("attention_stability_score") for l in logs]),
            # standard deviation of reaction times, in milliseconds
            "consistency_avg": _mean([l.get("performance_stability_variance") for l in logs]),
            # resistance to collapse when difficulty peaks
            "panic_resistance_avg": _mean([l.get("adaptation_accuracy_score") for l in logs]),
            "accuracy_avg": _mean([l.get("accuracy_rate") for l in logs]),
            "reaction_time_ms_avg": _mean([l.get("reaction_time_ms_avg") for l in logs]),
        },
        "recent_sessions": [
            {
                "date": l.get("session_date"),
                "mode": l.get("mode"),
                "accuracy": l.get("accuracy_rate"),
                "reaction_time_ms": l.get("reaction_time_ms_avg"),
                "level": l.get("difficulty_progression_level"),
            }
            for l in logs[:RAW_SESSIONS_IN_CONTEXT]
        ],
    }


# ─────────────────────────────────────────────────────────────────
# Phase 3 — generation
# ─────────────────────────────────────────────────────────────────

SYSTEM_INSTRUCTION = """You are a clinical data analyst summarising \
cognitive assessment telemetry for a healthcare professional.

Rules:
- Maximum three sentences.
- Reference the specific numbers provided. Never invent figures.
- Professional clinical register. No encouragement, no advice.
- Describe observed patterns only. Do not diagnose.
- A fatigue_ratio_avg below 1.0 indicates within-session attention \
decay; above 1.0 indicates improvement across the session."""


def generate_report(context: dict) -> str:
    """
    Gemini call, or a deterministic fallback when no key is present.

    The fallback is not a stub — it keeps the endpoint functional in
    CI and for any reviewer who clones the repo without a key. The
    service degrades rather than failing.
    """
    if not GOOGLE_API_KEY:
        t = context["cognitive_trends"]
        return (
            f"Analysis of {t['sessions_analysed']} sessions shows mean accuracy "
            f"{t['accuracy_avg']}% at {t['reaction_time_ms_avg']}ms. "
            f"Fatigue ratio {t['fatigue_ratio_avg']}, panic resistance "
            f"{t['panic_resistance_avg']}. [Generated without LLM — no API key set.]"
        )

    import google.generativeai as genai
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel(
        "gemini-flash-latest",
        system_instruction=SYSTEM_INSTRUCTION,
    )
    response = model.generate_content(
        f"Patient telemetry:\n{context}\n\nWrite the clinical summary."
    )
    return response.text.strip()


# ─────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict:
    """Liveness probe. Reports capability without leaking key values."""
    return {
        "status": "ok",
        "service": "neuroboost-clinical-insight",
        "llm_configured": bool(GOOGLE_API_KEY),
    }


@app.post("/api/diagnose")
def diagnose(
    req: PatientRequest,
    request: Request,
    _creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> JSONResponse:
    """
    Generate a clinical narrative for one patient.

    Error contract, and the v1 bug this fixes: v1 caught every
    exception and returned HTTP 200 with status "success" and the
    error string inside the report field. Clients could not detect
    failure — a broken backend looked identical to a working one.
    Here, failures return 502 with a structured body and a distinct
    "error" key the client branches on.
    """
    token = _bearer_token(request)

    try:
        supabase = _scoped_client(token)

        # RLS scopes both of these to the caller. If req.user_id is
        # someone else's, these return empty — the isolation holds
        # even though the id came from the request body.
        profile_res = (
            supabase.table("profiles")
            .select("age, primary_cohort, medical_conditions, "
                    "sleep_average_hours, baseline_notes")
            .eq("id", req.user_id)
            .maybe_single()          # tolerates a missing row
            .execute()
        )

        logs_res = (
            supabase.table("session_logs")
            .select("session_date, mode, accuracy_rate, reaction_time_ms_avg, "
                    "difficulty_progression_level, attention_stability_score, "
                    "performance_stability_variance, adaptation_accuracy_score")
            .eq("user_id", req.user_id)
            .order("session_date", desc=True)   # NEVER limit without order —
            .limit(req.limit)                   # Postgres guarantees no ordering
            .execute()                          # otherwise. This was a v1 bug.
        )

    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "error": "database_unavailable",
                     "detail": str(exc)},
        )

    profile = (profile_res.data or {}) if profile_res else {}
    logs = logs_res.data or []

    # Short-circuit before spending a model call on nothing.
    if len(logs) < MIN_SESSIONS_FOR_INSIGHT:
        return JSONResponse(
            status_code=200,
            content={
                "status": "insufficient_data",
                "extracted_sessions_count": len(logs),
                "ai_report": (
                    f"Complete at least {MIN_SESSIONS_FOR_INSIGHT} sessions "
                    f"for a clinical summary. Currently {len(logs)} on record."
                ),
            },
        )

    context = build_context(profile, logs)

    try:
        report = generate_report(context)
    except Exception as exc:
        if "429" in str(exec) or "quots" in str(exc).lower():
            return JSONResponse(
                content={
                    "status": "error",
                    "error": "llm_rate_limited",
                    "detail": "Generation quots exceeded. Retry shortly."
                },
            )
        return JSONResponse(
            status_code=502,
            content={"status": "error", "error": "llm_unavailable", "detail": str(exec),}
        )

    try:
        report = generate_report(context)
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={"status": "error", "error": "llm_unavailable",
                     "detail": str(exc)},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "extracted_sessions_count": len(logs),
            "ai_report": report,
        },
    )