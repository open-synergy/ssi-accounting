# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestAccountCodeMapping(TransactionCase):
    """Pure Python -- trigger P1 (L-01: `action: call` discards a method's
    return value; L-02: YAML's `target:` is only a registry dict lookup,
    there is no way to assert a bare `search()` result without first
    stashing it on a record in the registry).

    ``account.code.mapping`` must not be accessed unfiltered -- `_search`
    deliberately rejects any domain that does not filter on `account_id`,
    so the only way to test it is to call Python `search()`/`create()`
    directly and assert their return values, something no YAML action can
    express.
    """

    def test_search_without_account_id_is_rejected(self):
        """Searching without filtering on 'account_id' must raise 'UserError'."""
        with self.assertRaises(UserError):
            self.env["account.code.mapping"].search(
                [("company_id", "=", self.env.company.id)]
            )

    def test_mapping_reflects_account_code_per_company(self):
        """A fresh account gets exactly one mapping, holding its own company's code."""
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
