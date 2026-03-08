# =============================================================================
# app/games.py
# Game registry — the list of supported games users can select when uploading.
# =============================================================================

from fastapi import APIRouter
from app.models import GAME_REGISTRY

router = APIRouter()

@router.get("/", summary="List all supported games grouped by category")
def list_games():
    games = []
    for gid, info in GAME_REGISTRY.items():
        games.append({
            "ranked":   info.get("ranked", True) and info.get("category") in {"fps","racing","sports","strategy","fighting"},
            "id":       gid,
            "name":     info["name"],
            "category": info["category"],
            "style":    info["style"],
            "emoji":    info["emoji"],
        })
    return {"games": games}
