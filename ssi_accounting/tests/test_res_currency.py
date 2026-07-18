# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestResCurrency(YamlTransactionCase):
    def test_res_currency(self):
        self.run_yaml_scenario("test_data_res_currency.yaml")

    def test_has_accounting_entries_is_false_without_account_move_line(self):
        """Python murni -- pemicu P1 (L-01: `action: call` membuang nilai
        balik method; `_has_accounting_entries` mengembalikan `bool` yang
        tidak bisa ditangkap dari YAML).

        `account.move.line` belum ada di lingkup modul ini, jadi method ini
        harus selalu `False` sampai model itu ditambahkan pada unit
        berikutnya -- lihat docstring kelas `ResCurrency`.
        """
        currency = self.env.ref("base.USD")
        self.assertFalse(currency._has_accounting_entries())
