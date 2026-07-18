# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class TestMixinSequenceNumber(models.Model):
    """Concrete fixture model that inherits ``mixin.sequence_number`` so
    the abstract mixin can be exercised by the in-module test suite. It is
    intentionally kept minimal (no views, no menu, no state machine) to
    avoid adding any production dependency or UI to ``ssi_accounting`` —
    see the backlog item that introduced ``mixin.sequence_number`` for the
    rationale ("wiring the mixin into a concrete model is out of scope").

    ``_get_starting_sequence`` and ``_get_last_sequence_domain`` are
    overridden here only because ``mixin.sequence_number`` documents both
    methods as meant to be overridden by every concrete model — without
    an override, ``_get_last_sequence_domain`` returns an unrestricted
    domain and the reset-per-year behaviour under test could never
    trigger.
    """

    _name = "test.mixin_sequence_number"
    _description = "Test Mixin Sequence Number"
    _inherit = [
        "mixin.sequence_number",
    ]

    name = fields.Char(
        string="# Document",
        default="/",
        required=True,
        copy=False,
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
    )

    def _get_starting_sequence(self):
        # EXTENDS mixin.sequence_number: embed the document's own
        # accounting year so `_deduce_sequence_number_reset` detects a
        # yearly reset from the very first record.
        self.ensure_one()
        return "TEST/%04d/00000" % (self.date.year,)

    def _get_last_sequence_domain(self, relaxed=False):
        # EXTENDS mixin.sequence_number: scope the search for the
        # previous document number to the date range implied by the
        # reset periodicity of the closest reference document, so that a
        # document dated in a new year starts a fresh sequence instead of
        # continuing the previous year's numbering. Adapted (simplified,
        # no journal/refund/self-billing concepts) from
        # `account.move._get_last_sequence_domain`.
        self.ensure_one()
        if not self.date:
            return "WHERE FALSE", {}
        where_string = "WHERE name != '/'"
        param = {}
        if not relaxed:
            domain = [
                ("id", "!=", self.id or self._origin.id),
                ("name", "not in", ("/", "", False)),
            ]
            reference_name = (
                self.sudo()
                .search(domain + [("date", "<=", self.date)], order="date desc", limit=1)
                .name
            )
            if not reference_name:
                reference_name = (
                    self.sudo().search(domain, order="date asc", limit=1).name
                )
            sequence_number_reset = self._deduce_sequence_number_reset(reference_name)
            date_start, date_end, *__ = self._get_sequence_date_range(
                sequence_number_reset
            )
            where_string += " AND date BETWEEN %(date_start)s AND %(date_end)s"
            param["date_start"] = date_start
            param["date_end"] = date_end
        return where_string, param

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.name == "/":
                record._set_next_sequence()
        return records
