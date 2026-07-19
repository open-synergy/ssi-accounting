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
        self.run_yaml_scenario("test_data_journal_entry.yaml")

    def test_product_line_without_account_id_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22: `expect_error.type` tidak
        mencakup `psycopg2.IntegrityError`). `account_id` sengaja bukan
        `required=True` di level field (lihat docstring kelas
        `JournalEntryItem`) -- keharusannya untuk baris `display_type`
        `product`/`tax` ditegakkan oleh SQL `CHECK`
        `_check_accountable_required_fields`, yang gagal sebagai
        `IntegrityError` mentah di database, bukan salah satu dari 12
        tipe exception yang dikenal `expect_error`.
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
        """Python murni -- pemicu P5 (L-22), sama seperti test di atas
        tapi untuk `display_type` `tax`: `_check_accountable_required_fields`
        menegakkan `account_id` untuk `product`/`tax` sama-sama, bukan
        hanya `product`.
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

    def test_section_line_with_account_id_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22). `_check_non_accountable_fields_null`
        menolak baris `line_section`/`line_note` yang justru diisi
        `account_id` -- SQL `CHECK`, bukan `@api.constrains`, jadi
        gagalnya sebagai `IntegrityError` mentah, bukan `UserError`.
        """
        journal = self.env["account.journal"].create(
            {"name": "Section With Account Journal", "type": "general"}
        )
        acc_a = self.env["account.account"].create(
            {
                "name": "Section Account A",
                "account_type": "asset_current",
                "code": "JESEC001",
            }
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["journal_entry"].create(
                {
                    "journal_id": journal.id,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "display_type": "line_section",
                                "name": "Invalid Section",
                                "account_id": acc_a.id,
                            },
                        ),
                    ],
                }
            )

    def test_note_line_with_nonzero_debit_raises_integrity_error(self):
        """Python murni -- pemicu P5 (L-22). `_check_non_accountable_fields_null`
        juga menolak baris `line_note` yang `debit`-nya bukan nol, walau
        `account_id`-nya kosong.
        """
        journal = self.env["account.journal"].create(
            {"name": "Note With Debit Journal", "type": "general"}
        )
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.env["journal_entry"].create(
                {
                    "journal_id": journal.id,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "display_type": "line_note",
                                "name": "Invalid Note",
                                "debit": 5.0,
                            },
                        ),
                    ],
                }
            )

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
