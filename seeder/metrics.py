"""
Pure metric derivations. No I/O, no Supabase, no side effects.

That isolation is deliberate: every function here takes numbers and
returns numbers, which is what makes them unit-testable without a
database. It mirrors why AdaptiveEngine is a stateless static class.

Return contract: None means "not enough data to derive", NOT zero.
Zero is a measurement; None is an absence. The columns are nullable
precisely to preserve that distinction.
"""
from __future__ import annotations
import math

#matches NUMERIC(5,2) in session_logs: 5 total digits, 2 decimal.
NUMERIC_5_2_MAX = 999.99

def clean(val: float | int | None) -> None:
    """
    Guard every value before it reaches Postgres.

    Ratio metrics can produce inf when a denominator is 0, and
    learnability_score can exceed three integer digits. Either
    overflows NUMERIC(5,2) and fails the ENTIRE row insert — not
    just the offending column. One bad float loses the session.
    """
    if val is None:
        return None
    try:
        num = float(val)  #also normalises numpy.float64
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    num = max(-NUMERIC_5_2_MAX, min(NUMERIC_5_2_MAX, num))
    return round(num, 2)

def clean_int(val: float | int | None) -> int | None:
    """Integer column: round then clamp."""
    c = clean(val)
    return None if c is None else int(round(c))

# Trival derivations

def error_rate(accuracy: float) -> float | None:
    """Inverse accuracy. max(0, ..) guards agains accuracy > 100."""
    return clean(max(0.0, 100.0 - accuracy))

def effectiveness_score(accuracy: float, highest_level: int) -> float | None:
    """Difficulty-weighted accuracy.
    Exists because raw accuracy is not comparable accross levels:
    100% at level 1 must not outrank 80% at level 9
    """
    return clean((accuracy/100) * highest_level)

def efficiency_score(accuracy: float, avg_rt: float) -> float | None:
    """
    Accuracy per unit time, scaled by 1000 for readable magnitude.
    Not a percentage - 90% accuracy at 400ms gives 225. This is why
    the column has no 0-100 check constraint.
    """
    if avg_rt is None or avg_rt <= 0:
        return None
    return clean((accuracy/ avg_rt) * 1000.0)

# The four that carry the project

def performance_stability_variance(rts: list[float]) -> float | None:
    """
    Population standard deviation of reaction time.

    Measures CONSISTENCY, not speed. A user averaging 400ms with sigma=30 is cognitively
    different from one averaging 400ms with sigma = 180-same mean, completely different.

    Population (ddof=0), not sample: we are describing the spread of the THIS sessions's obs,
    not estimating a population parameter from a sample. the session is the whole population.
    """
    if not rts or len(rts) <= 2:
        return None
    mean = sum(rts) / len(rts)
    variance = sum((x - mean) ** 2 for x in rts) / len(rts)
    return clean(math.sqrt(variance))


def attention_stability_score(rts: list[float]) -> float | None:
    """
    mean(first half)/mean(second half) - within-session fatigue.
    >1.0 got faster(warming up)
    =1.0 flat
    < 1.0 got slower (attention decay)
    
    odd lengths: split at n//2, so the middle element falls into the second half.
    Arbitrary but consistent, which is what matters.
    """
    if not rts or len(rts) <= 2:
        return None
    half = len(rts)//2
    first, second = rts[:half], rts[half:]
    if not first or not second:
        return None
    second_mean = sum(second) / len(second)
    if second_mean == 0:
        return None
    return clean((sum(first) / len(first))/ second_mean)

def learnability_score(rts: list[float]) -> float | None:
    """
    (rt[0]+rt[1]) / (rt[3] + rt[4]) - slopes of the initial learning curve.
    How fast the user figures out the task.

    >1.0 means the 4th/5tth responses beat the 1st/2nd/
    Needs n>5 so indices 3 and 4 exist with data after them.
    """
    if not rts or len(rts) <= 5:
        return None
    denom = rts[3] + rts[4]
    if denom == 0:
        return None
    return clean((rts[0] + rts[1]) / denom)


def adaptation_accuracy_score(rts: list[float], level:int) ->float |None:
    """
    Panic resistance: does performance collapse when difficulty peaks?

    In the final 20% of interactions, count responses slower than
    1.5x the session mean. Score = 100 - (panic / late * 100).

    Returns 100.0 at level 1 — difficulty never rose, so there was
    nothing to resist. Needs n > 10 for the 20% slice to be
    meaningful; a 20% slice of 5 clicks is one click.   
    """
    if not rts or len(rts) <= 10:
        return None
    if level <= 1:
        return 100.0

    mean = sum(rts)/len(rts)
    threshold = mean*1.5
    late_start = int(len(rts)* 0.8)
    late = rts[late_start:]
    if not late:
        return None

    panic = sum(1 for rt in late if rt > threshold)
    return clean(100.0 - (panic / len(late) * 100.0))


# ── Cross-session ────────────────────────────────────────────────

def learning_improvement_rate(
    accuracy: float, prior_accuracies: list[float]
) -> float | None:
    """
    This session's accuracy minus the mean of all prior sessions in
    the same mode. Signed: negative means a worse-than-usual day.

    Returns 0.0 on the first session — no baseline to compare to,
    and 0.0 ("no change") is more honest than None here because the
    absence is structural rather than a data gap.
    """
    if not prior_accuracies:
        return 0.0
    return clean(accuracy - (sum(prior_accuracies) / len(prior_accuracies)))

# Assembles

def derive_all(
        rts: list[float],
        accuracy: float,
        highest_level: int,
        prior_accuracies: list[float],
)-> dict:
    """
    Every derived column for one session_logs row.
    Key match the shema exactly, so seed.py can 
    splat this into the insert payload without remapping.
    """
    avg_rt = (sum(rts) / len(rts)) if rts else None
    return{
        "error_rate": error_rate(accuracy),
        "efficiency_score": efficiency_score(accuracy, avg_rt),
        "performance_stability_variance": performance_stability_variance(rts),
        "attention_stability_score": attention_stability_score(rts),
        "learning_improvement_rate": learning_improvement_rate(accuracy, prior_accuracies),
        "effectiveness_score": effectiveness_score(accuracy, highest_level),
        "learnability_score": learnability_score(rts),
        "adaptation_accuracy_score": adaptation_accuracy_score(rts, highest_level)


    }