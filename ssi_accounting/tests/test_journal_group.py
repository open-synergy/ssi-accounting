# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestJournalGroup(YamlTransactionCase):
    def test_journal_group(self):
        """Run the YAML scenarios for 'account.journal.group'."""
        self.run_yaml_scenario("test_data_journal_group.yaml")

    def test_duplicate_name_in_same_company_raises_integrity_error(self):
        """Pure Python -- trigger P5 (L-22: `expect_error.type` does not
        cover `psycopg2.IntegrityError`; the `(name, company_id)`
        uniqueness is enforced through a SQL-level `models.Constraint` --
        not `@api.constrains` -- so its failure cannot be tested through
        YAML's `expect_error`).
        """
        company = self.env.company
        self.env["account.journal.group"].create(
            {"name": "Duplicate Group", "company_id": company.id}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["account.journal.group"].create(
                {"name": "Duplicate Group", "company_id": company.id}
            )
