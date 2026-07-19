# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DISPLAY_TYPE_SELECTION = [
    ("product", "Item"),
    ("tax", "Tax"),
    ("line_section", "Section"),
    ("line_note", "Note"),
]


class JournalEntryItem(models.Model):
    """A single debit/credit line of a ``journal_entry``.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_move_line.py`` (class
    ``AccountMoveLine``, model ``account.move.line``), because that model
    lives inside the ``account`` module, which ``ssi_accounting`` must not
    depend on. Renamed to ``journal_entry.item`` per this issue's
    Keputusan Desain -- see ``journal_entry.py``'s docstring for why the
    header is not kept dotted either.

    **``product_id``/``quantity``/``price_unit``/``price_subtotal`` are
    kept** (product decision, see the issue), but are purely
    informational: the tax base for an ``entry``-typed move is
    ``amount_currency``, and ``quantity``/``price_unit``/``discount`` are
    never read by the tax engine -- exactly upstream Odoo 19's own
    behaviour for ``account.move`` lines of ``move_type == 'entry'``.
    ``_compute_price_unit`` is dropped entirely: ``price_unit`` is a plain,
    directly-typeable field. ``_compute_totals`` mirrors
    ``price_subtotal``/``price_total`` from ``amount_currency`` rather
    than from ``quantity * price_unit`` -- **do not fork the engine to
    make it do that**, see the issue's Keputusan Desain.

    **``product_uom_id`` is dropped** -- safe, because
    ``tax._get_base_line_field_value_from_record`` (see ``models/tax.py``)
    already falls back to ``self.env['uom.uom']`` when the field is
    absent from a base line's source record.

    **``display_type`` is kept, required**, but shrunk to
    ``product``/``tax``/``line_section``/``line_note``: it is the base
    line vs. tax line discriminator the tax engine
    (``tax._prepare_base_line_for_taxes_computation``) relies on, so it
    cannot be dropped even though this issue does not yet wire the tax
    synchronisation itself (a later, separate unit).

    **``account_id`` is deliberately not ``required=True``.** Exactly
    upstream ``account.move.line``'s own mechanism, its necessity is
    enforced by two ``models.Constraint`` SQL ``CHECK`` constraints
    ported verbatim (table name aside) below --
    ``_check_accountable_required_fields``/``_check_non_accountable_fields_null``
    -- so a ``line_section``/``line_note`` row (purely cosmetic, never
    posts to an account) is never forced to carry one, while a
    ``product``/``tax`` row always is. A plain ``required=True`` on the
    field would wrongly force every row, cosmetic ones included.

    **``balance`` (not ``debit``/``credit``) is the primary, directly
    writable field** the three fill directions all resolve into --
    mirroring upstream ``account.move.line`` exactly: ``debit``/
    ``credit`` are ``compute``+``inverse`` pairs depending on
    ``balance`` (``_compute_debit_credit``/``_inverse_debit``/
    ``_inverse_credit``), and ``amount_currency`` is a ``compute``+
    ``inverse`` pair also depending on ``balance``
    (``_compute_amount_currency``/``_inverse_amount_currency``).
    **Do not invert this** (making ``balance`` computed+inverse *from*
    ``debit``/``credit`` instead, with ``amount_currency`` inverse
    writing into ``balance``): an inverse method assigning into another
    field that itself only has an ``inverse`` (not a plain
    ``@api.depends`` compute) does not reliably cascade during
    ``create()`` -- the second inverse is only invoked for fields
    present in the *original* ``vals``, not for ones merely assigned to
    by another field's inverse in the same call. Concretely, filling
    only ``amount_currency`` on create would leave ``debit``/``credit``
    at their stale default instead of being derived, silently producing
    an unbalanced entry. Depending on ``balance`` via ``@api.depends``
    instead of via a second inverse hop is what makes the cascade work
    for every fill direction.

    **A second, subtler pitfall: ``precompute=True`` on the *derived*
    fields.** Upstream sets it on ``debit``/``credit``/``amount_currency``
    (as well as ``balance``), and it is tempting to mirror that verbatim
    -- but doing so here reintroduces the exact same class of bug via a
    different mechanism: a ``precompute=True`` field lacking an explicit
    value gets computed **during ``create()``'s pre-insert pass**, before
    the *given*-field inverses of that same call (which is where
    ``balance`` gets its real, final value when the caller only supplied
    ``amount_currency``) have run. Concretely: create a line with only
    ``amount_currency`` given -- if ``debit``/``credit`` are
    ``precompute=True``, they get computed pre-insert against ``balance``
    still at its default (0), *then* ``_inverse_amount_currency`` sets
    ``balance`` for real post-insert, too late for the already-baked-in
    ``debit``/``credit`` to notice. Only ``balance`` itself keeps
    ``precompute=True`` here: its own compute is a self-referential
    no-op whenever a value already exists (see
    ``_compute_balance``'s docstring), so it is immune to this ordering
    trap regardless of which of the three fill directions supplied it.

    **Deliberately dropped from the upstream model** (out of this issue's
    scope, see the issue's "Dibuang dari baris" list):
    ``analytic_distribution``, ``term_key``, ``epd_*``,
    ``discount_allocation_*``, ``discount_date``, ``deductible_amount``,
    ``is_refund``, ``move_type``, ``payment_id``, ``statement_line_id``,
    ``reconcile_model_id``, ``product_category_id``. Reconciliation
    itself (``reconciled``, ``amount_residual``, ``full_reconcile_id``,
    ``matched_debit_ids``/``matched_credit_ids``) is also out of scope --
    see ``account.py``'s ``_toggle_reconcile_to_true``/
    ``_toggle_reconcile_to_false`` docstrings, guarded on those fields'
    absence, not merely on this model's.
    """

    _name = "journal_entry.item"
    _description = "Journal Item"
    _order = "date desc, move_name desc, id"
    _check_company_auto = True

    move_id = fields.Many2one(
        comodel_name="journal_entry",
        required=True,
        index=True,
        ondelete="cascade",
        help="Journal entry this line belongs to.",
    )
    move_name = fields.Char(
        related="move_id.name",
        store=True,
        index=True,
        help="Technical field: mirrors the parent journal entry's 'name', "
        "used to order/search journal items without joining.",
    )
    parent_state = fields.Selection(
        related="move_id.state",
        store=True,
        help="Technical field: mirrors the parent journal entry's "
        "'state', used by the view to make lines readonly once posted.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        related="move_id.journal_id",
        store=True,
        index=True,
        help="Technical field: mirrors the parent journal entry's journal.",
    )
    journal_group_id = fields.Many2one(
        comodel_name="account.journal.group",
        related="move_id.journal_group_id",
        store=True,
        help="Technical field: mirrors the parent journal entry's journal group.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="move_id.company_id",
        store=True,
        index=True,
        help="Technical field: mirrors the parent journal entry's company.",
    )
    company_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Company Currency",
        related="move_id.company_currency_id",
        store=True,
        help="Currency 'debit'/'credit'/'balance'/'cumulated_balance' are "
        "expressed in -- the company currency of the parent journal entry.",
    )
    date = fields.Date(
        related="move_id.date",
        store=True,
        index=True,
        help="Technical field: mirrors the parent journal entry's date.",
    )
    ref = fields.Char(
        related="move_id.ref",
        store=True,
        help="Technical field: mirrors the parent journal entry's reference.",
    )
    sequence = fields.Integer(
        default=10,
        help="Used to order lines within a journal entry.",
    )
    name = fields.Char(
        string="Label",
        help="Free-form description of this line.",
    )
    display_type = fields.Selection(
        selection=DISPLAY_TYPE_SELECTION,
        required=True,
        default="product",
        help="Distinguishes a normal debit/credit line ('Item') from a "
        "tax line ('Tax') or a purely cosmetic section/note line. Read "
        "by the tax engine to tell base lines from tax lines, and by "
        "this model's SQL CHECK constraints to tell which rows must "
        "carry an account and non-zero amounts and which must not.",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        index=True,
        check_company=True,
        help="Account this line posts to. Required for 'product'/'tax' "
        "rows and forbidden for 'line_section'/'line_note' rows -- "
        "enforced by SQL CHECK, not by 'required=True' here, see the "
        "class docstring.",
    )
    account_name = fields.Char(
        related="account_id.name",
        string="Account Name",
        help="Technical field: mirrors 'account_id.name' for list display.",
    )
    account_code = fields.Char(
        related="account_id.code",
        string="Account Code",
        help="Technical field: mirrors 'account_id.code' for list display.",
    )
    account_type = fields.Selection(
        related="account_id.account_type",
        string="Account Type",
        help="Technical field: mirrors 'account_id.account_type'.",
    )
    account_internal_group = fields.Selection(
        related="account_id.internal_group",
        string="Account Internal Group",
        help="Technical field: mirrors 'account_id.internal_group'.",
    )
    account_root_id = fields.Many2one(
        comodel_name="account.root",
        related="account_id.root_id",
        string="Account Root",
        help="Technical field: mirrors 'account_id.root_id'.",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        index=True,
        help="Optional partner this line is related to.",
    )
    debit = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_debit_credit",
        inverse="_inverse_debit",
        store=True,
        help="Amount posted to the debit side of 'account_id', in the "
        "company currency. Derived from 'balance'; editing this directly "
        "updates 'balance' (and therefore 'credit') back. Deliberately "
        "**not** 'precompute=True' -- see the class docstring: unlike "
        "'balance' itself, a precomputed 'debit'/'credit' would be baked "
        "in during create()'s pre-insert pass, before a same-call "
        "'amount_currency'-only fill direction gets a chance to resolve "
        "'balance' via its own inverse, leaving 'debit'/'credit' stale.",
    )
    credit = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_debit_credit",
        inverse="_inverse_credit",
        store=True,
        help="Amount posted to the credit side of 'account_id', in the "
        "company currency. Derived from 'balance'; editing this directly "
        "updates 'balance' (and therefore 'debit') back. See 'debit' for "
        "why this is deliberately not 'precompute=True'.",
    )
    balance = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_balance",
        store=True,
        readonly=False,
        precompute=True,
        help="Signed amount in the company currency: 'debit' minus "
        "'credit'. This is the primary field the other three fill "
        "directions ('debit'/'credit', 'amount_currency') all resolve "
        "back into -- see this class's docstring for why a two-hop "
        "inverse chain does not reliably cascade during create(). Safe "
        "to keep 'precompute=True' here (unlike 'debit'/'credit'/"
        "'amount_currency'): its compute is a self-referential no-op "
        "when a value already exists, so an early pre-insert pass never "
        "clobbers a value one of the other three inverses sets later in "
        "the same create() call.",
    )
    amount_currency = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_amount_currency",
        inverse="_inverse_amount_currency",
        store=True,
        readonly=False,
        help="Signed amount expressed in 'currency_id'. Equal to "
        "'balance' when the line's currency is the company currency; "
        "editing this directly on a foreign-currency line recomputes "
        "'balance' (and therefore 'debit'/'credit') using 'currency_rate'. "
        "See 'debit' for why this is deliberately not 'precompute=True'.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        required=True,
        precompute=True,
        help="Currency 'amount_currency' is expressed in. Defaults to the "
        "parent journal entry's currency.",
    )
    currency_rate = fields.Float(
        compute="_compute_currency_rate",
        store=True,
        digits=0,
        help="Technical field: rate used to convert 'balance' (company "
        "currency) into 'amount_currency' (line currency).",
    )
    is_same_currency = fields.Boolean(
        compute="_compute_is_same_currency",
        help="Technical field: whether 'currency_id' is the company currency.",
    )
    cumulated_balance = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_cumulated_balance",
        help="Running total of 'balance' over the lines currently listed, "
        "in the order they are listed (e.g. by date).",
    )
    date_maturity = fields.Date(
        help="Optional due date for this line.",
    )
    tax_ids = fields.Many2many(
        comodel_name="tax",
        string="Taxes",
        help="Taxes applying to this line when 'display_type' is 'product'.",
    )
    group_tax_id = fields.Many2one(
        comodel_name="tax",
        string="Originator Group of Taxes",
        help="Technical field: which 'Group of Taxes' this line was "
        "generated for, when 'display_type' is 'tax'.",
    )
    tax_line_id = fields.Many2one(
        comodel_name="tax",
        string="Originator Tax",
        related="tax_repartition_line_id.tax_id",
        store=True,
        index="btree_not_null",
        help="Technical field: which tax this line represents, when "
        "'display_type' is 'tax'. Derived from 'tax_repartition_line_id' "
        "-- ported (behaviour-wise) from upstream "
        "'account.move.line.tax_line_id', which is the same related "
        "field; not set directly by '_sync_tax_lines'/'_prepare_tax_lines' "
        "grouping keys, exactly like upstream.",
    )
    tax_group_id = fields.Many2one(
        comodel_name="tax_group",
        related="tax_line_id.tax_group_id",
        store=True,
        help="Technical field: mirrors 'tax_line_id.tax_group_id'.",
    )
    tax_base_amount = fields.Monetary(
        currency_field="company_currency_id",
        help="Technical field: base amount this tax line was computed from.",
    )
    tax_repartition_line_id = fields.Many2one(
        comodel_name="tax.repartition_line",
        string="Originator Tax Repartition Line",
        index="btree_not_null",
        help="Technical field: which tax repartition line this line was "
        "generated from, when 'display_type' is 'tax'.",
    )
    tax_tag_ids = fields.Many2many(
        comodel_name="account.tag",
        string="Tax Grids",
        domain=[("applicability", "=", "taxes")],
        help="Technical field: tax grid tags carried over from the "
        "originating tax repartition line.",
    )
    extra_tax_data = fields.Json(
        help="Technical field: opaque tax computation detail attached to "
        "this line by the (not yet wired) tax synchronisation engine.",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        help="Optional product this line is related to. Informational "
        "only -- ignored by the tax engine, see the class docstring.",
    )
    quantity = fields.Float(
        default=1.0,
        help="Informational only -- ignored by the tax engine, see the "
        "class docstring.",
    )
    price_unit = fields.Float(
        string="Unit Price",
        help="Informational only -- ignored by the tax engine, see the "
        "class docstring.",
    )
    discount = fields.Float(
        string="Discount (%)",
        help="Informational only -- ignored by the tax engine, see the "
        "class docstring.",
    )
    price_subtotal = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_totals",
        store=True,
        help="Mirrors 'amount_currency' on 'product' lines -- see the "
        "class docstring for why this is not 'quantity * price_unit'.",
    )
    price_total = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_totals",
        store=True,
        help="Mirrors 'amount_currency' on 'product' lines -- see the "
        "class docstring. No tax amount is added: tax synchronisation is "
        "a separate, later unit.",
    )

    # === Reconciliation fields ===
    # Ported (behaviour-wise) from upstream ``account.move.line``'s own
    # reconciliation fields -- see ``reconcile()``/the RECONCILIATION
    # methods section near the end of this class for the algorithm
    # writing them, and ``reconcile_partial``/``reconcile_full`` for the
    # two models they point to.
    reconciled = fields.Boolean(
        compute="_compute_amount_residual",
        store=True,
        help="Whether this line's residual is fully matched by "
        "'reconcile_partial'/'reconcile_full' rows.",
    )
    amount_residual = fields.Monetary(
        string="Residual Amount",
        currency_field="company_currency_id",
        compute="_compute_amount_residual",
        store=True,
        help="The residual amount on this line, in the company currency. "
        "Zero once fully reconciled, the original 'balance' while "
        "unreconciled, something in-between while partially reconciled.",
    )
    amount_residual_currency = fields.Monetary(
        string="Residual Amount in Currency",
        currency_field="currency_id",
        compute="_compute_amount_residual",
        store=True,
        help="The residual amount on this line, in 'currency_id'.",
    )
    full_reconcile_id = fields.Many2one(
        comodel_name="reconcile_full",
        string="Matching",
        copy=False,
        index="btree_not_null",
        readonly=True,
        help="Set once this line is matched down to zero residual -- see "
        "'reconcile_partial._update_matching_number'.",
    )
    matched_debit_ids = fields.One2many(
        comodel_name="reconcile_partial",
        inverse_name="credit_move_id",
        string="Matched Debits",
        readonly=True,
        help="Debit journal items matched with this (credit) journal item.",
    )
    matched_credit_ids = fields.One2many(
        comodel_name="reconcile_partial",
        inverse_name="debit_move_id",
        string="Matched Credits",
        readonly=True,
        help="Credit journal items matched with this (debit) journal item.",
    )
    reconciled_lines_ids = fields.Many2many(
        comodel_name="journal_entry.item",
        compute="_compute_reconciled_lines_ids",
        compute_sudo=True,
        help="Technical field: the other side of every "
        "'matched_debit_ids'/'matched_credit_ids' pairing on this line, "
        "restricted to lines readable by the current user.",
    )
    matching_number = fields.Char(
        string="Matching #",
        copy=False,
        index=True,
        help="'P<n>' while only partially reconciled, or the id of the "
        "'reconcile_full' row once fully reconciled. Written exclusively "
        "by 'reconcile_partial._update_matching_number' -- see its own "
        "docstring; this field has no 'compute=' of its own.",
    )
    exchange_move_ids = fields.Many2many(
        comodel_name="journal_entry",
        compute="_compute_exchange_move_ids",
        compute_sudo=True,
        help="Currency exchange difference entries generated for this "
        "line by 'reconcile_partial'/'journal_entry.item"
        "._create_exchange_difference_moves' -- empty for a line never "
        "reconciled across currencies.",
    )

    _check_accountable_required_fields = models.Constraint(
        "CHECK(display_type IN ('line_section', 'line_note') "
        "OR account_id IS NOT NULL)",
        "Missing required account on accountable line.",
    )
    _check_non_accountable_fields_null = models.Constraint(
        "CHECK(display_type NOT IN ('line_section', 'line_note') "
        "OR (amount_currency = 0 AND debit = 0 AND credit = 0 "
        "AND account_id IS NULL))",
        "Forbidden balance or account on non-accountable line.",
    )

    @api.depends("display_type")
    def _compute_balance(self):
        """Default 'balance' to itself (i.e. leave it alone) or zero it.

        'balance' has no inverse and is the field the other three fill
        directions ('debit'/'credit', 'amount_currency') resolve back
        into (see the field's own docstring) -- it must stay directly
        writable. Reading 'line.balance' back inside its own compute is
        the same self-referential "keep whatever was already
        precomputed/given, else fall back" pattern already used by
        'journal_entry._compute_currency_id'/this model's own
        '_compute_currency_id': for a line whose 'balance' was passed in
        create()/write() vals, the ORM already marked it "determined"
        before this compute runs, so the assignment below is a no-op;
        for one where it was not, 'line.balance' reads back the type's
        empty value (0.0), which is the correct default anyway.
        """
        for line in self:
            if line.display_type in ("line_section", "line_note"):
                line.balance = 0.0
            else:
                line.balance = line.balance or 0.0

    @api.depends("balance")
    def _compute_debit_credit(self):
        for line in self:
            line.debit = line.balance if line.balance > 0.0 else 0.0
            line.credit = -line.balance if line.balance < 0.0 else 0.0

    def _inverse_debit(self):
        for line in self:
            if line.debit:
                line.credit = 0.0
            line.balance = line.debit - line.credit

    def _inverse_credit(self):
        for line in self:
            if line.credit:
                line.debit = 0.0
            line.balance = line.debit - line.credit

    @api.depends("move_id.currency_id", "move_id.company_id.currency_id")
    def _compute_currency_id(self):
        """Default 'currency_id' from the parent entry, then the company.

        Reads through 'move_id' directly (not the stored, related
        'company_currency_id') and falls back to 'self.env.company' -- a
        plain, always-synchronously-resolvable field -- as the last
        resort, so this required field never precomputes to an empty
        value while 'move_id'/'company_currency_id' are still settling
        during create(). See 'journal_entry._compute_currency_id' for the
        same pattern on the header.
        """
        for line in self:
            line.currency_id = (
                line.move_id.currency_id
                or line.move_id.company_id.currency_id
                or line.currency_id
                or self.env.company.currency_id
            )

    @api.depends("currency_id", "company_currency_id")
    def _compute_is_same_currency(self):
        for line in self:
            line.is_same_currency = line.currency_id == line.company_currency_id

    @api.depends("currency_id", "company_id", "date")
    def _compute_currency_rate(self):
        for line in self:
            if not line.currency_id or line.currency_id == line.company_currency_id:
                line.currency_rate = 1.0
                continue
            line.currency_rate = self.env["res.currency"]._get_conversion_rate(
                from_currency=line.company_currency_id,
                to_currency=line.currency_id,
                company=line.company_id,
                date=line.date or fields.Date.context_today(line),
            )

    @api.depends("balance", "currency_rate", "is_same_currency")
    def _compute_amount_currency(self):
        for line in self:
            if line.is_same_currency:
                line.amount_currency = line.balance
            else:
                line.amount_currency = line.currency_id.round(
                    line.balance * line.currency_rate
                )

    def _inverse_amount_currency(self):
        for line in self:
            if line.is_same_currency:
                line.balance = line.amount_currency
            elif line.currency_rate:
                line.balance = line.company_currency_id.round(
                    line.amount_currency / line.currency_rate
                )

    def _compute_cumulated_balance(self):
        """Running total of 'balance' in the order lines are listed.

        Not stored and not ``@api.depends``-triggered on purpose: the
        value is meaningful only relative to the order of the current
        recordset (e.g. a list view sorted by date), not to any single
        line in isolation, exactly like upstream's own
        ``AccountMoveLine.cumulated_balance``.
        """
        cumulated_balance = 0.0
        for line in self:
            cumulated_balance += line.balance
            line.cumulated_balance = cumulated_balance

    @api.depends("amount_currency", "discount", "display_type")
    def _compute_totals(self):
        for line in self:
            if line.display_type != "product":
                line.price_subtotal = 0.0
                line.price_total = 0.0
                continue
            line.price_subtotal = line.amount_currency
            line.price_total = line.amount_currency

    @api.depends(
        "balance",
        "amount_currency",
        "account_id",
        "currency_id",
        "company_id",
        "matched_debit_ids",
        "matched_credit_ids",
    )
    def _compute_amount_residual(self):
        """Residual amount left on a reconcilable line, in both currencies.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._compute_amount_residual``, trimmed of the
        ``asset_cash``/``liability_credit_card`` exemption upstream
        grants ``need_residual_lines`` -- this issue's Keputusan Desain
        restricts reconciliation strictly to ``account_id.reconcile ==
        True`` (bank-statement reconciliation, the upstream exemption's
        only real use, is out of this issue's scope -- see the class
        docstring and ``_check_amls_exigibility_for_reconciliation``).
        """
        need_residual_lines = self.filtered(lambda line: line.account_id.reconcile)
        stored_lines = need_residual_lines._origin

        if stored_lines:
            self.env["reconcile_partial"].flush_model()
            self.env["res.currency"].flush_model(["decimal_places"])
            aml_ids = tuple(stored_lines.ids)
            self.env.cr.execute(
                """
                SELECT
                    part.debit_move_id AS line_id,
                    'debit' AS flag,
                    COALESCE(SUM(part.amount), 0.0) AS amount,
                    ROUND(SUM(part.debit_amount_currency), curr.decimal_places)
                        AS amount_currency
                  FROM reconcile_partial part
                  JOIN res_currency curr ON curr.id = part.debit_currency_id
                 WHERE part.debit_move_id IN %s
              GROUP BY part.debit_move_id, curr.decimal_places
                UNION ALL
                SELECT
                    part.credit_move_id AS line_id,
                    'credit' AS flag,
                    COALESCE(SUM(part.amount), 0.0) AS amount,
                    ROUND(SUM(part.credit_amount_currency), curr.decimal_places)
                        AS amount_currency
                  FROM reconcile_partial part
                  JOIN res_currency curr ON curr.id = part.credit_currency_id
                 WHERE part.credit_move_id IN %s
              GROUP BY part.credit_move_id, curr.decimal_places
                """,
                [aml_ids, aml_ids],
            )
            amounts_map = {
                (line_id, flag): (amount, amount_currency)
                for line_id, flag, amount, amount_currency in self.env.cr.fetchall()
            }
        else:
            amounts_map = {}

        for line in self - need_residual_lines:
            line.amount_residual = 0.0
            line.amount_residual_currency = 0.0
            line.reconciled = False

        for line in need_residual_lines:
            comp_curr = line.company_currency_id or self.env.company.currency_id
            foreign_curr = line.currency_id or comp_curr
            debit_amount, debit_amount_currency = amounts_map.get(
                (line._origin.id, "debit"), (0.0, 0.0)
            )
            credit_amount, credit_amount_currency = amounts_map.get(
                (line._origin.id, "credit"), (0.0, 0.0)
            )
            line.amount_residual = comp_curr.round(
                line.balance - debit_amount + credit_amount
            )
            line.amount_residual_currency = foreign_curr.round(
                line.amount_currency - debit_amount_currency + credit_amount_currency
            )
            line.reconciled = comp_curr.is_zero(
                line.amount_residual
            ) and foreign_curr.is_zero(line.amount_residual_currency)

    @api.depends("matched_debit_ids", "matched_credit_ids")
    def _compute_reconciled_lines_ids(self):
        """Mirror of the counterpart line of every matched partial.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._compute_reconciled_lines_ids``, trimmed of the
        Bankrec-widget-only ``first_reconciled_lines_id``/
        ``count_reconciled_lines`` fields and of its own ``inverse=``
        (that inverse exists solely to let the OWL widget drive
        ``reconcile()`` by assigning into this field -- this issue's UI
        wave drives it through ``action_reconcile`` instead, see the
        class docstring's "UI gelombang pertama" decision).
        """
        accessible_lines = set(
            (
                self.matched_debit_ids.debit_move_id
                + self.matched_credit_ids.credit_move_id
            )._filtered_access("read")
        )
        for line in self:
            line.sudo().reconciled_lines_ids = (
                line.matched_debit_ids.debit_move_id
                + line.matched_credit_ids.credit_move_id
            ).filtered(accessible_lines.__contains__)

    @api.depends(
        "matched_debit_ids.exchange_move_id", "matched_credit_ids.exchange_move_id"
    )
    def _compute_exchange_move_ids(self):
        """The exchange difference entries generated for this line's own partials.

        Field penghubung per this issue's Keputusan Desain: mirrors
        every non-empty 'exchange_move_id' of this line's
        'matched_debit_ids'/'matched_credit_ids' -- set by
        'journal_entry.item._reconcile_plan_with_sync' once
        '_create_exchange_difference_moves' returns.
        """
        for line in self:
            line.sudo().exchange_move_ids = (
                line.matched_debit_ids.exchange_move_id
                + line.matched_credit_ids.exchange_move_id
            )

    @api.model_create_multi
    def create(self, vals_list):
        """Force-refresh 'debit'/'credit'/'amount_currency' after insert.

        Belt-and-suspenders fix for a ``create()``-only ordering pitfall
        between ``@api.depends`` recompute and given-field ``inverse``
        calls (see the class docstring): when only ``amount_currency``
        is supplied for a new line, ``balance`` does end up correct (its
        own inverse-driven update always wins), but ``debit``/``credit``
        -- computed from ``balance`` -- were observed (CI, not just
        theory) to sometimes still reflect ``balance``'s pre-inverse
        value instead of the final one, because exactly *when* within
        ``create()`` a dependent's compute runs relative to another
        field's inverse is an ORM implementation detail this module
        cannot rely on. Explicitly recomputing both here, strictly
        *after* ``super().create()`` has fully returned (so ``balance``
        is unquestionably settled by then, whichever of the three fill
        directions produced it), removes that dependency entirely.
        Idempotent when 'debit'/'credit'/'amount_currency' were already
        consistent with 'balance' (the common case), so it is safe to
        run unconditionally rather than only for lines missing them.

        **Run under both sync-suppression flags**: this refresh assigns
        ``line.debit``/``line.credit``/``line.amount_currency``
        field-by-field, and Odoo's own field setter turns each assignment
        into a ``write()`` -- which, since ``debit``'s/``credit``'s
        ``inverse`` methods themselves assign back into ``balance``
        (see the class docstring), cascades into further nested
        ``write()`` calls. Every one of them would otherwise reach this
        model's ``write()`` override (re-running the full tax/balancing
        sync, redundantly, once per cascaded assignment) and its
        ``_check_balanced_constrains``/explicit final check (rejecting a
        taxed line whose own tax line has not been synced yet -- that
        only happens afterwards, in ``journal_entry._sync_tax_lines``).
        ``skip_journal_entry_sync_dynamic_lines`` suppresses the former
        (the same flag ``journal_entry.write()``'s own recursion guard
        uses), ``skip_check_balanced_constrains`` the latter -- see
        ``journal_entry._check_balanced_constrains``'s docstring for the
        full reasoning on that second one. ``records`` itself (returned
        below) is never rebound to the flagged context, so nothing leaks
        to the caller.
        """
        records = super().create(vals_list)
        refresh = records.with_context(
            skip_check_balanced_constrains=True,
            skip_journal_entry_sync_dynamic_lines=True,
        )
        refresh._compute_debit_credit()
        refresh._compute_amount_currency()
        return records

    def write(self, vals):
        """Trigger the parent entry's tax/balancing sync around a direct write.

        Necessary counterpart to ``journal_entry.write()``: the ORM
        applies a one2many command like ``(1, id, vals)`` by calling this
        model's own ``write()`` directly on the child, never re-entering
        ``journal_entry.write()`` -- so without this override, editing a
        line directly (the only way this repo's own "Journal Entry" form
        edits an existing row today, and the natural way to write a test
        against a single line) would bypass ``_sync_dynamic_lines``
        entirely and no tax line would ever be (re)computed. Ported
        (behaviour-wise, trimmed to this repo's scope -- no lock date/
        hash/reconciliation/tracking machinery, none of which exists here
        yet) from upstream ``AccountMoveLine.write``, which wraps its own
        ``super().write()`` the same way for the same reason.

        **Guarded with the same ``journal_entry._disable_recursion`` flag
        ``journal_entry.write()`` itself uses**, and for the same reason:
        without it, a header-side ``line_ids: [(1, id, vals), ...]`` write
        would re-enter the full sync stack once per line (each nested
        child ``write()`` re-running ``_sync_dynamic_lines`` on top of the
        header's own already-in-progress pass), and
        ``_sync_tax_lines``'s own bulk ``journal_entry.item.write()`` calls
        (creating/updating/deleting tax lines) would recursively
        re-trigger themselves. Both cases share this method's env/context
        with whichever ``journal_entry.write()``/``_sync_tax_lines`` call
        is already running, so the shared flag correctly recognises them
        and skips the redundant nested pass; a write reaching this method
        on its own (the common case -- editing a line directly, e.g. from
        a list view or a test) carries no such flag and syncs normally.

        **Also suppresses ``skip_check_balanced_constrains`` for the
        duration of the whole ``_sync_dynamic_lines`` cycle** (not just
        ``super().write()`` itself -- its post-yield tax/balancing stages
        can just as well force an unrelated compute to flush and
        re-validate every line's balance mid-cycle, see
        ``journal_entry.create()``'s docstring), then validates balance
        explicitly, exactly once, right after the sync closes -- same
        reasoning as ``journal_entry.write()``'s own docstring: a
        coordinated multi-line write (e.g. a taxed base line and its tax
        line updated together) is genuinely, transiently unbalanced
        between the individual child writes the ORM issues one at a
        time, and the eager constrain would otherwise reject it over
        that transient state.
        """
        if not vals:
            return True
        moves = self.move_id
        move_container = {"records": moves}
        if moves and moves._disable_recursion(
            move_container, "journal_entry_sync_dynamic_lines"
        ):
            return super().write(vals)
        flagged_moves = move_container["records"].with_context(
            skip_check_balanced_constrains=True
        )
        move_container["records"] = flagged_moves
        with flagged_moves._sync_dynamic_lines(move_container):
            result = super(
                JournalEntryItem,
                self.with_context(skip_check_balanced_constrains=True),
            ).write(vals)
        moves._check_balanced({"records": moves})
        return result

    def _affect_tax_report(self):
        """Whether this line carries a tax that affects the tax report.

        Ported verbatim (behaviour-wise) from upstream
        ``account.move.line._affect_tax_report``. Read by
        ``journal_entry._affect_tax_report``/``_post`` to decide
        whether ``tax_lock_date`` applies when posting.
        """
        self.ensure_one()
        return bool(
            self.tax_ids
            or self.tax_line_id
            or self.tax_tag_ids.filtered(lambda tag: tag.applicability == "taxes")
        )

    @api.constrains("debit", "credit")
    def _check_balanced_constrains(self):
        """Validate balance when a line's own debit/credit is written directly.

        Complements ``journal_entry._check_balanced_constrains``: a
        dotted constrain on the header (``"line_ids.debit"``) is not
        valid Odoo syntax and would silently do nothing (see that
        method's docstring) -- it does not fire when this line is
        written on its own (e.g. ``line.write({'debit': ...})``), which
        is exactly how inline list-view edits happen. Defining the
        constrain here, triggered by this model's own fields, covers
        that case -- same split as
        ``tax.repartition_line._check_repartition_line_factor``'s
        docstring already explains for a different model.

        **Skipped while ``create()``/``write()`` are still mid-sync**
        (flagged via ``skip_check_balanced_constrains``) -- see
        ``journal_entry._check_balanced_constrains``'s docstring for why.
        """
        if self.env.context.get("skip_check_balanced_constrains"):
            return
        for move in self.move_id:
            move._check_balanced({"records": move})

    # ======================================================================
    # RECONCILIATION
    # ======================================================================
    # Ported (behaviour-wise) from Odoo core ``account_move_line.py``'s own
    # "RECONCILIATION" section. Everything invoice/payment/cash-basis/
    # exchange-difference shaped is dropped -- see this issue's Keputusan
    # Desain and the individual method docstrings below for what and why.

    def _get_reconciliation_aml_field_value(self, field, shadowed_aml_values):
        """Read 'field' off 'self', unless 'shadowed_aml_values' overrides it.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._get_reconciliation_aml_field_value``.
        """
        self.ensure_one()
        if shadowed_aml_values and field in shadowed_aml_values.get(self, {}):
            return shadowed_aml_values[self][field]
        return self[field]

    @api.model
    def _prepare_move_line_residual_amounts(
        self,
        aml_values,
        counterpart_currency,
        shadowed_aml_values=None,
        other_aml_values=None,
    ):
        """Available residual amounts of one line, per currency it could reconcile in.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._prepare_move_line_residual_amounts``, trimmed
        of the ``is_payment()``/``is_invoice()`` branches inside its
        nested ``get_odoo_rate`` -- neither payment nor invoice is a
        concept on this model (see the class docstring), so the
        accounting-rate date always falls back to the line's own 'date'.
        ``other_aml_values`` is kept in the signature (unused) only to
        match the caller's shape -- it fed exclusively into the dropped
        ``is_payment(other_aml)`` check upstream.

        :param aml_values: this line's 'aml'/'amount_residual'/
            'amount_residual_currency' dict (see '_reconcile_plan_with_sync').
        :param counterpart_currency: the currency of the line this one is
            being matched against.
        :param shadowed_aml_values: optional aml -> dict override, used to
            preview a reconciliation before committing field changes.
        :param other_aml_values: unused, see above.
        :return: a mapping currency -> {'residual': ..., 'rate': ...}.
        """
        del other_aml_values

        def get_odoo_rate(aml, currency):
            if forced_rate := self.env.context.get("forced_rate_from_register_payment"):
                return forced_rate
            exchange_rate_date = aml._get_reconciliation_aml_field_value(
                "date", shadowed_aml_values
            )
            return currency._get_conversion_rate(
                aml.company_currency_id, currency, aml.company_id, exchange_rate_date
            )

        def get_accounting_rate(aml, currency):
            balance = aml._get_reconciliation_aml_field_value(
                "balance", shadowed_aml_values
            )
            amount_currency = aml._get_reconciliation_aml_field_value(
                "amount_currency", shadowed_aml_values
            )
            if not aml.company_currency_id.is_zero(balance) and not currency.is_zero(
                amount_currency
            ):
                return abs(amount_currency / balance)
            return None

        aml = aml_values["aml"]
        remaining_amount_curr = aml_values["amount_residual_currency"]
        remaining_amount = aml_values["amount_residual"]
        company_currency = aml.company_currency_id
        currency = aml._get_reconciliation_aml_field_value(
            "currency_id", shadowed_aml_values
        )
        account = aml._get_reconciliation_aml_field_value(
            "account_id", shadowed_aml_values
        )
        has_zero_residual = company_currency.is_zero(remaining_amount)
        has_zero_residual_currency = currency.is_zero(remaining_amount_curr)
        is_rec_pay_account = account.account_type in (
            "asset_receivable",
            "liability_payable",
        )

        available_residual_per_currency = {}

        if not has_zero_residual:
            available_residual_per_currency[company_currency] = {
                "residual": remaining_amount,
                "rate": 1,
            }
        if currency != company_currency and not has_zero_residual_currency:
            available_residual_per_currency[currency] = {
                "residual": remaining_amount_curr,
                "rate": get_accounting_rate(aml, currency),
            }

        if (
            currency == company_currency
            and is_rec_pay_account
            and not has_zero_residual
            and counterpart_currency != company_currency
        ):
            rate = get_odoo_rate(aml, counterpart_currency)
            residual_in_foreign_curr = counterpart_currency.round(
                remaining_amount * rate
            )
            if not counterpart_currency.is_zero(residual_in_foreign_curr):
                available_residual_per_currency[counterpart_currency] = {
                    "residual": residual_in_foreign_curr,
                    "rate": rate,
                }
        elif (
            currency == counterpart_currency
            and currency != company_currency
            and not has_zero_residual_currency
        ):
            available_residual_per_currency[counterpart_currency] = {
                "residual": remaining_amount_curr,
                "rate": get_accounting_rate(aml, currency),
            }
        return available_residual_per_currency

    @api.model
    def _prepare_reconciliation_single_partial_amounts(
        self,
        recon_currency,
        company_currency,
        debit_values,
        credit_values,
        debit_currency,
        credit_currency,
        debit_available_residual_amounts,
        credit_available_residual_amounts,
        min_recon_amount,
        exchange_line_mode,
    ):
        """The '<amount, debit_amount_currency, credit_amount_currency, ...>' tuple.

        Split out of '_prepare_reconciliation_single_partial' purely to
        keep that method under this repo's mccabe complexity budget -- a
        structural, not behavioural, deviation from a literal port (see
        'item-format.md' §0/§6). Ported (behaviour-wise) from the
        "Computation of partial amounts" section of upstream
        ``AccountMoveLine._prepare_reconciliation_single_partial``,
        rounding-avoidance block included: it corrects the *matching
        amount itself*, not an exchange-difference entry, so it applies
        regardless of currency.

        Also returns the intermediate ``partial_debit_amount``/
        ``partial_credit_amount`` (company-currency amounts each side
        alone would allow, before the final ``min()``/rounding-avoidance
        override) -- upstream's exchange-difference block reads those
        two directly; see
        '_prepare_reconciliation_single_partial_exchange_values_cross_currency'.
        In the ``recon_currency == company_currency`` branch neither
        concept exists (there is only ever one, shared, company-currency
        partial amount), so both simply mirror 'partial_amount' there --
        harmless, since that branch's caller never reads them.
        """
        remaining_debit_amount = debit_values["amount_residual"]
        remaining_credit_amount = credit_values["amount_residual"]

        def get_amount_range_after_rate(currency_from, currency_to, amount, rate):
            if not rate:
                return 0.0, 0.0, 0.0
            half_rounding = currency_from.rounding / 2
            return (
                currency_to.round((amount - half_rounding) * rate),
                currency_to.round(amount * rate),
                currency_to.round((amount + half_rounding) * rate),
            )

        if recon_currency == company_currency:
            if exchange_line_mode:
                debit_rate = None
                credit_rate = None
            else:
                debit_rate = debit_available_residual_amounts.get(
                    debit_currency, {}
                ).get("rate")
                credit_rate = credit_available_residual_amounts.get(
                    credit_currency, {}
                ).get("rate")

            partial_amount = min_recon_amount

            if debit_rate:
                partial_debit_amount_currency = min(
                    debit_currency.round(debit_rate * min_recon_amount),
                    debit_values["amount_residual_currency"],
                )
            else:
                partial_debit_amount_currency = 0.0
            if credit_rate:
                partial_credit_amount_currency = min(
                    credit_currency.round(credit_rate * min_recon_amount),
                    -credit_values["amount_residual_currency"],
                )
            else:
                partial_credit_amount_currency = 0.0
            return (
                partial_amount,
                partial_debit_amount_currency,
                partial_credit_amount_currency,
                partial_amount,
                partial_amount,
            )

        # recon_currency != company_currency
        if exchange_line_mode:
            debit_rate = None
            credit_rate = None
        else:
            debit_rate = debit_available_residual_amounts[recon_currency]["rate"]
            credit_rate = credit_available_residual_amounts[recon_currency]["rate"]

        partial_debit_amount_range = get_amount_range_after_rate(
            debit_currency,
            company_currency,
            min_recon_amount,
            (1 / debit_rate) if debit_rate else 0.0,
        )
        partial_debit_amount = min(
            partial_debit_amount_range[1], remaining_debit_amount
        )
        partial_credit_amount_range = get_amount_range_after_rate(
            credit_currency,
            company_currency,
            min_recon_amount,
            (1 / credit_rate) if credit_rate else 0.0,
        )
        partial_credit_amount = min(
            partial_credit_amount_range[1], -remaining_credit_amount
        )
        partial_amount = min(partial_debit_amount, partial_credit_amount)

        if (
            company_currency.compare_amounts(
                partial_debit_amount, partial_credit_amount_range[2]
            )
            <= 0
            and company_currency.compare_amounts(
                partial_debit_amount, partial_credit_amount_range[0]
            )
            >= 0
            and company_currency.compare_amounts(
                partial_credit_amount, partial_debit_amount_range[2]
            )
            <= 0
            and company_currency.compare_amounts(
                partial_credit_amount, partial_debit_amount_range[0]
            )
            >= 0
        ):
            partial_amount = min(remaining_debit_amount, -remaining_credit_amount)
            partial_debit_amount = partial_amount
            partial_credit_amount = partial_amount

        partial_debit_amount_currency = (
            partial_amount if debit_currency == company_currency else min_recon_amount
        )
        partial_credit_amount_currency = (
            partial_amount if credit_currency == company_currency else min_recon_amount
        )
        return (
            partial_amount,
            partial_debit_amount_currency,
            partial_credit_amount_currency,
            partial_debit_amount,
            partial_credit_amount,
        )

    def _prepare_reconciliation_single_partial_exchange_values_same_currency(
        self,
        debit_values,
        credit_values,
        debit_currency,
        credit_currency,
        partial_debit_amount_currency,
        partial_credit_amount_currency,
        debit_fully_matched,
        credit_fully_matched,
    ):
        """The 'recon_currency == company_currency' half of the exchange block.

        Split out of
        '_prepare_reconciliation_single_partial_exchange_values' purely
        to keep it under this repo's mccabe complexity budget -- a
        structural, not behavioural, deviation from a literal port (see
        'item-format.md' §0/§6). Ported (behaviour-wise) from the first
        branch of the "Computation of the partial exchange difference"
        section of upstream
        ``AccountMoveLine._prepare_reconciliation_single_partial``.
        Mutates 'debit_values'/'credit_values' in place, see the
        caller's own docstring.

        :return: '(exchange_lines_to_fix, amounts_list)', both possibly
            empty.
        """
        exchange_lines_to_fix = self.env["journal_entry.item"]
        amounts_list = []
        if debit_fully_matched:
            debit_exchange_amount = (
                debit_values["amount_residual_currency"] - partial_debit_amount_currency
            )
            if not debit_currency.is_zero(debit_exchange_amount):
                exchange_lines_to_fix += debit_values["aml"]
                amounts_list.append({"amount_residual_currency": debit_exchange_amount})
                debit_values["amount_residual_currency"] -= debit_exchange_amount
        if credit_fully_matched:
            credit_exchange_amount = (
                credit_values["amount_residual_currency"]
                + partial_credit_amount_currency
            )
            if not credit_currency.is_zero(credit_exchange_amount):
                exchange_lines_to_fix += credit_values["aml"]
                amounts_list.append(
                    {"amount_residual_currency": credit_exchange_amount}
                )
                credit_values["amount_residual_currency"] += credit_exchange_amount
        return exchange_lines_to_fix, amounts_list

    def _prepare_reconciliation_single_partial_exchange_values_cross_currency(
        self,
        debit_values,
        credit_values,
        company_currency,
        debit_currency,
        credit_currency,
        partial_amount,
        partial_debit_amount,
        partial_credit_amount,
        debit_fully_matched,
        credit_fully_matched,
    ):
        """The 'recon_currency != company_currency' half of the exchange block.

        Split out of
        '_prepare_reconciliation_single_partial_exchange_values' purely
        to keep it under this repo's mccabe complexity budget -- a
        structural, not behavioural, deviation from a literal port (see
        'item-format.md' §0/§6). Ported (behaviour-wise) from the second
        branch of the "Computation of the partial exchange difference"
        section of upstream
        ``AccountMoveLine._prepare_reconciliation_single_partial``.
        Mutates 'debit_values'/'credit_values' in place, see the
        caller's own docstring.

        :return: '(exchange_lines_to_fix, amounts_list)', both possibly
            empty.
        """
        exchange_lines_to_fix = self.env["journal_entry.item"]
        amounts_list = []
        if debit_fully_matched:
            debit_exchange_amount = debit_values["amount_residual"] - partial_amount
            if not company_currency.is_zero(debit_exchange_amount):
                exchange_lines_to_fix += debit_values["aml"]
                amounts_list.append({"amount_residual": debit_exchange_amount})
                debit_values["amount_residual"] -= debit_exchange_amount
                if debit_currency == company_currency:
                    debit_values["amount_residual_currency"] -= debit_exchange_amount
        else:
            debit_exchange_amount = partial_debit_amount - partial_amount
            if company_currency.compare_amounts(debit_exchange_amount, 0.0) > 0:
                exchange_lines_to_fix += debit_values["aml"]
                amounts_list.append({"amount_residual": debit_exchange_amount})
                debit_values["amount_residual"] -= debit_exchange_amount
                if debit_currency == company_currency:
                    debit_values["amount_residual_currency"] -= debit_exchange_amount

        if credit_fully_matched:
            credit_exchange_amount = credit_values["amount_residual"] + partial_amount
            if not company_currency.is_zero(credit_exchange_amount):
                exchange_lines_to_fix += credit_values["aml"]
                amounts_list.append({"amount_residual": credit_exchange_amount})
                credit_values["amount_residual"] -= credit_exchange_amount
                if credit_currency == company_currency:
                    credit_values["amount_residual_currency"] -= credit_exchange_amount
        else:
            credit_exchange_amount = partial_amount - partial_credit_amount
            if company_currency.compare_amounts(credit_exchange_amount, 0.0) < 0:
                exchange_lines_to_fix += credit_values["aml"]
                amounts_list.append({"amount_residual": credit_exchange_amount})
                credit_values["amount_residual"] -= credit_exchange_amount
                if credit_currency == company_currency:
                    credit_values["amount_residual_currency"] -= credit_exchange_amount
        return exchange_lines_to_fix, amounts_list

    def _prepare_reconciliation_single_partial_exchange_values(
        self,
        debit_values,
        credit_values,
        recon_currency,
        company_currency,
        debit_currency,
        credit_currency,
        partial_amount,
        partial_debit_amount_currency,
        partial_credit_amount_currency,
        partial_debit_amount,
        partial_credit_amount,
        debit_fully_matched,
        credit_fully_matched,
        shadowed_aml_values=None,
    ):
        """Compute (and apply) the exchange-difference adjustment of one partial.

        Restores the block '_prepare_reconciliation_single_partial'
        dropped when this repo's reconciliation unit (#12) landed --
        see that method's own docstring: selisih kurs entry generation
        is this unit's (#13) job. Ported (behaviour-wise) from the
        "Computation of the partial exchange difference" section of
        upstream ``AccountMoveLine._prepare_reconciliation_single_partial``,
        split into the two ``_..._same_currency``/``_..._cross_currency``
        helpers above purely to keep every individual method under this
        repo's mccabe complexity budget (see 'item-format.md' §0/§6) --
        a structural, not behavioural, deviation from upstream's own
        single, longer branch.

        Skipped entirely via the 'no_exchange_difference'/
        'no_exchange_difference_no_recursive' context keys, exactly like
        upstream -- set by '_create_exchange_difference_moves' while
        reconciling an exchange move's own line against the original
        one, so that second reconciliation does not recurse into
        generating a further exchange difference.

        Mutates 'debit_values'/'credit_values' in place -- whatever
        amount this exchange difference absorbs is subtracted from them
        here, exactly like the caller already does for the partial
        amount itself right after calling this method.

        :return: the 'exchange_values' dict for
            '_create_exchange_difference_moves' (see
            '_prepare_exchange_difference_move_vals'), or 'None' if no
            exchange difference is needed for this partial.
        """
        if self.env.context.get("no_exchange_difference") or self.env.context.get(
            "no_exchange_difference_no_recursive"
        ):
            return None

        if recon_currency == company_currency:
            exchange_lines_to_fix, amounts_list = (
                self._prepare_reconciliation_single_partial_exchange_values_same_currency(
                    debit_values,
                    credit_values,
                    debit_currency,
                    credit_currency,
                    partial_debit_amount_currency,
                    partial_credit_amount_currency,
                    debit_fully_matched,
                    credit_fully_matched,
                )
            )
        else:
            exchange_lines_to_fix, amounts_list = (
                self._prepare_reconciliation_single_partial_exchange_values_cross_currency(
                    debit_values,
                    credit_values,
                    company_currency,
                    debit_currency,
                    credit_currency,
                    partial_amount,
                    partial_debit_amount,
                    partial_credit_amount,
                    debit_fully_matched,
                    credit_fully_matched,
                )
            )

        if not exchange_lines_to_fix:
            return None

        debit_aml = debit_values["aml"]
        credit_aml = credit_values["aml"]
        exchange_values = exchange_lines_to_fix._prepare_exchange_difference_move_vals(
            amounts_list,
            exchange_date=max(
                debit_aml._get_reconciliation_aml_field_value(
                    "date", shadowed_aml_values
                ),
                credit_aml._get_reconciliation_aml_field_value(
                    "date", shadowed_aml_values
                ),
            ),
        )
        exchange_values["to_post"] = (
            debit_aml.parent_state == "posted" and credit_aml.parent_state == "posted"
        )
        return exchange_values

    @api.model
    def _prepare_reconciliation_single_partial(
        self, debit_values, credit_values, shadowed_aml_values=None
    ):
        """Compute one 'reconcile_partial''s worth of matching between two lines.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._prepare_reconciliation_single_partial``. A
        prior unit (#12) trimmed the whole exchange-difference-vals
        block (``res['exchange_values']`` and everything computing it),
        deferring selisih kurs entry generation to this, later,
        dedicated currency unit -- restored below via
        '_prepare_reconciliation_single_partial_exchange_values', split
        out to keep this method under this repo's mccabe complexity
        budget (see 'item-format.md' §0/§6).
        """
        res = {"debit_values": debit_values, "credit_values": credit_values}
        debit_aml = debit_values["aml"]
        credit_aml = credit_values["aml"]
        debit_currency = debit_aml._get_reconciliation_aml_field_value(
            "currency_id", shadowed_aml_values
        )
        credit_currency = credit_aml._get_reconciliation_aml_field_value(
            "currency_id", shadowed_aml_values
        )
        company_currency = debit_aml.company_currency_id

        debit_available_residual_amounts = self._prepare_move_line_residual_amounts(
            debit_values,
            credit_currency,
            shadowed_aml_values=shadowed_aml_values,
            other_aml_values=credit_values,
        )
        credit_available_residual_amounts = self._prepare_move_line_residual_amounts(
            credit_values,
            debit_currency,
            shadowed_aml_values=shadowed_aml_values,
            other_aml_values=debit_values,
        )

        if (
            debit_currency != company_currency
            and debit_currency in debit_available_residual_amounts
            and debit_currency in credit_available_residual_amounts
        ):
            recon_currency = debit_currency
        elif (
            credit_currency != company_currency
            and credit_currency in debit_available_residual_amounts
            and credit_currency in credit_available_residual_amounts
        ):
            recon_currency = credit_currency
        else:
            recon_currency = company_currency

        debit_recon_values = debit_available_residual_amounts.get(recon_currency)
        credit_recon_values = credit_available_residual_amounts.get(recon_currency)

        if not debit_recon_values:
            res["debit_values"] = None
        if not credit_recon_values:
            res["credit_values"] = None
        if res["debit_values"] is None or res["credit_values"] is None:
            return res

        recon_debit_amount = debit_recon_values["residual"]
        recon_credit_amount = -credit_recon_values["residual"]
        min_recon_amount = min(recon_debit_amount, recon_credit_amount)

        # Which line is fully matched by the other -- read by the
        # exchange-difference block below, see its own docstring.
        compare_amounts = recon_currency.compare_amounts(
            recon_debit_amount, recon_credit_amount
        )
        debit_fully_matched = compare_amounts <= 0
        credit_fully_matched = compare_amounts >= 0

        exchange_line_mode = (
            recon_currency == company_currency
            and debit_currency == credit_currency
            and (
                not debit_available_residual_amounts.get(debit_currency)
                or not credit_available_residual_amounts.get(credit_currency)
            )
        )

        (
            partial_amount,
            partial_debit_amount_currency,
            partial_credit_amount_currency,
            partial_debit_amount,
            partial_credit_amount,
        ) = self._prepare_reconciliation_single_partial_amounts(
            recon_currency,
            company_currency,
            debit_values,
            credit_values,
            debit_currency,
            credit_currency,
            debit_available_residual_amounts,
            credit_available_residual_amounts,
            min_recon_amount,
            exchange_line_mode,
        )

        exchange_values = self._prepare_reconciliation_single_partial_exchange_values(
            debit_values,
            credit_values,
            recon_currency,
            company_currency,
            debit_currency,
            credit_currency,
            partial_amount,
            partial_debit_amount_currency,
            partial_credit_amount_currency,
            partial_debit_amount,
            partial_credit_amount,
            debit_fully_matched,
            credit_fully_matched,
            shadowed_aml_values=shadowed_aml_values,
        )
        if exchange_values:
            res["exchange_values"] = exchange_values

        res["partial_values"] = {
            "amount": partial_amount,
            "debit_amount_currency": partial_debit_amount_currency,
            "credit_amount_currency": partial_credit_amount_currency,
            "debit_move_id": debit_aml.id,
            "credit_move_id": credit_aml.id,
        }

        debit_values["amount_residual"] -= partial_amount
        debit_values["amount_residual_currency"] -= partial_debit_amount_currency
        credit_values["amount_residual"] += partial_amount
        credit_values["amount_residual_currency"] += partial_credit_amount_currency

        if debit_currency.is_zero(
            debit_values["amount_residual_currency"]
        ) and company_currency.is_zero(debit_values["amount_residual"]):
            res["debit_values"] = None
        if credit_currency.is_zero(
            credit_values["amount_residual_currency"]
        ) and company_currency.is_zero(credit_values["amount_residual"]):
            res["credit_values"] = None
        return res

    @api.model
    def _prepare_reconciliation_amls(self, values_list, shadowed_aml_values=None):
        """Match debit lines against credit lines, in order, until none are left.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._prepare_reconciliation_amls``.
        """
        debit_values_list = iter(
            [
                x
                for x in values_list
                if x["aml"]._get_reconciliation_aml_field_value(
                    "balance", shadowed_aml_values
                )
                > 0.0
                or x["aml"]._get_reconciliation_aml_field_value(
                    "amount_currency", shadowed_aml_values
                )
                > 0.0
            ]
        )
        credit_values_list = iter(
            [
                x
                for x in values_list
                if x["aml"]._get_reconciliation_aml_field_value(
                    "balance", shadowed_aml_values
                )
                < 0.0
                or x["aml"]._get_reconciliation_aml_field_value(
                    "amount_currency", shadowed_aml_values
                )
                < 0.0
            ]
        )
        debit_values = None
        credit_values = None
        fully_reconciled_aml_ids = set()

        all_results = []
        while True:
            if not debit_values:
                debit_values = next(debit_values_list, None)
                if not debit_values:
                    break
            if not credit_values:
                credit_values = next(credit_values_list, None)
                if not credit_values:
                    break

            results = self._prepare_reconciliation_single_partial(
                debit_values, credit_values, shadowed_aml_values=shadowed_aml_values
            )
            if results.get("partial_values"):
                all_results.append(results)
            if results["debit_values"] is None:
                fully_reconciled_aml_ids.add(debit_values["aml"].id)
                debit_values = None
            if results["credit_values"] is None:
                fully_reconciled_aml_ids.add(credit_values["aml"].id)
                credit_values = None

        return all_results, fully_reconciled_aml_ids

    @api.model
    def _prepare_reconciliation_plan(
        self, plan, amls_values_map, shadowed_aml_values=None
    ):
        """Virtually reconcile 'plan', returning every partial's computed values.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._prepare_reconciliation_plan``.
        """
        all_fully_reconciled_aml_ids = set()
        all_results = []

        def process_amls(amls):
            remaining_amls = amls.filtered(
                lambda aml: aml.id not in all_fully_reconciled_aml_ids
            )
            if len(remaining_amls.mapped("partner_id")) > 1:
                remaining_amls = remaining_amls.sorted(
                    lambda aml: (aml.partner_id and aml.partner_id.id) or False
                )
            amls_results, fully_reconciled_aml_ids = self._prepare_reconciliation_amls(
                [amls_values_map[aml] for aml in remaining_amls],
                shadowed_aml_values=shadowed_aml_values,
            )
            all_fully_reconciled_aml_ids.update(fully_reconciled_aml_ids)
            all_results.extend(amls_results)

        def process_leaf(plan_node):
            for child_node in plan_node.get("nodes", []):
                process_leaf(child_node)
            process_amls(plan_node["amls"])

        process_leaf(plan)
        return all_results

    def _check_amls_exigibility_for_reconciliation(self, shadowed_aml_values=None):
        """Ensure 'self' is eligible to be reconciled together.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._check_amls_exigibility_for_reconciliation``,
        with two deliberate departures per this issue's Keputusan Desain
        ("Rekonsiliasi hanya boleh terjadi pada akun ber-reconcile
        bernilai True, dalam company yang sama, dan hanya untuk item
        pada entry ber-state posted"):

        - upstream exempts 'asset_cash'/'liability_credit_card' accounts
          from the 'reconcile' check (its only real use is bank-
          statement reconciliation, out of this issue's scope -- see the
          class docstring); that exemption is dropped, so
          'account_id.reconcile' must be true with no exception.
        - upstream only rejects a *cancelled* entry
          ('parent_state == "cancel"'), allowing a draft one through;
          this issue additionally rejects anything short of 'posted'.
        """
        not_reconciled_partial_matching_numbers = set(
            self.filtered(
                lambda aml: (
                    not aml.reconciled
                    and aml.matching_number
                    and aml.matching_number.startswith("P")
                )
            ).mapped("matching_number")
        )
        self = self.filtered(
            lambda aml: (
                not aml.reconciled
                or aml.matching_number not in not_reconciled_partial_matching_numbers
            )
        )
        if not self:
            return

        if any(aml.reconciled for aml in self):
            raise UserError(
                self.env._(
                    """
Context: Reconcile journal items
Problem: Some of the selected journal items are already reconciled
Solution: Unreconcile them first, or remove them from the selection
"""
                )
            )
        if any(aml.parent_state != "posted" for aml in self):
            raise UserError(
                self.env._(
                    """
Context: Reconcile journal items
Problem: Some of the selected journal items belong to an entry that is
    not posted
Solution: Post the journal entry first, then reconcile
"""
                )
            )
        accounts = self.mapped(
            lambda x: x._get_reconciliation_aml_field_value(
                "account_id", shadowed_aml_values
            )
        )
        if len(accounts) > 1:
            raise UserError(
                self.env._(
                    """
Context: Reconcile journal items
Problem: The selected journal items are not on the same account:
    %(accounts)s
Solution: Select journal items on a single account
""",
                    accounts=", ".join(accounts.mapped("display_name")),
                )
            )
        if len(self.company_id.root_id) > 1:
            raise UserError(
                self.env._(
                    """
Context: Reconcile journal items
Problem: The selected journal items don't belong to the same company:
    %(companies)s
Solution: Select journal items within a single company
""",
                    companies=", ".join(self.company_id.mapped("display_name")),
                )
            )
        if not accounts.reconcile:
            raise UserError(
                self.env._(
                    """
Context: Reconcile journal items
Problem: Account %(account)s does not allow reconciliation
Solution: Enable "Allow Reconciliation" on the account first
""",
                    account=accounts.display_name,
                )
            )

    @api.model
    def _optimize_reconciliation_plan(
        self, reconciliation_plan, shadowed_aml_values=None
    ):
        """Turn 'reconciliation_plan' into an execution tree, split by currency.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._optimize_reconciliation_plan``, dropping the
        ``reduced_line_sorting`` context toggle -- an ordering micro-
        optimisation for very large batches (upstream's own comment);
        always uses the fuller of its two sort keys.
        """

        def process_amls(amls):
            sorted_amls = amls.sorted(
                key=lambda aml: (
                    aml._get_reconciliation_aml_field_value(
                        "date_maturity", shadowed_aml_values
                    )
                    or aml._get_reconciliation_aml_field_value(
                        "date", shadowed_aml_values
                    ),
                    aml._get_reconciliation_aml_field_value(
                        "currency_id", shadowed_aml_values
                    ),
                    aml._get_reconciliation_aml_field_value(
                        "amount_currency", shadowed_aml_values
                    ),
                    aml._get_reconciliation_aml_field_value(
                        "balance", shadowed_aml_values
                    ),
                )
            )
            currencies = sorted_amls.mapped(
                lambda x: x._get_reconciliation_aml_field_value(
                    "currency_id", shadowed_aml_values
                )
            )
            results = {"amls": sorted_amls, "aml_ids": set(sorted_amls.ids)}
            if len(currencies) != 1:
                nodes = results["nodes"] = []
                for currency in currencies:
                    amls_in_currency = sorted_amls.filtered(
                        lambda x, currency=currency: (
                            x._get_reconciliation_aml_field_value(
                                "currency_id", shadowed_aml_values
                            )
                            == currency
                        )
                    )
                    nodes.append(
                        {
                            "amls": amls_in_currency,
                            "aml_ids": set(amls_in_currency.ids),
                        }
                    )
            return results

        def process_children(children):
            node = {"nodes": [], "aml_ids": set()}
            for child in children:
                results = process_leaf(child)
                if results:
                    node["nodes"].append(results)
                    node["aml_ids"].update(results["aml_ids"])
            node["amls"] = self.browse(node["aml_ids"])
            return node

        def process_leaf(item):
            if not item:
                return None
            if isinstance(item, models.BaseModel):
                return process_amls(item)
            return process_children(item)

        plan_list = []
        all_aml_ids = set()
        for item in reconciliation_plan:
            plan_node = process_leaf(item)
            if not plan_node or not plan_node.get("amls"):
                continue
            amls = plan_node["amls"]
            amls._check_amls_exigibility_for_reconciliation(
                shadowed_aml_values=shadowed_aml_values
            )
            plan_list.append(plan_node)
            all_aml_ids.update(plan_node["aml_ids"])

        return plan_list, self.browse(all_aml_ids)

    @api.model
    def _reconcile_plan(self, reconciliation_plan):
        """Reconcile 'reconciliation_plan' (see upstream for its shape).

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._reconcile_plan``, adapted to this repo's own
        ``journal_entry._check_balanced`` signature: unlike the upstream
        version this issue's Keputusan Desain was written against (a
        contextmanager wrapping the whole call), the copy already merged
        into this repo by the journal entry unit is a plain validating
        method, called once *after* ``_sync_dynamic_lines`` closes --
        exactly the pattern ``journal_entry.create()``/``write()``
        already use. Mirrored here rather than reintroducing a second,
        diverging ``_check_balanced``.
        """
        plan_list, all_amls = self._optimize_reconciliation_plan(reconciliation_plan)
        move_container = {"records": all_amls.move_id}
        with all_amls.move_id._sync_dynamic_lines(move_container):
            self._reconcile_plan_with_sync(plan_list, all_amls)
        all_amls.move_id._check_balanced(move_container)

    def _reconcile_plan_create_full_reconciles(
        self, plan_list, aml_values_map, all_amls
    ):
        """Group every fully-matched batch of lines into a 'reconcile_full'.

        Split out of '_reconcile_plan_with_sync' purely to keep it under
        this repo's mccabe complexity budget -- a structural, not
        behavioural, deviation from a literal port (see 'item-format.md'
        §0/§6). Ported (behaviour-wise) from the "Prepare full reconcile
        creation" section of upstream
        ``AccountMoveLine._reconcile_plan_with_sync``, trimmed of its
        'has_multiple_currencies' branch on residual-zero checks: a
        graph spanning more than one non-company currency is still out
        of this issue's scope (see the class docstring) -- an exchange
        move's own lines are stamped with the *same* currency as the
        line they fix (see '_prepare_exchange_difference_move_vals'), so
        even a cross-currency reconciliation graph never grows past one
        non-company currency here. Upstream's own plain
        'amount_residual_currency' check (its 'else' branch) is kept as
        the sole rule.
        """

        def is_line_reconciled(aml):
            if aml.reconciled:
                return True
            if not aml.matched_debit_ids and not aml.matched_credit_ids:
                return False
            return aml.currency_id.is_zero(aml.amount_residual_currency)

        full_batches = []
        all_aml_ids = set()
        number2lines = all_amls._reconciled_by_number()
        for plan in plan_list:
            for aml in plan["amls"]:
                if "full_batch_index" in aml_values_map[aml]:
                    continue
                involved_amls = plan["amls"]._filter_reconciled_by_number(number2lines)
                all_aml_ids.update(involved_amls.ids)
                full_batch_index = len(full_batches)
                is_fully_reconciled = all(
                    is_line_reconciled(involved_aml) for involved_aml in involved_amls
                )
                full_batches.append(
                    {"amls": involved_amls, "is_fully_reconciled": is_fully_reconciled}
                )
                for involved_aml in involved_amls:
                    if aml_values_map.get(involved_aml):
                        aml_values_map[involved_aml]["full_batch_index"] = (
                            full_batch_index
                        )

        # Upstream re-prefetches 'move_id'/'matched_debit_ids'/
        # 'matched_credit_ids' here as bare attribute-access statements
        # (a cache-warmup micro-optimisation); dropped -- this repo's
        # '.pylintrc'/'.ruff.toml' both enable 'pointless-statement'/B018,
        # so a bare expression statement fails CI regardless of intent.
        all_amls = self.browse(list(all_aml_ids))

        full_reconcile_values_list = []
        for full_batch in full_batches:
            if not full_batch["is_fully_reconciled"]:
                continue
            amls = full_batch["amls"]
            if amls and all(aml.full_reconcile_id for aml in amls):
                # Already grouped into a 'reconcile_full' by a *nested*
                # '_reconcile_plan' call that finished earlier in this
                # same call stack -- '_create_exchange_difference_moves'
                # (called by '_reconcile_plan_with_sync' below, before
                # this method runs) explicitly reconciles an exchange
                # move's line against the original one it fixes, and
                # that nested reconciliation's own full-reconcile step
                # already sees the *whole* graph (original debit/credit
                # lines and the exchange line all share one
                # 'matching_number' the moment the exchange partial is
                # created -- see 'reconcile_partial._update_matching_number').
                # Skip here, or this same, now-fully-matched graph would
                # get a second, duplicate 'reconcile_full'.
                continue
            involved_partials = amls.matched_debit_ids + amls.matched_credit_ids
            full_reconcile_values_list.append(
                {
                    "partial_reconcile_ids": [
                        Command.link(partial.id) for partial in involved_partials
                    ],
                    "reconciled_line_ids": [Command.link(aml.id) for aml in amls],
                }
            )

        self.env["reconcile_full"].create(full_reconcile_values_list)

    def _reconcile_plan_with_sync(self, plan_list, all_amls):
        """Create the 'reconcile_partial'/'reconcile_full' rows for 'plan_list'.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._reconcile_plan_with_sync``, trimmed of
        everything this issue's Keputusan Desain drops: the invoice-paid
        pre/post hooks (``_reconcile_pre_hook``/``_reconcile_post_hook``)
        and the tax-cash-basis stage (no ``tax_exigibility``/cash-basis
        concept exists anywhere in this repo). Also drops upstream's
        bare-attribute-access cache-warmup prefetch of 'move_id'/
        'matched_debit_ids'/'matched_credit_ids' -- see
        '_reconcile_plan_create_full_reconciles' for why.

        **The exchange-difference-move creation stage is restored here**
        (a prior unit, #12, dropped it -- see
        '_prepare_reconciliation_single_partial'\'s own docstring): every
        'exchange_values' a partial's computation produced is collected
        and handed to '_create_exchange_difference_moves' once every
        partial of this call has been created, then each exchange move
        is linked back onto the partial that produced it via
        'exchange_move_id' -- by *index*, not upstream's own
        'reconciled_lines_ids'-based heuristic (see that field's
        docstring on 'journal_entry.item': this repo dropped its
        inverse, and unlike upstream's batched heuristic this call
        already knows the exact 1:1 order between
        'exchange_diff_values_list' and the partial each entry came
        from, since '_create_exchange_difference_moves' both creates and
        reconciles those moves in that same order).
        """
        aml_values_map = {
            aml: {
                "aml": aml,
                "amount_residual": aml.amount_residual,
                "amount_residual_currency": aml.amount_residual_currency,
            }
            for aml in all_amls
        }

        partials_values_list = []
        exchange_diff_values_list = []
        exchange_diff_partial_indexes = []
        all_plan_results = []
        for plan in plan_list:
            plan_results = self._prepare_reconciliation_plan(plan, aml_values_map)
            all_plan_results.append(plan_results)
            for results in plan_results:
                partials_values_list.append(results["partial_values"])
                exchange_values = results.get("exchange_values")
                if exchange_values and exchange_values["move_values"]["line_ids"]:
                    exchange_diff_values_list.append(exchange_values)
                    exchange_diff_partial_indexes.append(len(partials_values_list) - 1)

        partials = self.env["reconcile_partial"].create(partials_values_list)
        start_range = 0
        for plan_results, plan in zip(all_plan_results, plan_list, strict=False):
            size = len(plan_results)
            plan["partials"] = partials[start_range : start_range + size]
            start_range += size

        exchange_moves = self._create_exchange_difference_moves(
            exchange_diff_values_list
        )
        for partial_index, exchange_move in zip(
            exchange_diff_partial_indexes, exchange_moves, strict=False
        ):
            partials[partial_index].exchange_move_id = exchange_move.id

        self._reconcile_plan_create_full_reconciles(plan_list, aml_values_map, all_amls)

    def _get_exchange_journal(self, company):
        """The journal an exchange difference entry for 'company' posts to.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._get_exchange_journal``. Always a 'general'
        journal -- enforced by 'currency_exchange_journal_id''s own
        domain, see 'res_company.py'.
        """
        return company.currency_exchange_journal_id

    def _get_exchange_account(self, company, amount):
        """The gain/loss account an exchange difference amount posts to.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._get_exchange_account``: a positive 'amount'
        (this line needs *more* debited to close its residual) is a
        loss, a negative one a gain.
        """
        if amount > 0.0:
            return company.expense_currency_exchange_account_id
        return company.income_currency_exchange_account_id

    def _prepare_exchange_difference_move_vals(
        self, amounts_list, company=None, exchange_date=None, **kwargs
    ):
        """Build create() vals for the exchange difference entry fixing 'self'.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._prepare_exchange_difference_move_vals``, with
        two adaptations forced by this repo's own trimmed models (per
        this issue's Keputusan Desain):

        - **Company determination is simplified.** Upstream filters
          'self.move_id' on 'is_invoice(True)' first -- that method does
          not resolve on 'journal_entry' any more (see its class
          docstring: the shims were removed once the tax synchronisation
          unit landed), so company is read straight off
          'move_id.company_id'.
        - **The exchange journal's accounting-date lookup is dropped**
          along with the field itself: 'account.journal.accounting_date'
          does not exist here (see 'journal.py''s class docstring, which
          lists it among upstream fields this repo has no sale/purchase/
          bank/cash journal type to justify). Lock-date postponement
          already happens uniformly at posting time, via
          'journal_entry._post()' -- see '_create_exchange_difference_moves'
          -- so 'exchange_date' (or, failing that, today) is used as
          this entry's initial date as-is.

        :param amounts_list: one dict per line of 'self', each either
            '{"amount_residual": ...}' or '{"amount_residual_currency": ...}'
            -- see the two '_prepare_reconciliation_single_partial_exchange_values_*'
            callers.
        :param company: fallback company, used only if 'self' is empty.
        :param exchange_date: date to stamp the entry with.
        :return: a dict with keys 'move_values' (create() vals),
            'to_reconcile' (a list of '(line, sequence)' tuples) and
            'amount_currency_fixups' (a list of '(sequence, amount_currency)'
            tuples -- see '_create_exchange_difference_moves').
        """
        company = (self.move_id.company_id or company)[:1]
        if not company:
            return None

        journal = self._get_exchange_journal(company)
        move_vals = {
            "date": exchange_date or fields.Date.context_today(self),
            "journal_id": journal.id,
            "line_ids": [],
        }
        to_reconcile = []
        amount_currency_fixups = []
        for line, amounts in zip(self, amounts_list, strict=False):
            move_vals["date"] = max(move_vals["date"], line.date)

            if "amount_residual" in amounts:
                amount_residual = amounts["amount_residual"]
                amount_residual_currency = 0.0
                if line.currency_id == line.company_currency_id:
                    amount_residual_currency = amount_residual
                amount_residual_to_fix = amount_residual
                if line.company_currency_id.is_zero(amount_residual):
                    continue
            elif "amount_residual_currency" in amounts:
                amount_residual = 0.0
                amount_residual_currency = amounts["amount_residual_currency"]
                amount_residual_to_fix = amount_residual_currency
                if line.currency_id.is_zero(amount_residual_currency):
                    continue
            else:
                continue

            exchange_line_account = self._get_exchange_account(
                company, amount_residual_to_fix
            )
            sequence = len(move_vals["line_ids"])
            # 'amount_currency' is deliberately left out of 'line_vals'
            # below and fixed up via raw SQL afterwards instead (see
            # '_create_exchange_difference_moves') -- giving both
            # 'balance'/'debit'/'credit' *and* 'amount_currency' on the
            # same create() vals does not reliably keep the given
            # 'balance': 'amount_currency''s own inverse
            # ('_inverse_amount_currency') unconditionally re-derives
            # 'balance' from 'amount_currency'/'currency_rate' whenever
            # 'amount_currency' is present in vals, silently clobbering
            # whatever 'balance' was also given -- exactly the ordering
            # pitfall the class docstring warns about for 'debit'/
            # 'credit', just one level removed. Since this exchange fix
            # is deliberately *not* rate-consistent by construction (an
            # 'amount_residual' fix always wants 'amount_currency == 0'
            # regardless of the line's real currency rate), there is no
            # vals combination that survives the inverse; only a
            # straight post-create UPDATE does.
            line_vals = [
                {
                    "name": self.env._("Currency exchange rate difference"),
                    "balance": -amount_residual,
                    "full_reconcile_id": line.full_reconcile_id.id,
                    "account_id": line.account_id.id,
                    "currency_id": line.currency_id.id,
                    "partner_id": line.partner_id.id,
                    "sequence": sequence,
                },
                {
                    "name": self.env._("Currency exchange rate difference"),
                    "balance": amount_residual,
                    "account_id": exchange_line_account.id,
                    "currency_id": line.currency_id.id,
                    "partner_id": line.partner_id.id,
                    "sequence": sequence + 1,
                },
            ]
            move_vals["line_ids"] += [Command.create(vals) for vals in line_vals]
            to_reconcile.append((line, sequence))
            amount_currency_fixups.append((sequence, -amount_residual_currency))
            amount_currency_fixups.append((sequence + 1, amount_residual_currency))

        return {
            "move_values": move_vals,
            "to_reconcile": to_reconcile,
            "amount_currency_fixups": amount_currency_fixups,
        }

    @api.model
    def _fixup_exchange_move_amount_currency(
        self, exchange_moves, exchange_diff_values_list
    ):
        """Force 'amount_currency' to the exact value each exchange line needs.

        Bypasses the ORM entirely (raw SQL, then cache invalidation) --
        see '_prepare_exchange_difference_move_vals''s own comment on why
        no combination of create() vals survives 'amount_currency''s
        inverse for a deliberately rate-inconsistent value.

        :param exchange_moves: freshly created 'journal_entry' records,
            in the same order as 'exchange_diff_values_list'.
        :param exchange_diff_values_list: list of
            '_prepare_exchange_difference_move_vals'\' return values.
        """
        fixup_rows = []
        for exchange_move, exchange_diff_values in zip(
            exchange_moves, exchange_diff_values_list, strict=False
        ):
            lines_by_sequence = {line.sequence: line for line in exchange_move.line_ids}
            for sequence, amount_currency in exchange_diff_values[
                "amount_currency_fixups"
            ]:
                line = lines_by_sequence.get(sequence)
                if line:
                    fixup_rows.append((amount_currency, line.id))
        if not fixup_rows:
            return
        self.env.cr.execute_values(
            """
            UPDATE journal_entry_item line
               SET amount_currency = source.amount_currency
              FROM (VALUES %s) AS source(amount_currency, id)
             WHERE line.id = source.id
            """,
            fixup_rows,
            page_size=1000,
        )
        exchange_moves.line_ids.invalidate_recordset(
            [
                "amount_currency",
                "price_subtotal",
                "price_total",
                "amount_residual",
                "amount_residual_currency",
                "reconciled",
            ]
        )

    @api.model
    def _create_exchange_difference_moves(self, exchange_diff_values_list):
        """Create, post and reconcile every exchange difference entry queued up.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._create_exchange_difference_moves``, with one
        structural adaptation forced by this repo's own trimmed model:
        upstream reconciles each entry's line against the original one
        it fixes by setting 'reconciled_lines_ids' in 'move_vals' and
        relying on that field's inverse -- 'journal_entry.item' dropped
        that inverse (see 'reconciled_lines_ids''s own field docstring:
        this repo's UI wave drives reconciliation through
        'action_reconcile' instead), so this method reconciles each
        '(line, sequence)' pair from 'to_reconcile' explicitly instead,
        via a plain 'reconcile()' call guarded with
        'no_exchange_difference=True' so it does not recurse into
        generating a further exchange difference of its own.

        **This is also where this issue's "posting cascade" resolves**
        (see 'journal_entry.py''s class docstring, which used to flag it
        as stubbed): every newly created exchange move whose originating
        lines were both already posted ('to_post', see
        '_prepare_reconciliation_single_partial_exchange_values') is
        posted here, immediately, before being reconciled.

        :param exchange_diff_values_list: list of
            '_prepare_exchange_difference_move_vals'\' return values.
        :return: the created 'journal_entry' records, in the same order
            as 'exchange_diff_values_list'.
        """
        if not exchange_diff_values_list:
            return self.env["journal_entry"]

        exchange_move_values_list = []
        journals = self.env["account.journal"]
        for exchange_diff_values in exchange_diff_values_list:
            move_vals = exchange_diff_values["move_values"]
            exchange_move_values_list.append(move_vals)
            if not move_vals["journal_id"]:
                raise UserError(
                    self.env._(
                        """
Context: Reconcile journal items in different currencies
Problem: No exchange difference journal is configured for this company
Solution: Set the "Exchange Difference Journal" in the company's \
Currency Exchange settings, then reconcile again
"""
                    )
                )
            journals |= self.env["account.journal"].browse(move_vals["journal_id"])

        for journal in journals:
            company = journal.company_id
            if not company.expense_currency_exchange_account_id:
                raise UserError(
                    self.env._(
                        """
Context: Reconcile journal items in different currencies
Problem: No loss exchange rate account is configured for this company
Solution: Set the "Loss Exchange Rate Account" in the company's \
Currency Exchange settings, then reconcile again
"""
                    )
                )
            if not company.income_currency_exchange_account_id:
                raise UserError(
                    self.env._(
                        """
Context: Reconcile journal items in different currencies
Problem: No gain exchange rate account is configured for this company
Solution: Set the "Gain Exchange Rate Account" in the company's \
Currency Exchange settings, then reconcile again
"""
                    )
                )

        exchange_moves = (
            self.env["journal_entry"]
            .with_context(no_exchange_difference=True)
            .create(exchange_move_values_list)
        )
        self._fixup_exchange_move_amount_currency(
            exchange_moves, exchange_diff_values_list
        )

        to_post = self.env["journal_entry"]
        for exchange_move, exchange_diff_values in zip(
            exchange_moves, exchange_diff_values_list, strict=False
        ):
            if exchange_diff_values["to_post"]:
                to_post |= exchange_move
        if to_post:
            to_post._post()

        for exchange_move, exchange_diff_values in zip(
            exchange_moves, exchange_diff_values_list, strict=False
        ):
            if exchange_move.state != "posted":
                continue
            for line, sequence in exchange_diff_values["to_reconcile"]:
                exchange_line = exchange_move.line_ids.filtered(
                    lambda item, sequence=sequence: item.sequence == sequence
                )
                (line + exchange_line).with_context(
                    no_exchange_difference=True
                ).reconcile()

        return exchange_moves

    def reconcile(self):
        """Reconcile 'self' (every line in it) all together."""
        return self._reconcile_plan([self])

    def remove_move_reconcile(self):
        """Undo a reconciliation: drop every partial matching a line in 'self'."""
        (self.matched_debit_ids + self.matched_credit_ids).unlink()

    def _reconcile_marked(self):
        """Reconcile every batch of lines sharing an import-pending matching number.

        Ported (behaviour-wise) from upstream
        ``AccountMoveLine._reconcile_marked``. Not wired to any button in
        this issue (no import flow exists yet to produce an
        'I'-prefixed 'matching_number') -- ported now, per this issue's
        Keputusan Desain, so a later import unit does not have to
        re-derive it from scratch.
        """
        temp_numbers = list(
            {
                line.matching_number
                for line in self
                if line.matching_number and line.matching_number.startswith("I")
            }
        )
        if not temp_numbers:
            return
        for _matching_number, account, lines in self._read_group(
            domain=[("matching_number", "in", temp_numbers)],
            groupby=["matching_number", "account_id"],
            aggregates=["id:recordset"],
        ):
            if all(move.state == "posted" for move in lines.move_id):
                if not account.reconcile:
                    _logger.info(
                        "%s has reconciled lines, changing the config",
                        account.display_name,
                    )
                    account.reconcile = True
                lines.reconcile()

    def _reconciled_lines(self):
        """Every reconciled line in 'self', plus their matched counterpart(s).

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._reconciled_lines``. Consumed today by
        ``journal_entry._compute_has_reconciled_entries``'s guard,
        forward-declared before this reconciliation unit existed -- see
        that method's own docstring: it starts reading real data the
        moment this method exists, no change needed there.
        """
        ids = []
        for aml in self.filtered("reconciled"):
            ids.extend(
                [r.debit_move_id.id for r in aml.matched_debit_ids]
                if aml.credit > 0
                else [r.credit_move_id.id for r in aml.matched_credit_ids]
            )
            ids.append(aml.id)
        return ids

    def _reconciled_by_number(self) -> dict:
        """Map every 'matching_number' found in 'self' to all lines sharing it.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._reconciled_by_number``.
        """
        matching_numbers = [n for n in set(self.mapped("matching_number")) if n]
        if not matching_numbers:
            return {}
        return {
            number: lines.with_env(self.env)
            for number, lines in self.sudo()._read_group(
                domain=[("matching_number", "in", matching_numbers)],
                groupby=["matching_number"],
                aggregates=["id:recordset"],
            )
        }

    def _filter_reconciled_by_number(self, mapping: dict):
        """Every line matched with a line in 'self', using a pre-built 'mapping'.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountMoveLine._filter_reconciled_by_number``.
        """
        matching_numbers = [
            n
            for n in set(self.mapped("matching_number"))
            if n and not n.startswith("I")
        ]
        return self | self.browse(
            [_id for number in matching_numbers for _id in mapping[number].ids]
        )

    def _all_reconciled_lines(self):
        """Every line matched with a line in 'self'."""
        return self._filter_reconciled_by_number(self._reconciled_by_number())

    def action_reconcile(self):
        """Reconcile the selected journal items.

        Bound to the "Reconcile" header button of the Journal Items list
        view (see the class docstring's "UI gelombang pertama" decision
        -- a multi-select list action, not an OWL widget); operates on
        whichever records the user selected, exactly like
        ``account.payment``'s own header ``action_post`` button in
        upstream Odoo.
        """
        self.sudo().reconcile()
        return True

    def action_remove_move_reconcile(self):
        """Undo reconciliation for the selected journal items.

        Bound to the "Unreconcile" header button of the Journal Items
        list view -- see 'action_reconcile'.
        """
        self.sudo().remove_move_reconcile()
        return True
