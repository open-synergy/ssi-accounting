# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from itertools import accumulate

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import Query


class AccountRoot(models.Model):
    """First 1-2 digits grouping of account codes.

    Ported verbatim from Odoo core ``addons/account/models/account_root.py``
    (class ``AccountRoot``, model ``account.root``), because that model
    lives inside the ``account`` module, which ``ssi_accounting`` must not
    depend on.

    This model deliberately has no database table (``_auto = False``,
    ``_table_query = '0'``) and a string primary key: every "record" is
    computed on the fly from the first two characters of an account code
    (e.g. account ``101200`` belongs to root ``10``, itself a child of root
    ``1``). Do not "fix" this into a regular stored model — the design is
    intentional and mirrors upstream Odoo exactly so any future code that
    expects this shape (e.g. a chart-of-accounts kanban grouped by root)
    keeps working unmodified.

    Because it has no table and no ``company_id``, this model gets no
    ``ir.model.access``, no ``ir.rule``, and no menu entry.
    """

    _name = "account.root"
    _description = "Account codes first 2 digits"
    _auto = False
    _table_query = "0"

    name = fields.Char(
        compute="_compute_root",
        help="The root code itself, identical to its own id.",
    )
    parent_id = fields.Many2one(
        comodel_name="account.root",
        compute="_compute_root",
        help="Root one digit shorter that this root belongs to, if any.",
    )

    @api.private
    def browse(self, ids=()):
        """Wrap 'ids' into a tuple when a single root code string is given."""
        if isinstance(ids, str):
            ids = (ids,)
        return super().browse(ids)

    def _search(self, domain, offset=0, limit=None, order=None, **kw) -> Query:
        match list(domain):
            case [("id", "in", ids)]:
                return self.browse(sorted(ids))._as_query()
            case [("id", "parent_of", ids)]:
                return self.browse(
                    sorted({s for _id in ids for s in accumulate(_id)})
                )._as_query()
        raise UserError(self.env._("Filter on the Account or its Display Name instead"))

    @api.model
    def _from_account_code(self, code):
        return self.browse(code and code[:2])

    def _compute_root(self):
        for root in self:
            root.name = root.id
            root.parent_id = self.browse(root.id[:-1] if len(root.id) > 1 else False)
