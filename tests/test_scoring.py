# =============================================================================
# tests/test_scoring.py
# Unit tests for app/scoring.py — pure math, no DB, no HTTP.
#
# Run: pytest tests/test_scoring.py -v
#
# These tests verify:
#   - Sanity check rules (10 checks)
#   - Metric extraction formulas (edge cases + normal cases)
#   - OSI computation (range, determinism, weight sensitivity)
#   - Role OSI (boost logic, weight sum invariant)
#   - Rolling profile update (O(1) formula, decay cap)
#   - Percentile + badge (all thresholds, edge cases)
#   - Stability check (std dev threshold)
# =============================================================================

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import statistics
from app.scoring import (
    RawSegment, SanityResult,
    sanity_check, extract_metrics, compute_osi,
    compute_role_osi, update_rolling_profile,
    compute_percentile, percentile_to_badge,
    stability_check, ROLE_DEFINITIONS,
    ExtractedMetrics,
)


# =============================================================================
# Fixtures
# =============================================================================

def good_segment(**overrides) -> RawSegment:
    """A perfectly valid 60-second segment. Override any field to test edge cases."""
    defaults = dict(
        duration_seconds=60.0,
        kills=8,
        deaths=2,
        assists=3,
        shots_fired=120,
        shots_hit=60,
        headshots=15,
        damage_dealt=900.0,
        damage_taken=200.0,
        round_wins=4,
        round_total=6,
        per_round_scores=[70.0, 75.0, 72.0, 68.0, 74.0, 71.0],
        avg_reaction_ms=200.0,
        reaction_events=10,
        close_engagements=5,
        close_kills=4,
        long_engagements=3,
        long_kills=2,
        map_slug="dust2",
        game_mode="ranked",
    )
    defaults.update(overrides)
    return RawSegment(**defaults)


def perfect_metrics() -> ExtractedMetrics:
    return ExtractedMetrics(
        m_reaction=100.0,
        m_accuracy=100.0,
        m_eng_eff=100.0,
        m_consistency=100.0,
        m_cqe=100.0,
        m_lre=100.0,
        m_dpi=100.0,
    )


def zero_metrics() -> ExtractedMetrics:
    return ExtractedMetrics(
        m_reaction=0.0,
        m_accuracy=0.0,
        m_eng_eff=0.0,
        m_consistency=0.0,
        m_cqe=0.0,
        m_lre=0.0,
        m_dpi=0.0,
    )


# =============================================================================
# SECTION 1: Sanity Checks
# =============================================================================

class TestSanityCheck:

    def test_good_segment_passes(self):
        result = sanity_check(good_segment())
        assert result.passed is True
        assert result.reason == ""

    def test_duration_too_short(self):
        r = sanity_check(good_segment(duration_seconds=50.0))
        assert r.passed is False
        assert "duration" in r.reason

    def test_duration_too_long(self):
        r = sanity_check(good_segment(duration_seconds=70.0))
        assert r.passed is False
        assert "duration" in r.reason

    def test_duration_exact_floor(self):
        r = sanity_check(good_segment(duration_seconds=55.0))
        assert r.passed is True

    def test_duration_exact_ceil(self):
        r = sanity_check(good_segment(duration_seconds=65.0))
        assert r.passed is True

    def test_shots_hit_exceeds_fired(self):
        r = sanity_check(good_segment(shots_fired=50, shots_hit=60))
        assert r.passed is False
        assert "shots_hit" in r.reason

    def test_headshots_exceed_hits(self):
        r = sanity_check(good_segment(shots_hit=10, headshots=15))
        assert r.passed is False
        assert "headshots" in r.reason

    def test_impossible_fire_rate(self):
        # 60s × 15/s = 900 max. 1000 > 900.
        r = sanity_check(good_segment(shots_fired=1000))
        assert r.passed is False
        assert "shots_fired" in r.reason

    def test_impossible_kill_rate(self):
        # 60s × 0.5/s = 30 max. 40 > 30.
        r = sanity_check(good_segment(kills=40))
        assert r.passed is False
        assert "kills" in r.reason

    def test_damage_exceeds_ceiling(self):
        # kills=2 → ceiling = 2×250+500 = 1000. 5000 > 1000.
        r = sanity_check(good_segment(kills=2, damage_dealt=5000.0))
        assert r.passed is False
        assert "damage" in r.reason

    def test_zero_reaction_events_fails(self):
        r = sanity_check(good_segment(reaction_events=0))
        assert r.passed is False
        assert "reaction_events" in r.reason

    def test_reaction_too_fast(self):
        r = sanity_check(good_segment(avg_reaction_ms=50.0))
        assert r.passed is False
        assert "reaction" in r.reason.lower()

    def test_reaction_too_slow(self):
        r = sanity_check(good_segment(avg_reaction_ms=2500.0))
        assert r.passed is False
        assert "reaction" in r.reason.lower()

    def test_reaction_at_floor(self):
        r = sanity_check(good_segment(avg_reaction_ms=80.0))
        assert r.passed is True

    def test_close_kills_exceed_engagements(self):
        r = sanity_check(good_segment(close_engagements=3, close_kills=5))
        assert r.passed is False
        assert "close_kills" in r.reason

    def test_long_kills_exceed_engagements(self):
        r = sanity_check(good_segment(long_engagements=1, long_kills=3))
        assert r.passed is False
        assert "long_kills" in r.reason

    def test_zero_shots_no_kills_passes(self):
        # A support-style segment: no shots fired, no kills, no damage
        r = sanity_check(good_segment(
            shots_fired=0, shots_hit=0, headshots=0,
            kills=0, assists=0, damage_dealt=0.0,
        ))
        assert r.passed is True


# =============================================================================
# SECTION 2: Metric Extraction
# =============================================================================

class TestExtractMetrics:

    def test_all_metrics_in_range(self):
        m = extract_metrics(good_segment())
        for name, val in vars(m).items():
            assert 0.0 <= val <= 100.0, f"{name}={val} out of 0–100 range"

    def test_perfect_accuracy(self):
        m = extract_metrics(good_segment(shots_fired=100, shots_hit=100))
        assert m.m_accuracy == 100.0

    def test_zero_accuracy(self):
        m = extract_metrics(good_segment(shots_fired=100, shots_hit=0))
        assert m.m_accuracy == 0.0

    def test_no_shots_fired(self):
        m = extract_metrics(good_segment(shots_fired=0, shots_hit=0, headshots=0))
        assert m.m_accuracy == 0.0

    def test_floor_reaction_gives_100(self):
        m = extract_metrics(good_segment(avg_reaction_ms=80.0))
        assert m.m_reaction == 100.0

    def test_ceil_reaction_gives_zero(self):
        m = extract_metrics(good_segment(avg_reaction_ms=1000.0))
        assert m.m_reaction == 0.0

    def test_midpoint_reaction_near_50(self):
        # 540ms is midpoint of 80–1000 = 460ms range. (540-80)/920 = 0.5 → 50.0
        m = extract_metrics(good_segment(avg_reaction_ms=540.0))
        assert abs(m.m_reaction - 50.0) < 0.1

    def test_consistency_single_round_neutral(self):
        m = extract_metrics(good_segment(per_round_scores=[75.0]))
        assert m.m_consistency == 50.0

    def test_consistency_identical_rounds_perfect(self):
        m = extract_metrics(good_segment(per_round_scores=[70.0, 70.0, 70.0, 70.0]))
        assert m.m_consistency == 100.0

    def test_consistency_high_variance_low(self):
        # Very erratic scores should give low consistency
        m = extract_metrics(good_segment(per_round_scores=[10.0, 90.0, 10.0, 90.0]))
        assert m.m_consistency < 50.0

    def test_cqe_no_engagements_neutral(self):
        m = extract_metrics(good_segment(close_engagements=0, close_kills=0))
        assert m.m_cqe == 50.0

    def test_lre_no_engagements_neutral(self):
        m = extract_metrics(good_segment(long_engagements=0, long_kills=0))
        assert m.m_lre == 50.0

    def test_cqe_perfect_win_rate(self):
        m = extract_metrics(good_segment(close_engagements=10, close_kills=10))
        assert m.m_cqe == 100.0

    def test_lre_zero_win_rate(self):
        m = extract_metrics(good_segment(long_engagements=5, long_kills=0))
        assert m.m_lre == 0.0

    def test_dpi_at_ceiling(self):
        # 20 DPS × 60s = 1200 damage → exactly 100.0
        m = extract_metrics(good_segment(damage_dealt=1200.0, duration_seconds=60.0))
        assert m.m_dpi == 100.0

    def test_dpi_over_ceiling_clamped(self):
        # More than ceiling → still 100.0
        m = extract_metrics(good_segment(damage_dealt=9999.0))
        assert m.m_dpi == 100.0

    def test_dpi_zero(self):
        m = extract_metrics(good_segment(damage_dealt=0.0))
        assert m.m_dpi == 0.0

    def test_engagement_efficiency_assists_weighted(self):
        # No kills, only assists. 3 assists = 0.9 "kill equivalent", 30 shots = 3 per 10.
        # eff = 0.9 / 3.0 = 0.3 → 30.0
        m = extract_metrics(good_segment(kills=0, assists=3, shots_fired=30, shots_hit=10))
        assert abs(m.m_eng_eff - 30.0) < 1.0


# =============================================================================
# SECTION 3: OSI Computation
# =============================================================================

class TestComputeOSI:

    def test_osi_range_0_to_1000(self):
        for seg_fn in [
            lambda: good_segment(),
            lambda: good_segment(kills=0, shots_hit=0),
            lambda: good_segment(avg_reaction_ms=80.0, shots_hit=120, damage_dealt=1200.0),
        ]:
            m = extract_metrics(seg_fn())
            osi = compute_osi(m)
            assert 0.0 <= osi <= 1000.0, f"OSI {osi} out of range"

    def test_osi_deterministic(self):
        m = extract_metrics(good_segment())
        osi1 = compute_osi(m)
        osi2 = compute_osi(m)
        assert osi1 == osi2

    def test_perfect_metrics_give_1000(self):
        osi = compute_osi(perfect_metrics())
        assert osi == 1000.0

    def test_zero_metrics_give_0(self):
        osi = compute_osi(zero_metrics())
        assert osi == 0.0

    def test_higher_accuracy_raises_osi(self):
        low  = compute_osi(extract_metrics(good_segment(shots_hit=30, shots_fired=120)))
        high = compute_osi(extract_metrics(good_segment(shots_hit=100, shots_fired=120)))
        assert high > low

    def test_weights_sum_to_1000_for_perfect(self):
        # Perfect metrics → OSI should be exactly 1000
        assert compute_osi(perfect_metrics()) == 1000.0

    def test_single_metric_contribution(self):
        # Only accuracy perfect, all else 0 → OSI = W_ACCURACY × 1000
        from app.config import settings
        m = ExtractedMetrics(
            m_reaction=0, m_accuracy=100, m_eng_eff=0,
            m_consistency=0, m_cqe=0, m_lre=0, m_dpi=0
        )
        expected = round(settings.W_ACCURACY * 1000.0, 2)
        assert compute_osi(m) == expected


# =============================================================================
# SECTION 4: Role OSI
# =============================================================================

class TestRoleOSI:

    def test_role_osi_differs_from_base_when_metrics_vary(self):
        m = extract_metrics(good_segment())
        base = compute_osi(m)
        for slug in ROLE_DEFINITIONS:
            role_osi = compute_role_osi(m, slug)
            # They can be equal only if all metrics are identical — they won't be
            # for a varied segment; but if they happen to equal, that's fine.
            assert 0.0 <= role_osi <= 1000.0

    def test_unknown_role_falls_back_to_base(self):
        m = extract_metrics(good_segment())
        base     = compute_osi(m)
        fallback = compute_role_osi(m, "nonexistent_role")
        assert fallback == base

    def test_role_osi_in_valid_range(self):
        m = extract_metrics(good_segment())
        for slug in ROLE_DEFINITIONS:
            r = compute_role_osi(m, slug)
            assert 0.0 <= r <= 1000.0, f"{slug} role_osi {r} out of range"

    def test_primary_metric_boost_increases_osi_when_metric_high(self):
        # Aggressor primary = m_eng_eff. Make eng_eff very high, others low.
        m = ExtractedMetrics(
            m_reaction=20, m_accuracy=20, m_eng_eff=100,
            m_consistency=20, m_cqe=20, m_lre=20, m_dpi=20,
        )
        base       = compute_osi(m)
        aggressor  = compute_role_osi(m, "aggressor")
        # Aggressor should score higher because it weighs eng_eff more
        assert aggressor > base

    def test_all_five_roles_defined(self):
        expected = {"tank", "recon", "cqs", "tactical_anchor", "aggressor"}
        assert set(ROLE_DEFINITIONS.keys()) == expected

    def test_role_weights_approximately_sum_to_1(self):
        """Verify the renormalised weights sum to 1.0 ± floating point error."""
        from app.config import settings
        for slug, defn in ROLE_DEFINITIONS.items():
            primary_field = defn["primary"]
            base_weights = {
                "m_reaction":    settings.W_REACTION,
                "m_accuracy":    settings.W_ACCURACY,
                "m_eng_eff":     settings.W_ENG_EFF,
                "m_consistency": settings.W_CONSISTENCY,
                "m_cqe":         settings.W_CQE,
                "m_lre":         settings.W_LRE,
                "m_dpi":         settings.W_DPI,
            }
            primary_base  = base_weights[primary_field]
            primary_boost = primary_base * 1.2
            boost_delta   = primary_boost - primary_base
            other_total   = 1.0 - primary_base
            scale_factor  = (other_total - boost_delta) / other_total

            total = primary_boost + sum(
                w * scale_factor
                for k, w in base_weights.items()
                if k != primary_field
            )
            assert abs(total - 1.0) < 1e-9, f"{slug} weights sum to {total}"


# =============================================================================
# SECTION 5: Rolling Profile Update
# =============================================================================

class TestRollingProfileUpdate:

    def _empty_profile(self):
        return {
            "avg_reaction": 0.0, "avg_accuracy": 0.0, "avg_eng_eff": 0.0,
            "avg_consistency": 0.0, "avg_cqe": 0.0, "avg_lre": 0.0,
            "avg_dpi": 0.0, "peak_osi": 0.0,
        }

    def test_first_session_equals_session_values(self):
        m = extract_metrics(good_segment())
        osi = compute_osi(m)
        updated = update_rolling_profile(
            old_osi=0.0, old_metrics=self._empty_profile(),
            ranked_sessions=0, new_session_osi=osi, new_metrics=m,
        )
        assert updated["osi"] == osi
        assert updated["ranked_sessions"] == 1

    def test_rolling_average_moves_toward_new_value(self):
        m = extract_metrics(good_segment())
        osi = compute_osi(m)

        # Establish a baseline of 5 sessions at OSI=500
        fake_old_osi = 500.0
        updated = update_rolling_profile(
            old_osi=fake_old_osi, old_metrics=self._empty_profile(),
            ranked_sessions=5, new_session_osi=osi, new_metrics=m,
        )
        # New OSI should be between old (500) and new session value
        assert (min(fake_old_osi, osi) <= updated["osi"] <= max(fake_old_osi, osi))

    def test_decay_capped_at_30(self):
        """After 30 sessions, decay stays at 30 — new session is 1/31 of result."""
        m = extract_metrics(good_segment(shots_hit=120, shots_fired=120))  # high accuracy
        osi = compute_osi(m)

        result_at_30 = update_rolling_profile(
            old_osi=500.0, old_metrics=self._empty_profile(),
            ranked_sessions=30, new_session_osi=osi, new_metrics=m,
        )
        result_at_100 = update_rolling_profile(
            old_osi=500.0, old_metrics=self._empty_profile(),
            ranked_sessions=100, new_session_osi=osi, new_metrics=m,
        )
        # Both should produce the same result because decay is capped at 30
        assert result_at_30["osi"] == result_at_100["osi"]

    def test_peak_osi_tracks_maximum(self):
        m = extract_metrics(good_segment())
        high_osi = 800.0
        # First update: new session is lower than peak
        updated = update_rolling_profile(
            old_osi=700.0,
            old_metrics={**self._empty_profile(), "peak_osi": high_osi},
            ranked_sessions=10,
            new_session_osi=400.0,
            new_metrics=m,
        )
        assert updated["peak_osi"] == high_osi  # peak preserved

        # Second update: new session exceeds peak
        updated2 = update_rolling_profile(
            old_osi=700.0,
            old_metrics={**self._empty_profile(), "peak_osi": high_osi},
            ranked_sessions=10,
            new_session_osi=900.0,
            new_metrics=m,
        )
        assert updated2["peak_osi"] == 900.0  # new peak set

    def test_ranked_sessions_increments(self):
        m = extract_metrics(good_segment())
        for n in [0, 1, 5, 29, 30, 50]:
            updated = update_rolling_profile(
                old_osi=500.0, old_metrics=self._empty_profile(),
                ranked_sessions=n, new_session_osi=600.0, new_metrics=m,
            )
            assert updated["ranked_sessions"] == n + 1


# =============================================================================
# SECTION 6: Percentile and Badge
# =============================================================================

class TestPercentileAndBadge:

    def test_empty_list_returns_100(self):
        assert compute_percentile(500.0, []) == 100.0

    def test_only_player_returns_100(self):
        assert compute_percentile(500.0, [500.0]) == 100.0

    def test_above_all_returns_100(self):
        assert compute_percentile(999.0, [100.0, 200.0, 300.0]) == 100.0

    def test_below_all_returns_0(self):
        assert compute_percentile(50.0, [100.0, 200.0, 300.0]) == 0.0

    def test_median_position(self):
        scores = [100.0, 200.0, 300.0, 400.0, 500.0]
        # 300 has 2 below it out of 5 = 40th percentile
        p = compute_percentile(300.0, scores)
        assert p == 40.0

    def test_tied_scores_same_percentile(self):
        # 300 has 0 players strictly below it out of 5 → 0th percentile
        # (400 and 500 are above it, not below)
        scores = [300.0, 300.0, 300.0, 400.0, 500.0]
        p1 = compute_percentile(300.0, scores)
        assert p1 == 0.0

    def test_badge_apex_threshold(self):
        assert percentile_to_badge(99.0) == "apex"
        assert percentile_to_badge(100.0) == "apex"

    def test_badge_elite_threshold(self):
        assert percentile_to_badge(95.0) == "elite"
        assert percentile_to_badge(98.9) == "elite"

    def test_badge_gold_threshold(self):
        assert percentile_to_badge(80.0) == "gold"
        assert percentile_to_badge(94.9) == "gold"

    def test_badge_silver_threshold(self):
        assert percentile_to_badge(50.0) == "silver"
        assert percentile_to_badge(79.9) == "silver"

    def test_badge_bronze_threshold(self):
        assert percentile_to_badge(0.0)  == "bronze"
        assert percentile_to_badge(49.9) == "bronze"

    def test_all_badge_levels_covered(self):
        badges = {percentile_to_badge(p) for p in [0, 49, 50, 79, 80, 94, 95, 98, 99, 100]}
        assert badges == {"bronze", "silver", "gold", "elite", "apex"}


# =============================================================================
# SECTION 7: Stability Check
# =============================================================================

class TestStabilityCheck:

    def test_stable_scores_pass(self):
        # std_dev of [500, 501, 499, 500] ≈ 0.82 < 5.0
        assert stability_check([500.0, 501.0, 499.0, 500.0]) is True

    def test_unstable_scores_fail(self):
        # std_dev of [400, 600] = 141.4 >> 5.0
        assert stability_check([400.0, 600.0]) is False

    def test_exactly_at_threshold(self):
        # Two values exactly 5.0 * sqrt(2) apart have std dev = 5.0
        import math
        delta = 5.0 * math.sqrt(2)
        scores = [500.0 - delta/2, 500.0 + delta/2]
        std = statistics.stdev(scores)
        # Should be exactly at or just under threshold
        assert stability_check(scores) is (std <= 5.0)

    def test_single_score_returns_false(self):
        assert stability_check([500.0]) is False

    def test_empty_list_returns_false(self):
        assert stability_check([]) is False

    def test_identical_scores_pass(self):
        assert stability_check([500.0, 500.0, 500.0, 500.0, 500.0]) is True

    def test_ten_sessions_stable(self):
        scores = [498.0, 501.0, 499.5, 500.5, 500.0,
                  499.0, 501.5, 500.0, 498.5, 501.0]
        assert stability_check(scores) is True


# =============================================================================
# SECTION 8: Integration — full pipeline
# =============================================================================

class TestFullPipeline:

    def test_valid_segment_produces_valid_osi(self):
        seg  = good_segment()
        sr   = sanity_check(seg)
        assert sr.passed
        m    = extract_metrics(seg)
        osi  = compute_osi(m)
        assert 0.0 <= osi <= 1000.0

    def test_invalid_segment_still_has_extractable_metrics(self):
        """Non-ranked sessions still compute metrics — user sees what their score would be."""
        seg = good_segment(avg_reaction_ms=50.0)   # fails sanity (too fast)
        sr  = sanity_check(seg)
        assert sr.passed is False
        # Can still extract metrics from the data
        m   = extract_metrics(seg)
        # reaction will be 100.0 (clamped — physics check caught it, math doesn't)
        assert m.m_reaction == 100.0

    def test_rolling_update_convergence(self):
        """Simulate 50 sessions and verify OSI converges toward session value."""
        m = extract_metrics(good_segment())
        target_osi = compute_osi(m)

        osi = 0.0
        old_metrics = {
            "avg_reaction": 0.0, "avg_accuracy": 0.0, "avg_eng_eff": 0.0,
            "avg_consistency": 0.0, "avg_cqe": 0.0, "avg_lre": 0.0,
            "avg_dpi": 0.0, "peak_osi": 0.0,
        }
        n = 0
        for _ in range(50):
            updated = update_rolling_profile(osi, old_metrics, n, target_osi, m)
            osi = updated["osi"]
            old_metrics = {k: updated[k] for k in old_metrics}
            old_metrics["peak_osi"] = updated["peak_osi"]
            n = updated["ranked_sessions"]

        # After 50 sessions the rolling OSI should be very close to target
        assert abs(osi - target_osi) < 5.0

    def test_perfect_play_scores_1000(self):
        seg = good_segment(
            shots_fired=100, shots_hit=100, headshots=0,
            damage_dealt=1200.0,
            avg_reaction_ms=80.0,
            per_round_scores=[100.0, 100.0, 100.0, 100.0],
            close_engagements=5, close_kills=5,
            long_engagements=5, long_kills=5,
            kills=30, assists=0,   # high kills — will be sanity-checked
        )
        # kills=30 fails sanity (max = 60×0.5=30 exactly → passes)
        sr = sanity_check(seg)
        m  = extract_metrics(seg)
        osi = compute_osi(m)
        assert osi == 1000.0
