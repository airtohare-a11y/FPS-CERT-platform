from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.database import init_db
from app.auth import router as auth_router
from app.analysis import router as analysis_router
from app.coaching import router as coaching_router
from app.payments import router as payments_router
from app.admin import router as admin_router
from app.ads import router as ads_router
from app.games import router as games_router
from app.legal import router as legal_router
from app.sponsors import router as sponsors_router
from app.sessions import router as sessions_router
from app.leaderboard import router as leaderboard_router
from app.roles import router as roles_router
from app.skillcard import router as skillcard_router
from app.competition import router as competition_router

app = FastAPI(title="MECHgg", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

app.include_router(auth_router,        prefix="/auth")
app.include_router(analysis_router,    prefix="/analysis")
app.include_router(coaching_router,    prefix="/coaching")
app.include_router(payments_router,    prefix="/payments")
app.include_router(admin_router,       prefix="/admin")
app.include_router(ads_router,         prefix="/ads")
app.include_router(games_router,       prefix="/games")
app.include_router(legal_router,       prefix="/legal")
app.include_router(sponsors_router,    prefix="/sponsors")
app.include_router(sessions_router,    prefix="/sessions")
app.include_router(leaderboard_router, prefix="/leaderboard")
app.include_router(roles_router,       prefix="/roles")
app.include_router(skillcard_router,   prefix="/skillcard")
app.include_router(competition_router, prefix="/competition")

app.mount("/css", StaticFiles(directory="static/css"), name="css")
app.mount("/js",  StaticFiles(directory="static/js"),  name="js")

@app.get("/admin-panel")
def admin_panel():
    return FileResponse("static/admin.html")

@app.get("/{full_path:path}")
def spa(full_path: str):
    return FileResponse("static/index.html")
