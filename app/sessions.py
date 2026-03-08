# =============================================================================
# app/sessions.py
# Metadata Upload Pipeline — Modules 2 & 3
#
# This is the core loop of the entire platform.
# Every ranked session upload flows through this sequence:
#
#   POST /sessions
#     1. Auth + tier quota gate       (Free: max 1 lifetime upload)
#     2. JSON schema validation        (Pydantic — rejects malformed payloads)
#     3. Sanity checks                 (physics-based plausibility — scoring.py)
#     4. Metric extraction             (7 normalised values — scoring.py)
#     ── RAW JSON DISCARDED HERE ──────────────────────────────────────────────
#     5. OSI computation               (weighted sum — scoring.py)
#     6. DB write: DerivedSession      (metrics + osi_session stored permanently)
#     7. DB write: SkillProfile UPSERT (rolling averages updated O(1))
#     8. DB write: User counters       (total_uploads + 1)
#     9. Role eligibility check        (inline — 2-3 queries max)
#    10. Mark leaderboard dirty        (triggers rebuild on next read)
#    11. Respond                       (session OSI + updated profile summary)
#
#   GET /sessions      — paginated session history (full history: Pro only)
#   GET /sessions/{id} — single session metrics
#   DELETE /sessions/{id} — mark non-ranked, recalculate rolling avg
# =============================================================================

import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_pro
from app.config import settings
from app.database import get_db
from app.models import (
    AppLog, AppState, DerivedSession, Role,
    SkillProfile, User, UserConsentLog,
    CertLevelEnum, RoleSlugEnum,
)
from app.scoring import (
    ExtractedMetrics, RawSegment,
    compute_osi, compute_role_osi,
    extract_metrics, percentile_to_badge,
    sanity_check, stability_check,
    update_rolling_profile, ROLE_DEFINITIONS,
)

router = APIRouter()


# =============================================================================
# Helpers
# =============================================================================

def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _get_current_season(db: Session) -> int:
    row = db.query(AppState).filter(AppState.key == "current_season").first()
    return int(row.value) if row else settings.CURRENT_SEASON


def _mark_leaderboard_dirty(db: Session) -> None:
    row = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()
    if row:
        row.value = "1"
    else:
        db.add(AppState(key="leaderboard_dirty", value="1"))


def _log_app(db: Session, level: str, source: str, message: str,
             detail: Optional[dict] = None, user_id: Optional[int] = None) -> None:
    db.add(AppLog(
        level=level,
        source=source,
        message=message,
        detail_json=json.dumps(detail) if detail else None,
        user_id=user_id,
        occurred_at=_now(),
    ))


# =============================================================================
# Request / Response Schemas
# =============================================================================

class SessionUploadRequest(BaseModel):
    """
    The complete JSON payload for one 60-second gameplay segment.
    Clients submit this; the server processes it and discards the raw data.

    LEGAL: By submitting, the user consents to metadata processing.
    This consent is recorded in user_consent_log per upload.
    """
    # Timing
    duration_seconds:   float

    # Combat
    kills:              int
    deaths:             int
    assists:            int
    shots_fired:        int
    shots_hit:          int
    headshots:          int
    damage_dealt:       float
    damage_taken:       float

    # Rounds
    round_wins:         int
    round_total:        int
    per_round_scores:   List[float]

    # Reaction
    avg_reaction_ms:    float
    reaction_events:    int

    # Range breakdown
    close_engagements:  int
    close_kills:        int
    long_engagements:   int
    long_kills:         int

    # Optional metadata
    map_slug:   str = ""
    game_mode:  str = "ranked"

    # Consent token — client must send the current disclaimer version
    # This records per-upload consent in user_consent_log.
    disclaimer_version: str = "1.0.0"

    @field_validator("duration_seconds")
    @classmethod
    def duration_reasonable(cls, v):
        if v <= 0:
            raise ValueError("duration_seconds must be positive")
        return v

    @field_validator("shots_fired", "shots_hit", "headshots", "kills",
                     "deaths", "assists", "reaction_events", mode="before")
    @classmethod
    def non_negative_int(cls, v):
        if v < 0:
            raise ValueError("stat fields cannot be negative")
        return v

    @field_validator("damage_dealt", "damage_taken", "avg_reaction_ms", mode="before")
    @classmethod
    def non_negative_float(cls, v):
        if v < 0:
            raise ValueError("float stat fields cannot be negative")
        return v

    @model_validator(mode="after")
    def cross_field_basics(self):
        if self.shots_hit > self.shots_fired:
            raise ValueError("shots_hit cannot exceed shots_fired")
        if self.headshots > self.shots_hit:
            raise ValueError("headshots cannot exceed shots_hit")
        if self.close_kills > self.close_engagements:
            raise ValueError("close_kills cannot exceed close_engagements")
        if self.long_kills > self.long_engagements:
            raise ValueError("long_kills cannot exceed long_engagements")
        return self


class SessionResponse(BaseModel):
    """Returned immediately after a successful upload."""
    session_id:      int
    is_ranked:       bool
    sanity_reason:   Optional[str]   # populated if is_ranked=False

    # Computed metrics (returned even for non-ranked sessions)
    osi_session:     float
    metrics: dict

    # Updated rolling profile
    profile: dict

    # Role changes this upload triggered
    roles_updated:   List[dict]

    # Legal notice embedded in every session response
    legal_notice:    str


# =============================================================================
# Upload quota gate
# =============================================================================

def _check_upload_quota(user: User) -> None:
    """
    Enforce Free tier: 1 lifetime upload.
    Pro tier: unlimited.
    Raises HTTP 403 with upgrade prompt if quota exceeded.
    """
    if user.tier == "pro":
        return  # no limit

    if user.total_uploads >= settings.FREE_TIER_MAX_UPLOADS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error":       "QUOTA_EXCEEDED",
                "message":     f"Free tier allows {settings.FREE_TIER_MAX_UPLOADS} lifetime analysis.",
                "uploaded":    user.total_uploads,
                "upgrade_url": "/payments/subscribe",
                "legal_notice": (
                    "Upgrade to Pro ($7/month) for unlimited uploads, "
                    "role certifications, and full leaderboard access."
                ),
            },
        )


# =============================================================================
# Role eligibility check (inline — called after every ranked session)
# =============================================================================

def _check_role_eligibility(
    db:      Session,
    user:    User,
    season:  int,
    profile: SkillProfile,
    metrics: ExtractedMetrics,
) -> List[dict]:
    """
    Evaluate and update role certifications after a ranked session.

    Runs inline after the session is committed. Cost: 2–3 indexed
    SELECT queries + at most one UPSERT. Never runs on read paths.

    State machine:
      NONE       → CANDIDATE  : total_ranked_uploads ≥ 5  AND tier='pro'
      CANDIDATE  → CERTIFIED  : ranked_sessions ≥ 10
                                AND std_dev(last 10 OSI) ≤ 5.0
      CERTIFIED  → ELITE      : checked at season close (not here)
      ANY ACTIVE → INACTIVE   : (now - last_activity) > 60 days
                                (reactivated here on upload)

    Returns a list of role-change dicts for the response.
    """
    if user.tier != "pro":
        return []   # Free users are not eligible for roles

    ranked = profile.ranked_sessions
    changes = []
    now = _now()
    inactivity_cutoff = now - (settings.ROLE_INACTIVITY_DAYS * 86400)

    for slug in ROLE_DEFINITIONS:
        role_slug_enum = RoleSlugEnum(slug)

        existing = (
            db.query(Role)
            .filter(
                Role.user_id   == user.id,
                Role.role_slug == role_slug_enum,
                Role.season    == season,
            )
            .first()
        )

        # Reactivate inactive role on new upload
        if existing and not existing.is_active:
            existing.is_active     = True
            existing.last_activity = now
            changes.append({
                "role":   slug,
                "change": "reactivated",
                "level":  existing.cert_level,
            })
            continue

        # Update last_activity on all active roles
        if existing and existing.is_active:
            existing.last_activity = now

            # CANDIDATE → CERTIFIED check
            if existing.cert_level == CertLevelEnum.candidate and ranked >= settings.CERTIFIED_MIN_SESSIONS:
                last_10 = (
                    db.query(DerivedSession.osi_session)
                    .filter(
                        DerivedSession.user_id   == user.id,
                        DerivedSession.season    == season,
                        DerivedSession.is_ranked == True,
                    )
                    .order_by(DerivedSession.submitted_at.desc())
                    .limit(10)
                    .all()
                )
                osi_scores = [r.osi_session for r in last_10 if r.osi_session is not None]

                if stability_check(osi_scores):
                    existing.cert_level = CertLevelEnum.certified
                    changes.append({
                        "role":   slug,
                        "change": "promoted",
                        "level":  "certified",
                    })
            continue

        # NONE → CANDIDATE check
        if not existing and ranked >= settings.CANDIDATE_MIN_SESSIONS:
            new_role = Role(
                user_id=user.id,
                season=season,
                role_slug=role_slug_enum,
                cert_level=CertLevelEnum.candidate,
                unlocked_at=now,
                last_activity=now,
                is_active=True,
            )
            db.add(new_role)
            changes.append({
                "role":   slug,
                "change": "unlocked",
                "level":  "candidate",
            })

    # Deactivate roles that have been inactive too long
    active_roles = (
        db.query(Role)
        .filter(
            Role.user_id  == user.id,
            Role.season   == season,
            Role.is_active == True,
        )
        .all()
    )
    for r in active_roles:
        if r.last_activity < inactivity_cutoff:
            r.is_active = False
            changes.append({
                "role":   r.role_slug,
                "change": "deactivated",
                "level":  r.cert_level,
                "reason": "60 days without upload",
            })

    return changes


# =============================================================================
# POST /sessions  — the core upload pipeline
# =============================================================================

@router.post(
    "/",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a 60-second gameplay metadata segment",
    description=(
        "The core platform endpoint. Accepts JSON gameplay metadata, "
        "extracts 7 skill metrics, computes OSI, and updates your profile. "
        "Raw payload is discarded after processing — only derived metrics stored.\n\n"
        "**Free tier**: 1 lifetime upload. **Pro**: unlimited.\n\n"
        "LEGAL: By uploading you confirm this is your gameplay data and consent "
        "to its processing. See /legal/upload for full notice."
    ),
)
def upload_session(
    body:         SessionUploadRequest,
    request:      Request,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    # ── Step 1: Quota gate ────────────────────────────────────────────────────
    _check_upload_quota(current_user)

    # ── Step 2: Record per-upload consent ─────────────────────────────────────
    # LEGAL: Immutable consent log entry for this specific upload.
    db.add(UserConsentLog(
        user_id=current_user.id,
        consent_type="upload_metadata",
        disclaimer_version=body.disclaimer_version,
        accepted_at=_now(),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))

    # ── Step 3: Build RawSegment from request body ────────────────────────────
    seg = RawSegment(
        duration_seconds=  body.duration_seconds,
        kills=             body.kills,
        deaths=            body.deaths,
        assists=           body.assists,
        shots_fired=       body.shots_fired,
        shots_hit=         body.shots_hit,
        headshots=         body.headshots,
        damage_dealt=      body.damage_dealt,
        damage_taken=      body.damage_taken,
        round_wins=        body.round_wins,
        round_total=       body.round_total,
        per_round_scores=  body.per_round_scores,
        avg_reaction_ms=   body.avg_reaction_ms,
        reaction_events=   body.reaction_events,
        close_engagements= body.close_engagements,
        close_kills=       body.close_kills,
        long_engagements=  body.long_engagements,
        long_kills=        body.long_kills,
        map_slug=          body.map_slug,
        game_mode=         body.game_mode,
    )

    # ── Step 4: Sanity check ──────────────────────────────────────────────────
    sanity = sanity_check(seg)
    is_ranked = sanity.passed

    if not sanity.passed:
        _log_app(db, "warning", "upload",
                 f"Sanity check failed for user {current_user.id}: {sanity.reason}",
                 detail={"user_id": current_user.id, "reason": sanity.reason})

    # ── Step 5: Extract metrics ───────────────────────────────────────────────
    # This is the last point raw data is in memory.
    # After this line, `seg` is no longer referenced and will be GC'd.
    metrics = extract_metrics(seg)
    del seg  # explicit: raw segment discarded

    # ── Step 6: Compute OSI ───────────────────────────────────────────────────
    osi_session = compute_osi(metrics) if is_ranked else compute_osi(metrics)
    # Note: we compute OSI even for non-ranked sessions so the user can see
    # what their score would have been. It just doesn't affect their profile.

    # ── Step 7: Get current season ────────────────────────────────────────────
    season = _get_current_season(db)

    # ── Step 8: Write DerivedSession ──────────────────────────────────────────
    derived = DerivedSession(
        user_id=      current_user.id,
        season=       season,
        submitted_at= _now(),
        map_slug=     body.map_slug or None,
        game_mode=    body.game_mode,
        is_ranked=    is_ranked,
        m_reaction=   metrics.m_reaction,
        m_accuracy=   metrics.m_accuracy,
        m_eng_eff=    metrics.m_eng_eff,
        m_consistency=metrics.m_consistency,
        m_cqe=        metrics.m_cqe,
        m_lre=        metrics.m_lre,
        m_dpi=        metrics.m_dpi,
        osi_session=  osi_session,
    )
    db.add(derived)
    db.flush()  # get derived.id before profile update

    # ── Step 9: Update SkillProfile (rolling averages) ────────────────────────
    # Only ranked sessions update the rolling profile.
    profile = (
        db.query(SkillProfile)
        .filter(
            SkillProfile.user_id == current_user.id,
            SkillProfile.season  == season,
        )
        .first()
    )

    if profile is None:
        # First session this season — create profile row
        profile = SkillProfile(
            user_id=        current_user.id,
            season=         season,
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
        db.add(profile)
        db.flush()

    if is_ranked:
        old_metrics_dict = {
            "avg_reaction":    profile.avg_reaction,
            "avg_accuracy":    profile.avg_accuracy,
            "avg_eng_eff":     profile.avg_eng_eff,
            "avg_consistency": profile.avg_consistency,
            "avg_cqe":         profile.avg_cqe,
            "avg_lre":         profile.avg_lre,
            "avg_dpi":         profile.avg_dpi,
            "peak_osi":        profile.peak_osi,
        }

        updated = update_rolling_profile(
            old_osi=         profile.osi,
            old_metrics=     old_metrics_dict,
            ranked_sessions= profile.ranked_sessions,
            new_session_osi= osi_session,
            new_metrics=     metrics,
        )

        profile.osi             = updated["osi"]
        profile.avg_reaction    = updated["avg_reaction"]
        profile.avg_accuracy    = updated["avg_accuracy"]
        profile.avg_eng_eff     = updated["avg_eng_eff"]
        profile.avg_consistency = updated["avg_consistency"]
        profile.avg_cqe         = updated["avg_cqe"]
        profile.avg_lre         = updated["avg_lre"]
        profile.avg_dpi         = updated["avg_dpi"]
        profile.peak_osi        = updated["peak_osi"]
        profile.ranked_sessions = updated["ranked_sessions"]
        profile.last_updated    = _now()

    # ── Step 10: Increment user upload counter ────────────────────────────────
    current_user.total_uploads   = (current_user.total_uploads or 0) + 1
    current_user.last_upload_at  = _now() if hasattr(current_user, "last_upload_at") else None

    # ── Step 11: Role eligibility check ──────────────────────────────────────
    role_changes = []
    if is_ranked:
        role_changes = _check_role_eligibility(db, current_user, season, profile, metrics)

    # ── Step 12: Mark leaderboard dirty ──────────────────────────────────────
    if is_ranked:
        _mark_leaderboard_dirty(db)

    # ── Step 13: Commit everything ────────────────────────────────────────────
    db.commit()
    db.refresh(derived)
    db.refresh(profile)

    # ── Step 14: Build response ───────────────────────────────────────────────
    return SessionResponse(
        session_id=    derived.id,
        is_ranked=     is_ranked,
        sanity_reason= sanity.reason if not is_ranked else None,
        osi_session=   osi_session,
        metrics={
            "reaction":    metrics.m_reaction,
            "accuracy":    metrics.m_accuracy,
            "eng_eff":     metrics.m_eng_eff,
            "consistency": metrics.m_consistency,
            "cqe":         metrics.m_cqe,
            "lre":         metrics.m_lre,
            "dpi":         metrics.m_dpi,
        },
        profile={
            "osi":              profile.osi,
            "peak_osi":         profile.peak_osi,
            "ranked_sessions":  profile.ranked_sessions,
            "avg_reaction":     profile.avg_reaction,
            "avg_accuracy":     profile.avg_accuracy,
            "avg_eng_eff":      profile.avg_eng_eff,
            "avg_consistency":  profile.avg_consistency,
            "avg_cqe":          profile.avg_cqe,
            "avg_lre":          profile.avg_lre,
            "avg_dpi":          profile.avg_dpi,
        },
        roles_updated= role_changes,
        legal_notice=(
            "This platform is not affiliated with any game developer or publisher. "
            "Metadata processed for skill analysis only. Raw data discarded."
        ),
    )


# =============================================================================
# GET /sessions — paginated session history
# =============================================================================

@router.get(
    "/",
    summary="Get session history",
    description=(
        "**Pro users**: full paginated history.\n"
        "**Free users**: most recent session only."
    ),
)
def list_sessions(
    page:         int  = 1,
    limit:        int  = 20,
    ranked_only:  bool = False,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    query = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == current_user.id)
    )

    if ranked_only:
        query = query.filter(DerivedSession.is_ranked == True)

    query = query.order_by(DerivedSession.submitted_at.desc())

    # Free tier: last 1 session only
    if current_user.tier != "pro":
        sessions = query.limit(1).all()
        return {
            "sessions":     _format_sessions(sessions),
            "total":        1,
            "tier_notice":  "Free tier shows the most recent session only. Upgrade for full history.",
        }

    total   = query.count()
    offset  = (page - 1) * limit
    sessions = query.offset(offset).limit(min(limit, 50)).all()

    return {
        "sessions": _format_sessions(sessions),
        "total":    total,
        "page":     page,
    }


def _format_sessions(sessions: list) -> list:
    return [
        {
            "id":           s.id,
            "season":       s.season,
            "submitted_at": s.submitted_at,
            "map_slug":     s.map_slug,
            "game_mode":    s.game_mode,
            "is_ranked":    s.is_ranked,
            "osi_session":  s.osi_session,
            "metrics": {
                "reaction":    s.m_reaction,
                "accuracy":    s.m_accuracy,
                "eng_eff":     s.m_eng_eff,
                "consistency": s.m_consistency,
                "cqe":         s.m_cqe,
                "lre":         s.m_lre,
                "dpi":         s.m_dpi,
            },
        }
        for s in sessions
    ]


# =============================================================================
# GET /sessions/{session_id} — single session detail
# =============================================================================

@router.get(
    "/{session_id}",
    summary="Get a single session by ID",
)
def get_session(
    session_id:   int,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    session = (
        db.query(DerivedSession)
        .filter(
            DerivedSession.id      == session_id,
            DerivedSession.user_id == current_user.id,  # users can only see own sessions
        )
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return {
        "id":           session.id,
        "season":       session.season,
        "submitted_at": session.submitted_at,
        "map_slug":     session.map_slug,
        "game_mode":    session.game_mode,
        "is_ranked":    session.is_ranked,
        "osi_session":  session.osi_session,
        "metrics": {
            "reaction":    session.m_reaction,
            "accuracy":    session.m_accuracy,
            "eng_eff":     session.m_eng_eff,
            "consistency": session.m_consistency,
            "cqe":         session.m_cqe,
            "lre":         session.m_lre,
            "dpi":         session.m_dpi,
        },
    }


# =============================================================================
# DELETE /sessions/{session_id} — mark non-ranked
# =============================================================================

@router.delete(
    "/{session_id}",
    summary="Invalidate a session (marks non-ranked, recalculates rolling avg)",
    description=(
        "Marks the session as non-ranked. Does not delete — preserved for audit. "
        "Recalculates the rolling profile by replaying all remaining ranked sessions."
    ),
)
def invalidate_session(
    session_id:   int,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    session = (
        db.query(DerivedSession)
        .filter(
            DerivedSession.id      == session_id,
            DerivedSession.user_id == current_user.id,
        )
        .first()
    )

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.is_ranked:
        return {"message": "Session was already non-ranked.", "session_id": session_id}

    session.is_ranked = False

    # Recalculate rolling profile from all remaining ranked sessions
    season = session.season
    remaining = (
        db.query(DerivedSession)
        .filter(
            DerivedSession.user_id  == current_user.id,
            DerivedSession.season   == season,
            DerivedSession.is_ranked == True,
            DerivedSession.id       != session_id,
        )
        .order_by(DerivedSession.submitted_at.asc())
        .all()
    )

    # Replay rolling avg from scratch over remaining sessions
    osi          = 0.0
    avg_reaction = avg_accuracy = avg_eng_eff = 0.0
    avg_consistency = avg_cqe = avg_lre = avg_dpi = 0.0
    peak_osi     = 0.0
    count        = 0

    for s in remaining:
        if s.osi_session is None:
            continue
        decay = min(count, 30)
        def roll(old, new): return (old * decay + new) / (decay + 1)
        osi             = roll(osi,             s.osi_session)
        avg_reaction    = roll(avg_reaction,    s.m_reaction    or 0.0)
        avg_accuracy    = roll(avg_accuracy,    s.m_accuracy    or 0.0)
        avg_eng_eff     = roll(avg_eng_eff,     s.m_eng_eff     or 0.0)
        avg_consistency = roll(avg_consistency, s.m_consistency or 0.0)
        avg_cqe         = roll(avg_cqe,         s.m_cqe         or 0.0)
        avg_lre         = roll(avg_lre,         s.m_lre         or 0.0)
        avg_dpi         = roll(avg_dpi,         s.m_dpi         or 0.0)
        peak_osi        = max(peak_osi, s.osi_session)
        count += 1

    profile = (
        db.query(SkillProfile)
        .filter(SkillProfile.user_id == current_user.id,
                SkillProfile.season  == season)
        .first()
    )

    if profile:
        profile.osi             = round(osi, 2)
        profile.avg_reaction    = round(avg_reaction, 4)
        profile.avg_accuracy    = round(avg_accuracy, 4)
        profile.avg_eng_eff     = round(avg_eng_eff, 4)
        profile.avg_consistency = round(avg_consistency, 4)
        profile.avg_cqe         = round(avg_cqe, 4)
        profile.avg_lre         = round(avg_lre, 4)
        profile.avg_dpi         = round(avg_dpi, 4)
        profile.peak_osi        = round(peak_osi, 2)
        profile.ranked_sessions = count
        profile.last_updated    = _now()

    _mark_leaderboard_dirty(db)
    db.commit()

    return {
        "message":          "Session marked non-ranked. Profile recalculated.",
        "session_id":       session_id,
        "new_ranked_count": count,
        "new_osi":          round(osi, 2),
    }


# =============================================================================
# GET /sessions/me/skills — current skill profile summary
# =============================================================================

@router.get(
    "/me/skills",
    summary="Get your current skill profile",
    description="Rolling averages across all 7 metrics plus OSI and peak OSI.",
)
def get_skill_profile(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    season = _get_current_season(db)
    profile = (
        db.query(SkillProfile)
        .filter(
            SkillProfile.user_id == current_user.id,
            SkillProfile.season  == season,
        )
        .first()
    )

    if not profile:
        return {
            "message": "No sessions uploaded yet this season.",
            "osi":     0.0,
            "ranked_sessions": 0,
        }

    # Pro-only: full percentile breakdown
    percentile_data = None
    if current_user.tier == "pro" and profile.current_percentile is not None:
        percentile_data = {
            "percentile": profile.current_percentile,
            "rank":       profile.current_rank,
            "badge":      percentile_to_badge(profile.current_percentile),
        }

    return {
        "season":          season,
        "osi":             profile.osi,
        "peak_osi":        profile.peak_osi,
        "ranked_sessions": profile.ranked_sessions,
        "metrics": {
            "reaction":    profile.avg_reaction,
            "accuracy":    profile.avg_accuracy,
            "eng_eff":     profile.avg_eng_eff,
            "consistency": profile.avg_consistency,
            "cqe":         profile.avg_cqe,
            "lre":         profile.avg_lre,
            "dpi":         profile.avg_dpi,
        },
        "percentile": percentile_data,
        "last_updated": profile.last_updated,
        "legal_notice": (
            "OSI scores are estimates based on submitted metadata. "
            "Not professionally validated assessments of gaming ability."
        ),
    }
