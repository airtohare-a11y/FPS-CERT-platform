# =============================================================================
# app/competition.py  — Fair, balanced genre-based monthly competitions
#
# RULES:
#   - One competition per genre running simultaneously
#   - Single best 90s clip per player is the competition entry
#   - Must have 3+ prior ranked sessions (history gate)
#   - Entry window: 1st–25th. Admin review 25th–31st. Winner announced 1st.
#   - Tiebreaker: consistency score across last 5 sessions
#   - Clips < 60s: 85% OSI penalty. Clips > 90s: capped at 90s.
#
# DISCLAIMER: MECHgg is not affiliated with any game developer, publisher,
# or esports organisation. All game names are used for identification only.
# =============================================================================

import calendar, math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text
from sqlalchemy.orm import Session, relationship

from app.auth import get_current_user
from app.database import Base, get_db
from app.models import User, DerivedSession, Sponsor
from app.admin import get_current_admin

router = APIRouter()
_now = lambda: int(datetime.now(timezone.utc).timestamp())

CLIP_FULL_SCORE_MIN_SECS = 60
CLIP_SHORT_PENALTY       = 0.85
CLIP_MAX_SECS            = 90
MIN_HISTORY_SESSIONS     = 3
ENTRY_DEADLINE_DAY       = 25
GENRE_LABELS = {
    "fps":      "🎯 FPS / Shooter",
    "racing":   "🏎️ Racing",
    "sports":   "⚽ Sports",
    "strategy": "⚔️ Strategy / MOBA",
    "fighting": "👊 Fighting",
}

# =============================================================================
# Models
# =============================================================================

class Competition(Base):
    __tablename__ = "competitions"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    title          = Column(String(100), nullable=False)
    genre          = Column(String(20),  nullable=False, index=True)
    prize_pool     = Column(Float,       nullable=False, default=0.0)
    status         = Column(String(20),  nullable=False, default="active")
    starts_at      = Column(Integer,     nullable=False, default=_now)
    entry_deadline = Column(Integer,     nullable=False)
    ends_at        = Column(Integer,     nullable=False)
    winner_user_id = Column(Integer,     ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    winner_osi     = Column(Float,       nullable=True)
    winner_consistency = Column(Float,   nullable=True)
    admin_notes    = Column(Text,        nullable=True)
    created_by     = Column(Integer,     nullable=True)
    created_at     = Column(Integer,     nullable=False, default=_now)
    winner   = relationship("User",               foreign_keys=[winner_user_id])
    entries  = relationship("CompetitionEntry",   back_populates="competition", cascade="all, delete-orphan")
    sponsors = relationship("CompetitionSponsor", back_populates="competition", cascade="all, delete-orphan")


class CompetitionEntry(Base):
    __tablename__ = "competition_entries"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    competition_id = Column(Integer, ForeignKey("competitions.id",      ondelete="CASCADE"), nullable=False, index=True)
    user_id        = Column(Integer, ForeignKey("users.id",             ondelete="CASCADE"), nullable=False, index=True)
    session_id     = Column(Integer, ForeignKey("derived_sessions.id",  ondelete="CASCADE"), nullable=False)
    raw_osi        = Column(Float,   nullable=False)
    entry_osi      = Column(Float,   nullable=False)
    consistency    = Column(Float,   nullable=True)
    clip_secs      = Column(Integer, nullable=True)
    duration_penalty_applied = Column(Boolean, nullable=False, default=False)
    qualified      = Column(Boolean, nullable=False, default=True)
    disqualified   = Column(Boolean, nullable=False, default=False)
    dq_reason      = Column(String(200), nullable=True)
    submitted_at   = Column(Integer, nullable=False, default=_now)
    competition = relationship("Competition",    back_populates="entries",  foreign_keys=[competition_id])
    user        = relationship("User",           foreign_keys=[user_id])
    session     = relationship("DerivedSession", foreign_keys=[session_id])


class CompetitionSponsor(Base):
    __tablename__ = "competition_sponsors"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    competition_id = Column(Integer, ForeignKey("competitions.id", ondelete="CASCADE"), nullable=False, index=True)
    sponsor_id     = Column(Integer, ForeignKey("sponsors.id",     ondelete="CASCADE"), nullable=False)
    contribution   = Column(Float,   nullable=False, default=0.0)
    approved       = Column(Boolean, nullable=False, default=True)
    created_at     = Column(Integer, nullable=False, default=_now)
    competition = relationship("Competition", back_populates="sponsors", foreign_keys=[competition_id])
    sponsor     = relationship("Sponsor",     foreign_keys=[sponsor_id])


# =============================================================================
# Helpers
# =============================================================================

def _end_of_month_ts():
    now = datetime.now(timezone.utc)
    last_day = calendar.monthrange(now.year, now.month)[1]
    return int(now.replace(day=last_day, hour=23, minute=59, second=59).timestamp())

def _entry_deadline_ts():
    now = datetime.now(timezone.utc)
    return int(now.replace(day=ENTRY_DEADLINE_DAY, hour=23, minute=59, second=59).timestamp())

def _days_remaining(ts):
    return max(0, int((ts - _now()) / 86400))

def _apply_duration_penalty(osi, clip_secs):
    if clip_secs is not None and clip_secs < CLIP_FULL_SCORE_MIN_SECS:
        return round(osi * CLIP_SHORT_PENALTY, 2), True
    return round(osi, 2), False

def _compute_consistency(user_id, db):
    sessions = (
        db.query(DerivedSession)
        .filter(DerivedSession.user_id == user_id, DerivedSession.is_ranked == True)
        .order_by(DerivedSession.submitted_at.desc())
        .limit(5).all()
    )
    osis = [s.osi_session for s in sessions if s.osi_session]
    if len(osis) < 2:
        return 50.0
    mean = sum(osis) / len(osis)
    if mean == 0:
        return 50.0
    std = math.sqrt(sum((x - mean) ** 2 for x in osis) / len(osis))
    return round(max(0.0, min(100.0, (1.0 - std/mean) * 100)), 1)

def _history_count(user_id, genre, db):
    return db.query(DerivedSession).filter(
        DerivedSession.user_id   == user_id,
        DerivedSession.game_mode == genre,
        DerivedSession.is_ranked == True,
    ).count()

def _is_entries_open(comp):
    return _now() <= comp.entry_deadline and comp.status == "active"

def _get_leaderboard(comp, db, limit=25):
    entries = (
        db.query(CompetitionEntry, User)
        .join(User, CompetitionEntry.user_id == User.id)
        .filter(
            CompetitionEntry.competition_id == comp.id,
            CompetitionEntry.qualified      == True,
            CompetitionEntry.disqualified   == False,
        ).all()
    )
    rows = [{
        "user_id":     u.id,
        "username":    u.username,
        "entry_osi":   e.entry_osi,
        "raw_osi":     e.raw_osi,
        "consistency": e.consistency or 50.0,
        "clip_secs":   e.clip_secs,
        "penalty":     e.duration_penalty_applied,
        "submitted_at":e.submitted_at,
    } for e, u in entries]
    rows.sort(key=lambda x: (x["entry_osi"], x["consistency"]), reverse=True)
    return rows[:limit]

def _format_comp(comp, db, include_lb=False):
    sponsors = [
        {"id": cs.sponsor.id, "name": cs.sponsor.name,
         "logo_url": cs.sponsor.logo_url, "link_url": cs.sponsor.link_url,
         "contribution": cs.contribution}
        for cs in comp.sponsors if cs.approved and cs.sponsor
    ]
    result = {
        "id":               comp.id,
        "title":            comp.title,
        "genre":            comp.genre,
        "genre_label":      GENRE_LABELS.get(comp.genre, comp.genre),
        "prize_pool":       comp.prize_pool,
        "status":           comp.status,
        "entries_open":     _is_entries_open(comp),
        "starts_at":        comp.starts_at,
        "entry_deadline":   comp.entry_deadline,
        "ends_at":          comp.ends_at,
        "days_to_deadline": _days_remaining(comp.entry_deadline),
        "days_to_end":      _days_remaining(comp.ends_at),
        "entry_count":      db.query(CompetitionEntry).filter(
            CompetitionEntry.competition_id == comp.id,
            CompetitionEntry.qualified == True,
            CompetitionEntry.disqualified == False,
        ).count(),
        "sponsors": sponsors,
        "rules": {
            "max_clip_secs":       CLIP_MAX_SECS,
            "short_clip_penalty":  f"{int(CLIP_SHORT_PENALTY*100)}% OSI if under {CLIP_FULL_SCORE_MIN_SECS}s",
            "history_required":    MIN_HISTORY_SESSIONS,
            "tiebreaker":          "Consistency score across last 5 sessions",
            "entry_deadline_day":  ENTRY_DEADLINE_DAY,
            "one_entry_per_player":"Your best 90-second clip only",
        },
        "winner": {
            "username":    comp.winner.username if comp.winner else None,
            "osi":         comp.winner_osi,
            "consistency": comp.winner_consistency,
        } if comp.status in ("closed", "archived") else None,
    }
    if include_lb:
        result["leaderboard"] = _get_leaderboard(comp, db)
    return result


# =============================================================================
# Public Routes
# =============================================================================

@router.get("/")
def list_competitions(genre: Optional[str]=None, db: Session=Depends(get_db)):
    q = db.query(Competition).filter(Competition.status.in_(["active","entries_closed"]))
    if genre:
        q = q.filter(Competition.genre == genre)
    comps = q.order_by(Competition.ends_at.asc()).all()
    return {"competitions": [_format_comp(c, db) for c in comps], "genres": GENRE_LABELS}

@router.get("/history")
def list_history(db: Session=Depends(get_db)):
    comps = db.query(Competition).filter(
        Competition.status.in_(["closed","archived"])
    ).order_by(Competition.ends_at.desc()).all()
    return {"competitions": [_format_comp(c, db) for c in comps]}

@router.get("/{competition_id}")
def get_competition(competition_id: int, db: Session=Depends(get_db)):
    comp = db.query(Competition).filter(Competition.id == competition_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    return _format_comp(comp, db, include_lb=True)

@router.get("/{competition_id}/my-entry")
def get_my_entry(competition_id: int, user: User=Depends(get_current_user), db: Session=Depends(get_db)):
    comp = db.query(Competition).filter(Competition.id == competition_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Not found")
    entry = db.query(CompetitionEntry).filter(
        CompetitionEntry.competition_id == competition_id,
        CompetitionEntry.user_id == user.id,
    ).first()
    h = _history_count(user.id, comp.genre, db)
    if not entry:
        return {"entered": False, "eligible": h >= MIN_HISTORY_SESSIONS,
                "history_count": h, "history_required": MIN_HISTORY_SESSIONS}
    return {"entered": True, "entry_osi": entry.entry_osi, "raw_osi": entry.raw_osi,
            "consistency": entry.consistency, "clip_secs": entry.clip_secs,
            "penalty_applied": entry.duration_penalty_applied,
            "qualified": entry.qualified, "submitted_at": entry.submitted_at}


class SubmitEntryRequest(BaseModel):
    competition_id: int
    session_id:     int
    clip_secs:      Optional[int] = None

@router.post("/enter")
def submit_entry(body: SubmitEntryRequest, user: User=Depends(get_current_user), db: Session=Depends(get_db)):
    comp = db.query(Competition).filter(Competition.id == body.competition_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    if not _is_entries_open(comp):
        raise HTTPException(status_code=409, detail="Entry deadline has passed.")
    session = db.query(DerivedSession).filter(
        DerivedSession.id == body.session_id, DerivedSession.user_id == user.id,
        DerivedSession.game_mode == comp.genre, DerivedSession.is_ranked == True,
    ).first()
    if not session:
        raise HTTPException(status_code=400, detail=f"Session not found or not a ranked {comp.genre} session.")

    h         = _history_count(user.id, comp.genre, db)
    qualified = h >= MIN_HISTORY_SESSIONS
    entry_osi, penalty = _apply_duration_penalty(session.osi_session or 0, body.clip_secs)
    consistency = _compute_consistency(user.id, db)

    existing = db.query(CompetitionEntry).filter(
        CompetitionEntry.competition_id == comp.id,
        CompetitionEntry.user_id == user.id,
    ).first()
    if existing:
        existing.session_id = session.id; existing.raw_osi = session.osi_session or 0
        existing.entry_osi  = entry_osi;  existing.consistency = consistency
        existing.clip_secs  = body.clip_secs; existing.duration_penalty_applied = penalty
        existing.qualified  = qualified; existing.submitted_at = _now()
    else:
        db.add(CompetitionEntry(
            competition_id=comp.id, user_id=user.id, session_id=session.id,
            raw_osi=session.osi_session or 0, entry_osi=entry_osi,
            consistency=consistency, clip_secs=body.clip_secs,
            duration_penalty_applied=penalty, qualified=qualified,
        ))
    db.commit()
    return {
        "message":       "Entry submitted" if not existing else "Entry updated",
        "entry_osi":     entry_osi, "raw_osi": session.osi_session,
        "penalty_applied": penalty, "consistency": consistency, "qualified": qualified,
        "warning": (f"Need {MIN_HISTORY_SESSIONS-h} more ranked {comp.genre} sessions before deadline.")
                    if not qualified else None,
    }


# =============================================================================
# Admin Routes
# =============================================================================

class CreateCompRequest(BaseModel):
    genre:          str
    title:          Optional[str] = None
    ends_at:        Optional[int] = None
    entry_deadline: Optional[int] = None

class AddSponsorRequest(BaseModel):
    sponsor_id:   int
    contribution: float

class CloseRequest(BaseModel):
    notes: Optional[str] = None

class DQRequest(BaseModel):
    user_id: int
    reason:  str

@router.post("/admin/create")
def admin_create(body: CreateCompRequest, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    if body.genre not in GENRE_LABELS:
        raise HTTPException(status_code=400, detail=f"Invalid genre. Choose: {', '.join(GENRE_LABELS)}")
    existing = db.query(Competition).filter(
        Competition.genre==body.genre, Competition.status.in_(["active","entries_closed"])
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Active {body.genre} competition already exists (ID {existing.id})")
    now  = datetime.now(timezone.utc)
    comp = Competition(
        title=body.title or f"{GENRE_LABELS[body.genre]} — {now.strftime('%B %Y')}",
        genre=body.genre, prize_pool=0.0, status="active", starts_at=_now(),
        entry_deadline=body.entry_deadline or _entry_deadline_ts(),
        ends_at=body.ends_at or _end_of_month_ts(), created_by=admin.id,
    )
    db.add(comp); db.commit(); db.refresh(comp)
    return {"message":"Competition created","competition_id":comp.id,"title":comp.title,"genre":comp.genre}

@router.post("/admin/create-all-genres")
def admin_create_all(db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    now = datetime.now(timezone.utc)
    month = now.strftime("%B %Y")
    created, skipped = [], []
    for genre, label in GENRE_LABELS.items():
        if db.query(Competition).filter(Competition.genre==genre, Competition.status.in_(["active","entries_closed"])).first():
            skipped.append(genre); continue
        db.add(Competition(title=f"{label} — {month}", genre=genre, prize_pool=0.0,
            status="active", starts_at=_now(), entry_deadline=_entry_deadline_ts(),
            ends_at=_end_of_month_ts(), created_by=admin.id))
        created.append(genre)
    db.commit()
    return {"message":f"Created {len(created)} competition(s)","created":created,"skipped":skipped}

@router.post("/admin/{competition_id}/close-entries")
def admin_close_entries(competition_id: int, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    comp = db.query(Competition).filter(Competition.id==competition_id).first()
    if not comp: raise HTTPException(status_code=404, detail="Not found")
    comp.status = "entries_closed"; db.commit()
    return {"message":"Entries closed"}

@router.post("/admin/{competition_id}/close")
def admin_close(competition_id: int, body: CloseRequest, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    comp = db.query(Competition).filter(Competition.id==competition_id).first()
    if not comp: raise HTTPException(status_code=404, detail="Not found")
    if comp.status not in ("active","entries_closed"):
        raise HTTPException(status_code=409, detail="Already closed")
    lb = _get_leaderboard(comp, db, limit=1)
    w  = lb[0] if lb else None
    comp.status             = "closed"
    comp.winner_user_id     = w["user_id"]     if w else None
    comp.winner_osi         = w["entry_osi"]   if w else None
    comp.winner_consistency = w["consistency"] if w else None
    comp.admin_notes        = body.notes
    db.commit()
    return {"message":"Closed","winner":w["username"] if w else None,"prize_pool":comp.prize_pool}

@router.post("/admin/{competition_id}/archive")
def admin_archive(competition_id: int, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    comp = db.query(Competition).filter(Competition.id==competition_id).first()
    if not comp: raise HTTPException(status_code=404, detail="Not found")
    comp.status = "archived"; db.commit()
    return {"message":"Archived"}

@router.post("/admin/{competition_id}/sponsor")
def admin_add_sponsor(competition_id: int, body: AddSponsorRequest, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    comp    = db.query(Competition).filter(Competition.id==competition_id).first()
    sponsor = db.query(Sponsor).filter(Sponsor.id==body.sponsor_id).first()
    if not comp:    raise HTTPException(status_code=404, detail="Competition not found")
    if not sponsor: raise HTTPException(status_code=404, detail="Sponsor not found")
    if db.query(CompetitionSponsor).filter(CompetitionSponsor.competition_id==competition_id, CompetitionSponsor.sponsor_id==body.sponsor_id).first():
        raise HTTPException(status_code=409, detail="Already added")
    db.add(CompetitionSponsor(competition_id=competition_id, sponsor_id=body.sponsor_id, contribution=body.contribution, approved=True))
    comp.prize_pool = (comp.prize_pool or 0.0) + body.contribution
    db.commit()
    return {"message":"Sponsor added","prize_pool":comp.prize_pool}

@router.delete("/admin/{competition_id}/sponsor/{sponsor_id}")
def admin_remove_sponsor(competition_id: int, sponsor_id: int, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    cs = db.query(CompetitionSponsor).filter(CompetitionSponsor.competition_id==competition_id, CompetitionSponsor.sponsor_id==sponsor_id).first()
    if not cs: raise HTTPException(status_code=404, detail="Not found")
    if cs.competition: cs.competition.prize_pool = max(0.0, (cs.competition.prize_pool or 0.0) - cs.contribution)
    db.delete(cs); db.commit()
    return {"message":"Removed"}

@router.post("/admin/{competition_id}/disqualify")
def admin_dq(competition_id: int, body: DQRequest, db: Session=Depends(get_db), admin=Depends(get_current_admin)):
    entry = db.query(CompetitionEntry).filter(CompetitionEntry.competition_id==competition_id, CompetitionEntry.user_id==body.user_id).first()
    if not entry: raise HTTPException(status_code=404, detail="Entry not found")
    entry.disqualified = True; entry.dq_reason = body.reason; db.commit()
    return {"message":"Disqualified"}
