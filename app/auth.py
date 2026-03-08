# =============================================================================
# app/auth.py
# User authentication — registration, login, JWT creation and verification.
#
# JWT design:
#   User tokens carry  { "sub": user_id, "type": "user" }
#   Admin tokens carry { "sub": admin_id, "type": "admin" }
#   The "type" claim is the hard boundary between the two auth systems.
#   A user token presented to an admin route is rejected at the middleware
#   level before any handler logic runs.
#
# Passwords:
#   bcrypt via passlib — never store or log plaintext passwords.
#   Minimum 8 characters enforced at registration.
# =============================================================================

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app import notifications
from app.database import get_db
from app.models import User, UserConsentLog, AppState

router = APIRouter()

# ── Password hashing ──────────────────────────────────────────────────────────
# bcrypt with 12 rounds — good balance of security and speed on Replit's CPU.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(user_id: int, token_type: str = "user") -> str:
    """
    Create a signed JWT for a user or admin.

    token_type must be "user" or "admin". This is enforced in
    get_current_user() and get_current_admin() so the two auth
    systems remain completely separate.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub":  str(user_id),
        "type": token_type,
        "exp":  expire,
        "iat":  datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT. Returns the payload dict.
    Raises HTTPException 401 on any failure.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if not payload.get("sub"):
            raise ValueError("missing sub claim")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI security dependency ───────────────────────────────────────────────
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve Bearer token → User ORM object.
    Rejects admin tokens (type != "user").
    Usage: user: User = Depends(get_current_user)
    """
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "user":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type for this endpoint",
        )

    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )
    return user


def require_pro(user: User = Depends(get_current_user)) -> User:
    """
    Gate a route to Pro-tier users only.
    Raises HTTP 403 for Free users with an upgrade prompt.
    Usage: user: User = Depends(require_pro)
    """
    if user.tier != "pro":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "PRO_REQUIRED",
                "message": "This feature requires a Pro subscription ($7/month).",
                "upgrade_url": "/payments/subscribe",
            },
        )
    return user


# ── Request / Response schemas ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:  str
    email:     EmailStr
    password:  str
    tos_accepted: bool   # must be True — frontend checkbox required

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        import re
        v = v.strip()
        if not re.match(r"^[A-Za-z0-9_\-]{3,40}$", v):
            raise ValueError(
                "Username must be 3–40 characters: letters, numbers, _ or -"
            )
        return v

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("tos_accepted")
    @classmethod
    def must_accept_tos(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "You must accept the Terms of Service to register"
            )
        return v


class LoginRequest(BaseModel):
    username: str   # can also be email — we check both
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    username:     str
    tier:         str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    description=(
        "Creates a user account. Requires ToS acceptance. "
        "Returns a JWT for immediate use. "
        "Legal notice: by registering you agree to our Terms of Service."
    ),
)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """
    Registration flow:
      1. Validate input (Pydantic — see schema above)
      2. Check username + email uniqueness
      3. Hash password (bcrypt — plaintext never stored)
      4. Create User row
      5. Record ToS consent in user_consent_log
      6. Return JWT

    LEGAL: ToS acceptance is required and recorded with IP address.
    """
    # Duplicate checks
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Get current disclaimer version for consent log
    version_row = db.query(AppState).filter(AppState.key == "disclaimer_version").first()
    disclaimer_version = version_row.value if version_row else "1.0.0"

    # Create user
    now = int(datetime.now(timezone.utc).timestamp())
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        tier="free",
        is_active=True,
        created_at=now,
        tos_version_accepted=disclaimer_version,
        tos_accepted_at=now,
    )
    db.add(user)
    db.flush()  # get user.id before consent log insert

    # Record ToS consent — immutable, legally significant
    # LEGAL: This row proves the user accepted the ToS at registration.
    consent = UserConsentLog(
        user_id=user.id,
        consent_type="terms_of_service",
        disclaimer_version=disclaimer_version,
        accepted_at=now,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(consent)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, token_type="user")
    return AuthResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        tier=user.tier,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Login and receive a JWT",
)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """
    Login flow:
      1. Find user by username OR email
      2. Verify password (bcrypt constant-time compare)
      3. Check account is active
      4. Update last_login timestamp
      5. Return JWT

    Uses constant-time password comparison to prevent timing attacks.
    Both username and email are accepted in the username field.
    """
    # Accept username or email in the username field
    user = (
        db.query(User).filter(User.username == body.username).first()
        or db.query(User).filter(User.email == body.username).first()
    )

    # Use a generic error message — never reveal whether username exists
    invalid_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
    )

    if not user:
        # Still verify a dummy hash to prevent timing attacks
        verify_password(body.password, "$2b$12$DUMMYHASHTOPREVENTTIMINGATTACKS000000000000")
        raise invalid_creds

    if not verify_password(body.password, user.password_hash):
        raise invalid_creds

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account suspended. Contact support.",
        )

    # Update last login timestamp
    user.last_login = int(datetime.now(timezone.utc).timestamp())
    db.commit()

    token = create_access_token(user.id, token_type="user")
    return AuthResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        tier=user.tier,
    )


@router.get(
    "/me",
    summary="Get current user profile",
)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's basic profile."""
    return {
        "id":             current_user.id,
        "username":       current_user.username,
        "email":          current_user.email,
        "tier":           current_user.tier,
        "total_uploads":  current_user.total_uploads,
        "created_at":     current_user.created_at,
        "last_login":     current_user.last_login,
    }


@router.delete(
    "/account",
    summary="Delete own account and all associated data",
    description=(
        "Hard deletes the user account and all associated data. "
        "CASCADE DELETE on the database handles sessions, roles, and consent log. "
        "GDPR Article 17 — right to erasure."
    ),
)
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    GDPR right to erasure.
    Deletes user row. All related rows cascade automatically
    (sessions, skill_profile, roles, consent_log, subscriptions).
    """
    db.delete(current_user)
    db.commit()
    return {"message": "Account and all associated data deleted."}
