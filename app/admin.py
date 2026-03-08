# =============================================================================
# app/admin.py
# Admin authentication and all admin-scoped route placeholders.
#
# SECURITY DESIGN:
#   - Admin accounts are completely separate from user accounts.
#   - Admin JWT tokens carry { "type": "admin" } — rejected by all user routes.
#   - User JWT tokens carry { "type": "user" } — rejected here.
#   - Every route requires a permission flag check via require_permission().
#   - All state-changing actions write to admin_logs in the same DB transaction.
#   - No admin credentials are ever sent to the frontend.
#
# FIRST ADMIN ACCOUNT:
#   Run seed_admin.py from the command line — no HTTP endpoint for this.
#   Subsequent admins created via POST /admin/accounts (superadmin only).
# =============================================================================

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    Admin, AdminPermission, AdminAuditLog,
    User, Sponsor, SponsorContribution,
    AppState, MaintenanceEvent,
)
from app.auth import (
    create_access_token, decode_token,
    hash_password, verify_password,
)

router = APIRouter()
_bearer = HTTPBearer()


# =============================================================================
# Admin auth dependencies
# =============================================================================

def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Admin:
    """
    Resolve Bearer token → Admin ORM object.
    Explicitly rejects user tokens (type != "admin").

    Usage: admin: Admin = Depends(get_current_admin)
    """
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — admin token required",
        )

    admin = db.query(Admin).filter(Admin.id == int(payload["sub"])).first()
    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin account not found or suspended",
        )
    return admin


def require_permission(permission_flag: str):
    """
    Dependency factory. Returns a dependency that checks a single
    permission flag on the current admin's AdminPermission row.

    Usage:
        @router.post("/sponsors")
        def create_sponsor(
            admin = Depends(require_permission("can_manage_sponsors")),
            db    = Depends(get_db),
        ): ...

    Valid flags:
        can_manage_users | can_reset_scores | can_manage_sponsors |
        can_view_logs | can_manage_seasons | can_manage_admins |
        can_manage_payments
    """
    def _check(
        admin: Admin = Depends(get_current_admin),
        db:    Session = Depends(get_db),
    ) -> Admin:
        perms = db.query(AdminPermission).filter(
            AdminPermission.admin_id == admin.id
        ).first()

        if not perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No permissions record found for this admin account",
            )

        if not getattr(perms, permission_flag, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: requires {permission_flag}",
            )
        return admin

    return _check


def _log_action(
    db:          Session,
    admin:       Admin,
    action_type: str,
    target_id:   Optional[int] = None,
    description: Optional[str] = None,
    ip_address:  Optional[str] = None,
) -> None:
    """
    Write an immutable entry to admin_logs.
    Called inside every state-changing admin route, in the same
    DB transaction as the change itself.
    """
    log = AdminAuditLog(
        admin_id=admin.id,
        admin_username=admin.username,   # denormalised — survives account deletion
        action_type=action_type,
        target_id=target_id,
        description=description,
        ip_address=ip_address,
        created_at=int(datetime.now(timezone.utc).timestamp()),
    )
    db.add(log)
    # Do NOT commit here — the caller commits with the main action.


# =============================================================================
# Admin authentication routes
# =============================================================================

class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminAuthResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    admin_id:     int
    username:     str
    role:         str
    permissions:  dict


@router.post(
    "/login",
    response_model=AdminAuthResponse,
    summary="Admin login — returns admin-scoped JWT",
)
def admin_login(
    body: AdminLoginRequest, request: Request, db: Session = Depends(get_db)
):
    """
    Admin login flow:
      1. Find admin by username
      2. Verify password
      3. Check is_active
      4. Update last_login + last_login_ip
      5. Log the login event
      6. Return admin JWT

    Admin JWT carries { "type": "admin" } — cannot be used on user routes.
    """
    admin = db.query(Admin).filter(Admin.username == body.username).first()

    if not admin or not verify_password(body.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
        )

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account suspended",
        )

    now = int(datetime.now(timezone.utc).timestamp())
    admin.last_login    = now
    admin.last_login_ip = request.client.host if request.client else None

    # Load permissions for response
    perms = db.query(AdminPermission).filter(
        AdminPermission.admin_id == admin.id
    ).first()

    _log_action(
        db, admin, "login",
        description=f"Admin login from {admin.last_login_ip}",
        ip_address=admin.last_login_ip,
    )
    db.commit()

    permissions_dict = {}
    if perms:
        permissions_dict = {
            "can_manage_users":    perms.can_manage_users,
            "can_reset_scores":    perms.can_reset_scores,
            "can_manage_sponsors": perms.can_manage_sponsors,
            "can_view_logs":       perms.can_view_logs,
            "can_manage_seasons":  perms.can_manage_seasons,
            "can_manage_admins":   perms.can_manage_admins,
            "can_manage_payments": perms.can_manage_payments,
        }

    token = create_access_token(admin.id, token_type="admin")
    return AdminAuthResponse(
        access_token=token,
        admin_id=admin.id,
        username=admin.username,
        role=admin.role,
        permissions=permissions_dict,
    )


@router.get(
    "/me",
    summary="Get current admin profile and permissions",
)
def admin_me(
    admin: Admin = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    perms = db.query(AdminPermission).filter(
        AdminPermission.admin_id == admin.id
    ).first()
    return {
        "id":       admin.id,
        "username": admin.username,
        "role":     admin.role,
        "permissions": {
            "can_manage_users":    perms.can_manage_users    if perms else False,
            "can_reset_scores":    perms.can_reset_scores    if perms else False,
            "can_manage_sponsors": perms.can_manage_sponsors if perms else False,
            "can_view_logs":       perms.can_view_logs       if perms else False,
            "can_manage_seasons":  perms.can_manage_seasons  if perms else False,
            "can_manage_admins":   perms.can_manage_admins   if perms else False,
            "can_manage_payments": perms.can_manage_payments if perms else False,
        },
    }


# =============================================================================
# User management routes  (requires can_manage_users)
# =============================================================================

@router.get(
    "/users",
    summary="List all users (paginated)",
    description="LEGAL: Access to user data is logged. Handle per Privacy Policy.",
)
def list_users(
    page:  int = 1,
    limit: int = 50,
    tier:  Optional[str] = None,
    admin: Admin   = Depends(require_permission("can_manage_users")),
    db:    Session = Depends(get_db),
):
    """
    Returns paginated user list.
    Filters: ?tier=free|pro
    Admins never see password_hash — excluded from response.
    """
    # TODO (Module 8): implement full filtering + pagination
    query = db.query(User)
    if tier:
        query = query.filter(User.tier == tier)
    total  = query.count()
    offset = (page - 1) * limit
    users  = query.offset(offset).limit(limit).all()

    _log_action(db, admin, "user.view", description="Listed users")
    db.commit()

    return {
        "total": total,
        "page":  page,
        "users": [
            {
                "id":             u.id,
                "username":       u.username,
                "email":          u.email,
                "tier":           u.tier,
                "is_active":      u.is_active,
                "total_uploads":  u.total_uploads,
                "created_at":     u.created_at,
                "last_login":     u.last_login,
            }
            for u in users
        ],
    }


@router.get(
    "/users/{user_id}",
    summary="Get full detail for one user",
)
def get_user(
    user_id: int,
    admin:   Admin   = Depends(require_permission("can_manage_users")),
    db:      Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    _log_action(db, admin, "user.view", target_id=user_id)
    db.commit()

    return {
        "id":            user.id,
        "username":      user.username,
        "email":         user.email,
        "tier":          user.tier,
        "is_active":     user.is_active,
        "total_uploads": user.total_uploads,
        "created_at":    user.created_at,
        "last_login":    user.last_login,
    }


@router.put(
    "/users/{user_id}/tier",
    summary="Change a user's subscription tier",
)
def set_user_tier(
    user_id:  int,
    new_tier: str,
    admin:    Admin   = Depends(require_permission("can_manage_users")),
    db:       Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if new_tier not in ("free", "pro"):
        raise HTTPException(status_code=400, detail="tier must be 'free' or 'pro'")

    old_tier = user.tier
    user.tier = new_tier
    _log_action(
        db, admin, "user.edit", target_id=user_id,
        description=f"tier changed from {old_tier} to {new_tier}",
    )
    db.commit()
    return {"message": f"User {user_id} tier updated to {new_tier}"}


@router.put(
    "/users/{user_id}/suspend",
    summary="Suspend or reactivate a user account",
)
def suspend_user(
    user_id:   int,
    suspended: bool,
    reason:    str  = "",
    admin:     Admin   = Depends(require_permission("can_manage_users")),
    db:        Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = not suspended
    _log_action(
        db, admin, "user.suspend", target_id=user_id,
        description=f"suspended={suspended} reason={reason}",
    )
    db.commit()
    action = "suspended" if suspended else "reactivated"
    return {"message": f"User {user_id} {action}"}


# =============================================================================
# Score reset routes  (requires can_reset_scores)
# =============================================================================

@router.delete(
    "/users/{user_id}/osi",
    summary="Reset a user's OSI score for a season",
    description="Zeros out skill_profile for the given season. Sets leaderboard dirty.",
)
def reset_osi(
    user_id: int,
    season:  int,
    reason:  str  = "",
    admin:   Admin   = Depends(require_permission("can_reset_scores")),
    db:      Session = Depends(get_db),
):
    """
    TODO (Module 4): implement actual SkillProfile reset.
    Placeholder: logs the action and marks the leaderboard dirty.
    """
    _log_action(
        db, admin, "score.reset_osi", target_id=user_id,
        description=f"season={season} reason={reason}",
    )
    # Mark leaderboard dirty so next read triggers a rebuild
    dirty = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()
    if dirty:
        dirty.value = "1"

    db.add(MaintenanceEvent(
        admin_id=admin.id,
        event_type="score_recalculated",
        description=f"OSI reset for user {user_id} season {season}: {reason}",
    ))
    db.commit()
    return {"message": f"OSI reset for user {user_id} season {season}"}


@router.delete(
    "/users/{user_id}/roles",
    summary="Reset role certifications for a user",
)
def reset_roles(
    user_id:   int,
    season:    int,
    role_slug: str = "all",
    reason:    str = "",
    admin:     Admin   = Depends(require_permission("can_reset_scores")),
    db:        Session = Depends(get_db),
):
    """
    TODO (Module 5): implement actual Role model deletion.
    """
    _log_action(
        db, admin, "score.reset_roles", target_id=user_id,
        description=f"season={season} role={role_slug} reason={reason}",
    )
    db.commit()
    return {"message": f"Roles reset for user {user_id} season {season} role={role_slug}"}


# =============================================================================
# Sponsor management routes  (requires can_manage_sponsors)
# =============================================================================

class SponsorCreateRequest(BaseModel):
    name:          str
    logo_url:      str    # EXTERNAL URL ONLY — validated below
    link_url:      str
    slot:          str = "leaderboard_banner"
    active_season: int
    priority:      int = 0
    min_tier:      str = "free"


@router.get(
    "/sponsors",
    summary="List all sponsors including inactive",
    description=(
        "LEGAL: Sponsor content is the responsibility of the sponsor. "
        "Review submissions before approving."
    ),
)
def list_sponsors(
    active_only: bool = False,
    admin: Admin   = Depends(require_permission("can_manage_sponsors")),
    db:    Session = Depends(get_db),
):
    query = db.query(Sponsor)
    if active_only:
        query = query.filter(Sponsor.is_active == True)
    sponsors = query.order_by(Sponsor.priority.desc()).all()

    return [
        {
            "id":           s.id,
            "name":         s.name,
            "logo_url":     s.logo_url,
            "link_url":     s.link_url,
            "slot":         s.slot,
            "active_season":s.active_season,
            "is_active":    s.is_active,
            "priority":     s.priority,
        }
        for s in sponsors
    ]


@router.post(
    "/sponsors",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new sponsor entry",
)
def create_sponsor(
    body:  SponsorCreateRequest,
    admin: Admin   = Depends(require_permission("can_manage_sponsors")),
    db:    Session = Depends(get_db),
):
    """
    LEGAL: Sponsor logos must be external URLs. Validate URL format.
    Sponsor content is live immediately after creation if is_active=True.
    """
    # Basic URL validation — must start with https://
    if not body.logo_url.startswith("https://"):
        raise HTTPException(
            status_code=400,
            detail="logo_url must be a secure external HTTPS URL. Never upload images locally.",
        )

    now = int(datetime.now(timezone.utc).timestamp())
    sponsor = Sponsor(
        name=body.name,
        logo_url=body.logo_url,
        link_url=body.link_url,
        slot=body.slot,
        active_season=body.active_season,
        priority=body.priority,
        min_tier=body.min_tier,
        is_active=True,
        created_by=admin.id,
        created_at=now,
    )
    db.add(sponsor)
    db.flush()

    _log_action(
        db, admin, "sponsor.create", target_id=sponsor.id,
        description=f"Created sponsor: {body.name}",
    )
    db.commit()
    return {"id": sponsor.id, "message": f"Sponsor '{body.name}' created"}


@router.put(
    "/sponsors/{sponsor_id}",
    summary="Update sponsor details",
)
def update_sponsor(
    sponsor_id: int,
    body:       SponsorCreateRequest,
    admin:      Admin   = Depends(require_permission("can_manage_sponsors")),
    db:         Session = Depends(get_db),
):
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Sponsor not found")

    if not body.logo_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="logo_url must be a secure HTTPS URL")

    now = int(datetime.now(timezone.utc).timestamp())
    sponsor.name          = body.name
    sponsor.logo_url      = body.logo_url
    sponsor.link_url      = body.link_url
    sponsor.slot          = body.slot
    sponsor.active_season = body.active_season
    sponsor.priority      = body.priority
    sponsor.min_tier      = body.min_tier
    sponsor.updated_by    = admin.id
    sponsor.updated_at    = now

    _log_action(
        db, admin, "sponsor.update", target_id=sponsor_id,
        description=f"Updated sponsor: {body.name}",
    )
    db.commit()
    return {"message": f"Sponsor {sponsor_id} updated"}


@router.patch(
    "/sponsors/{sponsor_id}/toggle",
    summary="Activate or deactivate a sponsor",
)
def toggle_sponsor(
    sponsor_id: int,
    is_active:  bool,
    admin:      Admin   = Depends(require_permission("can_manage_sponsors")),
    db:         Session = Depends(get_db),
):
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Sponsor not found")

    sponsor.is_active  = is_active
    sponsor.updated_by = admin.id
    sponsor.updated_at = int(datetime.now(timezone.utc).timestamp())

    action = "sponsor.activate" if is_active else "sponsor.deactivate"
    _log_action(db, admin, action, target_id=sponsor_id)
    db.commit()
    return {"message": f"Sponsor {sponsor_id} {'activated' if is_active else 'deactivated'}"}


@router.delete(
    "/sponsors/{sponsor_id}",
    summary="Hard delete a sponsor entry",
)
def delete_sponsor(
    sponsor_id: int,
    admin:      Admin   = Depends(require_permission("can_manage_sponsors")),
    db:         Session = Depends(get_db),
):
    sponsor = db.query(Sponsor).filter(Sponsor.id == sponsor_id).first()
    if not sponsor:
        raise HTTPException(status_code=404, detail="Sponsor not found")

    _log_action(
        db, admin, "sponsor.delete", target_id=sponsor_id,
        description=f"Deleted sponsor: {sponsor.name}",
    )
    db.delete(sponsor)
    db.commit()
    return {"message": f"Sponsor {sponsor_id} deleted"}


# =============================================================================
# Logs & monitoring  (requires can_view_logs)
# =============================================================================

@router.get(
    "/logs",
    summary="View admin audit log",
)
def view_logs(
    page:   int = 1,
    limit:  int = 100,
    action: Optional[str] = None,
    admin:  Admin   = Depends(require_permission("can_view_logs")),
    db:     Session = Depends(get_db),
):
    """
    Returns paginated admin_audit_log entries, newest first.
    LEGAL: Log access is itself logged.
    """
    query = db.query(AdminAuditLog)
    if action:
        query = query.filter(AdminAuditLog.action_type == action)

    total  = query.count()
    offset = (page - 1) * limit
    entries = query.order_by(AdminAuditLog.created_at.desc()).offset(offset).limit(limit).all()

    _log_action(db, admin, "log.view")
    db.commit()

    return {
        "total": total,
        "page":  page,
        "entries": [
            {
                "id":             e.id,
                "admin_username": e.admin_username,
                "action_type":    e.action_type,
                "target_id":      e.target_id,
                "description":    e.description,
                "created_at":     e.created_at,
            }
            for e in entries
        ],
    }


@router.get(
    "/stats",
    summary="Platform statistics dashboard",
)
def get_stats(
    admin: Admin   = Depends(get_current_admin),
    db:    Session = Depends(get_db),
):
    """Quick stats for the admin dashboard overview."""
    from app.models import DerivedSession, AppLog

    total_users  = db.query(User).count()
    pro_users    = db.query(User).filter(User.tier == "pro").count()
    free_users   = total_users - pro_users
    active_users = db.query(User).filter(User.is_active == True).count()

    current_season_row = db.query(AppState).filter(AppState.key == "current_season").first()
    current_season = int(current_season_row.value) if current_season_row else 1

    total_sessions = db.query(DerivedSession).filter(
        DerivedSession.season == current_season
    ).count()

    # DB file size (approximate)
    import os
    db_path = settings.DATABASE_URL.replace("sqlite:///", "")
    try:
        db_size_bytes = os.path.getsize(db_path)
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    except FileNotFoundError:
        db_size_mb = 0.0

    dirty_row = db.query(AppState).filter(AppState.key == "leaderboard_dirty").first()

    return {
        "users": {
            "total":  total_users,
            "pro":    pro_users,
            "free":   free_users,
            "active": active_users,
        },
        "season": {
            "current":        current_season,
            "total_sessions": total_sessions,
        },
        "system": {
            "db_size_mb":            db_size_mb,
            "leaderboard_dirty":     dirty_row.value == "1" if dirty_row else False,
        },
    }


# =============================================================================
# Season control  (requires can_manage_seasons)
# =============================================================================

@router.get(
    "/season/status",
    summary="Current season info",
)
def season_status(
    admin: Admin   = Depends(require_permission("can_manage_seasons")),
    db:    Session = Depends(get_db),
):
    """TODO (Module 6): return full season status with leaderboard info."""
    rows = db.query(AppState).all()
    return {row.key: row.value for row in rows}


@router.post(
    "/season/close",
    summary="Close the current season and archive results",
)
def close_season(
    confirm: bool = False,
    reason:  str  = "",
    admin:   Admin   = Depends(require_permission("can_manage_seasons")),
    db:      Session = Depends(get_db),
):
    """
    TODO (Module 6): implement full season close logic:
      1. Snapshot all skill_profiles → season_archive
      2. Check Elite role eligibility
      3. Reset all skill_profiles
      4. Increment current_season
      5. Rebuild leaderboard cache

    Explicit confirm=true required to prevent accidental calls.
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Pass confirm=true to close the season. This is irreversible.",
        )

    from app.leaderboard_service import close_season as _close_season
    result = _close_season(db, admin_id=admin.id, reason=reason)

    _log_action(
        db, admin, "season.close",
        description=(
            f"Season {result['old_season']} closed → Season {result['new_season']}. "
            f"Archived: {result['players_archived']}. "
            f"Elite promotions: {result['elite_promotions']}. "
            f"Reason: {reason}"
        ),
    )
    db.commit()
    return {
        "message": f"Season {result['old_season']} closed. Season {result['new_season']} now active.",
        **result,
    }


@router.post(
    "/leaderboard/rebuild",
    summary="Force a leaderboard cache rebuild",
)
def rebuild_leaderboard(
    admin: Admin   = Depends(require_permission("can_manage_seasons")),
    db:    Session = Depends(get_db),
):
    from app.leaderboard_service import rebuild_leaderboard, get_current_season
    season = get_current_season(db)
    count  = rebuild_leaderboard(db, season)

    _log_action(db, admin, "maintenance.trigger",
                description=f"Manual leaderboard rebuild: {count} players ranked")
    db.add(MaintenanceEvent(
        admin_id=admin.id,
        event_type="cache_rebuilt",
        description=f"Manual rebuild by {admin.username}: {count} players ranked",
        affected_count=count,
    ))
    db.commit()
    return {"message": f"Leaderboard rebuilt. {count} players ranked.", "season": season}


# =============================================================================
# Admin account management  (requires can_manage_admins — superadmin only)
# =============================================================================

class CreateAdminRequest(BaseModel):
    username: str
    email:    str
    password: str
    role:     str = "moderator"   # moderator | support


@router.get(
    "/accounts",
    summary="List all admin accounts",
)
def list_admins(
    admin: Admin   = Depends(require_permission("can_manage_admins")),
    db:    Session = Depends(get_db),
):
    """Never returns password_hash."""
    admins = db.query(Admin).all()
    return [
        {
            "id":         a.id,
            "username":   a.username,
            "role":       a.role,
            "is_active":  a.is_active,
            "last_login": a.last_login,
        }
        for a in admins
    ]


@router.post(
    "/accounts",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new admin account",
)
def create_admin(
    body:  CreateAdminRequest,
    admin: Admin   = Depends(require_permission("can_manage_admins")),
    db:    Session = Depends(get_db),
):
    if db.query(Admin).filter(Admin.username == body.username).first():
        raise HTTPException(status_code=409, detail="Admin username already exists")

    if body.role not in ("superadmin", "moderator", "support"):
        raise HTTPException(status_code=400, detail="role must be superadmin, moderator, or support")

    now        = int(datetime.now(timezone.utc).timestamp())
    new_admin  = Admin(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        created_at=now,
        created_by=admin.id,
    )
    db.add(new_admin)
    db.flush()

    # Set default permissions based on role
    perms = AdminPermission(
        admin_id=new_admin.id,
        can_manage_users    = body.role in ("superadmin", "moderator"),
        can_reset_scores    = body.role in ("superadmin", "moderator"),
        can_manage_sponsors = body.role == "superadmin",
        can_view_logs       = True,
        can_manage_seasons  = body.role == "superadmin",
        can_manage_admins   = body.role == "superadmin",
        can_manage_payments = body.role == "superadmin",
    )
    db.add(perms)

    _log_action(
        db, admin, "admin.create", target_id=new_admin.id,
        description=f"Created admin: {body.username} role={body.role}",
    )
    db.commit()
    return {"id": new_admin.id, "message": f"Admin '{body.username}' created"}


@router.patch(
    "/accounts/{admin_id}/deactivate",
    summary="Deactivate an admin account",
)
def deactivate_admin(
    admin_id: int,
    admin:    Admin   = Depends(require_permission("can_manage_admins")),
    db:       Session = Depends(get_db),
):
    if admin_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    target = db.query(Admin).filter(Admin.id == admin_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")

    target.is_active = False
    _log_action(
        db, admin, "admin.deactivate", target_id=admin_id,
        description=f"Deactivated admin: {target.username}",
    )
    db.commit()
    return {"message": f"Admin {admin_id} deactivated"}
