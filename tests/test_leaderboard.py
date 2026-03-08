# =============================================================================
# tests/test_leaderboard.py
# Unit tests for app/leaderboard_service.py
#
# Tests cover:
#   - Dirty flag helpers
#   - Cache rebuild: sorting, rank assignment, percentile, badge, denormalisation
#   - Season archive snapshot
#   - Elite role promotion logic
#   - Season close: counter increment, profile reset
#   - Edge cases: 0 players, 1 player, ties
# =============================================================================

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ── In-memory DB setup ────────────────────────────────────────────────────────
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from app.database import Base
import app.models as M

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Apply WAL pragmas (skipped for in-memory — just create tables)
Base.metadata.create_all(bind=engine)
TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _now():
    return int(datetime.now(timezone.utc).timestamp())


# ── Fixtures ──────────────────────────────────────────────────────────────────

def fresh_db():
    """Each test gets a clean session with a freshly cleared DB."""
    db = TestSession()
    # Truncate all tables
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    return db


def make_user(db, username="player1", tier="pro"):
    u = M.User(
        username=username, email=f"{username}@test.com",
        password_hash="x", tier=tier, is_active=True,
        created_at=_now(), total_uploads=0,
    )
    db.add(u)
    db.flush()
    return u


def make_profile(db, user_id, season=1, osi=500.0, ranked_sessions=5):
    p = M.SkillProfile(
        user_id=user_id, season=season, osi=osi,
        avg_reaction=50.0, avg_accuracy=50.0, avg_eng_eff=50.0,
        avg_consistency=50.0, avg_cqe=50.0, avg_lre=50.0, avg_dpi=50.0,
        peak_osi=osi, ranked_sessions=ranked_sessions, last_updated=_now(),
    )
    db.add(p)
    db.flush()
    return p


def make_app_state(db, season=1):
    for key, value in [
        ("current_season",    str(season)),
        ("leaderboard_dirty", "1"),
        ("disclaimer_version","1.0.0"),
    ]:
        if not db.query(M.AppState).filter(M.AppState.key == key).first():
            db.add(M.AppState(key=key, value=value))
    db.flush()


def make_admin(db):
    a = M.Admin(
        username="admin", email="admin@test.com",
        password_hash="x", role="superadmin",
        is_active=True, created_at=_now(),
    )
    db.add(a)
    db.flush()
    perms = M.AdminPermission(
        admin_id=a.id,
        can_manage_users=True, can_reset_scores=True,
        can_manage_sponsors=True, can_view_logs=True,
        can_manage_seasons=True, can_manage_admins=True,
        can_manage_payments=True,
    )
    db.add(perms)
    db.flush()
    return a


# =============================================================================
# SECTION 1: Dirty flag helpers
# =============================================================================

class TestDirtyFlag:

    def test_fresh_db_with_no_state_not_dirty(self):
        from app.leaderboard_service import is_leaderboard_dirty
        db = fresh_db()
        assert is_leaderboard_dirty(db) is False

    def test_dirty_flag_set_is_detected(self):
        from app.leaderboard_service import is_leaderboard_dirty
        db = fresh_db()
        db.add(M.AppState(key="leaderboard_dirty", value="1"))
        db.flush()
        assert is_leaderboard_dirty(db) is True

    def test_dirty_flag_zero_not_dirty(self):
        from app.leaderboard_service import is_leaderboard_dirty
        db = fresh_db()
        db.add(M.AppState(key="leaderboard_dirty", value="0"))
        db.flush()
        assert is_leaderboard_dirty(db) is False

    def test_mark_clean_sets_flag_to_zero(self):
        from app.leaderboard_service import is_leaderboard_dirty, mark_clean
        db = fresh_db()
        db.add(M.AppState(key="leaderboard_dirty", value="1"))
        db.flush()
        mark_clean(db)
        assert is_leaderboard_dirty(db) is False

    def test_get_current_season_default(self):
        from app.leaderboard_service import get_current_season
        db = fresh_db()
        # No app_state row → falls back to settings.CURRENT_SEASON = 1
        assert get_current_season(db) == 1

    def test_get_current_season_from_db(self):
        from app.leaderboard_service import get_current_season
        db = fresh_db()
        db.add(M.AppState(key="current_season", value="3"))
        db.flush()
        assert get_current_season(db) == 3


# =============================================================================
# SECTION 2: Cache rebuild
# =============================================================================

class TestRebuildLeaderboard:

    def test_empty_db_returns_zero(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        count = rebuild_leaderboard(db, season=1)
        assert count == 0

    def test_single_player_ranks_first(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u = make_user(db)
        make_profile(db, u.id, osi=750.0)
        db.commit()

        count = rebuild_leaderboard(db, season=1)
        db.commit()

        assert count == 1
        row = db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).first()
        assert row.rank == 1
        assert row.user_id == u.id
        assert row.osi == 750.0
        assert row.username == "player1"

    def test_multiple_players_sorted_by_osi(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u1 = make_user(db, "alice", "pro")
        u2 = make_user(db, "bob",   "pro")
        u3 = make_user(db, "carol", "pro")
        make_profile(db, u1.id, osi=800.0)
        make_profile(db, u2.id, osi=600.0)
        make_profile(db, u3.id, osi=700.0)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        rows = (
            db.query(M.LeaderboardCache)
            .filter(M.LeaderboardCache.season == 1)
            .order_by(M.LeaderboardCache.rank)
            .all()
        )
        assert len(rows) == 3
        assert rows[0].username == "alice"   # 800 → rank 1
        assert rows[1].username == "carol"   # 700 → rank 2
        assert rows[2].username == "bob"     # 600 → rank 3

    def test_rank_is_1_indexed(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        for i, osi in enumerate([900.0, 800.0, 700.0]):
            u = make_user(db, f"p{i}")
            make_profile(db, u.id, osi=osi)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        ranks = [r.rank for r in
                 db.query(M.LeaderboardCache)
                 .filter(M.LeaderboardCache.season == 1)
                 .order_by(M.LeaderboardCache.rank).all()]
        assert ranks == [1, 2, 3]

    def test_percentile_assigned(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u1 = make_user(db, "top")
        u2 = make_user(db, "bottom")
        make_profile(db, u1.id, osi=900.0)
        make_profile(db, u2.id, osi=100.0)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        rows = {r.username: r for r in
                db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).all()}
        # top player: 1 out of 2 below → 50th percentile
        assert rows["top"].percentile == 50.0
        # bottom player: 0 out of 2 below → 0th percentile
        assert rows["bottom"].percentile == 0.0

    def test_tier_badge_assigned(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        # Create 100 players so percentile thresholds work properly
        users = []
        for i in range(100):
            u = make_user(db, f"p{i:03d}")
            make_profile(db, u.id, osi=float(i + 1))
            users.append(u)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        rows = (
            db.query(M.LeaderboardCache)
            .filter(M.LeaderboardCache.season == 1)
            .order_by(M.LeaderboardCache.rank)
            .all()
        )
        # Top player (rank 1, osi=100) should be apex
        assert rows[0].tier_badge == "apex"
        # Bottom player should be bronze
        assert rows[-1].tier_badge == "bronze"

    def test_username_denormalised_in_cache(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u = make_user(db, "specialname")
        make_profile(db, u.id, osi=500.0)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        row = db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).first()
        assert row.username == "specialname"

    def test_zero_ranked_sessions_excluded(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u1 = make_user(db, "ranked")
        u2 = make_user(db, "unranked")
        make_profile(db, u1.id, osi=500.0, ranked_sessions=3)
        make_profile(db, u2.id, osi=400.0, ranked_sessions=0)  # never ranked
        db.commit()

        count = rebuild_leaderboard(db, season=1)
        db.commit()

        assert count == 1
        rows = db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).all()
        assert len(rows) == 1
        assert rows[0].username == "ranked"

    def test_dirty_flag_cleared_after_rebuild(self):
        from app.leaderboard_service import rebuild_leaderboard, is_leaderboard_dirty
        db = fresh_db()
        make_app_state(db)  # sets dirty=1
        u = make_user(db)
        make_profile(db, u.id)
        db.commit()

        rebuild_leaderboard(db, season=1)
        assert is_leaderboard_dirty(db) is False

    def test_old_cache_deleted_before_rebuild(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u = make_user(db)
        make_profile(db, u.id, osi=500.0)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        # Change OSI and rebuild again
        profile = db.query(M.SkillProfile).filter(M.SkillProfile.user_id == u.id).first()
        profile.osi = 750.0
        db.add(M.AppState(key="leaderboard_dirty", value="1"))
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        # Should have exactly 1 row, not 2
        rows = db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).all()
        assert len(rows) == 1
        assert rows[0].osi == 750.0

    def test_profile_rank_written_back(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u = make_user(db)
        make_profile(db, u.id, osi=600.0)
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        profile = db.query(M.SkillProfile).filter(M.SkillProfile.user_id == u.id).first()
        assert profile.current_rank == 1
        assert profile.current_percentile is not None

    def test_top_role_included_in_cache(self):
        from app.leaderboard_service import rebuild_leaderboard
        db = fresh_db()
        make_app_state(db)
        u = make_user(db)
        make_profile(db, u.id, osi=700.0)

        # Give the user a certified role
        db.add(M.Role(
            user_id=u.id, season=1,
            role_slug=M.RoleSlugEnum.aggressor,
            cert_level=M.CertLevelEnum.certified,
            unlocked_at=_now(), last_activity=_now(), is_active=True,
        ))
        db.commit()

        rebuild_leaderboard(db, season=1)
        db.commit()

        row = db.query(M.LeaderboardCache).filter(M.LeaderboardCache.season == 1).first()
        assert row.top_role == "aggressor"


# =============================================================================
# SECTION 3: read_leaderboard
# =============================================================================

class TestReadLeaderboard:

    def _setup_players(self, db, n=10):
        make_app_state(db)
        for i in range(n):
            u = make_user(db, f"player{i:02d}")
            make_profile(db, u.id, osi=float(500 + i * 10))
        db.commit()

    def test_returns_sorted_rows(self):
        from app.leaderboard_service import rebuild_leaderboard, read_leaderboard
        db = fresh_db()
        self._setup_players(db)
        rebuild_leaderboard(db, 1)
        db.commit()

        result = read_leaderboard(db, season=1, is_pro=True)
        ranks = [r["rank"] for r in result["leaderboard"]]
        assert ranks == sorted(ranks)

    def test_free_user_capped_at_50(self):
        from app.leaderboard_service import rebuild_leaderboard, read_leaderboard
        db = fresh_db()
        make_app_state(db)
        for i in range(60):
            u = make_user(db, f"p{i:03d}")
            make_profile(db, u.id, osi=float(i + 1))
        db.commit()
        rebuild_leaderboard(db, 1)
        db.commit()

        result = read_leaderboard(db, season=1, page=1, limit=100, is_pro=False)
        assert len(result["leaderboard"]) <= 50

    def test_dirty_flag_triggers_rebuild_on_read(self):
        from app.leaderboard_service import read_leaderboard, is_leaderboard_dirty
        db = fresh_db()
        self._setup_players(db)
        # Dirty is set by make_app_state

        result = read_leaderboard(db, season=1, is_pro=True)
        # After read, should be clean
        assert is_leaderboard_dirty(db) is False
        assert len(result["leaderboard"]) == 10


# =============================================================================
# SECTION 4: Season close
# =============================================================================

class TestSeasonClose:

    def _setup_season(self, db, season=1, n=5):
        make_app_state(db, season=season)
        admin = make_admin(db)
        users = []
        for i in range(n):
            u = make_user(db, f"player{i}", "pro")
            make_profile(db, u.id, season=season, osi=float(500 + i * 50))
            users.append(u)
        db.commit()
        return admin, users

    def test_season_close_increments_season(self):
        from app.leaderboard_service import close_season, get_current_season
        db = fresh_db()
        admin, _ = self._setup_season(db, season=1)

        close_season(db, admin_id=admin.id)
        db.commit()

        assert get_current_season(db) == 2

    def test_season_close_archives_all_players(self):
        from app.leaderboard_service import close_season
        db = fresh_db()
        admin, users = self._setup_season(db, season=1, n=5)

        result = close_season(db, admin_id=admin.id)
        db.commit()

        assert result["players_archived"] == 5
        archives = db.query(M.SeasonArchive).filter(M.SeasonArchive.season == 1).all()
        assert len(archives) == 5

    def test_archive_rows_immutable_after_close(self):
        from app.leaderboard_service import close_season
        db = fresh_db()
        admin, users = self._setup_season(db, season=1)

        close_season(db, admin_id=admin.id)
        db.commit()

        arc = db.query(M.SeasonArchive).filter(
            M.SeasonArchive.user_id == users[0].id,
            M.SeasonArchive.season  == 1,
        ).first()
        assert arc is not None
        assert arc.final_osi > 0

    def test_season_close_creates_new_profiles(self):
        from app.leaderboard_service import close_season
        db = fresh_db()
        admin, users = self._setup_season(db, season=1, n=3)

        close_season(db, admin_id=admin.id)
        db.commit()

        # New season profiles should exist and be zeroed
        for u in users:
            p = db.query(M.SkillProfile).filter(
                M.SkillProfile.user_id == u.id,
                M.SkillProfile.season  == 2,
            ).first()
            assert p is not None
            assert p.osi == 0.0
            assert p.ranked_sessions == 0

    def test_season_close_old_profiles_preserved(self):
        from app.leaderboard_service import close_season
        db = fresh_db()
        admin, users = self._setup_season(db, season=1, n=3)

        close_season(db, admin_id=admin.id)
        db.commit()

        # Season 1 profiles should still exist
        for u in users:
            p = db.query(M.SkillProfile).filter(
                M.SkillProfile.user_id == u.id,
                M.SkillProfile.season  == 1,
            ).first()
            assert p is not None

    def test_season_close_clears_dirty_flag(self):
        from app.leaderboard_service import close_season, is_leaderboard_dirty
        db = fresh_db()
        admin, _ = self._setup_season(db, season=1)

        close_season(db, admin_id=admin.id)
        db.commit()

        assert is_leaderboard_dirty(db) is False

    def test_season_close_result_dict_complete(self):
        from app.leaderboard_service import close_season
        db = fresh_db()
        admin, _ = self._setup_season(db, season=1, n=4)

        result = close_season(db, admin_id=admin.id, reason="test close")
        db.commit()

        assert result["old_season"]       == 1
        assert result["new_season"]       == 2
        assert result["players_archived"] == 4
        assert "elite_promotions"         in result
        assert "profiles_reset"           in result


# =============================================================================
# SECTION 5: Elite role eligibility
# =============================================================================

class TestEliteEligibility:

    def test_no_elite_in_first_season(self):
        from app.leaderboard_service import _check_elite_eligibility
        db = fresh_db()
        # Season 1 close — no previous season to compare
        count = _check_elite_eligibility(db, closing_season=1)
        assert count == 0

    def test_elite_promoted_when_top5_both_seasons(self):
        from app.leaderboard_service import _check_elite_eligibility
        db = fresh_db()
        u = make_user(db)

        # Season 1 archive: 99th percentile
        db.add(M.SeasonArchive(
            user_id=u.id, season=1, final_osi=950.0,
            final_rank=1, final_percentile=99.0,
            archived_at=_now(),
        ))
        # Season 2 archive: 96th percentile (top 5%)
        db.add(M.SeasonArchive(
            user_id=u.id, season=2, final_osi=960.0,
            final_rank=1, final_percentile=96.0,
            archived_at=_now(),
        ))
        # Certified role in season 2
        db.add(M.Role(
            user_id=u.id, season=2,
            role_slug=M.RoleSlugEnum.tank,
            cert_level=M.CertLevelEnum.certified,
            unlocked_at=_now(), last_activity=_now(), is_active=True,
        ))
        db.commit()

        count = _check_elite_eligibility(db, closing_season=2)
        assert count == 1

        role = db.query(M.Role).filter(
            M.Role.user_id == u.id, M.Role.season == 2
        ).first()
        assert role.cert_level == M.CertLevelEnum.elite

    def test_no_elite_if_only_one_season_top5(self):
        from app.leaderboard_service import _check_elite_eligibility
        db = fresh_db()
        u = make_user(db)

        # Only season 1 is top 5%; season 2 is not
        db.add(M.SeasonArchive(
            user_id=u.id, season=1, final_osi=500.0,
            final_rank=10, final_percentile=60.0,   # not top 5%
            archived_at=_now(),
        ))
        db.add(M.SeasonArchive(
            user_id=u.id, season=2, final_osi=960.0,
            final_rank=1, final_percentile=98.0,    # top 5%
            archived_at=_now(),
        ))
        db.add(M.Role(
            user_id=u.id, season=2,
            role_slug=M.RoleSlugEnum.recon,
            cert_level=M.CertLevelEnum.certified,
            unlocked_at=_now(), last_activity=_now(), is_active=True,
        ))
        db.commit()

        count = _check_elite_eligibility(db, closing_season=2)
        assert count == 0

        role = db.query(M.Role).filter(M.Role.user_id == u.id).first()
        assert role.cert_level == M.CertLevelEnum.certified  # unchanged

    def test_candidate_not_promoted_to_elite(self):
        """Only Certified roles can be promoted to Elite."""
        from app.leaderboard_service import _check_elite_eligibility
        db = fresh_db()
        u = make_user(db)

        db.add(M.SeasonArchive(user_id=u.id, season=1, final_osi=950.0,
            final_rank=1, final_percentile=99.0, archived_at=_now()))
        db.add(M.SeasonArchive(user_id=u.id, season=2, final_osi=960.0,
            final_rank=1, final_percentile=99.0, archived_at=_now()))
        db.add(M.Role(
            user_id=u.id, season=2,
            role_slug=M.RoleSlugEnum.cqs,
            cert_level=M.CertLevelEnum.candidate,   # candidate, not certified
            unlocked_at=_now(), last_activity=_now(), is_active=True,
        ))
        db.commit()

        count = _check_elite_eligibility(db, closing_season=2)
        assert count == 0


# =============================================================================
# Runner
# =============================================================================

if __name__ == "__main__":
    suites = [
        TestDirtyFlag,
        TestRebuildLeaderboard,
        TestReadLeaderboard,
        TestSeasonClose,
        TestEliteEligibility,
    ]

    passed = failed = 0
    errors = []

    for cls in suites:
        for method in sorted(m for m in dir(cls) if m.startswith("test_")):
            try:
                getattr(cls(), method)()
                passed += 1
                print(f"  ✅  {cls.__name__}.{method}")
            except Exception as e:
                failed += 1
                errors.append(f"{cls.__name__}.{method}: {e}")
                print(f"  ❌  {cls.__name__}.{method}: {e}")

    print(f"\n{'═'*50}")
    print(f"  {passed} passed   {failed} failed   {passed+failed} total")
    for e in errors:
        print(f"  FAIL: {e}")
