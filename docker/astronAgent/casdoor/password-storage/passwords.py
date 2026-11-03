#!/usr/bin/env python3
"""Explicit, transactional migration of Casdoor 2.67 local passwords.

The default is a dry run. No passwords, hashes, or database errors are logged.
Stop Casdoor before --apply; back up its database before changing credentials.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import pymysql
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError

HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
STRONG_TYPES = {"argon2id", "bcrypt"}


class MigrationError(Exception):
    """An account state requires operator review; never include its secret."""


def strong_hash(password_type, password):
    """Validate storage metadata without accepting plaintext as a hash."""
    if password_type == "bcrypt":
        match = re.fullmatch(r"\$2[aby]\$(\d{2})\$[./A-Za-z0-9]{53}", password)
        return bool(match and 10 <= int(match.group(1)) <= 31)
    if password_type != "argon2id":
        return False
    try:
        params = extract_parameters(password)
    except (InvalidHashError, ValueError):
        return False
    # Casdoor 2.67 creates Argon2id hashes with 64 MiB and one iteration.
    adequate_cost = params.memory_cost >= 65536 or (
        params.memory_cost >= 19456 and params.time_cost >= 2
    )
    return (
        params.type == Type.ID
        and params.version == 19
        and params.time_cost >= 1
        and params.salt_len >= 16
        and params.hash_len >= 16
        and adequate_cost
    )


def index_organizations(organizations):
    """User.owner references an organization name in Casdoor's admin scope."""
    by_name = {}
    for org in organizations:
        if org["name"] in by_name:
            raise MigrationError("Duplicate organization names require review")
        if org["owner"] != "admin":
            raise MigrationError("Unsupported organization owner requires review")
        by_name[org["name"]] = org
    return by_name


def plan_migration(organizations, users, hasher=HASHER):
    """Build all changes before writing; preserve already-hashed credentials."""
    by_name = index_organizations(organizations)
    changes = []
    for org in organizations:
        if org["password_type"] not in {"", "plain", *STRONG_TYPES}:
            raise MigrationError("Unsupported organization password type; review first")
        if org.get("master_password") or org.get("default_password"):
            raise MigrationError("Disable organization master/default passwords first")
    for user in users:
        org = by_name.get(user["owner"])
        if org is None:
            raise MigrationError("Account has no matching organization; review first")
        password = user["password"] or ""
        if not password:
            continue  # OAuth/LDAP-only accounts do not have a local password.
        if user.get("ldap"):
            raise MigrationError("LDAP account has a local password; review first")
        effective_type = user["password_type"] or org["password_type"] or "plain"
        if effective_type == "plain":
            if password.startswith(("$2a$", "$2b$", "$2y$", "$argon2")):
                raise MigrationError(
                    "Hash-like password has plaintext metadata; review first"
                )
            changes.append((hasher.hash(password), "argon2id", "", user))
        elif strong_hash(effective_type, password):
            # An empty type inherits the organization type. Pin it before changing
            # the organization policy so existing bcrypt logins keep working.
            if not user["password_type"]:
                changes.append((password, effective_type, user["password_salt"], user))
        else:
            raise MigrationError(
                "Unsupported or invalid stored hash; reset/review first"
            )
    policy_changes = [
        org for org in organizations if org["password_type"] != "argon2id"
    ]
    return changes, policy_changes


def verify_storage(organizations, users):
    """Read-only gate: every nonempty local password must be a strong hash."""
    by_name = index_organizations(organizations)
    if not organizations or "built-in" not in by_name:
        raise MigrationError("Casdoor initialization is incomplete")
    for org in organizations:
        if org["password_type"] not in STRONG_TYPES:
            raise MigrationError("Organization password policy requires migration")
        if org.get("master_password") or org.get("default_password"):
            raise MigrationError("Organization master/default password requires review")
    for user in users:
        org = by_name.get(user["owner"])
        if org is None:
            raise MigrationError("Account has no matching organization")
        password = user["password"] or ""
        if password and not strong_hash(
            user["password_type"] or org["password_type"], password
        ):
            raise MigrationError("Stored account passwords require migration")


def read_rows(connection, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT owner, name, password_type, password_salt, master_password, "
            "default_password FROM organization ORDER BY owner, name" + suffix
        )
        organizations = cursor.fetchall()
        cursor.execute(
            "SELECT owner, name, password, password_type, password_salt, ldap "
            "FROM user ORDER BY owner, name" + suffix
        )
        users = cursor.fetchall()
    return organizations, users


def migrate(connection, apply=False):
    """Commit all password and policy changes together, or roll everything back."""
    try:
        connection.begin()
        organizations, users = read_rows(connection, lock=apply)
        if not organizations or not any(
            org["name"] == "built-in" for org in organizations
        ):
            raise MigrationError("Initialize Casdoor privately before migrating")
        changes, policies = plan_migration(organizations, users)
        if apply:
            with connection.cursor() as cursor:
                for password, password_type, salt, user in changes:
                    cursor.execute(
                        "UPDATE user SET password=%s, password_type=%s, password_salt=%s "
                        "WHERE owner=%s AND name=%s",
                        (password, password_type, salt, user["owner"], user["name"]),
                    )
                    if cursor.rowcount != 1:
                        raise MigrationError(
                            "Account update count changed; transaction rolled back"
                        )
                for org in policies:
                    cursor.execute(
                        "UPDATE organization SET password_type=%s WHERE owner=%s AND name=%s",
                        ("argon2id", org["owner"], org["name"]),
                    )
            verify_storage(*read_rows(connection))
            connection.commit()
        else:
            connection.rollback()
        return {
            "applied": apply,
            "account_updates": len(changes),
            "policy_updates": len(policies),
        }
    except Exception:
        connection.rollback()
        raise


def wait_for_casdoor(url, timeout=120):
    """HTTP starts only after Casdoor's initialization has completed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return
        except urllib.error.HTTPError:
            return  # Any HTTP response proves the listener started.
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1)
    raise MigrationError("Casdoor did not finish initialization before the timeout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply the migration while Casdoor is stopped",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="read-only verification; fail on plaintext storage",
    )
    parser.add_argument(
        "--wait-url", help="wait for private Casdoor initialization before --check"
    )
    args = parser.parse_args()
    if args.wait_url and not args.check:
        parser.error("--wait-url is only valid with --check")
    try:
        if args.wait_url:
            wait_for_casdoor(args.wait_url)
        password = os.environ.get("CASDOOR_MIGRATION_DB_PASSWORD")
        if password is None:
            raise MigrationError(
                "Set CASDOOR_MIGRATION_DB_PASSWORD; do not pass it on the command line"
            )
        connection = pymysql.connect(
            host=os.getenv("CASDOOR_MIGRATION_DB_HOST", "casdoor-mysql"),
            port=int(os.getenv("CASDOOR_MIGRATION_DB_PORT", "3306")),
            user=os.getenv("CASDOOR_MIGRATION_DB_USER", "casdoor"),
            password=password,
            database=os.getenv("CASDOOR_MIGRATION_DB_NAME", "casdoor"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
        )
        try:
            if args.check:
                verify_storage(*read_rows(connection))
                connection.rollback()
                print("Password storage verification passed")
            else:
                print(json.dumps(migrate(connection, args.apply)))
        finally:
            connection.close()
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Database/migration failure; no credential data is logged. Check the connection and schema.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
