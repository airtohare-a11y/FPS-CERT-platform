# =============================================================================
# app/config.py
# Central configuration. Every tunable value lives here.
# Values are read from environment variables — never hardcode secrets.
# On Replit: set secrets in the Secrets tab (🔒), not in this file.
# Locally:   copy .env.example → .env and fill in values.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; Replit Secrets tab takes priority


class Settings:

    # ── Security ──────────────────────────────────────────────────────────────
    # Generate a strong key:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "CHANGE_ME_BEFORE_ANY_DEPLOYMENT")
    ALGORITHM: str = "HS256"
    # Token lifetime in minutes. 1440 = 24 hours.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440")
    )

    # ── Database ──────────────────────────────────────────────────────────────
    # SQLite stored in project root. Replit persists this across restarts.
    # To switch to PostgreSQL later, just change this URL — no other code changes.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")

    # ── Stripe — Phase 7 placeholder ─────────────────────────────────────────
    # Leave empty until the payments module is built.
    # NEVER commit real keys to source control.
    STRIPE_SECRET_KEY: str      = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str  = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRO_PRICE_ID: str    = os.getenv("STRIPE_PRO_PRICE_ID", "")
    STRIPE_COACH_PRICE_ID: str  = os.getenv("STRIPE_COACH_PRICE_ID", "")

    # ── Pricing ───────────────────────────────────────────────────────────────
    PRO_MONTHLY_PRICE_USD: float = float(os.getenv("PRO_MONTHLY_PRICE_USD", "7.00"))

    # ── Application metadata ──────────────────────────────────────────────────
    APP_NAME: str    = "MECHgg Skill Certification Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool      = os.getenv("DEBUG", "false").lower() == "true"

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Replace "*" with your Replit domain before going live.
    # e.g. "https://myapp.replit.app"
    ALLOWED_ORIGINS: list = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    # ── Season ────────────────────────────────────────────────────────────────
    CURRENT_SEASON: int = int(os.getenv("CURRENT_SEASON", "1"))

    # ── Tier limits ───────────────────────────────────────────────────────────
    # Free tier: 1 lifetime upload. Enforced in session upload middleware.
    FREE_TIER_MAX_UPLOADS: int = 1

    # ── Scoring weights (locked — adjustable here without touching scoring.py)
    # These are used by scoring.py (Module 2). Defined here so they can be
    # overridden via env vars during testing without code changes.
    W_REACTION:    float = float(os.getenv("W_REACTION",    "0.15"))
    W_ACCURACY:    float = float(os.getenv("W_ACCURACY",    "0.20"))
    W_ENG_EFF:     float = float(os.getenv("W_ENG_EFF",     "0.15"))
    W_CONSISTENCY: float = float(os.getenv("W_CONSISTENCY", "0.15"))
    W_CQE:         float = float(os.getenv("W_CQE",         "0.10"))
    W_LRE:         float = float(os.getenv("W_LRE",         "0.10"))
    W_DPI:         float = float(os.getenv("W_DPI",         "0.15"))

    # ── Scoring ceilings ──────────────────────────────────────────────────────
    REACTION_MS_FLOOR: float = float(os.getenv("REACTION_MS_FLOOR", "80.0"))
    REACTION_MS_CEIL:  float = float(os.getenv("REACTION_MS_CEIL",  "1000.0"))
    DPS_CEILING:       float = float(os.getenv("DPS_CEILING",       "20.0"))
    MAX_FIRE_RATE:     float = float(os.getenv("MAX_FIRE_RATE",      "15.0"))
    MAX_KILL_RATE:     float = float(os.getenv("MAX_KILL_RATE",      "0.5"))

    # ── Role unlock thresholds ────────────────────────────────────────────────
    CANDIDATE_MIN_SESSIONS:  int   = int(os.getenv("CANDIDATE_MIN_SESSIONS",  "5"))
    CERTIFIED_MIN_SESSIONS:  int   = int(os.getenv("CERTIFIED_MIN_SESSIONS",  "10"))
    CERTIFIED_STABILITY_STD: float = float(os.getenv("CERTIFIED_STABILITY_STD", "5.0"))
    ROLE_INACTIVITY_DAYS:    int   = int(os.getenv("ROLE_INACTIVITY_DAYS",    "60"))


# Single shared instance — used everywhere:
#   from app.config import settings
settings = Settings()

# Fail loudly at startup if the secret key is still the default.
# This prevents accidental deployment with an insecure key.
if settings.SECRET_KEY == "CHANGE_ME_BEFORE_ANY_DEPLOYMENT":
    import warnings
    warnings.warn(
        "⚠️  SECRET_KEY is not set. Set it in your .env file or Replit Secrets tab "
        "before deploying to production.",
        stacklevel=2,
    )
