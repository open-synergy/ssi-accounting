# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestTax(YamlTransactionCase):
    def test_tax(self):
        """Run the YAML scenarios for 'tax'."""
        self.run_yaml_scenario("test_data_tax.yaml")

    def test_create_without_tax_group_raises_integrity_error(self):
        """Pure Python -- trigger P5 (L-22: `expect_error.type` does not
        cover `psycopg2.IntegrityError`; the requiredness of
        `tax_group_id` is enforced through a SQL-level NOT NULL -- not
        `@api.constrains` -- so its failure cannot be tested through
        YAML's `expect_error`).
        """
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["tax"].create(
                {
                    "name": "No Group Tax",
                    "amount_type": "percent",
                    "amount": 10.0,
                }
            )
