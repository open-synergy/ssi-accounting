# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import Command, api, fields, models

TYPE_TAX_USE_SELECTION = [
    ("sale", "Sales"),
    ("purchase", "Purchases"),
    ("none", "None"),
]

AMOUNT_TYPE_SELECTION = [
    ("group", "Group of Taxes"),
    ("fixed", "Fixed"),
    ("percent", "Percentage"),
    ("division", "Percentage Tax Included"),
]

PRICE_INCLUDE_SELECTION = [
    ("tax_included", "Tax Included"),
    ("tax_excluded", "Tax Excluded"),
]


class Tax(models.Model):
    """Tax configuration: rate, computation method, and repartition lines.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_tax.py`` (class ``AccountTax``, model
    ``account.tax``), because that model lives inside the ``account``
    module, which ``ssi_accounting`` must not depend on. Renamed to
    ``tax`` per this issue's Keputusan Desain.

    **Configuration only, no engine**: this model carries no
    ``compute_all``/``_prepare_base_line_*``/``_round_base_lines_*``
    logic -- computing tax amounts on a document is a separate unit, kept
    out of this issue's scope on purpose so each can be reviewed and
    tested on its own.

    **Vocabulary neutralised** (binding, see the issue's Keputusan
    Desain): ``invoice_repartition_line_ids`` ->
    ``repartition_line_base_ids``, ``refund_repartition_line_ids`` ->
    ``repartition_line_reverse_ids``, ``invoice_label`` -> ``label``. The
    ``document_type`` values themselves are renamed on
    ``tax.repartition_line`` (see that model), not here.

    ``type_tax_use`` defaults to ``none`` (upstream defaults to
    ``sale``): this repo has no sale/purchase documents to filter a tax
    selector by, so there is no natural default document flow to assume.

    **Deliberately dropped from the upstream model** (out of this issue's
    scope, see the issue's "Dibuang dari tax" list): ``fiscal_position_ids``/
    ``original_tax_ids``/``replacing_tax_ids``/``is_domestic`` (fiscal
    position, entirely out of scope), ``tax_exigibility``/
    ``cash_basis_transition_account_id`` (cash basis, out of scope),
    ``analytic`` (no analytic accounting concept in this repo yet),
    ``tax_scope`` (no goods/services product distinction here),
    ``invoice_legal_notes`` (no invoice document), ``repartition_lines_str``
    (upstream's chatter-tracking helper for repartition line changes --
    ``mail.thread`` tracking on the plain fields is enough for this
    issue's scope).
    """

    _name = "tax"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Tax"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(
        string="Tax Name",
        required=True,
        translate=True,
        tracking=True,
        help="Label of the tax, shown wherever a tax can be selected.",
    )
    type_tax_use = fields.Selection(
        selection=TYPE_TAX_USE_SELECTION,
        string="Tax Type",
        required=True,
        default="none",
        help="Where this tax is selectable. 'None' means the tax cannot "
        "be used by itself, but can still be used inside a group.",
    )
    amount_type = fields.Selection(
        selection=AMOUNT_TYPE_SELECTION,
        string="Tax Computation",
        required=True,
        default="percent",
        help="'Group of Taxes': this tax is a set of sub taxes. 'Fixed': "
        "the tax amount stays the same whatever the price. 'Percentage': "
        "the tax amount is a percentage of the price. 'Percentage Tax "
        "Included': the tax amount is a division of the price.",
    )
    amount = fields.Float(
        required=True,
        digits=(16, 4),
        default=0.0,
        help="Tax rate or fixed amount, interpreted according to 'amount_type'.",
    )
    active = fields.Boolean(
        default=True,
        help="Set active to false to hide the tax without removing it.",
    )
    sequence = fields.Integer(
        required=True,
        default=1,
        help="Used to order taxes when several are applied together.",
    )
    description = fields.Html(
        translate=True,
        help="Free-form description of this tax, e.g. printed on documents.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
        help="Company this tax belongs to.",
    )
    children_tax_ids = fields.Many2many(
        comodel_name="tax",
        relation="tax_filiation_rel",
        column1="parent_tax_id",
        column2="child_tax_id",
        string="Children Taxes",
        check_company=True,
        help="Sub taxes aggregated by this tax. Only used when "
        "'amount_type' is 'Group of Taxes'.",
    )
    tax_group_id = fields.Many2one(
        comodel_name="tax_group",
        string="Tax Group",
        required=True,
        help="Tax group this tax belongs to, mainly used to derive its "
        "payable/receivable accounts.",
    )
    price_include_override = fields.Selection(
        selection=PRICE_INCLUDE_SELECTION,
        string="Included in Price",
        help="Overrides the company's default on whether the price "
        "already includes this tax. Leave empty to follow the company "
        "default.",
    )
    price_include = fields.Boolean(
        compute="_compute_price_include",
        store=True,
        help="Whether the price used wherever this tax applies already "
        "includes this tax.",
    )
    company_price_include = fields.Selection(
        related="company_id.account_price_include",
        help="Technical field: the company's default on whether prices include taxes.",
    )
    include_base_amount = fields.Boolean(
        string="Affect Base of Subsequent Taxes",
        default=False,
        help="If set, taxes with a higher sequence than this one will be "
        "affected by it, provided they accept it.",
    )
    is_base_affected = fields.Boolean(
        string="Base Affected by Previous Taxes",
        default=True,
        help="If set, taxes with a lower sequence might affect this "
        "one's base, provided they try to do it.",
    )
    repartition_line_base_ids = fields.One2many(
        string="Distribution for Base",
        comodel_name="tax.repartition_line",
        inverse_name="tax_id",
        compute="_compute_repartition_line_base_ids",
        store=True,
        readonly=False,
        domain=[("document_type", "=", "base")],
        help="How this tax's amount is split across accounts when the "
        "tax is used normally.",
    )
    repartition_line_reverse_ids = fields.One2many(
        string="Distribution for Reverse",
        comodel_name="tax.repartition_line",
        inverse_name="tax_id",
        compute="_compute_repartition_line_reverse_ids",
        store=True,
        readonly=False,
        domain=[("document_type", "=", "reverse")],
        help="How this tax's amount is split across accounts when the "
        "journal entry it was used on is reversed.",
    )
    repartition_line_ids = fields.One2many(
        string="Distribution",
        comodel_name="tax.repartition_line",
        inverse_name="tax_id",
        help="Technical field: every repartition line of this tax, base "
        "and reverse combined.",
    )
    country_id = fields.Many2one(
        comodel_name="res.country",
        default=lambda self: self.env.company.country_id,
        help="Country this tax is applicable for.",
    )
    label = fields.Char(
        translate=True,
        help="Short label shown for this tax on documents, e.g. 'VAT "
        "11%'. Defaults to the tax name when left empty.",
    )
    has_negative_factor = fields.Boolean(
        compute="_compute_has_negative_factor",
        help="Technical field: whether at least one 'Tax' repartition "
        "line of the base distribution has a negative factor.",
    )
    is_used = fields.Boolean(
        string="Tax used",
        compute="_compute_is_used",
        help="Whether this tax is already used on at least one journal "
        "item. Always false while journal items do not exist yet.",
    )

    @api.depends(
        "repartition_line_base_ids.factor_percent",
        "repartition_line_base_ids.repartition_type",
    )
    def _compute_has_negative_factor(self):
        for tax in self:
            tax_reps = tax.repartition_line_base_ids.filtered(
                lambda line: line.repartition_type == "tax"
            )
            tax.has_negative_factor = bool(
                tax_reps.filtered(lambda line: line.factor < 0.0)
            )

    def _compute_is_used(self):
        """Whether this tax is already used on a journal item.

        Guarded on ``account.move.line``, which does not exist yet in
        this repo -- always ``False`` until that model lands, exactly
        like the other forward references guarded in ``account.py``/
        ``journal.py``.
        """
        if "account.move.line" not in self.env:
            self.is_used = False
            return
        for tax in self:
            tax.is_used = bool(
                self.env["account.move.line"].search_count(
                    [("tax_ids", "in", tax.ids)], limit=1
                )
            )

    @api.depends("price_include_override", "company_price_include")
    def _compute_price_include(self):
        for tax in self:
            tax.price_include = tax.price_include_override == "tax_included" or (
                tax.company_price_include == "tax_included"
                and not tax.price_include_override
            )

    @api.depends("company_id")
    def _compute_repartition_line_base_ids(self):
        for tax in self:
            if not tax.repartition_line_base_ids:
                tax.repartition_line_base_ids = [
                    Command.create(
                        {"document_type": "base", "repartition_type": "base"}
                    ),
                    Command.create(
                        {"document_type": "base", "repartition_type": "tax"}
                    ),
                ]

    @api.depends("company_id")
    def _compute_repartition_line_reverse_ids(self):
        for tax in self:
            if not tax.repartition_line_reverse_ids:
                tax.repartition_line_reverse_ids = [
                    Command.create(
                        {"document_type": "reverse", "repartition_type": "base"}
                    ),
                    Command.create(
                        {"document_type": "reverse", "repartition_type": "tax"}
                    ),
                ]

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for tax, vals in zip(self, vals_list, strict=True):
            if "name" not in default:
                vals["name"] = self.env._("%(name)s (copy)", name=tax.name)
        return vals_list
