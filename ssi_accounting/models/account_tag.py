# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountTag(models.Model):
    """Free-standing tag used to classify accounts and tax grids.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_account_tag.py`` (class
    ``AccountAccountTag``, model ``account.account.tag``), because that
    model lives inside the ``account`` module, which ``ssi_accounting``
    must not depend on. Renamed to ``account.tag`` here to follow the
    same short naming convention used by ``account.root``/``account.group``.

    Deliberately dropped from the upstream model: the ``products``
    ``applicability`` value (no product-tax relationship in this repo's
    scope), and ``report_expression_id``/``balance_negate`` together with
    every ``account.report`` integration -- that whole reporting engine is
    out of scope. As a direct consequence, ``tax.repartition_line.tag_ids``
    (once that model exists) will be a plain grid tag without any
    report-derived sign semantics; document this in the README.

    Because ``report_expression_id`` is gone, the "+"/"-" sign prefix that
    upstream derives from the linked report expression cannot be reused.
    ``_compute_display_name`` below is a self-contained replacement: a tax
    grid tag whose name does not already start with a sign is shown with a
    leading "+", mirroring how tax grid tags conventionally read
    (e.g. "+036"/"-036").
    """

    _name = "account.tag"
    _description = "Account Tag"

    name = fields.Char(
        string="Tag Name",
        required=True,
        translate=True,
        help="Label of the tag, shown wherever the tag is selected (chart "
        "of accounts, tax grids).",
    )
    applicability = fields.Selection(
        selection=[("accounts", "Accounts"), ("taxes", "Taxes")],
        required=True,
        default="accounts",
        help="Where this tag can be applied: on accounts, or on tax grid lines.",
    )
    color = fields.Integer(
        string="Color Index",
        help="Color index used to display this tag as a colored badge in the UI.",
    )
    active = fields.Boolean(
        default=True,
        help="Set active to false to hide the account tag without removing it.",
    )
    country_id = fields.Many2one(
        string="Country",
        comodel_name="res.country",
        help="Country for which this tag is available, when applied on taxes.",
    )

    @api.depends("name", "applicability")
    def _compute_display_name(self):
        for tag in self:
            name = tag.name or ""
            if (
                tag.applicability == "taxes"
                and name
                and not name.startswith(("+", "-"))
            ):
                name = "+" + name
            tag.display_name = name

    @api.constrains("name", "applicability", "country_id")
    def _check_duplicate_name(self):
        for tag in self.sudo():
            if not tag._check_duplicate_name_condition():
                raise ValidationError(
                    tag.env._(
                        """
Context: Save account tag
Database ID: %(database_id)s
Problem: Another account tag with the same name, applicability and
    country already exists
Solution: Choose a different name or applicability, or clear the
    country for a tag available to every country
""",
                        database_id=tag.id,
                    )
                )

    def _check_duplicate_name_condition(self):
        self.ensure_one()
        domain = [
            ("id", "!=", self.id),
            ("name", "=", self.name),
            ("applicability", "=", self.applicability),
            ("country_id", "=", self.country_id.id),
        ]
        return not self.search_count(domain)
