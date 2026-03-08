# MECHgg — Skill Certification Platform

Metadata-only FPS skill ranking. No video. No AI. Pure deterministic math.

Upload a 60-second gameplay metadata segment → get an OSI score → earn role certifications → climb the seasonal leaderboard.

---

## Architecture

```
FastAPI backend + SQLite (WAL mode) + Vanilla JS SPA
```

### 20-table database schema
`users` · `admin_accounts` · `derived_sessions` · `skill_profiles` · `roles` · `role_progress` · `leaderboard_cache` · `season_archive` · `payment_transactions` · `subscriptions` · `sponsors` · `sponsor_contributions` · `disclaimers` · `user_consent_log` · `admin_audit_log` · `app_state` · `legal_templates` · `role_definitions` · `user_metadata` · `session_metadata`

### 41 API routes across 9 routers
`/auth` · `/admin` · `/sessions` · `/roles` · `/leaderboard` · `/payments` · `/sponsors` · `/legal` · `/skillcard`

---

## Modules (all complete)

| # | Module | Status | Files |
|---|--------|--------|-------|
| 1 | Core scaffold | ✅ | config, database, models, auth, admin, payments, sponsors, legal, main, seed_admin |
| 2 | Scoring engine | ✅ | app/scoring.py |
| 3 | Upload pipeline | ✅ | app/sessions.py |
| 4 | Role certifications | ✅ | app/roles.py |
| 5 | Leaderboard + season | ✅ | app/leaderboard.py, app/leaderboard_service.py |
| 6 | Tests | ✅ | tests/test_scoring.py, tests/test_leaderboard.py |
| 7 | Payments (Stripe stubs) | ✅* | app/payments.py |
| 8 | Admin panel backend | ✅ | app/admin.py (21 routes) |
| 9 | Frontend + skillcard | ✅ | static/, app/skillcard.py |

*Stripe: set `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` in environment to activate.

---

## Quick Start (Replit)

1. Upload this zip to Replit (Python template)
2. Set secrets in **Replit Secrets** tab:
   ```
   SECRET_KEY=<random 32+ chars>
   STRIPE_SECRET_KEY=sk_live_...        # optional — enables payments
   STRIPE_WEBHOOK_SECRET=whsec_...     # optional — enables webhooks
   STRIPE_PRO_PRICE_ID=price_...       # optional — your Stripe price ID
   ```
3. Run: `uvicorn main:app --host 0.0.0.0 --port 8000`
4. Create first admin: `python seed_admin.py`
5. Visit `/docs` for Swagger UI, `/` for the frontend SPA

---

## OSI Scoring

**OSI = Overall Skill Index (0–1000)**

```
OSI = Σ(metric_i × weight_i) × 1000
```

| Metric | Description | Weight |
|--------|-------------|--------|
| Reaction | Linear map: 80ms→100, 1000ms→0 | 15% |
| Accuracy | shots_hit / shots_fired | 20% |
| Engagement Efficiency | (kills + 0.3×assists) per 10 shots | 15% |
| Consistency | 1 − CV of per-round scores | 15% |
| Close-Quarters Efficiency | close_kills / close_engagements | 10% |
| Long-Range Efficiency | long_kills / long_engagements | 10% |
| Damage Pressure Index | damage_dealt / (duration × 20 DPS ceiling) | 15% |

**Rolling average:** 30-session decay cap, O(1) incremental update.

**Sanity checks (10 rules):** duration 55–65s, shots_hit ≤ shots_fired, headshots ≤ shots_hit, max fire rate 15/s, max kill rate 0.5/s, damage ceiling, reaction bounds 80–2000ms, range kill consistency.

---

## Role Certifications

Five roles, three cert levels:

| Role | Primary Metric | Icon |
|------|---------------|------|
| Tank | Damage Pressure (DPI) | 🛡️ |
| Recon | Long-Range Efficiency (LRE) | 🔭 |
| CQS | Close-Quarters Efficiency (CQE) | ⚡ |
| Tactical Anchor | Consistency | ⚓ |
| Aggressor | Engagement Efficiency | 💥 |

**State machine:**
- NONE → CANDIDATE: 5 ranked uploads + Pro tier
- CANDIDATE → CERTIFIED: 10 ranked uploads + std_dev(last 10 OSI) ≤ 5.0
- CERTIFIED → ELITE: top 5% role score for 2 consecutive seasons (season close)
- ANY → INACTIVE: 60 days without ranked upload (reactivated on next upload)

---

## Tier Badges

| Badge | Percentile |
|-------|------------|
| APEX  | ≥ 99% |
| ELITE | ≥ 95% |
| GOLD  | ≥ 80% |
| SILVER| ≥ 50% |
| BRONZE| < 50% |

---

## Tier Limits (Free vs Pro)

| Feature | Free | Pro ($7/mo) |
|---------|------|-------------|
| Lifetime uploads | 1 | Unlimited |
| Session history | Last 1 | Full |
| Leaderboard | Top 50 | Full + own rank |
| Role certifications | — | Full state machine |
| Percentile display | — | ✅ |
| Skill card SVG | Public only | Own card |

---

## Admin

Admin accounts are created via CLI only (no web registration):

```bash
python seed_admin.py
```

Login at `/#/admin` — separate JWT scope, all actions logged immutably.

**Permissions matrix:**
- `superadmin`: all permissions
- `moderator`: manage users, view logs
- `support`: view users only

---

## Skill Card SVG

```
GET /skillcard/{user_id}   — public card (cached 5 min)
GET /skillcard/me          — own card (authenticated)
```

Returns standalone SVG — embeddable in Discord bios, forum sigs, Reddit posts.

---

## Legal

- All gameplay data discarded after metric extraction
- Only derived metrics stored (OSI, 7 normalised signals)
- Disclaimer registry with version-pinned user consent log
- "Not a professional assessment" notice on all ranked outputs
- GDPR-compatible: DELETE /auth/account wipes all user data

---

## File Structure

```
fps_platform/
├── main.py                    # FastAPI app factory
├── seed_admin.py              # CLI: create first admin
├── requirements.txt
├── .env.example
├── app/
│   ├── config.py             # Settings + env vars
│   ├── database.py           # SQLite WAL + session factory
│   ├── models.py             # 20 ORM tables
│   ├── auth.py               # JWT auth, register, login, delete
│   ├── admin.py              # 21 admin routes, audit log
│   ├── scoring.py            # Pure math: OSI engine
│   ├── sessions.py           # 14-step upload pipeline
│   ├── roles.py              # Role cert read routes
│   ├── leaderboard.py        # LB routes + season archive
│   ├── leaderboard_service.py# Cache rebuild + season close
│   ├── payments.py           # Stripe stubs + payment log
│   ├── sponsors.py           # Sponsor CRUD
│   ├── legal.py              # Disclaimer registry
│   └── skillcard.py          # SVG card generator
├── static/
│   ├── index.html            # SPA shell
│   ├── css/style.css         # Industrial terminal aesthetic
│   └── js/app.js             # Hash-router SPA (8 pages)
└── tests/
    ├── test_scoring.py        # 628 lines, pure math tests
    └── test_leaderboard.py    # 676 lines, cache + season tests
```

---

*MECHgg is not affiliated with any game developer or publisher. OSI scores are for entertainment purposes only. Not a professional skill assessment. Not for wagering.*
