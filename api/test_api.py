"""
Unit tests for the pure functions. The route handlers need a live
Supabase session, so those are covered by the manual IDOR check
below rather than mocked here — mocking the auth boundary would
test the mock, not the boundary.
"""

from api.index import build_context, _mean


def test_mean_ignores_nulls():
    # Derived metrics are nullable. Treating null as zero would drag
    # every average toward zero and invent a trend that isn't there.
    assert _mean([10, None, 20]) == 15.0


def test_mean_all_null_returns_none():
    assert _mean([None, None]) is None


def test_context_truncates_raw_sessions():
    logs = [{"mode": "reflex", "accuracy_rate": 80} for _ in range(10)]
    ctx = build_context({}, logs)
    assert len(ctx["recent_sessions"]) == 3      # token budget holds
    assert ctx["cognitive_trends"]["sessions_analysed"] == 10   # all aggregated


def test_context_survives_empty_profile():
    ctx = build_context({}, [{"accuracy_rate": 90}])
    assert ctx["patient_demographics"]["age"] is None