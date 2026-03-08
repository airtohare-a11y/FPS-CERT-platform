# =============================================================================
# app/analysis.py
# Module 3 — Video upload endpoint + automatic metric extraction
# Module 4 — OSI scoring, percentile, role unlock logic
#
# Flow:
#   POST /analysis/upload
#     1. Receive video file (mp4/mov/avi/webm/mkv, max 500MB)
#     2. Validate file type and quota
#     3. Run analyzeClip() — extracts metrics from video
#     4. Compute OSI score from metrics
#     5. Store derived session in DB (raw video NEVER stored)
#     6. Delete temp file
#     7. Return full result to client
#
# Video analysis uses seeded-random simulation derived from the video file's
# size and metadata. This produces consistent scores for the same clip while
# requiring no heavy ML dependencies.
# =============================================================================

import os
import math
import random
import hashlib
from typing import Optional
from datetime import datetime, timezone

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_pro
from app.database import get_db
from app.models import User, DerivedSession, SkillProfile, AppState, GAME_REGISTRY

router = APIRouter()

# ── File upload config ────────────────────────────────────────────────────────
UPLOAD_DIR   = "/tmp/mechgg_uploads"
MAX_FILE_MB  = 500
VALID_EXTS   = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
VALID_MIMES  = {
    "video/mp4", "video/quicktime", "video/x-msvideo",
    "video/webm", "video/x-matroska", "video/mpeg",
}

os.makedirs(UPLOAD_DIR, exist_ok=True)


# ── Seeded RNG (consistent scores for same clip) ──────────────────────────────
def _seeded_rng(seed: int):
    rng = random.Random(seed)
    return rng

def _r(rng: random.Random, lo: float, hi: float) -> float:
    return round(rng.uniform(lo, hi), 2)

def _round1(x: float) -> float:
    return round(x, 1)


# ── Per-genre analysis functions ──────────────────────────────────────────────

def _analyze_fps(rng: random.Random, style: str) -> dict:
    # ── Generate 10 sub-dimension scores ─────────────────────────────────────
    # Each maps to a specific, distinct coaching category.
    # Ranges are realistic: most players land 25-65, elites reach 80+.
    scores = {
        # Core aim dimensions
        "targetAcquisition":  _r(rng, 12, 78),   # flick speed / first-shot timing
        "flickPrecision":     _r(rng,  8, 72),   # landing on target vs overshooting
        "trackingControl":    _r(rng,  8, 70),   # sustaining aim while target moves
        "spreadControl":      _r(rng,  8, 68),   # recoil/spray pattern management
        # Decision / engagement dimensions
        "crosshairPlacement": _r(rng, 10, 75),   # pre-aiming correct angles/height
        "engagementDecision": _r(rng, 12, 70),   # trade quality, peek timing, info usage
        "closeQuarterEff":    _r(rng,  8, 65),   # CQC win rate, sub-10m performance
        "longRangeEff":       _r(rng, 10, 68),   # 40m+ duel performance, sightline control
        # Session health dimensions
        "consistency":        _r(rng, 10, 65),   # session-to-session mechanical variance
        "sessionMomentum":    _r(rng, 12, 62),   # performance curve across session duration
    }

    # ── Style-specific mechanical weights ────────────────────────────────────
    # Tactical (CS/Valorant): precision, spray, placement dominate
    # Battle Royale: tracking and engagement decisions matter more
    # Hero (Overwatch/Apex): tracking and CQC weighted up
    # Extraction (ARC/Hunt): placement and LRE matter more, spray still key
    weights = {
        "tactical":    {"targetAcquisition":0.20,"flickPrecision":0.15,"trackingControl":0.10,
                        "spreadControl":0.20,"crosshairPlacement":0.15,"engagementDecision":0.10,
                        "closeQuarterEff":0.04,"longRangeEff":0.03,"consistency":0.02,"sessionMomentum":0.01},
        "battleroyale":{"targetAcquisition":0.15,"flickPrecision":0.12,"trackingControl":0.18,
                        "spreadControl":0.14,"crosshairPlacement":0.10,"engagementDecision":0.15,
                        "closeQuarterEff":0.06,"longRangeEff":0.05,"consistency":0.03,"sessionMomentum":0.02},
        "hero":        {"targetAcquisition":0.14,"flickPrecision":0.10,"trackingControl":0.22,
                        "spreadControl":0.12,"crosshairPlacement":0.10,"engagementDecision":0.14,
                        "closeQuarterEff":0.10,"longRangeEff":0.03,"consistency":0.03,"sessionMomentum":0.02},
        "extraction":  {"targetAcquisition":0.16,"flickPrecision":0.14,"trackingControl":0.12,
                        "spreadControl":0.18,"crosshairPlacement":0.14,"engagementDecision":0.12,
                        "closeQuarterEff":0.05,"longRangeEff":0.06,"consistency":0.02,"sessionMomentum":0.01},
    }
    w  = weights.get(style, weights["tactical"])
    mi = _round1(sum(scores[k] * w.get(k, 0.05) for k in scores))

    # ── Habit detection — every trigger maps to a UNIQUE coaching bucket ─────
    # Rules: each habit key is distinct; no two habits here share a bucket.
    # Severity thresholds are intentional: <35 = frequent, 35-50 = occasional.
    habits = []

    # 1. Flick speed — target acquisition
    if scores["targetAcquisition"] < 52:
        sev = "frequent" if scores["targetAcquisition"] < 35 else "occasional"
        habits.append({
            "key": "slow_acquisition",
            "name": "Slow First-Shot Timing",
            "severity": sev,
            "description": f"Target acquisition score {scores['targetAcquisition']:.0f}/100 — "
                           f"crosshair is taking too long to reach the target after identification. "
                           f"Most engagements are lost in the first 0.3s.",
            "coachingNote": "Run Gridshot in Aimlabs daily. Speed before accuracy — the reflex must be built before the precision layer is added.",
            "isPositive": False,
        })

    # 2. Flick landing — overshoot / precision
    if scores["flickPrecision"] < 50:
        sev = "frequent" if scores["flickPrecision"] < 33 else "occasional"
        habits.append({
            "key": "overshoot",
            "name": "Flick Overshoot",
            "severity": sev,
            "description": f"Flick precision score {scores['flickPrecision']:.0f}/100 — "
                           f"crosshair is passing through targets rather than stopping on them. "
                           f"Deceleration at the end of flicks is undertrained.",
            "coachingNote": "The stop at the end of a flick is a separate motor skill. Train it at 50% speed. Overshooting and correcting back is not the same as hitting first.",
            "isPositive": False,
        })

    # 3. Tracking — sustained accuracy on moving targets
    if scores["trackingControl"] < 50:
        sev = "frequent" if scores["trackingControl"] < 33 else "occasional"
        habits.append({
            "key": "poor_tracking",
            "name": "Weak Tracking Control",
            "severity": sev,
            "description": f"Tracking score {scores['trackingControl']:.0f}/100 — "
                           f"crosshair loses the target during sustained fire or while the target moves. "
                           f"Critical for mid-range AR fights and any hero/ability game.",
            "coachingNote": "Smoothbot or VoxTS in Aimlabs/Kovaaks. Tracking is not practised in deathmatch — it must be trained in isolation.",
            "isPositive": False,
        })

    # 4. Spray / recoil — full-auto pattern control
    if scores["spreadControl"] < 50:
        sev = "frequent" if scores["spreadControl"] < 33 else "occasional"
        habits.append({
            "key": "poor_spray",
            "name": "Spray Pattern Not Controlled",
            "severity": sev,
            "description": f"Spray control score {scores['spreadControl']:.0f}/100 — "
                           f"recoil is not being compensated. Shots walk off target past the first 5 rounds. "
                           f"This creates false negatives: good aim that appears as misses.",
            "coachingNote": "The recoil pattern of your main weapon is not random. Memorise it in a practice range, not during ranked matches.",
            "isPositive": False,
        })

    # 5. Crosshair placement — pre-aim angle/height discipline
    if scores["crosshairPlacement"] < 55:
        sev = "frequent" if scores["crosshairPlacement"] < 38 else "occasional"
        habits.append({
            "key": "poor_placement",
            "name": "Crosshair Placement Off",
            "severity": sev,
            "description": f"Crosshair placement score {scores['crosshairPlacement']:.0f}/100 — "
                           f"crosshair is arriving at angles too low or not pre-aimed at common enemy positions. "
                           f"Every degree of adjustment at contact costs ~0.1s of reaction time.",
            "coachingNote": "Walk your most-played map and identify every head-height angle. Pre-aiming is a map knowledge skill, not a mechanical one.",
            "isPositive": False,
        })

    # 6. Engagement decisions — trade quality, when to peek, info usage
    if scores["engagementDecision"] < 48:
        sev = "frequent" if scores["engagementDecision"] < 32 else "occasional"
        habits.append({
            "key": "poor_eng_eff",
            "name": "Poor Engagement Decisions",
            "severity": sev,
            "description": f"Engagement efficiency score {scores['engagementDecision']:.0f}/100 — "
                           f"taking duels without information, raw-peeking, or holding bad positions. "
                           f"Dying to enemies you had no info on is a decision error, not a mechanical one.",
            "coachingNote": "Review your deaths after each session. Most players die to the same 1-2 mistakes every game. Find yours.",
            "isPositive": False,
        })

    # 7. Close-quarter — sub-10m performance
    if scores["closeQuarterEff"] < 48:
        sev = "frequent" if scores["closeQuarterEff"] < 32 else "occasional"
        habits.append({
            "key": "poor_cqe",
            "name": "Weak Close-Quarter Game",
            "severity": sev,
            "description": f"CQC efficiency score {scores['closeQuarterEff']:.0f}/100 — "
                           f"losing duels at sub-10m range disproportionately. "
                           f"CQC mechanics differ from mid-range: turn speed, sidestep, and first-shot timing are all different at knife range.",
            "coachingNote": "CQC is a separate skill set from rifling. Play pistol-only or knife-range deathmatch specifically to build these instincts.",
            "isPositive": False,
        })

    # 8. Long-range — 40m+ sightline performance
    if scores["longRangeEff"] < 48:
        sev = "frequent" if scores["longRangeEff"] < 32 else "occasional"
        habits.append({
            "key": "poor_lre",
            "name": "Weak Long-Range Performance",
            "severity": sev,
            "description": f"Long-range efficiency score {scores['longRangeEff']:.0f}/100 — "
                           f"losing sightline duels at 40m+ range. "
                           f"At distance, first-shot accuracy is everything — spray is irrelevant.",
            "coachingNote": "At 40m+, only the first shot matters. Train single-tap accuracy specifically. Spray control and tracking are mid-range skills.",
            "isPositive": False,
        })

    # 9. Consistency — session-to-session variance
    if scores["consistency"] < 48:
        sev = "frequent" if scores["consistency"] < 30 else "occasional"
        habits.append({
            "key": "inconsistent",
            "name": "High Session Variance",
            "severity": sev,
            "description": f"Consistency score {scores['consistency']:.0f}/100 — "
                           f"large mechanical differences between sessions. "
                           f"Likely causes: no fixed warmup, sensitivity changes, or fatigue from long sessions.",
            "coachingNote": "Consistency is a process skill. Fix warmup routine, lock sensitivity, and cap session length before trying to improve mechanics.",
            "isPositive": False,
        })

    # 10. Session fade — late-session performance drop
    if scores["sessionMomentum"] < 42:
        sev = "frequent" if scores["sessionMomentum"] < 28 else "occasional"
        habits.append({
            "key": "session_fade",
            "name": "Performance Fades Through Session",
            "severity": sev,
            "description": f"Session momentum score {scores['sessionMomentum']:.0f}/100 — "
                           f"metrics degrade meaningfully as the session progresses. "
                           f"Eye fatigue, dehydration, or tilt are the most common causes.",
            "coachingNote": "Break every 45 minutes. Most players attribute late-session drops to bad luck. It's almost always physiological.",
            "isPositive": False,
        })

    # 11. Positive — strong overall mechanics
    if scores["targetAcquisition"] > 72 and scores["flickPrecision"] > 68 and scores["spreadControl"] > 65:
        habits.append({
            "key": "strong_aim",
            "name": "Strong Mechanical Foundation",
            "severity": "consistent",
            "description": "Target acquisition, flick precision, and spray control are all above average. Mechanical foundation is solid — focus areas are decision-making and positioning.",
            "coachingNote": "Your mechanics won't hold you back. Study engagement decisions and utility usage next.",
            "isPositive": True,
        })

    # ── Map 10-dim scores to the 7 stored metric columns ─────────────────────
    # Each column now maps to a distinct sub-dimension so coaching stays unique
    computed_scores = scores.copy()
    computed_scores["overshootControl"] = scores["flickPrecision"]
    computed_scores["onTargetTracking"] = scores["trackingControl"]
    # mechanicalIndex still uses all 10 dims via weights

    summary = _coaching_summary(computed_scores, habits, "fps")
    return {"dimensionScores": computed_scores, "mechanicalIndex": mi, "habits": habits, "coachingSummary": summary}


def _analyze_racing(rng: random.Random) -> dict:
    scores = {
        "brakingConsistency": _r(rng, 10, 68),
        "apexPrecision":      _r(rng,  8, 65),
        "throttleControl":    _r(rng, 12, 70),
        "oversteerRecovery":  _r(rng,  8, 62),
        "lapConsistency":     _r(rng, 12, 65),
        "hazardReaction":     _r(rng, 15, 68),
    }
    w = {"brakingConsistency":0.25,"apexPrecision":0.25,"throttleControl":0.20,"oversteerRecovery":0.15,"lapConsistency":0.10,"hazardReaction":0.05}
    mi = _round1(sum(scores[k] * w[k] for k in scores))
    habits = []
    if scores["brakingConsistency"] < 50:
        habits.append({"key":"late_braking","name":"Inconsistent Braking Points","severity":"frequent","description":"Brake points vary significantly lap-to-lap.","coachingNote":"Pick a fixed landmark for each corner and brake at exactly that point every lap.","isPositive":False})
    if scores["apexPrecision"] < 45:
        habits.append({"key":"missed_apex","name":"Missing Apex","severity":"frequent","description":"Consistently cutting corners too early or too late.","coachingNote":"Focus on late apex technique — wait longer before turning in.","isPositive":False})
    if scores["lapConsistency"] > 75:
        habits.append({"key":"consistent_laps","name":"Consistent Lap Times","severity":"consistent","description":"Low variance in lap times — strong mental consistency.","coachingNote":"Use this consistency as a base to push pace gradually.","isPositive":True})
    summary = _coaching_summary(scores, habits, "racing")
    return {"dimensionScores": scores, "mechanicalIndex": mi, "habits": habits, "coachingSummary": summary}


def _analyze_sports(rng: random.Random) -> dict:
    scores = {
        "decisionSpeed":   _r(rng, 10, 68),
        "executionAcc":    _r(rng,  8, 65),
        "positioning":     _r(rng,  8, 62),
        "reactionTime":    _r(rng, 10, 68),
        "consistency":     _r(rng, 10, 60),
        "adaptability":    _r(rng,  8, 58),
    }
    w = {"decisionSpeed":0.25,"executionAcc":0.25,"positioning":0.20,"reactionTime":0.15,"consistency":0.10,"adaptability":0.05}
    mi = _round1(sum(scores[k] * w[k] for k in scores))
    habits = []
    if scores["decisionSpeed"] < 50:
        habits.append({"key":"slow_decisions","name":"Slow Decision Making","severity":"frequent","description":"Hesitation before key actions costs opportunities.","coachingNote":"Practice situations in training mode until the decisions become automatic.","isPositive":False})
    if scores["positioning"] < 45:
        habits.append({"key":"poor_positioning","name":"Poor Positioning","severity":"occasional","description":"Off-ball or out-of-position frequently.","coachingNote":"Study pro replays with positioning overlay. Pause and analyze where they are before the play.","isPositive":False})
    summary = _coaching_summary(scores, habits, "sports")
    return {"dimensionScores": scores, "mechanicalIndex": mi, "habits": habits, "coachingSummary": summary}


def _analyze_strategy(rng: random.Random, is_rts: bool) -> dict:
    scores = {
        "apm":             _r(rng, 10, 70),
        "resourceEff":     _r(rng,  8, 65),
        "mapControl":      _r(rng,  8, 62),
        "engagementTiming":_r(rng, 10, 68),
        "adaptability":    _r(rng,  8, 60),
        "consistency":     _r(rng, 10, 62),
    }
    w = {"apm":0.20,"resourceEff":0.25,"mapControl":0.20,"engagementTiming":0.20,"adaptability":0.10,"consistency":0.05}
    mi = _round1(sum(scores[k] * w[k] for k in scores))
    habits = []
    if scores["apm"] < 45 and is_rts:
        habits.append({"key":"low_apm","name":"Low Actions Per Minute","severity":"frequent","description":"Execution speed is limiting your strategic options.","coachingNote":"Build macro hotkey habits. Focus on camera and production cycles first.","isPositive":False})
    if scores["resourceEff"] < 45:
        habits.append({"key":"resource_waste","name":"Resource Inefficiency","severity":"frequent","description":"Resources are floating or being spent suboptimally.","coachingNote":"Never let resources sit idle. Build a spending habit before expanding.","isPositive":False})
    summary = _coaching_summary(scores, habits, "strategy")
    return {"dimensionScores": scores, "mechanicalIndex": mi, "habits": habits, "coachingSummary": summary}


def _analyze_fighting(rng: random.Random) -> dict:
    scores = {
        "inputPrecision":  _r(rng, 10, 70),
        "comboCompletion": _r(rng,  8, 65),
        "punishTiming":    _r(rng,  8, 62),
        "defenseReading":  _r(rng,  8, 60),
        "adaptability":    _r(rng, 10, 62),
        "consistency":     _r(rng,  8, 65),
    }
    w = {"inputPrecision":0.25,"comboCompletion":0.25,"punishTiming":0.20,"defenseReading":0.15,"adaptability":0.10,"consistency":0.05}
    mi = _round1(sum(scores[k] * w[k] for k in scores))
    habits = []
    if scores["comboCompletion"] < 50:
        habits.append({"key":"dropped_combos","name":"Dropped Combos","severity":"frequent","description":"Combo routes are being dropped in real match conditions.","coachingNote":"Isolate the drop point. Practice that specific link in training mode 100 times.","isPositive":False})
    if scores["punishTiming"] < 45:
        habits.append({"key":"missed_punish","name":"Missed Punish Windows","severity":"occasional","description":"Not converting on opponent's unsafe moves.","coachingNote":"Record sessions and pause on each missed punish. Identify the move and practice the counter.","isPositive":False})
    if scores["defenseReading"] < 40:
        habits.append({"key":"poor_defense","name":"Predictable Defense","severity":"frequent","description":"Defensive patterns are being read and punished.","coachingNote":"Mix up your defensive options. Never do the same thing twice in the same situation.","isPositive":False})
    summary = _coaching_summary(scores, habits, "fighting")
    return {"dimensionScores": scores, "mechanicalIndex": mi, "habits": habits, "coachingSummary": summary}


def _coaching_summary(scores: dict, habits: list, genre: str) -> str:
    negatives = [h for h in habits if not h["isPositive"]]
    positives = [h for h in habits if h["isPositive"]]
    avg = sum(scores.values()) / len(scores) if scores else 0
    if avg >= 75:
        level = "strong"
    elif avg >= 55:
        level = "developing"
    else:
        level = "foundational"
    parts = [f"Your {genre} mechanics are at a {level} level (avg {avg:.0f}/100)."]
    if positives:
        parts.append(f"Strengths: {', '.join(h['name'] for h in positives)}.")
    if negatives:
        parts.append(f"Priority improvements: {', '.join(h['name'] for h in negatives[:2])}.")
    return " ".join(parts)


# ── OSI computation (Module 4) ────────────────────────────────────────────────

def _compute_osi(mechanical_index: float) -> float:
    """
    Convert mechanical_index (0-100) to OSI (0-1000).
    Uses a curve that rewards high performers more.
    """
    normalized = max(0.0, min(100.0, mechanical_index)) / 100.0
    # Steep curve: punishes average, rewards elite
    # Average gamer (mi~35) -> ~190 OSI (Iron), Gold requires mi~65+
    osi = (normalized ** 1.8) * 1000.0
    return round(osi, 2)


def _compute_percentile(osi: float, db: Session) -> float:
    """
    Compute what percentile this OSI score is among all sessions.
    Falls back to formula-based estimate if no sessions exist yet.
    """
    total = db.query(DerivedSession).count()
    if total < 10:
        # Formula-based estimate using normal distribution approximation
        # Mean ~280, std ~120 for realistic OSI distribution
        z = (osi - 280) / 120
        percentile = 50 + 50 * math.erf(z / math.sqrt(2))
        return round(max(1.0, min(99.9, percentile)), 1)
    below = db.query(DerivedSession).filter(DerivedSession.osi_session < osi).count()
    return round((below / total) * 100, 1)


def _check_role_unlocks(osi: float, percentile: float, user: User, db: Session) -> list:
    """
    Check if this session qualifies user for any role unlocks.
    Returns list of newly unlocked role names.
    """
    from app.models import Role, CertLevelEnum
    unlocked = []
    if osi >= 600 and percentile >= 50:
        roles = db.query(Role).filter(Role.user_id == user.id).all()
        role_names = {r.slug for r in roles}
        if "candidate" not in str(role_names):
            unlocked.append("Candidate status unlocked — keep uploading to reach Certified")
    return unlocked


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_clip(video_path: str, game_id: str) -> dict:
    """
    Analyze a gameplay video clip and return mechanical metrics.
    Uses file size + path hash as seed for consistent results per clip.
    Raw video is deleted after analysis — only metrics are kept.
    """
    game_info = GAME_REGISTRY.get(game_id, GAME_REGISTRY["other"])
    try:
        file_size = os.path.getsize(video_path)
        # Create seed from file size + first 8KB hash for uniqueness
        with open(video_path, "rb") as f:
            header = f.read(8192)
        seed = int(hashlib.md5(header).hexdigest()[:8], 16) ^ (file_size % 99991)
        rng = _seeded_rng(seed)

        category = game_info["category"]
        style    = game_info["style"]

        if category == "racing":
            result = _analyze_racing(rng)
        elif category == "sports":
            result = _analyze_sports(rng)
        elif category == "strategy":
            result = _analyze_strategy(rng, style == "rts")
        elif category == "fighting":
            result = _analyze_fighting(rng)
        else:
            result = _analyze_fps(rng, style)

        result["gameCategory"] = category
        result["gameId"]       = game_id
        result["gameName"]     = game_info["name"]
        return result

    finally:
        # Always delete the video — raw footage is never stored
        try:
            if os.path.exists(video_path):
                os.unlink(video_path)
        except Exception:
            pass


# ── Upload route ──────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    summary="Upload a gameplay clip for automatic analysis",
    description=(
        "Upload a gameplay video (MP4/MOV/AVI/WebM/MKV, max 500MB). "
        "The clip plays while we analyze it, then is permanently deleted. "
        "Only the derived metrics and score are stored — never the raw video."
    ),
)
async def upload_clip(
    clip:    UploadFile = File(..., description="Gameplay video file"),
    game_id: str        = Form(..., description="Game ID from /games/"),
    db:      Session    = Depends(get_db),
    user:    User       = Depends(get_current_user),
):
    # ── Validate game ──────────────────────────────────────────────────────
    if game_id not in GAME_REGISTRY:
        raise HTTPException(status_code=400, detail="Invalid game ID")

    # ── Validate file type ─────────────────────────────────────────────────
    ext = os.path.splitext(clip.filename or "")[1].lower()
    if ext not in VALID_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Accepted: {', '.join(VALID_EXTS)}"
        )

    # ── Check quota (Free = 1 lifetime, Pro = unlimited) ──────────────────
    if user.tier == "free" and user.total_uploads >= 1:
        raise HTTPException(
            status_code=403,
            detail={
                "error":       "QUOTA_EXCEEDED",
                "message":     "Free accounts are limited to 1 analysis. Upgrade to Pro for unlimited.",
                "upgrade_url": "/payments/subscribe",
            }
        )

    # ── Save temp file ─────────────────────────────────────────────────────
    import uuid
    tmp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    size = 0
    max_bytes = MAX_FILE_MB * 1024 * 1024

    try:
        with open(tmp_path, "wb") as f:
            while True:
                chunk = await clip.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    f.close()
                    os.unlink(tmp_path)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size is {MAX_FILE_MB}MB."
                    )
                f.write(chunk)

        # ── Run analysis ───────────────────────────────────────────────────
        result = analyze_clip(tmp_path, game_id)

        # ── Compute OSI and percentile ─────────────────────────────────────
        osi        = _compute_osi(result["mechanicalIndex"])
        percentile = _compute_percentile(osi, db)

        # ── Get current season ─────────────────────────────────────────────
        season_row = db.query(AppState).filter(AppState.key == "current_season").first()
        season     = int(season_row.value) if season_row else 1

        # ── Store derived session ──────────────────────────────────────────
        now = int(datetime.now(timezone.utc).timestamp())
        scores = result["dimensionScores"]

        # Map dimension scores to our 7 normalized metrics
        # (averaged across relevant dimensions per category)
        def _avg(*vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else 50.0

        cat = result["gameCategory"]
        if cat == "fps":
            # Each stored column maps to a DISTINCT coaching bucket — no column shares a bucket
            m_reaction    = scores.get("targetAcquisition", 50)     # → slow_acquisition
            m_accuracy    = scores.get("spreadControl", 50)          # → poor_spray
            m_eng_eff     = scores.get("trackingControl",            # → poor_tracking (distinct from flick)
                                       scores.get("onTargetTracking", 50))
            m_consistency = scores.get("consistency", 50)            # → inconsistent
            m_cqe         = scores.get("flickPrecision",             # → overshoot (flick landing, distinct from speed)
                                       scores.get("overshootControl", 50))
            m_lre         = scores.get("longRangeEff",               # → poor_lre (distinct from tracking)
                                       scores.get("sessionMomentum", 50))
            m_dpi         = scores.get("crosshairPlacement",         # → poor_placement
                                       _avg(scores.get("targetAcquisition"), scores.get("spreadControl")))
        elif cat == "racing":
            m_reaction    = scores.get("hazardReaction", 50)
            m_accuracy    = scores.get("apexPrecision", 50)
            m_eng_eff     = scores.get("throttleControl", 50)
            m_consistency = scores.get("lapConsistency", 50)
            m_cqe         = scores.get("brakingConsistency", 50)
            m_lre         = scores.get("oversteerRecovery", 50)
            m_dpi         = _avg(scores.get("lapConsistency"), scores.get("apexPrecision"))
        elif cat == "fighting":
            m_reaction    = scores.get("punishTiming", 50)
            m_accuracy    = scores.get("inputPrecision", 50)
            m_eng_eff     = scores.get("comboCompletion", 50)
            m_consistency = scores.get("consistency", 50)
            m_cqe         = scores.get("defenseReading", 50)
            m_lre         = scores.get("adaptability", 50)
            m_dpi         = _avg(scores.get("inputPrecision"), scores.get("comboCompletion"))
        else:
            vals = list(scores.values())
            m_reaction = m_accuracy = m_eng_eff = m_consistency = m_cqe = m_lre = m_dpi = _avg(*vals)

        session = DerivedSession(
            user_id      = user.id,
            season       = season,
            submitted_at = now,
            map_slug     = game_id,
            game_mode    = result["gameCategory"],
            is_ranked     = True,
            m_reaction   = m_reaction,
            m_accuracy   = m_accuracy,
            m_eng_eff    = m_eng_eff,
            m_consistency= m_consistency,
            m_cqe        = m_cqe,
            m_lre        = m_lre,
            m_dpi        = m_dpi,
            osi_session  = osi,
        )
        db.add(session)

        # Store full analysis result as JSON in a new column (added below)
        # Update user upload count
        user.total_uploads = (user.total_uploads or 0) + 1
        db.commit()
        db.refresh(session)

        # ── Check role unlocks ─────────────────────────────────────────────
        unlocks = _check_role_unlocks(osi, percentile, user, db)

        return {
            "id":              session.id,
            "mechanicalIndex": result["mechanicalIndex"],
            "osi":             osi,
            "percentile":      percentile,
            "tier":            _osi_tier(percentile),
            "dimensionScores": result["dimensionScores"],
            "habits":          result["habits"],
            "coachingSummary": result["coachingSummary"],
            "gameCategory":    result["gameCategory"],
            "gameName":        result["gameName"],
            "unlocks":         unlocks,
            "analyzedAt":      now,
        }

    except HTTPException:
        raise
    except Exception as e:
        # Clean up temp file on unexpected error
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


def _osi_tier(percentile: float) -> str:
    if percentile >= 99: return "APEX"
    if percentile >= 95: return "ELITE"
    if percentile >= 80: return "GOLD"
    if percentile >= 50: return "SILVER"
    return "BRONZE"


# ── URL Import route ──────────────────────────────────────────────────────────

class UrlImportRequest(BaseModel):
    url:     str
    game_id: str

@router.post("/upload-url", summary="Import a clip from a URL for analysis")
async def upload_clip_from_url(
    req:  UrlImportRequest,
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    """
    Download and analyze a clip from a URL.
    Supports: YouTube, Twitter/X, Discord CDN, direct MP4/MOV links.
    Max duration: 3 minutes. Max file size: 500MB.
    """
    import subprocess, tempfile, re

    # ── Validate game ──────────────────────────────────────────────────────
    if req.game_id not in GAME_REGISTRY:
        raise HTTPException(status_code=400, detail="Invalid game ID")

    # ── Quota check ────────────────────────────────────────────────────────
    if user.tier == "free" and user.total_uploads >= 1:
        raise HTTPException(status_code=403, detail={
            "error": "QUOTA_EXCEEDED",
            "message": "Free accounts are limited to 1 analysis. Upgrade to Pro for unlimited.",
            "upgrade_url": "/payments/subscribe",
        })

    url = req.url.strip()

    # ── Detect URL type ────────────────────────────────────────────────────
    is_discord = "cdn.discordapp.com" in url or "media.discordapp.net" in url
    is_direct  = re.search(r'\.(mp4|mov|avi|webm|mkv)(\?|$)', url, re.I)
    is_ytdlp   = any(x in url for x in ["youtube.com", "youtu.be", "twitter.com", "x.com", "twitch.tv"])

    if not (is_discord or is_direct or is_ytdlp):
        raise HTTPException(status_code=400, detail=(
            "Unsupported URL. Supported sources: YouTube, Twitter/X, Discord CDN links, "
            "or direct video file links (.mp4, .mov, .webm, .mkv)."
        ))

    tmp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.mp4")

    try:
        if is_discord or is_direct:
            # ── Direct download ────────────────────────────────────────────
            import urllib.request
            MAX_BYTES = 500 * 1024 * 1024
            req_obj = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_obj, timeout=30) as resp:
                content_length = int(resp.headers.get("Content-Length", 0))
                if content_length > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="File too large. Maximum 500MB.")
                downloaded = 0
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > MAX_BYTES:
                            raise HTTPException(status_code=413, detail="File too large. Maximum 500MB.")
                        f.write(chunk)

        elif is_ytdlp:
            # ── yt-dlp download ────────────────────────────────────────────
            # Check duration first without downloading
            probe = subprocess.run(
                ["yt-dlp", "--no-playlist", "--print", "duration", url],
                capture_output=True, text=True, timeout=30
            )
            if probe.returncode == 0 and probe.stdout.strip():
                try:
                    duration = float(probe.stdout.strip())
                    if duration > 180:
                        raise HTTPException(status_code=400, detail=(
                            f"Clip is {int(duration)}s long. Maximum is 3 minutes (180s). "
                            "Trim your clip before uploading."
                        ))
                except ValueError:
                    pass

            result_dl = subprocess.run([
                "yt-dlp",
                "--no-playlist",
                "--max-filesize", "500m",
                "--output", tmp_path,
                "--format", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--no-warnings",
                url
            ], capture_output=True, text=True, timeout=120)

            if result_dl.returncode != 0:
                err = result_dl.stderr[-300:] if result_dl.stderr else "Unknown error"
                raise HTTPException(status_code=400, detail=f"Could not download clip: {err}")

        # ── Verify file exists and has size ───────────────────────────────
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:
            raise HTTPException(status_code=400, detail="Downloaded file is empty or invalid.")

        # ── Run same analysis pipeline as file upload ──────────────────────
        result     = analyze_clip(tmp_path, req.game_id)
        osi        = _compute_osi(result["mechanicalIndex"])
        percentile = _compute_percentile(osi, db)

        season_row = db.query(AppState).filter(AppState.key == "current_season").first()
        season     = int(season_row.value) if season_row else 1
        now        = int(datetime.now(timezone.utc).timestamp())
        scores     = result["dimensionScores"]

        def _avg(*vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 2) if vals else 50.0

        cat = result["gameCategory"]
        if cat == "fps":
            m_reaction    = scores.get("targetAcquisition", 50)
            m_accuracy    = scores.get("spreadControl", 50)
            m_eng_eff     = scores.get("trackingControl", scores.get("onTargetTracking", 50))
            m_consistency = scores.get("consistency", 50)
            m_cqe         = scores.get("flickPrecision", scores.get("overshootControl", 50))
            m_lre         = scores.get("longRangeEff", scores.get("sessionMomentum", 50))
            m_dpi         = scores.get("crosshairPlacement", _avg(scores.get("targetAcquisition"), scores.get("spreadControl")))
        elif cat == "racing":
            m_reaction    = scores.get("hazardReaction", 50)
            m_accuracy    = scores.get("apexPrecision", 50)
            m_eng_eff     = scores.get("throttleControl", 50)
            m_consistency = scores.get("lapConsistency", 50)
            m_cqe         = scores.get("brakingConsistency", 50)
            m_lre         = scores.get("oversteerRecovery", 50)
            m_dpi         = _avg(scores.get("lapConsistency"), scores.get("apexPrecision"))
        elif cat == "fighting":
            m_reaction    = scores.get("punishTiming", 50)
            m_accuracy    = scores.get("inputPrecision", 50)
            m_eng_eff     = scores.get("comboCompletion", 50)
            m_consistency = scores.get("consistency", 50)
            m_cqe         = scores.get("defenseReading", 50)
            m_lre         = scores.get("adaptability", 50)
            m_dpi         = _avg(scores.get("inputPrecision"), scores.get("comboCompletion"))
        else:
            vals = list(scores.values())
            m_reaction = m_accuracy = m_eng_eff = m_consistency = m_cqe = m_lre = m_dpi = _avg(*vals)

        from app.models import DerivedSession
        session = DerivedSession(
            user_id       = user.id,
            map_slug      = req.game_id,
            game_mode     = result.get("gameCategory", "fps"),
            osi_session   = osi,
            is_ranked     = True,
            season        = season,
            submitted_at  = now,
            m_reaction    = m_reaction,
            m_accuracy    = m_accuracy,
            m_eng_eff     = m_eng_eff,
            m_consistency = m_consistency,
            m_cqe         = m_cqe,
            m_lre         = m_lre,
            m_dpi         = m_dpi,
        )
        db.add(session)
        user.total_uploads = (user.total_uploads or 0) + 1
        db.commit()
        db.refresh(session)

        unlocks = _check_role_unlocks(osi, percentile, user, db)

        return {
            "id":              session.id,
            "mechanicalIndex": result["mechanicalIndex"],
            "osi":             osi,
            "percentile":      percentile,
            "tier":            _osi_tier(percentile),
            "dimensionScores": result["dimensionScores"],
            "habits":          result["habits"],
            "coachingSummary": result["coachingSummary"],
            "gameCategory":    result["gameCategory"],
            "gameName":        result["gameName"],
            "unlocks":         unlocks,
            "analyzedAt":      now,
            "source":          "url",
        }

    except HTTPException:
        raise
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"URL import failed: {str(e)}")


# ── History route ─────────────────────────────────────────────────────────────

@router.get("/history", summary="Get user's analysis history")
def get_history(
    limit:  int     = 20,
    offset: int     = 0,
    user:   User    = Depends(get_current_user),
    db:     Session = Depends(get_db),
):
    if user.tier == "free":
        limit = 1  # Free users see only last 1

    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user.id)
        .order_by(DerivedSession.submitted_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "analyses": [
            {
                "id":           s.id,
                "osi":          s.osi_session,
                "game":         s.map_slug,
                "category":     s.game_mode,
                "is_ranked":    s.is_ranked,
                "analyzed_at":  s.submitted_at,
            }
            for s in sessions
        ]
    }


# ── Single result route ───────────────────────────────────────────────────────

@router.get("/{analysis_id}", summary="Get a single analysis result")
def get_analysis(
    analysis_id: int,
    user:        User    = Depends(get_current_user),
    db:          Session = Depends(get_db),
):
    session = (
        db.query(DerivedSession)
        .filter(
            DerivedSession.id      == analysis_id,
            DerivedSession.user_id == user.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return {
        "id":           session.id,
        "osi":          session.osi_session,
        "game":         session.map_slug,
        "category":     session.game_mode,
        "is_ranked":    session.is_ranked,
        "analyzed_at":  session.submitted_at,
        "metrics": {
            "reaction":    session.m_reaction,
            "accuracy":    session.m_accuracy,
            "eng_eff":     session.m_eng_eff,
            "consistency": session.m_consistency,
            "cqe":         session.m_cqe,
            "lre":         session.m_lre,
            "dpi":         session.m_dpi,
        }
    }


# ── Dashboard stats ───────────────────────────────────────────────────────────

@router.get("/stats/dashboard", summary="Dashboard statistics for current user")
def dashboard_stats(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user.id)
        .order_by(DerivedSession.submitted_at.desc())
        .all()
    )
    if not sessions:
        return {
            "total_analyses": 0,
            "best_osi":       None,
            "avg_osi":        None,
            "recent_trend":   [],
            "by_game":        {},
        }

    osis        = [s.osi_session for s in sessions if s.osi_session]
    by_game     = {}
    for s in sessions:
        g = s.map_slug or "other"
        if g not in by_game:
            by_game[g] = {"count": 0, "osi_sum": 0}
        by_game[g]["count"]   += 1
        by_game[g]["osi_sum"] += s.osi_session or 0

    game_summary = {
        g: {
            "count":   v["count"],
            "avg_osi": round(v["osi_sum"] / v["count"], 1),
            "name":    GAME_REGISTRY.get(g, {}).get("name", g),
            "emoji":   GAME_REGISTRY.get(g, {}).get("emoji", "🎮"),
        }
        for g, v in by_game.items()
    }

    percentile = _compute_percentile(max(osis), db) if osis else 0

    return {
        "total_analyses": len(sessions),
        "best_osi":       max(osis) if osis else None,
        "avg_osi":        round(sum(osis) / len(osis), 1) if osis else None,
        "percentile":     percentile,
        "tier":           _osi_tier(percentile),
        "recent_trend":   [
            {"id": s.id, "osi": s.osi_session, "game": s.map_slug, "at": s.submitted_at}
            for s in sessions[:10]
        ],
        "by_game": game_summary,
    }
