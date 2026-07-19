# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged
from odoo.tools.sql import table_exists


@tagged("post_install", "-at_install")
class TestAccountRoot(TransactionCase):
    """Pure Python -- trigger P1 (L-01: `action: call` discards a method's
    return value; L-02: YAML's `target:` is only a registry dict lookup,
    there is no way to alias the result of `browse()`/`_from_account_code()`).

    ``account.root`` has no `create`/`write` -- every "record" is derived
    from an id string (see ``models/account_root.py``), so the only surface
    left to test is the return value of `browse()`, `_from_account_code()`,
    and `_search()`, none of which any YAML `create`/`write`/`assert` action
    can capture.
    """

    def test_browse_computes_name_and_parent(self):
        """Browsing a two-digit root computes its 'name' and one-digit 'parent_id'."""
        root = self.env["account.root"].browse("10")
        self.assertEqual(root.name, "10")
        self.assertEqual(root.parent_id.id, "1")

    def test_browse_single_digit_has_no_parent(self):
        """A one-digit root has no shorter root to be its parent."""
        root = self.env["account.root"].browse("1")
        self.assertEqual(root.name, "1")
        self.assertFalse(root.parent_id)

    def test_from_account_code_returns_first_two_digits(self):
        """'_from_account_code' derives the root id from an account code's prefix."""
        root = self.env["account.root"]._from_account_code("101200")
        self.assertEqual(root.id, "10")

    def test_from_account_code_empty_code_returns_empty_recordset(self):
        """An empty account code yields an empty 'account.root' recordset."""
        root = self.env["account.root"]._from_account_code(False)
        self.assertFalse(root)

    def test_search_id_in_returns_matching_roots(self):
        """A '("id", "in", [...])' domain returns exactly the matching roots."""
        roots = self.env["account.root"].search([("id", "in", ["10", "20"])])
        self.assertEqual(sorted(roots.ids), ["10", "20"])

    def test_search_id_parent_of_returns_ancestor_chain(self):
        """A '("id", "parent_of", [...])' domain returns the full ancestor chain."""
        roots = self.env["account.root"].search([("id", "parent_of", ["10"])])
        self.assertEqual(sorted(roots.ids), ["1", "10"])

    def test_auto_is_false_and_model_has_no_table(self):
        """'account.root' is a tableless, computed-only model."""
        model = self.env["account.root"]
        self.assertFalse(model._auto)
        self.assertFalse(
            table_exists(self.env.cr, "account_root"),
            "account.root must not create a database table",
        )
