# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import Query

COMPANY_OFFSET = 10000


class AccountCodeMapping(models.Model):
    """UI-only projection of ``account.code_store`` per company.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_code_mapping.py`` (class
    ``AccountCodeMapping``, model ``account.code.mapping``), because that
    model lives inside the ``account`` module, which ``ssi_accounting``
    must not depend on. The comodel of ``account_id`` is this repo's own
    ``account.account`` model (``models/account.py``), same name as
    upstream.

    This model has no database table (``_auto = False``,
    ``_table_query = '0'``): every "record" is computed on the fly from a
    synthetic id (``account_id * COMPANY_OFFSET + company_id``), and the
    ``_search`` override refuses any access that is not scoped to a
    specific set of ``account_id`` values -- it only makes sense reached
    through ``account.code_mapping_ids`` (the "Mapping" tab on the account
    form). Do not "fix" this into a regular stored model; the design is
    intentional and mirrors upstream exactly.
    """

    _name = "account.code.mapping"
    _description = "Mapping of account codes per company"
    _auto = False
    _table_query = "0"

    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Account",
        compute="_compute_account_id",
        search=True,
        help="Account this code mapping row belongs to.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        compute="_compute_company_id",
        readonly=False,
        help="Company this row's code applies to.",
    )
    code = fields.Char(
        string="Code",
        compute="_compute_code",
        inverse="_inverse_code",
        help="Account code as seen from Company.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        mappings = self.browse(
            [
                vals["account_id"] * COMPANY_OFFSET + vals["company_id"]
                for vals in vals_list
            ]
        )
        for mapping, vals in zip(mappings, vals_list):
            mapping.code = vals["code"]
        return mappings

    def _search(self, domain, offset=0, limit=None, order=None, **kw) -> Query:
        account_ids = []

        def get_accounts(condition):
            if (
                not account_ids
                and condition.field_expr == "account_id"
                and condition.operator == "in"
            ):
                account_ids.extend(condition.value)
                return Domain(bool(condition.value))
            return condition

        remaining_domain = Domain(domain).map_conditions(get_accounts)
        if not account_ids:
            raise UserError(
                self.env._(
                    "Account Code Mapping cannot be accessed directly. "
                    "It is designed to be used only through the Chart of "
                    "Accounts."
                )
            )
        return self.browse(
            [
                account_id * COMPANY_OFFSET + company.id
                for account_id in account_ids
                for company in self.env.user.with_context(
                    active_test=True
                ).company_ids.sorted(lambda c: (c.sequence, c.name))
            ]
        ).filtered_domain(remaining_domain)._as_query()

    def _compute_account_id(self):
        for record in self:
            record.account_id = record._origin.id // COMPANY_OFFSET

    def _compute_company_id(self):
        for record in self:
            record.company_id = record._origin.id % COMPANY_OFFSET

    @api.depends("account_id.code")
    def _compute_code(self):
        for record in self:
            account = record.account_id.with_company(record.company_id._origin)
            record.code = account.code

    def _inverse_code(self):
        for record in self:
            record.account_id.with_company(record.company_id).write(
                {"code": record.code}
            )
