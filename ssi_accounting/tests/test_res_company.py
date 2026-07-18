# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import datetime

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestResCompany(YamlTransactionCase):
    def test_res_company(self):
        self.run_yaml_scenario("test_data_res_company.yaml")

    def test_get_lock_date_violations_returns_violated_fields(self):
        """Python murni -- pemicu P1 (L-01: `action: call` membuang nilai
        balik method; `_get_lock_date_violations`/`_format_lock_dates`
        tidak mengubah record apa pun, satu-satunya cara mengujinya adalah
        menangkap nilai balik langsung).
        """
        company = self.env.company
        company.write(
            {
                "fiscalyear_lock_date": datetime.date(2026, 1, 31),
                "tax_lock_date": False,
            }
        )

        locks = company._get_lock_date_violations(datetime.date(2026, 1, 15))
        self.assertEqual(locks, [(datetime.date(2026, 1, 31), "fiscalyear_lock_date")])

        formatted = company._format_lock_dates(locks)
        self.assertIn("Global Lock Date", formatted)
        self.assertIn("2026", formatted)

        no_locks = company._get_lock_date_violations(datetime.date(2026, 2, 1))
        self.assertEqual(no_locks, [])
