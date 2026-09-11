"""
Sign in as each test user, generate sessions, insert under RLS.

Note this seeds through the ANON key with real user sessions — not
service_role. So a successful run is itself a second proof that the
INSERT policies work: every row is written by the user who owns it.
"""

from __future__ import annotations
import os, sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import create_client

# sys.path.insert(0, os.path.dirname(__file__))
# import cohorts, metrics
from seeder import cohorts, metrics

load_dotenv()
URL = os.environ["SUPABASE_URL"]
KEY = os.environ["SUPABASE_ANON_KEY"]
PW  = os.environ.get("TEST_PASSWORD", "TestPass123!")

# Assign a cohort per patient. doctor.c is excluded — a doctor is a
# reader here, not a subject.
PATIENTS = [
    ("patient.a@test.com", "healthy"),
    ("patient.b@test.com", "sleep_deprived"),
]

SESSIONS_PER_PATIENT = 30
DAYS_BACK = 30


def sign_in(email: str):
    client = create_client(URL, KEY)
    client.auth.sign_in_with_password({"email": email, "password": PW})
    return client, client.auth.get_user().user.id


def seed_patient(email: str, cohort: str) -> int:
    client, uid = sign_in(email)
    print(f"\n{email}  ({cohort})  uid={uid[:8]}…")

    # prior accuracy per mode, so learning_improvement_rate has a
    # real baseline that grows as sessions accumulate
    history: dict[str, list[float]] = {m: [] for m in cohorts.MODES}
    rows, inserted = [], 0

    for i in range(SESSIONS_PER_PATIENT):
        mode = cohorts.MODES[i % len(cohorts.MODES)]
        p = cohorts.session_payload(cohort, mode)
        level = p.pop("_highest_level")

        derived = metrics.derive_all(
            rts=p["raw_reaction_times"],
            accuracy=p["accuracy_rate"],
            highest_level=level,
            prior_accuracies=history[mode],
        )
        history[mode].append(p["accuracy_rate"])

        # backdate, newest last
        offset = DAYS_BACK - int(i * DAYS_BACK / SESSIONS_PER_PATIENT)
        session_date = datetime.now(timezone.utc) - timedelta(
            days=offset, hours=int(i % 12)
        )

        rows.append({
            "user_id": uid,
            "session_date": session_date.isoformat(),
            **p,
            **derived,
        })

    # chunked insert: one 30-row request beats 30 round trips
    for chunk in (rows[i:i + 10] for i in range(0, len(rows), 10)):
        client.table("session_logs").insert(chunk).execute()
        inserted += len(chunk)

    _upsert_stats(client, uid, rows)
    print(f"  inserted {inserted} sessions")
    return inserted


def _upsert_stats(client, uid: str, rows: list[dict]) -> None:
    """
    user_stats is the adaptive-engine CHECKPOINT, not a summary.
    The current_* columns are where the engine settled — which is
    why they cannot be derived from session_logs and why this table
    exists at all.
    """
    accs = [r["accuracy_rate"] for r in rows]
    rts  = [r["reaction_time_ms_avg"] for r in rows]

    # EWMA: 0.9 * previous + 0.1 * current. Weights recent
    # performance higher and costs O(1) instead of rescanning.
    ewma_rt = rts[0]
    for rt in rts[1:]:
        ewma_rt = 0.9 * ewma_rt + 0.1 * rt

    client.table("user_stats").upsert({
        "user_id": uid,
        "total_sessions_completed": len(rows),
        "overall_play_time_seconds": int(sum(r["completion_time_seconds"] for r in rows)),
        "current_streak_days": 4,
        "longest_streak_days": 11,
        "last_session_at": rows[-1]["session_date"],
        # respect the CHECK ranges in schema.sql
        "current_reflex_spawn_rate": 1400,
        "current_memory_sequence": 5,
        "current_focus_distractors": 11,
        "baseline_reaction_time_ms": round(ewma_rt, 2),
        "baseline_accuracy_rate": round(sum(accs) / len(accs), 2),
    }, on_conflict="user_id").execute()


if __name__ == "__main__":
    total = sum(seed_patient(e, c) for e, c in PATIENTS)
    print(f"\ndone — {total} sessions across {len(PATIENTS)} patients")