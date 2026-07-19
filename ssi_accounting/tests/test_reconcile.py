# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestReconcile(YamlTransactionCase):
    def test_reconcile(self):
        self.run_yaml_scenario("test_data_reconcile.yaml")

    def test_exchange_difference_amount_is_asserted_with_float_tolerance(self):
        """Python murni -- pemicu P2 (L-04: `equals` YAML adalah `!=` mentah,
        tidak ada toleransi float/`places`/`compare_amounts` untuk nilai
        moneter hasil rekonsiliasi lintas kurs).

        CRUD-nya (journal, akun, currency, entry, posting, reconcile) tetap
        dijalankan seperti skenario YAML pada umumnya -- hanya assertion
        angka selisih kurs yang butuh toleransi pembulatan ini yang turun ke
        Python, sesuai Keputusan Desain issue ini.
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
                            "balance": 15000000.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": acc_income.id,
                            "debit": 0.0,
                            "credit": 15000000.0,
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
                            "balance": -15500000.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": acc_income.id,
                            "debit": 15500000.0,
                            "credit": 0.0,
                        },
                    ),
                ],
            }
        )
        entry2.action_post()

        line1 = entry1.line_ids.filtered(lambda line: line.account_id == acc_ar)
        line2 = entry2.line_ids.filtered(lambda line: line.account_id == acc_ar)
        (line1 + line2).reconcile()

        self.assertAlmostEqual(line1.amount_residual, 0.0, places=2)
        self.assertAlmostEqual(line2.amount_residual, 0.0, places=2)

        main_partial = self.env["reconcile_partial"].search(
            [("debit_move_id", "=", line1.id), ("credit_move_id", "=", line2.id)]
        )
        self.assertEqual(len(main_partial), 1)
        self.assertAlmostEqual(main_partial.amount, 15000000.0, places=2)

        gain_line = main_partial.exchange_move_id.line_ids.filtered(
            lambda item: item.account_id == acc_gain
        )
        self.assertAlmostEqual(gain_line.credit, 500000.0, places=2)
        self.assertAlmostEqual(gain_line.debit, 0.0, places=2)
