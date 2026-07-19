# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.tools import frozendict
from odoo.tools.float_utils import float_is_zero, float_round

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

    **Computation engine** (added by this issue): ``compute_all`` and its
    supporting ``_prepare_base_line_*``/``_add_tax_details_*``/
    ``_round_base_lines_tax_details``/``_prepare_tax_lines`` methods are
    ported (behaviour-wise) from the same upstream file, operating on
    plain ``dict`` "base lines" so the computation can be unit tested
    without any ``journal_entry``/``journal_entry.item`` state. Wiring
    the engine to real journal entries/items is a separate unit ("unit
    sinkronisasi pajak"), kept out of this issue's scope on purpose even
    though those models now exist.

    **Simplifications the engine makes relative to upstream**, all a
    direct consequence of concepts already dropped from this model (see
    below) or genuinely out of this issue's scope:

    - No EDI ``extra_tax_data`` import/export plumbing (``computation_key``/
      ``manual_total_excluded``/``manual_tax_amounts`` are still accepted
      as plain keyword arguments to ``_prepare_base_line_for_taxes_computation``,
      just not derived from a serialised blob).
    - ``_prepare_tax_line_for_taxes_computation`` (added by the tax
      synchronisation unit, ``journal_entry``/``journal_entry.item``) is
      the counterpart of ``_prepare_base_line_for_taxes_computation`` for
      an *existing* tax-typed line, letting ``_round_base_lines_tax_details``/
      ``_prepare_tax_lines`` tell which stored tax line a freshly
      recomputed one matches (and therefore whether a manually edited
      amount on it should be preserved) instead of always recomputing
      from scratch.
    - Cash basis (``tax_exigibility``)/analytic/product tax-grid tags are
      not modelled here (see the class-level "Deliberately dropped" list
      below), so ``_add_accounting_data_to_base_line_tax_details`` always
      behaves as if every tax were "on invoice" and never mixes in
      analytic distribution or product tags. ``include_caba_tags`` is
      kept on ``_add_accounting_data_in_base_lines_tax_details`` only for
      signature parity with upstream -- it has no effect.
    - ``compute_all``'s returned ``taxes`` dicts drop the ``analytic``,
      ``tax_exigibility`` and ``use_in_tax_closing`` keys: none of the
      three fields they read exist on ``tax``/``tax.repartition_line`` in
      this repo (see the "Deliberately dropped" lists on both models).

    The two named batch-processing passes inside the tax evaluation
    (``_ascending_process_fixed_taxes_batch``/
    ``_descending_process_price_included_taxes_batch``, plus a third,
    symmetrically named ``_ascending_process_price_excluded_taxes_batch``)
    are extracted here as their own methods; upstream inlines them as
    nested closures inside ``_get_tax_details``. The names/ordering match
    upstream's own docstrings for ``_eval_tax_amount_fixed_amount``
    ("first ascending order for fixed taxes") and
    ``_eval_tax_amount_price_included`` ("descending order for
    price-included taxes").

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

        Reads ``journal_entry.item.tax_ids`` (originally written against
        the placeholder name ``account.move.line`` before the journal
        entry unit settled on ``journal_entry.item``), which now exists,
        so this works for real -- guard kept for install-order safety.
        """
        if "journal_entry.item" not in self.env:
            self.is_used = False
            return
        for tax in self:
            tax.is_used = bool(
                self.env["journal_entry.item"].search_count(
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

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: CONTEXT HELPERS
    # -------------------------------------------------------------------
    # Ported (behaviour-wise) from ``addons/account/models/account_tax.py``.
    # No custom tax formula ("code") amount type exists on this model, so
    # unlike upstream these "product"/"product uom" context builders never
    # have extra fields to preload -- they stay trivial pass-throughs, kept
    # only so ``_get_tax_details``'s ``evaluation_context`` shape matches
    # upstream and can grow custom fields later without changing callers.

    def _eval_taxes_computation_prepare_product_fields(self):
        """Fields to extract from a product for the taxes computation.

        :return: a set of ``product.product`` field names.
        """
        return set()

    @api.model
    def _eval_taxes_computation_prepare_product_default_values(self, field_names):
        """Default values to use when no product is given.

        :param field_names: field names returned by
            ``_eval_taxes_computation_prepare_product_fields``.
        :return: a mapping ``field_name`` -> ``{'type', 'default_value'}``.
        """
        default_value_map = {"integer": 0, "float": 0.0, "monetary": 0.0}
        product_fields_values = {}
        for field_name in field_names:
            field = self.env["product.product"]._fields[field_name]
            product_fields_values[field_name] = {
                "type": field.type,
                "default_value": default_value_map[field.type],
            }
        return product_fields_values

    @api.model
    def _eval_taxes_computation_prepare_product_values(
        self, default_product_values, product=None
    ):
        """Convert ``product`` into the values used by ``evaluation_context``."""
        product = product and product.sudo()
        product_values = {}
        for field_name, field_info in default_product_values.items():
            product_values[field_name] = (
                product and product[field_name] or field_info["default_value"]
            )
        return product_values

    def _eval_taxes_computation_turn_to_product_values(self, product=None):
        """Call the three ``product`` helpers above at once."""
        product_fields = self._eval_taxes_computation_prepare_product_fields()
        default_product_values = (
            self._eval_taxes_computation_prepare_product_default_values(product_fields)
        )
        return self._eval_taxes_computation_prepare_product_values(
            default_product_values=default_product_values,
            product=product,
        )

    def _eval_taxes_computation_prepare_product_uom_fields(self):
        """Fields to extract from a product uom for the taxes computation.

        :return: a set of ``uom.uom`` field names.
        """
        return set()

    @api.model
    def _eval_taxes_computation_prepare_product_uom_default_values(self, field_names):
        """Default values to use when no product uom is given.

        :param field_names: field names returned by
            ``_eval_taxes_computation_prepare_product_uom_fields``.
        :return: a mapping ``field_name`` -> ``{'type', 'default_value'}``.
        """
        default_value_map = {"integer": 0, "float": 0.0, "monetary": 0.0}
        product_uom_fields_values = {}
        for field_name in field_names:
            field = self.env["uom.uom"]._fields[field_name]
            product_uom_fields_values[field_name] = {
                "type": field.type,
                "default_value": default_value_map[field.type],
            }
        return product_uom_fields_values

    @api.model
    def _eval_taxes_computation_prepare_product_uom_values(
        self, default_product_uom_values, product_uom=None
    ):
        """Convert ``product_uom`` into the values used by ``evaluation_context``."""
        product_uom = product_uom and product_uom.sudo()
        product_uom_values = {}
        for field_name, field_info in default_product_uom_values.items():
            product_uom_values[field_name] = (
                product_uom and product_uom[field_name] or field_info["default_value"]
            )
        return product_uom_values

    def _eval_taxes_computation_turn_to_product_uom_values(self, product_uom=None):
        """Call the three ``product uom`` helpers above at once."""
        product_uom_fields = self._eval_taxes_computation_prepare_product_uom_fields()
        default_product_uom_values = (
            self._eval_taxes_computation_prepare_product_uom_default_values(
                product_uom_fields
            )
        )
        return self._eval_taxes_computation_prepare_product_uom_values(
            default_product_uom_values=default_product_uom_values,
            product_uom=product_uom,
        )

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: BASE LINE
    # -------------------------------------------------------------------

    @api.model
    def _get_base_line_field_value_from_record(
        self, record, field, extra_values, fallback, from_base_line=False
    ):
        """Extract a default value for a record or something looking like one.

        Suppose ``field`` is ``'product_id'`` and ``fallback`` is
        ``self.env['product.product']``: if ``record`` is a recordset, the
        returned value is ``record.product_id._origin``; if ``record`` is
        a ``dict``, it is ``record.get('product_id', fallback)``.

        :param record: a record, a dict, or a falsy value.
        :param field: the name of the field to extract.
        :param extra_values: the extra kwargs passed in addition of 'record'.
        :param fallback: the value to return if not found in record/extra_values.
        :param from_base_line: if True, never read ``field`` off ``record``
            even when ``record`` is a model instance carrying that field.
        :return: the field value corresponding to ``field``.
        """
        need_origin = isinstance(fallback, models.Model)
        if field in extra_values:
            value = extra_values[field] or fallback
        elif (
            isinstance(record, models.Model)
            and field in record._fields
            and not from_base_line
        ):
            value = record[field]
        elif isinstance(record, dict):
            value = record.get(field, fallback)
        else:
            value = fallback
        if need_origin:
            value = value._origin
        return value

    @api.model
    def _prepare_base_line_for_taxes_computation(self, record, **kwargs):
        """Convert a business object into the dict used by the tax engine.

        :param record: a representation of a business object -- a record
            or a dictionary.
        :param kwargs: extra values overriding what would be taken from
            ``record``.
        :return: a dictionary representing a base line.
        """

        def load(field, fallback):
            return self._get_base_line_field_value_from_record(
                record, field, kwargs, fallback
            )

        currency = (
            load("currency_id", None)
            or load("company_currency_id", None)
            or load("company_id", self.env["res.company"]).currency_id
            or self.env["res.currency"]
        )

        base_line = {
            **kwargs,
            "record": record,
            "id": load("id", 0),
            "product_id": load("product_id", self.env["product.product"]),
            "product_uom_id": load("product_uom_id", self.env["uom.uom"]),
            "tax_ids": load("tax_ids", self.env["tax"]),
            "price_unit": load("price_unit", 0.0),
            "quantity": load("quantity", 0.0),
            "discount": load("discount", 0.0),
            "currency_id": currency,
            "special_mode": kwargs.get("special_mode") or False,
            "special_type": kwargs.get("special_type") or False,
            "rate": load("rate", 1.0),
            "filter_tax_function": kwargs.get("filter_tax_function") or None,
            "sign": load("sign", 1.0),
            "is_refund": load("is_refund", False),
            "partner_id": load("partner_id", self.env["res.partner"]),
            "account_id": load("account_id", self.env["account.account"]),
            "analytic_distribution": load("analytic_distribution", None),
            "computation_key": kwargs.get("computation_key"),
            "manual_total_excluded_currency": kwargs.get(
                "manual_total_excluded_currency"
            ),
            "manual_total_excluded": kwargs.get("manual_total_excluded"),
            "manual_tax_amounts": kwargs.get("manual_tax_amounts"),
        }

        if record and isinstance(record, dict):
            for key, value in record.items():
                if key.startswith("_") and key not in base_line:
                    base_line[key] = value

        return base_line

    @api.model
    def _prepare_tax_line_for_taxes_computation(self, record, **kwargs):
        """Convert an *existing* tax-typed line into the dict the engine uses.

        Counterpart of ``_prepare_base_line_for_taxes_computation`` for a
        line that already carries a ``tax_repartition_line_id`` (a
        ``journal_entry.item`` with ``display_type == 'tax'``): its
        current amounts are what ``_prepare_tax_lines`` matches a
        recomputed grouping key against, to decide whether that stored
        line should be updated, deleted, or left as-is because it was
        edited manually. Ported (behaviour-wise) verbatim from upstream
        ``account.tax._prepare_tax_line_for_taxes_computation``, with a
        smaller field set (no ``analytic_distribution`` -- dropped from
        ``journal_entry.item``, see that model's own docstring).

        :param record: a representation of an existing tax line -- a
            record or a dictionary.
        :param kwargs: extra values overriding what would be taken from
            ``record``.
        :return: a dictionary representing a tax line.
        """

        def load(field, fallback):
            return self._get_base_line_field_value_from_record(
                record, field, kwargs, fallback
            )

        currency = (
            load("currency_id", None)
            or load("company_currency_id", None)
            or load("company_id", self.env["res.company"]).currency_id
            or self.env["res.currency"]
        )

        return {
            **kwargs,
            "record": record,
            "id": load("id", 0),
            "tax_repartition_line_id": load(
                "tax_repartition_line_id", self.env["tax.repartition_line"]
            ),
            "group_tax_id": load("group_tax_id", self.env["tax"]),
            "tax_ids": load("tax_ids", self.env["tax"]),
            "tax_tag_ids": load("tax_tag_ids", self.env["account.tag"]),
            "currency_id": currency,
            "partner_id": load("partner_id", self.env["res.partner"]),
            "account_id": load("account_id", self.env["account.account"]),
            "analytic_distribution": load("analytic_distribution", None),
            "sign": load("sign", 1.0),
            "amount_currency": load("amount_currency", 0.0),
            "balance": load("balance", 0.0),
        }

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: BATCHING & EVALUATION
    # -------------------------------------------------------------------

    def _flatten_taxes_and_sort_them(self):
        """Flatten the taxes contained in this recordset, sorted by sequence.

        E.g. considering letters as taxes and alphabetic order as
        sequence: ``[G, B([A, D, F]), E, C]`` becomes ``[A, D, F, C, E, G]``.

        :return: a tuple ``<sorted_taxes, group_per_tax>`` where
            ``group_per_tax`` maps each tax to its parent group of taxes,
            if any.
        """

        def sort_key(tax):
            return tax.sequence, tax.id or None

        group_per_tax = {}
        sorted_taxes = self.env["tax"]
        for tax in self.sorted(key=sort_key):
            if tax.amount_type == "group":
                children = tax.children_tax_ids.sorted(key=sort_key)
                sorted_taxes |= children
                for child in children:
                    group_per_tax[child.id] = tax
            else:
                sorted_taxes |= tax
        return sorted_taxes, group_per_tax

    def flatten_taxes_hierarchy(self):
        """Public shortcut returning only the sorted taxes recordset."""
        return self._flatten_taxes_and_sort_them()[0]

    def _batch_for_taxes_computation(
        self, special_mode=False, filter_tax_function=None
    ):
        """Group the current taxes like price-included/division taxes.

        :param special_mode: ``False``, ``'total_excluded'`` or
            ``'total_included'``.
        :param filter_tax_function: optional function filtering out taxes.
        :return: a dict with ``batch_per_tax``, ``group_per_tax`` and
            ``sorted_taxes`` (see ``_flatten_taxes_and_sort_them``).
        """
        sorted_taxes, group_per_tax = self._flatten_taxes_and_sort_them()
        if filter_tax_function:
            sorted_taxes = sorted_taxes.filtered(filter_tax_function)

        results = {
            "batch_per_tax": {},
            "group_per_tax": group_per_tax,
            "sorted_taxes": sorted_taxes,
        }

        batch = self.env["tax"]
        is_base_affected = False
        for tax in reversed(results["sorted_taxes"]):
            if batch:
                same_batch = (
                    tax.amount_type == batch[0].amount_type
                    and (special_mode or tax.price_include == batch[0].price_include)
                    and tax.include_base_amount == batch[0].include_base_amount
                    and (
                        (tax.include_base_amount and not is_base_affected)
                        or not tax.include_base_amount
                    )
                )
                if not same_batch:
                    for batch_tax in batch:
                        results["batch_per_tax"][batch_tax.id] = batch
                    batch = self.env["tax"]

            is_base_affected = tax.is_base_affected
            batch |= tax

        if batch:
            for batch_tax in batch:
                results["batch_per_tax"][batch_tax.id] = batch
        return results

    def _taxes_computation_order_before(self, tax, taxes_data):
        """Yield the taxes of ``self`` preceding ``tax``'s own batch."""
        for tax_before in self:
            if tax_before in taxes_data[tax.id]["batch"]:
                break
            yield tax_before

    def _taxes_computation_order_after(self, tax, taxes_data):
        """Yield the taxes of ``self`` following ``tax``'s own batch."""
        for tax_after in reversed(list(self)):
            if tax_after in taxes_data[tax.id]["batch"]:
                break
            yield tax_after

    def _add_extra_tax_base(self, tax, other_tax, sign, taxes_data):
        """Add ``tax``'s amount as an extra base for ``other_tax``."""
        tax_amount = taxes_data[tax.id]["tax_amount"]
        if "tax_amount" not in taxes_data[other_tax.id]:
            taxes_data[other_tax.id]["extra_base_for_tax"] += sign * tax_amount
        taxes_data[other_tax.id]["extra_base_for_base"] += sign * tax_amount

    def _propagate_extra_taxes_base_price_include(self, tax, taxes_data, special_mode):
        """``_propagate_extra_taxes_base`` for a price-included ``tax``."""
        if special_mode in (False, "total_included"):
            for other_tax in self._taxes_computation_order_after(tax, taxes_data):
                if not tax.include_base_amount or not other_tax.is_base_affected:
                    self._add_extra_tax_base(tax, other_tax, -1, taxes_data)
            for other_tax in self._taxes_computation_order_before(tax, taxes_data):
                self._add_extra_tax_base(tax, other_tax, -1, taxes_data)
        elif tax.include_base_amount:  # special_mode == 'total_excluded'
            for other_tax in self._taxes_computation_order_after(tax, taxes_data):
                if other_tax.is_base_affected:
                    self._add_extra_tax_base(tax, other_tax, 1, taxes_data)

    def _propagate_extra_taxes_base_price_exclude(self, tax, taxes_data, special_mode):
        """``_propagate_extra_taxes_base`` for a price-excluded ``tax``."""
        if special_mode in (False, "total_excluded"):
            if tax.include_base_amount:
                for other_tax in self._taxes_computation_order_after(tax, taxes_data):
                    if other_tax.is_base_affected:
                        self._add_extra_tax_base(tax, other_tax, 1, taxes_data)
        else:  # special_mode == 'total_included'
            if not tax.include_base_amount:
                for other_tax in self._taxes_computation_order_after(tax, taxes_data):
                    self._add_extra_tax_base(tax, other_tax, -1, taxes_data)
            for other_tax in self._taxes_computation_order_before(tax, taxes_data):
                self._add_extra_tax_base(tax, other_tax, -1, taxes_data)

    def _propagate_extra_taxes_base(self, tax, taxes_data, special_mode=False):
        """Mark which taxes need an ``extra_base`` from ``tax``.

        Depending on the computation order, ``special_mode`` and the
        involved taxes' configuration (price included, affects base of
        subsequent taxes, ...), some taxes affect the base/tax amount of
        others. This flags that in ``taxes_data``.

        :param tax: the tax for which to propagate.
        :param taxes_data: the computed values for taxes so far.
        :param special_mode: ``False``, ``'total_excluded'`` or
            ``'total_included'``.
        """
        if tax.price_include:
            self._propagate_extra_taxes_base_price_include(
                tax, taxes_data, special_mode
            )
        else:
            self._propagate_extra_taxes_base_price_exclude(
                tax, taxes_data, special_mode
            )

    def _eval_tax_amount_fixed_amount(self, batch, raw_base, evaluation_context):
        """Tax amount for a 'fixed' tax, first (ascending) pass.

        :return: the tax amount, or ``None`` if not a 'fixed' tax.
        """
        if self.amount_type == "fixed":
            sign = -1 if evaluation_context["price_unit"] < 0.0 else 1
            return sign * evaluation_context["quantity"] * self.amount

    def _eval_tax_amount_price_included(self, batch, raw_base, evaluation_context):
        """Tax amount for a price-included tax, descending pass.

        :return: the tax amount, or ``None`` if not 'percent'/'division'.
        """
        self.ensure_one()
        if self.amount_type == "percent":
            total_percentage = sum(tax.amount for tax in batch) / 100.0
            to_price_excluded_factor = (
                1 / (1 + total_percentage) if total_percentage != -1 else 0.0
            )
            return raw_base * to_price_excluded_factor * self.amount / 100.0

        if self.amount_type == "division":
            return raw_base * self.amount / 100.0

    def _eval_tax_amount_price_excluded(self, batch, raw_base, evaluation_context):
        """Tax amount for a price-excluded tax, second (ascending) pass.

        :return: the tax amount, or ``None`` if not 'percent'/'division'.
        """
        self.ensure_one()
        if self.amount_type == "percent":
            return raw_base * self.amount / 100.0

        if self.amount_type == "division":
            total_percentage = sum(tax.amount for tax in batch) / 100.0
            incl_base_multiplicator = (
                1.0 if total_percentage == 1.0 else 1 - total_percentage
            )
            return raw_base * self.amount / 100.0 / incl_base_multiplicator

    @api.model
    def _prepare_tax_extra_data(self, tax, special_mode, **kwargs):
        """Seed ``taxes_data[tax.id]`` before any amount is evaluated."""
        if tax.has_negative_factor:
            price_include = False
        elif special_mode == "total_included":
            price_include = True
        elif special_mode == "total_excluded":
            price_include = False
        else:
            price_include = tax.price_include
        return {
            **kwargs,
            "tax": tax,
            "price_include": price_include,
            "extra_base_for_tax": 0.0,
            "extra_base_for_base": 0.0,
        }

    @api.model
    def _add_tax_amount_to_computation_state(self, tax, tax_amount, computation_state):
        """Store an evaluated ``tax_amount`` and propagate its extra base."""
        taxes_data = computation_state["taxes_data"]
        taxes_data[tax.id]["tax_amount"] = tax_amount
        if computation_state["rounding_method"] == "round_per_line":
            taxes_data[tax.id]["tax_amount"] = float_round(
                taxes_data[tax.id]["tax_amount"],
                precision_rounding=computation_state["precision_rounding"],
            )
        if tax.has_negative_factor:
            computation_state["reverse_charge_taxes_data"][tax.id][
                "tax_amount"
            ] = -taxes_data[tax.id]["tax_amount"]
        computation_state["sorted_taxes"]._propagate_extra_taxes_base(
            tax, taxes_data, special_mode=computation_state["special_mode"]
        )

    @api.model
    def _evaluate_tax_amount(self, tax, tax_amount_function, computation_state):
        """Evaluate ``tax_amount_function`` for ``tax``, unless already done."""
        taxes_data = computation_state["taxes_data"]
        if "tax_amount" in taxes_data[tax.id]:
            return

        tax_amount = tax_amount_function(
            taxes_data[tax.id]["batch"],
            computation_state["raw_base"] + taxes_data[tax.id]["extra_base_for_tax"],
            computation_state["evaluation_context"],
        )
        if tax_amount is not None:
            self._add_tax_amount_to_computation_state(
                tax, tax_amount, computation_state
            )

    @api.model
    def _ascending_process_fixed_taxes_batch(self, sorted_taxes, computation_state):
        """First pass: evaluate every 'fixed' tax.

        Walked from the last batch to the first ("first ascending order"
        per ``_eval_tax_amount_fixed_amount``'s docstring) because a
        'fixed' tax with ``include_base_amount`` set can still push its
        amount into an earlier batch's base, and fixed amounts never
        depend on any other tax's amount, so they can all be resolved
        upfront.

        :param sorted_taxes: recordset from ``_batch_for_taxes_computation``.
        :param computation_state: the mutable state built by
            ``_get_tax_details``.
        """
        for tax in reversed(sorted_taxes):
            self._evaluate_tax_amount(
                tax, tax._eval_tax_amount_fixed_amount, computation_state
            )

    @api.model
    def _descending_process_price_included_taxes_batch(
        self, sorted_taxes, computation_state
    ):
        """Second pass: evaluate price-included 'percent'/'division' taxes.

        Walked from the last batch to the first (the "descending order"
        named by ``_eval_tax_amount_price_included``'s docstring).

        :param sorted_taxes: recordset from ``_batch_for_taxes_computation``.
        :param computation_state: the mutable state built by
            ``_get_tax_details``.
        """
        for tax in reversed(sorted_taxes):
            if computation_state["taxes_data"][tax.id]["price_include"]:
                self._evaluate_tax_amount(
                    tax, tax._eval_tax_amount_price_included, computation_state
                )

    @api.model
    def _ascending_process_price_excluded_taxes_batch(
        self, sorted_taxes, computation_state
    ):
        """Third pass: evaluate price-excluded 'percent'/'division' taxes.

        Walked from the first batch to the last (the "second ascending
        order" named by ``_eval_tax_amount_price_excluded``'s docstring).

        :param sorted_taxes: recordset from ``_batch_for_taxes_computation``.
        :param computation_state: the mutable state built by
            ``_get_tax_details``.
        """
        for tax in sorted_taxes:
            if not computation_state["taxes_data"][tax.id]["price_include"]:
                self._evaluate_tax_amount(
                    tax, tax._eval_tax_amount_price_excluded, computation_state
                )

    def _get_tax_details(
        self,
        price_unit,
        quantity,
        precision_rounding=0.01,
        rounding_method="round_per_line",
        product=None,
        product_uom=None,
        special_mode=False,
        filter_tax_function=None,
    ):
        """Compute the tax/base amounts for the current taxes (``self``).

        :param price_unit: the price unit of the line.
        :param quantity: the quantity of the line.
        :param precision_rounding: rounding precision for 'round_per_line'.
        :param rounding_method: 'round_per_line' or 'round_globally'.
        :param product: the product of the line, if any.
        :param product_uom: the product uom of the line, if any.
        :param special_mode: ``False``, ``'total_excluded'`` (the base
            excludes all price-included taxes) or ``'total_included'``
            (the base is the total with taxes).
        :param filter_tax_function: optional function filtering out taxes.
        :return: a dict with ``total_excluded``, ``total_included`` and
            ``taxes_data`` (one entry per tax, with ``tax``/``taxes``/
            ``group``/``batch``/``tax_amount``/``price_include``/
            ``base_amount``/``is_reverse_charge``).
        """
        batching_results = self._batch_for_taxes_computation(
            special_mode=special_mode, filter_tax_function=filter_tax_function
        )
        sorted_taxes = batching_results["sorted_taxes"]
        taxes_data = {}
        reverse_charge_taxes_data = {}
        for tax in sorted_taxes:
            taxes_data[tax.id] = self._prepare_tax_extra_data(
                tax,
                special_mode,
                group=batching_results["group_per_tax"].get(tax.id),
                batch=batching_results["batch_per_tax"][tax.id],
            )
            if tax.has_negative_factor:
                reverse_charge_taxes_data[tax.id] = {
                    **taxes_data[tax.id],
                    "is_reverse_charge": True,
                }

        raw_base = quantity * price_unit
        if rounding_method == "round_per_line":
            raw_base = float_round(raw_base, precision_rounding=precision_rounding)

        evaluation_context = {
            "product": sorted_taxes._eval_taxes_computation_turn_to_product_values(
                product=product
            ),
            "uom": sorted_taxes._eval_taxes_computation_turn_to_product_uom_values(
                product_uom=product_uom
            ),
            "price_unit": price_unit,
            "quantity": quantity,
            "raw_base": raw_base,
            "special_mode": special_mode,
        }

        computation_state = {
            "taxes_data": taxes_data,
            "reverse_charge_taxes_data": reverse_charge_taxes_data,
            "raw_base": raw_base,
            "rounding_method": rounding_method,
            "precision_rounding": precision_rounding,
            "special_mode": special_mode,
            "evaluation_context": evaluation_context,
            "sorted_taxes": sorted_taxes,
        }

        self._ascending_process_fixed_taxes_batch(sorted_taxes, computation_state)
        self._descending_process_price_included_taxes_batch(
            sorted_taxes, computation_state
        )
        self._ascending_process_price_excluded_taxes_batch(
            sorted_taxes, computation_state
        )

        # Mark the base to be computed in the descending order (order does
        # not matter for no special mode / 'total_excluded', but must be
        # reversed for 'total_included').
        subsequent_taxes = self.env["tax"]
        for tax in reversed(sorted_taxes):
            tax_data = taxes_data[tax.id]
            if "tax_amount" not in tax_data:
                continue

            total_tax_amount = sum(
                taxes_data[other_tax.id]["tax_amount"]
                for other_tax in tax_data["batch"]
            )
            total_tax_amount += sum(
                reverse_charge_taxes_data[other_tax.id]["tax_amount"]
                for other_tax in taxes_data[tax.id]["batch"]
                if other_tax.has_negative_factor
            )
            base = raw_base + tax_data["extra_base_for_base"]
            if tax_data["price_include"] and special_mode in (False, "total_included"):
                base -= total_tax_amount
            tax_data["base"] = base

            tax_data["taxes"] = self.env["tax"]
            if tax.include_base_amount:
                tax_data["taxes"] |= subsequent_taxes

            if tax.has_negative_factor:
                reverse_charge_tax_data = reverse_charge_taxes_data[tax.id]
                reverse_charge_tax_data["base"] = base
                reverse_charge_tax_data["taxes"] = tax_data["taxes"]

            if tax.is_base_affected:
                subsequent_taxes |= tax

        taxes_data_list = []
        for tax_data in taxes_data.values():
            if "tax_amount" in tax_data:
                taxes_data_list.append(tax_data)
                tax = tax_data["tax"]
                if tax.has_negative_factor:
                    taxes_data_list.append(reverse_charge_taxes_data[tax.id])

        if taxes_data_list:
            total_excluded = taxes_data_list[0]["base"]
            tax_amount = sum(tax_data["tax_amount"] for tax_data in taxes_data_list)
            total_included = total_excluded + tax_amount
        else:
            total_included = total_excluded = raw_base

        return {
            "total_excluded": total_excluded,
            "total_included": total_included,
            "taxes_data": [
                {
                    "tax": tax_data["tax"],
                    "taxes": tax_data["taxes"],
                    "group": (
                        batching_results["group_per_tax"].get(tax_data["tax"].id)
                        or self.env["tax"]
                    ),
                    "batch": batching_results["batch_per_tax"][tax_data["tax"].id],
                    "tax_amount": tax_data["tax_amount"],
                    "price_include": tax_data["price_include"],
                    "base_amount": tax_data["base"],
                    "is_reverse_charge": tax_data.get("is_reverse_charge", False),
                }
                for tax_data in taxes_data_list
            ],
        }

    @api.model
    def _add_tax_details_in_base_line(self, base_line, company, rounding_method=None):
        """Compute the tax details for ``base_line`` and store them.

        Amounts are rounded or not depending on the tax calculation
        method; call ``_round_base_lines_tax_details`` afterwards if
        monetary fields need to be computed from them.

        :param base_line: a base line from
            ``_prepare_base_line_for_taxes_computation``.
        :param company: the company owning the base line.
        :param rounding_method: overrides the company's rounding method.
        """
        rounding_method = rounding_method or company.tax_calculation_rounding_method
        price_unit_after_discount = base_line["price_unit"] * (
            1 - (base_line["discount"] / 100.0)
        )
        taxes_computation = base_line["tax_ids"]._get_tax_details(
            price_unit=price_unit_after_discount,
            quantity=base_line["quantity"],
            precision_rounding=base_line["currency_id"].rounding,
            rounding_method=rounding_method,
            product=base_line["product_id"],
            product_uom=base_line["product_uom_id"],
            special_mode=base_line["special_mode"],
            filter_tax_function=base_line["filter_tax_function"],
        )

        if base_line["special_type"] == "non_deductible":
            taxes_data = taxes_computation["taxes_data"]
            taxes_computation["taxes_data"] = []
            for tax_data in taxes_data:
                if not tax_data.get("is_reverse_charge"):
                    taxes_computation["taxes_data"].append(tax_data)
                else:
                    taxes_computation["total_included"] -= tax_data["tax_amount"]

        rate = base_line["rate"]
        tax_details = base_line["tax_details"] = {
            "raw_total_excluded_currency": taxes_computation["total_excluded"],
            "raw_total_excluded": (
                taxes_computation["total_excluded"] / rate if rate else 0.0
            ),
            "raw_total_included_currency": taxes_computation["total_included"],
            "raw_total_included": (
                taxes_computation["total_included"] / rate if rate else 0.0
            ),
            "taxes_data": [],
        }
        if rounding_method == "round_per_line":
            tax_details["raw_total_excluded"] = company.currency_id.round(
                tax_details["raw_total_excluded"]
            )
            tax_details["raw_total_included"] = company.currency_id.round(
                tax_details["raw_total_included"]
            )
        for tax_data in taxes_computation["taxes_data"]:
            tax_amount = tax_data["tax_amount"] / rate if rate else 0.0
            base_amount = tax_data["base_amount"] / rate if rate else 0.0
            if rounding_method == "round_per_line":
                tax_amount = company.currency_id.round(tax_amount)
                base_amount = company.currency_id.round(base_amount)
            tax_details["taxes_data"].append(
                {
                    **tax_data,
                    "raw_tax_amount_currency": tax_data["tax_amount"],
                    "raw_tax_amount": tax_amount,
                    "raw_base_amount_currency": tax_data["base_amount"],
                    "raw_base_amount": base_amount,
                }
            )

    @api.model
    def _add_tax_details_in_base_lines(self, base_lines, company):
        """Call ``_add_tax_details_in_base_line`` on multiple base lines."""
        for base_line in base_lines:
            self._add_tax_details_in_base_line(base_line, company)

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: ROUNDING
    # -------------------------------------------------------------------

    @api.model
    def _normalize_target_factors(self, target_factors):
        """Normalize the factors passed as parameter to sum to 1.

        :param target_factors: a list of dicts with at least ``factor``.
        :return: a list of ``<index, normalized_factor>`` tuples.
        """
        factors = [
            (i, abs(target_factor["factor"]))
            for i, target_factor in enumerate(target_factors)
        ]
        factors.sort(key=lambda x: x[1], reverse=True)
        sum_of_factors = sum(x[1] for x in factors)
        return [
            (i, factor / sum_of_factors if sum_of_factors else 1 / len(factors))
            for i, factor in factors
        ]

    @api.model
    def _distribute_delta_amount_smoothly(
        self, precision_digits, delta_amount, target_factors
    ):
        """Distribute ``delta_amount`` across ``target_factors``.

        :param precision_digits: decimal places of the delta.
        :param delta_amount: the delta amount to distribute.
        :param target_factors: a list of dicts with at least ``factor``.
        :return: a list of floats, one per element of ``target_factors``.
        """
        precision_rounding = float(f"1e-{precision_digits}")
        amounts_to_distribute = [0.0] * len(target_factors)
        if float_is_zero(delta_amount, precision_digits=precision_digits):
            return amounts_to_distribute

        sign = -1 if delta_amount < 0.0 else 1
        nb_of_errors = round(abs(delta_amount / precision_rounding))
        remaining_errors = nb_of_errors

        factors = self._normalize_target_factors(target_factors)
        for i, factor in factors:
            if not remaining_errors:
                break

            nb_of_amount_to_distribute = min(
                round(factor * nb_of_errors),
                remaining_errors,
            )
            remaining_errors -= nb_of_amount_to_distribute
            amount_to_distribute = (
                sign * nb_of_amount_to_distribute * precision_rounding
            )
            amounts_to_distribute[i] += amount_to_distribute

        for i in range(remaining_errors):
            amounts_to_distribute[factors[i][0]] += sign * precision_rounding

        return amounts_to_distribute

    @api.model
    def _aggregate_base_line_tax_details(self, base_line, grouping_function):
        """Aggregate the tax details of a single base line by a custom key.

        If ``base_line`` has no tax, ``grouping_function`` is called once
        with an empty ``tax_data`` to get the grouping key for the line.

        Call ``_add_tax_details_in_base_lines``/``_round_base_lines_tax_details``
        before calling this method.

        :param base_line: a base line from
            ``_prepare_base_line_for_taxes_computation``.
        :param grouping_function: a function ``<base_line, tax_data>`` ->
            a hashable grouping key (never ``None``).
        :return: a mapping ``grouping_key`` -> aggregated amounts.
        """
        values_per_grouping_key = {}
        tax_details = base_line["tax_details"]
        taxes_data = tax_details["taxes_data"]
        manual_tax_amounts = base_line["manual_tax_amounts"]

        for tax_data in taxes_data or [None]:
            current_manual_tax_amounts = (
                manual_tax_amounts
                and tax_data
                and manual_tax_amounts.get(str(tax_data["tax"].id))
                or {}
            )

            grouping_key = grouping_function(base_line, tax_data)
            if isinstance(grouping_key, dict):
                grouping_key = frozendict(grouping_key)

            if grouping_key not in values_per_grouping_key:
                values = values_per_grouping_key[grouping_key] = {
                    "grouping_key": grouping_key,
                    "taxes_data": [],
                }
                for suffix in ("_currency", ""):
                    excluded_rounded_field = f"total_excluded{suffix}"
                    excluded_delta_field = f"delta_{excluded_rounded_field}"
                    excluded_raw_field = f"raw_{excluded_rounded_field}"
                    excluded_target_field = f"target_{excluded_rounded_field}"
                    excluded_manual_field = f"manual_{excluded_rounded_field}"
                    excluded_rounded_amount = (
                        tax_details[excluded_rounded_field]
                        + tax_details[excluded_delta_field]
                    )
                    excluded_raw_amount = tax_details[excluded_raw_field]
                    values[excluded_rounded_field] = excluded_rounded_amount
                    values[excluded_raw_field] = excluded_raw_amount
                    if base_line[excluded_manual_field] is not None:
                        excluded_target_amount = base_line[excluded_manual_field]
                    elif (
                        not suffix
                        and base_line["manual_total_excluded_currency"] is not None
                    ):
                        excluded_target_amount = excluded_rounded_amount
                    else:
                        excluded_target_amount = excluded_raw_amount
                    values[excluded_target_field] = excluded_target_amount

                    tax_base_rounded_field = f"base_amount{suffix}"
                    tax_base_raw_field = f"raw_{tax_base_rounded_field}"
                    tax_base_target_field = f"target_{tax_base_rounded_field}"
                    if tax_data:
                        values[tax_base_rounded_field] = tax_data[
                            tax_base_rounded_field
                        ]
                        values[tax_base_raw_field] = tax_data[tax_base_raw_field]
                        if tax_base_rounded_field in current_manual_tax_amounts:
                            values[tax_base_target_field] = current_manual_tax_amounts[
                                tax_base_rounded_field
                            ]
                        elif (
                            not suffix
                            and "base_amount_currency" in current_manual_tax_amounts
                        ):
                            values[tax_base_target_field] = tax_data[
                                tax_base_rounded_field
                            ]
                        else:
                            values[tax_base_target_field] = tax_data[tax_base_raw_field]
                    else:
                        values[tax_base_rounded_field] = excluded_rounded_amount
                        values[tax_base_raw_field] = excluded_raw_amount
                        values[tax_base_target_field] = excluded_target_amount

                    tax_rounded_field = f"tax_amount{suffix}"
                    tax_raw_field = f"raw_{tax_rounded_field}"
                    tax_target_field = f"target_{tax_rounded_field}"
                    values[tax_rounded_field] = 0.0
                    values[tax_raw_field] = 0.0
                    values[tax_target_field] = 0.0

            if tax_data:
                reverse_charge_sign = -1 if tax_data["is_reverse_charge"] else 1
                values = values_per_grouping_key[grouping_key]
                for suffix in ("_currency", ""):
                    tax_rounded_field = f"tax_amount{suffix}"
                    tax_raw_field = f"raw_{tax_rounded_field}"
                    tax_target_field = f"target_{tax_rounded_field}"
                    values[tax_rounded_field] += tax_data[tax_rounded_field]
                    values[tax_raw_field] += tax_data[tax_raw_field]
                    if tax_rounded_field in current_manual_tax_amounts:
                        values[tax_target_field] += (
                            reverse_charge_sign
                            * current_manual_tax_amounts[tax_rounded_field]
                        )
                    elif (
                        not suffix
                        and "tax_amount_currency" in current_manual_tax_amounts
                    ):
                        values[tax_target_field] = tax_data[tax_rounded_field]
                    else:
                        values[tax_target_field] += tax_data[tax_raw_field]
                values["taxes_data"].append(tax_data)

        return values_per_grouping_key

    @api.model
    def _aggregate_base_lines_tax_details(self, base_lines, grouping_function):
        """Call ``_aggregate_base_line_tax_details`` on multiple base lines.

        :return: a list of ``<base_line, results>`` tuples.
        """
        return [
            (
                base_line,
                self._aggregate_base_line_tax_details(base_line, grouping_function),
            )
            for base_line in base_lines
        ]

    @api.model
    def _aggregate_base_lines_aggregated_values(self, base_lines_aggregated_values):
        """Aggregate the per-line results for the whole document.

        :param base_lines_aggregated_values: result of
            ``_aggregate_base_lines_tax_details``.
        :return: a mapping ``grouping_key`` -> aggregated amounts, with a
            ``base_line_x_taxes_data`` list of ``<base_line, taxes_data>``.
        """
        default_float_fields = set()
        for prefix in ("", "raw_", "target_"):
            for suffix in ("_currency", ""):
                for field in ("base_amount", "tax_amount", "total_excluded"):
                    default_float_fields.add(f"{prefix}{field}{suffix}")

        values_per_grouping_key = defaultdict(
            lambda: {
                **dict.fromkeys(default_float_fields, 0.0),
                "base_line_x_taxes_data": [],
            }
        )
        for base_line, aggregated_values in base_lines_aggregated_values:
            for grouping_key, values in aggregated_values.items():
                agg_values = values_per_grouping_key[grouping_key]
                for field in default_float_fields:
                    agg_values[field] += values[field]
                agg_values["grouping_key"] = grouping_key
                agg_values["base_line_x_taxes_data"].append(
                    (base_line, values["taxes_data"])
                )
        return values_per_grouping_key

    @api.model
    def _round_tax_details_tax_amounts(self, base_lines, company, mode="mixed"):
        """Dispatch the 'round_globally' delta across tax amount details.

        :param mode: ``excluded`` (round base/tax independently),
            ``included`` (round base+tax then subtract tax) or ``mixed``
            (pick per tax depending on ``price_include``).
        """

        def grouping_function(base_line, tax_data):
            if not tax_data:
                return
            return {
                "tax": tax_data["tax"],
                "currency": base_line["currency_id"],
                "is_refund": base_line["is_refund"],
                "is_reverse_charge": tax_data["is_reverse_charge"],
                "price_include": tax_data["price_include"],
                "computation_key": base_line["computation_key"],
            }

        base_lines_aggregated_values = self._aggregate_base_lines_tax_details(
            base_lines, grouping_function
        )
        values_per_grouping_key = self._aggregate_base_lines_aggregated_values(
            base_lines_aggregated_values
        )
        for grouping_key, values in values_per_grouping_key.items():
            if not grouping_key:
                continue

            price_include = grouping_key["price_include"]
            currency = grouping_key["currency"]
            for delta_currency_indicator, delta_currency in (
                ("_currency", currency),
                ("", company.currency_id),
            ):
                raw_total_tax_amount = values[
                    f"target_tax_amount{delta_currency_indicator}"
                ]
                rounded_raw_total_tax_amount = delta_currency.round(
                    raw_total_tax_amount
                )
                total_tax_amount = values[f"tax_amount{delta_currency_indicator}"]
                delta_total_tax_amount = rounded_raw_total_tax_amount - total_tax_amount

                if not delta_currency.is_zero(delta_total_tax_amount):
                    target_factors = [
                        {
                            "factor": tax_data[
                                f"raw_tax_amount{delta_currency_indicator}"
                            ],
                            "tax_data": tax_data,
                        }
                        for _base_line, taxes_data in values["base_line_x_taxes_data"]
                        for tax_data in taxes_data
                    ]
                    amounts_to_distribute = self._distribute_delta_amount_smoothly(
                        precision_digits=delta_currency.decimal_places,
                        delta_amount=delta_total_tax_amount,
                        target_factors=target_factors,
                    )
                    for target_factor, amount_to_distribute in zip(
                        target_factors, amounts_to_distribute, strict=False
                    ):
                        tax_data = target_factor["tax_data"]
                        tax_data[f"tax_amount{delta_currency_indicator}"] += (
                            amount_to_distribute
                        )

                raw_total_base_amount = values[
                    f"target_base_amount{delta_currency_indicator}"
                ]
                if (mode == "mixed" and price_include) or mode == "included":
                    raw_total_amount = raw_total_base_amount + raw_total_tax_amount
                    rounded_raw_total_amount = delta_currency.round(raw_total_amount)
                    total_amount = (
                        values[f"base_amount{delta_currency_indicator}"]
                        + total_tax_amount
                        + delta_total_tax_amount
                    )
                    delta_total_base_amount = rounded_raw_total_amount - total_amount
                elif (mode == "mixed" and not price_include) or mode == "excluded":
                    rounded_raw_total_base_amount = delta_currency.round(
                        raw_total_base_amount
                    )
                    total_base_amount = values[f"base_amount{delta_currency_indicator}"]
                    delta_total_base_amount = (
                        rounded_raw_total_base_amount - total_base_amount
                    )

                if not delta_currency.is_zero(delta_total_base_amount):
                    target_factors = [
                        {
                            "factor": tax_data[
                                f"raw_base_amount{delta_currency_indicator}"
                            ],
                            "tax_data": tax_data,
                        }
                        for _base_line, taxes_data in values["base_line_x_taxes_data"]
                        for tax_data in taxes_data
                    ]
                    amounts_to_distribute = self._distribute_delta_amount_smoothly(
                        precision_digits=delta_currency.decimal_places,
                        delta_amount=delta_total_base_amount,
                        target_factors=target_factors,
                    )
                    for target_factor, amount_to_distribute in zip(
                        target_factors, amounts_to_distribute, strict=False
                    ):
                        tax_data = target_factor["tax_data"]
                        tax_data[f"base_amount{delta_currency_indicator}"] += (
                            amount_to_distribute
                        )

    @api.model
    def _round_tax_details_base_lines(self, base_lines, company, mode="mixed"):
        """Additional global rounding for price-included/excluded taxes.

        This does not change ``taxes_data`` rounding; it computes an
        adjustment for ``tax_details['total_excluded{_currency}']`` and
        stores it as ``tax_details['delta_total_excluded{_currency}']``.

        :param mode: see ``_round_tax_details_tax_amounts``.
        """

        def grouping_function(base_line, tax_data):
            return {
                "currency": base_line["currency_id"],
                "is_refund": base_line["is_refund"],
                "computation_key": base_line["computation_key"],
            }

        base_lines_aggregated_values = self._aggregate_base_lines_tax_details(
            base_lines, grouping_function
        )
        values_per_grouping_key = self._aggregate_base_lines_aggregated_values(
            base_lines_aggregated_values
        )
        for grouping_key, values in values_per_grouping_key.items():
            current_mode = mode
            if mode == "mixed":
                current_mode = "included"
                for base_line, taxes_data in values["base_line_x_taxes_data"]:
                    if any(
                        not tax_data["price_include"]
                        for tax_data in taxes_data
                        if (
                            not base_line["currency_id"].is_zero(
                                tax_data["tax_amount_currency"]
                            )
                            or not company.currency_id.is_zero(tax_data["tax_amount"])
                        )
                    ):
                        current_mode = "excluded"
                        break

            currency = grouping_key["currency"]
            for delta_currency_indicator, delta_currency in (
                ("_currency", currency),
                ("", company.currency_id),
            ):
                if current_mode == "excluded":
                    raw_total_excluded = values[
                        f"target_total_excluded{delta_currency_indicator}"
                    ]
                    if not raw_total_excluded:
                        continue

                    rounded_raw_total_excluded = delta_currency.round(
                        raw_total_excluded
                    )
                    total_excluded = values[f"total_excluded{delta_currency_indicator}"]
                    delta_total_excluded = rounded_raw_total_excluded - total_excluded
                    target_factors = [
                        {
                            "factor": base_line["tax_details"][
                                f"raw_total_excluded{delta_currency_indicator}"
                            ],
                            "base_line": base_line,
                        }
                        for base_line, _taxes_data in values["base_line_x_taxes_data"]
                    ]
                else:
                    raw_total_included = (
                        values[f"target_total_excluded{delta_currency_indicator}"]
                        + values[f"target_tax_amount{delta_currency_indicator}"]
                    )
                    if not raw_total_included:
                        continue

                    rounded_raw_total_included = delta_currency.round(
                        raw_total_included
                    )
                    total_included = (
                        values[f"total_excluded{delta_currency_indicator}"]
                        + values[f"tax_amount{delta_currency_indicator}"]
                    )
                    delta_total_excluded = rounded_raw_total_included - total_included
                    target_factors = [
                        {
                            "factor": base_line["tax_details"][
                                f"raw_total_included{delta_currency_indicator}"
                            ],
                            "base_line": base_line,
                        }
                        for base_line, _taxes_data in values["base_line_x_taxes_data"]
                    ]

                amounts_to_distribute = self._distribute_delta_amount_smoothly(
                    precision_digits=delta_currency.decimal_places,
                    delta_amount=delta_total_excluded,
                    target_factors=target_factors,
                )
                for target_factor, amount_to_distribute in zip(
                    target_factors, amounts_to_distribute, strict=False
                ):
                    base_line = target_factor["base_line"]
                    base_line["tax_details"][
                        f"delta_total_excluded{delta_currency_indicator}"
                    ] += amount_to_distribute

    @api.model
    def _round_tax_details_tax_amounts_from_tax_lines(
        self, base_lines, company, tax_lines
    ):
        """Aggregate totals according to existing tax lines, if given.

        :param tax_lines: an optional list of dicts from
            ``_prepare_tax_line_for_taxes_computation`` (not ported in
            this repo -- ``tax_lines`` is always empty in practice, and
            this method is then a no-op, kept only for interface parity
            with ``_round_base_lines_tax_details``).
        """
        if not tax_lines:
            return

        total_per_tax_line_key = defaultdict(
            lambda: {
                "currency": None,
                "tax_amount_currency": 0.0,
                "tax_amount": 0.0,
            }
        )
        for tax_line in tax_lines:
            tax_rep = tax_line["tax_repartition_line_id"]
            sign = tax_line["sign"]
            tax = tax_rep.tax_id
            currency = tax_line["currency_id"]
            tax_line_key = (tax.id, currency.id, tax_rep.document_type == "reverse")
            total_per_tax_line_key[tax_line_key]["currency"] = currency
            total_per_tax_line_key[tax_line_key]["tax_amount_currency"] += (
                sign * tax_line["amount_currency"]
            )
            total_per_tax_line_key[tax_line_key]["tax_amount"] += (
                sign * tax_line["balance"]
            )

        def grouping_function(base_line, tax_data):
            if not tax_data:
                return
            return {
                "tax": tax_data["tax"],
                "currency": base_line["currency_id"],
                "is_refund": base_line["is_refund"],
            }

        base_lines_aggregated_values = self._aggregate_base_lines_tax_details(
            base_lines, grouping_function
        )
        values_per_grouping_key = self._aggregate_base_lines_aggregated_values(
            base_lines_aggregated_values
        )
        for grouping_key, values in values_per_grouping_key.items():
            if not grouping_key:
                continue

            currency = grouping_key["currency"]
            tax_line_key = (
                grouping_key["tax"].id,
                currency.id,
                grouping_key["is_refund"],
            )
            if tax_line_key not in total_per_tax_line_key:
                continue

            for delta_currency_indicator, delta_currency in (
                ("_currency", currency),
                ("", company.currency_id),
            ):
                current_total_tax_amount = values[
                    f"tax_amount{delta_currency_indicator}"
                ]
                if not current_total_tax_amount:
                    continue

                target_total_tax_amount = total_per_tax_line_key[tax_line_key][
                    f"tax_amount{delta_currency_indicator}"
                ]
                delta_total_tax_amount = (
                    target_total_tax_amount - current_total_tax_amount
                )

                target_factors = [
                    {
                        "factor": tax_data[f"tax_amount{delta_currency_indicator}"],
                        "tax_data": tax_data,
                    }
                    for _base_line, taxes_data in values["base_line_x_taxes_data"]
                    for tax_data in taxes_data
                ]
                amounts_to_distribute = self._distribute_delta_amount_smoothly(
                    precision_digits=delta_currency.decimal_places,
                    delta_amount=delta_total_tax_amount,
                    target_factors=target_factors,
                )
                for target_factor, amount_to_distribute in zip(
                    target_factors, amounts_to_distribute, strict=False
                ):
                    tax_data = target_factor["tax_data"]
                    tax_data[f"tax_amount{delta_currency_indicator}"] += (
                        amount_to_distribute
                    )

    @api.model
    def _round_base_lines_tax_details(self, base_lines, company, tax_lines=None):
        """Round the ``tax_details`` added by ``_add_tax_details_in_base_lines``.

        Copies every ``raw_``-prefixed float in ``tax_details`` to its
        rounded counterpart, taking care of the rounding issues that can
        appear with ``round_globally`` (especially with price-included
        taxes): totals are rounded per tax first, then the delta is
        distributed across base lines, available as
        ``delta_total_excluded_currency``/``delta_total_excluded``.

        :param base_lines: base lines from
            ``_prepare_base_line_for_taxes_computation``, already run
            through ``_add_tax_details_in_base_lines``.
        :param company: the company owning the base lines.
        :param tax_lines: see ``_round_tax_details_tax_amounts_from_tax_lines``.
        """
        for base_line in base_lines:
            tax_details = base_line["tax_details"]

            for suffix, currency in (
                ("_currency", base_line["currency_id"]),
                ("", company.currency_id),
            ):
                total_excluded_field = f"total_excluded{suffix}"
                tax_details[total_excluded_field] = currency.round(
                    tax_details[f"raw_{total_excluded_field}"]
                )

                for tax_data in tax_details["taxes_data"]:
                    for prefix in ("base", "tax"):
                        field = f"{prefix}_amount{suffix}"
                        tax_data[field] = currency.round(tax_data[f"raw_{field}"])

        for base_line in base_lines:
            manual_tax_amounts = base_line["manual_tax_amounts"]
            rate = base_line["rate"]
            tax_details = base_line["tax_details"]

            for suffix, currency in (
                ("_currency", base_line["currency_id"]),
                ("", company.currency_id),
            ):
                total_field = f"total_excluded{suffix}"
                manual_field = f"manual_{total_field}"
                if base_line[manual_field] is not None:
                    tax_details[total_field] = base_line[manual_field]
                    if suffix == "_currency" and rate:
                        tax_details["total_excluded"] = company.currency_id.round(
                            tax_details[total_field] / rate
                        )

                for tax_data in tax_details["taxes_data"]:
                    tax = tax_data["tax"]
                    reverse_charge_sign = -1 if tax_data["is_reverse_charge"] else 1
                    current_manual_tax_amounts = (
                        manual_tax_amounts and manual_tax_amounts.get(str(tax.id)) or {}
                    )
                    for prefix, factor in (("base", 1), ("tax", reverse_charge_sign)):
                        field = f"{prefix}_amount{suffix}"
                        if field in current_manual_tax_amounts:
                            tax_data[field] = currency.round(
                                factor * current_manual_tax_amounts[field]
                            )
                            if suffix == "_currency" and rate:
                                tax_data[f"{prefix}_amount"] = (
                                    company.currency_id.round(tax_data[field] / rate)
                                )

        for base_line in base_lines:
            tax_details = base_line["tax_details"]

            for suffix in ("_currency", ""):
                tax_details[f"delta_total_excluded{suffix}"] = 0.0
                tax_details[f"total_included{suffix}"] = tax_details[
                    f"total_excluded{suffix}"
                ]

                for tax_data in tax_details["taxes_data"]:
                    tax_details[f"total_included{suffix}"] += tax_data[
                        f"tax_amount{suffix}"
                    ]

        self._round_tax_details_tax_amounts(base_lines, company)
        self._round_tax_details_base_lines(base_lines, company)
        self._round_tax_details_tax_amounts_from_tax_lines(
            base_lines, company, tax_lines
        )

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: ACCOUNTING / REPARTITION MAPPING
    # -------------------------------------------------------------------

    @api.model
    def _prepare_base_line_grouping_key(self, base_line):
        """Accounting grouping key for a base line, used by ``_prepare_tax_lines``.

        :param base_line: a base line from
            ``_prepare_base_line_for_taxes_computation``.
        :return: the grouping key to generate the tax line for this base line.
        """
        return {
            "partner_id": base_line["partner_id"].id,
            "currency_id": base_line["currency_id"].id,
            "analytic_distribution": base_line["analytic_distribution"],
            "account_id": base_line["account_id"].id,
            "tax_ids": [Command.set(base_line["tax_ids"].ids)],
        }

    @api.model
    def _prepare_base_line_tax_repartition_grouping_key(
        self, base_line, base_line_grouping_key, tax_data, tax_rep_data
    ):
        """Add a single tax repartition line's data to the grouping key.

        :param base_line: a base line from
            ``_prepare_base_line_for_taxes_computation``.
        :param base_line_grouping_key: from ``_prepare_base_line_grouping_key``.
        :param tax_data: an entry of ``base_line['tax_details']['taxes_data']``.
        :param tax_rep_data: an entry of ``tax_data['tax_reps_data']``.
        :return: the grouping key to generate the tax line for this repartition line.
        """
        tax_rep = tax_rep_data["tax_rep"]
        return {
            **base_line_grouping_key,
            "tax_repartition_line_id": tax_rep.id,
            "partner_id": base_line["partner_id"].id,
            "currency_id": base_line["currency_id"].id,
            "group_tax_id": tax_data["group"].id,
            "analytic_distribution": base_line_grouping_key["analytic_distribution"],
            "account_id": (
                tax_rep_data["account"].id or base_line_grouping_key["account_id"]
            ),
            "tax_ids": [Command.set(tax_rep_data["taxes"].ids)],
            "tax_tag_ids": [Command.set(tax_rep_data["tax_tags"].ids)],
            "__keep_zero_line": False,
        }

    @api.model
    def _prepare_tax_line_repartition_grouping_key(self, tax_line):
        """Accounting grouping key for an existing tax line.

        Kept consistent with ``_prepare_base_line_tax_repartition_grouping_key``
        so ``_prepare_tax_lines`` can match one against the other -- see
        that method's ``tax_lines`` parameter for why this is unreachable
        in practice in this repo today.

        :param tax_line: a dict from ``_prepare_tax_line_for_taxes_computation``.
        :return: the grouping key for ``tax_line``.
        """
        return {
            "tax_repartition_line_id": tax_line["tax_repartition_line_id"].id,
            "partner_id": tax_line["partner_id"].id,
            "currency_id": tax_line["currency_id"].id,
            "group_tax_id": tax_line["group_tax_id"].id,
            "analytic_distribution": tax_line["analytic_distribution"],
            "account_id": tax_line["account_id"].id,
            "tax_ids": [Command.set(tax_line["tax_ids"].ids)],
            "tax_tag_ids": [Command.set(tax_line["tax_tag_ids"].ids)],
        }

    @api.model
    def _add_accounting_data_to_base_line_tax_details(
        self, base_line, company, include_caba_tags=False
    ):
        """Add repartition-line accounting data to a base line's tax details.

        For each ``tax_data`` in ``base_line['tax_details']['taxes_data']``,
        adds ``tax_reps_data``: a list of dicts with ``tax_rep``,
        ``tax_amount_currency``, ``tax_amount``, ``account`` and (once
        ``grouping_key``/``taxes``/``tax_tags`` are filled below) enough
        to build a tax line. Also sets ``base_line['tax_tag_ids']``.

        Which of ``repartition_line_base_ids``/``repartition_line_reverse_ids``
        is used is driven by ``base_line['is_refund']`` -- never by a
        document/move type, per this issue's Keputusan Desain.

        :param base_line: a base line, already run through
            ``_add_tax_details_in_base_line``.
        :param company: the company owning the base line.
        :param include_caba_tags: kept for signature parity with upstream;
            has no effect (see the class docstring).
        """
        is_refund = base_line["is_refund"]
        currency = base_line["currency_id"]
        company_currency = company.currency_id
        repartition_lines_field = (
            "repartition_line_reverse_ids" if is_refund else "repartition_line_base_ids"
        )

        taxes_data = base_line["tax_details"]["taxes_data"]
        base_line["tax_tag_ids"] = self.env["account.tag"]

        for tax_data in taxes_data:
            tax = tax_data["tax"]

            base_line["tax_tag_ids"] |= (
                tax[repartition_lines_field]
                .filtered(lambda line: line.repartition_type == "base")
                .tag_ids
            )

            if tax_data["is_reverse_charge"]:
                tax_reps = tax[repartition_lines_field].filtered(
                    lambda line: line.repartition_type == "tax" and line.factor < 0.0
                )
                tax_rep_sign = -1.0
            else:
                tax_reps = tax[repartition_lines_field].filtered(
                    lambda line: line.repartition_type == "tax" and line.factor >= 0.0
                )
                tax_rep_sign = 1.0

            total_tax_rep_amounts = {"tax_amount_currency": 0.0, "tax_amount": 0.0}
            tax_reps_data = tax_data["tax_reps_data"] = []
            for tax_rep in tax_reps:
                tax_amount_currency = tax_data.get("tax_amount_currency")
                if self.env.context.get("compute_all_use_raw_base_lines"):
                    tax_amount_currency = tax_data.get("raw_tax_amount_currency")

                tax_rep_data = {
                    "tax_rep": tax_rep,
                    "tax_amount_currency": currency.round(
                        tax_amount_currency * tax_rep.factor * tax_rep_sign
                    ),
                    "tax_amount": company_currency.round(
                        tax_data["tax_amount"] * tax_rep.factor * tax_rep_sign
                    ),
                    "account": tax_rep.account_id or base_line["account_id"],
                }
                total_tax_rep_amounts["tax_amount_currency"] += tax_rep_data[
                    "tax_amount_currency"
                ]
                total_tax_rep_amounts["tax_amount"] += tax_rep_data["tax_amount"]
                tax_reps_data.append(tax_rep_data)

            sorted_tax_reps_data = sorted(
                tax_reps_data,
                key=lambda line: (
                    -abs(line["tax_amount_currency"]),
                    -abs(line["tax_amount"]),
                ),
            )
            for delta_suffix, delta_currency in (
                ("_currency", currency),
                ("", company_currency),
            ):
                field = f"tax_amount{delta_suffix}"
                tax_amount = tax_data.get(field)
                if self.env.context.get("compute_all_use_raw_base_lines"):
                    tax_amount = tax_data.get(f"raw_{field}")

                delta_amount = tax_amount - total_tax_rep_amounts[field]
                target_factors = [
                    {"factor": tax_rep_data[field], "tax_rep_data": tax_rep_data}
                    for tax_rep_data in sorted_tax_reps_data
                ]
                amounts_to_distribute = self._distribute_delta_amount_smoothly(
                    precision_digits=delta_currency.decimal_places,
                    delta_amount=delta_amount,
                    target_factors=target_factors,
                )
                for target_factor, amount_to_distribute in zip(
                    target_factors, amounts_to_distribute, strict=False
                ):
                    target_factor["tax_rep_data"][field] += amount_to_distribute

        for tax_data in reversed(taxes_data):
            for tax_rep_data in tax_data["tax_reps_data"]:
                tax_rep = tax_rep_data["tax_rep"]
                tax_rep_data["taxes"] = tax_data["taxes"]
                tax_rep_data["tax_tags"] = tax_rep.tag_ids

                base_line_grouping_key = self._prepare_base_line_grouping_key(base_line)
                tax_rep_data["grouping_key"] = (
                    self._prepare_base_line_tax_repartition_grouping_key(
                        base_line, base_line_grouping_key, tax_data, tax_rep_data
                    )
                )

    @api.model
    def _add_accounting_data_in_base_lines_tax_details(
        self, base_lines, company, include_caba_tags=False
    ):
        """Call ``_add_accounting_data_to_base_line_tax_details`` on many lines."""
        for base_line in base_lines:
            self._add_accounting_data_to_base_line_tax_details(
                base_line, company, include_caba_tags=include_caba_tags
            )

    @api.model
    def _prepare_tax_lines(self, base_lines, company, tax_lines=None):
        """Prepare the diff of tax lines to add/update/delete for base lines.

        Call ``_add_tax_details_in_base_lines``,
        ``_round_base_lines_tax_details`` and
        ``_add_accounting_data_in_base_lines_tax_details`` before this.

        :param base_lines: base lines from
            ``_prepare_base_line_for_taxes_computation``.
        :param company: the company owning the base lines.
        :param tax_lines: see ``_round_tax_details_tax_amounts_from_tax_lines``.
        :return: a dict with ``tax_lines_to_add``, ``tax_lines_to_delete``,
            ``tax_lines_to_update`` and ``base_lines_to_update``.
        """
        tax_lines_mapping = defaultdict(
            lambda: {"tax_base_amount": 0.0, "amount_currency": 0.0, "balance": 0.0}
        )

        base_lines_to_update = []
        for base_line in base_lines:
            sign = base_line["sign"]
            tax_details = base_line["tax_details"]
            base_lines_to_update.append(
                (
                    base_line,
                    {
                        "tax_tag_ids": [Command.set(base_line["tax_tag_ids"].ids)],
                        "amount_currency": sign
                        * (
                            tax_details["total_excluded_currency"]
                            + tax_details["delta_total_excluded_currency"]
                        ),
                        "balance": sign
                        * (
                            tax_details["total_excluded"]
                            + tax_details["delta_total_excluded"]
                        ),
                    },
                )
            )
            for tax_data in tax_details["taxes_data"]:
                tax = tax_data["tax"]
                for tax_rep_data in tax_data["tax_reps_data"]:
                    grouping_key = frozendict(tax_rep_data["grouping_key"])
                    tax_line = tax_lines_mapping[grouping_key]
                    tax_line["name"] = base_line.get("manual_tax_line_name", tax.name)
                    tax_line["tax_base_amount"] += sign * tax_data["base_amount"]
                    tax_line["amount_currency"] += (
                        sign * tax_rep_data["tax_amount_currency"]
                    )
                    tax_line["balance"] += sign * tax_rep_data["tax_amount"]

        tax_lines_mapping = {
            frozendict(
                {
                    grouping_k: k[grouping_k]
                    for grouping_k in k
                    if not grouping_k.startswith("__")
                }
            ): v
            for k, v in tax_lines_mapping.items()
            if (
                k["__keep_zero_line"]
                or not self.env["res.currency"]
                .browse(k["currency_id"])
                .is_zero(v["amount_currency"])
                or not company.currency_id.is_zero(v["balance"])
            )
        }

        tax_lines_to_update = []
        tax_lines_to_delete = []
        for tax_line in tax_lines or []:
            grouping_key = frozendict(
                self._prepare_tax_line_repartition_grouping_key(tax_line)
            )
            already_updated = grouping_key in tax_lines_to_update
            if grouping_key in tax_lines_mapping and not already_updated:
                amounts = tax_lines_mapping.pop(grouping_key)
                tax_lines_to_update.append((tax_line, grouping_key, amounts))
            else:
                tax_lines_to_delete.append(tax_line)
        tax_lines_to_add = [
            {**grouping_key, **values}
            for grouping_key, values in tax_lines_mapping.items()
        ]

        return {
            "tax_lines_to_add": tax_lines_to_add,
            "tax_lines_to_delete": tax_lines_to_delete,
            "tax_lines_to_update": tax_lines_to_update,
            "base_lines_to_update": base_lines_to_update,
        }

    # -------------------------------------------------------------------
    # TAXES COMPUTATION: LEGACY API
    # -------------------------------------------------------------------

    def compute_all(
        self,
        price_unit,
        currency=None,
        quantity=1.0,
        product=None,
        partner=None,
        is_refund=False,
        handle_price_include=True,
        include_caba_tags=False,
        rounding_method=None,
    ):
        """Compute all information required to apply taxes (in self + children).

        Considers the sequence of the parent for a group of taxes.

        :param price_unit: the unit price of the line to compute taxes on.
        :param currency: the currency ``price_unit`` is expressed in.
        :param quantity: the quantity of the product to compute taxes on.
        :param product: the product to compute taxes on, if any.
        :param partner: the partner to compute taxes on, if any (used for
            the tax name's language).
        :param is_refund: whether this is a refund -- selects
            ``repartition_line_reverse_ids`` over ``repartition_line_base_ids``.
        :param handle_price_include: if False, ``price_unit`` is treated
            as the base of all computations, ignoring price-included taxes.
        :param include_caba_tags: kept for signature parity with upstream;
            has no effect (see the class docstring).
        :param rounding_method: overrides the company's rounding method.
        :return: a dict with ``base_tags``, ``taxes`` (one dict per tax
            repartition line), ``total_excluded``, ``total_included`` and
            ``total_void``.
        """
        company = self[0].company_id if self else self.env.company

        currency = currency or company.currency_id
        if not handle_price_include:
            special_mode = "total_excluded"
        else:
            special_mode = False
        base_line = self._prepare_base_line_for_taxes_computation(
            None,
            partner_id=partner,
            currency_id=currency,
            product_id=product,
            tax_ids=self,
            price_unit=price_unit,
            quantity=quantity,
            is_refund=is_refund,
            special_mode=special_mode,
        )
        self._add_tax_details_in_base_line(
            base_line, company, rounding_method=rounding_method
        )
        self.with_context(
            compute_all_use_raw_base_lines=True,
        )._add_accounting_data_to_base_line_tax_details(
            base_line, company, include_caba_tags=include_caba_tags
        )

        tax_details = base_line["tax_details"]
        total_void = total_excluded = tax_details["raw_total_excluded_currency"]
        total_included = tax_details["raw_total_included_currency"]

        taxes = []
        for tax_data in tax_details["taxes_data"]:
            tax = tax_data["tax"]
            for tax_rep_data in tax_data["tax_reps_data"]:
                rep_line = tax_rep_data["tax_rep"]
                taxes.append(
                    {
                        "id": tax.id,
                        "name": (
                            partner
                            and tax.with_context(lang=partner.lang).name
                            or tax.name
                        ),
                        "amount": tax_rep_data["tax_amount_currency"],
                        "base": tax_data["raw_base_amount_currency"],
                        "sequence": tax.sequence,
                        "account_id": tax_rep_data["account"].id,
                        "is_reverse_charge": tax_data["is_reverse_charge"],
                        "price_include": tax.price_include,
                        "tax_repartition_line_id": rep_line.id,
                        "group": tax_data["group"],
                        "tag_ids": tax_rep_data["tax_tags"].ids,
                        "tax_ids": tax_rep_data["taxes"].ids,
                    }
                )
                if not rep_line.account_id:
                    total_void += tax_rep_data["tax_amount_currency"]

        if self.env.context.get("round_base", True):
            total_excluded = currency.round(total_excluded)
            total_included = currency.round(total_included)

        return {
            "base_tags": base_line["tax_tag_ids"].ids,
            "taxes": taxes,
            "total_excluded": total_excluded,
            "total_included": total_included,
            "total_void": total_void,
        }

    def copy_data(self, default=None):
        """Suffix the copied tax's name with '(copy)' unless 'default' overrides it."""
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for tax, vals in zip(self, vals_list, strict=True):
            if "name" not in default:
                vals["name"] = self.env._("%(name)s (copy)", name=tax.name)
        return vals_list
