# =============================================================================
# app/leaderboard.py
# Leaderboard HTTP routes — Module 6
#
# READ FLOW:
#   GET /leaderboard
#     → check dirty flag
#     → if dirty: rebuild cache (one pass, all players), mark clean
#     → SELECT from leaderboard_cache ORDER BY rank OFFSET/LIMIT
#     → zero JOINs, zero math, pure key lookup
#
# TIER GATING:
#   Free  → top 50 rows, no personal rank/percentile shown
#   Pro   → full pagination, own rank shown, season archive access
#
# LEGAL:
#   Ranking is for entertainment. Scoring_accuracy disclaimer
#   is embedded in every response.
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.leaderboard_service import (
    get_current_season, read_leaderboard,
    is_leaderboard_dirty, rebuild_leaderboard,
)
from app.models import (
    LeaderboardCache, SeasonArchive, SkillProfile,
    Role, User, AppState, CertLevelEnum,
)
from app.scoring import percentile_to_badge

router = APIRouter()

_LEGAL_NOTICE = (
    "OSI scores are computed from submitted metadata only. "
    "Rankings are for entertainment purposes. Not a professional assessment of skill. "
    "The platform is not affiliated with any game developer or publisher."
)


# =============================================================================
# GET /leaderboard — main leaderboard
# =============================================================================

@router.get(
    "/",
    summary="Global seasonal leaderboard",
    description=(
        "Returns the current season's ranked player list.\n\n"
        "**Free**: top 50 players only.\n"
        "**Pro**: full pagination.\n\n"
        "Cache is rebuilt lazily on first read after any ranked session upload."
    ),
)
def get_leaderboard(
    page:         int  = 1,
    limit:        int  = 50,
    current_user: User = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    season = get_current_season(db)
    is_pro = current_user.tier == "pro"

    # Clamp limit
    limit = min(max(limit, 1), 100)

    data = read_leaderboard(db, season, page=page, limit=limit, is_pro=is_pro)
    db.commit()  # persist dirty-flag clear + profile rank writes

    response = {
        **data,
        "legal_notice": _LEGAL_NOTICE,
    }

    if not is_pro:
        response["tier_notice"] = (
            "Free tier shows the top 50 players. "
            "Upgrade to Pro to see your own rank and full leaderboard."
        )

    return response


# =============================================================================
# GET /leaderboard/me — own rank (Pro only)
# =============================================================================

@router.get(
    "/me",
    summary="Your current rank and percentile (Pro only)",
    description="Returns your position in the current season's leaderboard.",
)
def get_my_rank(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if current_user.tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error":       "PRO_REQUIRED",
                "message":     "Your rank is visible to Pro subscribers only.",
                "upgrade_url": "/payments/subscribe",
            },
        )

    season = get_current_season(db)

    # Force rebuild if dirty so rank is fresh
    if is_leaderboard_dirty(db):
        rebuild_leaderboard(db, season)
        db.commit()

    cache_row = (
        db.query(LeaderboardCache)
        .filter(
            LeaderboardCache.season  == season,
            LeaderboardCache.user_id == current_user.id,
        )
        .first()
    )

    if not cache_row:
        # Player hasn't submitted a ranked session this season
        profile = (
            db.query(SkillProfile)
            .filter(
                SkillProfile.user_id == current_user.id,
                SkillProfile.season  == season,
            )
            .first()
        )
        return {
            "ranked": False,
            "message": "No ranked sessions this season.",
            "ranked_sessions": profile.ranked_sessions if profile else 0,
            "legal_notice": _LEGAL_NOTICE,
        }

    # Total players on leaderboard for context
    total = db.query(LeaderboardCache).filter(LeaderboardCache.season == season).count()

    return {
        "ranked":          True,
        "rank":            cache_row.rank,
        "total_players":   total,
        "osi":             cache_row.osi,
        "percentile":      cache_row.percentile,
        "tier_badge":      cache_row.tier_badge,
        "top_role":        cache_row.top_role,
        "season":          season,
        "legal_notice":    _LEGAL_NOTICE,
    }


# =============================================================================
# GET /leaderboard/tiers — badge distribution
# =============================================================================

@router.get(
    "/tiers",
    summary="Tier badge distribution for the current season",
    description="Shows how many players hold each badge. Public endpoint.",
)
def get_tier_distribution(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    season = get_current_season(db)

    if is_leaderboard_dirty(db):
        rebuild_leaderboard(db, season)
        db.commit()

    rows = (
        db.query(LeaderboardCache.tier_badge)
        .filter(LeaderboardCache.season == season)
        .all()
    )

    counts = {"apex": 0, "elite": 0, "gold": 0, "silver": 0, "bronze": 0}
    for (badge,) in rows:
        if badge in counts:
            counts[badge] += 1

    total = sum(counts.values())

    return {
        "season":      season,
        "total":       total,
        "distribution": counts,
        "percentages": {
            badge: round(count / total * 100, 1) if total else 0.0
            for badge, count in counts.items()
        },
    }


# =============================================================================
# GET /leaderboard/season/{season_id} — archived season (Pro only)
# =============================================================================

@router.get(
    "/season/{season_id}",
    summary="View an archived season's final rankings (Pro only)",
)
def get_season_archive(
    season_id:    int,
    page:         int  = 1,
    limit:        int  = 50,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if current_user.tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error":       "PRO_REQUIRED",
                "message":     "Season archives are available to Pro subscribers only.",
                "upgrade_url": "/payments/subscribe",
            },
        )

    current_season = get_current_season(db)
    if season_id >= current_season:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Season {season_id} is not archived yet. Current season is {current_season}.",
        )

    limit  = min(max(limit, 1), 100)
    offset = (page - 1) * limit
    total  = db.query(SeasonArchive).filter(SeasonArchive.season == season_id).count()

    if total == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No archive data found for season {season_id}.",
        )

    archives = (
        db.query(SeasonArchive, User.username)
        .join(User, User.id == SeasonArchive.user_id)
        .filter(SeasonArchive.season == season_id)
        .order_by(SeasonArchive.final_rank)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "season":  season_id,
        "total":   total,
        "page":    page,
        "results": [
            {
                "rank":       arc.final_rank,
                "username":   username,
                "final_osi":  arc.final_osi,
                "percentile": arc.final_percentile,
                "badge":      percentile_to_badge(arc.final_percentile or 0),
                "roles":      arc.roles_json,
                "archived_at":arc.archived_at,
            }
            for arc, username in archives
        ],
        "legal_notice": _LEGAL_NOTICE,
    }


# =============================================================================
# GET /leaderboard/season/{season_id}/me — own archive result (Pro only)
# =============================================================================

@router.get(
    "/season/{season_id}/me",
    summary="Your result in an archived season (Pro only)",
)
def get_my_season_result(
    season_id:    int,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    if current_user.tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "PRO_REQUIRED", "upgrade_url": "/payments/subscribe"},
        )

    arc = (
        db.query(SeasonArchive)
        .filter(
            SeasonArchive.user_id == current_user.id,
            SeasonArchive.season  == season_id,
        )
        .first()
    )

    if not arc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No data for you in season {season_id}.",
        )

    return {
        "season":       season_id,
        "final_osi":    arc.final_osi,
        "final_rank":   arc.final_rank,
        "percentile":   arc.final_percentile,
        "badge":        percentile_to_badge(arc.final_percentile or 0),
        "roles":        arc.roles_json,
        "archived_at":  arc.archived_at,
        "legal_notice": _LEGAL_NOTICE,
    }
