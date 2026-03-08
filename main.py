# =============================================================================
# main.py
# FastAPI application factory.
#
# Responsibilities:
#   - Creates the FastAPI app instance
#   - Registers all routers with their URL prefixes
#   - Configures CORS middleware
#   - Runs database initialisation on startup
#   - Serves the static frontend from /static
#   - Health check endpoint at /health
#
# Run locally:   uvicorn main:app --reload
# Run on Replit: uvicorn main:app --host 0.0.0.0 --port 8000
# =============================================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db, Base

# ── Import all routers ────────────────────────────────────────────────────────
from app.auth     import router as auth_router
from app.admin    import router as admin_router
from app.payments import router as payments_router
from app.sponsors import router as sponsors_router
from app.legal    import router as legal_router

# ── App factory ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "MECHgg Skill Certification Platform — metadata-only, deterministic scoring, "
        "role certification, seasonal leaderboards. No video. No AI. Pure math.\n\n"
        "**Module 1**: Project scaffold + database schema\n"
        "**Module 2+**: Scoring engine, upload pipeline, leaderboard, roles, frontend"
    ),
    version=settings.APP_VERSION,
    docs_url="/docs",       # Swagger UI
    redoc_url="/redoc",     # ReDoc
    # Add contact/license info before public launch
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# During development: allow all origins.
# Before production: set ALLOWED_ORIGINS in Replit Secrets to your domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup: initialise database ──────────────────────────────────────────────
@app.on_event("startup")
def on_startup() -> None:
    """
    Runs once when the server starts.
    Creates all database tables (idempotent — safe on every restart).
    Seeds default app_state and legal disclaimer rows if they don't exist.
    """
    # Import all model modules so SQLAlchemy registers tables before create_all
    import app.models      # noqa
    import app.coaching    # noqa
    import app.competition # noqa
    init_db()


# ── Route registration ────────────────────────────────────────────────────────
# Each module registers its own APIRouter. URL prefixes are set here,
# not inside the router files, so prefixes are visible in one place.

app.include_router(
    auth_router,
    prefix="/auth",
    tags=["Authentication"],
)

app.include_router(
    admin_router,
    prefix="/admin",
    tags=["Admin"],
)

app.include_router(
    payments_router,
    prefix="/payments",
    tags=["Payments"],
)

app.include_router(
    sponsors_router,
    prefix="/sponsors",
    tags=["Sponsors"],
)

app.include_router(
    legal_router,
    prefix="/legal",
    tags=["Legal"],
)

# ── Placeholder routers for future modules ────────────────────────────────────
# These are imported and registered here so main.py never needs to change
# as modules are built. Each file just needs its router to have routes added.

from fastapi import APIRouter

# Modules 2 & 3: Metadata upload pipeline + scoring engine (COMPLETE)
from app.sessions import router as sessions_router
app.include_router(sessions_router, prefix="/sessions", tags=["Sessions"])

# Module 5: Role read routes (state machine lives in sessions.py — COMPLETE)
from app.roles import router as roles_router
app.include_router(roles_router, prefix="/roles", tags=["Roles"])

# Module 6: Leaderboard + Season close (COMPLETE)
from app.leaderboard import router as leaderboard_router
app.include_router(leaderboard_router, prefix="/leaderboard", tags=["Leaderboard"])

# Module 9: Skill card SVG (COMPLETE)
from app.skillcard import router as skillcard_router
app.include_router(skillcard_router, prefix="/skillcard", tags=["Skill Card"])

# Module 3+4: Video analysis engine
from app.analysis import router as analysis_router
app.include_router(analysis_router, prefix="/analysis", tags=["Analysis"])

# Game registry
from app.games import router as games_router
app.include_router(games_router, prefix="/games", tags=["Games"])

# Module 5: Ranking, coaching, coach profiles, booking, messaging
from app.coaching import router as coaching_router
app.include_router(coaching_router, prefix="/coaching", tags=["Coaching"])

# Module 6: Multi-genre competition system
from app.competition import router as competition_router
app.include_router(competition_router, prefix="/competitions", tags=["Competitions"])

# Module 7: Advertisement slots
from app.ads import router as ads_router
app.include_router(ads_router, prefix="/ads", tags=["Ads"])


# ── Health check ──────────────────────────────────────────────────────────────
@app.get(
    "/health",
    tags=["System"],
    summary="Health check — confirms API is running",
    include_in_schema=True,
)
def health_check():
    """
    Returns basic operational status.
    Used by Replit health monitoring and uptime checkers.
    """
    from app.database import SessionLocal
    from app.models import AppState

    db = SessionLocal()
    try:
        season_row = db.query(AppState).filter(AppState.key == "current_season").first()
        current_season = int(season_row.value) if season_row else 1
        db_ok = True
    except Exception as e:
        current_season = 0
        db_ok = False
    finally:
        db.close()

    return {
        "status":         "ok" if db_ok else "degraded",
        "app":            settings.APP_NAME,
        "version":        settings.APP_VERSION,
        "current_season": current_season,
        "database":       "connected" if db_ok else "error",
        "modules_active":  ["auth", "admin", "payments", "sponsors", "legal",
                            "sessions", "scoring", "roles", "leaderboard",
                            "skillcard", "frontend"],
        "stripe_status":  "stub_ready — set STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET to activate",
    }



# ── Static frontend ───────────────────────────────────────────────────────────
import os
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if os.path.isdir(os.path.join(STATIC_DIR, "css")):
    app.mount("/css", StaticFiles(directory=os.path.join(STATIC_DIR, "css")), name="css")
if os.path.isdir(os.path.join(STATIC_DIR, "js")):
    app.mount("/js",  StaticFiles(directory=os.path.join(STATIC_DIR, "js")),  name="js")

@app.get("/admin-panel", include_in_schema=False)
async def serve_admin():
    return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str = ""):
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"message": "MECHgg API is running."})
