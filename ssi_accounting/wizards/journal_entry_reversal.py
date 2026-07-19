# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.exceptions import UserError

REFUND_METHOD_SELECTION = [
    ("cancel", "Cancel: create a reversal entry and reconcile it with the original"),
    ("modify", "Modify: create a reversal entry, then reopen a new draft copy"),
]


class JournalEntryReversal(models.TransientModel):
    """Wizard to reverse one or more posted 'journal_entry' records.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/wizard/account_move_reversal.py`` (class
    ``AccountMoveReversal``, model ``account.move.reversal``), adapted
    per this issue's Keputusan Desain: no ``move_type``/invoice concept
    exists on ``journal_entry`` (see that model's own docstring), so
    upstream's third ``refund_method`` value (``refund``, specific to
    invoice credit notes) is dropped -- only ``cancel``/``modify`` remain.

    Opened from ``journal_entry.action_reverse`` (the "Reverse Entry"
    header button), which pre-fills ``move_ids``/``date`` via context
    defaults; can also be driven directly (e.g. from a test) since
    ``move_ids`` additionally falls back to ``active_id``/``active_ids``,
    matching every other SSI wizard opened this way (see
    ``base.select_cancel_reason`` in ``ssi_transaction_cancel_mixin``).
    """

    _name = "journal_entry_reversal"
    _description = "Reverse Journal Entry"

    def _default_move_ids(self):
        active_ids = self.env.context.get("active_ids")
        if not active_ids and self.env.context.get("active_id"):
            active_ids = [self.env.context["active_id"]]
        return active_ids or []

    date = fields.Date(
        required=True,
        default=fields.Date.context_today,
        help="Accounting date given to the reversal entry (and, for "
        "'modify', the new draft copy) -- not the original entry's own date.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        help="Journal the reversal entry (and, for 'modify', the new "
        "draft copy) is posted to. Left empty to reuse each original "
        "entry's own journal.",
    )
    reason = fields.Char(
        help="Optional free-form reason appended to the reversal entry's reference.",
    )
    move_ids = fields.Many2many(
        comodel_name="journal_entry",
        string="Journal Entries",
        default=lambda self: self._default_move_ids(),
        help="Posted journal entries to reverse.",
    )
    refund_method = fields.Selection(
        selection=REFUND_METHOD_SELECTION,
        required=True,
        default="cancel",
        help="'Cancel' reverses and auto-reconciles the reversal against "
        "the original. 'Modify' reverses, then opens a new draft copy of "
        "the original lines to correct and re-post.",
    )

    def _prepare_default_reversal(self, move):
        """Build '_reverse_move_vals''s 'default_values' override for 'move'."""
        self.ensure_one()
        default_values = {"date": self.date}
        if self.journal_id:
            default_values["journal_id"] = self.journal_id.id
        default_values["ref"] = (
            self.env._(
                "Reversal of: %(name)s, %(reason)s",
                name=move.display_name,
                reason=self.reason,
            )
            if self.reason
            else self.env._("Reversal of: %(name)s", name=move.display_name)
        )
        return default_values

    def _check_reversible(self):
        self.ensure_one()
        if not self.move_ids:
            raise UserError(
                self.env._(
                    """
Context: Reverse journal entry
Problem: No journal entry was selected
Solution: Select at least one posted journal entry to reverse
"""
                )
            )
        not_posted = self.move_ids.filtered(lambda move: move.state != "posted")
        if not_posted:
            raise UserError(
                self.env._(
                    """
Context: Reverse journal entry
Problem: The following entries are not posted, so cannot be reversed:
%(entries)s
Solution: Select only posted journal entries
""",
                    entries="\n".join(f"- {move.display_name}" for move in not_posted),
                )
            )

    def _open_moves(self, moves):
        self.ensure_one()
        action = {
            "name": self.env._("Journal Entries"),
            "type": "ir.actions.act_window",
            "res_model": "journal_entry",
        }
        if len(moves) == 1:
            action.update({"view_mode": "form", "res_id": moves.id})
        else:
            action.update(
                {"view_mode": "list,form", "domain": [("id", "in", moves.ids)]}
            )
        return action

    def action_reverse_moves(self):
        """Reverse every wizard's 'move_ids' and return the resulting action.

        NOT '.sudo()' -- 'journal_entry._post()' (reached from
        '_confirm_reverse' -> 'journal_entry._reverse_moves') keys its
        own accounting-group check off 'self.env.su'; sudo-ing here
        would silently bypass it for every user. See
        'journal_entry.action_reverse''s own note.
        """
        for wizard in self:
            result = wizard._confirm_reverse()
        return result

    def _confirm_reverse(self):
        """Reverse 'move_ids', branching on 'refund_method'.

        Ported (behaviour-wise) from Odoo core
        ``AccountMoveReversal.reverse_moves``: 'cancel' reverses and
        auto-reconciles (see ``journal_entry._reconcile_reversed_moves``
        -- a no-op guard until issue #12 lands); 'modify' reverses
        without reconciling, then opens a fresh, editable draft copy of
        the original lines instead of the reversal entry itself.
        """
        self.ensure_one()
        self._check_reversible()
        default_values_list = [
            self._prepare_default_reversal(move) for move in self.move_ids
        ]
        reverse_moves = self.move_ids._reverse_moves(
            default_values_list, cancel=(self.refund_method == "cancel")
        )
        if self.refund_method == "modify":
            new_moves = self.env["journal_entry"].create(
                [
                    move._prepare_modify_move_vals(default_values)
                    for move, default_values in zip(
                        self.move_ids, default_values_list, strict=False
                    )
                ]
            )
            return self._open_moves(new_moves)
        return self._open_moves(reverse_moves)
