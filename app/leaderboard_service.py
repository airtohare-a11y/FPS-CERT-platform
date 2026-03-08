# =============================================================================
# app/leaderboard_service.py
# Leaderboard cache management and season lifecycle.
#
# WHY A CACHE?
#   Sorting 50,000 users by OSI on every leaderboard read would be
#   50,000 row scans + a filesort. With the dirty-flag pattern:
#     WRITE path: upload completes → SET leaderboard_dirty = '1' (1 UPDATE)
#     READ  path: IF dirty → rebuild once → serve from pre-sorted cache rows
#     READ  path: IF clean → SELECT ranked rows by offset (pure key lookup)
#
#   Cache rows are denormalised (username stored inline) so reads need
#   zero JOINs. The entire top-100 fits in a single sequential scan.
#
# REBUILD TRIGGER:
#   Any ranked session upload sets dirty = '1'.
#   The next leaderboard read triggers a single rebuild, then clears the flag.
#   No background worker. No cron. No Redis. Pure SQLite.
#
# SEASON LIFECYCLE:
#   Season close is a 5-step atomic transaction:
#     1. Snapshot skill_profiles → season_archive (immutable)
#     2. Check Elite role eligibility (top 5% two consecutive seasons)
#     3. Reset all skill_profiles for new season
#     4. Increment current_season in app_state
#     5. Clear leaderboard_cache for old season, set dirty = '0'
# =============================================================================

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models import (
    AppState, DerivedSession, LeaderboardCache,
    Role, SeasonArchive, SkillProfile, User,
    CertLevelEnum, RoleSlugEnum, MaintenanceEvent,
)
from app.scoring import compute_percentile, percentile_to_badge


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# =============================================================================
# Dirty flag helpers
# =============================================================================

def is_leaderboard_dirty(db: Session) -> bool:
    row = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()
    return row is not None and row.value == "1"


def mark_clean(db: Session) -> None:
    row = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()
    if row:
        row.value = "0"


def get_current_season(db: Session) -> int:
    row = db.query(AppState).filter(AppState.key == "current_season").first()
    return int(row.value) if row else 1


# =============================================================================
# Leaderboard cache rebuild
# =============================================================================

def rebuild_leaderboard(db: Session, season: Optional[int] = None) -> int:
    """
    Rebuild the leaderboard_cache for the given season (defaults to current).

    Steps:
      1. Load all SkillProfiles for the season (at least 1 ranked session)
      2. Sort by OSI descending → assign rank (1-indexed)
      3. Compute percentile for each player against all OSI scores
      4. Assign tier_badge from percentile
      5. Find top_role for each user (highest cert_level this season)
      6. DELETE old cache rows for this season
      7. INSERT new rows (bulk)
      8. Back-write current_rank + current_percentile onto each SkillProfile
      9. Clear dirty flag

    Returns the number of players ranked.
    """
    if season is None:
        season = get_current_season(db)

    # ── 1. Load all ranked profiles ───────────────────────────────────────────
    profiles = (
        db.query(SkillProfile, User.username)
        .join(User, User.id == SkillProfile.user_id)
        .filter(
            SkillProfile.season          == season,
            SkillProfile.ranked_sessions >= 1,
        )
        .all()
    )

    if not profiles:
        mark_clean(db)
        return 0

    # ── 2. Sort by OSI descending ─────────────────────────────────────────────
    profiles_sorted = sorted(profiles, key=lambda p: p[0].osi, reverse=True)
    all_osi = [p[0].osi for p in profiles_sorted]

    # ── 3–5. Compute rank / percentile / badge / top_role ────────────────────
    # Load all roles for this season in one query, then group by user_id
    roles_by_user: dict = {}
    all_roles = (
        db.query(Role)
        .filter(Role.season == season, Role.is_active == True)
        .all()
    )
    for r in all_roles:
        uid = r.user_id
        existing = roles_by_user.get(uid)
        # Keep the role with the highest cert_level
        level_order = {
            CertLevelEnum.elite:     3,
            CertLevelEnum.certified: 2,
            CertLevelEnum.candidate: 1,
        }
        if (existing is None or
                level_order.get(r.cert_level, 0) > level_order.get(existing.cert_level, 0)):
            roles_by_user[uid] = r

    # ── 6. DELETE old cache rows ──────────────────────────────────────────────
    db.query(LeaderboardCache).filter(LeaderboardCache.season == season).delete()

    # ── 7. INSERT new rows ────────────────────────────────────────────────────
    new_rows = []
    now = _now()

    for rank_0based, (profile, username) in enumerate(profiles_sorted):
        rank       = rank_0based + 1
        percentile = compute_percentile(profile.osi, all_osi)
        badge      = percentile_to_badge(percentile)
        top_role_obj = roles_by_user.get(profile.user_id)
        top_role   = top_role_obj.role_slug if top_role_obj else None

        cache_row = LeaderboardCache(
            season=    season,
            rank=      rank,
            user_id=   profile.user_id,
            username=  username,
            osi=       round(profile.osi, 2),
            percentile=round(percentile, 2),
            tier_badge=badge,
            top_role=  str(top_role) if top_role else None,
            built_at=  now,
        )
        new_rows.append(cache_row)

        # ── 8. Back-write rank + percentile onto SkillProfile ─────────────────
        profile.current_rank       = rank
        profile.current_percentile = round(percentile, 2)

    db.add_all(new_rows)

    # ── 9. Clear dirty flag ───────────────────────────────────────────────────
    mark_clean(db)

    return len(new_rows)


# =============================================================================
# Leaderboard read (respects tier gating)
# =============================================================================

def read_leaderboard(
    db:          Session,
    season:      int,
    page:        int    = 1,
    limit:       int    = 50,
    is_pro:      bool   = False,
) -> dict:
    """
    Read leaderboard rows from cache, rebuilding first if dirty.

    Free users: top 50 only, no rank/percentile for themselves.
    Pro users:  full paginated access.
    """
    # Rebuild if dirty
    if is_leaderboard_dirty(db):
        rebuild_leaderboard(db, season)
        db.commit()

    # Free tier: cap at top 50
    if not is_pro:
        limit = min(limit, 50)
        page  = 1

    total = db.query(LeaderboardCache).filter(LeaderboardCache.season == season).count()

    offset = (page - 1) * limit
    rows   = (
        db.query(LeaderboardCache)
        .filter(LeaderboardCache.season == season)
        .order_by(LeaderboardCache.rank)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "season":      season,
        "total":       total,
        "page":        page,
        "limit":       limit,
        "leaderboard": [
            {
                "rank":       r.rank,
                "user_id":    r.user_id,
                "username":   r.username,
                "osi":        r.osi,
                "percentile": r.percentile,
                "tier_badge": r.tier_badge,
                "top_role":   r.top_role,
            }
            for r in rows
        ],
    }


# =============================================================================
# Season close — atomic 5-step transaction
# =============================================================================

def close_season(db: Session, admin_id: int, reason: str = "") -> dict:
    """
    Close the current season.

    This is IRREVERSIBLE. Requires explicit confirm=true from the caller.

    Steps (all in one transaction):
      1. Force a final leaderboard rebuild (so archive has final ranks)
      2. Snapshot all SkillProfiles → season_archive
      3. Check Elite role eligibility across seasons
      4. Reset all SkillProfiles for new season
      5. Increment current_season, clear dirty flag

    Returns a summary dict.
    """
    old_season = get_current_season(db)
    new_season = old_season + 1

    # ── Step 1: Final rebuild so archive captures final ranks ─────────────────
    ranked_count = rebuild_leaderboard(db, old_season)
    db.flush()

    # ── Step 2: Snapshot to season_archive ───────────────────────────────────
    profiles = (
        db.query(SkillProfile, User.username)
        .join(User, User.id == SkillProfile.user_id)
        .filter(SkillProfile.season == old_season)
        .all()
    )

    archived_count = 0
    for profile, username in profiles:
        # Gather roles for this user this season
        roles = (
            db.query(Role)
            .filter(Role.user_id == profile.user_id, Role.season == old_season)
            .all()
        )
        roles_data = [
            {"slug": str(r.role_slug), "cert_level": str(r.cert_level)}
            for r in roles
        ]

        archive_row = SeasonArchive(
            user_id=         profile.user_id,
            season=          old_season,
            final_osi=       profile.osi,
            final_rank=      profile.current_rank,
            final_percentile=profile.current_percentile,
            roles_json=      json.dumps(roles_data),
            archived_at=     _now(),
        )
        db.add(archive_row)
        archived_count += 1

    db.flush()

    # ── Step 3: Elite role check ──────────────────────────────────────────────
    # Elite = top 5% role_osi in BOTH previous season AND current season.
    # We use season_archive percentile as a proxy (role-specific percentile
    # is a Module 5 enhancement — base Elite check uses overall percentile).
    elite_promotions = _check_elite_eligibility(db, old_season)
    db.flush()

    # ── Step 4: Reset SkillProfiles for new season ────────────────────────────
    # Set ranked_sessions=0 and all scores to 0 for the new season.
    # We INSERT new blank rows — don't delete old ones (history preserved).
    reset_count = 0
    for profile, _ in profiles:
        new_profile = SkillProfile(
            user_id=        profile.user_id,
            season=         new_season,
            osi=            0.0,
            avg_reaction=   0.0,
            avg_accuracy=   0.0,
            avg_eng_eff=    0.0,
            avg_consistency=0.0,
            avg_cqe=        0.0,
            avg_lre=        0.0,
            avg_dpi=        0.0,
            peak_osi=       0.0,
            ranked_sessions=0,
            last_updated=   _now(),
        )
        db.add(new_profile)
        reset_count += 1

    # ── Step 5: Increment season, clear dirty flag ────────────────────────────
    season_row = db.query(AppState).filter(AppState.key == "current_season").first()
    if season_row:
        season_row.value = str(new_season)

    dirty_row = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()
    if dirty_row:
        dirty_row.value = "0"

    # Log maintenance event
    db.add(MaintenanceEvent(
        admin_id=admin_id,
        event_type="season_closed",
        description=(
            f"Season {old_season} → {new_season}. "
            f"Archived: {archived_count} players. "
            f"Elite promotions: {elite_promotions}. "
            f"Reason: {reason}"
        ),
        affected_count=archived_count,
        performed_at=_now(),
    ))

    return {
        "old_season":       old_season,
        "new_season":       new_season,
        "players_archived": archived_count,
        "players_ranked":   ranked_count,
        "elite_promotions": elite_promotions,
        "profiles_reset":   reset_count,
    }


def _check_elite_eligibility(db: Session, closing_season: int) -> int:
    """
    Promote Certified roles to Elite where the player was in the top 5%
    for BOTH the previous season and the closing season.

    Uses final_percentile from season_archive for the previous season
    and current_percentile from skill_profiles for the closing season.

    Returns count of promotions made.
    """
    if closing_season < 2:
        return 0  # need at least 2 seasons to compare

    prev_season = closing_season - 1
    promotions  = 0

    # Load closing season archive rows (just flushed above)
    closing_archives = (
        db.query(SeasonArchive)
        .filter(SeasonArchive.season == closing_season)
        .all()
    )

    for arc in closing_archives:
        if arc.final_percentile is None or arc.final_percentile < 95.0:
            continue  # not top 5% this season

        # Check previous season
        prev_arc = (
            db.query(SeasonArchive)
            .filter(
                SeasonArchive.user_id == arc.user_id,
                SeasonArchive.season  == prev_season,
            )
            .first()
        )
        if not prev_arc or (prev_arc.final_percentile or 0) < 95.0:
            continue  # not top 5% previous season either

        # Promote all Certified roles for this user in the closing season
        certified_roles = (
            db.query(Role)
            .filter(
                Role.user_id   == arc.user_id,
                Role.season    == closing_season,
                Role.cert_level == CertLevelEnum.certified,
                Role.is_active == True,
            )
            .all()
        )
        for role in certified_roles:
            role.cert_level = CertLevelEnum.elite
            promotions += 1

    return promotions
