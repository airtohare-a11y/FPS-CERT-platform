# =============================================================================
# app/payments.py
# Payment endpoints — user subscriptions and sponsor contributions.
#
# PCI COMPLIANCE:
#   This server NEVER touches card data. Stripe Checkout handles
#   all card entry on Stripe's hosted page. We store only Stripe
#   reference IDs (PaymentIntent ID, Subscription ID, Customer ID).
#   PCI DSS scope: SAQ-A (minimum tier — requires only HTTPS + Stripe TLS).
#
# STRIPE INTEGRATION STATUS: Phase 7 placeholder.
#   All Stripe API calls are commented out with # STRIPE: markers.
#   The data flow and webhook handling are fully designed.
#   To activate: install stripe package, set STRIPE_SECRET_KEY in Replit Secrets.
#
# WEBHOOK IDEMPOTENCY:
#   Every Stripe event has a unique event ID (evt_...).
#   Before processing, we check if stripe_event_id exists in transactions.
#   If found: return 200 immediately (prevents double-processing on retries).
#   If not found: process and insert with stripe_event_id.
# =============================================================================

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    User, Subscription, PaymentTransaction,
    RefundRequest, SponsorContribution, PaymentAuditLog,
    Sponsor, AppState,
)
from app.auth import get_current_user

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _log_payment(
    db:          Session,
    actor_type:  str,
    action:      str,
    actor_id:    Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id:   Optional[int] = None,
    amount_cents:Optional[int] = None,
    detail:      Optional[dict] = None,
    stripe_event_id: Optional[str] = None,
) -> None:
    """Append an immutable row to payment_audit_log."""
    db.add(PaymentAuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        amount_cents=amount_cents,
        detail_json=json.dumps(detail) if detail else None,
        stripe_event_id=stripe_event_id,
        occurred_at=_now(),
    ))


def _check_stripe_configured():
    """
    Raise a clear error if Stripe keys are not set.
    Called at the top of every live Stripe API call.
    """
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail=(
                "Payment processing is not yet configured. "
                "Set STRIPE_SECRET_KEY in Replit Secrets to activate."
            ),
        )


# =============================================================================
# User subscription routes
# =============================================================================

class SubscribeRequest(BaseModel):
    plan: str = "pro"   # only "pro" supported in v1


@router.post(
    "/subscribe",
    summary="Initiate Pro subscription checkout",
    description=(
        "Creates a Stripe Checkout Session and returns the hosted checkout URL. "
        "Card data never reaches this server — all entry happens on Stripe's page. "
        "LEGAL: All payments processed securely by Stripe. By subscribing, you agree "
        "to the Terms of Service and Refund Policy."
    ),
)
def subscribe(
    body:         SubscribeRequest,
    request:      Request,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Subscription flow:
      1. Check user isn't already Pro
      2. Create pending subscription row
      3. [STRIPE]: Create Stripe Checkout Session
      4. Return checkout_url for client redirect

    The actual tier upgrade happens via the Stripe webhook (POST /webhook)
    after Stripe confirms payment — NOT here. This prevents tier upgrades
    from unconfirmed payments.

    LEGAL: Subscription payment disclaimer shown before this endpoint is called.
    """
    if body.plan != "pro":
        raise HTTPException(status_code=400, detail="Only 'pro' plan is available")

    if current_user.tier == "pro":
        raise HTTPException(status_code=409, detail="You already have an active Pro subscription")

    # Create a pending subscription row — activated by webhook on payment confirmation
    sub = Subscription(
        user_id=current_user.id,
        status="pending",
        plan="pro",
        amount_cents=int(settings.PRO_MONTHLY_PRICE_USD * 100),
        currency="usd",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(sub)
    db.flush()

    _log_payment(
        db, actor_type="user", action="checkout_initiated",
        actor_id=current_user.id, entity_type="subscription", entity_id=sub.id,
        amount_cents=sub.amount_cents,
    )

    # ── STRIPE API CALL (Phase 7) ─────────────────────────────────────────────
    # _check_stripe_configured()
    # import stripe
    # stripe.api_key = settings.STRIPE_SECRET_KEY
    #
    # session = stripe.checkout.Session.create(
    #     payment_method_types=["card"],
    #     mode="subscription",
    #     customer_email=current_user.email,
    #     line_items=[{
    #         "price": settings.STRIPE_PRO_PRICE_ID,
    #         "quantity": 1,
    #     }],
    #     success_url=f"{request.base_url}payments/success?session_id={{CHECKOUT_SESSION_ID}}",
    #     cancel_url=f"{request.base_url}payments/cancel",
    #     metadata={"user_id": str(current_user.id), "subscription_id": str(sub.id)},
    # )
    # sub.stripe_pi_id = session.payment_intent
    # checkout_url = session.url
    # ─────────────────────────────────────────────────────────────────────────

    # Placeholder response until Stripe is configured
    checkout_url = "https://checkout.stripe.com/PLACEHOLDER_STRIPE_NOT_CONFIGURED"

    db.commit()

    return {
        "checkout_url":     checkout_url,
        "subscription_id":  sub.id,
        "amount_usd":       settings.PRO_MONTHLY_PRICE_USD,
        "legal_notice": (
            "All payments are processed securely by Stripe. "
            "By subscribing, you agree to the Terms of Service and Refund Policy. "
            "The platform is not responsible for banking or payment processing issues."
        ),
        "note": "Stripe integration pending configuration. Set STRIPE_SECRET_KEY.",
    }


@router.get(
    "/subscription",
    summary="Get current subscription status",
)
def get_subscription(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Returns the user's most recent active or cancelled subscription.
    DB read only — no Stripe API call (keeps this fast for every page load).
    """
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .filter(Subscription.status.in_(["active", "cancelled", "pending"]))
        .order_by(Subscription.created_at.desc())
        .first()
    )

    if not sub:
        return {
            "status":     "none",
            "plan":       None,
            "tier":       current_user.tier,
            "period_end": None,
        }

    # Lazy expiry check — if period_end has passed, downgrade tier
    if sub.status == "active" and sub.period_end and sub.period_end < _now():
        sub.status     = "expired"
        current_user.tier = "free"
        _log_payment(
            db, actor_type="system", action="tier_downgraded",
            actor_id=current_user.id,
            detail={"reason": "subscription_expired", "subscription_id": sub.id},
        )
        db.commit()

    return {
        "status":     sub.status,
        "plan":       sub.plan,
        "tier":       current_user.tier,
        "period_end": sub.period_end,
    }


@router.post(
    "/cancel",
    summary="Cancel subscription at end of current period",
)
def cancel_subscription(
    reason:       str  = "",
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Cancels the subscription at period_end — user retains Pro access until then.

    STRIPE: Calls stripe.Subscription.modify(sub.stripe_sub_id, cancel_at_period_end=True)
    """
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .filter(Subscription.status == "active")
        .order_by(Subscription.created_at.desc())
        .first()
    )

    if not sub:
        raise HTTPException(
            status_code=404,
            detail="No active subscription found",
        )

    # ── STRIPE API CALL (Phase 7) ─────────────────────────────────────────────
    # _check_stripe_configured()
    # stripe.Subscription.modify(sub.stripe_sub_id, cancel_at_period_end=True)
    # ─────────────────────────────────────────────────────────────────────────

    sub.status       = "cancelled"
    sub.cancelled_at = _now()
    sub.updated_at   = _now()

    _log_payment(
        db, actor_type="user", action="subscription_cancelled",
        actor_id=current_user.id, entity_type="subscription", entity_id=sub.id,
        detail={"reason": reason},
    )
    db.commit()

    return {
        "message":      "Subscription cancelled. Pro access continues until period end.",
        "access_until": sub.period_end,
    }


@router.post(
    "/refund-request",
    summary="Submit a refund request for admin review",
    description=(
        "Refund requests are reviewed within 3 business days. "
        "Only available within 7 days of the most recent charge. "
        "LEGAL: Refunds are at the platform's discretion per the Refund Policy."
    ),
)
def request_refund(
    reason:       str,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Creates a refund_request row for admin review.
    Refunds are NEVER auto-approved — all require admin action.
    """
    # Find the most recent successful transaction
    txn = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.user_id == current_user.id)
        .filter(PaymentTransaction.status == "succeeded")
        .order_by(PaymentTransaction.created_at.desc())
        .first()
    )

    if not txn:
        raise HTTPException(
            status_code=404,
            detail="No eligible transaction found for refund",
        )

    # Check 7-day window
    days_since = (_now() - txn.created_at) / 86400
    if days_since > 7:
        raise HTTPException(
            status_code=400,
            detail=f"Refund window expired ({days_since:.0f} days since charge). Window is 7 days.",
        )

    # Find associated subscription
    sub = (
        db.query(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Subscription.created_at.desc())
        .first()
    )

    refund_req = RefundRequest(
        user_id=current_user.id,
        subscription_id=sub.id if sub else 0,
        transaction_id=txn.id,
        reason=reason,
        status="pending",
        amount_cents=txn.amount_cents,
        requested_at=_now(),
    )
    db.add(refund_req)
    db.flush()

    _log_payment(
        db, actor_type="user", action="refund_requested",
        actor_id=current_user.id, entity_type="refund", entity_id=refund_req.id,
        amount_cents=txn.amount_cents,
    )
    db.commit()

    return {
        "request_id": refund_req.id,
        "status":     "pending",
        "message":    "Refund request submitted. Admin review within 3 business days.",
    }


@router.get(
    "/history",
    summary="Get payment history for current user",
)
def payment_history(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """
    Returns payment history. NEVER includes card details or internal Stripe refs.
    Only display-safe fields are returned to the client.
    """
    transactions = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.user_id == current_user.id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(50)
        .all()
    )

    return {
        "transactions": [
            {
                "id":               t.id,
                "type":             t.transaction_type,
                "amount_usd":       round(t.amount_cents / 100, 2),
                "currency":         t.currency,
                "status":           t.status,
                "description":      t.description,
                "created_at":       t.created_at,
            }
            for t in transactions
        ]
    }


# =============================================================================
# Stripe webhook
# =============================================================================

@router.post(
    "/webhook",
    summary="Stripe webhook receiver",
    description=(
        "Receives Stripe payment events. "
        "Verified via Stripe-Signature header. "
        "Idempotent — duplicate events are silently rejected."
    ),
    include_in_schema=False,  # hide from public Swagger docs
)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Stripe sends webhook events to this endpoint on every payment event.

    Security:
      - Verify Stripe-Signature header using STRIPE_WEBHOOK_SECRET
      - Check stripe_event_id against transactions table for duplicates
      - Always return HTTP 200 (Stripe retries on non-200 responses)

    Handled events:
      checkout.session.completed    → upgrade user to Pro
      invoice.payment_succeeded     → renew subscription
      invoice.payment_failed        → downgrade to Free
      customer.subscription.deleted → downgrade to Free
      charge.refunded               → process approved refund

    Phase 7: Uncomment Stripe verification block when keys are set.
    """
    payload   = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    _log_payment(db, actor_type="stripe_webhook", action="webhook_received",
                 detail={"sig_present": bool(sig_header)})

    # ── STRIPE SIGNATURE VERIFICATION (Phase 7) ───────────────────────────────
    # _check_stripe_configured()
    # import stripe
    # stripe.api_key = settings.STRIPE_SECRET_KEY
    # try:
    #     event = stripe.Webhook.construct_event(
    #         payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
    #     )
    # except stripe.error.SignatureVerificationError:
    #     db.commit()
    #     raise HTTPException(status_code=400, detail="Invalid webhook signature")
    #
    # stripe_event_id = event["id"]
    #
    # # Idempotency check — reject duplicates
    # existing = db.query(PaymentTransaction).filter(
    #     PaymentTransaction.external_transaction_id == stripe_event_id
    # ).first()
    # if existing:
    #     _log_payment(db, actor_type="stripe_webhook",
    #                  action="webhook_duplicate_rejected",
    #                  stripe_event_id=stripe_event_id)
    #     db.commit()
    #     return {"status": "duplicate_ignored"}
    #
    # # Route to handler based on event type
    # event_type = event["type"]
    # if event_type == "checkout.session.completed":
    #     _handle_checkout_complete(event["data"]["object"], stripe_event_id, db)
    # elif event_type == "invoice.payment_succeeded":
    #     _handle_payment_succeeded(event["data"]["object"], stripe_event_id, db)
    # elif event_type == "invoice.payment_failed":
    #     _handle_payment_failed(event["data"]["object"], stripe_event_id, db)
    # elif event_type == "customer.subscription.deleted":
    #     _handle_subscription_deleted(event["data"]["object"], stripe_event_id, db)
    # elif event_type == "charge.refunded":
    #     _handle_charge_refunded(event["data"]["object"], stripe_event_id, db)
    # ─────────────────────────────────────────────────────────────────────────

    db.commit()
    # Always return 200 — Stripe retries on any other status code
    return {"status": "received"}


# ── Webhook handler stubs (Phase 7 — fully implemented when Stripe is wired) ──

def _handle_checkout_complete(session_obj: dict, event_id: str, db: Session):
    """
    checkout.session.completed:
      - Find pending subscription by metadata.subscription_id
      - UPDATE status='active', period_start, period_end
      - UPDATE users SET tier='pro'
      - INSERT payment_transaction (succeeded)
      - LOG tier_upgraded
    """
    # TODO Phase 7
    pass


def _handle_payment_succeeded(invoice_obj: dict, event_id: str, db: Session):
    """
    invoice.payment_succeeded (renewal):
      - Find subscription by stripe_sub_id
      - INSERT new Subscription row for new period
      - INSERT payment_transaction (subscription_renewed)
    """
    # TODO Phase 7
    pass


def _handle_payment_failed(invoice_obj: dict, event_id: str, db: Session):
    """
    invoice.payment_failed:
      - Find subscription by stripe_sub_id
      - UPDATE status='failed'
      - UPDATE users SET tier='free'
      - INSERT payment_transaction (failed)
      - LOG tier_downgraded
    """
    # TODO Phase 7
    pass


def _handle_subscription_deleted(sub_obj: dict, event_id: str, db: Session):
    """
    customer.subscription.deleted (Stripe-side cancellation):
      - UPDATE subscription status='expired'
      - UPDATE users SET tier='free'
      - LOG tier_downgraded
    """
    # TODO Phase 7
    pass


def _handle_charge_refunded(charge_obj: dict, event_id: str, db: Session):
    """
    charge.refunded:
      - UPDATE refund_requests status='approved'
      - UPDATE subscription status='refunded'
      - UPDATE users SET tier='free'
      - INSERT payment_transaction (refund_issued)
      - LOG tier_downgraded, refund_issued
    """
    # TODO Phase 7
    pass


# =============================================================================
# Sponsor contribution route (public — no auth required for submission)
# =============================================================================

class SponsorContributionRequest(BaseModel):
    sponsor_name:       str
    contribution_type:  str = "cash"   # cash | prize | in_kind
    amount_cents:       Optional[int] = None
    currency:           str = "usd"
    prize_description:  Optional[str] = None
    link_url:           Optional[str] = None
    active_season:      int
    contact_email:      str


@router.post(
    "/sponsor-donation",
    status_code=status.HTTP_201_CREATED,
    summary="Submit a sponsor contribution for admin review",
    description=(
        "Sponsors submit contributions here for admin approval. "
        "Sponsor ad slot is NOT activated until admin approves. "
        "LEGAL: All sponsor contributions are voluntary. Sponsors are responsible "
        "for their content. The platform does not endorse sponsor materials."
    ),
)
def sponsor_donation(
    body: SponsorContributionRequest,
    db:   Session = Depends(get_db),
):
    """
    Sponsor contribution flow:
      1. Validate input
      2. Find or note sponsor (admin links sponsor record later)
      3. Insert contribution row (approval_status='pending')
      4. Notify admins (TODO: email notification in Phase 8)

    The sponsor's ad slot is NOT activated here.
    Admin must approve via POST /admin/payments/sponsor-contributions/{id}/approve
    """
    contribution = SponsorContribution(
        sponsor_id=0,           # linked to Sponsor row by admin after review
        sponsor_name=body.sponsor_name,
        contribution_type=body.contribution_type,
        amount_cents=body.amount_cents,
        currency=body.currency,
        prize_description=body.prize_description,
        link_url=body.link_url,
        active_season=body.active_season,
        approval_status="pending",
        submitted_at=_now(),
    )
    db.add(contribution)
    db.flush()

    _log_payment(
        db, actor_type="system", action="sponsor_contribution_submitted",
        entity_type="sponsor_contribution", entity_id=contribution.id,
        amount_cents=body.amount_cents,
        detail={"sponsor_name": body.sponsor_name, "type": body.contribution_type},
    )
    db.commit()

    return {
        "contribution_id": contribution.id,
        "status":          "pending",
        "message":         "Thank you. Your contribution has been submitted for review.",
        "legal_notice": (
            "All sponsor contributions are voluntary. Sponsors are responsible "
            "for their content. The platform does not endorse or guarantee the "
            "legality, accuracy, or safety of sponsor materials."
        ),
    }


# =============================================================================
# Advertiser payment routes
# =============================================================================

class AdvertiserPayRequest(BaseModel):
    slot_name:    str   # e.g. "sidebar_top", "between_content_1"
    company_name: str
    logo_url:     str
    link_url:     str
    months:       int = 1

@router.post(
    "/advertiser/subscribe",
    summary="Advertiser pays for an ad slot",
)
def advertiser_subscribe(
    body: AdvertiserPayRequest,
    db:   Session = Depends(get_db),
):
    """
    Advertiser pays for a named ad slot for N months.
    Stripe checkout stub — activate with STRIPE_SECRET_KEY.
    Admin approves before ad goes live.
    """
    _log_payment(db, actor_type="advertiser", action="ad_slot_checkout_initiated",
                 detail=f"slot={body.slot_name} company={body.company_name} months={body.months}")

    # STRIPE STUB
    # import stripe
    # stripe.api_key = settings.STRIPE_SECRET_KEY
    # session = stripe.checkout.Session.create(
    #     mode="payment",
    #     line_items=[{"price_data": {"currency":"usd","product_data":{"name":f"Ad Slot: {body.slot_name}"},"unit_amount":int(AD_SLOT_RATES.get(body.slot_name,999)*100)},"quantity":body.months}],
    #     success_url="https://yoursite.com/advertiser/success",
    #     cancel_url="https://yoursite.com/advertiser/cancel",
    # )
    # checkout_url = session.url

    checkout_url = "https://checkout.stripe.com/PLACEHOLDER_ADVERTISER_NOT_CONFIGURED"

    return {
        "checkout_url": checkout_url,
        "slot":         body.slot_name,
        "months":       body.months,
        "status":       "pending_payment",
        "message":      "Complete payment to reserve your ad slot. Admin approval required before going live.",
    }


@router.post(
    "/sponsor/prize-pool",
    summary="Sponsor pays into the monthly prize pool",
)
def sponsor_prize_pool(
    body: SponsorContributionRequest,
    db:   Session = Depends(get_db),
):
    """
    Sponsor contributes to the monthly competition prize pool.
    In return, they get a banner slot on the competition page.
    Multiple sponsors = multiple banners + larger prize pool.
    Admin approves before sponsor banner goes live.
    """
    _log_payment(db, actor_type="sponsor", action="prize_pool_contribution_initiated",
                 detail=f"amount=${body.amount} sponsor={body.sponsor_name}")

    # STRIPE STUB
    # import stripe
    # stripe.api_key = settings.STRIPE_SECRET_KEY
    # session = stripe.checkout.Session.create(
    #     mode="payment",
    #     line_items=[{"price_data":{"currency":"usd","product_data":{"name":f"MECHgg Prize Pool Contribution"},"unit_amount":int(body.amount*100)},"quantity":1}],
    #     success_url="https://yoursite.com/sponsor/success",
    #     cancel_url="https://yoursite.com/sponsor/cancel",
    # )
    # checkout_url = session.url

    checkout_url = "https://checkout.stripe.com/PLACEHOLDER_SPONSOR_NOT_CONFIGURED"

    return {
        "checkout_url": checkout_url,
        "amount":       body.amount,
        "status":       "pending_payment",
        "message":      "Complete payment to contribute to the prize pool. Your banner will appear on the competition page after admin approval.",
    }


@router.get(
    "/competition/prize-pool",
    summary="Get current month's prize pool total and sponsors",
)
def get_prize_pool(db: Session = Depends(get_db)):
    """
    Returns total prize pool and list of approved sponsors for current month.
    Used on the competition page header.
    """
    from app.models import SponsorContribution, Sponsor, AppState
    import calendar
    from datetime import datetime

    now    = datetime.now()
    season = db.query(AppState).filter(AppState.key == "current_season").first()
    season_num = int(season.value) if season else 1

    contributions = (
        db.query(SponsorContribution)
        .filter(
            SponsorContribution.season == season_num,
            SponsorContribution.approved_by_admin != None,
        )
        .all()
    )

    total = sum(c.amount for c in contributions if c.amount)

    # Get sponsor details for banners
    sponsor_ids  = [c.sponsor_id for c in contributions]
    sponsors_raw = db.query(Sponsor).filter(Sponsor.id.in_(sponsor_ids), Sponsor.is_active == True).all()

    # Days remaining in month
    last_day = calendar.monthrange(now.year, now.month)[1]
    days_left = last_day - now.day

    return {
        "season":       season_num,
        "total_prize":  total,
        "sponsor_count":len(sponsors_raw),
        "days_remaining": days_left,
        "sponsors": [
            {
                "id":       s.id,
                "name":     s.name,
                "logo_url": s.logo_url,
                "link_url": s.link_url,
            }
            for s in sponsors_raw
        ],
    }
