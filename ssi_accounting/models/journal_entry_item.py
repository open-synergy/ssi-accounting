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
        "by the tax engine to tell base lines from tax lines.",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        required=True,
        index=True,
        check_company=True,
        help="Account this line posts to.",
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
        default=0.0,
        help="Amount posted to the debit side of 'account_id', in the "
        "company currency.",
    )
    credit = fields.Monetary(
        currency_field="company_currency_id",
        default=0.0,
        help="Amount posted to the credit side of 'account_id', in the "
        "company currency.",
    )
    balance = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_balance",
        inverse="_inverse_balance",
        store=True,
        readonly=False,
        help="Signed amount in the company currency: 'debit' minus "
        "'credit'. Editing this directly fills 'debit'/'credit' back in.",
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
        "'balance' (and therefore 'debit'/'credit') using 'currency_rate'.",
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
        index="btree_not_null",
        help="Technical field: which tax this line represents, when "
        "'display_type' is 'tax'.",
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

    @api.depends("debit", "credit")
    def _compute_balance(self):
        for line in self:
            line.balance = line.debit - line.credit

    def _inverse_balance(self):
        for line in self:
            balance = line.balance
            line.debit = balance if balance > 0.0 else 0.0
            line.credit = -balance if balance < 0.0 else 0.0

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
