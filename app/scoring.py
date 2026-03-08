# =============================================================================
# app/scoring.py
# Deterministic OSI Scoring Engine — Module 3
#
# CORE GUARANTEE: Same input → same output. Always.
# No randomness. No ML. No external calls. Pure arithmetic.
#
# Call sequence from the upload pipeline:
#   raw JSON → RawSegment → sanity_check() → extract_metrics()
#                                          → compute_osi()
#                                          → compute_role_osi()
#                        ← raw JSON discarded here, metrics written to DB
#                                          → update_rolling_profile()
#                                          → compute_percentile()
#                                          → percentile_to_badge()
#
# All functions are pure (no side effects, no DB access).
# The session route in sessions.py owns all DB writes.
# =============================================================================

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional

from app.config import settings


# =============================================================================
# 1. INPUT SCHEMA
#    The raw JSON payload sent by the client for each 60-second segment.
#    This object is constructed, used for metric extraction, then discarded.
#    It is NEVER written to the database.
# =============================================================================

@dataclass
class RawSegment:
    """
    Expected fields in the uploaded JSON payload.
    All numeric fields represent totals over the 60-second segment.
    Optional fields default to 0 / empty so clients can omit them
    when the game mode doesn't generate that data.
    """
    # ── Core timing ───────────────────────────────────────────────────────────
    duration_seconds:   float         # target: 60s, tolerance: 55–65s

    # ── Combat stats ─────────────────────────────────────────────────────────
    kills:              int
    deaths:             int
    assists:            int
    shots_fired:        int
    shots_hit:          int
    headshots:          int           # must be ≤ shots_hit
    damage_dealt:       float
    damage_taken:       float

    # ── Round data ────────────────────────────────────────────────────────────
    round_wins:         int
    round_total:        int
    per_round_scores:   list          # list of floats, one per round played

    # ── Reaction events ───────────────────────────────────────────────────────
    # avg_reaction_ms: mean time from enemy spotted to first shot (milliseconds)
    avg_reaction_ms:    float
    reaction_events:    int           # how many spotted-enemy events were recorded

    # ── Range breakdown ───────────────────────────────────────────────────────
    close_engagements:  int           # ≤10m proxy
    close_kills:        int
    long_engagements:   int           # ≥25m proxy
    long_kills:         int

    # ── Optional metadata ─────────────────────────────────────────────────────
    map_slug:   str = ""              # e.g. "dust2", "mirage"
    game_mode:  str = "ranked"        # "ranked" | "casual" | "comp"


# =============================================================================
# 2. SANITY CHECKS
#    Physics-based validation. Any single failure → is_ranked = False.
#    The session is still stored for audit, but excluded from OSI calculations.
#    Returns (passed: bool, reason: str).
# =============================================================================

@dataclass
class SanityResult:
    passed: bool
    reason: str = ""


def sanity_check(seg: RawSegment) -> SanityResult:
    """
    Validate that the submitted metadata is physically plausible.

    These rules prevent grossly inflated stats from polluting rankings.
    Each rule is documented with its rationale.
    A single failure is enough to mark the session non-ranked.
    """
    dur = seg.duration_seconds

    # Duration window: 60s ± 5s tolerance for upload/network delay
    if not (55.0 <= dur <= 65.0):
        return SanityResult(False, f"duration {dur:.1f}s outside 55–65s window")

    # Hits cannot exceed shots fired (physical impossibility)
    if seg.shots_hit > seg.shots_fired:
        return SanityResult(False,
            f"shots_hit {seg.shots_hit} > shots_fired {seg.shots_fired}")

    # Headshots cannot exceed hits
    if seg.headshots > seg.shots_hit:
        return SanityResult(False,
            f"headshots {seg.headshots} > shots_hit {seg.shots_hit}")

    # Max fire rate: settings.MAX_FIRE_RATE shots/second (default: 15)
    # Covers full-auto SMGs at maximum ROF
    max_shots = dur * settings.MAX_FIRE_RATE
    if seg.shots_fired > max_shots:
        return SanityResult(False,
            f"shots_fired {seg.shots_fired} exceeds max {max_shots:.0f} "
            f"({settings.MAX_FIRE_RATE}/s × {dur:.0f}s)")

    # Max kill rate: 1 kill per 2 seconds (settings.MAX_KILL_RATE = 0.5/s)
    # Even the fastest pro players don't sustain this for a full minute
    max_kills = dur * settings.MAX_KILL_RATE
    if seg.kills > max_kills:
        return SanityResult(False,
            f"kills {seg.kills} exceeds max {max_kills:.0f} "
            f"({settings.MAX_KILL_RATE}/s × {dur:.0f}s)")

    # Damage ceiling: generous upper bound per kill + flat overhead
    # Allows for high-damage weapons, assists, and environment damage
    damage_ceil = seg.kills * 250 + 500
    if seg.damage_dealt > damage_ceil:
        return SanityResult(False,
            f"damage_dealt {seg.damage_dealt:.0f} exceeds ceiling {damage_ceil}")

    # Must have recorded at least one reaction event to score reaction speed
    if seg.reaction_events < 1:
        return SanityResult(False, "reaction_events must be ≥ 1")

    # Reaction time physical bounds:
    #   80ms floor: human physiological limit (fastest documented reactions)
    #   2000ms ceil: beyond this the player effectively wasn't engaging
    if not (settings.REACTION_MS_FLOOR <= seg.avg_reaction_ms <= 2000.0):
        return SanityResult(False,
            f"avg_reaction_ms {seg.avg_reaction_ms:.0f} outside "
            f"[{settings.REACTION_MS_FLOOR:.0f}, 2000]")

    # Range kills cannot exceed their engagement counts
    if seg.close_kills > seg.close_engagements:
        return SanityResult(False,
            f"close_kills {seg.close_kills} > close_engagements {seg.close_engagements}")

    if seg.long_kills > seg.long_engagements:
        return SanityResult(False,
            f"long_kills {seg.long_kills} > long_engagements {seg.long_engagements}")

    return SanityResult(True)


# =============================================================================
# 3. METRIC EXTRACTION
#    Maps raw segment fields to 7 normalised metrics (0.0 – 100.0).
#    Every formula is documented with its rationale and edge cases.
#    Output is stored in derived_sessions. Input is discarded.
# =============================================================================

@dataclass
class ExtractedMetrics:
    """Seven normalised metrics on a 0.0–100.0 scale."""
    m_reaction:    float   # Reaction Speed            (weight: 15%)
    m_accuracy:    float   # Shot Accuracy             (weight: 20%)
    m_eng_eff:     float   # Engagement Efficiency     (weight: 15%)
    m_consistency: float   # Round-to-Round Stability  (weight: 15%)
    m_cqe:         float   # Close Quarters Eff.       (weight: 10%)
    m_lre:         float   # Long-Range Eff.           (weight: 10%)
    m_dpi:         float   # Damage Pressure Index     (weight: 15%)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a value to [lo, hi]. Used throughout normalisation."""
    return max(lo, min(hi, value))


def extract_metrics(seg: RawSegment) -> ExtractedMetrics:
    """
    Convert raw segment fields into 7 normalised metrics (0.0–100.0).

    Design principle: metrics are independent — a player who never engages
    at long range is not penalised for long-range effectiveness. Instead,
    neutral values (50.0) are returned when the game situation didn't
    generate data for a metric.
    """

    # ── m_reaction: Reaction Speed ─────────────────────────────────────────
    # Linear map: FLOOR ms → 100.0,  CEIL ms → 0.0
    # Formula: (1 - (avg_ms - FLOOR) / (CEIL - FLOOR)) × 100
    # 80ms  → 100.0  (world-class reflex, ~3σ from mean)
    # 540ms → 50.0   (average human reaction to visual stimulus)
    # 1000ms → 0.0   (slow / distracted)
    reaction_range = settings.REACTION_MS_CEIL - settings.REACTION_MS_FLOOR
    m_reaction = _clamp(
        1.0 - (seg.avg_reaction_ms - settings.REACTION_MS_FLOOR) / reaction_range
    ) * 100.0

    # ── m_accuracy: Shot Accuracy ─────────────────────────────────────────
    # Simple hit / fired ratio.
    # 0 shots_fired → 0.0 (avoids ZeroDivisionError; session failed sanity if
    # shots_fired=0 and kills>0, but we handle gracefully anyway)
    if seg.shots_fired > 0:
        m_accuracy = _clamp(seg.shots_hit / seg.shots_fired) * 100.0
    else:
        m_accuracy = 0.0

    # ── m_eng_eff: Engagement Efficiency ────────────────────────────────
    # "How many kills+assists did you get per 10 shots fired?"
    # Ceiling at 1.0: meaning 1 kill per 10 shots = perfect efficiency.
    # Assists count 30% of a kill — reward team play without overpowering solo frags.
    # max(shots/10, 1) prevents ZeroDivisionError when shots_fired=0.
    eff_raw = (seg.kills + 0.3 * seg.assists) / max(seg.shots_fired / 10.0, 1.0)
    m_eng_eff = _clamp(eff_raw) * 100.0

    # ── m_consistency: Round-to-Round Stability ──────────────────────────
    # Coefficient of Variation (std_dev / mean) of per-round scores.
    # Low CV = consistent performance. High CV = erratic (big peaks and troughs).
    # Formula: (1 - CV) × 100, clamped to 0–100.
    # Special cases:
    #   < 2 rounds  → 50.0 (neutral — not enough data to reward or penalise)
    #   mean = 0    → 50.0 (neutral — all rounds scored 0)
    if len(seg.per_round_scores) >= 2:
        mean_score = statistics.mean(seg.per_round_scores)
        if mean_score > 0:
            cv = statistics.stdev(seg.per_round_scores) / mean_score
            m_consistency = _clamp(1.0 - cv) * 100.0
        else:
            m_consistency = 50.0
    else:
        m_consistency = 50.0  # insufficient round data

    # ── m_cqe: Close Quarters Effectiveness ─────────────────────────────
    # Win rate on close-range engagements (≤10m proxy).
    # 0 close engagements → 50.0 (neutral — map didn't generate close-range
    # situations; the player is not penalised for map geometry)
    if seg.close_engagements > 0:
        m_cqe = _clamp(seg.close_kills / seg.close_engagements) * 100.0
    else:
        m_cqe = 50.0

    # ── m_lre: Long-Range Effectiveness ────────────────────────────────
    # Win rate on long-range engagements (≥25m proxy).
    # Same neutral default logic as CQE.
    if seg.long_engagements > 0:
        m_lre = _clamp(seg.long_kills / seg.long_engagements) * 100.0
    else:
        m_lre = 50.0

    # ── m_dpi: Damage Pressure Index ────────────────────────────────────
    # Sustained damage output per second, normalised to DPS_CEILING (default 20).
    # Formula: damage_dealt / (duration_seconds × DPS_CEILING)
    # Rationale: a player who sustains 20 DPS for 60s dealt 1200 damage —
    # enough to secure ~12 kills if all land on 100-HP targets. This ceiling
    # captures aggressive, sustained pressure rather than burst damage.
    dpi_raw = seg.damage_dealt / (seg.duration_seconds * settings.DPS_CEILING)
    m_dpi = _clamp(dpi_raw) * 100.0

    return ExtractedMetrics(
        m_reaction=    round(m_reaction,    4),
        m_accuracy=    round(m_accuracy,    4),
        m_eng_eff=     round(m_eng_eff,     4),
        m_consistency= round(m_consistency, 4),
        m_cqe=         round(m_cqe,         4),
        m_lre=         round(m_lre,         4),
        m_dpi=         round(m_dpi,         4),
    )


# =============================================================================
# 4. OSI COMPUTATION
#    Weighted sum of all 7 normalised metrics × 1000.
#    Range: 0–1000. Deterministic: identical input → identical output.
# =============================================================================

def compute_osi(metrics: ExtractedMetrics) -> float:
    """
    Overall Skill Index (OSI) — range 0–1000.

    Formula:
        OSI = (
            m_reaction    / 100 × W_REACTION    +
            m_accuracy    / 100 × W_ACCURACY    +
            m_eng_eff     / 100 × W_ENG_EFF     +
            m_consistency / 100 × W_CONSISTENCY +
            m_cqe         / 100 × W_CQE         +
            m_lre         / 100 × W_LRE         +
            m_dpi         / 100 × W_DPI
        ) × 1000

    Each metric is divided by 100 to convert 0–100 scale → 0–1 before weighting.
    The × 1000 gives a human-readable 0–1000 range.

    Weights are read from settings (config.py) so they can be adjusted
    via environment variables without touching this function.
    """
    s = settings
    osi_unit = (
        (metrics.m_reaction    / 100.0) * s.W_REACTION    +
        (metrics.m_accuracy    / 100.0) * s.W_ACCURACY    +
        (metrics.m_eng_eff     / 100.0) * s.W_ENG_EFF     +
        (metrics.m_consistency / 100.0) * s.W_CONSISTENCY +
        (metrics.m_cqe         / 100.0) * s.W_CQE         +
        (metrics.m_lre         / 100.0) * s.W_LRE         +
        (metrics.m_dpi         / 100.0) * s.W_DPI
    )
    return round(_clamp(osi_unit) * 1000.0, 2)


# =============================================================================
# 5. ROLE-SPECIFIC OSI
#    Same 7 stored metrics, primary metric boosted ×1.2.
#    Weights are renormalised so they still sum to 1.0.
#    No extra DB storage needed — computed from existing metrics on demand.
# =============================================================================

# Role definitions: primary metric and display info.
# Primary metric gets ×1.2 weight in role_osi calculation.
ROLE_DEFINITIONS: dict = {
    "tank": {
        "primary":     "m_dpi",
        "display":     "Tank",
        "description": "Sustained damage output and map presence.",
    },
    "recon": {
        "primary":     "m_lre",
        "display":     "Recon",
        "description": "Long-range dominance and reaction precision.",
    },
    "cqs": {
        "primary":     "m_cqe",
        "display":     "Close Quarters Specialist",
        "description": "Short-range dominance and aggressive entry.",
    },
    "tactical_anchor": {
        "primary":     "m_consistency",
        "display":     "Tactical Anchor",
        "description": "Reliable, stable performance under pressure.",
    },
    "aggressor": {
        "primary":     "m_eng_eff",
        "display":     "Aggressor",
        "description": "High kill efficiency and aggressive fragging.",
    },
}


def compute_role_osi(metrics: ExtractedMetrics, role_slug: str) -> float:
    """
    Role-specific OSI: the primary metric's weight is multiplied by 1.2,
    and the surplus weight is distributed proportionally across the other
    six metrics so the total still sums to 1.0.

    Example — aggressor (primary = m_eng_eff, base weight = 0.15):
        primary_boosted = 0.15 × 1.2 = 0.180
        boost_delta     = 0.180 - 0.15 = 0.030
        other_total     = 1.0 - 0.15  = 0.85
        scale_factor    = (0.85 - 0.030) / 0.85 ≈ 0.9647
        each other weight × 0.9647
        sum check: 0.180 + 0.85 × 0.9647 ≈ 1.000 ✓

    Falls back to compute_osi() for unknown role slugs.
    """
    if role_slug not in ROLE_DEFINITIONS:
        return compute_osi(metrics)

    primary_field = ROLE_DEFINITIONS[role_slug]["primary"]
    s = settings

    base_weights = {
        "m_reaction":    s.W_REACTION,
        "m_accuracy":    s.W_ACCURACY,
        "m_eng_eff":     s.W_ENG_EFF,
        "m_consistency": s.W_CONSISTENCY,
        "m_cqe":         s.W_CQE,
        "m_lre":         s.W_LRE,
        "m_dpi":         s.W_DPI,
    }

    primary_base  = base_weights[primary_field]
    primary_boost = primary_base * 1.2
    boost_delta   = primary_boost - primary_base
    other_total   = 1.0 - primary_base
    scale_factor  = (other_total - boost_delta) / other_total

    adjusted = {}
    for key, w in base_weights.items():
        adjusted[key] = primary_boost if key == primary_field else w * scale_factor

    metric_vals = {
        "m_reaction":    metrics.m_reaction    / 100.0,
        "m_accuracy":    metrics.m_accuracy    / 100.0,
        "m_eng_eff":     metrics.m_eng_eff     / 100.0,
        "m_consistency": metrics.m_consistency / 100.0,
        "m_cqe":         metrics.m_cqe         / 100.0,
        "m_lre":         metrics.m_lre         / 100.0,
        "m_dpi":         metrics.m_dpi         / 100.0,
    }

    role_osi_unit = sum(metric_vals[k] * adjusted[k] for k in adjusted)
    return round(_clamp(role_osi_unit) * 1000.0, 2)


# =============================================================================
# 6. ROLLING PROFILE UPDATE
#    O(1) incremental update — no historical query needed.
#    Called after every ranked session to update skill_profiles.
# =============================================================================

def update_rolling_profile(
    old_osi:          float,
    old_metrics:      dict,    # keys: avg_reaction, avg_accuracy, etc.
    ranked_sessions:  int,     # session count BEFORE this update
    new_session_osi:  float,
    new_metrics:      ExtractedMetrics,
) -> dict:
    """
    Update rolling averages for all 7 metrics and overall OSI.

    Formula (exponential-style rolling average with capped history):
        decay    = min(ranked_sessions, 30)
        new_avg  = (old_avg × decay + new_value) / (decay + 1)

    Why cap at 30?
      - Sessions 1–30: each new session carries proportionally more weight
        as history builds up. Early sessions establish the baseline.
      - Sessions 31+: each new session counts as ~1/31 of the rolling score.
        A player can meaningfully improve within ~30 sessions.
      - Without a cap: after 1000 sessions, one improvement would shift
        the score by only 0.1% — too slow to reflect real skill growth.

    Returns a dict ready to UPSERT into skill_profiles.
    Does NOT commit to DB — the caller (sessions.py) owns the transaction.
    """
    decay = min(ranked_sessions, 30)

    def roll(old: float, new_val: float) -> float:
        return round((old * decay + new_val) / (decay + 1), 4)

    new_osi = roll(old_osi, new_session_osi)

    return {
        "osi":             new_osi,
        "avg_reaction":    roll(old_metrics.get("avg_reaction",    0.0), new_metrics.m_reaction),
        "avg_accuracy":    roll(old_metrics.get("avg_accuracy",    0.0), new_metrics.m_accuracy),
        "avg_eng_eff":     roll(old_metrics.get("avg_eng_eff",     0.0), new_metrics.m_eng_eff),
        "avg_consistency": roll(old_metrics.get("avg_consistency", 0.0), new_metrics.m_consistency),
        "avg_cqe":         roll(old_metrics.get("avg_cqe",         0.0), new_metrics.m_cqe),
        "avg_lre":         roll(old_metrics.get("avg_lre",         0.0), new_metrics.m_lre),
        "avg_dpi":         roll(old_metrics.get("avg_dpi",         0.0), new_metrics.m_dpi),
        "ranked_sessions": ranked_sessions + 1,
        "peak_osi":        max(old_metrics.get("peak_osi", 0.0), new_session_osi),
    }


# =============================================================================
# 7. PERCENTILE + BADGE
#    Relative ranking against all active players this season.
#    Called during leaderboard cache rebuild, not per-upload.
# =============================================================================

def compute_percentile(user_osi: float, all_osi_scores: list) -> float:
    """
    Calculate the percentile this user's OSI falls in.

    Formula: (players with OSI strictly < user_osi) / total_players × 100

    Returns 0.0–100.0.

    Edge cases:
      - Empty list → 100.0 (first player is top by definition)
      - Tied scores: this player is counted above all strictly lower scores.
        Two players with the same OSI will have the same percentile.
    """
    if not all_osi_scores:
        return 100.0
    total = len(all_osi_scores)
    below = sum(1 for s in all_osi_scores if s < user_osi)
    # Single player with same score as themselves → 0 below, 1 total → 0th percentile
    # But conceptually a single player IS top. Use (below / total)*100 then override
    # for the edge case where all scores tie with user.
    if below == 0 and all(s >= user_osi for s in all_osi_scores):
        # Everyone ties or is above — check if user is in the list at all
        # If all scores == user_osi, user is tied for top → return 100.0
        if all(s == user_osi for s in all_osi_scores):
            return 100.0
    return round((below / total) * 100.0, 2)


def percentile_to_badge(percentile: float) -> str:
    """
    Map a percentile to a tier badge label.

    Percentile-based thresholds self-calibrate as the player base grows —
    badge counts remain proportionally stable regardless of total user count.

    apex   ≥ 99.0 → top 1%
    elite  ≥ 95.0 → top 5%
    gold   ≥ 80.0 → top 20%
    silver ≥ 50.0 → top 50%
    bronze          everyone else
    """
    if percentile >= 99.0: return "apex"
    if percentile >= 95.0: return "elite"
    if percentile >= 80.0: return "gold"
    if percentile >= 50.0: return "silver"
    return "bronze"


# =============================================================================
# 8. STABILITY CHECK
#    Used by role certification: Candidate → Certified requires
#    consistent OSI scores across the last N sessions.
# =============================================================================

def stability_check(last_n_osi_scores: list) -> bool:
    """
    Return True if the player's recent OSI scores are stable enough
    to advance from Candidate to Certified.

    Condition: std_dev(last N scores) ≤ CERTIFIED_STABILITY_STD (default 5.0)

    Why OSI-scale std dev (not normalised)?
    At OSI 0–1000 scale, std dev of 5.0 = 0.5% variation.
    This confirms the player performs reliably at their stated level.

    Requires ≥ 2 scores to compute std dev.
    """
    if len(last_n_osi_scores) < 2:
        return False
    std = statistics.stdev(last_n_osi_scores)
    return std <= settings.CERTIFIED_STABILITY_STD
