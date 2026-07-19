# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class TaxGroup(models.Model):
    """Grouping of taxes, mainly used to derive their payable/receivable accounts.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_tax.py`` (class ``AccountTaxGroup``,
    model ``account.tax.group``), because that model lives inside the
    ``account`` module, which ``ssi_accounting`` must not depend on.
    Renamed to ``tax_group`` here per this issue's Keputusan Desain (part
    of the tax vocabulary neutralisation together with ``tax`` and
    ``tax.repartition_line``).

    **Deliberately dropped from the upstream model** (out of this issue's
    scope): ``advance_tax_payment_account_id`` (tax closing entry is a
    later, dedicated unit) and ``pos_receipt_label`` (no Point of Sale
    integration anywhere in this repo's scope).

    Unlike upstream, ``country_id``/``country_code`` are plain fields with
    no ``_compute_country_id`` -- this repo's ``res.company`` (see
    ``res_company.py``) does not carry an ``account_fiscal_country_id``
    field to derive a default from (fiscal position is out of this
    issue's scope), so the user sets it directly when relevant.
    """

    _name = "tax_group"
    _description = "Tax Group"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(
        required=True,
        translate=True,
        help="Label of the tax group, shown on the tax configuration form "
        "and wherever taxes are grouped for reporting.",
    )
    sequence = fields.Integer(
        default=10,
        help="Used to order tax groups in lists.",
    )
    company_id = fields.Many2one(
        string="Company",
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
        help="Company that owns this tax group.",
    )
    country_id = fields.Many2one(
        string="Country",
        comodel_name="res.country",
        help="Country this tax group applies to, if relevant for reporting.",
    )
    country_code = fields.Char(
        related="country_id.code",
        help="Technical field: two-letter code of 'country_id'.",
    )
    preceding_subtotal = fields.Char(
        translate=True,
        help="If set, used as the label of a subtotal excluding this tax "
        "group, shown on documents before this group's taxes. If left "
        "empty, the tax group is shown after the 'Untaxed amount' subtotal.",
    )
    tax_payable_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Tax Payable Account",
        check_company=True,
        help="Tax current account used as a counterpart to the Tax Closing "
        "Entry when in favor of the tax authorities.",
    )
    tax_receivable_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Tax Receivable Account",
        check_company=True,
        help="Tax current account used as a counterpart to the Tax Closing "
        "Entry when in favor of the company.",
    )
