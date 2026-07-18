# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged
from odoo.tools.sql import table_exists


@tagged("post_install", "-at_install")
class TestAccountRoot(TransactionCase):
    """Python murni -- pemicu P1 (L-01: `action: call` membuang nilai balik
    method; L-02: `target:` di YAML hanya lookup dict pada registry, tidak
    ada cara memberi alias pada hasil `browse()`/`_from_account_code()`).

    ``account.root`` tidak memiliki `create`/`write` -- setiap "record"
    dihitung dari sebuah id string (lihat ``models/account_root.py``), jadi
    satu-satunya permukaan yang bisa diuji adalah nilai balik `browse()`,
    `_from_account_code()`, dan `_search()`, yang semuanya mustahil
    ditangkap lewat aksi YAML `create`/`write`/`assert`.
    """

    def test_browse_computes_name_and_parent(self):
        root = self.env["account.root"].browse("10")
        self.assertEqual(root.name, "10")
        self.assertEqual(root.parent_id.id, "1")

    def test_browse_single_digit_has_no_parent(self):
        root = self.env["account.root"].browse("1")
        self.assertEqual(root.name, "1")
        self.assertFalse(root.parent_id)

    def test_from_account_code_returns_first_two_digits(self):
        root = self.env["account.root"]._from_account_code("101200")
        self.assertEqual(root.id, "10")

    def test_from_account_code_empty_code_returns_empty_recordset(self):
        root = self.env["account.root"]._from_account_code(False)
        self.assertFalse(root)

    def test_search_id_in_returns_matching_roots(self):
        roots = self.env["account.root"].search([("id", "in", ["10", "20"])])
        self.assertEqual(sorted(roots.ids), ["10", "20"])

    def test_search_id_parent_of_returns_ancestor_chain(self):
        roots = self.env["account.root"].search([("id", "parent_of", ["10"])])
        self.assertEqual(sorted(roots.ids), ["1", "10"])

    def test_auto_is_false_and_model_has_no_table(self):
        model = self.env["account.root"]
        self.assertFalse(model._auto)
        self.assertFalse(
            table_exists(self.env.cr, "account_root"),
            "account.root must not create a database table",
        )
