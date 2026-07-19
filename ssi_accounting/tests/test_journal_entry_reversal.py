# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestJournalEntryReversal(YamlTransactionCase):
    def test_journal_entry_reversal(self):
        self.run_yaml_scenario("test_data_journal_entry_reversal.yaml")

    @mute_logger("odoo.sql_db")
    def test_reversal_wizard_without_date_is_rejected(self):
        """Python murni -- pemicu P5 (L-22: `psycopg2.IntegrityError` di luar 12 tipe).

        'date' pada 'journal_entry_reversal' adalah 'required=True', jadi
        DB-level 'NOT NULL' menolak sebelum kode Python modul ini sempat
        berjalan -- persis 'IntegrityError' yang `expect_error` YAML tidak
        bisa tangkap (L-22), jadi diuji di sini, bukan di
        'test_data_journal_entry_reversal.yaml'. `mute_logger("odoo.sql_db")`
        membungkam baris ERROR PostgreSQL yang NORMAL muncul di sini; tanpa
        itu `oca_checklog_odoo` menggagalkan CI walau test lulus.
        """
        with self.assertRaises(IntegrityError):
            self.env["journal_entry_reversal"].create(
                {
                    "date": False,
                    "refund_method": "cancel",
                }
            )
