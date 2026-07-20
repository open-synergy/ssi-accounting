# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestUiJournalEntry(HttpCase):
    """Browser tours executing the 'journal_entry' work instructions.

    Each test runs one tour, and each tour executes one IK file step for
    step -- see 'static/tests/tours/journal_entry_tour.esm.js'. These tours
    assert only what the user can see (view rendered, button available,
    status bar moved); field values, computes and totals stay in the YAML
    scenarios of 'test_journal_entry.py' /
    'test_journal_entry_posting.py'.

    The IK pre-conditions are set up here rather than clicked through the
    UI. 'admin' is used as the tour user because 'base.user_admin' is a
    member of 'group_accounting_manager', which implies
    'group_accounting_user' -- the group '_post()' requires.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Pre-Condition -- at least one Journal and two Accounts exist.
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "TOUR-JOURNAL",
                "type": "general",
            }
        )
        cls.account_debit = cls.env["account.account"].create(
            {
                "name": "Tour Debit Account",
                "account_type": "asset_current",
                "code": "TOURDR",
            }
        )
        cls.account_credit = cls.env["account.account"].create(
            {
                "name": "Tour Credit Account",
                "account_type": "income",
                "code": "TOURCR",
            }
        )

        # Pre-Condition of 04-post -- a draft entry with balanced lines.
        # 'ref' carries a unique marker so the tour can find the right
        # list row with ':contains(...)' instead of a brittle ':first'.
        cls.entry_to_post = cls._create_entry("TOUR-POST-001")

        # Pre-Condition of 05-unpost -- the same, already posted.
        cls.entry_to_unpost = cls._create_entry("TOUR-UNPOST-001")
        cls.entry_to_unpost.action_post()

    @classmethod
    def _create_entry(cls, reference):
        """Create a balanced draft entry marked with 'reference'."""
        return cls.env["journal_entry"].create(
            {
                "journal_id": cls.journal.id,
                "date": "2026-03-10",
                "ref": reference,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": cls.account_debit.id,
                            "name": "Tour debit line",
                            "debit": 100.0,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": cls.account_credit.id,
                            "name": "Tour credit line",
                            "debit": 0.0,
                            "credit": 100.0,
                        },
                    ),
                ],
            }
        )

    def test_create(self):
        """IK: docs/journal_entry/01-create.md"""
        self.start_tour("/odoo", "ssi_accounting_journal_entry_create", login="admin")

    def test_post(self):
        """IK: docs/journal_entry/04-post.md"""
        self.start_tour("/odoo", "ssi_accounting_journal_entry_post", login="admin")

    def test_unpost(self):
        """IK: docs/journal_entry/05-unpost.md"""
        self.start_tour("/odoo", "ssi_accounting_journal_entry_unpost", login="admin")
