# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccountCodeMapping(TransactionCase):
    """Python murni -- pemicu P1 (L-01: `action: call` membuang nilai balik
    method; L-02: `target:` di YAML hanya lookup dict pada registry, tidak
    ada cara meng-assert hasil `search()` langsung tanpa melalui record yang
    tersimpan di registry).

    ``account.code.mapping`` tidak boleh diakses langsung -- `_search`
    sengaja menolak domain yang tidak menyaring `account_id`, sehingga satu
    -satunya cara mengujinya adalah memanggil `search()`/`create()` Python
    langsung dan meng-assert nilai baliknya, sesuatu yang tidak bisa
    dilakukan lewat aksi YAML manapun.
    """

    def test_search_without_account_id_is_rejected(self):
        with self.assertRaises(UserError):
            self.env["account.code.mapping"].search(
                [("company_id", "=", self.env.company.id)]
            )

    def test_mapping_reflects_account_code_per_company(self):
        account = self.env["account.account"].create(
            {
                "name": "Mapping Test",
                "account_type": "asset_current",
                "code": "701000",
            }
        )
        mappings = self.env["account.code.mapping"].search(
            [("account_id", "in", account.ids)]
        )
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings.company_id, self.env.company)
        self.assertEqual(mappings.code, "701000")
