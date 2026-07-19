# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import Command, api, fields, models


class ReconcileFull(models.Model):
    """The "full match" group a set of ``reconcile_partial`` rows collapses into.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_full_reconcile.py`` (class
    ``AccountFullReconcile``, model ``account.full.reconcile``), because
    that model lives inside the ``account`` module, which
    ``ssi_accounting`` must not depend on. Renamed to ``reconcile_full``
    per this issue's Keputusan Desain.

    A full reconcile only ever exists once every journal item it links
    (``reconciled_line_ids``) has zero residual left -- see
    ``journal_entry.item._reconcile_plan_with_sync``, the only writer of
    this model. Its own ``id`` becomes the non-``P``-prefixed matching
    number every one of those lines is stamped with (see
    ``reconcile_partial._update_matching_number``).

    **``create()``/``unlink()`` are ported apa adanya**, raw SQL bulk
    update included, per this issue's Keputusan Desain -- table names
    adapted (``account_move_line`` -> ``journal_entry_item``), model
    names adapted (``account.partial.reconcile`` -> ``reconcile_partial``,
    ``account.move.line`` -> ``journal_entry.item``). The comment on
    ``unlink()`` documents a real upstream bug fix (a plain
    ``ondelete='set null'`` nulls ``full_reconcile_id`` but leaves the
    now-stale ``matching_number`` behind) -- kept verbatim, not
    simplified, exactly as this issue instructs.
    """

    _name = "reconcile_full"
    _description = "Full Reconcile"

    partial_reconcile_ids = fields.One2many(
        comodel_name="reconcile_partial",
        inverse_name="full_reconcile_id",
        string="Reconciliation Parts",
        help="'reconcile_partial' rows collapsed into this full match.",
    )
    reconciled_line_ids = fields.One2many(
        comodel_name="journal_entry.item",
        inverse_name="full_reconcile_id",
        string="Matched Journal Items",
        help="Journal items fully matched by this full reconcile -- each "
        "has zero residual left.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Create, then bulk-stamp 'full_reconcile_id' via raw SQL.

        Ported verbatim (behaviour-wise) from upstream
        ``AccountFullReconcile.create``: the O2M fields are supplied as
        plain ``Command.LINK``/``Command.SET`` lists by
        ``journal_entry.item._reconcile_plan_with_sync`` (never
        ``Command.CREATE``), so decoding them into flat id lists and
        writing the inverse FK with a single bulk ``UPDATE`` is far
        cheaper than letting the ORM apply each command one at a time.
        """

        def get_ids(commands):
            for command in commands:
                if command[0] == Command.LINK:
                    yield command[1]
                elif command[0] == Command.SET:
                    yield from command[2]
                else:
                    raise ValueError(f"Unexpected command: {command}")

        move_line_ids = [
            list(get_ids(vals.pop("reconciled_line_ids"))) for vals in vals_list
        ]
        partial_ids = [
            list(get_ids(vals.pop("partial_reconcile_ids"))) for vals in vals_list
        ]
        fulls = super(ReconcileFull, self.with_context(tracking_disable=True)).create(
            vals_list
        )

        self.env.cr.execute_values(
            """
            UPDATE journal_entry_item line
               SET full_reconcile_id = source.full_id
              FROM (VALUES %s) AS source(full_id, line_ids)
             WHERE line.id = ANY(source.line_ids)
            """,
            [
                (full.id, line_ids)
                for full, line_ids in zip(fulls, move_line_ids, strict=False)
            ],
            page_size=1000,
        )
        fulls.reconciled_line_ids.invalidate_recordset(
            ["full_reconcile_id"], flush=False
        )
        fulls.invalidate_recordset(["reconciled_line_ids"], flush=False)

        self.env.cr.execute_values(
            """
            UPDATE reconcile_partial partial
               SET full_reconcile_id = source.full_id
              FROM (VALUES %s) AS source(full_id, partial_ids)
             WHERE partial.id = ANY(source.partial_ids)
            """,
            [
                (full.id, line_ids)
                for full, line_ids in zip(fulls, partial_ids, strict=False)
            ],
            page_size=1000,
        )
        fulls.partial_reconcile_ids.invalidate_recordset(
            ["full_reconcile_id"], flush=False
        )
        fulls.invalidate_recordset(["partial_reconcile_ids"], flush=False)

        self.env["reconcile_partial"]._update_matching_number(fulls.reconciled_line_ids)
        return fulls

    def unlink(self):
        """Unlink, then repair the now-stale 'matching_number' of unlinked lines.

        The default ``ondelete='set null'`` on
        ``journal_entry.item.full_reconcile_id`` nulls the FK in
        PostgreSQL when the full reconcile is removed, but
        ``journal_entry.item.matching_number`` is a plain Char that
        nobody recomputes. Mirror the contract of ``create()`` (see the
        ``_update_matching_number`` call right after the UPDATE above) on
        the unlink path so each previously-linked line ends up with the
        correct value (``False``, or ``'P<n>'`` when partial reconciles
        survive as zombies).
        """
        amls = self.reconciled_line_ids
        res = super().unlink()
        amls = amls.exists()
        if amls:
            self.env["reconcile_partial"]._update_matching_number(amls)
        return res
