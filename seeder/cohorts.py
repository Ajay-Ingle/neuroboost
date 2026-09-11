"""
Synthetic cohort generators.

The cohorts are shaped so the metrics actually differentiate them.
That is the point: when the MCP demo says "this patient shows
within-session fatigue", the data genuinely contains fatigue, and
the analysis tools can be validated against known ground truth.
"""

from __future__ import annotations
import numpy as np

rng = np.random.default_rng(42)          # seeded: reproducible runs

MODES = ("reflex", "memory", "focus")


def _clip(rts: np.ndarray) -> list[float]:
    """Floor at 120ms — below that is faster than human reaction."""
    return [float(max(120.0, x)) for x in rts]


def healthy(n: int = 25) -> list[float]:
    """
    Stable mean, low variance, no drift.
    Expect: attention_stability ~1.0, low variance, high panic score.
    """
    return _clip(rng.normal(loc=380, scale=35, size=n))


def sleep_deprived(n: int = 25) -> list[float]:
    """
    Mean drifts upward across the session — the signature of fatigue.
    A linear ramp adds up to +180ms by the final interaction.
    Expect: attention_stability_score well below 1.0.
    """
    base = rng.normal(loc=430, scale=45, size=n)
    drift = np.linspace(0, 180, n)
    return _clip(base + drift)


def attention_deficit(n: int = 25) -> list[float]:
    """
    High variance throughout, plus lapse spikes concentrated in the
    final third.
    Expect: high performance_stability_variance, low
    adaptation_accuracy_score.
    """
    rts = rng.normal(loc=400, scale=130, size=n)
    late = int(n * 0.67)
    spikes = rng.random(n - late) < 0.4         # 40% chance of lapse
    rts[late:] += spikes * rng.normal(500, 120, n - late)
    return _clip(rts)


COHORTS = {
    "healthy":           healthy,
    "sleep_deprived":    sleep_deprived,
    "attention_deficit": attention_deficit,
}


def session_payload(cohort: str, mode: str) -> dict:
    """
    One session's raw measurements. Accuracy is correlated with the
    cohort so the derived metrics stay internally consistent —
    a fatigued run should not also show 97% accuracy.
    """
    rts = COHORTS[cohort]()
    avg_rt = sum(rts) / len(rts)

    accuracy_base = {
        "healthy": 88.0, "sleep_deprived": 74.0, "attention_deficit": 69.0
    }[cohort]
    accuracy = float(np.clip(rng.normal(accuracy_base, 6.0), 0, 100))

    level = int(np.clip(rng.integers(1, 9), 1, 9))

    return {
        "mode": mode,
        "raw_reaction_times": [round(x, 2) for x in rts],
        "reaction_time_ms_avg": round(avg_rt, 2),
        "accuracy_rate": round(accuracy, 2),
        "completion_time_seconds": round(float(rng.normal(60, 8)), 2),
        "memory_span_level": level if mode == "memory" else None,
        "difficulty_progression_level": level,
        "cognitive_load_perceived": int(rng.integers(1, 11)),
        "user_satisfaction": int(rng.integers(1, 6)),
        "_highest_level": level,          # stripped before insert
    }