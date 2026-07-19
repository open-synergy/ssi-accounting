# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

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
