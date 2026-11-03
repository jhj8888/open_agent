"""Regression tests for migration planning and its fail-closed verifier."""

import copy
import unittest

from argon2 import PasswordHasher

from passwords import HASHER, MigrationError, plan_migration, verify_storage


def organization(kind="plain"):
    return {
        "owner": "admin",
        "name": "built-in",
        "password_type": kind,
        "password_salt": "",
        "master_password": "",
        "default_password": "",
    }


def account(password="old password", kind="plain", name="alice"):
    return {
        "owner": "built-in",
        "name": name,
        "password": password,
        "password_type": kind,
        "password_salt": "",
        "ldap": "",
    }


class PasswordStorageTests(unittest.TestCase):
    def test_duplicate_or_unexpected_organization_owner_is_rejected(self):
        duplicate = organization()
        duplicate["owner"] = "unexpected-owner"
        for orgs in ([organization(), duplicate], [duplicate]):
            with self.subTest(organizations=len(orgs)):
                with self.assertRaises(MigrationError):
                    plan_migration(orgs, [account()])
                with self.assertRaises(MigrationError):
                    verify_storage(orgs, [account()])

    def test_plain_and_inherited_accounts_keep_their_password(self):
        users = [account(), account("long password: " + "x" * 100, "", "bob")]
        before = copy.deepcopy(users)
        changes, policies = plan_migration([organization()], users)
        self.assertEqual(len(changes), 2)
        self.assertEqual(len(policies), 1)
        self.assertEqual(users, before)
        for hashed, kind, salt, user in changes:
            self.assertEqual(kind, "argon2id")
            self.assertEqual(salt, "")
            self.assertTrue(HASHER.verify(hashed, user["password"]))

    def test_identical_passwords_receive_different_salts(self):
        changes, _ = plan_migration(
            [organization()], [account(name="alice"), account(name="bob")]
        )
        self.assertNotEqual(changes[0][0], changes[1][0])

    def test_second_migration_preserves_hashes(self):
        org = organization()
        user = account()
        changes, _ = plan_migration([org], [user])
        user.update(password=changes[0][0], password_type="argon2id")
        org["password_type"] = "argon2id"
        verify_storage([org], [user])
        self.assertEqual(plan_migration([org], [user]), ([], []))

    def test_existing_bcrypt_with_inherited_type_is_not_rehashed(self):
        # The planner validates the encoding/cost; actual bcrypt login is exercised
        # against Casdoor in the integration test.
        hashed = "$2a$10$" + "A" * 53
        user = account(hashed, "")
        changes, _ = plan_migration([organization("bcrypt")], [user])
        self.assertEqual(changes[0][:3], (hashed, "bcrypt", ""))

    def test_external_only_accounts_are_not_given_a_password(self):
        user = account("", "")
        user["ldap"] = "external-directory"
        self.assertEqual(plan_migration([organization()], [user])[0], [])
        verify_storage([organization("argon2id")], [user])

    def test_invalid_and_ambiguous_states_block_migration(self):
        cases = [
            account("$argon2id$already-a-hash", "plain"),
            account("broken", "argon2id"),
            account("old-digest", "md5-salt"),
            account("$2a$04$" + "A" * 53, "bcrypt"),
        ]
        for user in cases:
            with self.subTest(kind=user["password_type"]):
                with self.assertRaises(MigrationError):
                    plan_migration([organization()], [user])

    def test_master_and_default_passwords_require_operator_review(self):
        for field in ("master_password", "default_password"):
            org = organization()
            org[field] = "configured-value"
            with self.subTest(field=field):
                with self.assertRaises(MigrationError):
                    plan_migration([org], [account()])

    def test_verifier_rejects_plain_accounts_even_with_strong_org_policy(self):
        with self.assertRaises(MigrationError):
            verify_storage([organization("argon2id")], [account()])

    def test_verifier_accepts_upstream_casdoor_argon2_parameters(self):
        upstream_hasher = PasswordHasher(time_cost=1, memory_cost=65536)
        user = account(upstream_hasher.hash("fixture-password"), "argon2id")
        verify_storage([organization("argon2id")], [user])

    def test_verifier_rejects_missing_organization_and_weak_argon2(self):
        with self.assertRaises(MigrationError):
            verify_storage([], [])
        weak = PasswordHasher(time_cost=1, memory_cost=8192).hash("fixture")
        with self.assertRaises(MigrationError):
            verify_storage([organization("argon2id")], [account(weak, "argon2id")])


if __name__ == "__main__":
    unittest.main()
