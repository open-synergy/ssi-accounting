# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models
from odoo.exceptions import UserError


class ResCurrency(models.Model):
    """Guard against deactivating a currency already used in journal items.

    Adapted from Odoo core ``addons/account/models/res_currency.py``
    (class ``ResCurrency``), because that extension lives inside the
    ``account`` module, which ``ssi_accounting`` must not depend on. Only
    ``_has_accounting_entries()`` and a ``write()`` guard are kept -- the
    whole currency-rate-table builder used by ``account.report`` is out of
    scope.

    Unlike upstream (which guards against reducing a currency's rounding
    precision), this guard blocks **deactivating** a currency that has
    accounting entries -- see the acceptance criteria of the issue this
    was built for. ``_has_accounting_entries`` looks at
    ``account.move.line``, a model that does not exist yet in this repo's
    scope (it lands together with ``account``/``journal`` in a later
    unit), so the guard is a no-op until then: with no journal items
    possible, deactivating any currency succeeds.
    """

    _inherit = "res.currency"

    def write(self, vals):
        if "active" in vals and not vals["active"]:
            for record in self:
                if record._has_accounting_entries():
                    raise UserError(
                        record.env._(
                            """
Context: Deactivate currency
Database ID: %(database_id)s
Problem: This currency has already been used to make accounting entries
Solution: Keep the currency active, or first remove/reassign the
    journal items using it
""",
                            database_id=record.id,
                        )
                    )
        return super().write(vals)

    def _has_accounting_entries(self):
        """Whether this currency has been used to generate move lines.

        :return: ``True`` iff this currency was used, either as the
            foreign currency or as the company currency, on at least one
            ``account.move.line``. Always ``False`` while
            ``account.move.line`` does not exist yet.
        """
        self.ensure_one()
        if "account.move.line" not in self.env:
            return False
        return bool(
            self.env["account.move.line"]
            .sudo()
            .search_count(
                [
                    "|",
                    ("currency_id", "=", self.id),
                    ("company_currency_id", "=", self.id),
                ]
            )
        )
