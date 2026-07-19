# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestJournal(YamlTransactionCase):
    def test_journal(self):
        """Run the YAML scenarios for 'account.journal'."""
        self.run_yaml_scenario("test_data_journal.yaml")

    def test_duplicate_code_in_same_company_raises_integrity_error(self):
        """Pure Python -- trigger P5 (L-22: `expect_error.type` does not
        cover `psycopg2.IntegrityError`; the `(code, company_id)`
        uniqueness is enforced through a SQL-level `models.Constraint` --
        not `@api.constrains` -- so its failure cannot be tested through
        YAML's `expect_error`).
        """
        company = self.env.company
        self.env["account.journal"].create(
            {
                "name": "Journal A",
                "code": "DUP01",
                "type": "general",
                "company_id": company.id,
            }
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["account.journal"].create(
                {
                    "name": "Journal B",
                    "code": "DUP01",
                    "type": "general",
                    "company_id": company.id,
                }
            )

    def test_copy_data_regenerates_a_fresh_code(self):
        """Pure Python -- trigger P1 (L-01: `action: call` discards a
        method's return value, and `copy()`/`copy_data()` are not among
        the actions that populate YAML's registry; the only way to check
        the vals `copy_data()` produces -- specifically that `code` is
        excluded from vals altogether (not merely set to `False`) so that
        `_compute_code` generates a fresh, unique code -- is to capture
        its return value directly).
        """
        journal = self.env["account.journal"].create(
            {"name": "Original", "code": "CPY01", "type": "general"}
        )
        vals_list = journal.copy_data()
        self.assertEqual(len(vals_list), 1)
        self.assertNotIn("code", vals_list[0])
        self.assertEqual(vals_list[0]["name"], "Original (copy)")

        copy = journal.copy()
        self.assertNotEqual(copy.code, journal.code)
        self.assertTrue(copy.code)
