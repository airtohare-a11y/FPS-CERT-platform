#!/usr/bin/env python
# =============================================================================
# seed_admin.py
# CLI script to create the first superadmin account.
#
# This is the ONLY way to create the first admin.
# There is no HTTP endpoint for admin registration — by design.
# Run this once on the server, then create subsequent admins via
# POST /admin/accounts (requires can_manage_admins permission).
#
# Usage:
#   python seed_admin.py --username admin --email admin@yoursite.com
#   (prompts for password securely)
#
# Or with all args:
#   python seed_admin.py --username admin --email admin@yoursite.com --password yourpassword
# =============================================================================

import argparse
import getpass
import sys
from datetime import datetime, timezone

# Bootstrap the app context
sys.path.insert(0, ".")

from app.database import SessionLocal, init_db
from app.models import Admin, AdminPermission
from app.auth import hash_password


def seed_superadmin(username: str, email: str, password: str) -> None:
    init_db()
    db = SessionLocal()

    try:
        # Check if any admin already exists
        existing = db.query(Admin).filter(Admin.username == username).first()
        if existing:
            print(f"❌  Admin '{username}' already exists.")
            sys.exit(1)



        now = int(datetime.now(timezone.utc).timestamp())

        admin = Admin(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="superadmin",
            is_active=True,
            created_at=now,
        )
        db.add(admin)
        db.flush()

        # Superadmin gets all permissions
        permissions = AdminPermission(
            admin_id=admin.id,
            can_manage_users    = True,
            can_reset_scores    = True,
            can_manage_sponsors = True,
            can_view_logs       = True,
            can_manage_seasons  = True,
            can_manage_admins   = True,
            can_manage_payments = True,
        )
        db.add(permissions)
        db.commit()

        print(f"✅  Superadmin '{username}' created successfully.")
        print(f"    Login at: POST /admin/login")
        print(f"    Username: {username}")
        print(f"    Role:     superadmin (all permissions enabled)")

    except Exception as e:
        db.rollback()
        print(f"❌  Error creating admin: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed first superadmin account")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--email",    required=True, help="Admin email")
    parser.add_argument("--password", default=None,  help="Password (prompted if omitted)")
    args = parser.parse_args()

    password = args.password
    if not password:
        password = getpass.getpass("Admin password: ")
        confirm  = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("❌  Passwords do not match.")
            sys.exit(1)

    seed_superadmin(args.username, args.email, password)
