# =============================================================================
# app/skillcard.py
# Module 9 — SVG Skill Card Generator
#
# GET /skillcard/{user_id}   — public SVG card (any user)
# GET /skillcard/me          — own card (authenticated)
#
# Returns a standalone SVG document — viewable in browser, embeddable in
# Discord bios, forum signatures, Reddit posts.
#
# Design: dark military terminal card matching the frontend aesthetic.
# No external dependencies — pure string SVG generation.
# Font stack: system monospace (no Google Fonts CDN dependency in SVG).
# =============================================================================

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import LeaderboardCache, Role, SkillProfile, User, CertLevelEnum
from app.scoring import percentile_to_badge, ROLE_DEFINITIONS

router = APIRouter()


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _get_profile_data(user_id: int, db: Session) -> dict:
    """Gather all data needed to render a skill card."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get current season from app_state
    from app.models import AppState
    state = db.query(AppState).filter(AppState.key == "current_season").first()
    season = int(state.value) if state else 1

    profile = (
        db.query(SkillProfile)
        .filter(SkillProfile.user_id == user_id, SkillProfile.season == season)
        .first()
    )

    lb_row = (
        db.query(LeaderboardCache)
        .filter(LeaderboardCache.user_id == user_id, LeaderboardCache.season == season)
        .first()
    )

    roles = (
        db.query(Role)
        .filter(
            Role.user_id == user_id,
            Role.season  == season,
            Role.is_active == True,
        )
        .order_by(Role.cert_level.desc())
        .limit(3)
        .all()
    )

    osi         = profile.osi if profile else 0.0
    peak_osi    = profile.peak_osi if profile else 0.0
    ranked_sess = profile.ranked_sessions if profile else 0
    rank        = lb_row.rank if lb_row else None
    pct         = lb_row.percentile if lb_row else None
    badge       = lb_row.tier_badge if lb_row else None
    metrics     = {
        "reaction":    profile.avg_reaction    if profile else 0,
        "accuracy":    profile.avg_accuracy    if profile else 0,
        "eng_eff":     profile.avg_eng_eff     if profile else 0,
        "consistency": profile.avg_consistency if profile else 0,
        "cqe":         profile.avg_cqe         if profile else 0,
        "lre":         profile.avg_lre         if profile else 0,
        "dpi":         profile.avg_dpi         if profile else 0,
    }

    cert_levels = {
        CertLevelEnum.elite:     "ELITE",
        CertLevelEnum.certified: "CERT",
        CertLevelEnum.candidate: "CAND",
    }

    role_labels = [
        f"{ROLE_DEFINITIONS.get(str(r.role_slug),{}).get('display', str(r.role_slug))[:12].upper()} · {cert_levels.get(r.cert_level,'?')}"
        for r in roles
    ]

    return {
        "username":    user.username,
        "tier":        user.tier,
        "season":      season,
        "osi":         round(osi, 1),
        "peak_osi":    round(peak_osi, 1),
        "ranked_sess": ranked_sess,
        "rank":        rank,
        "percentile":  round(pct, 1) if pct is not None else None,
        "badge":       badge,
        "metrics":     metrics,
        "roles":       role_labels,
    }


def _badge_color(badge: str | None) -> str:
    return {
        "apex":   "#f0c040",
        "elite":  "#f0a500",
        "gold":   "#e8c840",
        "silver": "#a8b8c8",
        "bronze": "#b87040",
    }.get(badge or "", "#3d4f62")


def _metric_bar(label: str, value: float, y: int, bar_width: int = 160) -> str:
    """Render a single metric bar row as SVG elements."""
    pct   = min(100, max(0, value))
    fill  = int(bar_width * pct / 100)
    color = "#f0a500" if pct >= 70 else "#7a8799" if pct >= 40 else "#3d4f62"
    return f"""
  <text x="16" y="{y}" font-family="monospace" font-size="8" fill="#7a8799" letter-spacing="0.5">{label[:8].upper()}</text>
  <rect x="72" y="{y-8}" width="{bar_width}" height="6" rx="1" fill="#1c222b"/>
  <rect x="72" y="{y-8}" width="{fill}" height="6" rx="1" fill="{color}"/>
  <text x="{72+bar_width+6}" y="{y}" font-family="monospace" font-size="8" fill="{color}">{int(pct)}</text>"""


def render_svg(data: dict) -> str:
    """Generate the complete skill card SVG."""
    W, H = 320, 240
    badge_col    = _badge_color(data["badge"])
    osi_display  = f"{data['osi']:.0f}"
    rank_display = f"#{data['rank']}" if data["rank"] else "—"
    pct_display  = f"{data['percentile']}%" if data["percentile"] is not None else "—"
    tier_label   = (data["badge"] or "UNRANKED").upper()

    # Metric bars (7 metrics, stacked)
    metric_items = [
        ("REACT",  data["metrics"]["reaction"]),
        ("ACCUR",  data["metrics"]["accuracy"]),
        ("ENG EF", data["metrics"]["eng_eff"]),
        ("CONSIS", data["metrics"]["consistency"]),
        ("CQE",    data["metrics"]["cqe"]),
        ("LRE",    data["metrics"]["lre"]),
        ("DPI",    data["metrics"]["dpi"]),
    ]
    bars_svg = ""
    bar_y    = 128
    for label, val in metric_items:
        bars_svg += _metric_bar(label, val, bar_y)
        bar_y += 13

    # Roles (up to 3)
    roles_svg = ""
    for i, role_label in enumerate(data["roles"][:3]):
        rx = 16 + i * 102
        roles_svg += f"""
  <rect x="{rx}" y="222" width="96" height="14" rx="1" fill="#1c222b" stroke="#263040" stroke-width="0.5"/>
  <text x="{rx+48}" y="232" font-family="monospace" font-size="7" fill="{badge_col}" text-anchor="middle" letter-spacing="0.3">{role_label}</text>"""

    # OSI ring (simplified arc for SVG)
    osi_pct  = min(1.0, data["osi"] / 1000.0)
    r        = 28
    cx, cy   = 258, 88
    circ     = 2 * 3.14159 * r
    dashoff  = circ * (1 - osi_pct)
    ring_svg = f"""
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#1c222b" stroke-width="5"/>
  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{badge_col}" stroke-width="5"
    stroke-dasharray="{circ:.1f}" stroke-dashoffset="{dashoff:.1f}"
    stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>
  <text x="{cx}" y="{cy+4}" font-family="monospace" font-size="12" font-weight="bold"
    fill="{badge_col}" text-anchor="middle">{osi_display}</text>
  <text x="{cx}" y="{cy+16}" font-family="monospace" font-size="7" fill="#3d4f62"
    text-anchor="middle">OSI</text>"""

    tier_marker_x = W - 16 - len(tier_label) * 6.5
    no_roles_msg  = "" if data["roles"] else f"""
  <text x="16" y="232" font-family="monospace" font-size="7" fill="#3d4f62">Upload ranked sessions to unlock roles</text>"""

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0c0e10"/>
      <stop offset="100%" stop-color="#080909"/>
    </linearGradient>
    <linearGradient id="ambg" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="{badge_col}" stop-opacity="0.8"/>
      <stop offset="100%" stop-color="{badge_col}" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <!-- Background -->
  <rect width="{W}" height="{H}" fill="url(#bg)" rx="4"/>
  <rect width="{W}" height="{H}" fill="none" stroke="#1f2730" stroke-width="1" rx="4"/>

  <!-- Top accent bar -->
  <rect width="{W}" height="3" fill="url(#ambg)" rx="2"/>

  <!-- Grid texture (subtle) -->
  <pattern id="grid" width="16" height="16" patternUnits="userSpaceOnUse">
    <path d="M 16 0 L 0 0 0 16" fill="none" stroke="#ffffff" stroke-width="0.3" opacity="0.03"/>
  </pattern>
  <rect width="{W}" height="{H}" fill="url(#grid)"/>

  <!-- Crosshair decoration -->
  <g opacity="0.06" transform="translate(268,{H-30})">
    <circle r="12" fill="none" stroke="{badge_col}" stroke-width="1"/>
    <circle r="3" fill="{badge_col}"/>
    <line x1="0" y1="-16" x2="0" y2="-13" stroke="{badge_col}" stroke-width="1"/>
    <line x1="0" y1="13" x2="0" y2="16" stroke="{badge_col}" stroke-width="1"/>
    <line x1="-16" y1="0" x2="-13" y2="0" stroke="{badge_col}" stroke-width="1"/>
    <line x1="13" y1="0" x2="16" y2="0" stroke="{badge_col}" stroke-width="1"/>
  </g>

  <!-- Platform label -->
  <text x="16" y="18" font-family="monospace" font-size="7" fill="#3d4f62" letter-spacing="2">MECHgg SKILL CARD</text>
  <text x="{W-16}" y="18" font-family="monospace" font-size="7" fill="#3d4f62" text-anchor="end">S{data['season']}</text>

  <!-- Divider -->
  <line x1="16" y1="24" x2="{W-16}" y2="24" stroke="#1f2730" stroke-width="0.5"/>

  <!-- Username -->
  <text x="16" y="48" font-family="monospace" font-size="18" font-weight="bold"
    fill="#dde2ec" letter-spacing="0.5">{data['username'][:16]}</text>

  <!-- Tier badge -->
  <rect x="{int(tier_marker_x)-6}" y="36" width="{int(len(tier_label)*6.5)+12}" height="13" rx="1"
    fill="{badge_col}" fill-opacity="0.12" stroke="{badge_col}" stroke-width="0.5" stroke-opacity="0.4"/>
  <text x="{W-16}" y="46" font-family="monospace" font-size="7" fill="{badge_col}"
    text-anchor="end" letter-spacing="1">{tier_label}</text>

  <!-- Stats row -->
  <text x="16" y="66" font-family="monospace" font-size="7" fill="#3d4f62" letter-spacing="0.5">RANK</text>
  <text x="16" y="78" font-family="monospace" font-size="12" font-weight="bold" fill="{badge_col}">{rank_display}</text>

  <text x="70" y="66" font-family="monospace" font-size="7" fill="#3d4f62" letter-spacing="0.5">PERCENTILE</text>
  <text x="70" y="78" font-family="monospace" font-size="12" font-weight="bold" fill="#dde2ec">{pct_display}</text>

  <text x="140" y="66" font-family="monospace" font-size="7" fill="#3d4f62" letter-spacing="0.5">SESSIONS</text>
  <text x="140" y="78" font-family="monospace" font-size="12" font-weight="bold" fill="#dde2ec">{data['ranked_sess']}</text>

  <!-- Divider -->
  <line x1="16" y1="90" x2="230" y2="90" stroke="#1f2730" stroke-width="0.5"/>

  <!-- Section label -->
  <text x="16" y="104" font-family="monospace" font-size="7" fill="#3d4f62" letter-spacing="1">SKILL METRICS</text>

  <!-- Metric bars -->
  {bars_svg}

  <!-- OSI ring (right column) -->
  {ring_svg}

  <!-- Peak OSI -->
  <text x="{cx}" y="{cy+34}" font-family="monospace" font-size="7" fill="#3d4f62" text-anchor="middle">PEAK</text>
  <text x="{cx}" y="{cy+45}" font-family="monospace" font-size="9" fill="#7a8799" text-anchor="middle">{data['peak_osi']:.0f}</text>

  <!-- Bottom divider -->
  <line x1="16" y1="216" x2="{W-16}" y2="216" stroke="#1f2730" stroke-width="0.5"/>

  <!-- Role certifications -->
  {roles_svg}
  {no_roles_msg}
</svg>"""

    return svg


# =============================================================================
# Routes
# =============================================================================

@router.get(
    "/me",
    summary="Your skill card (SVG)",
    description="Returns a standalone SVG skill card for the authenticated user.",
    response_class=Response,
)
def my_skill_card(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    data = _get_profile_data(current_user.id, db)
    svg  = render_svg(data)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=300",   # 5-min cache
            "X-Card-Season": str(data["season"]),
        },
    )


@router.get(
    "/{user_id}",
    summary="Public skill card (SVG)",
    description=(
        "Returns a public SVG skill card for any user by ID. "
        "Embeddable in Discord, forum signatures, Reddit posts. "
        "Cached for 5 minutes. "
        "LEGAL: Displays only derived metrics. No raw gameplay data.\n\n"
        "Example embed: `![Skill Card](https://yoursite.com/skillcard/42)`"
    ),
    response_class=Response,
)
def public_skill_card(
    user_id: int,
    db:      Session = Depends(get_db),
):
    data = _get_profile_data(user_id, db)
    svg  = render_svg(data)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Card-Season": str(data["season"]),
        },
    )
