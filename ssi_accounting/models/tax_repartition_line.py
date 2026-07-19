# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_is_zero


class TaxRepartitionLine(models.Model):
    """How much of a tax, and to which account, a journal entry line posts.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_tax.py`` (class
    ``AccountTaxRepartitionLine``, model ``account.tax.repartition.line``),
    because that model lives inside the ``account`` module, which
    ``ssi_accounting`` must not depend on. Renamed to ``tax.repartition_line``
    per this issue's Keputusan Desain.

    **Vocabulary neutralised**: ``document_type`` values ``invoice``/
    ``refund`` (which assume an invoice document this repo does not have)
    become ``base``/``reverse`` here. The base/reverse split itself is
    kept -- not dropped -- because a journal entry can be reversed, and
    the reversal must use the oppositely-signed repartition group.

    **Deliberately dropped from the upstream model**: ``use_in_tax_closing``
    -- tax closing entry generation is out of this issue's scope (see
    ``tax_group.py``'s docstring), so there is nothing yet to flag a
    repartition line for.

    As a pure child model of ``tax`` (``ondelete="cascade"``), this model
    gets no group, menu, standalone view, or ``ir.rule`` of its own -- its
    ``ir.model.access`` rows are written together with ``tax``'s own rows
    (see ``security/ir.model.access.csv``), and it is only ever reached
    through ``tax.repartition_line_base_ids``/``repartition_line_reverse_ids``.

    **The 100% factor check lives here, on the child, not on ``tax`` as a
    dotted ``@api.constrains('repartition_line_base_ids.factor_percent')``.**
    A dotted constrain on the parent only reliably fires when the O2M
    field itself is touched from the parent's side; it does not fire when
    a child record's own field (``factor_percent``) is written directly,
    which is exactly how these lines get edited (inline editable list on
    the ``tax`` form, or a plain ``tax.repartition_line`` write). Defining
    the constrain here, triggered by this model's own fields, covers both.
    """

    _name = "tax.repartition_line"
    _description = "Tax Repartition Line"
    _order = "document_type, repartition_type, sequence, id"
    _check_company_auto = True

    factor_percent = fields.Float(
        string="%",
        digits=(16, 12),
        required=True,
        default=100,
        help="Factor to apply on the journal entry lines generated from "
        "this repartition line, in percent.",
    )
    factor = fields.Float(
        string="Factor Ratio",
        compute="_compute_factor",
        help="Factor to apply on the journal entry lines generated from "
        "this repartition line, as a ratio (factor_percent / 100).",
    )
    repartition_type = fields.Selection(
        selection=[("base", "Base"), ("tax", "Tax")],
        string="Based On",
        required=True,
        default="tax",
        help="Base on which the factor is applied: the untaxed amount "
        "('Base'), or the computed tax amount ('Tax').",
    )
    document_type = fields.Selection(
        selection=[("base", "Base"), ("reverse", "Reverse")],
        required=True,
        help="Whether this line applies when the tax is used normally "
        "('Base'), or when the journal entry it was used on is reversed "
        "('Reverse').",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        domain="[('account_type', 'not in', "
        "('asset_receivable', 'liability_payable', 'off_balance'))]",
        check_company=True,
        help="Account on which to post the tax amount for this repartition "
        "line. Left empty on 'Base' repartition lines.",
    )
    tag_ids = fields.Many2many(
        comodel_name="account.tag",
        string="Tax Grids",
        domain=[("applicability", "=", "taxes")],
        ondelete="restrict",
        help="Optional grid tags used for tax reporting.",
    )
    tag_ids_domain = fields.Binary(
        compute="_compute_tag_ids_domain",
        help="Technical field: dynamic domain applied to 'tag_ids', "
        "restricted to the tax's own country (or no country).",
    )
    tax_id = fields.Many2one(
        comodel_name="tax",
        index="btree_not_null",
        ondelete="cascade",
        help="Tax this repartition line belongs to.",
    )
    company_id = fields.Many2one(
        string="Company",
        comodel_name="res.company",
        related="tax_id.company_id",
        store=True,
        help="Company this repartition line belongs to, inherited from its tax.",
    )
    sequence = fields.Integer(
        default=1,
        help="Order in which repartition lines are displayed and matched. "
        "For reversal to work correctly, 'Base' and 'Reverse' repartition "
        "lines should be arranged in the same order.",
    )

    @api.depends("factor_percent")
    def _compute_factor(self):
        for line in self:
            line.factor = line.factor_percent / 100.0

    @api.depends("tax_id.country_id")
    def _compute_tag_ids_domain(self):
        for line in self:
            line.tag_ids_domain = [
                ("applicability", "=", "taxes"),
                ("country_id", "in", (False, line.tax_id.country_id.id)),
            ]

    @api.onchange("repartition_type")
    def onchange_account_id(self):
        """Clear 'account_id' when 'repartition_type' switches back to 'base'."""
        if self.repartition_type == "base":
            self.account_id = False

    @api.constrains("factor_percent", "repartition_type")
    def _check_repartition_line_factor(self):
        checked_groups = set()
        for line in self:
            group = (line.tax_id.id, line.document_type)
            if group in checked_groups:
                continue
            checked_groups.add(group)
            siblings = line.tax_id.repartition_line_ids.filtered(
                lambda sibling, doc=line.document_type: sibling.document_type == doc
            )
            tax_lines = siblings.filtered(
                lambda sibling: sibling.repartition_type == "tax"
            )
            total_factor = sum(tax_lines.mapped("factor_percent"))
            if tax_lines and not float_is_zero(total_factor - 100, precision_digits=2):
                raise ValidationError(
                    self.env._(
                        """
Context: Save tax repartition line
Database ID: %(database_id)s
Problem: The total factor of the %(group)s repartition lines is
    %(total)s%%, it must be exactly 100%%
Solution: Adjust the factor of each %(group)s repartition line so their
    total equals 100%%
""",
                        database_id=line.tax_id.id,
                        group=line.document_type,
                        total=total_factor,
                    )
                )
