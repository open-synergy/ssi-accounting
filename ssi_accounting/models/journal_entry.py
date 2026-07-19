# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from contextlib import contextmanager

from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class JournalEntry(models.Model):
    """A pure double-entry journal entry: header + balanced debit/credit lines.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_move.py`` (class ``AccountMove``, model
    ``account.move``), because that model lives inside the ``account``
    module, which ``ssi_accounting`` must not depend on. Renamed to
    ``journal_entry`` per this issue's Keputusan Desain -- unlike
    ``account.journal``/``account.account`` (kept dotted so already-shipped
    forward-reference guards elsewhere in this module start working
    unmodified), this model has no such guard depending on the dotted
    name, so it is free to follow the plain-underscore SSI convention.

    **Everything invoice/bill/refund/payment/bank-statement/POS shaped is
    dropped** (see the issue's Keputusan Desain for the itemised list):
    ``amount_untaxed``/``amount_tax``/``amount_total``/``amount_residual``
    and their ``*_signed`` variants, ``tax_totals``, ``direction_sign``,
    ``payment_state``, ``amount_total_words``, ``auto_post`` and the whole
    recurring-entry chain, hash chain, ``checked``, ``is_storno``,
    ``commercial_partner_id``, and every field prefixed ``invoice_``/
    ``statement_``/``payment_``/``tax_cash_basis_``/``adjusting_entry_``.
    In their place, ``amount_total_debit``/``amount_total_credit`` are the
    only aggregate fields kept -- for a pure journal entry, total debit and
    total credit are the only aggregates that mean anything.

    **``move_type`` is dropped.** ``is_entry()``/``is_invoice()`` are kept
    as shims (returning ``True``/``False`` respectively) purely so code
    ported from upstream elsewhere in this repo (e.g.
    ``journal_entry.item._compute_currency_rate``) compiles/works
    unmodified. **These shims must be deleted, along with their now-dead
    branches, once the tax synchronisation unit lands** -- see the issue's
    Keputusan Desain.

    **Posting/numbering/state machine are a separate, later unit.** This
    unit keeps every entry in ``draft`` (no ``action_post``/
    ``button_draft``/``button_cancel``) and ``name`` is filled in manually,
    defaulting to ``'/'``.

    **``_check_balanced``/``_get_unbalanced_moves`` are ported verbatim**
    (including their raw SQL), with only the table names adapted
    (``account_move``/``account_move_line`` -> ``journal_entry``/
    ``journal_entry_item``) -- see their own docstrings for why they read
    the database directly instead of going through the ORM cache.

    **``_sync_dynamic_lines`` is ported as a context-manager scaffold**,
    its stack trimmed to a single stage, ``_sync_unbalanced_lines``
    (upstream additionally resyncs rounding/cash-rounding/payment-term/tax
    lines here -- all out of this issue's scope). ``_disable_recursion`` is
    kept from upstream to break the recursive ``write()`` call
    ``_sync_unbalanced_lines`` makes when it adjusts the auto-balancing
    line.
    """

    _name = "journal_entry"
    _description = "Journal Entry"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, name desc, id desc"
    _check_company_auto = True

    name = fields.Char(
        default="/",
        copy=False,
        index="trigram",
        tracking=True,
        help="Reference/number of this journal entry. Stays '/' until a "
        "later unit adds automatic numbering -- fill it in manually "
        "until then.",
    )
    ref = fields.Char(
        string="Reference",
        tracking=True,
        help="Free-form external reference for this journal entry.",
    )
    date = fields.Date(
        required=True,
        index=True,
        default=fields.Date.context_today,
        tracking=True,
        help="Accounting date of this journal entry.",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("posted", "Posted"),
            ("cancel", "Cancelled"),
        ],
        required=True,
        readonly=True,
        copy=False,
        default="draft",
        tracking=True,
        help="Status of this journal entry. Posting/cancelling is added "
        "by a later unit -- every entry stays 'draft' for now.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        required=True,
        index=True,
        check_company=True,
        help="Journal this entry is posted to.",
    )
    journal_group_id = fields.Many2one(
        comodel_name="account.journal.group",
        compute="_compute_journal_group_id",
        store=True,
        help="Technical field: first journal group the journal belongs "
        "to, used to filter journal entry lists by group.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        related="journal_id.company_id",
        store=True,
        index=True,
        help="Company this journal entry belongs to, inherited from its journal.",
    )
    company_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Company Currency",
        related="company_id.currency_id",
        help="Currency of the company this journal entry belongs to.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        required=True,
        precompute=True,
        help="Currency used to encode amounts on this journal entry's lines.",
    )
    line_ids = fields.One2many(
        comodel_name="journal_entry.item",
        inverse_name="move_id",
        copy=True,
        help="Lines (journal items) of this journal entry.",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        help="Optional partner this journal entry is related to.",
    )
    narration = fields.Html(
        string="Terms and Conditions",
        help="Free-form note attached to this journal entry.",
    )
    posted_before = fields.Boolean(
        copy=False,
        help="Technical field: whether this journal entry has ever been "
        "posted. Always false until posting is added by a later unit.",
    )
    suitable_journal_ids = fields.Many2many(
        comodel_name="account.journal",
        compute="_compute_suitable_journal_ids",
        help="Technical field: journals selectable on 'journal_id', "
        "restricted to this entry's company.",
    )
    tax_calculation_rounding_method = fields.Selection(
        related="company_id.tax_calculation_rounding_method",
        help="Technical field: the company's tax rounding method.",
    )
    company_price_include = fields.Selection(
        related="company_id.account_price_include",
        help="Technical field: the company's default on whether prices include taxes.",
    )
    amount_total_debit = fields.Monetary(
        compute="_compute_amount_total",
        store=True,
        currency_field="company_currency_id",
        help="Sum of the debit of every line, in the company currency.",
    )
    amount_total_credit = fields.Monetary(
        compute="_compute_amount_total",
        store=True,
        currency_field="company_currency_id",
        help="Sum of the credit of every line, in the company currency.",
    )

    @api.depends("name")
    def _compute_display_name(self):
        for move in self:
            if move.name and move.name != "/":
                move.display_name = move.name
            else:
                move.display_name = f"*{move.id}"

    @api.depends("journal_id", "journal_id.journal_group_ids")
    def _compute_journal_group_id(self):
        for move in self:
            move.journal_group_id = move.journal_id.journal_group_ids[:1]

    @api.depends("journal_id.currency_id", "journal_id.company_id.currency_id")
    def _compute_currency_id(self):
        """Default 'currency_id' from the journal, then the company.

        Reads through 'journal_id' directly (not the stored, related
        'company_id') and falls back to 'self.env.company' -- a plain,
        always-synchronously-resolvable field -- as the last resort, so
        this required field never precomputes to an empty value while
        'journal_id'/'company_id' are still settling during create().
        """
        for move in self:
            move.currency_id = (
                move.journal_id.currency_id
                or move.journal_id.company_id.currency_id
                or move.currency_id
                or self.env.company.currency_id
            )

    @api.depends("company_id")
    def _compute_suitable_journal_ids(self):
        for move in self:
            move.suitable_journal_ids = self.env["account.journal"].search(
                [("company_id", "=", move.company_id.id or self.env.company.id)]
            )

    @api.depends("line_ids.debit", "line_ids.credit")
    def _compute_amount_total(self):
        for move in self:
            move.amount_total_debit = sum(move.line_ids.mapped("debit"))
            move.amount_total_credit = sum(move.line_ids.mapped("credit"))

    def is_entry(self):
        """Shim: every journal entry in this repo is a plain entry.

        Kept only so code ported (behaviour-wise) from upstream elsewhere
        in this repo keeps compiling/working unmodified -- see this
        class's docstring. **Must be removed**, along with its now-dead
        callers' branches, once the tax synchronisation unit lands.
        """
        return True

    def is_invoice(self, include_receipts=False):
        """Shim: this repo has no invoice documents. Always ``False``.

        See ``is_entry()``'s docstring -- same removal obligation.
        """
        return False

    @api.model
    def _disable_recursion(self, container, method_name, default=None, target=True):
        """Guard against a method recursively triggering itself.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._disable_recursion``. Used here so
        ``_sync_unbalanced_lines``'s own ``write()`` call (to add/update/
        remove the auto-balancing line) does not re-enter
        ``_sync_dynamic_lines`` and loop forever.

        :param container: mutable dict holding a ``'records'`` key,
            updated in place to the context-flagged recordset.
        :param method_name: name of the method being guarded.
        :param default: value of the flag when not yet set.
        :param target: value meaning "currently running".
        :return: ``True`` if the caller should skip its guarded body
            (already running higher up the stack), ``False`` otherwise.
        """
        recursion_flag = f"skip_{method_name}"
        current = self.env.context.get(recursion_flag, default)
        if current == target:
            return True
        flagged = self.with_context(**{recursion_flag: target})
        container["records"] = flagged
        return False

    @contextmanager
    def _sync_dynamic_lines(self, container):
        """Auto-balance ``container['records']``'s lines around the wrapped block.

        Ported (framework-wise) from Odoo core
        ``AccountMove._sync_dynamic_lines``, its stack trimmed to a single
        stage: ``_sync_unbalanced_lines``. Upstream additionally
        (re)computes rounding/cash-rounding/payment-term/tax lines here --
        all out of this issue's scope, see the class docstring.

        :param container: dict with a ``'records'`` key, read again after
            the wrapped block runs (``create()`` only knows the final
            recordset once ``super().create()`` has returned).
        """

        def get_lines_data():
            return {
                line: (
                    line.account_id,
                    line.currency_id,
                    line.amount_currency,
                    line.balance,
                )
                for line in container["records"].line_ids
            }

        before = get_lines_data()
        yield
        after = get_lines_data()

        changed = self.env["journal_entry.item"]
        for line, data in after.items():
            if data != before.get(line):
                changed += line
        if not changed:
            return

        for move in changed.move_id:
            move._sync_unbalanced_lines(
                changed.filtered(lambda line, move=move: line.move_id == move)
            )

    def _sync_unbalanced_lines(self, lines):
        """Keep ``self`` balanced by adjusting a single auto-balancing line.

        Framework scaffold only (see the class docstring): this issue
        keeps ``_sync_dynamic_lines``'s stack to this single stage.
        Targets the journal's ``suspense_account_id`` -- when unset,
        this is a no-op and unbalanced entries are simply rejected by
        ``_check_balanced``.

        :param lines: the lines of ``self`` that changed and triggered
            this sync (unused here -- the whole balance is recomputed
            from ``self.line_ids``, matching upstream's own approach for
            this kind of whole-document rebalancing).
        """
        self.ensure_one()
        if not self.journal_id.suspense_account_id:
            return
        auto_balance_name = self.env._("Automatic Balancing Line")
        suspense_line = self.line_ids.filtered(
            lambda line: (
                line.name == auto_balance_name
                and line.account_id == self.journal_id.suspense_account_id
            )
        )
        balance = sum(self.line_ids.mapped("balance")) - sum(
            suspense_line.mapped("balance")
        )
        if self.company_currency_id.is_zero(balance):
            if suspense_line:
                self.line_ids = [
                    Command.delete(line_id) for line_id in suspense_line.ids
                ]
            return
        values = {
            "name": auto_balance_name,
            "account_id": self.journal_id.suspense_account_id.id,
            "display_type": "product",
            "debit": -balance if balance < 0.0 else 0.0,
            "credit": balance if balance > 0.0 else 0.0,
        }
        if suspense_line:
            self.line_ids = [Command.update(suspense_line.id, values)]
        else:
            self.line_ids = [Command.create(values)]

    @api.model
    def _get_unbalanced_moves(self, container):
        """Which of ``container['records']`` are not debit/credit balanced.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._get_unbalanced_moves``, raw SQL included, with
        only the table names changed (``account_move``/
        ``account_move_line`` -> ``journal_entry``/``journal_entry_item``).

        Deliberately reads ``line.balance`` through raw SQL against the
        database rather than the ORM cache: this runs from
        ``_check_balanced``, itself triggered by a constrain that can
        fire while lines are still settling from
        ``_sync_dynamic_lines``/``_sync_unbalanced_lines`` -- going
        through the ORM here risks reading a stale/partially recomputed
        value. Flushing first and querying the database directly is the
        only way to be sure the balance seen here matches what is
        actually stored.

        :param container: a dict with a ``'records'`` key holding the
            ``journal_entry`` recordset to check.
        :return: a list of ``(move_id, unbalanced_amount)`` tuples for
            the entries that are not balanced.
        """
        moves = container["records"].filtered(lambda move: move.line_ids)
        if not moves:
            return []

        moves.flush_recordset(["name"])
        self.env["journal_entry.item"].flush_model(
            ["debit", "credit", "balance", "move_id"]
        )
        self.env["journal_entry"].flush_model(["company_id"])
        self.env["res.company"].flush_model(["currency_id"])
        self.env["res.currency"].flush_model(["decimal_places"])
        self.env.cr.execute(
            """
            SELECT line.move_id,
                   ROUND(SUM(line.balance), currency.decimal_places)
              FROM journal_entry_item line
              JOIN journal_entry move ON move.id = line.move_id
              JOIN res_company company ON company.id = move.company_id
              JOIN res_currency currency ON currency.id = company.currency_id
             WHERE line.move_id IN %(move_ids)s
          GROUP BY line.move_id, currency.decimal_places
            HAVING ROUND(SUM(line.balance), currency.decimal_places) != 0.0
            """,
            {"move_ids": tuple(moves.ids)},
        )
        return self.env.cr.fetchall()

    def _check_balanced(self, container):
        """Raise if any of ``container['records']`` is not balanced.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._check_balanced``.

        :param container: a dict with a ``'records'`` key holding the
            ``journal_entry`` recordset to check.
        """
        unbalanced_moves = self._get_unbalanced_moves(container)
        if not unbalanced_moves:
            return
        raise UserError(
            self.env._(
                """
Context: Save journal entry
Database ID: %(database_id)s
Problem: The following journal entries are not balanced (sum of debit
    minus credit is not zero):
%(lines)s
Solution: Adjust the lines' debit/credit so each entry balances to zero,
    or set a suspense account on the journal to auto-balance drafts
""",
                database_id=",".join(
                    str(move_id) for move_id, _delta in unbalanced_moves
                ),
                lines="\n".join(
                    self.env._(
                        "- %(move)s: unbalanced by %(delta)s",
                        move=self.browse(move_id).display_name,
                        delta=delta,
                    )
                    for move_id, delta in unbalanced_moves
                ),
            )
        )

    @api.constrains("line_ids")
    def _check_balanced_constrains(self):
        """Validate balance when lines are added/removed/reassigned.

        ``@api.constrains`` does not accept dotted paths through a
        relation (``"line_ids.debit"`` logs a warning and is ignored at
        registry-build time) -- only bare field names of this model. So
        this only reliably fires when ``line_ids`` itself is touched from
        the header's side (e.g. every ``create()`` with inline lines).
        ``journal_entry.item._check_balanced_constrains`` below covers
        the complementary case of a line's own ``debit``/``credit`` being
        written directly -- same split as
        ``tax.repartition_line._check_repartition_line_factor``'s
        docstring already explains for a different model.
        """
        self._check_balanced({"records": self})

    @api.model_create_multi
    def create(self, vals_list):
        container = {"records": self.env["journal_entry"]}
        with self._sync_dynamic_lines(container):
            moves = super().create(vals_list)
            container["records"] = moves
        return moves

    def write(self, vals):
        container = {"records": self}
        if self._disable_recursion(container, "journal_entry_sync_dynamic_lines"):
            return super().write(vals)
        flagged = container["records"]
        with flagged._sync_dynamic_lines(container):
            result = super(JournalEntry, flagged).write(vals)
        return result
