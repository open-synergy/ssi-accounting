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
        self.run_yaml_scenario("test_data_journal.yaml")

    def test_duplicate_code_in_same_company_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22: `expect_error.type` tidak
        mencakup `psycopg2.IntegrityError`; keunikan `(code, company_id)`
        ditegakkan lewat `models.Constraint` di level SQL -- bukan
        `@api.constrains` -- sehingga kegagalannya tidak bisa diuji lewat
        `expect_error` di YAML).
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
        """Python murni -- pemicu P1 (L-01: `action: call` membuang nilai
        balik method, dan `copy()`/`copy_data()` tidak termasuk aksi yang
        mengisi registry di YAML; satu-satunya cara memeriksa vals yang
        dihasilkan `copy_data()` -- terutama bahwa `code` dikeluarkan dari
        vals (bukan sekadar diisi `False`) agar `_compute_code`
        membuatkan kode baru yang unik -- adalah menangkap nilai baliknya
        langsung).
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
