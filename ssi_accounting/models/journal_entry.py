# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import calendar
from contextlib import contextmanager
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils


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

    **Posting/numbering/state machine land in this issue.**
    ``journal_entry`` inherits ``mixin.sequence_number``
    (``_sequence_field = "name"``, ``_sequence_date_field = "date"``,
    ``_sequence_index = "journal_id"`` -- numbering resets per journal,
    not globally, see ``models/mixin_sequence_number.py``). ``name`` is
    assigned only on posting: ``_compute_name`` only calls
    ``_set_next_sequence()`` once ``state != "draft"``, so a draft entry
    stays empty (``name_placeholder`` previews what it will get).
    ``action_post``/``_post`` keep the checks upstream's own ``_post``
    performs on state/lines/journal/currency/account archival and
    cross-company accounts, plus the two lock dates this repo exposes
    (``fiscalyear_lock_date``/``tax_lock_date`` -- see
    ``res_company.py``); a locked date is pushed forward rather than
    rejected, exactly like upstream. ``soft``/``auto_post`` and the
    whole recurring-entry chain are gone entirely, so **a future-dated
    entry posts immediately instead of being queued** -- unlike
    upstream Odoo, see the README. The access check upstream performs
    against ``account.group_account_invoice`` becomes
    ``ssi_accounting.group_accounting_user`` here. ``button_draft``
    keeps the posted/cancelled-only guard and a reconciliation guard
    (``has_reconciled_entries``, forward-declared the same defensive
    way as ``account.py``'s ``_toggle_reconcile_to_true`` -- always
    ``False`` until a later reconciliation unit adds the fields it
    reads); ``name`` is deliberately **not** cleared by it, and
    ``posted_before`` stays ``True``. ``button_cancel`` writes straight
    to ``cancel`` from either ``draft`` or ``posted`` (tracked
    automatically via ``state``'s own ``tracking=True``). The
    exchange-difference posting cascade upstream's ``_post`` triggers
    on ``to_post`` is out of scope here -- a later multi-currency
    unit's job -- so ``made_sequence_gap`` is declared but not
    maintained by any cascade in this unit.

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
    _inherit = ["mail.thread", "mail.activity.mixin", "mixin.sequence_number"]
    _order = "date desc, name desc, id desc"
    _check_company_auto = True

    _sequence_field = "name"
    _sequence_date_field = "date"
    _sequence_index = "journal_id"

    _unique_name = models.UniqueIndex(
        "(name, journal_id) WHERE (state = 'posted' AND name != '/')",
        "Another entry with the same name already exists.",
    )

    name = fields.Char(
        compute="_compute_name",
        inverse="_inverse_name",
        readonly=False,
        store=True,
        copy=False,
        index="trigram",
        tracking=True,
        help="Statutory number of this journal entry, assigned "
        "automatically on posting -- gapless per journal, resetting "
        "yearly (see 'mixin.sequence_number'). Stays empty while "
        "'draft'; 'name_placeholder' previews the number it will get.",
    )
    name_placeholder = fields.Char(
        compute="_compute_name_placeholder",
        help="Preview of the number this entry will get once posted, "
        "shown in the form while 'name' itself is still empty.",
    )
    highest_name = fields.Char(
        compute="_compute_highest_name",
        help="Technical field: the last sequence number issued so far "
        "in this entry's journal, used to compute 'name_placeholder' "
        "and to adjust the accounting date on posting.",
    )
    made_sequence_gap = fields.Boolean(
        copy=False,
        help="Technical field: whether this entry breaks the "
        "numbering chain of its journal. Declared for forward "
        "compatibility but not maintained by any cascade in this unit "
        "-- see the class docstring.",
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
        help="Status of this journal entry: 'draft' can still be "
        "edited freely, 'posted' is final and numbered, 'cancel' is "
        "void.",
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
        help="Technical field: whether this journal entry has ever "
        "been posted. Stays true after a 'button_draft' reset.",
    )
    show_reset_to_draft_button = fields.Boolean(
        compute="_compute_show_reset_to_draft_button",
        help="Technical field: whether the 'Reset to Draft' button "
        "should be shown -- true for posted or cancelled entries.",
    )
    has_reconciled_entries = fields.Boolean(
        compute="_compute_has_reconciled_entries",
        help="Technical field: whether this entry has reconciled "
        "journal items, used to guard 'button_draft'. Always false "
        "until a later reconciliation unit adds the fields it reads.",
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

    # ======================================================================
    # SEQUENCE MIXIN
    # ======================================================================

    @property
    def _sequence_monthly_regex(self):
        # EXTENDS mixin.sequence_number
        return (
            self.journal_id.sequence_override_regex or super()._sequence_monthly_regex
        )

    @property
    def _sequence_yearly_regex(self):
        # EXTENDS mixin.sequence_number
        return self.journal_id.sequence_override_regex or super()._sequence_yearly_regex

    @property
    def _sequence_year_range_regex(self):
        # EXTENDS mixin.sequence_number
        return (
            self.journal_id.sequence_override_regex
            or super()._sequence_year_range_regex
        )

    @property
    def _sequence_fixed_regex(self):
        # EXTENDS mixin.sequence_number
        return self.journal_id.sequence_override_regex or super()._sequence_fixed_regex

    @property
    def _sequence_year_range_monthly_regex(self):
        # EXTENDS mixin.sequence_number
        return (
            self.journal_id.sequence_override_regex
            or super()._sequence_year_range_monthly_regex
        )

    def _must_check_constrains_date_sequence(self):
        # OVERRIDES mixin.sequence_number: only a posted entry's 'name'
        # must line up with its 'date' -- a draft kept its old number
        # after a 'button_draft' reset and may still have its date
        # edited before being posted again.
        return self.state == "posted"

    def _get_last_sequence_domain(self, relaxed=False):
        # EXTENDS mixin.sequence_number: scope the search for the
        # previous entry number to this entry's own journal (matching
        # '_sequence_index'), and to the date range implied by the
        # reset periodicity of the closest reference entry. Adapted
        # (behaviour-wise, simplified: no move_type/refund/payment/
        # self-billing concepts, none of which exist in this repo)
        # from upstream ``account.move._get_last_sequence_domain``.
        self.ensure_one()
        if not self.date or not self.journal_id:
            return "WHERE FALSE", {}
        where_string = "WHERE journal_id = %(journal_id)s AND name != '/'"
        param = {"journal_id": self.journal_id.id}
        if not relaxed:
            domain = [
                ("journal_id", "=", self.journal_id.id),
                ("id", "!=", self.id or self._origin.id),
                ("name", "not in", ("/", "", False)),
            ]
            reference_name = (
                self.sudo()
                .search(
                    domain + [("date", "<=", self.date)],
                    order="date desc",
                    limit=1,
                )
                .name
            )
            if not reference_name:
                reference_name = (
                    self.sudo().search(domain, order="date asc", limit=1).name
                )
            sequence_number_reset = self._deduce_sequence_number_reset(reference_name)
            date_start, date_end, *_unused = self._get_sequence_date_range(
                sequence_number_reset
            )
            where_string += " AND date BETWEEN %(date_start)s AND %(date_end)s"
            param["date_start"] = date_start
            param["date_end"] = date_end

            # Exclude sequence formats the regex would otherwise also
            # catch (e.g. a monthly-formatted number when we are
            # actually scanning for a yearly one) -- see upstream's own
            # comment on ``account.move._get_last_sequence_domain``.
            if sequence_number_reset in ("year", "year_range"):
                param["anti_regex"] = (
                    self._make_regex_non_capturing(
                        self._sequence_monthly_regex.split("(?P<seq>")[0]
                    )
                    + "$"
                )
            elif sequence_number_reset == "never":
                param["anti_regex"] = (
                    self._make_regex_non_capturing(
                        self._sequence_yearly_regex.split("(?P<seq>")[0]
                    )
                    + "$"
                )
            if param.get("anti_regex") and not self.journal_id.sequence_override_regex:
                where_string += " AND sequence_prefix !~ %(anti_regex)s "
        return where_string, param

    def _get_starting_sequence(self):
        # EXTENDS mixin.sequence_number: '<journal code>/<year>/00000',
        # resetting yearly -- upstream's own equivalent of its "annual"
        # branch (sale/bank/cash/credit journal types), applied to
        # every journal here since this repo's 'account.journal.type'
        # has no such split (see 'journal.py'). Staggered fiscal years
        # (company 'fiscalyear_last_day'/'fiscalyear_last_month' not
        # 31 Dec) shrink the running number to 4 digits and the year
        # segment to a "YY-YY" range, exactly like upstream.
        self.ensure_one()
        entry_date = self.date or fields.Date.context_today(self)
        year_part = f"{entry_date.year:04d}"
        last_day = int(self.company_id.fiscalyear_last_day)
        last_month = int(self.company_id.fiscalyear_last_month)
        is_staggered_year = last_month != 12 or last_day != 31
        if is_staggered_year:
            max_last_day = calendar.monthrange(entry_date.year, last_month)[1]
            last_day = min(last_day, max_last_day)
            if entry_date > date(entry_date.year, last_month, last_day):
                year_part = f"{entry_date:%y}-{entry_date + relativedelta(years=1):%y}"
            else:
                year_part = f"{entry_date + relativedelta(years=-1):%y}-{entry_date:%y}"
        seq_placeholder = "0000" if is_staggered_year else "00000"
        return f"{self.journal_id.code}/{year_part}/{seq_placeholder}"

    def _affect_tax_report(self):
        self.ensure_one()
        return any(line._affect_tax_report() for line in self.line_ids)

    def _get_violated_lock_dates(self, entry_date, has_tax):
        self.ensure_one()
        return self.company_id._get_lock_date_violations(entry_date, tax=has_tax)

    def _get_accounting_date(self, entry_date, has_tax, lock_dates=None):
        # Adapted (behaviour-wise, simplified: no invoice/sale-document
        # concepts) from upstream ``account.move._get_accounting_date``.
        self.ensure_one()
        lock_dates = lock_dates or self._get_violated_lock_dates(entry_date, has_tax)
        today = fields.Date.context_today(self)
        highest_name = self.highest_name or self._get_last_sequence(relaxed=True)
        number_reset = self._deduce_sequence_number_reset(highest_name)
        if lock_dates:
            entry_date = lock_dates[-1][0] + timedelta(days=1)
        if not highest_name or number_reset in ("month", "year_range_month"):
            if (today.year, today.month) > (entry_date.year, entry_date.month):
                return date_utils.get_month(entry_date)[1]
            return max(entry_date, today)
        if number_reset == "year":
            if today.year > entry_date.year:
                return date(entry_date.year, 12, 31)
            return max(entry_date, today)
        return entry_date

    @api.depends("posted_before", "state", "journal_id", "date")
    def _compute_name(self):
        # EXTENDS mixin.sequence_number: assign the number only once
        # posted -- see the class docstring. Every branch below assigns
        # 'name' explicitly (even when that means self-reading and
        # re-writing its current value) -- same self-referential no-op
        # pattern as 'journal_entry.item._compute_balance'. A record
        # left without an explicit assignment inside this loop is not
        # safe to rely on keeping its previous value once 'state' (an
        # '@api.depends' trigger here) is written, e.g. by
        # 'button_draft' -- it is not preserved.
        self = self.sorted(lambda move: (move.date, move._origin.id))
        for move in self:
            if move.state == "cancel":
                move.name = move.name
                continue
            move_has_name = move.name and move.name != "/"
            if not move.posted_before and not move._sequence_matches_date():
                move.name = False
                continue
            if move.date and not move_has_name and move.state != "draft":
                move._set_next_sequence()
            else:
                move.name = move.name
        self._inverse_name()

    def _inverse_name(self):
        """No-op inverse, only present so 'name' stays directly writable.

        Upstream's own inverse additionally triggers
        ``_update_sequence_made_gap()`` -- out of scope here, see
        'made_sequence_gap' field's own help text and the class docstring.
        """

    @api.depends(
        "date",
        "journal_id",
        "name",
        "posted_before",
        "sequence_number",
        "sequence_prefix",
        "state",
    )
    def _compute_name_placeholder(self):
        for move in self:
            if (
                (not move.name or move.name == "/")
                and move.date
                and not move._get_last_sequence()
            ):
                (
                    sequence_format_string,
                    sequence_format_values,
                ) = move._get_next_sequence_format()
                sequence_format_values["seq"] += 1
                move.name_placeholder = sequence_format_string.format(
                    **sequence_format_values
                )
            else:
                move.name_placeholder = False

    @api.depends("journal_id", "date")
    def _compute_highest_name(self):
        for move in self:
            move.highest_name = move._get_last_sequence()

    @api.depends("state")
    def _compute_show_reset_to_draft_button(self):
        for move in self:
            move.show_reset_to_draft_button = move.state in ("posted", "cancel")

    @api.depends("line_ids")
    def _compute_has_reconciled_entries(self):
        """Always false until a later reconciliation unit lands.

        Guarded the same defensive way as ``account.py``'s
        ``_toggle_reconcile_to_true``/``_toggle_reconcile_to_false``:
        reads the field only if it exists, so this starts working the
        moment a later unit adds 'reconciled' to
        'journal_entry.item' -- no change needed here at that point.
        """
        if "reconciled" not in self.env["journal_entry.item"]._fields:
            for move in self:
                move.has_reconciled_entries = False
            return
        for move in self:
            move.has_reconciled_entries = len(move.line_ids._reconciled_lines()) > 1

    # ======================================================================
    # STATE MACHINE
    # ======================================================================

    def action_post(self):
        if self:
            self._post()
        return False

    def _post(self):
        """Post 'self': give each entry its statutory number and lock it in.

        Trimmed (behaviour-wise) from Odoo core ``AccountMove._post`` --
        see the class docstring for the itemised list of checks kept
        and dropped, and the issue's Keputusan Desain for the full
        rationale. ``soft``/``auto_post`` are gone entirely.
        """
        if not self.env.su and not self.env.user.has_group(
            "ssi_accounting.group_accounting_user"
        ):
            raise AccessError(
                self.env._("You don't have the access rights to post a journal entry.")
            )

        problems = []
        for move in self:
            if move.state in ("posted", "cancel"):
                problems.append(
                    self.env._(
                        "%(name)s (id %(id)s) must be in draft",
                        name=move.display_name,
                        id=move.id,
                    )
                )
            if not move.line_ids.filtered(
                lambda line: line.display_type not in ("line_section", "line_note")
            ):
                problems.append(
                    self.env._("%(name)s has no postable line", name=move.display_name)
                )
            if not move.journal_id.active:
                problems.append(
                    self.env._(
                        "%(name)s posts to the archived journal %(journal)s",
                        name=move.display_name,
                        journal=move.journal_id.display_name,
                    )
                )
            if move.currency_id and not move.currency_id.active:
                problems.append(
                    self.env._(
                        "%(name)s uses the archived currency %(currency)s",
                        name=move.display_name,
                        currency=move.currency_id.name,
                    )
                )
            archived_accounts = move.line_ids.account_id.filtered(
                lambda account: not account.active
            )
            if archived_accounts:
                problems.append(
                    self.env._(
                        "%(name)s uses the archived account(s) %(accounts)s",
                        name=move.display_name,
                        accounts=", ".join(archived_accounts.mapped("display_name")),
                    )
                )
            parent_companies = move.company_id.sudo().parent_ids
            mismatched_accounts = move.line_ids.mapped("account_id").filtered(
                lambda account, parents=parent_companies: not (
                    parents & account.sudo().company_ids
                )
            )
            if mismatched_accounts:
                problems.append(
                    self.env._(
                        "%(name)s uses account(s) from a different company: "
                        "%(accounts)s",
                        name=move.display_name,
                        accounts=", ".join(mismatched_accounts.mapped("display_name")),
                    )
                )

        if problems:
            raise UserError(
                self.env._(
                    """
Context: Post journal entry
Problem: %(problems)s
Solution: Fix the listed issue(s), then post again
""",
                    problems="\n".join(f"- {problem}" for problem in problems),
                )
            )

        for move in self:
            affects_tax_report = move._affect_tax_report()
            lock_dates = move._get_violated_lock_dates(move.date, affects_tax_report)
            if not lock_dates:
                continue
            new_date = move._get_accounting_date(
                move.date, affects_tax_report, lock_dates=lock_dates
            )
            move.message_post(
                body=self.env._(
                    """
Context: Post journal entry
Database ID: %(database_id)s
Problem: The date %(old_date)s falls within a locked period (%(lock_date_info)s)
Solution: The entry is accounted on %(new_date)s instead
""",
                    database_id=move.id,
                    old_date=move.date,
                    lock_date_info=self.env["res.company"]._format_lock_dates(
                        lock_dates
                    ),
                    new_date=new_date,
                )
            )
            move.date = new_date

        self.write({"state": "posted", "posted_before": True})
        return self

    def button_draft(self):
        """Reset 'self' to draft.

        Trimmed from Odoo core ``AccountMove.button_draft``: keeps the
        posted/cancelled-only guard and the reconciliation guard (via
        'has_reconciled_entries', see that field's own help text).
        Drops the hash/secured guard (no hash chain here) and every
        payment/bank-statement cascade (no such documents here).
        'name' is deliberately left untouched -- see its own help text.
        """
        if any(move.state not in ("cancel", "posted") for move in self):
            raise UserError(
                self.env._(
                    "Only posted or cancelled journal entries can be reset to draft."
                )
            )
        if any(move.has_reconciled_entries for move in self):
            raise UserError(
                self.env._(
                    "You cannot reset to draft a journal entry that has "
                    "reconciled entries."
                )
            )
        self.write({"state": "draft"})

    def button_cancel(self):
        """Cancel 'self', from either 'draft' or 'posted'.

        Trimmed from Odoo core ``AccountMove.button_cancel``: writes
        'state' straight to 'cancel' (tracked automatically via the
        'state' field's own 'tracking=True' -- no separate
        message_post needed). Drops 'need_cancel_request'/e-invoice
        cancellation (neither exists here) and the
        posted->draft->cancel cascade through 'button_draft' (its
        guards do not apply to a plain cancel).
        """
        if any(move.state not in ("draft", "posted") for move in self):
            raise UserError(
                self.env._("Only draft or posted journal entries can be cancelled.")
            )
        self.write({"state": "cancel"})

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
