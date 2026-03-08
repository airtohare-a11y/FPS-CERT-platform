# =============================================================================
# app/sponsors.py
# Public sponsor display endpoints.
#
# Sponsor content is queried live from the DB on every request.
# No caching is needed because sponsor records change rarely (weekly at most)
# and the query is fast (one indexed SELECT on a tiny table).
# This means admin changes to sponsors are live within milliseconds.
#
# LEGAL: Sponsor logos are ALWAYS external URLs. No images are stored locally.
#        See the sponsor_content disclaimer for display rules.
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Sponsor, LegalDisclaimer

router = APIRouter()


@router.get(
    "/active",
    summary="Get all active sponsors for the current season",
    description=(
        "Returns active sponsor entries. "
        "LEGAL: Sponsor content is independently managed. "
        "The platform does not endorse sponsor products or services."
    ),
)
def get_active_sponsors(
    slot: str = None,    # filter by slot: leaderboard_banner | profile_badge
    db:   Session = Depends(get_db),
):
    """
    Public endpoint — no auth required.
    Sponsors are visible to all visitors.

    Returns the legal disclaimer for sponsor content alongside results.
    Frontend must display this disclaimer near any sponsor logo.
    """
    query = db.query(Sponsor).filter(Sponsor.is_active == True)

    if slot:
        if slot not in ("leaderboard_banner", "profile_badge"):
            raise HTTPException(
                status_code=400,
                detail="slot must be 'leaderboard_banner' or 'profile_badge'",
            )
        query = query.filter(Sponsor.slot == slot)

    sponsors = query.order_by(Sponsor.priority.desc()).all()

    # Fetch the legal disclaimer for sponsor content
    disclaimer = db.query(LegalDisclaimer).filter(
        LegalDisclaimer.location_tag == "sponsors"
    ).first()

    return {
        "sponsors": [
            {
                "id":       s.id,
                "name":     s.name,
                "logo_url": s.logo_url,    # EXTERNAL URL — render with <img src="">
                "link_url": s.link_url,    # render as <a href="">
                "slot":     s.slot,
                # LEGAL: Frontend must display disclaimer near this logo
                "legal_notice": disclaimer.content if disclaimer else (
                    "Sponsor content is independently managed. "
                    "Not endorsed by this platform."
                ),
            }
            for s in sponsors
        ],
        "count": len(sponsors),
    }


@router.get(
    "/{slot}",
    summary="Get the highest-priority active sponsor for a display slot",
    description=(
        "Returns a single sponsor for placement in a specific slot. "
        "LEGAL: Frontend must display the returned legal_notice near the logo."
    ),
)
def get_sponsor_for_slot(
    slot:    str,
    db:      Session = Depends(get_db),
):
    """
    Returns the highest-priority active sponsor for the given slot.
    Returns null data if no sponsor is active for this slot.

    Used by frontend to render sponsor banners:
        <a href="{link_url}">
          <img src="{logo_url}" alt="{name}"/>
        </a>
        <p class="sponsor-disclaimer">{legal_notice}</p>
    """
    if slot not in ("leaderboard_banner", "profile_badge"):
        raise HTTPException(
            status_code=400,
            detail="slot must be 'leaderboard_banner' or 'profile_badge'",
        )

    sponsor = (
        db.query(Sponsor)
        .filter(
            Sponsor.slot      == slot,
            Sponsor.is_active == True,
        )
        .order_by(Sponsor.priority.desc())
        .first()
    )

    disclaimer = db.query(LegalDisclaimer).filter(
        LegalDisclaimer.location_tag == "sponsors"
    ).first()

    legal_notice = (
        disclaimer.content if disclaimer else
        "Sponsor content is independently managed. Not endorsed by this platform."
    )

    if not sponsor:
        return {
            "sponsor":     None,
            "legal_notice": legal_notice,
        }

    return {
        "sponsor": {
            "id":       sponsor.id,
            "name":     sponsor.name,
            "logo_url": sponsor.logo_url,
            "link_url": sponsor.link_url,
        },
        "legal_notice": legal_notice,
    }
