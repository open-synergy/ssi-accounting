# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestJournalEntry(YamlTransactionCase):
    def test_journal_entry(self):
        """Run the YAML scenarios for 'journal_entry'/'journal_entry.item'."""
        self.run_yaml_scenario("test_data_journal_entry.yaml")

    def test_product_line_without_account_id_raises_integrity_error(self):
        """Pure Python -- trigger P5 (L-22: `expect_error.type` does not
        cover `psycopg2.IntegrityError`). `account_id` is `required=True`
        at the field level (see the `JournalEntryItem` class docstring),
        enforced by the column's `NOT NULL` SQL constraint. A direct
        Python `create()` call (unlike an RPC call, which the dispatch
        layer's `retrying()` translates into a `ValidationError`) lets
        the raw `psycopg2.errors.NotNullViolation` -- a subclass of
        `IntegrityError` -- propagate as-is, not one of the 12 exception
        types `expect_error` recognises.
        """
        journal = self.env["account.journal"].create(
            {"name": "No Account Journal", "type": "general"}
        )
        acc_b = self.env["account.account"].create(
            {"name": "No Account B", "account_type": "income", "code": "JENOACC1"}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["journal_entry"].create(
                {
                    "journal_id": journal.id,
                    "line_ids": [
                        # display_type defaults to "product" -- left implicit
                        # on purpose, matching how a real product line would
                        # be created.
                        (0, 0, {"debit": 10.0, "credit": 0.0}),
                        (0, 0, {"account_id": acc_b.id, "debit": 0.0, "credit": 10.0}),
                    ],
                }
            )

    def test_tax_line_without_account_id_raises_integrity_error(self):
        """Pure Python -- trigger P5 (L-22), same as the test above but
        for `display_type` `tax`: `account_id`'s `required=True` applies
        regardless of `display_type`, so it is enforced for `tax` rows
        exactly like `product` ones.
        """
        journal = self.env["account.journal"].create(
            {"name": "No Account Tax Journal", "type": "general"}
        )
        acc_b = self.env["account.account"].create(
            {"name": "No Account Tax B", "account_type": "income", "code": "JENOACC2"}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["journal_entry"].create(
                {
                    "journal_id": journal.id,
                    "line_ids": [
                        (0, 0, {"display_type": "tax", "debit": 5.0, "credit": 0.0}),
                        (0, 0, {"account_id": acc_b.id, "debit": 0.0, "credit": 5.0}),
                    ],
                }
            )

    def test_cumulated_balance_runs_over_lines_in_date_order(self):
        """Pure Python -- trigger P3 (L-06: YAML's o2m/m2m comparison is
        `set`-based, row order can NEVER be asserted, and there is no
        per-row assert. `cumulated_balance` only makes sense relative to
        the recordset's order as read -- the only way to check its
        per-row values is to read the ordered recordset directly in
        Python).
        """
        journal = self.env["account.journal"].create(
            {"name": "CB Journal", "type": "general"}
        )
        acc_a = self.env["account.account"].create(
            {"name": "CB Account A", "account_type": "asset_current", "code": "CB0001"}
        )
        acc_b = self.env["account.account"].create(
            {"name": "CB Account B", "account_type": "income", "code": "CB0002"}
        )
        entry_1 = self.env["journal_entry"].create(
            {
                "journal_id": journal.id,
                "date": "2026-01-10",
                "line_ids": [
                    (0, 0, {"account_id": acc_a.id, "debit": 50.0, "credit": 0.0}),
                    (0, 0, {"account_id": acc_b.id, "debit": 0.0, "credit": 50.0}),
                ],
            }
        )
        entry_2 = self.env["journal_entry"].create(
            {
                "journal_id": journal.id,
                "date": "2026-01-20",
                "line_ids": [
                    (0, 0, {"account_id": acc_a.id, "debit": 30.0, "credit": 0.0}),
                    (0, 0, {"account_id": acc_b.id, "debit": 0.0, "credit": 30.0}),
                ],
            }
        )
        lines = self.env["journal_entry.item"].search(
            [
                ("account_id", "=", acc_a.id),
                ("move_id", "in", (entry_1.id, entry_2.id)),
            ],
            order="date asc, id asc",
        )
        self.assertEqual(len(lines), 2)
        # Read both values off the SAME recordset in one shot: indexing
        # `lines[0]`/`lines[1]` separately would each create its own
        # single-record recordset, resetting the running total per access
        # (see `_compute_cumulated_balance`'s docstring).
        self.assertEqual(lines.mapped("cumulated_balance"), [50.0, 80.0])
