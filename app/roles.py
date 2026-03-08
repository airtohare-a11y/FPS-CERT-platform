# =============================================================================
# app/roles.py
# Role certification read routes — Module 5 (read side)
#
# The role STATE MACHINE runs inline in sessions.py after every ranked
# upload. This file provides the read-only HTTP surface:
#
#   GET /roles/me              — own active certifications
#   GET /roles/eligible        — which roles the user can unlock next
#   GET /roles/{slug}          — detail for one role (what it means, thresholds)
#   GET /roles/leaderboard/{slug} — top players for a specific role
#
# GATING:
#   Role certifications are Pro-only. Free users get a clear upgrade prompt.
#   Role read endpoints are still accessible to free users but show 0 certs.
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import (
    Role, SkillProfile, User, LeaderboardCache,
    CertLevelEnum, RoleSlugEnum, AppState,
)
from app.scoring import ROLE_DEFINITIONS, compute_role_osi
from app.leaderboard_service import get_current_season, is_leaderboard_dirty, rebuild_leaderboard
from app.config import settings

router = APIRouter()


def _cert_order(level) -> int:
    return {CertLevelEnum.elite: 3, CertLevelEnum.certified: 2, CertLevelEnum.candidate: 1}.get(level, 0)


# =============================================================================
# GET /roles/me — own active certifications
# =============================================================================

@router.get(
    "/me",
    summary="Your role certifications for the current season",
    description=(
        "Returns all active role certifications for the authenticated user.\n\n"
        "**Free tier**: roles are never unlocked; this returns an empty list "
        "with an upgrade notice.\n\n"
        "**Pro tier**: returns all Candidate / Certified / Elite roles."
    ),
)
def get_my_roles(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    season = get_current_season(db)

    if current_user.tier != "pro":
        return {
            "roles":        [],
            "tier_notice":  (
                "Role certifications are available to Pro subscribers ($7/month). "
                "Upgrade to unlock Candidate, Certified, and Elite roles."
            ),
            "upgrade_url":  "/payments/subscribe",
        }

    roles = (
        db.query(Role)
        .filter(
            Role.user_id == current_user.id,
            Role.season  == season,
        )
        .order_by(Role.cert_level.desc(), Role.unlocked_at)
        .all()
    )

    profile = (
        db.query(SkillProfile)
        .filter(
            SkillProfile.user_id == current_user.id,
            SkillProfile.season  == season,
        )
        .first()
    )

    ranked_sessions = profile.ranked_sessions if profile else 0

    return {
        "season":          season,
        "ranked_sessions": ranked_sessions,
        "roles": [
            {
                "slug":         str(r.role_slug),
                "display":      ROLE_DEFINITIONS.get(str(r.role_slug), {}).get("display", r.role_slug),
                "cert_level":   str(r.cert_level),
                "is_active":    r.is_active,
                "unlocked_at":  r.unlocked_at,
                "last_activity":r.last_activity,
                "description":  ROLE_DEFINITIONS.get(str(r.role_slug), {}).get("description", ""),
            }
            for r in roles
        ],
        "cert_progress": _build_progress(ranked_sessions, roles),
    }


def _build_progress(ranked_sessions: int, roles: list) -> dict:
    """
    Show what thresholds remain before the next unlock.
    Returned in /roles/me so the frontend can show a progress bar.
    """
    has_any_candidate = any(
        r.cert_level in (CertLevelEnum.candidate, CertLevelEnum.certified, CertLevelEnum.elite)
        for r in roles
    )
    has_any_certified = any(
        r.cert_level in (CertLevelEnum.certified, CertLevelEnum.elite)
        for r in roles
    )

    return {
        "candidate_threshold":  settings.CANDIDATE_MIN_SESSIONS,
        "certified_threshold":  settings.CERTIFIED_MIN_SESSIONS,
        "ranked_sessions":      ranked_sessions,
        "candidate_unlocked":   has_any_candidate,
        "certified_unlocked":   has_any_certified,
        "sessions_to_candidate": max(0, settings.CANDIDATE_MIN_SESSIONS - ranked_sessions),
        "sessions_to_certified": max(0, settings.CERTIFIED_MIN_SESSIONS - ranked_sessions),
        "stability_requirement": f"std_dev ≤ {settings.CERTIFIED_STABILITY_STD} over last {settings.CERTIFIED_MIN_SESSIONS} sessions",
        "elite_note": "Elite requires top 5% performance in both the previous and current season.",
    }


# =============================================================================
# GET /roles/eligible — which roles can the user unlock next
# =============================================================================

@router.get(
    "/eligible",
    summary="Roles you are eligible to unlock",
    description="Shows which roles you qualify for based on your current session count.",
)
def get_eligible_roles(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if current_user.tier != "pro":
        return {
            "eligible": [],
            "tier_notice": "Role certifications require a Pro subscription.",
            "upgrade_url": "/payments/subscribe",
        }

    season = get_current_season(db)

    profile = (
        db.query(SkillProfile)
        .filter(
            SkillProfile.user_id == current_user.id,
            SkillProfile.season  == season,
        )
        .first()
    )

    ranked = profile.ranked_sessions if profile else 0

    existing_roles = {
        str(r.role_slug): r
        for r in db.query(Role).filter(
            Role.user_id == current_user.id,
            Role.season  == season,
        ).all()
    }

    eligible = []
    for slug, defn in ROLE_DEFINITIONS.items():
        existing = existing_roles.get(slug)
        current_level = str(existing.cert_level) if existing else "none"
        is_active     = existing.is_active if existing else False

        if not existing and ranked >= settings.CANDIDATE_MIN_SESSIONS:
            eligible.append({
                "slug":        slug,
                "display":     defn["display"],
                "description": defn["description"],
                "action":      "unlock_candidate",
                "note":        f"You have {ranked} ranked sessions (≥ {settings.CANDIDATE_MIN_SESSIONS} required).",
            })
        elif existing and existing.cert_level == CertLevelEnum.candidate and ranked >= settings.CERTIFIED_MIN_SESSIONS:
            eligible.append({
                "slug":        slug,
                "display":     defn["display"],
                "description": defn["description"],
                "action":      "check_certified",
                "note": (
                    f"You have {ranked} sessions. Certified requires "
                    f"std_dev ≤ {settings.CERTIFIED_STABILITY_STD} over last {settings.CERTIFIED_MIN_SESSIONS} sessions."
                ),
            })

    return {
        "season":          season,
        "ranked_sessions": ranked,
        "eligible":        eligible,
        "candidate_threshold":  settings.CANDIDATE_MIN_SESSIONS,
        "certified_threshold":  settings.CERTIFIED_MIN_SESSIONS,
    }


# =============================================================================
# GET /roles/{slug} — role definition and current user's status for it
# =============================================================================

@router.get(
    "/{slug}",
    summary="Role definition and your status for a specific role",
)
def get_role_detail(
    slug:         str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if slug not in ROLE_DEFINITIONS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown role '{slug}'. Valid roles: {', '.join(ROLE_DEFINITIONS.keys())}",
        )

    defn   = ROLE_DEFINITIONS[slug]
    season = get_current_season(db)

    # Current user's status for this role
    user_role = None
    if current_user.tier == "pro":
        user_role = (
            db.query(Role)
            .filter(
                Role.user_id   == current_user.id,
                Role.role_slug == RoleSlugEnum(slug),
                Role.season    == season,
            )
            .first()
        )

    # Top players with this role for display
    if is_leaderboard_dirty(db):
        rebuild_leaderboard(db, season)
        db.commit()

    top_in_role = (
        db.query(LeaderboardCache)
        .filter(
            LeaderboardCache.season   == season,
            LeaderboardCache.top_role == slug,
        )
        .order_by(LeaderboardCache.rank)
        .limit(10)
        .all()
    )

    return {
        "slug":        slug,
        "display":     defn["display"],
        "description": defn["description"],
        "primary_metric": defn["primary"],
        "primary_boost":  "×1.2 weight applied to primary metric in role OSI",
        "thresholds": {
            "candidate": f"{settings.CANDIDATE_MIN_SESSIONS} ranked sessions",
            "certified": (
                f"{settings.CERTIFIED_MIN_SESSIONS} ranked sessions "
                f"AND std_dev ≤ {settings.CERTIFIED_STABILITY_STD} over last {settings.CERTIFIED_MIN_SESSIONS}"
            ),
            "elite":     "Top 5% role score for 2 consecutive seasons",
        },
        "inactivity_days": settings.ROLE_INACTIVITY_DAYS,
        "user_status": {
            "cert_level":    str(user_role.cert_level) if user_role else "none",
            "is_active":     user_role.is_active if user_role else False,
            "unlocked_at":   user_role.unlocked_at if user_role else None,
            "last_activity": user_role.last_activity if user_role else None,
        } if current_user.tier == "pro" else {
            "cert_level": "none",
            "note": "Role certifications require a Pro subscription.",
        },
        "top_players": [
            {
                "rank":       r.rank,
                "username":   r.username,
                "osi":        r.osi,
                "tier_badge": r.tier_badge,
            }
            for r in top_in_role
        ],
    }
