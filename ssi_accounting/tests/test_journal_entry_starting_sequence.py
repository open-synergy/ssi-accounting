# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo import fields
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestJournalEntryStartingSequence(YamlTransactionCase):
    """Pure Python -- trigger P1 (L-01: `action: call` discards a method's
    return value, while what is under test here IS the string returned by
    `_get_starting_sequence`).

    Regression guard for the `calendar.IllegalMonthError: bad month number
    0` raised when the form of a brand new journal entry was rendered:
    `company_id` is `related="journal_id.company_id"`, so it is empty
    until a journal is picked, and `int(False)` used to feed month `0` to
    `calendar.monthrange`. The fixtures use in-memory records
    (`.new(...)`) because `journal_id` is `required=True`, so a record
    with an empty `company_id` cannot be built through `create()` -- an
    in-memory record is exactly the state the form is in right after
    pressing New.
    """

    @classmethod
    def setUpClass(cls):
        """Create the journal shared by the journal-bound scenarios."""
        super().setUpClass()
        cls.journal = cls.env["account.journal"].create(
            {"name": "Starting Sequence Journal", "code": "SSQ", "type": "general"}
        )

    def test_no_journal_does_not_raise(self):
        """An in-memory entry without a journal still yields a number.

        This is the bug's exact reproduction: no `journal_id` means no
        `company_id`, which previously made `calendar.monthrange` raise.
        """
        entry = self.env["journal_entry"].new(
            {"date": fields.Date.context_today(self.env["journal_entry"])}
        )
        starting_sequence = entry._get_starting_sequence()
        self.assertIsInstance(starting_sequence, str)
        self.assertTrue(starting_sequence)

    def test_default_fiscal_year_keeps_the_previous_format(self):
        """A default (31 December) fiscal year is NOT treated as staggered.

        The company falls back to `self.env.company`, whose
        `fiscalyear_last_day`/`fiscalyear_last_month` are `31`/`"12"` by
        default, so `is_staggered_year` stays `False` and the number keeps
        its 4-digit year segment and 5-digit running placeholder -- byte
        for byte what it was before the fix.
        """
        entry_date = fields.Date.to_date("2026-03-15")
        entry = self.env["journal_entry"].new(
            {"journal_id": self.journal.id, "date": entry_date}
        )
        self.assertEqual(entry._get_starting_sequence(), "SSQ/2026/00000")

    def test_staggered_fiscal_year_still_yields_a_year_range(self):
        """A staggered (30 June) fiscal year keeps its "YY-YY" segment.

        Proves the staggered branch was not collaterally broken: the year
        segment becomes a range and the running placeholder shrinks to 4
        digits, exactly as upstream does.
        """
        self.env.company.write(
            {"fiscalyear_last_day": 30, "fiscalyear_last_month": "6"}
        )
        # 15 March 2026 falls BEFORE the 30 June 2026 year end, so the
        # fiscal year it belongs to started in 2025.
        entry = self.env["journal_entry"].new(
            {
                "journal_id": self.journal.id,
                "date": fields.Date.to_date("2026-03-15"),
            }
        )
        self.assertEqual(entry._get_starting_sequence(), "SSQ/25-26/0000")
        # 15 August 2026 falls AFTER it, so it opens the 2026-2027 one.
        entry_next = self.env["journal_entry"].new(
            {
                "journal_id": self.journal.id,
                "date": fields.Date.to_date("2026-08-15"),
            }
        )
        self.assertEqual(entry_next._get_starting_sequence(), "SSQ/26-27/0000")
