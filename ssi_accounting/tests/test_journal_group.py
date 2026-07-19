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
        self.run_yaml_scenario("test_data_journal_group.yaml")

    def test_duplicate_name_in_same_company_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22: `expect_error.type` tidak
        mencakup `psycopg2.IntegrityError`; keunikan `(name, company_id)`
        ditegakkan lewat `models.Constraint` di level SQL -- bukan
        `@api.constrains` -- sehingga kegagalannya tidak bisa diuji lewat
        `expect_error` di YAML).
        """
        company = self.env.company
        self.env["account.journal.group"].create(
            {"name": "Duplicate Group", "company_id": company.id}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["account.journal.group"].create(
                {"name": "Duplicate Group", "company_id": company.id}
            )
