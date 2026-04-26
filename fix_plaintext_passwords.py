"""
fix_plaintext_passwords.py  —  ONE-TIME migration script
=========================================================

PURPOSE
-------
The `users.hashed_password` column contains plain-text values for some rows
(typically faculty accounts seeded before the hashing layer was in place).
bcrypt.verify() fails against plain text → POST /attendance/unlock returns 401.

This script:
  1. Reads every user row.
  2. Detects whether hashed_password is already a valid bcrypt hash.
  3. For rows that are NOT hashed  → hashes the plain-text value in-place.
  4. Prints a clear per-row report and a final summary.
  5. Commits only after all rows are processed (atomic — all-or-nothing).

USAGE
-----
  # From your project root (virtualenv active):
  python fix_plaintext_passwords.py

  # Dry-run (report only, no writes):
  python fix_plaintext_passwords.py --dry-run

SAFETY
------
  - Already-hashed rows are skipped — they are NEVER re-hashed.
  - Plain-text rows are updated to hash(same_value), so login still works
    as long as the faculty member knows their current (plain-text) password.
  - The script is idempotent: running it twice produces identical results.
  - After running, verify with:
      SELECT email, LEFT(hashed_password, 7) AS prefix FROM users;
    Every prefix should be '$2b$12' (bcrypt) or '$2a$12' / '$2y$12'.

AFTER THIS SCRIPT
-----------------
  All new users are created with hash_password() in auth.py, so this
  script never needs to run again unless rows are inserted directly via SQL.
"""

import sys
import argparse

from passlib.context import CryptContext

# ── Import your app's DB session + User model ────────────────────────────────
# Adjust the import path to match your project layout if needed.
from app.database import get_student_db
from app.models import User

# ── bcrypt context — same config as attendance_v2.py + auth/utils.py ─────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def is_bcrypt_hash(value: str) -> bool:
    """
    Return True if value looks like a bcrypt hash.
    All bcrypt hashes start with $2a$, $2b$, or $2y$ followed by the cost factor.
    Plain text passwords will never match this pattern.
    """
    return isinstance(value, str) and value.startswith(("$2a$", "$2b$", "$2y$"))


def fix_plaintext_passwords(dry_run: bool = False) -> None:
    db_gen  = get_student_db()
    db      = next(db_gen)

    try:
        users = db.query(User).all()

        already_hashed = 0
        needs_fix      = 0
        errors         = 0

        print(f"\n{'[DRY RUN] ' if dry_run else ''}Scanning {len(users)} user(s)...\n")
        print(f"{'Email':<40} {'Role':<10} {'Action'}")
        print("-" * 72)

        for user in users:
            pw = user.hashed_password or ""

            if is_bcrypt_hash(pw):
                already_hashed += 1
                print(f"{user.email:<40} {str(user.role.value):<10} SKIP  (already bcrypt)")
                continue

            # Plain-text detected — hash it
            needs_fix += 1
            try:
                new_hash = pwd_context.hash(pw)

                if dry_run:
                    print(
                        f"{user.email:<40} {str(user.role.value):<10} "
                        f"WOULD FIX  plain='{pw[:20]}{'...' if len(pw)>20 else ''}'"
                    )
                else:
                    user.hashed_password = new_hash
                    print(
                        f"{user.email:<40} {str(user.role.value):<10} "
                        f"FIXED      plain='{pw[:20]}{'...' if len(pw)>20 else ''}' "
                        f"→ {new_hash[:20]}..."
                    )

            except Exception as exc:
                errors += 1
                print(f"{user.email:<40} {'ERROR':<10} {exc}")

        # ── Commit (single transaction — atomic) ─────────────────────────────
        if not dry_run and needs_fix > 0 and errors == 0:
            db.commit()
            print(f"\n✅ Committed {needs_fix} update(s).")
        elif not dry_run and errors > 0:
            db.rollback()
            print(f"\n❌ Rolled back — {errors} error(s) encountered. Fix errors and re-run.")
        elif dry_run:
            print(f"\n[DRY RUN] No changes written.")

        # ── Summary ───────────────────────────────────────────────────────────
        print(f"""
Summary
-------
  Total users   : {len(users)}
  Already hashed: {already_hashed}
  Fixed         : {needs_fix if not dry_run else 0} ({needs_fix} would be fixed)
  Errors        : {errors}
""")

        if needs_fix == 0:
            print("✅ All passwords are already hashed. /attendance/unlock should work now.")
        elif not dry_run and errors == 0:
            print("✅ Done. Test /attendance/unlock — it should return 200 OK for correct passwords.")
        elif dry_run and needs_fix > 0:
            print(f"👆 Re-run WITHOUT --dry-run to apply {needs_fix} fix(es).")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hash plain-text passwords in the users table.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to the database.",
    )
    args = parser.parse_args()
    fix_plaintext_passwords(dry_run=args.dry_run)