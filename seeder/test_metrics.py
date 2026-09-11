"""
Hand-computed expectations. Every number below was worked out on
paper first — that is the point of the exercise.
"""

import math
import pytest

from seeder.metrics import (
    clean, error_rate, efficiency_score, effectiveness_score,
    performance_stability_variance, attention_stability_score,
    learnability_score, adaptation_accuracy_score,
    learning_improvement_rate,
)


class TestClean:
    def test_clamps_high(self):      assert clean(50_000) == 999.99
    def test_clamps_low(self):       assert clean(-50_000) == -999.99
    def test_infinity_is_none(self): assert clean(float("inf")) is None
    def test_nan_is_none(self):      assert clean(float("nan")) is None
    def test_none_passthrough(self): assert clean(None) is None
    def test_rounds_to_two(self):    assert clean(3.14159) == 3.14


class TestStabilityVariance:
    def test_hand_computed(self):
        # [10,20,30,40,50] mean=30
        # deviations: -20,-10,0,10,20 → squares 400,100,0,100,400
        # sum=1000, /5=200, sqrt(200)=14.142…
        assert performance_stability_variance([10, 20, 30, 40, 50]) == 14.14

    def test_zero_variance(self):
        assert performance_stability_variance([400, 400, 400, 400]) == 0.0

    def test_too_short(self):
        assert performance_stability_variance([1, 2]) is None


class TestAttentionStability:
    def test_slowed_down(self):
        # first [100,100] mean 100; second [200,200] mean 200 → 0.5
        assert attention_stability_score([100, 100, 200, 200]) == 0.5

    def test_sped_up(self):
        assert attention_stability_score([200, 200, 100, 100]) == 2.0

    def test_zero_denominator(self):
        assert attention_stability_score([100, 100, 0, 0]) is None


class TestLearnability:
    def test_hand_computed(self):
        # (10+20) / (40+50) = 30/90 = 0.333…
        assert learnability_score([10, 20, 30, 40, 50, 60]) == 0.33

    def test_needs_more_than_five(self):
        assert learnability_score([10, 20, 30, 40, 50]) is None


class TestAdaptationAccuracy:
    def test_level_one_returns_full(self):
        assert adaptation_accuracy_score([100] * 20, level=1) == 100.0

    def test_no_panic_clicks(self):
        assert adaptation_accuracy_score([400] * 20, level=5) == 100.0

    def test_all_late_clicks_panic(self):
        # 16 fast + 4 very slow; mean ≈ 480, threshold ≈ 720
        # final 20% = 4 clicks, all above threshold → 0.0
        rts = [400] * 16 + [2000] * 4
        assert adaptation_accuracy_score(rts, level=5) == 0.0

    def test_too_short(self):
        assert adaptation_accuracy_score([400] * 10, level=5) is None


class TestSimple:
    def test_error_rate(self):          assert error_rate(87.5) == 12.5
    def test_error_rate_floors(self):   assert error_rate(105) == 0.0
    def test_efficiency(self):          assert efficiency_score(90, 400) == 225.0
    def test_efficiency_zero_rt(self):  assert efficiency_score(90, 0) is None
    def test_effectiveness(self):       assert effectiveness_score(80, 9) == 7.2


class TestLearningImprovement:
    def test_first_session(self):
        assert learning_improvement_rate(80, []) == 0.0

    def test_negative_is_allowed(self):
        # 70 - mean(80,90) = 70 - 85 = -15
        assert learning_improvement_rate(70, [80, 90]) == -15.0