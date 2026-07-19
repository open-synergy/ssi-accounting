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
        """Run the YAML scenarios for the journal entry reversal wizard."""
        self.run_yaml_scenario("test_data_journal_entry_reversal.yaml")

    @mute_logger("odoo.sql_db")
    def test_reversal_wizard_without_date_is_rejected(self):
        """Pure Python -- trigger P5 (L-22: `psycopg2.IntegrityError` is
        outside the 12 known exception types).

        'date' on 'journal_entry_reversal' is 'required=True', so the
        DB-level 'NOT NULL' rejects the row before this module's own Python
        code ever runs -- exactly the 'IntegrityError' that YAML's
        `expect_error` cannot catch (L-22), hence tested here instead of in
        'test_data_journal_entry_reversal.yaml'. `mute_logger("odoo.sql_db")`
        silences the PostgreSQL ERROR line that NORMALLY appears here;
        without it `oca_checklog_odoo` fails CI even though the test passes.
        """
        with self.assertRaises(IntegrityError):
            self.env["journal_entry_reversal"].create(
                {
                    "date": False,
                    "refund_method": "cancel",
                }
            )
