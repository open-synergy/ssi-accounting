# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ReconcilePartial(models.Model):
    """One debit/credit journal item pairing, produced by 'reconcile()'.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_partial_reconcile.py`` (class
    ``AccountPartialReconcile``, model ``account.partial.reconcile``),
    because that model lives inside the ``account`` module, which
    ``ssi_accounting`` must not depend on. Renamed to ``reconcile_partial``
    per this issue's Keputusan Desain -- ``reconcile_full`` is its sibling.

    **Deliberately dropped from the upstream model** (out of this issue's
    scope, see the issue's "Tidak termasuk"/"Dibuang" lists): the whole
    cash-basis-tax machinery (``draft_caba_move_vals`` and every
    ``_*_cash_basis_*``/``_create_tax_cash_basis_moves`` method) and
    ``_get_to_update_payments`` (no ``account.payment`` concept exists
    anywhere in this repo).

    **``exchange_move_id`` is kept but never populated by this unit** --
    a plain, always-empty forward reference until a later currency unit
    lands, the same pattern ``journal_entry.reversed_entry_id``'s sibling
    fields and ``account.py``'s ``_toggle_reconcile_to_true`` already use
    for fields/behaviour still out of scope.

    **``create()``/``_update_matching_number()`` are ported apa adanya**
    (raw SQL bulk update included), and **``unlink()`` keeps its bug-fix
    comment verbatim, not simplified** -- both per this issue's Keputusan
    Desain. Table/model names are adapted throughout
    (``account_move_line``/``account.move.line`` ->
    ``journal_entry_item``/``journal_entry.item``).
    """

    _name = "reconcile_partial"
    _description = "Partial Reconcile"

    debit_move_id = fields.Many2one(
        comodel_name="journal_entry.item",
        required=True,
        index=True,
        ondelete="cascade",
        help="The debit-side journal item matched by this partial reconcile.",
    )
    credit_move_id = fields.Many2one(
        comodel_name="journal_entry.item",
        required=True,
        index=True,
        ondelete="cascade",
        help="The credit-side journal item matched by this partial reconcile.",
    )
    full_reconcile_id = fields.Many2one(
        comodel_name="reconcile_full",
        string="Full Reconcile",
        copy=False,
        index=True,
        ondelete="cascade",
        help="Set once every journal item on this partial's matching "
        "number graph has zero residual left -- see "
        "'journal_entry.item._reconcile_plan_with_sync'.",
    )
    exchange_move_id = fields.Many2one(
        comodel_name="journal_entry",
        index=True,
        help="Technical field: the currency exchange difference entry "
        "generated for this partial. Never populated by this unit -- see "
        "the class docstring -- filled by a later, dedicated currency unit.",
    )
    company_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Company Currency",
        compute="_compute_currencies",
        store=True,
        precompute=True,
        compute_sudo=True,
        help="Currency 'amount' is expressed in -- the company currency "
        "of this partial (see 'company_id').",
    )
    debit_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Debit Currency",
        compute="_compute_currencies",
        store=True,
        precompute=True,
        compute_sudo=True,
        help="Currency 'debit_amount_currency' is expressed in -- mirrors "
        "'debit_move_id.currency_id'.",
    )
    credit_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Credit Currency",
        compute="_compute_currencies",
        store=True,
        precompute=True,
        compute_sudo=True,
        help="Currency 'credit_amount_currency' is expressed in -- mirrors "
        "'credit_move_id.currency_id'.",
    )
    amount = fields.Monetary(
        currency_field="company_currency_id",
        help="Always-positive amount matched by this partial, expressed "
        "in the company currency.",
    )
    debit_amount_currency = fields.Monetary(
        currency_field="debit_currency_id",
        help="Always-positive amount matched by this partial, expressed "
        "in the debit journal item's own currency.",
    )
    credit_amount_currency = fields.Monetary(
        currency_field="credit_currency_id",
        help="Always-positive amount matched by this partial, expressed "
        "in the credit journal item's own currency.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        compute="_compute_company_id",
        store=True,
        readonly=False,
        precompute=True,
        compute_sudo=True,
        help="Company this partial reconcile belongs to -- 'debit_move_id'"
        "'s company (see the compute's docstring for why not "
        "'credit_move_id''s).",
    )
    max_date = fields.Date(
        string="Max Date of Matched Lines",
        compute="_compute_max_date",
        store=True,
        precompute=True,
        compute_sudo=True,
        help="Technical field: the later of 'debit_move_id.date'/"
        "'credit_move_id.date', used to determine at which date this "
        "reconciliation would show up on an aged receivable/payable report.",
    )

    @api.depends("debit_move_id.date", "credit_move_id.date")
    def _compute_max_date(self):
        for partial in self:
            partial.max_date = max(
                partial.debit_move_id.date, partial.credit_move_id.date
            )

    @api.depends("debit_move_id", "credit_move_id")
    def _compute_company_id(self):
        """Default 'company_id' from 'debit_move_id'.

        Upstream picks whichever side ``move_id.is_invoice(True)`` --
        neither concept exists here (see ``journal_entry``'s own
        docstring), so this always resolves through the debit side.
        Harmless in practice: both sides are already required to share a
        company by ``journal_entry.item._check_amls_exigibility_for_reconciliation``
        before a partial is ever created.
        """
        for partial in self:
            partial.company_id = (
                partial.debit_move_id.company_id or partial.credit_move_id.company_id
            )

    @api.depends(
        "company_id", "debit_move_id.currency_id", "credit_move_id.currency_id"
    )
    def _compute_currencies(self):
        for partial in self:
            partial.company_currency_id = partial.company_id.currency_id
            partial.debit_currency_id = partial.debit_move_id.currency_id
            partial.credit_currency_id = partial.credit_move_id.currency_id

    # -------------------------------------------------------------------
    # LOW-LEVEL METHODS
    # -------------------------------------------------------------------

    def unlink(self):
        """Unlink, then repair the matching number of every line involved.

        Ported (behaviour-wise) from upstream
        ``AccountPartialReconcile.unlink``, trimmed of
        ``_get_to_update_payments``/CABA-move-reversal (see the class
        docstring) -- the exchange-move-reversal leg is kept (always a
        no-op today, since 'exchange_move_id' is never populated by this
        unit) so it starts working unmodified once a later currency unit
        populates that field, matching this repo's own established
        forward-reference pattern.

        Comments below (ordering of the 'unlink()'/'full_to_unlink.unlink()'
        calls) document a real upstream bug fix -- kept verbatim per this
        issue's Keputusan Desain, not simplified.
        """
        if not self:
            return True

        # Same defensive avoidance of cyclic unlink calls as upstream:
        # removing partials can remove a full reconcile, which must not
        # loop back into removing partials again.
        moves_to_reverse = self.exchange_move_id

        # Retrieve the matching number to unlink.
        full_to_unlink = self.full_reconcile_id

        # if the move is draft and can be removed, there is no need to
        # update the matching number
        all_reconciled = self.debit_move_id + self.credit_move_id

        # Unlink partials before doing anything else to avoid 'Record has
        # already been deleted' due to the recursion.
        res = super().unlink()

        # Remove the matching numbers before reversing the moves to avoid
        # trying to remove the full twice.
        full_to_unlink.unlink()

        # Reverse or unlink exchange difference move entries.
        if moves_to_reverse:
            not_draft_moves = moves_to_reverse.filtered(
                lambda move: move.state != "draft"
            )
            draft_moves = moves_to_reverse - not_draft_moves
            default_values_list = [
                {
                    "date": move._get_accounting_date(
                        move.date, move._affect_tax_report()
                    ),
                    "ref": move.env._("Reversal of: %(name)s", name=move.name),
                }
                for move in not_draft_moves
            ]
            not_draft_moves._reverse_moves(default_values_list, cancel=True)
            draft_moves.unlink()

        all_reconciled = all_reconciled.exists()
        self._update_matching_number(all_reconciled)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        self._update_matching_number(partials.debit_move_id + partials.credit_move_id)
        return partials

    @api.model
    def _update_matching_number(self, amls):
        """Recompute 'matching_number' for every line reachable from 'amls'.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountPartialReconcile._update_matching_number``, table/model
        names adapted. This is the **only** writer of
        ``journal_entry.item.matching_number`` -- that field is a plain
        ``Char`` with no ``compute=`` of its own (see this issue's
        Keputusan Desain and ``journal_entry.item``'s own field
        docstring).

        :param amls: the journal items whose matching number graph must
            be recomputed.
        """
        amls = amls._all_reconciled_lines()
        all_partials = amls.matched_debit_ids | amls.matched_credit_ids

        # The matchings form a set of graphs, which can be numbered: this
        # is the matching number. We iterate on each edge of the graphs,
        # giving it a number (min of its edge ids). By iterating, we
        # either simply add a node (move line) to the graph and assign
        # the number to it or we merge the two graphs. At the end, we
        # have an index for the number to assign of all lines.
        number2lines = {}
        line2number = {}
        for partial in all_partials.sorted("id"):
            debit_min_id = line2number.get(partial.debit_move_id.id)
            credit_min_id = line2number.get(partial.credit_move_id.id)
            if debit_min_id and credit_min_id:
                # merging the 2 graphs into the one with the smallest number
                if debit_min_id != credit_min_id:
                    min_min_id = min(debit_min_id, credit_min_id)
                    max_min_id = max(debit_min_id, credit_min_id)
                    for line_id in number2lines[max_min_id]:
                        line2number[line_id] = min_min_id
                    number2lines[min_min_id].extend(number2lines.pop(max_min_id))
            elif debit_min_id:  # adding a new node to a graph
                number2lines[debit_min_id].append(partial.credit_move_id.id)
                line2number[partial.credit_move_id.id] = debit_min_id
            elif credit_min_id:  # adding a new node to a graph
                number2lines[credit_min_id].append(partial.debit_move_id.id)
                line2number[partial.debit_move_id.id] = credit_min_id
            else:  # creating a new graph
                number2lines[partial.id] = [
                    partial.debit_move_id.id,
                    partial.credit_move_id.id,
                ]
                line2number[partial.debit_move_id.id] = partial.id
                line2number[partial.credit_move_id.id] = partial.id

        amls.flush_recordset(["full_reconcile_id"])
        self.env.cr.execute_values(
            """
            UPDATE journal_entry_item l
               SET matching_number = CASE
                       WHEN l.full_reconcile_id IS NOT NULL
                           THEN l.full_reconcile_id::text
                       ELSE 'P' || source.number
                   END
              FROM (VALUES %s) AS source(number, ids)
             WHERE l.id = ANY(source.ids)
            """,
            list(number2lines.items()),
            page_size=1000,
        )
        processed_amls = self.env["journal_entry.item"].browse(
            [_id for ids in number2lines.values() for _id in ids]
        )
        processed_amls.invalidate_recordset(["matching_number"])
        (amls - processed_amls).matching_number = False
