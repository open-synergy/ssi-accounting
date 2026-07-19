# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestResCurrency(YamlTransactionCase):
    def test_res_currency(self):
        """Run the YAML scenarios for the 'res.currency' deactivation guard."""
        self.run_yaml_scenario("test_data_res_currency.yaml")

    def test_has_accounting_entries_is_false_without_account_move_line(self):
        """Pure Python -- trigger P1 (L-01: `action: call` discards a
        method's return value; `_has_accounting_entries` returns a `bool`
        that cannot be captured from YAML).

        A currency with no 'journal_entry.item' referencing it at all
        (neither as its foreign currency nor as a company currency) must
        report `False` -- the plain empty-state case of the real query
        `_has_accounting_entries` now runs, see the `ResCurrency` class
        docstring for how that query is built.
        """
        currency = self.env.ref("base.USD")
        self.assertFalse(currency._has_accounting_entries())
