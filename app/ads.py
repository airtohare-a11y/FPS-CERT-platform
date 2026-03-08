# =============================================================================
# app/ads.py
# Advertisement serving and admin management.
#
# Slots:
#   landing_top        — full-width banner at top of landing page
#   landing_sidebar    — sidebar on landing page
#   between_content_1  — between features and stats sections
#   between_content_2  — second between-content position
#   dashboard_sidebar  — sidebar on dashboard
#   analysis_sidebar   — sidebar on analysis result page
#
# Admin creates ads after payment is confirmed.
# Public endpoint serves active ads by slot — no auth required.
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Advertisement, AD_SLOTS
from app.admin import get_current_admin

router = APIRouter()
_now = lambda: int(datetime.now(timezone.utc).timestamp())


# =============================================================================
# Public — serve ads by slot
# =============================================================================

@router.get("/slot/{slot_name}", summary="Get active ad for a slot")
def get_ad_for_slot(slot_name: str, db: Session = Depends(get_db)):
    """Returns the active ad for a given slot, or null if none."""
    now = _now()
    ad = db.query(Advertisement).filter(
        Advertisement.slot_name == slot_name,
        Advertisement.active    == True,
        Advertisement.starts_at <= now,
    ).filter(
        (Advertisement.ends_at == None) | (Advertisement.ends_at >= now)
    ).order_by(Advertisement.created_at.desc()).first()

    if not ad:
        return {"ad": None, "slot": slot_name}

    return {
        "ad": {
            "id":           ad.id,
            "company_name": ad.company_name,
            "logo_url":     ad.logo_url,
            "image_url":    ad.image_url,
            "link_url":     ad.link_url,
            "alt_text":     ad.alt_text or f"Advertisement by {ad.company_name}",
        },
        "slot": slot_name,
    }


@router.get("/slots", summary="Get all available ad slots with rates")
def get_slots():
    return {"slots": [
        {"id": k, "label": v["label"], "rate_per_month": v["rate_month"]}
        for k, v in AD_SLOTS.items()
    ]}


@router.get("/active", summary="Get all active ads grouped by slot")
def get_all_active(db: Session = Depends(get_db)):
    now = _now()
    ads = db.query(Advertisement).filter(
        Advertisement.active    == True,
        Advertisement.starts_at <= now,
    ).filter(
        (Advertisement.ends_at == None) | (Advertisement.ends_at >= now)
    ).all()

    grouped = {}
    for ad in ads:
        if ad.slot_name not in grouped:
            grouped[ad.slot_name] = []
        grouped[ad.slot_name].append({
            "id":           ad.id,
            "company_name": ad.company_name,
            "logo_url":     ad.logo_url,
            "image_url":    ad.image_url,
            "link_url":     ad.link_url,
            "alt_text":     ad.alt_text,
        })

    return {"ads": grouped}


# =============================================================================
# Admin — create and manage ads
# =============================================================================

class CreateAdRequest(BaseModel):
    slot_name:    str
    company_name: str
    logo_url:     Optional[str] = None
    image_url:    Optional[str] = None
    link_url:     Optional[str] = None
    alt_text:     Optional[str] = None
    ends_at:      Optional[int] = None  # unix timestamp, None = no expiry


@router.post("/admin/create", summary="Admin: Create an ad placement")
def admin_create_ad(
    body:  CreateAdRequest,
    db:    Session = Depends(get_db),
    admin  = Depends(get_current_admin),
):
    if body.slot_name not in AD_SLOTS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid slot. Available: {', '.join(AD_SLOTS.keys())}"
        )

    ad = Advertisement(
        slot_name    = body.slot_name,
        company_name = body.company_name,
        logo_url     = body.logo_url,
        image_url    = body.image_url,
        link_url     = body.link_url,
        alt_text     = body.alt_text,
        active       = True,
        starts_at    = _now(),
        ends_at      = body.ends_at,
    )
    db.add(ad)
    db.commit()
    db.refresh(ad)

    return {
        "message": "Ad created and live",
        "ad_id":   ad.id,
        "slot":    ad.slot_name,
        "company": ad.company_name,
    }


@router.delete("/admin/{ad_id}", summary="Admin: Remove an ad")
def admin_remove_ad(
    ad_id: int,
    db:    Session = Depends(get_db),
    admin  = Depends(get_current_admin),
):
    ad = db.query(Advertisement).filter(Advertisement.id == ad_id).first()
    if not ad:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad.active = False
    db.commit()
    return {"message": "Ad deactivated"}


@router.get("/admin/all", summary="Admin: List all ads")
def admin_list_ads(
    db:    Session = Depends(get_db),
    admin  = Depends(get_current_admin),
):
    ads = db.query(Advertisement).order_by(Advertisement.created_at.desc()).all()
    return {"ads": [
        {
            "id":           a.id,
            "slot_name":    a.slot_name,
            "company_name": a.company_name,
            "active":       a.active,
            "starts_at":    a.starts_at,
            "ends_at":      a.ends_at,
            "logo_url":     a.logo_url,
            "link_url":     a.link_url,
        } for a in ads
    ]}
