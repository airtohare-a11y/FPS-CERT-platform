# =============================================================================
# app/legal.py
# Legal disclaimer endpoints and consent recording.
#
# DESIGN: Disclaimer text is stored in the legal_disclaimers DB table,
# seeded from database.py on first startup. Admins can update text via
# the admin panel without a code deploy.
#
# GET /legal/{location_tag}  — frontend fetches disclaimers per page
# GET /legal/terms           — full Terms of Service document
# POST /legal/consent        — record that user accepted a disclaimer
#
# LEGAL REVIEW REQUIRED:
#   The default disclaimer text in database.py is a starting template.
#   Have a qualified attorney review all text before public launch.
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LegalDisclaimer, UserConsentLog, AppState, User
from app.auth import get_current_user

router = APIRouter()


# =============================================================================
# Public disclaimer endpoints (no auth required)
# =============================================================================

@router.get(
    "/terms",
    summary="Full Terms of Service document",
    description="Returns all disclaimer sections assembled into the full ToS.",
)
def get_terms(db: Session = Depends(get_db)):
    """
    Assembles the Terms of Service from all LegalDisclaimer rows.
    Used by the /legal/terms page and the registration modal.

    Frontend displays this before the ToS acceptance checkbox.
    """
    disclaimers = db.query(LegalDisclaimer).order_by(LegalDisclaimer.id).all()

    version_row = db.query(AppState).filter(AppState.key == "disclaimer_version").first()
    version = version_row.value if version_row else "1.0.0"

    return {
        "title":   "Terms of Service & Legal Notices",
        "version": version,
        "acceptance_statement": (
            "By creating an account or using this platform, you confirm that you "
            "have read, understood, and agree to all sections of these Terms of Service. "
            "If you do not agree, do not use this platform."
        ),
        "sections": [
            {
                "id":           d.id,
                "title":        d.title,
                "content":      d.content,
                "location_tag": d.location_tag,
                "last_updated": d.last_updated,
            }
            for d in disclaimers
        ],
    }


@router.get(
    "/privacy",
    summary="Privacy Policy",
    description="Returns data privacy and user consent disclaimers.",
)
def get_privacy(db: Session = Depends(get_db)):
    """
    Returns privacy-related disclaimers.
    Used by the Privacy Policy page linked from the footer.
    """
    disclaimers = db.query(LegalDisclaimer).filter(
        LegalDisclaimer.location_tag.in_(["upload", "registration"])
    ).all()

    return {
        "title": "Privacy Policy",
        "sections": [
            {
                "title":   d.title,
                "content": d.content,
            }
            for d in disclaimers
        ],
    }


@router.get(
    "/version",
    summary="Current disclaimer version",
    description=(
        "Returns the current disclaimer version string. "
        "Frontend checks this on login to detect if re-acceptance is needed."
    ),
)
def get_version(db: Session = Depends(get_db)):
    row = db.query(AppState).filter(AppState.key == "disclaimer_version").first()
    return {"version": row.value if row else "1.0.0"}


@router.get(
    "/{location_tag}",
    summary="Get disclaimers for a specific page/location",
    description=(
        "Returns all disclaimers applicable to a named location tag. "
        "Frontend calls this on page load to render the correct notices.\n\n"
        "Valid tags: login | upload | leaderboard | payments | admin | sponsors | "
        "registration | billing | competition | skill_card | footer"
    ),
)
def get_disclaimers_by_tag(
    location_tag: str,
    db:           Session = Depends(get_db),
):
    """
    Frontend pattern:
        const { data } = await fetch('/legal/upload')
        data.disclaimers.forEach(d => renderDisclaimer(d))

    Used by every page that needs to display legal notices.
    """
    valid_tags = {
        "login", "upload", "leaderboard", "payments", "admin",
        "sponsors", "registration", "billing", "competition",
        "skill_card", "footer",
    }

    if location_tag not in valid_tags:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown location_tag. Valid values: {', '.join(sorted(valid_tags))}",
        )

    disclaimers = (
        db.query(LegalDisclaimer)
        .filter(LegalDisclaimer.location_tag == location_tag)
        .order_by(LegalDisclaimer.id)
        .all()
    )

    version_row = db.query(AppState).filter(AppState.key == "disclaimer_version").first()
    version = version_row.value if version_row else "1.0.0"

    return {
        "location_tag": location_tag,
        "version":      version,
        "disclaimers": [
            {
                "id":           d.id,
                "title":        d.title,
                "content":      d.content,
                "last_updated": d.last_updated,
            }
            for d in disclaimers
        ],
    }


# =============================================================================
# Consent recording (authenticated)
# =============================================================================

class ConsentRequest(BaseModel):
    consent_type: str
    # terms_of_service | privacy_policy | upload_metadata | competition_entry
    version: str


@router.post(
    "/consent",
    status_code=status.HTTP_201_CREATED,
    summary="Record user consent for a disclaimer",
    description=(
        "Records that the authenticated user accepted a specific disclaimer version. "
        "Called at registration, after version bumps, and on each metadata upload."
    ),
)
def record_consent(
    body:         ConsentRequest,
    request:      Request,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Writes an immutable row to user_consent_log.
    This is the legal evidence that a user accepted specific terms.

    Called by:
      - POST /auth/register (terms_of_service)
      - POST /sessions (upload_metadata — once per upload)
      - Login middleware (terms_of_service — when version has changed)
    """
    valid_types = {
        "terms_of_service",
        "privacy_policy",
        "upload_metadata",
        "competition_entry",
    }

    if body.consent_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"consent_type must be one of: {', '.join(sorted(valid_types))}",
        )

    now = int(datetime.now(timezone.utc).timestamp())

    consent = UserConsentLog(
        user_id=current_user.id,
        consent_type=body.consent_type,
        disclaimer_version=body.version,
        accepted_at=now,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(consent)

    # Update user's accepted version if this is a ToS consent
    if body.consent_type == "terms_of_service":
        current_user.tos_version_accepted = body.version
        current_user.tos_accepted_at      = now

    db.commit()

    return {
        "recorded":    True,
        "consent_type": body.consent_type,
        "version":     body.version,
    }


@router.get(
    "/consent/status",
    summary="Check if current user needs to re-accept the ToS",
)
def consent_status(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Returns whether the user's accepted version matches the current version.
    Called on login — if re_acceptance_required=true, redirect to /legal/terms.
    """
    version_row = db.query(AppState).filter(AppState.key == "disclaimer_version").first()
    current_version = version_row.value if version_row else "1.0.0"

    needs_update = (
        current_user.tos_version_accepted is None or
        current_user.tos_version_accepted != current_version
    )

    return {
        "current_version":      current_version,
        "user_accepted_version": current_user.tos_version_accepted,
        "re_acceptance_required": needs_update,
    }
