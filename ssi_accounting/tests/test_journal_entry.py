# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestJournalEntry(YamlTransactionCase):
    def test_journal_entry(self):
        self.run_yaml_scenario("test_data_journal_entry.yaml")

    def test_cumulated_balance_runs_over_lines_in_date_order(self):
        """Python murni -- pemicu P3 (L-06: perbandingan o2m/m2m di YAML
        berbasis `set`, urutan baris TIDAK PERNAH bisa di-assert, dan
        tidak ada assert per-baris. `cumulated_balance` hanya bermakna
        relatif terhadap urutan recordset saat dibaca -- satu-satunya
        cara memeriksa nilainya per-baris adalah membaca recordset
        terurut langsung di Python).
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
