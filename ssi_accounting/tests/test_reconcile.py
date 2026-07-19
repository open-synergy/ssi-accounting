# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestReconcile(YamlTransactionCase):
    def test_reconcile(self):
        """Run the YAML scenarios for journal item reconciliation."""
        self.run_yaml_scenario("test_data_reconcile.yaml")

    def test_exchange_difference_amount_is_asserted_with_float_tolerance(self):
        """Pure Python -- trigger P2 (L-04: YAML's `equals` is a raw `!=`,
        there is no float tolerance/`places`/`compare_amounts` for the
        monetary values a cross-currency reconciliation produces).

        Its CRUD (journal, accounts, currency, entry, posting, reconcile)
        still runs like any ordinary YAML scenario -- only the exchange
        difference amount assertion, which needs this rounding tolerance,
        drops down to Python, per this issue's Keputusan Desain.
        """
        company = self.env.company
        currency = self.env["res.currency"].create(
            {"name": "FCP", "symbol": "FPY", "rounding": 0.01}
        )
        journal = self.env["account.journal"].create(
            {"name": "Reconcile Journal PY", "type": "general"}
        )
        acc_ar = self.env["account.account"].create(
            {
                "name": "Receivable PY",
                "account_type": "asset_receivable",
                "code": "RECPYAR",
            }
        )
        acc_income = self.env["account.account"].create(
            {"name": "Income PY", "account_type": "income", "code": "RECPYINC"}
        )
        acc_gain = self.env["account.account"].create(
            {
                "name": "Exchange Gain PY",
                "account_type": "income",
                "code": "RECPYGAIN",
            }
        )
        acc_loss = self.env["account.account"].create(
            {
                "name": "Exchange Loss PY",
                "account_type": "expense",
                "code": "RECPYLOSS",
            }
        )
        company.write(
            {
                "currency_exchange_journal_id": journal.id,
                "income_currency_exchange_account_id": acc_gain.id,
                "expense_currency_exchange_account_id": acc_loss.id,
            }
        )
        # 'balance' is deliberately never given alongside 'amount_currency'
        # below -- see '_prepare_exchange_difference_move_vals''s own
        # comment: 'amount_currency''s inverse unconditionally re-derives
        # 'balance' from 'amount_currency'/'currency_rate', clobbering any
        # 'balance' also given on the same create() vals. Configuring an
        # explicit rate per date instead (the same technique
        # 'test_data_journal_entry.yaml' already uses) lets 'balance' be
        # correctly *derived*, deterministically, from 'amount_currency'.
        self.env["res.currency.rate"].create(
            {"currency_id": currency.id, "rate": 0.5, "name": "2026-06-09"}
        )
        self.env["res.currency.rate"].create(
            {"currency_id": currency.id, "rate": 0.4, "name": "2026-06-10"}
        )

        entry1 = self.env["journal_entry"].create(
            {
                "journal_id": journal.id,
                "date": "2026-06-09",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": acc_ar.id,
                            "currency_id": currency.id,
                            "amount_currency": 1000.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": acc_income.id,
                            "debit": 0.0,
                            "credit": 2000.0,
                        },
                    ),
                ],
            }
        )
        entry1.action_post()
        entry2 = self.env["journal_entry"].create(
            {
                "journal_id": journal.id,
                "date": "2026-06-10",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": acc_ar.id,
                            "currency_id": currency.id,
                            "amount_currency": -1000.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": acc_income.id,
                            "debit": 2500.0,
                            "credit": 0.0,
                        },
                    ),
                ],
            }
        )
        entry2.action_post()

        line1 = entry1.line_ids.filtered(lambda line: line.account_id == acc_ar)
        line2 = entry2.line_ids.filtered(lambda line: line.account_id == acc_ar)
        self.assertAlmostEqual(line1.balance, 2000.0, places=2)
        self.assertAlmostEqual(line2.balance, -2500.0, places=2)
        (line1 + line2).reconcile()

        self.assertAlmostEqual(line1.amount_residual, 0.0, places=2)
        self.assertAlmostEqual(line2.amount_residual, 0.0, places=2)

        main_partial = self.env["reconcile_partial"].search(
            [("debit_move_id", "=", line1.id), ("credit_move_id", "=", line2.id)]
        )
        self.assertEqual(len(main_partial), 1)
        self.assertAlmostEqual(main_partial.amount, 2000.0, places=2)

        gain_line = main_partial.exchange_move_id.line_ids.filtered(
            lambda item: item.account_id == acc_gain
        )
        self.assertAlmostEqual(gain_line.credit, 500.0, places=2)
        self.assertAlmostEqual(gain_line.debit, 0.0, places=2)
