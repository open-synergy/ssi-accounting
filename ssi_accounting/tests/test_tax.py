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
        self.run_yaml_scenario("test_data_tax.yaml")

    def test_create_without_tax_group_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22: `expect_error.type` tidak
        mencakup `psycopg2.IntegrityError`; `tax_group_id` wajib diisi
        ditegakkan lewat NOT NULL di level SQL -- bukan `@api.constrains`
        -- sehingga kegagalannya tidak bisa diuji lewat `expect_error` di
        YAML).
        """
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["tax"].create(
                {
                    "name": "No Group Tax",
                    "amount_type": "percent",
                    "amount": 10.0,
                }
            )
