# =============================================================================
# app/database.py
# SQLAlchemy engine, session factory, and table initialisation.
#
# Why SQLite + WAL mode?
#   SQLite in WAL (Write-Ahead Logging) mode allows concurrent reads while
#   a write is in flight. Without WAL, a session upload would block every
#   leaderboard read. On Replit's single-process deployment, SQLite with
#   WAL is sufficient for 5,000–10,000 users.
#
# Why StaticPool?
#   SQLite performs best with a single reused connection. StaticPool
#   prevents SQLAlchemy from opening new connections for every request,
#   keeping memory and file-handle usage minimal.
# =============================================================================

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from app.config import settings

# ── Force SQLite — Replit injects a postgresql:// DATABASE_URL automatically
# when a Replit DB is present. We use SQLite only. Hardcode it here so
# Replit's environment variable can never break startup with a psycopg2 error.
_DB_URL = "sqlite:///./app.db"

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=settings.DEBUG,
)


# ── WAL mode + performance PRAGMAs ───────────────────────────────────────────
@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous  = NORMAL")
    cursor.execute("PRAGMA cache_size   = -8000")
    cursor.execute("PRAGMA temp_store   = MEMORY")
    cursor.execute("PRAGMA mmap_size    = 67108864")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


# ── Declarative base ──────────────────────────────────────────────────────────
# All ORM model classes inherit from Base.
# Base.metadata holds the complete table registry used by create_all().
Base = declarative_base()


# ── Session factory ───────────────────────────────────────────────────────────
# Produces database sessions for request handlers.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── FastAPI dependency ────────────────────────────────────────────────────────
def get_db():
    """
    Yields one DB session per HTTP request, then closes it.

    Usage in route handlers:
        from app.database import get_db
        from sqlalchemy.orm import Session

        @router.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Table creation ────────────────────────────────────────────────────────────
def init_db() -> None:
    """
    Create all tables registered on Base.metadata.
    Called once at application startup from main.py.

    Model modules must be imported before this function is called so their
    classes register themselves on Base. The import in main.py handles this.

    This is idempotent — safe to call on every startup.
    Existing tables are never dropped or modified.
    """
    # Import all model modules here to ensure they are registered on Base
    # before create_all() runs. Order does not matter — SQLAlchemy resolves
    # foreign key dependencies automatically.
    import app.models  # noqa: F401 — side-effect import registers all models

    Base.metadata.create_all(bind=engine)

    # Seed default app_state rows if they don't already exist.
    _seed_defaults()

    print(f"✅  Database initialised  |  Season {settings.CURRENT_SEASON}")


def _seed_defaults() -> None:
    """
    Insert default configuration rows that the application reads at runtime.
    Uses a separate session so this cannot interfere with request sessions.
    """
    from app.models import AppState, LegalDisclaimer  # imported here to avoid circular imports

    db = SessionLocal()
    try:
        # ── app_state defaults ────────────────────────────────────────────────
        defaults = {
            "current_season":    str(settings.CURRENT_SEASON),
            "leaderboard_dirty": "0",
            "season_end_date":   "2026-03-01",
            "reddit_url":        "",
            "discord_url":       "",
            "disclaimer_version": "1.0.0",
        }
        for key, value in defaults.items():
            existing = db.query(AppState).filter(AppState.key == key).first()
            if not existing:
                db.add(AppState(key=key, value=value))

        # ── seed legal disclaimers if table is empty ──────────────────────────
        # Full disclaimer text is managed in app/legal.py.
        # This just ensures rows exist so /legal/{tag} never returns 404.
        if db.query(LegalDisclaimer).count() == 0:
            _seed_disclaimers(db)

        db.commit()
    finally:
        db.close()


def _seed_disclaimers(db) -> None:
    """
    Insert the initial set of legal disclaimer rows.
    These are the same texts defined in app/legal.py.
    Stored in DB so admins can update them without a code deploy.
    """
    from app.models import LegalDisclaimer
    from datetime import datetime, timezone

    now = int(datetime.now(timezone.utc).timestamp())

    disclaimers = [
        {
            "title": "Game Copyright & Trademark",
            "content": (
                "This platform is not affiliated with, endorsed by, or sponsored by "
                "any video game developer or publisher. All game content is the "
                "property of its respective owners. Users submit gameplay data "
                "voluntarily and at their own discretion."
            ),
            "location_tag": "upload",
        },
        {
            "title": "Competition & Rankings Disclaimer",
            "content": (
                "All competitions are for entertainment purposes and are skill-based. "
                "No gambling is involved. Participants must be of legal age in their "
                "jurisdiction. The platform and its sponsors are not responsible for "
                "financial loss or misuse."
            ),
            "location_tag": "leaderboard",
        },
        {
            "title": "Sponsor & Advertising Disclaimer",
            "content": (
                "Sponsors are independently responsible for their content. "
                "The platform does not endorse or guarantee the accuracy, safety, "
                "or legality of sponsor products or services."
            ),
            "location_tag": "sponsors",
        },
        {
            "title": "Privacy & Metadata Notice",
            "content": (
                "All user data is collected for gameplay analysis only. "
                "No personal data is shared with third parties except as required "
                "for payments or legal compliance. Users consent to the processing "
                "of metadata upon upload."
            ),
            "location_tag": "upload",
        },
        {
            "title": "General Platform Usage",
            "content": (
                "By using this platform, you agree to our Terms of Service and "
                "Privacy Policy. The platform is provided as-is and the developers "
                "are not liable for inaccuracies, technical failures, or any damages."
            ),
            "location_tag": "login",
        },
        {
            "title": "Payment & Subscription Terms",
            "content": (
                "All payments are processed securely. By subscribing, you agree to "
                "the platform's Terms of Service and Refund Policy. The platform is "
                "not responsible for banking or payment processing issues."
            ),
            "location_tag": "payments",
        },
        {
            "title": "Administrator Data Handling",
            "content": (
                "You are accessing user data in your capacity as an administrator. "
                "All actions are logged. User data must be handled in accordance "
                "with the platform's Privacy Policy and applicable data protection laws."
            ),
            "location_tag": "admin",
        },
    ]

    for d in disclaimers:
        db.add(LegalDisclaimer(
            title=d["title"],
            content=d["content"],
            location_tag=d["location_tag"],
            last_updated=now,
        ))
