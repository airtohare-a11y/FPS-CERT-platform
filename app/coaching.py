# =============================================================================
# app/coaching.py
# Module 5 — Ranking, Role Suggestion, Coaching, Coach Profiles,
#             Booking System, Messaging
#
# Rank system:
#   Requires 5 analyses. Rank = f(avg OSI, consistency across sessions).
#   Recruit → Iron → Bronze → Silver → Gold → Platinum → Diamond → Elite → Apex
#
# Role suggestion (FPS only, deep):
#   Entry Fragger, Support/Anchor, AWPer/Sniper, IGL, Lurker, Rifler, Flex
#   Assigned after 3+ sessions based on metric patterns.
#
# Coach system:
#   Must be Diamond+ rank to apply as coach.
#   Players browse coaches by role/rank, book sessions, message coaches.
# =============================================================================

import math
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text
from sqlalchemy.orm import Session, relationship

from app.auth import get_current_user
from app.database import get_db, Base
from app.models import User, DerivedSession

router = APIRouter()

_now = lambda: int(datetime.now(timezone.utc).timestamp())


# =============================================================================
# Database Models
# =============================================================================

class PlayerRank(Base):
    """
    Official rank assigned after 5+ analyses.
    Recomputed each time a new analysis is submitted.
    """
    __tablename__ = "player_ranks"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, unique=True, index=True)
    rank_label    = Column(String(20), nullable=False, default="Unranked")
    rank_tier     = Column(Integer,  nullable=False, default=0)  # 0=Unranked, 1=Recruit...9=Apex
    avg_osi       = Column(Float,    nullable=True)
    consistency   = Column(Float,    nullable=True)   # 0-100, higher = more consistent
    total_sessions= Column(Integer,  nullable=False, default=0)
    suggested_role= Column(String(40), nullable=True)
    role_reason   = Column(Text,     nullable=True)
    last_updated  = Column(Integer,  nullable=False, default=_now)

    user = relationship("User", foreign_keys=[user_id])


class CoachProfile(Base):
    """
    Coach listing. Only Diamond+ ranked players can apply.
    Admin must approve before profile goes live.
    """
    __tablename__ = "coach_profiles"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, unique=True, index=True)
    display_name  = Column(String(60),  nullable=False)
    bio           = Column(Text,        nullable=True)
    specialty_role= Column(String(40),  nullable=True)   # Entry, IGL, AWP, etc.
    rank_label    = Column(String(20),  nullable=False)
    rate_per_hour = Column(Float,       nullable=True)   # USD
    is_active     = Column(Boolean,     nullable=False, default=False)  # admin approval
    is_available  = Column(Boolean,     nullable=False, default=True)
    total_sessions= Column(Integer,     nullable=False, default=0)
    avg_rating    = Column(Float,       nullable=True)
    created_at    = Column(Integer,     nullable=False, default=_now)

    user     = relationship("User", foreign_keys=[user_id])
    bookings = relationship("CoachBooking", back_populates="coach", foreign_keys="CoachBooking.coach_id")


class CoachBooking(Base):
    """
    Booking request from player to coach.
    States: pending → accepted / declined → completed / cancelled
    """
    __tablename__ = "coach_bookings"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    player_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    coach_id     = Column(Integer, ForeignKey("coach_profiles.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    status       = Column(String(20), nullable=False, default="pending")
    message      = Column(Text,       nullable=True)   # player's intro message
    scheduled_at = Column(Integer,    nullable=True)   # unix timestamp
    rate_agreed  = Column(Float,      nullable=True)
    coach_notes  = Column(Text,       nullable=True)
    rating       = Column(Integer,    nullable=True)   # 1-5 stars after session
    review       = Column(Text,       nullable=True)
    created_at   = Column(Integer,    nullable=False, default=_now)
    updated_at   = Column(Integer,    nullable=False, default=_now)

    player = relationship("User",         foreign_keys=[player_id])
    coach  = relationship("CoachProfile", back_populates="bookings", foreign_keys=[coach_id])


class CoachMessage(Base):
    """
    Direct messages between player and coach within a booking context.
    """
    __tablename__ = "coach_messages"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    booking_id  = Column(Integer, ForeignKey("coach_bookings.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    sender_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                         nullable=False)
    content     = Column(Text,    nullable=False)
    is_read     = Column(Boolean, nullable=False, default=False)
    sent_at     = Column(Integer, nullable=False, default=_now)

    sender  = relationship("User",         foreign_keys=[sender_id])
    booking = relationship("CoachBooking", foreign_keys=[booking_id])


# =============================================================================
# Rank Engine
# =============================================================================

RANKS = [
    {"tier": 0, "label": "Unranked",  "min_osi": 0,    "color": "#6b7280"},
    {"tier": 1, "label": "Recruit",   "min_osi": 0,    "color": "#9ca3af"},
    {"tier": 2, "label": "Iron",      "min_osi": 200,  "color": "#78716c"},
    {"tier": 3, "label": "Bronze",    "min_osi": 350,  "color": "#b45309"},
    {"tier": 4, "label": "Silver",    "min_osi": 450,  "color": "#6b7280"},
    {"tier": 5, "label": "Gold",      "min_osi": 550,  "color": "#d97706"},
    {"tier": 6, "label": "Platinum",  "min_osi": 650,  "color": "#0ea5e9"},
    {"tier": 7, "label": "Diamond",   "min_osi": 730,  "color": "#6366f1"},
    {"tier": 8, "label": "Elite",     "min_osi": 820,  "color": "#8b5cf6"},
    {"tier": 9, "label": "Apex",      "min_osi": 920,  "color": "#f0a500"},
]

MIN_SESSIONS_FOR_RANK = 5
MIN_RANK_FOR_COACH    = 7  # Diamond tier


def _compute_consistency(osis: list) -> float:
    """
    Consistency = 100 - coefficient_of_variation * 100
    High consistency = low variance relative to mean.
    Returns 0-100.
    """
    if len(osis) < 2:
        return 50.0
    mean = sum(osis) / len(osis)
    if mean == 0:
        return 50.0
    std = math.sqrt(sum((x - mean) ** 2 for x in osis) / len(osis))
    cv  = std / mean
    # cv=0 → 100, cv=0.5 → 50, cv=1.0 → 0
    consistency = max(0.0, min(100.0, (1.0 - cv) * 100))
    return round(consistency, 1)


def _assign_rank(avg_osi: float, consistency: float, total_sessions: int) -> dict:
    """
    Assign rank based on avg OSI + consistency bonus/penalty.
    Consistency above 70 gives up to +30 OSI equivalent.
    Consistency below 40 gives up to -30 OSI equivalent.
    """
    if total_sessions < MIN_SESSIONS_FOR_RANK:
        return RANKS[0]  # Unranked

    # Apply consistency modifier
    consistency_modifier = (consistency - 55) * 0.6  # ±30 at extremes
    effective_osi = avg_osi + consistency_modifier

    # Find highest rank the player qualifies for
    assigned = RANKS[1]  # Start at Recruit
    for rank in RANKS[1:]:
        if effective_osi >= rank["min_osi"]:
            assigned = rank
        else:
            break
    return assigned


# =============================================================================
# Role Suggestion Engine (FPS-specific, deep)
# =============================================================================

FPS_ROLES = {
    "entry_fragger": {
        "name":        "Entry Fragger",
        "description": "You lead pushes and create space for your team. Raw first-contact speed and spray control under pressure are your weapons.",
        "playstyle":   "Aggressive, first-contact, high-risk high-reward",
        "strengths":   ["Target Acquisition Speed", "Spray Control", "Close-Quarter Efficiency"],
        "weaknesses":  ["Consistency under pressure", "Over-peeking"],
        "drills":      ["Gridshot — Speed Priority", "Deathmatch First Contact", "Counter-Strafe + Spray"],
        "pro_example": "Top-ranked FPS fraggers",
        "key_metrics": {"targetAcquisition": 0.35, "spreadControl": 0.25, "trackingControl": 0.20, "crosshairPlacement": 0.20},
    },
    "support": {
        "name":        "Support / Anchor",
        "description": "You read the game, hold angles, and enable your team. Crosshair placement and consistency are your greatest assets.",
        "playstyle":   "Methodical, positional, team-first",
        "strengths":   ["Crosshair Placement", "Consistency", "Engagement Discipline"],
        "weaknesses":  ["Raw duel speed", "Solo plays"],
        "drills":      ["Head Level Lock Drill", "Fixed Warmup Sequence", "Pre-aim Corner Study"],
        "pro_example": "Elite competitive anchors",
        "key_metrics": {"consistency": 0.30, "crosshairPlacement": 0.30, "spreadControl": 0.25, "longRangeEff": 0.15},
    },
    "awper": {
        "name":        "AWPer / Sniper",
        "description": "You control sightlines and punish mistakes. Flick precision, crosshair placement, and long-range efficiency define you.",
        "playstyle":   "Patient, high-precision, long-angle control",
        "strengths":   ["Flick Precision", "Long-Range Efficiency", "Crosshair Placement"],
        "weaknesses":  ["Close-quarters duels", "Spray situations"],
        "drills":      ["Micro-Adjustment Isolation", "Long-Range Map Positions Study", "Pre-aim Corner Study"],
        "pro_example": "Elite AWPers and snipers",
        "key_metrics": {"flickPrecision": 0.35, "longRangeEff": 0.30, "crosshairPlacement": 0.25, "consistency": 0.10},
    },
    "igl": {
        "name":        "IGL (In-Game Leader)",
        "description": "You think two steps ahead. Consistency and engagement discipline offset mechanical ceiling through decision quality.",
        "playstyle":   "Strategic, communicative, adaptive",
        "strengths":   ["Consistency", "Engagement Decisions", "Crosshair Placement"],
        "weaknesses":  ["Individual mechanical ceiling"],
        "drills":      ["Fixed Warmup Sequence", "Trade Audit — Death Review", "Map-Specific Crosshair Checkpoints"],
        "pro_example": "Elite IGL competitors",
        "key_metrics": {"consistency": 0.40, "crosshairPlacement": 0.25, "flickPrecision": 0.20, "longRangeEff": 0.15},
    },
    "lurker": {
        "name":        "Lurker",
        "description": "You thrive in isolation, catching rotators off guard. First-shot precision and crosshair placement are your survival tools.",
        "playstyle":   "Patient, sneaky, high-impact timing",
        "strengths":   ["Flick Precision", "Crosshair Placement", "First-shot Accuracy"],
        "weaknesses":  ["Team coordination", "Late rotations"],
        "drills":      ["One-Tap Only Deathmatch", "Off-Angle Exposure Drill", "Audio Cue Training"],
        "pro_example": "Elite lurkers",
        "key_metrics": {"flickPrecision": 0.35, "targetAcquisition": 0.30, "crosshairPlacement": 0.25, "consistency": 0.10},
    },
    "rifler": {
        "name":        "Rifler",
        "description": "You are the backbone of the team. Spray control, tracking, and consistency across all ranges define the role.",
        "playstyle":   "Balanced, consistent, adaptable",
        "strengths":   ["Spray Control", "Tracking Control", "Consistency"],
        "weaknesses":  ["Specialisation ceiling"],
        "drills":      ["Spray Pattern Trace — Single Weapon", "Smoothbot or VoxTS Tracking", "Fixed Warmup Sequence"],
        "pro_example": "Elite riflers",
        "key_metrics": {"spreadControl": 0.30, "trackingControl": 0.28, "consistency": 0.27, "targetAcquisition": 0.15},
    },
    "flex": {
        "name":        "Flex / Hybrid",
        "description": "You adapt to what the team needs. Balanced metrics across all dimensions make you invaluable in any line-up.",
        "playstyle":   "Adaptive, team-aware, multi-role",
        "strengths":   ["Balance across all metrics", "Adaptability"],
        "weaknesses":  ["No dominant speciality yet — identify your ceiling"],
        "drills":      ["Fixed Warmup Sequence", "Deathmatch First Contact", "Trade Audit — Death Review"],
        "pro_example": "Top-ranked flex players",
        "key_metrics": {"consistency": 0.20, "spreadControl": 0.20, "targetAcquisition": 0.20, "trackingControl": 0.20, "crosshairPlacement": 0.20},
    },
}

# =============================================================================
# Role Registries — Racing, Sports, Strategy, Fighting
# =============================================================================

RACING_ROLES = {
    "sim_racer": {
        "name": "Sim Specialist",
        "description": "You thrive on precision and technical mastery. Lap consistency and braking accuracy define your style.",
        "playstyle": "Technical, precise, methodical",
        "strengths": ["Lap Consistency", "Braking Consistency", "Apex Precision"],
        "weaknesses": ["Hazard reaction under pressure"],
        "drills": ["Marker Braking", "One Corner Focus", "Trail Braking Practice"],
        "pro_example": "Elite sim racing specialists",
        "key_metrics": {"lapConsistency": 0.35, "brakingConsistency": 0.35, "apexPrecision": 0.30},
    },
    "aggressive_driver": {
        "name": "Aggressive Driver",
        "description": "You carry speed everywhere and recover from mistakes fast. Throttle control and oversteer recovery are your weapons.",
        "playstyle": "Aggressive, high-speed, risk-tolerant",
        "strengths": ["Throttle Control", "Oversteer Recovery", "Hazard Reaction"],
        "weaknesses": ["Consistency across long stints"],
        "drills": ["Slow Practice Mode", "Trail Braking Practice", "45-minute Break Rule"],
        "pro_example": "Elite aggressive drivers",
        "key_metrics": {"throttleControl": 0.35, "oversteerRecovery": 0.35, "hazardReaction": 0.30},
    },
    "corner_specialist": {
        "name": "Corner Specialist",
        "description": "You find time in the technical sections others lose. Apex precision and braking are elite.",
        "playstyle": "Patient, technical, corner-focused",
        "strengths": ["Apex Precision", "Braking Consistency", "Throttle Control"],
        "weaknesses": ["Straight-line aggression"],
        "drills": ["Late Apex Drill", "Marker Braking", "One Corner Focus"],
        "pro_example": "Elite corner specialists",
        "key_metrics": {"apexPrecision": 0.40, "brakingConsistency": 0.35, "throttleControl": 0.25},
    },
    "consistent_finisher": {
        "name": "Consistent Finisher",
        "description": "You rarely crash and always bring it home. Lap consistency and hazard avoidance are your greatest assets.",
        "playstyle": "Safe, consistent, long-race focused",
        "strengths": ["Lap Consistency", "Hazard Reaction", "Braking Consistency"],
        "weaknesses": ["Peak pace ceiling"],
        "drills": ["Pre-session Warmup Routine", "Session Length Cap", "Marker Braking"],
        "pro_example": "Elite consistent finishers",
        "key_metrics": {"lapConsistency": 0.40, "hazardReaction": 0.35, "brakingConsistency": 0.25},
    },
}

SPORTS_ROLES = {
    "playmaker": {
        "name": "Playmaker",
        "description": "You create opportunities for others. Decision speed and execution accuracy define your game.",
        "playstyle": "Creative, fast-thinking, team-first",
        "strengths": ["Decision Speed", "Execution Accuracy", "Adaptability"],
        "weaknesses": ["Defensive awareness"],
        "drills": ["Decision Speed Drills", "High-Stakes Practice Mode", "Input Window Analysis"],
        "pro_example": "Elite playmakers",
        "key_metrics": {"decisionSpeed": 0.40, "executionAcc": 0.35, "adaptability": 0.25},
    },
    "defensive_anchor": {
        "name": "Defensive Anchor",
        "description": "You read the opponent and shut down attacks. Positioning and reaction time are your foundation.",
        "playstyle": "Reactive, positional, disciplined",
        "strengths": ["Positioning", "Reaction Time", "Consistency"],
        "weaknesses": ["Offensive contribution"],
        "drills": ["Positioning Study", "Pre-performance Routine", "Session Length Cap"],
        "pro_example": "Elite defensive anchors",
        "key_metrics": {"positioning": 0.40, "reactionTime": 0.35, "consistency": 0.25},
    },
    "finisher": {
        "name": "Finisher / Scorer",
        "description": "You convert opportunities. Execution accuracy and reaction time when it matters most.",
        "playstyle": "Clinical, opportunistic, high-impact",
        "strengths": ["Execution Accuracy", "Reaction Time", "Decision Speed"],
        "weaknesses": ["Build-up play contribution"],
        "drills": ["Execution Accuracy Drills", "High-Stakes Practice Mode", "Slow Practice Mode"],
        "pro_example": "Elite finishers",
        "key_metrics": {"executionAcc": 0.40, "reactionTime": 0.35, "decisionSpeed": 0.25},
    },
    "all_rounder": {
        "name": "All-Rounder",
        "description": "You contribute everywhere on the pitch. Balanced metrics across all areas.",
        "playstyle": "Versatile, balanced, adaptable",
        "strengths": ["Consistency", "Adaptability", "Balanced metrics"],
        "weaknesses": ["Peak specialisation"],
        "drills": ["Pre-session Warmup Routine", "High-Stakes Practice Mode", "Positioning Study"],
        "pro_example": "Elite all-rounders",
        "key_metrics": {"consistency": 0.30, "adaptability": 0.25, "decisionSpeed": 0.25, "executionAcc": 0.20},
    },
}

STRATEGY_ROLES = {
    "macro_player": {
        "name": "Macro Player",
        "description": "You win through superior resource management and map control. APM is secondary to decision quality.",
        "playstyle": "Strategic, patient, economy-focused",
        "strengths": ["Resource Efficiency", "Map Control", "Engagement Timing"],
        "weaknesses": ["Mechanical execution speed"],
        "drills": ["Zero Float Challenge", "10-second Camera Sweep", "Minimap-first Rule"],
        "pro_example": "Elite macro strategists",
        "key_metrics": {"resourceEff": 0.35, "mapControl": 0.35, "engagementTiming": 0.30},
    },
    "micro_specialist": {
        "name": "Micro Specialist",
        "description": "Your APM and execution are elite. You win individual fights and out-mechanically overwhelm opponents.",
        "playstyle": "Mechanical, aggressive, high-APM",
        "strengths": ["APM", "Engagement Timing", "Adaptability"],
        "weaknesses": ["Long-game macro strategy"],
        "drills": ["Input Window Analysis", "Slow Practice Mode", "Zero Float Challenge"],
        "pro_example": "Elite micro specialists",
        "key_metrics": {"apm": 0.40, "engagementTiming": 0.35, "adaptability": 0.25},
    },
    "support_utility": {
        "name": "Support / Utility",
        "description": "You enable your team through vision, setup, and sacrifice. Map control and consistency define you.",
        "playstyle": "Team-first, visionary, consistent",
        "strengths": ["Map Control", "Consistency", "Resource Efficiency"],
        "weaknesses": ["Individual carry potential"],
        "drills": ["Minimap-first Rule", "Pre-session Warmup Routine", "10-second Camera Sweep"],
        "pro_example": "Elite support players",
        "key_metrics": {"mapControl": 0.40, "consistency": 0.35, "resourceEff": 0.25},
    },
    "aggressive_rusher": {
        "name": "Aggressive Rusher",
        "description": "You apply constant pressure and deny your opponent time to breathe. Early aggression is your identity.",
        "playstyle": "Aggressive, early-game, pressure-based",
        "strengths": ["Engagement Timing", "APM", "Adaptability"],
        "weaknesses": ["Late game macro consistency"],
        "drills": ["Zero Float Challenge", "High-Stakes Practice Mode", "Input Window Analysis"],
        "pro_example": "Elite aggressive rushers",
        "key_metrics": {"engagementTiming": 0.40, "apm": 0.35, "adaptability": 0.25},
    },
}

FIGHTING_ROLES = {
    "rushdown": {
        "name": "Rushdown",
        "description": "You apply relentless pressure and never let the opponent breathe. Input precision and combo completion are your weapons.",
        "playstyle": "Aggressive, combo-heavy, relentless",
        "strengths": ["Input Precision", "Combo Completion", "Adaptability"],
        "weaknesses": ["Defence when pressured yourself"],
        "drills": ["Slow Practice Mode", "Input Window Analysis", "High-Stakes Practice Mode"],
        "pro_example": "Elite rushdown players",
        "key_metrics": {"inputPrecision": 0.35, "comboCompletion": 0.35, "adaptability": 0.30},
    },
    "footsies_player": {
        "name": "Footsies / Neutral Player",
        "description": "You control space and punish every mistake. Punish timing and defense reading are elite.",
        "playstyle": "Patient, space-controlling, punish-focused",
        "strengths": ["Punish Timing", "Defense Reading", "Consistency"],
        "weaknesses": ["Offensive combo routes"],
        "drills": ["Slow Flick Training", "Micro-adjustment Drill", "Input Window Analysis"],
        "pro_example": "Elite footsies players",
        "key_metrics": {"punishTiming": 0.40, "defenseReading": 0.35, "consistency": 0.25},
    },
    "grappler": {
        "name": "Grappler",
        "description": "You win through reads, conditioning, and high-damage confirms. Defense reading and input precision are critical.",
        "playstyle": "Read-heavy, high-damage, conditioning",
        "strengths": ["Defense Reading", "Input Precision", "Punish Timing"],
        "weaknesses": ["Neutral footsies"],
        "drills": ["Input Window Analysis", "High-Stakes Practice Mode", "Slow Practice Mode"],
        "pro_example": "Elite grappler players",
        "key_metrics": {"defenseReading": 0.40, "inputPrecision": 0.35, "punishTiming": 0.25},
    },
    "defensive_turtle": {
        "name": "Defensive / Turtle",
        "description": "You outlast opponents through superior defense and consistent punishes. Low risk, high reward.",
        "playstyle": "Defensive, patient, counter-offensive",
        "strengths": ["Defense Reading", "Consistency", "Punish Timing"],
        "weaknesses": ["Offensive mix-up generation"],
        "drills": ["Pre-session Warmup Routine", "Session Length Cap", "Input Window Analysis"],
        "pro_example": "Elite defensive players",
        "key_metrics": {"defenseReading": 0.35, "consistency": 0.35, "punishTiming": 0.30},
    },
}

GENRE_ROLE_MAP = {
    "fps":      FPS_ROLES,
    "racing":   RACING_ROLES,
    "sports":   SPORTS_ROLES,
    "strategy": STRATEGY_ROLES,
    "fighting": FIGHTING_ROLES,
}

GENRE_METRIC_MAP = {
    "fps": {
        "targetAcquisition":  "m_reaction",      # flick speed
        "spreadControl":      "m_accuracy",       # spray/recoil
        "trackingControl":    "m_eng_eff",        # sustained tracking (distinct from flick)
        "consistency":        "m_consistency",
        "flickPrecision":     "m_cqe",            # flick landing accuracy
        "longRangeEff":       "m_lre",            # 40m+ sightline performance
        "crosshairPlacement": "m_dpi",            # pre-aim angle/height discipline
    },
    "racing": {
        "brakingConsistency": "m_reaction",
        "apexPrecision":      "m_accuracy",
        "throttleControl":    "m_eng_eff",
        "lapConsistency":     "m_consistency",
        "oversteerRecovery":  "m_cqe",
        "hazardReaction":     "m_lre",
    },
    "sports": {
        "decisionSpeed":  "m_reaction",
        "executionAcc":   "m_accuracy",
        "positioning":    "m_eng_eff",
        "reactionTime":   "m_consistency",
        "consistency":    "m_cqe",
        "adaptability":   "m_lre",
    },
    "strategy": {
        "apm":              "m_reaction",
        "resourceEff":      "m_accuracy",
        "mapControl":       "m_eng_eff",
        "engagementTiming": "m_consistency",
        "adaptability":     "m_cqe",
        "consistency":      "m_lre",
    },
    "fighting": {
        "inputPrecision":  "m_reaction",
        "comboCompletion": "m_accuracy",
        "punishTiming":    "m_eng_eff",
        "defenseReading":  "m_consistency",
        "adaptability":    "m_cqe",
        "consistency":     "m_lre",
    },
}


def _suggest_role(sessions: list) -> dict:
    """
    Analyse metric patterns across sessions and suggest the best-fit role.
    Genre-aware: uses the most common game_mode across sessions.
    Requires 5+ sessions to unlock. Returns None until threshold met.
    """
    if len(sessions) < 5:
        return None

    # Determine dominant genre from sessions
    genre_counts = {}
    for s in sessions:
        g = s.game_mode or "fps"
        genre_counts[g] = genre_counts.get(g, 0) + 1
    genre = max(genre_counts, key=genre_counts.get)
    if genre not in GENRE_ROLE_MAP:
        genre = "fps"

    role_registry  = GENRE_ROLE_MAP[genre]
    metric_map     = GENRE_METRIC_MAP.get(genre, GENRE_METRIC_MAP["fps"])

    # Average each stored metric across sessions
    stored_keys = ["m_reaction", "m_accuracy", "m_eng_eff", "m_consistency", "m_cqe", "m_lre", "m_dpi"]
    avgs = {}
    for key in stored_keys:
        vals = [getattr(s, key) for s in sessions if getattr(s, key) is not None]
        avgs[key] = sum(vals) / len(vals) if vals else 50.0

    # Map stored metrics to genre dimension names
    dim = {dim_name: avgs[stored_key] for dim_name, stored_key in metric_map.items()}

    # Score each role
    best_role  = None
    best_score = -1
    for role_key, role_info in role_registry.items():
        score = sum(
            dim.get(metric, 50.0) * weight
            for metric, weight in role_info["key_metrics"].items()
        )
        if score > best_score:
            best_score = score
            best_role  = role_key

    role = role_registry[best_role]

    # Build specific reason
    top_metric = max(dim.items(), key=lambda x: x[1])
    low_metric = min(dim.items(), key=lambda x: x[1])

    def _fmt(k):
        return k.replace("_", " ").title()

    reason = (
        f"Across your last {len(sessions)} sessions, your {_fmt(top_metric[0])} "
        f"scores {top_metric[1]:.0f}/100 — which aligns strongly with the {role['name']} role. "
        f"Your {_fmt(low_metric[0])} ({low_metric[1]:.0f}/100) is your biggest growth area. "
        f"{role['description']}"
    )

    return {
        "role_key":    best_role,
        "name":        role["name"],
        "description": role["description"],
        "playstyle":   role["playstyle"],
        "strengths":   role["strengths"],
        "weaknesses":  role["weaknesses"],
        "drills":      role["drills"],
        "pro_example": role["pro_example"],
        "reason":      reason,
        "genre":       genre,
    }


# =============================================================================
# Drill Library
# =============================================================================

DRILL_LIBRARY = {
    # ── FPS — Target Acquisition (slow first-shot, late on target) ───────────
    "slow_acquisition": [
        {"id":"acq1","name":"Gridshot — Speed Priority","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"In Aimlabs or Kovaaks, run Gridshot Ultimate. Your only goal: click the instant the target appears. Don't aim first — react first. Accuracy improves naturally when speed is prioritised. Run this before any ranked game."},
        {"id":"acq2","name":"Deathmatch First Contact","duration":"15 min","frequency":"3x/week","difficulty":"Intermediate","description":"In deathmatch, measure yourself on one thing only: how quickly you get the first shot on target after spotting an enemy. Ignore kills, ignore deaths. If you're shooting within 0.4s of first contact, you're doing it right. Most players waste 0.6-1.0s before firing."},
        {"id":"acq3","name":"Target Switch Drill","duration":"8 min","frequency":"Daily","difficulty":"Intermediate","description":"In a tracking scenario, manually switch targets every 0.5 seconds. Don't wait for a target to die — switch mid-shot. Trains your eyes to lead the crosshair to the next target rather than trailing behind it."},
        {"id":"acq4","name":"Peripheral Acquisition Practice","duration":"10 min","frequency":"2x/week","difficulty":"Advanced","description":"In Aimlabs, run any scenario where targets appear at the edge of screen. Force yourself to flick without pre-aiming — your peripheral vision should trigger the movement, not your conscious decision. This is the reflex gap between players."},
    ],
    # ── FPS — Tracking (losing target while shooting, low sustained accuracy) ─
    "poor_tracking": [
        {"id":"trk1","name":"Smoothbot or VoxTS Tracking","duration":"12 min","frequency":"Daily","difficulty":"Beginner","description":"Run Smoothbot in Aimlabs or VoxTS in Kovaaks daily. The goal is to keep the dot inside the circle for the entire duration — not to click fast. Tracking is a separate skill from flicking. Most FPS players neglect it because deathmatch doesn't teach it."},
        {"id":"trk2","name":"Click-Timing Tracking Drill","duration":"10 min","frequency":"Daily","difficulty":"Intermediate","description":"In Aimlabs, run a tracking scenario and only fire when you're actually on target — not while moving toward it. Most players fire continuously and wonder why their accuracy is low. Controlled shots beat spray every time."},
        {"id":"trk3","name":"Low-Sensitivity Tracking Week","duration":"7 days","frequency":"Daily","difficulty":"Intermediate","description":"Drop your sensitivity by 15% for one full week. Tracking errors are often hidden by high sens corrections. Lower sens exposes the true weakness in your wrist movement and forces you to fix it with arm motion instead."},
        {"id":"trk4","name":"Moving Target Drill — Strafe Prediction","duration":"10 min","frequency":"3x/week","difficulty":"Advanced","description":"In a tracking scenario, the target will strafe left-right. Practice leading the target slightly ahead of its movement direction. Players who react to position lose. Players who predict movement win. The lead distance is about 0.1-0.2 crosshair widths at medium range."},
    ],
    # ── FPS — Spray Control (full-auto inaccuracy, pattern not controlled) ────
    "poor_spray": [
        {"id":"spr1","name":"Spray Pattern Trace — Single Weapon","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"Take your most-used weapon into the practice range. Fire a full magazine at a wall from close range. Study the exact pattern — it's not random. Then fire again while pulling your mouse in the opposite direction of the pattern. Repeat 50 times until the wall trace is tight."},
        {"id":"spr2","name":"7-Bullet Burst Discipline","duration":"8 min","frequency":"Daily","difficulty":"Beginner","description":"Fire 7-round bursts only, with a deliberate 0.4s pause between bursts. This breaks the habit of holding trigger when the gun is already out of your control. The first 7 rounds of any AR spray are accurate — beyond that is luck unless you've specifically trained the pattern."},
        {"id":"spr3","name":"Counter-Strafe + Spray","duration":"12 min","frequency":"3x/week","difficulty":"Intermediate","description":"The most common spray error: shooting while still moving. Drill: tap A, release, immediately fire 5 rounds, tap D, release, fire 5 rounds. The stop must be complete — both feet planted — before shooting. Counter-strafing means pressing the opposite direction key to cancel momentum instantly, not just releasing."},
        {"id":"spr4","name":"Range Distance Brackets","duration":"15 min","frequency":"2x/week","difficulty":"Intermediate","description":"Set up targets at 5m, 15m, 30m, 50m. Practice which fire mode is correct at each distance: full-auto to 10m, 5-round burst to 25m, single shots beyond. Most spray errors happen because players use full-auto at ranges where only tapping is accurate. Learn your weapon's effective range, not just its damage."},
        {"id":"spr5","name":"Recoil Pattern Memorisation Test","duration":"10 min","frequency":"Weekly","difficulty":"Advanced","description":"Without watching your crosshair, fire a full magazine at a wall. Then fire again while trying to keep the holes on top of each other. If the second pattern doesn't land on the first, you're compensating without knowing the pattern. You need the pattern memorised, not reacted to — reaction is too slow at 600 RPM."},
    ],
    # ── FPS — Overshoot / Flick Precision (landing past the target) ───────────
    "overshoot": [
        {"id":"ov1","name":"Slow Flick Training — 50% Speed","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"In any aim trainer, halve the scenario speed or target size. Focus only on stopping exactly on target — not past it. Overshoot is a deceleration problem: your hand accelerates fast but doesn't slow down before the stop point. The pause at the end of a flick is a separate motor skill from the movement itself."},
        {"id":"ov2","name":"Micro-Adjustment Isolation","duration":"8 min","frequency":"Daily","difficulty":"Intermediate","description":"Start your crosshair intentionally 5-10 degrees off target. Make only micro-corrections to land on it — no overshooting and correcting back. Builds the fine motor control for the final 10% of any flick. Most players can get to the vicinity of the target but fail at the last inch."},
        {"id":"ov3","name":"Bounce-Back Drill","duration":"12 min","frequency":"3x/week","difficulty":"Intermediate","description":"In Aimlabs: run a scenario where targets appear, flick to them, click, then immediately flick back to the origin point. The discipline is to stop exactly at the origin — not behind it. Trains deceleration in both directions simultaneously."},
        {"id":"ov4","name":"One-Tap Only Deathmatch","duration":"15 min","frequency":"2x/week","difficulty":"Advanced","description":"In deathmatch, allow yourself only one shot per engagement. If you miss, disengage and reset. Forces you to make the first shot count — which forces precision over volume. Players with overshoot problems often fire extra rounds to compensate for their missed first shot. Remove the option."},
    ],
    # ── FPS — Crosshair Placement (pre-aiming wrong angles, head level errors) ─
    "poor_placement": [
        {"id":"cp1","name":"Head Level Lock Drill","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"In any aim trainer or practice range, consciously keep your crosshair at exact head height at all times — not chest, not floor, not above. Most players let the crosshair drift to chest level by default, costing 0.1-0.15s per engagement on the micro-adjust. At 60 duels per game that's 6-9 seconds of wasted time."},
        {"id":"cp2","name":"Pre-aim Corner Study","duration":"15 min","frequency":"3x/week","difficulty":"Beginner","description":"Walk your most-played map and identify every common corner/angle an enemy can hold. For each one: where exactly should your crosshair be? Practice walking past each corner with your crosshair pre-placed. You should never arrive at an angle and then move your crosshair to head level — it should already be there."},
        {"id":"cp3","name":"Off-Angle Exposure Drill","duration":"10 min","frequency":"3x/week","difficulty":"Intermediate","description":"In deathmatch, practice peeking only after pre-aiming the exact pixel where an enemy head will appear. Don't peek and then aim — aim before you peek. This means your gun needs to be pre-positioned before your body is exposed. Most deaths are caused by peeking first and aiming second."},
        {"id":"cp4","name":"Map-Specific Crosshair Checkpoints","duration":"20 min","frequency":"Weekly","difficulty":"Intermediate","description":"Build a mental list of 10 crosshair checkpoints on your most-played map — specific pixels at each key angle. Warmup by walking the map with no enemies and hitting each checkpoint. This converts game sense knowledge into muscle memory so crosshair placement becomes automatic, not conscious."},
    ],
    # ── FPS — Close-Quarter Engagement (low win rate in tight spaces) ─────────
    "poor_cqe": [
        {"id":"cq1","name":"Shotgun Deathmatch Training","duration":"15 min","frequency":"2x/week","difficulty":"Beginner","description":"Switch to a shotgun or pistol only in deathmatch. Forced close-range engagements develop the specific reaction pattern for CQC — turning speed, sidestep before shooting, awareness of the enemy's gun barrel. Players who only rifle never develop comfortable CQC instincts."},
        {"id":"cq2","name":"Strafe-Peek vs Static Drill","duration":"10 min","frequency":"3x/week","difficulty":"Intermediate","description":"In deathmatch, test both methods in close range: (1) static stop-and-shoot, (2) strafe-peek while shooting. Most players default to static. At sub-10m, moving while shooting is often more effective because you're harder to hit. Learn which method your current skill level supports — don't assume."},
        {"id":"cq3","name":"Knife-Range Awareness Training","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"Play 10 deathmatch rounds where you only engage enemies within 5 metres. This forces you to learn the exact turn speed, crosshair placement, and pre-aim angle for knife-range duels — a completely different skill set from mid-range. Most players panic in CQC because they've never specifically trained it."},
        {"id":"cq4","name":"Counter-Peek Timing","duration":"12 min","frequency":"2x/week","difficulty":"Advanced","description":"At close range, the player who peeks second often wins — they see the enemy first while staying covered longer. Practice holding an angle until you hear footsteps very close, then pre-fire the corner slightly ahead of where the head will appear. This is peekers advantage exploited defensively."},
    ],
    # ── FPS — Long-Range Engagement (poor first shots at distance) ────────────
    "poor_lre": [
        {"id":"lr1","name":"Single-Shot Precision Drill","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"At long range, the first shot is everything. In your aim trainer, run a flicking scenario with large distances and train clicking once per target — not spraying. At 50m+ in most games, spray control is irrelevant: only first-shot accuracy matters. A 90% first-shot rate with single clicks beats a 40% rate with spray at distance."},
        {"id":"lr2","name":"Breath Control Simulation","duration":"8 min","frequency":"Daily","difficulty":"Beginner","description":"Before each long-range shot in practice, force a 0.3s pause after counter-strafing — don't shoot while your crosshair is still settling. This simulates the real accuracy window. Most players fire the moment they stop moving, but the crosshair needs an additional 0.1-0.2s to fully settle after stopping."},
        {"id":"lr3","name":"Scope/ADS Discipline Practice","duration":"12 min","frequency":"3x/week","difficulty":"Intermediate","description":"If your game has ADS/scope mechanics, practice: counter-strafe → ADS → fire → un-ADS → move. Most players ADS too early (telegraphing position) or too late (missing the accuracy window). The ADS should happen simultaneously with the stop, not before or after."},
        {"id":"lr4","name":"Long-Range Map Positions Study","duration":"15 min","frequency":"Weekly","difficulty":"Intermediate","description":"Walk your most-played map and identify every sightline longer than 40m. For each one: what's the optimal position, cover, and angle? Long-range duels are won by positioning before mechanics. If you're losing long-range fights regularly, check whether you're giving the enemy a better angle than you have — not whether your aim is bad."},
    ],
    # ── FPS — Consistency (high session-to-session variance) ─────────────────
    "inconsistent": [
        {"id":"inc1","name":"Fixed Warmup Sequence","duration":"15 min","frequency":"Before every session","difficulty":"Beginner","description":"Build a fixed 3-step warmup you run in the same order before every session: (1) 5 min Gridshot/equivalent, (2) 5 min tracking scenario, (3) 5 min deathmatch at 50% engagement. Players with high session variance almost always have no warmup routine. Consistency comes from routine, not talent."},
        {"id":"inc2","name":"90-Minute Session Cap","duration":"90 min max","frequency":"Daily","difficulty":"Beginner","description":"Cut ranked sessions to 90 minutes hard. Log your performance in session 1 vs session 3+ — most players drop 15-25% in mechanical precision after 90 minutes. The sessions hurting your rank are almost always the late ones where you're fatigued and in denial about it."},
        {"id":"inc3","name":"Sensitivity Lock — 30 Days","duration":"30 day commitment","frequency":"One-time","difficulty":"Beginner","description":"Stop changing your sensitivity. Every change resets muscle memory partially. Pick a sensitivity in the 15-25cm/360 range and lock it for 30 days minimum. If you've changed sens in the last 2 weeks, your inconsistency is probably not mechanical — it's procedural. Let the muscle memory solidify before evaluating."},
        {"id":"inc4","name":"Session Rating Log","duration":"2 min","frequency":"After every session","difficulty":"Beginner","description":"After each session, write down: (1) overall feel 1-10, (2) what went wrong, (3) what went right, (4) hours slept last night. Do this for 2 weeks. Your inconsistency has a pattern — you just haven't found it yet. Sleep, time of day, and previous session count are the three most common hidden variables."},
        {"id":"inc5","name":"Identical Settings Checklist","duration":"1 min","frequency":"Before every session","difficulty":"Beginner","description":"Before playing, check: same DPI, same in-game sensitivity, same resolution, same Hz. A single setting change invalidates muscle memory comparisons between sessions. Write your settings down and check them against the list before every session for 2 weeks."},
    ],
    # ── FPS — Session Fade (performance degrades as session progresses) ────────
    "session_fade": [
        {"id":"sf1","name":"45-Minute Hard Stop","duration":"5 min break","frequency":"Every 45 min","difficulty":"Beginner","description":"Set a phone timer. At 45 minutes: stand up, walk around, look at something 6+ metres away for 60 seconds. Eye fatigue is the primary driver of reaction time decay in long sessions. Most players attribute late-session performance drops to tilt or bad luck. It's usually eyes."},
        {"id":"sf2","name":"Hydration Protocol","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Drink 250ml of water every 20 minutes during play. Mild dehydration (1-2% body weight) measurably reduces reaction time and decision-making. Keep a bottle at your desk — not in the kitchen. The barrier of getting up is why most players don't hydrate during sessions."},
        {"id":"sf3","name":"Late-Session Only Deathmatch","duration":"15 min","frequency":"When fading","difficulty":"Intermediate","description":"If you notice performance dropping after 60+ minutes, switch to unranked deathmatch for the next 15 minutes instead of continuing ranked. Reset your mechanics in a low-stakes environment before queueing again. Forcing ranked games while fading locks in bad habits and tanks your rank simultaneously."},
        {"id":"sf4","name":"Tilt Recognition Protocol","duration":"Ongoing","frequency":"When needed","difficulty":"Beginner","description":"Define your tilt threshold in advance: 3 losses in a row = mandatory 15-minute break, no exceptions. Most players know they're tilted but keep playing anyway. Pre-committing to the rule removes the decision from your tilted brain. Write the rule down somewhere visible before your next session."},
    ],
    # ── FPS — Reaction Time (globally slow first input) ───────────────────────
    "slow_reaction": [
        {"id":"rt1","name":"Human Benchmark Daily Baseline","duration":"3 min","frequency":"Daily","difficulty":"Beginner","description":"Take the Human Benchmark reaction time test every day before gaming. Log the result. Your baseline will improve naturally with sleep, hydration, and caffeine management. More importantly, you'll see when you're playing below baseline — which is the signal to stop or warm up harder, not to keep grinding."},
        {"id":"rt2","name":"Audio Cue Training","duration":"10 min","frequency":"3x/week","difficulty":"Intermediate","description":"In deathmatch, practice reacting to audio cues before visual ones. Most FPS games give audio information (footsteps, reload sounds, ability sounds) 0.2-0.4s before the visual appears. Players who react to sound are consistently faster than players who wait to see. Find the specific audio cue for your most-played game's common engagements."},
        {"id":"rt3","name":"Pre-Aim While Rotating","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"While rotating or repositioning, keep your crosshair at head level and pre-aim the most likely angle you'll encounter an enemy. The reaction time advantage comes from pre-aiming — not from having a faster nervous system. An average player who always pre-aims beats a skilled player who doesn't, most of the time."},
        {"id":"rt4","name":"Caffeine Timing Test","duration":"1 week test","frequency":"Weekly","difficulty":"Beginner","description":"Test your reaction time 30 minutes after a moderate caffeine intake vs without. For most people caffeine improves reaction time by 5-10ms. For competitive play that's meaningful. Find your optimal dose and timing — and learn whether you're crashing during sessions by taking caffeine too early."},
    ],
    # ── FPS — Engagement Efficiency (low kill/death ratio, bad trade decisions) ─
    "poor_eng_eff": [
        {"id":"ee1","name":"Trade Audit — Death Review","duration":"10 min","frequency":"After every session","difficulty":"Beginner","description":"After each session, review only your deaths, not your kills. For each death ask: (1) was I in a bad position? (2) did I peek without info? (3) did I hold too long? Most players who die often make the same 1-2 mistakes repeatedly. You can find them in 10 minutes of honest review — without a coach."},
        {"id":"ee2","name":"Utility-First Habit","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"Before every peek or engagement, use one piece of utility — smoke, flash, molotov, or ability. Players who peek raw in games with utility are systematically disadvantaged. If you're not using utility before every contact, you're not playing the game — you're playing a worse version of it."},
        {"id":"ee3","name":"Information Discipline Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"Stop taking duels when you don't have information. In practice: if you don't know where an enemy is, don't push. Gather information (audio, minimap, teammate comms) first. Most low-efficiency players die to enemies they had zero information about — those deaths are 100% avoidable regardless of mechanics."},
        {"id":"ee4","name":"Economy Awareness Training","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"In CS/Valorant: before every round, check your team's economy and the opponent's estimated economy. Your decision to eco, half-buy, or full-buy should be made with information, not habit. A team that eco-disciplines correctly wins 10-15% more rounds on economy advantages alone — before mechanics even matter."},
    ],
    # ── FPS — Strong mechanics (player is performing well) ────────────────────
    "strong_aim": [
        {"id":"sa1","name":"Maintain — Don't Grind","duration":"10 min","frequency":"Daily","difficulty":"Intermediate","description":"Your mechanics are above average. The biggest risk now is over-training: grinding aim for 60+ minutes before sessions causes micro-fatigue that hurts your actual rank games. Cap aim training at 15 minutes max. Use the saved time to study positioning and utility — that's where your next rank up comes from."},
        {"id":"sa2","name":"Advanced Scenario Rotation","duration":"15 min","frequency":"Daily","difficulty":"Advanced","description":"Rotate between 3 different scenario types daily: flicking, tracking, and clicking precision. This prevents over-specialisation. Strong aimers who plateau usually have one underdeveloped category hiding behind their overall good score. Find it by testing each type separately."},
    ],

    # ── Racing ────────────────────────────────────────────────────────────────
    "late_braking": [
        {"id":"rb1","name":"Brake Marker Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"Pick one corner per track. Place a mental or visual marker and commit to the same braking point every lap. Consistency before aggression."},
        {"id":"rb2","name":"Trail Braking Practice","duration":"20 min","frequency":"3x/week","difficulty":"Intermediate","description":"Practice releasing the brake gradually as you turn in. The goal is to feel the car rotate — brake too late and you understeer, too early and you lose time."},
        {"id":"rb3","name":"One Corner Focus","duration":"20 min","frequency":"3x/week","difficulty":"Intermediate","description":"Choose the hardest corner on your current track. Do 20 laps focusing only on that corner. Ignore your lap time — improve the corner."},
    ],
    "poor_throttle": [
        {"id":"th1","name":"Smooth Exit Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"On corner exit, apply throttle in a slow, progressive squeeze — not a stomp. Count to 2 seconds from apex to full throttle. Feel the rear grip before pushing."},
        {"id":"th2","name":"Wet Weather Practice","duration":"20 min","frequency":"2x/week","difficulty":"Intermediate","description":"Run 10 laps in wet conditions even if you mainly race dry. Low grip forces smooth inputs — it fixes aggressive throttle habits fast."},
    ],
    "racing_inconsistent": [
        {"id":"ri1","name":"Reference Lap Study","duration":"15 min","frequency":"Before each session","difficulty":"Beginner","description":"Watch one clean reference lap of your current track before playing. Focus on turn-in points, not racing lines. Build the mental map before you drive."},
        {"id":"ri2","name":"Sector Focus Method","duration":"20 min","frequency":"3x/week","difficulty":"Intermediate","description":"Split the track into 3 sectors. Spend 15 minutes improving only Sector 1, then Sector 2, then Sector 3 — separately. Don't try to nail the whole lap at once."},
    ],
    "poor_racing_line": [
        {"id":"rl1","name":"Geometric Line Drill","duration":"20 min","frequency":"3x/week","difficulty":"Beginner","description":"Aim for late apex on every corner — turn in later than feels natural. A late apex protects your exit speed which matters more than the entry."},
        {"id":"rl2","name":"Ghost Car Chase","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"Race your own ghost or a time trial ghost. Follow its line exactly, don't try to beat it. You're learning the line, not the pace."},
    ],

    # ── Sports ────────────────────────────────────────────────────────────────
    "poor_positioning": [
        {"id":"sp1","name":"Rotation Awareness Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"After every action — shot, pass, tackle — pause mentally and ask: where should I be next? Practice moving to that position before the play develops."},
        {"id":"sp2","name":"Shadow Positioning","duration":"20 min","frequency":"3x/week","difficulty":"Intermediate","description":"In free play or practice mode, move without the ball for 5 minutes straight. Focus only on spacing relative to teammates and opponents."},
    ],
    "slow_decision": [
        {"id":"sd1","name":"First Touch Direction Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"Every time you receive the ball, your first touch must move it in the direction of your next action. No dead-ball traps — always touch with purpose."},
        {"id":"sd2","name":"One-Touch Challenge","duration":"20 min","frequency":"3x/week","difficulty":"Intermediate","description":"In a practice session, limit yourself to one touch whenever possible. Forces you to read play before the ball arrives rather than after."},
    ],
    "poor_shot": [
        {"id":"ps1","name":"Shot Placement Drill","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"In shooting practice, aim for the corners — low left, low right, top corners. Don't blast it, place it. Placement beats power at most skill levels."},
        {"id":"ps2","name":"Finishing Under Pressure","duration":"15 min","frequency":"3x/week","difficulty":"Intermediate","description":"Have a defender close you down in practice before shooting. Simulate real match pressure — shooting while off-balance is different from a clean strike."},
    ],

    # ── Strategy / MOBA ───────────────────────────────────────────────────────
    "poor_macro": [
        {"id":"mac1","name":"Minimap Check Habit","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Set a timer to beep every 10 seconds. Each beep = check the minimap. Do this for one full session. After a week it becomes automatic."},
        {"id":"mac2","name":"Objective Timer Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Write down or mentally track when major objectives spawn. Move toward them 30 seconds early. Most macro errors are timing errors."},
        {"id":"mac3","name":"Win Condition Review","duration":"5 min","frequency":"After every game","difficulty":"Intermediate","description":"After each game, ask: what was my win condition? Did I play toward it? Replays or mental review only — no excuses, just analysis."},
    ],
    "poor_resource": [
        {"id":"res1","name":"CS/Farm Focus Session","duration":"20 min","frequency":"Daily","difficulty":"Beginner","description":"Play a practice game with one goal: hit your CS/farm target every minute. Ignore kills and objectives for now. Resource collection is a separate skill."},
        {"id":"res2","name":"Wave Management Study","duration":"15 min","frequency":"3x/week","difficulty":"Intermediate","description":"Watch one replay focusing only on wave state — not fights. Identify when waves froze, slow pushed, or crashed. Most resource errors start with misread waves."},
    ],
    "poor_team_play": [
        {"id":"tp1","name":"Ping Discipline Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Only ping when it adds information your team doesn't have. Over-pinging desensitizes teammates. One useful ping beats five noise pings."},
        {"id":"tp2","name":"Follow-the-Shotcaller","duration":"20 min","frequency":"3x/week","difficulty":"Beginner","description":"For one full game, do exactly what your most communicative teammate suggests — even if you disagree. Builds coordination habits and shows you how others read the game."},
    ],

    # ── Extraction Shooters (ARC Raiders, Escape From Tarkov, Hunt: Showdown) ──
    "poor_extraction": [
        {"id":"ex1","name":"5-Minute Extraction Rule","duration":"Ongoing","frequency":"Every raid","difficulty":"Beginner","description":"In ARC Raiders, set a mental timer when you land. After 5 minutes in a hot zone, start moving toward extraction regardless of loot. Most deaths happen to players who overstay. Loot efficiency beats loot volume."},
        {"id":"ex2","name":"Exit Route Pre-Planning","duration":"2 min","frequency":"Before every raid","difficulty":"Beginner","description":"Before dropping in, identify two extraction points on the map. Know your primary and your backup before you touch the ground. Players who die at extraction mostly never had a plan B."},
        {"id":"ex3","name":"Noise Budget Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"ARC Raiders punishes sound. Give yourself a noise budget per area: 2 loud actions max (breaking a container, firing a shot) before repositioning. If you exceed it, move — don't wait to see if you were heard."},
    ],
    "poor_stamina_mgmt": [
        {"id":"st1","name":"Mobility Tree Priority","duration":"1 session","frequency":"Once","difficulty":"Beginner","description":"In ARC Raiders, your first 15 skill points should go into Mobility — Marathon Runner and Youthful Lungs first. Low stamina is a hidden difficulty multiplier. Every fight you take with low stamina is harder than it needs to be."},
        {"id":"st2","name":"Sprint-Stop-Assess Habit","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Never sprint into an unknown area. Sprint to cover, stop, look, then decide. Sprinting into open ground with low stamina is the most common death pattern in extraction games."},
        {"id":"st3","name":"Stamina Recovery Timing","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"Learn your stamina regen delay — in ARC Raiders it starts ~1.5s after you stop sprinting. Use this during reloads: stop, reload, your stamina is mostly recovered by the time the mag is in. Stack downtime actions."},
    ],
    "poor_ai_awareness": [
        {"id":"ai1","name":"ARC Aggro Reset Exploit","duration":"15 min","frequency":"3x/week","difficulty":"Intermediate","description":"ARC units in ARC Raiders reset aggro if you leave their search area before they reach your last known position. Practice luring a Tick or Leaper, breaking line of sight, and waiting behind cover. When sound stops — they reset. Learn the timer."},
        {"id":"ai2","name":"Snitch Awareness Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Snitches call reinforcements if they spot you in the open. Practice identifying Snitch patrol routes in Dam Battlegrounds first. Move between cover, check sky, move again. Getting spotted by a Snitch while looting is an avoidable death."},
        {"id":"ai3","name":"ARC Sound Cue Study","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"Spend 10 minutes in a raid doing nothing but listening. Learn the distinct audio signature of each ARC type — Ticks (scuttle), Leapers (heavy step), Bombardier (spotter drone hum). Audio identification is faster than visual in this game."},
    ],
    "poor_looting": [
        {"id":"lo1","name":"Security Breach First","duration":"1 session","frequency":"Once","difficulty":"Beginner","description":"In ARC Raiders, Security Breach (Survival tree pinnacle) unlocks Security Lockers — the best loot in the game. If you haven't taken this skill yet, stop spending points elsewhere. One Security Locker per raid is worth more than 10 standard containers."},
        {"id":"lo2","name":"Container Noise Management","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"Breaching lockers and cars makes sharp sounds that carry. Before cracking a container, clear the immediate area and listen for footsteps. The loot isn't worth dying for — take 5 seconds to check first."},
        {"id":"lo3","name":"Stack Target Discipline","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Before each raid, set a specific loot target: ammo to X, augments to Y, one weapon upgrade. Players who loot with a list extract faster and die less than players who grab everything. Know what you came for."},
    ],

    # ── ARC Raiders — Enemy Weak Points ─────────────────────────────────────
    "arc_enemy_knowledge": [
        {"id":"wp1","name":"Yellow = Shoot Here","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"ARC Raiders color codes all weak points yellow. If you see a glowing or yellow part on any machine — that's your target. White surfaces take full damage, grey/dark armor resists light ammo. Always ID the yellow before firing. Mag dumping grey plates wastes ammo and draws attention."},
        {"id":"wp2","name":"Tick Clearing Drill","duration":"Ongoing","frequency":"Every indoor raid","difficulty":"Beginner","description":"Ticks have no armor — any weapon kills them fast. The danger is the group ambush from ceilings and walls. Before entering a building, slow down and listen for skittering. Shotguns clear Tick groups in one shot. Never sprint into a dark indoor area blindly."},
        {"id":"wp3","name":"Wasp Thruster Priority","duration":"10 min","frequency":"3x/week","difficulty":"Beginner","description":"To kill a Wasp, destroy two of its thrusters — it crashes and dies from the fall. Don't aim for the body. Three Anvil shots or equivalent to the body works too, but thruster shots are faster and waste less ammo. Ignore the wings entirely."},
        {"id":"wp4","name":"Hornet Front Plate Break","duration":"10 min","frequency":"3x/week","difficulty":"Intermediate","description":"Most players aim for the rear thrusters on Hornets — hard to hit in combat. Instead break the front plating with medium-to-heavy ammo first, then shoot the exposed thrusters behind it. Destroying two thrusters on one side kills it faster than going for the back angle."},
        {"id":"wp5","name":"Snitch Priority Protocol","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"The Snitch doesn't fight — it calls reinforcements. It has enhanced hearing unlike most ARC. Rule: if you spot a Snitch, everything else stops until it's dead. One Snitch alert can pull enough ARC to end your raid. It dies in a few shots — it has no armor. Shoot it from distance before it detects you."},
        {"id":"wp6","name":"Fireball Core Window Drill","duration":"10 min","frequency":"3x/week","difficulty":"Intermediate","description":"The Fireball's weak point is its core, which only opens when it's about to attack with its flamethrower. The move: bait it, back away, wait for it to start the attack animation, then dump shots into the open core. Don't shoot the front armor — it's heavily plated. Time your shot windows."},
        {"id":"wp7","name":"Surveyor Blue Beam Timing","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"The Surveyor has no weapons but calls nearby ARC when it detects you. Its yellow weak point only exposes while it's scanning (emitting the blue beam upward). Wait for the beam, then shoot the yellow core. Breaking armor plates drops loot — each plate is worth picking up."},
        {"id":"wp8","name":"Leaper Fire Weakness","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"Leapers are heavily armored spider machines — but they have a critical weakness to fire. Two Fireball Burners or Blaze Grenades will kill a Leaper in seconds. Without fire, shoot its red eye for bonus damage, or target the yellow rear joints to slow it and expose inner sections. Never fight a Leaper in the open — use cover to bait its leap attack."},
        {"id":"wp9","name":"Bombardier — Spotter First","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"The Bombardier fires mortar rounds that follow your movement. Its Spotter drones paint your position making it accurate. Kill the Spotter drone first — this disorients the Bombardier immediately. Then target its yellow kneecaps to slow it and expose inner sections, or the yellow rear cylinder to disable it. Attack right after it fires artillery — that's your safe window."},
        {"id":"wp10","name":"Bastion Back Weak Spot Approach","duration":"15 min","frequency":"2x/week","difficulty":"Advanced","description":"The Bastion's weak point is on its back — risky to reach. Two approaches: (1) Use sticky grenades — Snap Hook or Trigger grenades stuck directly to its back weak spot. Bouncing grenades don't work. (2) Bait it near cover, damage its knees until they explode, use the stun window to dump damage. Repeat. Never approach Bastion in the open."},
        {"id":"wp11","name":"Queen Fight — Team Protocol","duration":"20 min","frequency":"Once per Harvester event","difficulty":"Advanced","description":"The Queen only spawns during Harvester events. Required: heavy ammo and Heavy Fuze Grenades. Phase 1 — destroy leg armor to expose yellow leg joints, this reduces mobility. Phase 2 — the head core opens briefly during its laser attack only. That is your entire damage window. Miss it and the fight drags. The head closes again after the laser — stop shooting and reposition. Focus fire and communication decides this fight, not individual DPS."},
        {"id":"wp12","name":"Rocketeer Grenade Stun","duration":"10 min","frequency":"2x/week","difficulty":"Intermediate","description":"The Rocketeer looks intimidating but is stunnable. Hit it with a Hornet Driver or Showstopper grenade — this grounds it for around 10 seconds. Then shoot its thrusters freely while it's down. With heavy firepower, a Wolfpack plus one Hullcracker shot is a clean instant takedown. Missing the grenade makes this fight significantly harder."},
    ],

    # ── Battle Royale (Warzone, Fortnite, Apex Legends) ──────────────────────
    "poor_zone_awareness": [
        {"id":"br1","name":"Zone Timer Habit","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Check the zone timer every 30 seconds — not when you hear it closing. Most late-game deaths are avoidable zone damage. Set a habit: loot 2 items, check timer, loot 2 items, check timer."},
        {"id":"br2","name":"High Ground Priority Drill","duration":"Ongoing","frequency":"Every session","difficulty":"Intermediate","description":"When the final zone spawns, identify the highest point inside it and move there before fighting. High ground in the final circle wins more games than superior aim. Position first, then engage."},
    ],
    "poor_drop_decision": [
        {"id":"dr1","name":"Drop Location Discipline","duration":"Ongoing","frequency":"Every session","difficulty":"Beginner","description":"Pick one drop location per session and stick to it for 5 games. Rotating drop spots means you never master any of them. Consistency builds map knowledge faster than variety."},
        {"id":"dr2","name":"Hot Drop Survival Drill","duration":"Ongoing","frequency":"2x/week","difficulty":"Intermediate","description":"Intentionally hot drop for 5 games — land on the first contested POI. Focus only on: land speed, first loot priority (weapon over armor), and first engagement decision. Getting good under pressure requires practicing pressure."},
    ],

    # ── Fighting ─────────────────────────────────────────────────────────────
    "poor_neutral": [
        {"id":"fn1","name":"Whiff Punish Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"In training mode, have the dummy perform its most punishable move on repeat. Practice the punish 50 times until it's automatic. Neutral is 50% whiff punishing."},
        {"id":"fn2","name":"Footsie Walk Drill","duration":"15 min","frequency":"Daily","difficulty":"Intermediate","description":"Walk in and out of the opponent's max range for 2 minutes without attacking. Learn the exact distance where their moves whiff. That distance is your office."},
    ],
    "poor_combo": [
        {"id":"fc1","name":"BnB Combo Isolation","duration":"20 min","frequency":"Daily","difficulty":"Beginner","description":"Pick your character's most important punish combo. Do it 100 times in training — from both sides. Don't move on until you hit it 10 in a row."},
        {"id":"fc2","name":"Confirm Training","duration":"15 min","frequency":"Daily","difficulty":"Intermediate","description":"Practice hit-confirming into your combo from a jab or poke. Set dummy to random guard. React to the hit, not the input. Execution is nothing if you can't confirm."},
    ],
    "poor_defense": [
        {"id":"fd1","name":"Reversal Timing Drill","duration":"15 min","frequency":"Daily","difficulty":"Beginner","description":"Practice your reversal/invincible move from block stun 50 times. Know its startup, know its range. An unreliable reversal is worse than no reversal."},
        {"id":"fd2","name":"Crouch Block Drill","duration":"10 min","frequency":"Daily","difficulty":"Beginner","description":"Set the dummy to random high/low attacks. Practice blocking low by default and standing for overheads. Low-blocking is the safer default at most skill levels."},
        {"id":"fd3","name":"Throw Break Practice","duration":"10 min","frequency":"Daily","difficulty":"Intermediate","description":"Set the dummy to throw randomly. React and break. Throw-teching is a reflex — it takes dedicated repetitions to build, not match experience alone."},
    ],
}


def _get_drills_for_habits(habits: list) -> list:
    """Return relevant drills based on identified habits."""
    drills = []
    seen = set()
    for habit in habits:
        key = habit.get("key", "")
        if key in DRILL_LIBRARY:
            for drill in DRILL_LIBRARY[key]:
                if drill["id"] not in seen:
                    seen.add(drill["id"])
                    drills.append({**drill, "habit_key": key})
    return drills


# =============================================================================
# Rank Routes
# =============================================================================

@router.get("/rank/me", summary="Get current player rank and role suggestion")
def get_my_rank(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user.id, DerivedSession.is_ranked == True)
        .order_by(DerivedSession.submitted_at.desc())
        .all()
    )

    total = len(sessions)
    sessions_needed = max(0, MIN_SESSIONS_FOR_RANK - total)

    if total == 0:
        return {
            "rank":            "Unranked",
            "tier":            0,
            "sessions_needed": MIN_SESSIONS_FOR_RANK,
            "progress":        0,
            "message":         f"Upload {MIN_SESSIONS_FOR_RANK} ranked sessions to receive your official rank.",
            "suggested_role":  None,
        }

    osis = [s.osi_session for s in sessions if s.osi_session]
    avg_osi     = round(sum(osis) / len(osis), 1) if osis else 0
    consistency = _compute_consistency(osis)
    rank        = _assign_rank(avg_osi, consistency, total)

    # Role suggestion (needs 3+)
    role_suggestion = _suggest_role(sessions) if total >= 3 else None

    # Update or create PlayerRank row
    player_rank = db.query(PlayerRank).filter(PlayerRank.user_id == user.id).first()
    if not player_rank:
        player_rank = PlayerRank(user_id=user.id)
        db.add(player_rank)

    player_rank.rank_label    = rank["label"]
    player_rank.rank_tier     = rank["tier"]
    player_rank.avg_osi       = avg_osi
    player_rank.consistency   = consistency
    player_rank.total_sessions= total
    player_rank.suggested_role= role_suggestion["role_key"] if role_suggestion else None
    player_rank.role_reason   = role_suggestion["reason"] if role_suggestion else None
    player_rank.last_updated  = _now()
    db.commit()

    return {
        "rank":            rank["label"],
        "tier":            rank["tier"],
        "color":           rank["color"],
        "avg_osi":         avg_osi,
        "consistency":     consistency,
        "total_sessions":  total,
        "sessions_needed": sessions_needed,
        "progress":        min(100, int((total / MIN_SESSIONS_FOR_RANK) * 100)),
        "is_ranked":       total >= MIN_SESSIONS_FOR_RANK,
        "suggested_role":  role_suggestion,
        "eligible_coach":  rank["tier"] >= MIN_RANK_FOR_COACH,
        "message":         (
            f"You are officially ranked {rank['label']}." if total >= MIN_SESSIONS_FOR_RANK
            else f"{sessions_needed} more session{'s' if sessions_needed != 1 else ''} needed for your official rank."
        ),
    }


@router.get("/rank/progress", summary="Detailed rank progression breakdown")
def get_rank_progress(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user.id, DerivedSession.is_ranked == True)
        .order_by(DerivedSession.submitted_at.asc())
        .all()
    )

    trend = []
    for i, s in enumerate(sessions):
        chunk = sessions[:i+1]
        osis  = [x.osi_session for x in chunk if x.osi_session]
        avg   = round(sum(osis) / len(osis), 1) if osis else 0
        cons  = _compute_consistency(osis)
        rank  = _assign_rank(avg, cons, len(chunk))
        trend.append({
            "session":     i + 1,
            "osi":         s.osi_session,
            "rolling_avg": avg,
            "consistency": cons,
            "rank":        rank["label"],
            "at":          s.submitted_at,
        })

    next_rank = None
    if sessions:
        osis = [s.osi_session for s in sessions if s.osi_session]
        avg  = sum(osis) / len(osis) if osis else 0
        cons = _compute_consistency(osis)
        rank = _assign_rank(avg, cons, len(sessions))
        if rank["tier"] < 9:
            next_r = RANKS[rank["tier"] + 1]
            next_rank = {
                "label":      next_r["label"],
                "osi_needed": next_r["min_osi"],
                "gap":        max(0, round(next_r["min_osi"] - avg, 1)),
            }

    return {
        "total_sessions": len(sessions),
        "trend":          trend,
        "next_rank":      next_rank,
        "ranks":          [{"tier": r["tier"], "label": r["label"], "min_osi": r["min_osi"], "color": r["color"]} for r in RANKS],
    }


# =============================================================================
# Coaching / Drill Routes
# =============================================================================

@router.get("/coaching/plan", summary="Get personalised coaching plan based on all analyses")
def get_coaching_plan(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user.id)
        .order_by(DerivedSession.submitted_at.desc())
        .limit(10)
        .all()
    )

    if not sessions:
        return {"message": "Upload at least one session to receive a coaching plan.", "drills": [], "trends": {}}

    # ── Step 1: Determine dominant genre ──────────────────────────────────────
    from app.models import GAME_REGISTRY
    _gcounts = {}
    for s in sessions:
        g = getattr(s, "game_id", None) or "other"
        _gcounts[g] = _gcounts.get(g, 0) + 1
    _dominant = max(_gcounts, key=_gcounts.get) if _gcounts else "other"
    _game_data = GAME_REGISTRY.get(_dominant, {})
    _style     = _game_data.get("style", "tactical")
    _style_map = {
        "extraction":   "extraction",
        "battleroyale": "battleroyale",
        "tactical":     "fps",
        "arena":        "fps",
        "hero":         "fps",
        "sim":          "racing",
        "arcade":       "racing",
        "rts":          "strategy",
        "moba":         "strategy",
        "fighting":     "fighting",
        "sports":       "sports",
    }
    _genre_cat = _style_map.get(_style, _game_data.get("category", "fps"))

    # ── Step 2: Compute per-metric trends (avg + direction) ───────────────────
    metric_keys = {
        "reaction":    "m_reaction",     # FPS: targetAcquisition / flick speed
        "accuracy":    "m_accuracy",     # FPS: spreadControl / spray
        "eng_eff":     "m_eng_eff",      # FPS: onTargetTracking / trackingControl
        "consistency": "m_consistency",  # session-to-session variance
        "cqe":         "m_cqe",          # FPS: overshootControl / flickPrecision
        "lre":         "m_lre",          # FPS: sessionMomentum / long-range
        "dpi":         "m_dpi",          # composite / crosshair placement proxy
    }
    trends = {}
    for label, col in metric_keys.items():
        vals = [getattr(s, col) for s in sessions if getattr(s, col) is not None]
        if not vals:
            continue
        avg  = round(sum(vals) / len(vals), 1)
        half = max(1, len(vals) // 2)
        older    = vals[half:]
        newer    = vals[:half]
        old_avg  = sum(older) / len(older) if older else avg
        new_avg  = sum(newer) / len(newer) if newer else avg
        delta    = round(new_avg - old_avg, 1)
        trends[label] = {
            "avg":    avg,
            "delta":  delta,
            "status": "improving" if delta > 2 else ("declining" if delta < -2 else "stable"),
        }

    # ── Step 3: Collect all habits from individual session analyses ───────────
    # These are the habits the analysis engine already detected per clip.
    # Group by habit key and count occurrences across sessions.
    habit_frequency: dict = {}   # key → {"count": int, "severity": str, "name": str}
    for s in sessions:
        session_habits = getattr(s, "habits_json", None) or []
        if isinstance(session_habits, str):
            import json as _json
            try:
                session_habits = _json.loads(session_habits)
            except Exception:
                session_habits = []
        for h in session_habits:
            k = h.get("key", "")
            if not k or h.get("isPositive", False):
                continue
            if k not in habit_frequency:
                habit_frequency[k] = {"count": 0, "severity": h.get("severity", "occasional"), "name": h.get("name", k)}
            habit_frequency[k]["count"] += 1

    # ── Step 4: FPS-specific — metric→habit mapping with NO duplicates ────────
    # Each stored metric column maps to a UNIQUE drill bucket.
    # This is the core fix: reaction and eng_eff no longer both map to slow_acquisition.
    FPS_METRIC_TO_HABIT = {
        # m_reaction    = targetAcquisition = flick speed
        "reaction":    "slow_acquisition",
        # m_accuracy    = spreadControl = recoil pattern
        "accuracy":    "poor_spray",
        # m_eng_eff     = onTargetTracking = sustained tracking (DISTINCT from flick)
        "eng_eff":     "poor_tracking",
        # m_consistency = session variance
        "consistency": "inconsistent",
        # m_cqe         = overshootControl = flickPrecision (distinct from acquisition speed)
        "cqe":         "overshoot",
        # m_lre         = sessionMomentum proxy for long-range eff
        "lre":         "poor_lre",
        # m_dpi         = composite — crosshair placement proxy
        "dpi":         "poor_placement",
    }
    GENRE_METRIC_TO_HABIT = {
        "fps":         FPS_METRIC_TO_HABIT,
        "extraction": {
            "reaction":    "poor_ai_awareness",
            "accuracy":    "arc_enemy_knowledge",
            "eng_eff":     "poor_stamina_mgmt",
            "consistency": "poor_extraction",
            "cqe":         "poor_looting",
            "lre":         "poor_extraction",
            "dpi":         "session_fade",
        },
        "battleroyale": {
            "reaction":    "poor_drop_decision",
            "accuracy":    "poor_spray",
            "eng_eff":     "poor_zone_awareness",
            "consistency": "inconsistent",
            "cqe":         "overshoot",
            "lre":         "poor_drop_decision",
            "dpi":         "session_fade",
        },
        "racing": {
            "reaction":    "late_braking",
            "accuracy":    "poor_racing_line",
            "eng_eff":     "poor_throttle",
            "consistency": "racing_inconsistent",
            "cqe":         "poor_racing_line",
            "lre":         "late_braking",
            "dpi":         "session_fade",
        },
        "sports": {
            "reaction":    "slow_decision",
            "accuracy":    "poor_shot",
            "eng_eff":     "poor_positioning",
            "consistency": "inconsistent",
            "cqe":         "poor_shot",
            "lre":         "poor_positioning",
            "dpi":         "session_fade",
        },
        "strategy": {
            "reaction":    "poor_macro",
            "accuracy":    "poor_resource",
            "eng_eff":     "poor_macro",
            "consistency": "inconsistent",
            "cqe":         "poor_team_play",
            "lre":         "poor_resource",
            "dpi":         "session_fade",
        },
        "fighting": {
            "reaction":    "poor_neutral",
            "accuracy":    "poor_combo",
            "eng_eff":     "poor_neutral",
            "consistency": "inconsistent",
            "cqe":         "poor_defense",
            "lre":         "poor_combo",
            "dpi":         "session_fade",
        },
    }
    metric_to_habit = GENRE_METRIC_TO_HABIT.get(_genre_cat, FPS_METRIC_TO_HABIT)

    # ── Step 5: Score and rank drill priorities ───────────────────────────────
    # Priority = f(metric avg, trend direction, habit recurrence across sessions)
    # Lower metric average = higher priority.
    # Declining trend adds +15 priority points (urgent fix).
    # Each habit key counted once — no duplicates possible.
    drill_candidates: dict = {}  # habit_key → priority_score

    for metric, trend_data in trends.items():
        habit_key = metric_to_habit.get(metric)
        if not habit_key or habit_key not in DRILL_LIBRARY:
            continue
        if habit_key in drill_candidates:
            continue  # Already scored — no duplicate entries

        priority = 100 - trend_data["avg"]  # lower score = higher priority
        if trend_data["status"] == "declining":
            priority += 15   # actively getting worse — push to top
        elif trend_data["status"] == "improving":
            priority -= 10   # already improving — deprioritise unless very low

        # Boost priority if this habit appeared in multiple sessions
        recurrence = habit_frequency.get(habit_key, {}).get("count", 0)
        priority += recurrence * 5  # each repeated session adds urgency

        # Don't show drills for metrics that are already strong (avg > 72)
        if trend_data["avg"] > 72 and trend_data["status"] != "declining":
            continue

        drill_candidates[habit_key] = round(priority, 1)

    # Also add any habits detected in sessions that aren't in the metric map
    for habit_key, hdata in habit_frequency.items():
        if habit_key in drill_candidates:
            continue
        if habit_key not in DRILL_LIBRARY:
            continue
        # Recurring detected habit not covered by metric map — add it
        priority = 40 + hdata["count"] * 8
        drill_candidates[habit_key] = priority

    # Sort by priority descending
    sorted_habits = sorted(drill_candidates.items(), key=lambda x: x[1], reverse=True)

    # ── Step 6: Build drill plan — pick top habits, 2 drills each, no repeats ─
    # Rule: max 2 drills per habit bucket; max 8 total drills per plan.
    # Drills within each bucket are rotated based on session count so the
    # same player sees different drills each week, not the same ones forever.
    total_sessions = len(sessions)
    drill_plan     = []
    seen_drill_ids = set()
    MAX_DRILLS     = 8
    MAX_PER_BUCKET = 2

    for habit_key, priority in sorted_habits:
        if len(drill_plan) >= MAX_DRILLS:
            break
        bucket = DRILL_LIBRARY.get(habit_key, [])
        if not bucket:
            continue

        # Rotate starting index based on session count so drills cycle over time
        start_idx  = total_sessions % len(bucket)
        rotated    = bucket[start_idx:] + bucket[:start_idx]
        added_from_bucket = 0

        for drill in rotated:
            if added_from_bucket >= MAX_PER_BUCKET:
                break
            if len(drill_plan) >= MAX_DRILLS:
                break
            if drill["id"] not in seen_drill_ids:
                seen_drill_ids.add(drill["id"])
                drill_plan.append({
                    **drill,
                    "habit_key":      habit_key,
                    "priority_score": priority,
                    "recurring":      habit_frequency.get(habit_key, {}).get("count", 0) > 2,
                })
                added_from_bucket += 1

    # ── Step 7: Summary ───────────────────────────────────────────────────────
    declining = [k for k, v in trends.items() if v["status"] == "declining"]
    improving = [k for k, v in trends.items() if v["status"] == "improving"]
    summary_parts = []
    if improving:
        improving_labels = {"reaction":"target acquisition","accuracy":"spray control",
                            "eng_eff":"tracking","consistency":"consistency",
                            "cqe":"flick precision","lre":"long-range","dpi":"crosshair placement"}
        names = [improving_labels.get(k, k) for k in improving]
        summary_parts.append(f"Your {', '.join(names)} {'are' if len(names) > 1 else 'is'} trending up — keep the routine.")
    if declining:
        declining_labels = {"reaction":"target acquisition speed","accuracy":"spray/recoil control",
                            "eng_eff":"tracking accuracy","consistency":"session consistency",
                            "cqe":"flick precision","lre":"long-range efficiency","dpi":"crosshair placement"}
        names = [declining_labels.get(k, k) for k in declining]
        summary_parts.append(f"Actively declining: {', '.join(names)}. These are your priority drills this week.")
    if not summary_parts:
        summary_parts.append("Your metrics are stable across sessions. Keep your warmup routine and push for rank.")

    role = _suggest_role(sessions)

    return {
        "summary":          " ".join(summary_parts),
        "trends":           trends,
        "drill_priorities": {k: v for k, v in sorted_habits},
        "drills":           drill_plan,
        "suggested_role":   role,
        "session_count":    total_sessions,
        "genre":            _genre_cat,
    }


@router.get("/coaching/drills/{habit_key}", summary="Get drills for a specific habit")
def get_drills(habit_key: str):
    drills = DRILL_LIBRARY.get(habit_key)
    if not drills:
        raise HTTPException(status_code=404, detail="No drills found for that habit key")
    return {"habit_key": habit_key, "drills": drills}


# =============================================================================
# Coach Profile Routes
# =============================================================================

class CoachApplyRequest(BaseModel):
    display_name:   str
    bio:            str
    specialty_role: str
    rate_per_hour:  float

@router.post("/coaches/apply", summary="Apply to become a coach (Diamond+ rank required)")
def apply_as_coach(
    body: CoachApplyRequest,
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    # Check rank
    player_rank = db.query(PlayerRank).filter(PlayerRank.user_id == user.id).first()
    if not player_rank or player_rank.rank_tier < MIN_RANK_FOR_COACH:
        needed = RANKS[MIN_RANK_FOR_COACH]["label"]
        current = player_rank.rank_label if player_rank else "Unranked"
        raise HTTPException(
            status_code=403,
            detail=f"You must be {needed} rank or above to apply as a coach. Your current rank: {current}."
        )

    existing = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()
    if existing:
        raise HTTPException(status_code=409, detail="You already have a coach profile.")

    if body.specialty_role not in FPS_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {', '.join(FPS_ROLES.keys())}")

    coach = CoachProfile(
        user_id       = user.id,
        display_name  = body.display_name,
        bio           = body.bio,
        specialty_role= body.specialty_role,
        rank_label    = player_rank.rank_label,
        rate_per_hour = body.rate_per_hour,
        is_active     = False,  # Requires admin approval
    )
    db.add(coach)
    db.commit()
    db.refresh(coach)

    return {
        "message": "Application submitted. An admin will review your profile within 24 hours.",
        "coach_id": coach.id,
        "status":   "pending_approval",
    }


@router.get("/coaches", summary="Browse all active coaches")
def list_coaches(
    role:   Optional[str] = None,
    rank:   Optional[str] = None,
    limit:  int           = 20,
    offset: int           = 0,
    db:     Session       = Depends(get_db),
):
    q = db.query(CoachProfile).filter(CoachProfile.is_active == True)
    if role:
        q = q.filter(CoachProfile.specialty_role == role)
    coaches = q.order_by(CoachProfile.avg_rating.desc().nullslast()).offset(offset).limit(limit).all()

    return {
        "coaches": [
            {
                "id":             c.id,
                "display_name":   c.display_name,
                "bio":            c.bio,
                "specialty_role": c.specialty_role,
                "role_name":      FPS_ROLES.get(c.specialty_role, {}).get("name", c.specialty_role),
                "rank":           c.rank_label,
                "rate_per_hour":  c.rate_per_hour,
                "avg_rating":     c.avg_rating,
                "total_sessions": c.total_sessions,
                "is_available":   c.is_available,
            }
            for c in coaches
        ]
    }


@router.get("/coaches/{coach_id}", summary="Get a single coach profile")
def get_coach(coach_id: int, db: Session = Depends(get_db)):
    coach = db.query(CoachProfile).filter(
        CoachProfile.id == coach_id,
        CoachProfile.is_active == True
    ).first()
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found")

    role_info = FPS_ROLES.get(coach.specialty_role, {})
    return {
        "id":             coach.id,
        "display_name":   coach.display_name,
        "bio":            coach.bio,
        "specialty_role": coach.specialty_role,
        "role_name":      role_info.get("name", coach.specialty_role),
        "role_description": role_info.get("description"),
        "role_drills":    role_info.get("drills", []),
        "rank":           coach.rank_label,
        "rate_per_hour":  coach.rate_per_hour,
        "avg_rating":     coach.avg_rating,
        "total_sessions": coach.total_sessions,
        "is_available":   coach.is_available,
    }


# =============================================================================
# Booking Routes
# =============================================================================

class BookingRequest(BaseModel):
    coach_id:     int
    message:      str
    scheduled_at: Optional[int] = None  # unix timestamp

@router.post("/bookings", summary="Request a coaching session")
def request_booking(
    body: BookingRequest,
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    coach = db.query(CoachProfile).filter(
        CoachProfile.id       == body.coach_id,
        CoachProfile.is_active== True,
    ).first()
    if not coach:
        raise HTTPException(status_code=404, detail="Coach not found or not available")
    if not coach.is_available:
        raise HTTPException(status_code=409, detail="This coach is currently not taking new bookings")
    if coach.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot book yourself")

    booking = CoachBooking(
        player_id    = user.id,
        coach_id     = coach.id,
        status       = "pending",
        message      = body.message,
        scheduled_at = body.scheduled_at,
        rate_agreed  = coach.rate_per_hour,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    return {
        "booking_id": booking.id,
        "status":     "pending",
        "message":    "Booking request sent. The coach will respond within 24 hours.",
        "coach":      coach.display_name,
    }


@router.get("/bookings/me", summary="Get all bookings for current user (as player or coach)")
def get_my_bookings(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    # Bookings as player
    as_player = db.query(CoachBooking).filter(CoachBooking.player_id == user.id).all()

    # Bookings as coach (if they have a coach profile)
    coach_profile = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()
    as_coach = []
    if coach_profile:
        as_coach = db.query(CoachBooking).filter(CoachBooking.coach_id == coach_profile.id).all()

    def _fmt(b, role):
        return {
            "id":           b.id,
            "role":         role,
            "status":       b.status,
            "scheduled_at": b.scheduled_at,
            "rate":         b.rate_agreed,
            "created_at":   b.created_at,
        }

    return {
        "as_player": [_fmt(b, "player") for b in as_player],
        "as_coach":  [_fmt(b, "coach")  for b in as_coach],
    }


class BookingActionRequest(BaseModel):
    action: str  # accept / decline / complete / cancel
    notes:  Optional[str] = None

@router.patch("/bookings/{booking_id}", summary="Accept, decline, complete, or cancel a booking")
def update_booking(
    booking_id: int,
    body:       BookingActionRequest,
    user:       User    = Depends(get_current_user),
    db:         Session = Depends(get_db),
):
    booking = db.query(CoachBooking).filter(CoachBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    coach_profile = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()
    is_coach  = coach_profile and booking.coach_id == coach_profile.id
    is_player = booking.player_id == user.id

    if not is_coach and not is_player:
        raise HTTPException(status_code=403, detail="Not your booking")

    valid_transitions = {
        "accept":   ("pending",   "accepted",  is_coach),
        "decline":  ("pending",   "declined",  is_coach),
        "complete": ("accepted",  "completed", is_coach),
        "cancel":   ("pending",   "cancelled", is_player or is_coach),
    }

    if body.action not in valid_transitions:
        raise HTTPException(status_code=400, detail=f"Invalid action. Choose: {', '.join(valid_transitions)}")

    required_status, new_status, allowed = valid_transitions[body.action]
    if not allowed:
        raise HTTPException(status_code=403, detail="You are not allowed to perform this action")
    if booking.status != required_status:
        raise HTTPException(status_code=409, detail=f"Booking must be '{required_status}' to perform this action")

    booking.status     = new_status
    booking.updated_at = _now()
    if body.notes:
        booking.coach_notes = body.notes
    if new_status == "completed" and coach_profile:
        coach_profile.total_sessions += 1

    db.commit()
    return {"booking_id": booking.id, "status": new_status}


class ReviewRequest(BaseModel):
    rating: int   # 1-5
    review: str

@router.post("/bookings/{booking_id}/review", summary="Leave a review after a completed session")
def leave_review(
    booking_id: int,
    body:       ReviewRequest,
    user:       User    = Depends(get_current_user),
    db:         Session = Depends(get_db),
):
    if not 1 <= body.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    booking = db.query(CoachBooking).filter(
        CoachBooking.id        == booking_id,
        CoachBooking.player_id == user.id,
        CoachBooking.status    == "completed",
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Completed booking not found")

    booking.rating = body.rating
    booking.review = body.review

    # Update coach average rating
    coach = booking.coach
    all_ratings = db.query(CoachBooking.rating).filter(
        CoachBooking.coach_id == coach.id,
        CoachBooking.rating   != None,
    ).all()
    ratings = [r[0] for r in all_ratings] + [body.rating]
    coach.avg_rating = round(sum(ratings) / len(ratings), 2)

    db.commit()
    return {"message": "Review submitted", "rating": body.rating}


# =============================================================================
# Messaging Routes
# =============================================================================

class MessageRequest(BaseModel):
    content: str

@router.post("/bookings/{booking_id}/messages", summary="Send a message in a booking thread")
def send_message(
    booking_id: int,
    body:       MessageRequest,
    user:       User    = Depends(get_current_user),
    db:         Session = Depends(get_db),
):
    booking = db.query(CoachBooking).filter(CoachBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    coach_profile = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()
    is_coach  = coach_profile and booking.coach_id == coach_profile.id
    is_player = booking.player_id == user.id

    if not is_coach and not is_player:
        raise HTTPException(status_code=403, detail="You are not part of this booking")

    if booking.status in ("declined", "cancelled"):
        raise HTTPException(status_code=409, detail="Cannot message on a closed booking")

    msg = CoachMessage(
        booking_id = booking.id,
        sender_id  = user.id,
        content    = body.content,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    return {"message_id": msg.id, "sent_at": msg.sent_at}


@router.get("/bookings/{booking_id}/messages", summary="Get all messages in a booking thread")
def get_messages(
    booking_id: int,
    user:       User    = Depends(get_current_user),
    db:         Session = Depends(get_db),
):
    booking = db.query(CoachBooking).filter(CoachBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    coach_profile = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()
    is_coach  = coach_profile and booking.coach_id == coach_profile.id
    is_player = booking.player_id == user.id

    if not is_coach and not is_player:
        raise HTTPException(status_code=403, detail="Not your booking")

    messages = (
        db.query(CoachMessage)
        .filter(CoachMessage.booking_id == booking_id)
        .order_by(CoachMessage.sent_at.asc())
        .all()
    )

    # Mark unread messages as read
    for m in messages:
        if m.sender_id != user.id and not m.is_read:
            m.is_read = True
    db.commit()

    return {
        "booking_id": booking_id,
        "messages": [
            {
                "id":        m.id,
                "sender_id": m.sender_id,
                "content":   m.content,
                "is_read":   m.is_read,
                "sent_at":   m.sent_at,
                "is_mine":   m.sender_id == user.id,
            }
            for m in messages
        ]
    }


@router.get("/messages/unread", summary="Get unread message count across all bookings")
def unread_count(
    user: User    = Depends(get_current_user),
    db:   Session = Depends(get_db),
):
    coach_profile = db.query(CoachProfile).filter(CoachProfile.user_id == user.id).first()

    # Get all booking IDs the user is part of
    booking_ids = [b.id for b in db.query(CoachBooking).filter(CoachBooking.player_id == user.id).all()]
    if coach_profile:
        coach_bookings = [b.id for b in db.query(CoachBooking).filter(CoachBooking.coach_id == coach_profile.id).all()]
        booking_ids.extend(coach_bookings)

    if not booking_ids:
        return {"unread": 0}

    count = db.query(CoachMessage).filter(
        CoachMessage.booking_id.in_(booking_ids),
        CoachMessage.sender_id  != user.id,
        CoachMessage.is_read    == False,
    ).count()

    return {"unread": count}
