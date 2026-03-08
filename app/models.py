# =============================================================================
# app/models.py
# All SQLAlchemy ORM models — one file for Module 1 clarity.
#
# Table domains:
#   DOMAIN 1 — Users & Auth         (User, Admin, AdminPermission)
#   DOMAIN 2 — Gameplay & Scoring   (DerivedSession, SkillProfile)
#   DOMAIN 3 — Roles & Seasons      (Role, LeaderboardCache, SeasonArchive)
#   DOMAIN 4 — Sponsors             (Sponsor, SponsorContribution)
#   DOMAIN 5 — Payments             (Subscription, PaymentTransaction,
#                                    RefundRequest, PaymentAuditLog)
#   DOMAIN 6 — Audit & Compliance   (AdminAuditLog, UserConsentLog,
#                                    AppLog, MaintenanceEvent)
#   DOMAIN 7 — System Config        (AppState, LegalDisclaimer)
#
# Relationships are defined using SQLAlchemy back_populates so both
# sides of a relationship are navigable without extra queries.
# =============================================================================

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Enum as SAEnum, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ── Shared helper ─────────────────────────────────────────────────────────────
def _now() -> int:
    """Current UTC time as a Unix timestamp integer."""
    return int(datetime.now(timezone.utc).timestamp())


# =============================================================================
# DOMAIN 1 — USERS & AUTH
# =============================================================================

class TierEnum(str, enum.Enum):
    free = "free"
    pro  = "pro"


class User(Base):
    """
    Core user accounts.
    tier controls access gating throughout the platform:
      free → 1 lifetime upload, limited leaderboard, no role unlock
      pro  → unlimited uploads, full features
    """
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    username        = Column(String(40),  unique=True, nullable=False, index=True)
    email           = Column(String(255), unique=True, nullable=False, index=True)
    password_hash   = Column(String(255), nullable=False)
    tier            = Column(SAEnum(TierEnum), nullable=False, default=TierEnum.free)
    is_active       = Column(Boolean, nullable=False, default=True)
    # Timestamps stored as Unix integers — no timezone conversion surprises.
    created_at      = Column(Integer, nullable=False, default=_now)
    last_login      = Column(Integer, nullable=True)
    # Populated from Stripe webhook — never from client input
    stripe_customer_id = Column(String(255), nullable=True)
    # Track total uploads for free tier quota enforcement
    total_uploads   = Column(Integer, nullable=False, default=0)
    # Track which ToS version this user last accepted
    tos_version_accepted = Column(String(20), nullable=True)
    tos_accepted_at      = Column(Integer,    nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    sessions         = relationship("DerivedSession",    back_populates="user",
                                    cascade="all, delete-orphan")
    skill_profile    = relationship("SkillProfile",      back_populates="user",
                                    uselist=False, cascade="all, delete-orphan")
    roles            = relationship("Role",              back_populates="user",
                                    cascade="all, delete-orphan")
    subscriptions    = relationship("Subscription",      back_populates="user",
                                    cascade="all, delete-orphan")
    transactions     = relationship("PaymentTransaction", back_populates="user")
    consent_log      = relationship("UserConsentLog",    back_populates="user",
                                    cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User {self.username} [{self.tier}]>"


class AdminRoleEnum(str, enum.Enum):
    superadmin = "superadmin"
    moderator  = "moderator"
    support    = "support"


class Admin(Base):
    """
    Admin accounts — completely separate from the users table.
    No public registration endpoint. Created via seed_admin.py CLI script.
    Admins authenticate via /admin/login and receive admin-scoped JWTs.
    Admin JWTs carry { "type": "admin" } — rejected by all user routes.
    """
    __tablename__ = "admins"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    username      = Column(String(40), unique=True, nullable=False, index=True)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role          = Column(SAEnum(AdminRoleEnum), nullable=False,
                           default=AdminRoleEnum.moderator)
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(Integer, nullable=False, default=_now)
    created_by    = Column(Integer, ForeignKey("admins.id"), nullable=True)
    last_login    = Column(Integer, nullable=True)
    last_login_ip = Column(String(45), nullable=True)  # supports IPv6

    # ── Relationships ─────────────────────────────────────────────────────────
    permissions   = relationship("AdminPermission", back_populates="admin",
                                 uselist=False, cascade="all, delete-orphan")
    audit_log     = relationship("AdminAuditLog",   back_populates="admin")

    def __repr__(self) -> str:
        return f"<Admin {self.username} [{self.role}]>"


class AdminPermission(Base):
    """
    Granular per-admin permission flags.
    Separating from the role column allows one-off overrides
    (e.g. give a 'support' admin temporary score-reset ability).

    Default sets by role (applied in seed_admin.py):
      superadmin → all true
      moderator  → manage_users, reset_scores, view_logs
      support    → view_logs only
    """
    __tablename__ = "admin_permissions"

    admin_id             = Column(Integer, ForeignKey("admins.id", ondelete="CASCADE"),
                                  primary_key=True)
    can_manage_users     = Column(Boolean, nullable=False, default=False)
    can_reset_scores     = Column(Boolean, nullable=False, default=False)
    can_manage_sponsors  = Column(Boolean, nullable=False, default=False)
    can_view_logs        = Column(Boolean, nullable=False, default=True)   # all admins
    can_manage_seasons   = Column(Boolean, nullable=False, default=False)
    can_manage_admins    = Column(Boolean, nullable=False, default=False)  # superadmin only
    can_manage_payments  = Column(Boolean, nullable=False, default=False)

    admin = relationship("Admin", back_populates="permissions")


# =============================================================================
# DOMAIN 2 — GAMEPLAY & SCORING
# (populated in Module 2 — scoring engine)
# =============================================================================

class DerivedSession(Base):
    """
    Stores only derived metrics extracted from the raw 60-second JSON upload.
    Raw JSON is NEVER persisted — it is processed in memory and discarded.
    is_ranked=False if the session failed sanity checks.
    """
    __tablename__ = "derived_sessions"

    id           = Column(Integer,  primary_key=True, autoincrement=True)
    user_id      = Column(Integer,  ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    season       = Column(Integer,  nullable=False, index=True)
    submitted_at = Column(Integer,  nullable=False, default=_now)
    map_slug     = Column(String(50), nullable=True)
    game_mode    = Column(String(20), nullable=True)
    is_ranked    = Column(Boolean,  nullable=False, default=True)

    # ── 7 Normalised metrics (0.0 – 100.0) ───────────────────────────────────
    # Computed by scoring.py (Module 2). Stored once, never recomputed.
    m_reaction    = Column(Float, nullable=True)   # Reaction Speed     (15%)
    m_accuracy    = Column(Float, nullable=True)   # Accuracy           (20%)
    m_eng_eff     = Column(Float, nullable=True)   # Engagement Eff.    (15%)
    m_consistency = Column(Float, nullable=True)   # Consistency        (15%)
    m_cqe         = Column(Float, nullable=True)   # Close Quarters     (10%)
    m_lre         = Column(Float, nullable=True)   # Long Range         (10%)
    m_dpi         = Column(Float, nullable=True)   # Damage Pressure    (15%)

    # Per-session OSI (0–1000). Computed once at upload, stored permanently.
    osi_session   = Column(Float, nullable=True)

    user = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<DerivedSession #{self.id} user={self.user_id} osi={self.osi_session}>"


class SkillProfile(Base):
    """
    One row per user. Rolling averages updated incrementally on each
    ranked session — never rebuilt from scratch.

    Rolling formula (O(1), no historical query needed):
        decay    = min(ranked_sessions, 30)
        new_avg  = (old_avg × decay + new_value) / (decay + 1)

    current_rank and current_percentile are written by the
    leaderboard cache rebuild, not by the upload pipeline.
    """
    __tablename__ = "skill_profiles"

    user_id           = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                               primary_key=True)
    season            = Column(Integer, nullable=False, default=1)
    osi               = Column(Float,   nullable=False, default=0.0)
    avg_reaction      = Column(Float,   nullable=False, default=0.0)
    avg_accuracy      = Column(Float,   nullable=False, default=0.0)
    avg_eng_eff       = Column(Float,   nullable=False, default=0.0)
    avg_consistency   = Column(Float,   nullable=False, default=0.0)
    avg_cqe           = Column(Float,   nullable=False, default=0.0)
    avg_lre           = Column(Float,   nullable=False, default=0.0)
    avg_dpi           = Column(Float,   nullable=False, default=0.0)
    peak_osi          = Column(Float,   nullable=False, default=0.0)
    ranked_sessions   = Column(Integer, nullable=False, default=0)
    current_rank      = Column(Integer, nullable=True)
    current_percentile= Column(Float,   nullable=True)
    last_updated      = Column(Integer, nullable=False, default=_now)

    user = relationship("User", back_populates="skill_profile")


# =============================================================================
# DOMAIN 3 — ROLES & SEASONS
# (role logic wired in Module 5)
# =============================================================================

class RoleSlugEnum(str, enum.Enum):
    tank            = "tank"
    recon           = "recon"
    cqs             = "cqs"             # Close Quarters Specialist
    tactical_anchor = "tactical_anchor"
    aggressor       = "aggressor"


class CertLevelEnum(str, enum.Enum):
    candidate = "candidate"
    certified = "certified"
    elite     = "elite"


class Role(Base):
    """
    One row per (user × role_slug × season).
    cert_level moves forward only: candidate → certified → elite.
    is_active flips to False after ROLE_INACTIVITY_DAYS without upload.
    Reactivates automatically on next ranked upload — cert_level preserved.
    """
    __tablename__ = "roles"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    season        = Column(Integer, nullable=False)
    role_slug     = Column(SAEnum(RoleSlugEnum), nullable=False)
    cert_level    = Column(SAEnum(CertLevelEnum), nullable=False)
    unlocked_at   = Column(Integer, nullable=False, default=_now)
    last_activity = Column(Integer, nullable=False, default=_now)
    is_active     = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("user_id", "role_slug", "season", name="uq_role_user_season"),
    )

    user = relationship("User", back_populates="roles")


class LeaderboardCache(Base):
    """
    Pre-sorted leaderboard rows rebuilt lazily via the dirty flag in app_state.
    Read paths hit this table only — never aggregate from derived_sessions.
    username is denormalised here so leaderboard reads need zero JOINs.
    """
    __tablename__ = "leaderboard_cache"

    season     = Column(Integer, primary_key=True)
    rank       = Column(Integer, primary_key=True)
    user_id    = Column(Integer, nullable=False, index=True)
    username   = Column(String,  nullable=False)  # denormalised for zero-join reads
    osi        = Column(Float,   nullable=False)
    percentile = Column(Float,   nullable=False)
    tier_badge = Column(String,  nullable=False)  # bronze|silver|gold|elite|apex
    top_role   = Column(String,  nullable=True)
    built_at   = Column(Integer, nullable=False, default=_now)


class SeasonArchive(Base):
    """
    Immutable end-of-season snapshots.
    Written once when an admin closes a season. Never updated.
    """
    __tablename__ = "season_archive"

    user_id           = Column(Integer, primary_key=True)
    season            = Column(Integer, primary_key=True)
    final_osi         = Column(Float,   nullable=False)
    final_rank        = Column(Integer, nullable=True)
    final_percentile  = Column(Float,   nullable=True)
    roles_json        = Column(Text,    nullable=True)  # JSON: [{slug, cert_level}]
    archived_at       = Column(Integer, nullable=False, default=_now)


# =============================================================================
# DOMAIN 4 — SPONSORS
# =============================================================================

class Sponsor(Base):
    """
    Sponsor display entries. Logos are ALWAYS external URLs.
    No image files are stored in the application.
    Changes by admins propagate immediately — no cache flush needed
    because /sponsors/active queries this table directly.
    """
    __tablename__ = "sponsors"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    name          = Column(String(100), nullable=False)
    logo_url      = Column(String(500), nullable=False)   # EXTERNAL URL ONLY
    link_url      = Column(String(500), nullable=False)
    slot          = Column(String(30),  nullable=False, default="leaderboard_banner")
    # leaderboard_banner | profile_badge
    active_season = Column(Integer, nullable=False)
    priority      = Column(Integer, nullable=False, default=0)  # higher shown first
    min_tier      = Column(String(10), nullable=False, default="free")
    is_active     = Column(Boolean, nullable=False, default=True)
    created_by    = Column(Integer, ForeignKey("admins.id"), nullable=True)
    created_at    = Column(Integer, nullable=False, default=_now)
    updated_by    = Column(Integer, ForeignKey("admins.id"), nullable=True)
    updated_at    = Column(Integer, nullable=True)

    contributions = relationship("SponsorContribution", back_populates="sponsor",
                                 cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Sponsor {self.name} active={self.is_active}>"


class SponsorContribution(Base):
    """
    Tracks sponsor monetary contributions, prizes, and in-kind donations.
    All submissions require admin approval before the sponsor's ads are activated.
    For cash contributions, payment is handled externally and recorded here
    by the admin after confirmation — no card data is ever stored.
    """
    __tablename__ = "sponsor_contributions"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    sponsor_id        = Column(Integer, ForeignKey("sponsors.id", ondelete="CASCADE"),
                               nullable=False)
    sponsor_name      = Column(String(100), nullable=False)  # denormalised for audit permanence
    contribution_type = Column(String(20),  nullable=False, default="cash")
    # cash | prize | in_kind
    amount_cents      = Column(Integer, nullable=True)        # NULL for non-cash
    currency          = Column(String(3), nullable=True, default="usd")
    prize_description = Column(Text,    nullable=True)
    link_url          = Column(String(500), nullable=True)
    active_season     = Column(Integer, nullable=False)
    approval_status   = Column(String(20), nullable=False, default="pending")
    # pending | approved | rejected | revoked
    approved_by       = Column(Integer, ForeignKey("admins.id"), nullable=True)
    admin_note        = Column(Text,    nullable=True)
    stripe_reference  = Column(String(255), nullable=True)  # Stripe PaymentIntent ID if cash
    submitted_at      = Column(Integer, nullable=False, default=_now)
    approved_at       = Column(Integer, nullable=True)

    sponsor = relationship("Sponsor", back_populates="contributions")


# =============================================================================
# DOMAIN 5 — PAYMENTS
# ⚠  PCI COMPLIANCE: This application NEVER stores card numbers, CVV,
#    expiry dates, or any raw payment credentials. Only Stripe reference IDs
#    are stored. Stripe Checkout handles all card entry (SAQ-A scope).
# =============================================================================

class SubscriptionStatusEnum(str, enum.Enum):
    pending   = "pending"
    active    = "active"
    cancelled = "cancelled"
    expired   = "expired"
    failed    = "failed"
    refunded  = "refunded"


class Subscription(Base):
    """
    One row per subscription period per user.
    A new row is inserted on each renewal (not updated in place)
    so full billing history is preserved without needing payment_transactions.

    stripe_customer_id — set when user first subscribes, reused for renewals.
    stripe_sub_id      — Stripe Subscription object (recurring billing).
    stripe_pi_id       — Stripe PaymentIntent (individual charges).

    NEVER store: card numbers, CVV, expiry dates, bank details.
    """
    __tablename__ = "user_subscriptions"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    user_id              = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    status               = Column(SAEnum(SubscriptionStatusEnum), nullable=False,
                                  default=SubscriptionStatusEnum.pending)
    plan                 = Column(String(20), nullable=False, default="pro")
    amount_cents         = Column(Integer, nullable=False, default=700)  # $7.00
    currency             = Column(String(3), nullable=False, default="usd")
    # Stripe reference IDs — safe to store, carry no payment credentials
    stripe_customer_id   = Column(String(255), nullable=True)
    stripe_sub_id        = Column(String(255), nullable=True, index=True)
    stripe_pi_id         = Column(String(255), nullable=True)
    period_start         = Column(Integer, nullable=True)
    period_end           = Column(Integer, nullable=True)
    cancelled_at         = Column(Integer, nullable=True)
    created_at           = Column(Integer, nullable=False, default=_now)
    updated_at           = Column(Integer, nullable=False, default=_now)

    user = relationship("User", back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription user={self.user_id} status={self.status}>"


class PaymentTransaction(Base):
    """
    Immutable payment ledger. Every payment event appends a new row.
    Never updated. Source of truth for billing history.

    stripe_event_id has a UNIQUE constraint — this is the idempotency key.
    Before processing any Stripe webhook, we check if stripe_event_id exists.
    If it does, we return 200 immediately without re-processing.
    This prevents duplicate charges on Stripe retries.
    """
    __tablename__ = "transactions"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    # entity_type distinguishes user payments from sponsor contributions
    entity_type        = Column(String(20), nullable=False, default="user")
    # 'user' | 'sponsor'
    user_id            = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    sponsor_id         = Column(Integer, ForeignKey("sponsors.id"), nullable=True)
    transaction_type   = Column(String(40), nullable=False)
    # subscription_created | subscription_renewed | subscription_cancelled |
    # subscription_failed | refund_issued |
    # sponsor_contribution_received | sponsor_contribution_refunded
    amount_cents       = Column(Integer, nullable=False)  # negative for refunds
    currency           = Column(String(3), nullable=False, default="usd")
    status             = Column(String(20), nullable=False, default="pending")
    # pending | succeeded | failed | refunded
    # Stripe event ID — UNIQUE constraint prevents duplicate processing
    external_transaction_id = Column(String(255), nullable=True, unique=True)
    stripe_reference   = Column(String(255), nullable=True)  # PaymentIntent or Sub ID
    description        = Column(String(255), nullable=True)
    created_at         = Column(Integer, nullable=False, default=_now)

    user = relationship("User", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction #{self.id} {self.transaction_type} {self.amount_cents}¢>"


class RefundRequest(Base):
    """
    User-submitted refund requests. Always requires admin review.
    Refunds are never auto-approved.
    Actual Stripe refund is triggered by admin approval action.
    """
    __tablename__ = "refund_requests"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    user_id            = Column(Integer, ForeignKey("users.id"), nullable=False)
    subscription_id    = Column(Integer, ForeignKey("user_subscriptions.id"), nullable=False)
    transaction_id     = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    reason             = Column(Text, nullable=False)
    status             = Column(String(20), nullable=False, default="pending")
    # pending | approved | rejected | cancelled
    admin_id           = Column(Integer, ForeignKey("admins.id"), nullable=True)
    admin_note         = Column(Text, nullable=True)
    stripe_refund_id   = Column(String(255), nullable=True)
    amount_cents       = Column(Integer, nullable=False)
    requested_at       = Column(Integer, nullable=False, default=_now)
    resolved_at        = Column(Integer, nullable=True)


class PaymentAuditLog(Base):
    """
    Immutable log of every payment-related event.
    Separate from admin_audit_log so payment history is independently
    queryable for financial reporting and dispute resolution.
    """
    __tablename__ = "payment_audit_log"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    actor_type      = Column(String(20), nullable=False)
    # 'user' | 'admin' | 'system' | 'stripe_webhook'
    actor_id        = Column(Integer, nullable=True)
    action          = Column(String(50), nullable=False)
    # checkout_initiated | payment_confirmed | payment_failed |
    # subscription_cancelled | refund_requested | refund_approved |
    # refund_rejected | refund_issued | tier_upgraded | tier_downgraded |
    # sponsor_contribution_submitted | sponsor_contribution_approved |
    # webhook_received | webhook_duplicate_rejected
    entity_type     = Column(String(20), nullable=True)
    entity_id       = Column(Integer,    nullable=True)
    amount_cents    = Column(Integer,    nullable=True)
    detail_json     = Column(Text,       nullable=True)
    stripe_event_id = Column(String(255), nullable=True)
    occurred_at     = Column(Integer, nullable=False, default=_now)


# =============================================================================
# DOMAIN 6 — AUDIT & COMPLIANCE
# =============================================================================

class AdminAuditLog(Base):
    """
    Immutable log of every admin action.
    admin_username is denormalised — if the admin account is later deleted,
    the log entry still shows who performed the action.
    All state-changing admin actions write here in the same DB transaction
    as the change itself — they cannot get out of sync.
    """
    __tablename__ = "admin_logs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    admin_id       = Column(Integer, ForeignKey("admins.id"), nullable=False, index=True)
    admin_username = Column(String(40), nullable=False)  # denormalised for permanence
    action_type    = Column(String(50), nullable=False)
    # login | logout | user.view | user.edit | user.suspend |
    # score.reset_osi | score.reset_roles | sponsor.create |
    # sponsor.update | sponsor.delete | season.close | admin.create |
    # log.view | maintenance.trigger
    target_id      = Column(Integer, nullable=True)
    description    = Column(Text,    nullable=True)  # JSON with before/after values
    ip_address     = Column(String(45), nullable=True)
    created_at     = Column(Integer, nullable=False, default=_now)

    admin = relationship("Admin", back_populates="audit_log")


class UserConsentLog(Base):
    """
    Immutable record of every ToS/Privacy acceptance event.
    Legally critical: proves a specific user accepted a specific
    disclaimer version at a specific time.
    Recorded at registration, first login after version bump,
    and each metadata upload.
    """
    __tablename__ = "user_consent_log"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    user_id            = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    consent_type       = Column(String(30), nullable=False)
    # terms_of_service | privacy_policy | upload_metadata | competition_entry
    disclaimer_version = Column(String(20), nullable=False)
    accepted_at        = Column(Integer, nullable=False, default=_now)
    ip_address         = Column(String(45), nullable=True)
    user_agent         = Column(String(255), nullable=True)

    user = relationship("User", back_populates="consent_log")


class AppLog(Base):
    """
    Application event log. Written by the backend for upload errors,
    failed processing, auth events, and system events.
    Admins read this via /admin/logs.
    Auto-purged after N days via /admin/maintenance/purge-old-logs.
    """
    __tablename__ = "app_logs"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    level       = Column(String(10), nullable=False)  # info | warning | error | critical
    source      = Column(String(20), nullable=False)  # upload | scoring | auth | season | system
    message     = Column(String(500), nullable=False)
    detail_json = Column(Text, nullable=True)  # JSON context: user_id, error trace, etc.
    user_id     = Column(Integer, nullable=True, index=True)  # NULL for system events
    occurred_at = Column(Integer, nullable=False, default=_now)


class MaintenanceEvent(Base):
    """
    Records every manual maintenance operation performed by admins.
    Gives an audit trail of patches, resets, and cache rebuilds.
    """
    __tablename__ = "maintenance_events"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    admin_id       = Column(Integer, ForeignKey("admins.id"), nullable=False)
    event_type     = Column(String(40), nullable=False)
    # session_invalidated | score_recalculated | cache_rebuilt |
    # season_closed | db_vacuum | log_purge | test_data_cleared
    description    = Column(Text, nullable=False)
    affected_count = Column(Integer, nullable=True)
    performed_at   = Column(Integer, nullable=False, default=_now)


# =============================================================================
# DOMAIN 7 — SYSTEM CONFIG
# =============================================================================

class AppState(Base):
    """
    Single key/value config store for runtime application state.
    Seeded with defaults on startup.

    Keys used:
      current_season       "1"
      leaderboard_dirty    "0" | "1"
      season_end_date      "YYYY-MM-DD"
      reddit_url           link or ""
      discord_url          link or ""
      disclaimer_version   "1.0.0"
    """
    __tablename__ = "app_state"

    key   = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<AppState {self.key}={self.value}>"


class LegalDisclaimer(Base):
    """
    Stored disclaimer text served by /legal/{location_tag}.
    Stored in DB so admins can update text without a code deploy.
    The canonical defaults are seeded from database.py on first startup.

    location_tag values used throughout the frontend:
      login | upload | leaderboard | payments | admin | sponsors
      registration | billing | competition | skill_card | footer
    """
    __tablename__ = "legal_disclaimers"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    title        = Column(String(100), nullable=False)
    content      = Column(Text,        nullable=False)
    location_tag = Column(String(30),  nullable=False, index=True)
    last_updated = Column(Integer,     nullable=False, default=_now)

    def __repr__(self) -> str:
        return f"<LegalDisclaimer {self.location_tag}: {self.title}>"

# =============================================================================
# Game Registry — supported games for video analysis
# =============================================================================

GAME_REGISTRY = {
    "valorant":       {"name":"Valorant",          "category":"fps",      "style":"tactical",     "emoji":"🎯"},
    "csgo":           {"name":"CS2",                "category":"fps",      "style":"tactical",     "emoji":"💣"},
    "r6siege":        {"name":"Rainbow Six Siege",  "category":"fps",      "style":"tactical",     "emoji":"🛡️"},
    "arcraiders":     {"name":"Arc Raiders",        "category":"fps",      "style":"extraction",   "emoji":"⚡"},
    "apex":           {"name":"Apex Legends",       "category":"fps",      "style":"battleroyale", "emoji":"🦁"},
    "fortnite":       {"name":"Fortnite",           "category":"fps",      "style":"battleroyale", "emoji":"🏗️"},
    "warzone":        {"name":"Warzone",            "category":"fps",      "style":"battleroyale", "emoji":"🪖"},
    "overwatch2":     {"name":"Overwatch 2",        "category":"fps",      "style":"hero",         "emoji":"🦸"},
    "f1":             {"name":"F1",                 "category":"racing",   "style":"sim",          "emoji":"🏎️"},
    "gt7":            {"name":"Gran Turismo 7",     "category":"racing",   "style":"sim",          "emoji":"🚗"},
    "forzamotorsport":{"name":"Forza Motorsport",   "category":"racing",   "style":"sim",          "emoji":"🏁"},
    "forzahorizon":   {"name":"Forza Horizon",      "category":"racing",   "style":"arcade",       "emoji":"🌄"},
    "mariokart":      {"name":"Mario Kart",         "category":"racing",   "style":"arcade",       "emoji":"🍄"},
    "dirtally":       {"name":"Dirt Rally",         "category":"racing",   "style":"rally",        "emoji":"🪨"},
    "fifa":           {"name":"EA FC",              "category":"sports",   "style":"football",     "emoji":"⚽"},
    "nba2k":          {"name":"NBA 2K",             "category":"sports",   "style":"basketball",   "emoji":"🏀"},
    "madden":         {"name":"Madden NFL",         "category":"sports",   "style":"football",     "emoji":"🏈"},
    "nhl":            {"name":"NHL",                "category":"sports",   "style":"hockey",       "emoji":"🏒"},
    "mlbtheshow":     {"name":"MLB The Show",       "category":"sports",   "style":"baseball",     "emoji":"⚾"},
    "starcraft2":     {"name":"StarCraft II",       "category":"strategy", "style":"rts",          "emoji":"🚀"},
    "aoe4":           {"name":"Age of Empires IV",  "category":"strategy", "style":"rts",          "emoji":"⚔️"},
    "leagueoflegends":{"name":"League of Legends",  "category":"strategy", "style":"moba",         "emoji":"🔮"},
    "dota2":          {"name":"Dota 2",             "category":"strategy", "style":"moba",         "emoji":"🌟"},
    "sf6":            {"name":"Street Fighter 6",   "category":"fighting", "style":"traditional",  "emoji":"👊"},
    "tekken8":        {"name":"Tekken 8",           "category":"fighting", "style":"traditional",  "emoji":"🥊"},
    "mortalkombat":   {"name":"Mortal Kombat",      "category":"fighting", "style":"traditional",  "emoji":"💀"},
    "smashbros":      {"name":"Super Smash Bros",   "category":"fighting", "style":"platform",     "emoji":"🎮"},
    # ── Casual / Unsupported ─────────────────────────────────────────────────
    # These games score but do NOT qualify for ranked competition or leaderboard
    "other":          {"name":"Other Supported Game",  "category":"other",    "style":"casual",      "emoji":"🎮", "ranked": False},
    "roblox":         {"name":"Roblox",                "category":"other",    "style":"casual",      "emoji":"🧱", "ranked": False},
    "minecraft":      {"name":"Minecraft",             "category":"other",    "style":"casual",      "emoji":"⛏️", "ranked": False},
    "fishingplanet":  {"name":"Fishing Planet",        "category":"other",    "style":"casual",      "emoji":"🎣", "ranked": False},
    "thehunter":      {"name":"theHunter: Call of the Wild", "category":"other", "style":"casual",   "emoji":"🦌", "ranked": False},
    "stardewvalley":  {"name":"Stardew Valley",        "category":"other",    "style":"casual",      "emoji":"🌾", "ranked": False},
    "thesims":        {"name":"The Sims",              "category":"other",    "style":"casual",      "emoji":"🏠", "ranked": False},
}

# Games that qualify for ranked play and competition
RANKED_CATEGORIES = {"fps", "racing", "sports", "strategy", "fighting"}

def is_ranked_game(game_id: str) -> bool:
    """Returns True if this game qualifies for ranked play and competition."""
    info = GAME_REGISTRY.get(game_id, {})
    if not info:
        return False
    if info.get("ranked") is False:
        return False
    return info.get("category") in RANKED_CATEGORIES


# =============================================================================
# Advertisement Slots
# =============================================================================

AD_SLOTS = {
    "landing_top":        {"label": "Landing Page — Top Banner",     "rate_month": 299},
    "landing_sidebar":    {"label": "Landing Page — Sidebar",        "rate_month": 149},
    "between_content_1":  {"label": "Between Content — Position 1",  "rate_month": 199},
    "between_content_2":  {"label": "Between Content — Position 2",  "rate_month": 149},
    "dashboard_sidebar":  {"label": "Dashboard — Sidebar",           "rate_month": 249},
    "analysis_sidebar":   {"label": "Analysis Result — Sidebar",     "rate_month": 199},
}

class Advertisement(Base):
    """
    An approved advertisement placed in a named slot.
    Created by admin after advertiser payment is confirmed.
    """
    __tablename__ = "advertisements"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    slot_name    = Column(String(50),  nullable=False, index=True)
    company_name = Column(String(100), nullable=False)
    logo_url     = Column(String(500), nullable=True)
    link_url     = Column(String(500), nullable=True)
    image_url    = Column(String(500), nullable=True)  # banner image
    alt_text     = Column(String(200), nullable=True)
    active       = Column(Boolean,     nullable=False, default=True)
    starts_at    = Column(Integer,     nullable=False, default=_now)
    ends_at      = Column(Integer,     nullable=True)
    created_at   = Column(Integer,     nullable=False, default=_now)
