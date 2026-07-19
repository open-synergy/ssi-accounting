# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import calendar
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils, frozendict


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

    **``is_entry()``/``is_invoice()`` are gone too.**
    Those two were kept as shims (returning ``True``/``False``
    respectively) only until the tax synchronisation unit's code ported
    from upstream (behaviour-gated on them) landed; now that it has (see
    the "TAXES COMPUTATION" methods below and this issue's Keputusan
    Desain), every branch that used to depend on them was pruned rather
    than left dead, and the shims themselves are gone -- neither name
    resolves on this model any more.

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
    automatically via ``state``'s own ``tracking=True``). **The
    exchange-difference posting cascade upstream's ``_post`` triggers on
    ``to_post`` now resolves here** -- not inside ``_post()`` itself
    (upstream's own ``to_post`` gate lives in ``account.move.line``'s
    ``_create_exchange_difference_moves``, which this repo's currency
    reconciliation unit ported onto ``journal_entry.item``): every
    exchange difference entry whose originating lines were already
    posted gets posted right there, immediately after being created.
    ``made_sequence_gap`` remains declared but not maintained by any
    cascade in this unit -- a separate, still out-of-scope concept (see
    its own field help text).

    **``_check_balanced``/``_get_unbalanced_moves`` are ported verbatim**
    (including their raw SQL), with only the table names adapted
    (``account_move``/``account_move_line`` -> ``journal_entry``/
    ``journal_entry_item``) -- see their own docstrings for why they read
    the database directly instead of going through the ORM cache.

    **``_sync_dynamic_lines`` is ported as an ``ExitStack`` scaffold**,
    trimmed to two stages -- ``_sync_balancing_lines`` (formerly the whole
    of this method, before the tax synchronisation unit added a second
    stage) and ``_sync_tax_lines`` -- upstream additionally resyncs
    rounding/cash-rounding/payment-term lines and invoice-only concerns
    here, all out of this issue's scope; see ``_sync_dynamic_lines``'s own
    docstring for why the two stages are entered in that specific order.
    ``_disable_recursion`` is kept from upstream to break the recursive
    ``write()`` call ``_sync_balancing_lines`` makes when it adjusts the
    auto-balancing line.
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
    reversed_entry_id = fields.Many2one(
        comodel_name="journal_entry",
        string="Reversal Of",
        index=True,
        copy=False,
        help="The journal entry this entry is a mirror reversal of, set "
        "by the 'journal_entry_reversal' wizard.",
    )
    reversal_move_ids = fields.One2many(
        comodel_name="journal_entry",
        inverse_name="reversed_entry_id",
        string="Reversals",
        help="Journal entries that reverse this one.",
    )
    reversal_move_count = fields.Integer(
        compute="_compute_reversal_move_count",
        help="Technical field: number of 'reversal_move_ids', shown on "
        "the smart button.",
    )
    is_refund = fields.Boolean(
        copy=False,
        help="Technical field: whether this entry's tax lines must be "
        "synced from the 'reverse' repartition lines instead of the "
        "'base' ones. Set explicitly by the 'journal_entry_reversal' "
        "wizard when it builds a mirror entry -- never derived from "
        "'state' or a document-type concept (this model has none), per "
        "this issue's Keputusan Desain. See "
        "'_prepare_product_base_line_for_taxes_computation'.",
    )
    exchange_diff_partial_ids = fields.One2many(
        comodel_name="reconcile_partial",
        inverse_name="exchange_move_id",
        string="Related Partials",
        help="Field penghubung: the 'reconcile_partial' row(s) whose "
        "cross-currency matching generated this entry -- set by "
        "'journal_entry.item._reconcile_plan_with_sync' once "
        "'journal_entry.item._create_exchange_difference_moves' "
        "returns. Empty for a journal entry that is not itself an "
        "exchange difference entry.",
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
        # (behaviour-wise, simplified: no refund/payment/self-billing
        # concepts, none of which exist in this repo)
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
        # posted -- see the class docstring. Ported (behaviour-wise)
        # verbatim from upstream ``AccountMove._compute_name``: a
        # record whose branch does not reassign 'name' here keeps its
        # existing value. That is only actually safe for callers that
        # write 'state' without 'name' already carrying a real number
        # -- 'button_draft'/'button_cancel'/re-posting all go through
        # '_write_state' instead of a plain 'write({"state": ...})' for
        # that exact reason; see its docstring.
        self = self.sorted(lambda move: (move.date, move._origin.id))
        for move in self:
            if move.state == "cancel":
                continue
            move_has_name = move.name and move.name != "/"
            if not move.posted_before and not move._sequence_matches_date():
                move.name = False
                continue
            if move.date and not move_has_name and move.state != "draft":
                move._set_next_sequence()
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

    @api.depends("reversal_move_ids")
    def _compute_reversal_move_count(self):
        for move in self:
            move.reversal_move_count = len(move.reversal_move_ids)

    # ======================================================================
    # STATE MACHINE
    # ======================================================================

    def action_post(self):
        """Post every entry in 'self' (button handler for '_post')."""
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
            if not move.line_ids:
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

        self._write_state("posted", extra_vals={"posted_before": True})
        return self

    def button_draft(self):
        """Reset 'self' to draft.

        Trimmed from Odoo core ``AccountMove.button_draft``: keeps the
        posted/cancelled-only guard and the reconciliation guard (via
        'has_reconciled_entries', see that field's own help text).
        Drops the hash/secured guard (no hash chain here) and every
        payment/bank-statement cascade (no such documents here).
        'name' is deliberately left untouched -- see its own help text
        and '_write_state'\'s docstring for how that is enforced.
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
        self._write_state("draft")

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
        self._write_state("cancel")

    def _write_state(self, state, extra_vals=None):
        """Write 'state' (plus 'extra_vals') without losing 'name'.

        'name' depends on 'state' (see '_compute_name') purely so
        posting can assign it -- once a move already carries a real
        number, a *later* 'state' write (draft/cancel, or re-posting)
        must never touch it again. Re-triggering '_compute_name' for a
        record that already has a number is exactly that: harmless in
        principle ('_compute_name' falls through to preserving it), but
        Odoo's recompute-in-progress guard makes 'move.name' read back
        empty while the field is being recomputed, even for a plain
        self-referential re-assignment -- so leaving 'name' out of
        `vals` here is not safe to rely on. Writing it back explicitly,
        alongside 'state', in the *same* call protects it from ever
        being marked "to compute" in the first place (Odoo never
        recomputes a field whose value was just given directly).
        Records with no number yet (a fresh 'draft' being posted for
        the first time) are written in a separate, plain call instead,
        so '_compute_name' still gets to run and number them.
        """
        vals = {"state": state, **(extra_vals or {})}
        already_numbered = self.filtered(lambda move: move.name)
        unnumbered = self - already_numbered
        for move in already_numbered:
            move.write({**vals, "name": move.name})
        if unnumbered:
            unnumbered.write(vals)

    # ======================================================================
    # REVERSAL
    # ======================================================================
    # A posted entry is never corrected in place -- it is mirrored by a
    # reversal entry instead (see the class docstring's rationale and this
    # issue's Keputusan Desain). Ported (behaviour-wise, simplified: no
    # invoice-specific default values -- that concept does not exist on
    # this model) from Odoo core
    # ``addons/account/models/account_move.py``'s own reversal methods.
    # Driven end-to-end by the 'journal_entry_reversal' wizard
    # (``wizards/journal_entry_reversal.py``); 'action_reverse' below only
    # opens it.

    def action_reverse(self):
        """Open the reversal wizard for every entry in 'self'."""
        # NOT '.sudo()' -- unlike the generic SSI button pattern, this
        # method only ever opens a wizard (no cross-model read that
        # needs elevated access), and '_post()' (reached later, once the
        # wizard confirms) deliberately keys its own accounting-group
        # check off 'self.env.su' -- sudo-ing here would silently let
        # any user post a reversal regardless of that check. Matches
        # 'action_post'/'button_draft'/'button_cancel' above, none of
        # which sudo either.
        for record in self:
            result = record._reverse()
        return result

    def _reverse(self):
        """Open the 'journal_entry_reversal' wizard, pre-filled with 'self'.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove.action_reverse``.
        """
        self.ensure_one()
        return {
            "name": self.env._("Reverse Entry"),
            "type": "ir.actions.act_window",
            "res_model": "journal_entry_reversal",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_move_ids": [Command.set(self.ids)],
                "default_date": fields.Date.context_today(self),
            },
        }

    def action_view_reversal_moves(self):
        """Open the reversal entries of every entry in 'self' (smart button)."""
        for record in self:
            result = record._view_reversal_moves()
        return result

    def _view_reversal_moves(self):
        """Open 'reversal_move_ids' -- backs the header smart button."""
        self.ensure_one()
        return {
            "name": self.env._("Reversal Entries"),
            "type": "ir.actions.act_window",
            "res_model": "journal_entry",
            "view_mode": "list,form",
            "domain": [("id", "in", self.reversal_move_ids.ids)],
        }

    def _prepare_reversal_line_vals(self, line, negate):
        """Build create() vals for one mirror line of a reversal/copy entry.

        Only ever called with a 'product' 'line' -- every caller filters
        out 'tax'-typed lines first, since those are left for the *new*
        entry's own ``create()``
        (``_sync_dynamic_lines``/``_sync_tax_lines``) to regenerate from
        scratch, driven by that entry's own 'is_refund' flag (selecting
        the reverse repartition lines for a true reversal, the base ones
        for the 'modify' method's plain copy -- see the class docstring's
        tax engine section and this issue's Keputusan Desain).

        :param line: a ``journal_entry.item`` of ``self``.
        :param negate: whether ``balance``/``amount_currency`` are
            flipped (a true reversal) or kept as-is (the 'modify'
            method's fresh, editable draft copy).
        :return: a vals dict suitable for ``Command.create``.
        """
        sign = -1 if negate else 1
        vals = {
            "name": line.name,
            "sequence": line.sequence,
            "display_type": line.display_type,
            "account_id": line.account_id.id,
            "partner_id": line.partner_id.id,
            "product_id": line.product_id.id,
            "quantity": line.quantity,
            "price_unit": line.price_unit,
            "discount": line.discount,
            "date_maturity": line.date_maturity,
            "currency_id": line.currency_id.id,
            "balance": sign * line.balance,
            "amount_currency": sign * line.amount_currency,
        }
        if line.display_type == "product":
            vals["tax_ids"] = [Command.set(line.tax_ids.ids)]
        return vals

    def _reverse_move_vals(self, default_values=None):
        """Build create() vals for the mirror entry reversing 'self'.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._reverse_move_vals``, simplified: 'is_refund' is
        set directly (there is no invoice-type flag to swap on this
        model -- see the class docstring), and no invoice-specific
        vals (payment term, due date, ...) exist to strip.

        :param default_values: optional dict overriding 'journal_id'/
            'date'/'ref' -- see 'journal_entry_reversal._prepare_default_reversal'.
        :return: a vals dict suitable for ``journal_entry.create()``.
        """
        self.ensure_one()
        default_values = default_values or {}
        return {
            "journal_id": default_values.get("journal_id", self.journal_id.id),
            "date": default_values.get("date") or fields.Date.context_today(self),
            "ref": default_values.get("ref")
            or self.env._("Reversal of: %(name)s", name=self.display_name),
            "partner_id": self.partner_id.id,
            "is_refund": True,
            "line_ids": [
                Command.create(self._prepare_reversal_line_vals(line, negate=True))
                for line in self.line_ids
                if line.display_type != "tax"
            ],
        }

    def _prepare_modify_move_vals(self, default_values=None):
        """Build create() vals for the 'modify' method's fresh draft copy.

        Unlike '_reverse_move_vals', lines are copied as-is (not
        negated) and 'is_refund' is left at its default ('False'): this
        entry is a plain, editable continuation of 'self', not a
        reversal -- see the 'refund_method' field's help text on
        'journal_entry_reversal'.

        :param default_values: optional dict, same shape as
            '_reverse_move_vals''s own parameter.
        :return: a vals dict suitable for ``journal_entry.create()``.
        """
        self.ensure_one()
        default_values = default_values or {}
        return {
            "journal_id": default_values.get("journal_id", self.journal_id.id),
            "date": default_values.get("date") or fields.Date.context_today(self),
            "ref": self.ref,
            "partner_id": self.partner_id.id,
            "line_ids": [
                Command.create(self._prepare_reversal_line_vals(line, negate=False))
                for line in self.line_ids
                if line.display_type != "tax"
            ],
        }

    def _set_reversed_entry(self, reverse_move):
        """Link 'reverse_move' back to 'self' as its reversal counterpart."""
        self.ensure_one()
        reverse_move.reversed_entry_id = self.id

    def _reverse_moves(self, default_values_list=None, cancel=False):
        """Create, link, and post mirror entries reversing each of 'self'.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._reverse_moves``.

        :param default_values_list: list of dicts, same length/order as
            'self', each overriding '_reverse_move_vals''s own defaults
            for the matching entry.
        :param cancel: whether to also auto-reconcile each entry against
            its reversal -- see '_reconcile_reversed_moves' (a no-op
            until issue #12 lands, see this issue's Keputusan Desain).
        :return: the created, posted reversal entries, in the same
            order as 'self'.
        """
        if not default_values_list:
            default_values_list = [{}] * len(self)
        reverse_moves = self.env["journal_entry"].create(
            [
                move._reverse_move_vals(default_values)
                for move, default_values in zip(self, default_values_list, strict=False)
            ]
        )
        for move, reverse_move in zip(self, reverse_moves, strict=False):
            move._set_reversed_entry(reverse_move)
        reverse_moves._post()
        if cancel:
            self._reconcile_reversed_moves(reverse_moves)
        return reverse_moves

    def _reconcile_reversed_moves(self, reverse_moves):
        """Auto-reconcile each of 'self' against its matching 'reverse_moves'.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._reconcile_reversed_moves``.

        **Guarded as a forward reference**: 'journal_entry.item' has no
        'reconcile()'/'amount_residual' yet -- those are added by a
        later reconciliation unit (issue #12), which does **not**
        declare a dependency on this one, so they must not be assumed
        here (see this issue's Keputusan Desain). No-ops until then,
        the same pattern already used by ``account.py``'s
        ``_toggle_reconcile_to_true``/``_toggle_reconcile_to_false``;
        starts reconciling for real the moment #12 lands, without any
        further change here.

        :param reverse_moves: the reversal entries, in the same
            order as 'self' (see '_reverse_moves').
        """
        if "reconciled" not in self.env["journal_entry.item"]._fields:
            return
        for move, reverse_move in zip(self, reverse_moves, strict=False):
            accounts = move.line_ids.account_id.filtered(
                lambda account: account.reconcile
            )
            for account in accounts:
                (move.line_ids + reverse_move.line_ids).filtered(
                    lambda line, account=account: (
                        line.account_id == account and not line.reconciled
                    )
                ).reconcile()

    def _unlink_or_reverse(self):
        """Safely dispose of 'self': unlink entries never posted, reverse the rest.

        Ported (behaviour-wise) from Odoo core
        ``AccountMove._unlink_or_reverse`` -- a utility a later unit can
        call to get rid of a journal entry without ever hard-deleting
        one that was posted. Not wired to any button in this issue (see
        its out-of-scope section); ported now so a later unit's own
        acceptance criteria does not have to re-derive it from scratch.
        """
        if not self:
            return
        to_reverse = self.filtered(
            lambda move: move.posted_before or move.state != "draft"
        )
        to_unlink = self - to_reverse
        to_reverse._reverse_moves(cancel=True)
        to_unlink.filtered(lambda move: move.state != "cancel").button_cancel()
        to_unlink.unlink()

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

    # ======================================================================
    # TAXES COMPUTATION: BASE/TAX LINE ADAPTERS
    # ======================================================================
    # Ported (behaviour-wise) from Odoo core ``account_move.py``. These sit
    # on the header (not on ``journal_entry.item``), exactly like upstream's
    # own placement on ``AccountMove`` -- they convert a *line* into the
    # dict shape ``tax``'s computation engine (``models/tax.py``, added by
    # a prior unit) understands.

    def _get_product_base_line_currency_rate(self, product_line):
        """Conversion rate used by the tax engine for a 'product' line.

        Ported (behaviour-wise) from upstream
        ``AccountMove._get_product_base_line_currency_rate``, pruned to
        the entry branch: a plain journal entry has no
        ``invoice_currency_rate`` to fall back to (see the class
        docstring), so the rate is always the ratio between
        ``amount_currency`` and ``balance``.
        """
        return (
            abs(product_line.amount_currency / product_line.balance)
            if product_line.balance
            else 0.0
        )

    def _prepare_product_base_line_for_taxes_computation(self, product_line):
        """Convert a 'product' journal item into a base line for the tax engine.

        Ported (behaviour-wise) from upstream
        ``AccountMove._prepare_product_base_line_for_taxes_computation``,
        pruned to the entry branch (see the class docstring and this
        issue's Keputusan Desain): ``price_unit`` always comes from
        ``amount_currency`` (not ``price_unit``, which this model keeps
        purely informational -- see ``journal_entry.item``'s docstring),
        ``quantity``/``discount``/``sign`` are hardcoded to
        ``1.0``/``0.0``/``1``, and ``special_mode`` is always
        ``'total_excluded'``. **This enforces the product decision that
        ``quantity``/``price_unit`` stay informational -- do not fork the
        engine to make them drive the tax base.**

        ``is_refund`` is threaded through from ``self.is_refund`` (see
        that field's own help text) -- this is the sole, explicit switch
        the tax engine (``tax._prepare_base_line_for_taxes_computation``)
        uses to select ``repartition_line_reverse_ids`` over
        ``repartition_line_base_ids``, per the reversal issue's Keputusan
        Desain. Never derive it from ``state`` or any document-type
        concept here -- this model has neither.

        :param product_line: a ``journal_entry.item`` with
            ``display_type == 'product'``.
        :return: a base line, see ``tax._prepare_base_line_for_taxes_computation``.
        """
        self.ensure_one()
        kwargs = {
            "price_unit": product_line.amount_currency,
            "quantity": 1.0,
            "discount": 0.0,
            "rate": self._get_product_base_line_currency_rate(product_line),
            "sign": 1,
            "special_mode": "total_excluded",
            "is_refund": self.is_refund,
            "name": product_line.name,
        }

        computation_key = (product_line.extra_tax_data or {}).get("computation_key", "")
        if computation_key.startswith("global_discount"):
            kwargs["special_type"] = "global_discount"
        elif computation_key.startswith("down_payment"):
            kwargs["special_type"] = "down_payment"

        return self.env["tax"]._prepare_base_line_for_taxes_computation(
            product_line, **kwargs
        )

    def _prepare_tax_line_for_taxes_computation(self, tax_line):
        """Convert a 'tax' journal item into a tax line for the tax engine.

        Ported (behaviour-wise) from upstream
        ``AccountMove._prepare_tax_line_for_taxes_computation``, pruned to
        the entry branch: ``sign`` is always ``1`` (no invoice direction
        concept exists on ``journal_entry`` -- see the class docstring).

        :param tax_line: a ``journal_entry.item`` with
            ``tax_repartition_line_id`` set.
        :return: a tax line, see ``tax._prepare_tax_line_for_taxes_computation``.
        """
        self.ensure_one()
        return self.env["tax"]._prepare_tax_line_for_taxes_computation(tax_line, sign=1)

    def _get_rounded_base_and_tax_lines(self, round_from_tax_lines=True):
        """Extract the rounded base/tax lines for the taxes computation.

        Ported (behaviour-wise) from upstream
        ``AccountMove._get_rounded_base_and_tax_lines``, pruned per this
        issue's Keputusan Desain: a base line is always a ``product``-typed
        ``journal_entry.item`` (no ``invoice_line_ids``/epd/cash-rounding/
        non-deductible concepts exist on this model -- see the class
        docstring) and a tax line is always one carrying
        ``tax_repartition_line_id``.

        :param round_from_tax_lines: whether the manual tax amounts of
            existing tax journal items should be kept, rather than
            recomputed from scratch. Only matters once the entry is
            stored (``self.id``).
        :return: a tuple ``<base_lines, tax_lines>`` for the taxes
            computation.
        """
        self.ensure_one()
        AccountTax = self.env["tax"]
        base_amls = self.line_ids.filtered(lambda line: line.display_type == "product")
        base_lines = [
            self._prepare_product_base_line_for_taxes_computation(line)
            for line in base_amls
        ]

        tax_lines = []
        if self.id:
            AccountTax._add_tax_details_in_base_lines(base_lines, self.company_id)
            tax_amls = self.line_ids.filtered("tax_repartition_line_id")
            tax_lines = [
                self._prepare_tax_line_for_taxes_computation(tax_line)
                for tax_line in tax_amls
            ]
            AccountTax._round_base_lines_tax_details(
                base_lines,
                self.company_id,
                tax_lines=tax_lines if round_from_tax_lines else [],
            )
        else:
            AccountTax._add_tax_details_in_base_lines(base_lines, self.company_id)
            AccountTax._round_base_lines_tax_details(base_lines, self.company_id)
        return base_lines, tax_lines

    # -- '_sync_tax_lines' helpers -----------------------------------------
    # Split out of the contextmanager itself (upstream inlines all of these
    # as nested closures) purely to keep '_sync_tax_lines' under this
    # repo's mccabe complexity budget -- a structural, not behavioural,
    # deviation from a literal port (see 'item-format.md' §0/§6: path and
    # internal structure are left to the executor).

    def _sync_tax_lines_get_base_lines(self, move):
        return move.line_ids.filtered(lambda line: line.display_type == "product")

    def _sync_tax_lines_get_tax_lines(self, move):
        return move.line_ids.filtered("tax_repartition_line_id")

    @api.model
    def _sync_tax_lines_get_value(self, record, field):
        return record._fields[field].convert_to_write(record[field], record)

    def _sync_tax_lines_get_tax_line_tracked_fields(self, line):
        return ("amount_currency", "balance")

    def _sync_tax_lines_get_base_line_tracked_fields(self, line):
        AccountTax = self.env["tax"]
        fake_base_line = AccountTax._prepare_base_line_for_taxes_computation(None)
        grouping_key = AccountTax._prepare_base_line_grouping_key(fake_base_line)
        item_fields = self.env["journal_entry.item"]._fields
        return [key for key in grouping_key if key in item_fields] + ["amount_currency"]

    def _sync_tax_lines_field_has_changed(self, values, record, field):
        return self._sync_tax_lines_get_value(record, field) != values.get(
            record, {}
        ).get(field)

    def _sync_tax_lines_get_changed_lines(self, values, records, fields=None):
        return (
            record
            for record in records
            if record not in values
            or any(
                self._sync_tax_lines_field_has_changed(values, record, field)
                for field in values[record]
                if not fields or field in fields
            )
        )

    def _sync_tax_lines_any_field_has_changed(self, values, records, fields=None):
        return any(
            record
            for record in self._sync_tax_lines_get_changed_lines(
                values, records, fields
            )
        )

    def _sync_tax_lines_is_write_needed(self, line, values):
        item_fields = self.env["journal_entry.item"]._fields
        return any(
            item_fields[fname].convert_to_write(line[fname], self) != values[fname]
            for fname in values
        )

    def _sync_tax_lines_snapshot(self, container):
        """Capture the "before" state ``_sync_tax_lines`` diffs against.

        :return: a ``(base_lines_before, tax_lines_before)`` tuple, each
            keyed by move.
        """
        base_lines_before = {
            move: {
                line: {
                    field: self._sync_tax_lines_get_value(line, field)
                    for field in self._sync_tax_lines_get_base_line_tracked_fields(line)
                }
                for line in self._sync_tax_lines_get_base_lines(move)
            }
            for move in container["records"]
        }
        tax_lines_before = {
            move: {
                line: {
                    field: self._sync_tax_lines_get_value(line, field)
                    for field in self._sync_tax_lines_get_tax_line_tracked_fields(line)
                }
                for line in self._sync_tax_lines_get_tax_lines(move)
            }
            for move in container["records"]
        }
        return base_lines_before, tax_lines_before

    def _sync_tax_lines_compute_round_from_tax_lines(
        self, base_lines, tax_lines, base_before, tax_before
    ):
        """Decide how ``_get_rounded_base_and_tax_lines`` should treat this move.

        Upstream's own version also takes the changing move itself, to
        decide (via ``is_invoice()``) whether a bare ``currency_id``
        change alone should force
        ``round_from_tax_lines = False`` -- unreachable here (see
        ``_sync_tax_lines``'s docstring), so dropped along with the
        ``move`` parameter. It can also return
        ``'reapply_currency_rate'`` (a third value for its
        ``round_from_tax_lines`` parameter) -- likewise unreachable.

        :return: ``False``/``True`` (see
            ``_get_rounded_base_and_tax_lines``'s own ``round_from_tax_lines``
            parameter) to resync this move, or ``None`` to skip it entirely.
        """
        if any(
            line not in base_lines
            for line, values in base_before.items()
            if values["tax_ids"]
        ):
            return self._sync_tax_lines_any_field_has_changed(tax_before, tax_lines)
        changed_lines = list(
            self._sync_tax_lines_get_changed_lines(base_before, base_lines)
        )
        if not changed_lines:
            return None
        round_from_tax_lines = all(
            not line.tax_ids and not base_before.get(line, {}).get("tax_ids")
            for line in changed_lines
        ) or (
            list(tax_before) != list(tax_lines)
            or any(
                self.env.is_protected(line._fields[fname], line)
                for line in tax_lines
                for fname in tax_before[line]
            )
        )
        if round_from_tax_lines and any(
            line[field]
            for line in changed_lines
            for field in ("amount_currency", "balance")
        ):
            return None
        return round_from_tax_lines

    def _sync_tax_lines_collect(
        self, move, round_from_tax_lines, to_delete, to_create, grouped_update
    ):
        """Compute ``move``'s tax line diff, accumulated into the caller's lists."""
        AccountTax = self.env["tax"]
        item_fields = self.env["journal_entry.item"]._fields
        base_lines_values, tax_lines_values = move._get_rounded_base_and_tax_lines(
            round_from_tax_lines=round_from_tax_lines
        )
        AccountTax._add_accounting_data_in_base_lines_tax_details(
            # Upstream passes 'move.always_tax_exigible' here -- that field
            # was never created (cash basis is out of scope, see the
            # issue's Keputusan Desain), so unlike every other
            # is_invoice()-dead branch in this method, there is no branch
            # to keep alive: hardcoded 'True' is already this issue's
            # final value, nothing left to prune later.
            base_lines_values,
            move.company_id,
            include_caba_tags=True,
        )
        tax_results = AccountTax._prepare_tax_lines(
            base_lines_values, move.company_id, tax_lines=tax_lines_values
        )

        for base_line, to_update in tax_results["base_lines_to_update"]:
            line = base_line["record"]
            if self._sync_tax_lines_is_write_needed(line, to_update):
                grouped_update[line.currency_id.id, frozendict(to_update)].add(line.id)

        for tax_line_vals in tax_results["tax_lines_to_delete"]:
            to_delete.append(tax_line_vals["record"].id)

        for tax_line_vals in tax_results["tax_lines_to_add"]:
            to_create.append(
                {
                    **{
                        key: value
                        for key, value in tax_line_vals.items()
                        if key in item_fields
                    },
                    "display_type": "tax",
                    "move_id": move.id,
                }
            )

        for tax_line_vals, _grouping_key, to_update in tax_results[
            "tax_lines_to_update"
        ]:
            line = tax_line_vals["record"]
            if self._sync_tax_lines_is_write_needed(line, to_update):
                grouped_update[line.currency_id.id, frozendict(to_update)].add(line.id)

    @contextmanager
    def _sync_tax_lines(self, container):
        """Create/update/delete tax lines to match their base lines' taxes.

        Ported (behaviour-wise) from Odoo core ``AccountMove._sync_tax_lines``,
        pruned per this issue's Keputusan Desain: only entries whose lines
        carry ``tax_ids``/``tax_repartition_line_id`` reach this stage at
        all (see ``_sync_dynamic_lines``), a base line is always
        ``display_type == 'product'``, and every ``is_invoice()``-gated
        branch upstream had (a bare ``currency_id`` change
        forcing a full recompute, ``price_unit``/``quantity``/``discount``
        as extra tracked fields, ``'reapply_currency_rate'``) is gone --
        this model has no invoice-type concept at all (see the class
        docstring). Split into the ``_sync_tax_lines_*`` helper methods
        above purely to keep every individual method under this repo's
        mccabe complexity budget -- a structural deviation from upstream's
        own single nested-closure-laden method, not a behavioural one.

        :param container: dict with a ``'records'`` key, holding only the
            entries whose lines carry ``tax_ids``/``tax_repartition_line_id``
            (see ``_sync_dynamic_lines``).
        """
        base_lines_before, tax_lines_before = self._sync_tax_lines_snapshot(container)
        yield

        to_delete = []
        to_create = []
        grouped_update = defaultdict(set)
        for move in container["records"]:
            if move.state != "draft":
                continue

            tax_lines = self._sync_tax_lines_get_tax_lines(move)
            base_lines = self._sync_tax_lines_get_base_lines(move)
            round_from_tax_lines = self._sync_tax_lines_compute_round_from_tax_lines(
                base_lines,
                tax_lines,
                base_lines_before.get(move, {}),
                tax_lines_before.get(move, {}),
            )
            if round_from_tax_lines is None:
                continue
            self._sync_tax_lines_collect(
                move, round_from_tax_lines, to_delete, to_create, grouped_update
            )

        if grouped_update:
            for (_currency_id, values), lines in grouped_update.items():
                self.env["journal_entry.item"].browse(lines).write(dict(values))
        if to_delete:
            self.env["journal_entry.item"].browse(to_delete).unlink()
        if to_create:
            self.env["journal_entry.item"].create(to_create)

    @contextmanager
    def _sync_balancing_lines(self, container):
        """Auto-balance ``container['records']``'s lines around the wrapped block.

        Ported (framework-wise) from Odoo core
        ``AccountMove._sync_unbalanced_lines`` (the contextmanager half of
        upstream's own single sync stage, before this issue's tax stage
        joined it as a second one -- see ``_sync_dynamic_lines``).
        Unchanged behaviour-wise from the single-stage version this
        method replaces: the docstring below is the same one that used
        to sit directly on ``_sync_dynamic_lines``.

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

    @contextmanager
    def _sync_dynamic_lines(self, container):
        """Run the two-stage sync (tax lines, then balancing) around the block.

        Ported (framework-wise) from Odoo core ``AccountMove._sync_dynamic_lines``,
        its ``ExitStack`` scaffold trimmed to exactly two stages --
        ``_sync_balancing_lines`` and ``_sync_tax_lines`` -- upstream's
        payment-term/discount/rounding/epd/invoice stages all being out
        of this repo's scope (see the class docstring). Matching
        upstream's own relative ordering (``_sync_unbalanced_lines`` at
        sequence 20, ``_sync_tax_lines`` at sequence 50 -- entered
        low-to-high), the balancing stage is *entered* first and the tax
        stage second, so that on exit (``ExitStack`` unwinds
        last-entered-first) the tax stage's post-yield code runs
        *before* the balancing stage's: new/updated/deleted tax lines
        must exist before the auto-balancing line is recomputed against
        the entry's final line set.

        :param container: dict with a ``'records'`` key, read again after
            the wrapped block runs (``create()`` only knows the final
            recordset once ``super().create()`` has returned).
        """

        def get_tax_container_records():
            return container["records"].filtered(
                lambda move: move.line_ids.tax_ids
                or move.line_ids.tax_repartition_line_id
            )

        tax_container = {"records": get_tax_container_records()}
        with ExitStack() as stack:
            stack.enter_context(self._sync_balancing_lines(container))
            stack.enter_context(self._sync_tax_lines(tax_container))
            yield
            tax_container["records"] = get_tax_container_records()

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

        **Skipped while ``create()``/``write()`` are still mid-sync**
        (flagged via ``skip_check_balanced_constrains``, see those
        methods): a coordinated multi-line update -- most commonly this
        unit's own tax resync, which by design touches a base line and
        its tax line(s) as separate, sequential writes -- is genuinely,
        transiently unbalanced between those writes. Upstream's own
        ``_check_balanced`` sidesteps this entirely by being a
        contextmanager checked once, after the whole operation settles,
        rather than an eager ``@api.constrains``; ``create()``/``write()``
        below replicate that by suppressing this eager check during their
        own sync window and calling ``_check_balanced`` explicitly,
        exactly once, right after it closes.
        """
        if self.env.context.get("skip_check_balanced_constrains"):
            return
        self._check_balanced({"records": self})

    @api.model_create_multi
    def create(self, vals_list):
        """Create, then validate balance once the tax/balancing sync has settled.

        See ``_check_balanced_constrains``'s docstring for why the eager
        constrain is suppressed (``skip_check_balanced_constrains``) for
        the duration of the whole ``_sync_dynamic_lines`` cycle -- not
        just ``super().create()`` itself, but also its post-yield tax/
        balancing stages (e.g. deleting a now-untaxed line's tax line
        forces an unrelated compute -- ``amount_total_debit`` -- to flush
        and re-validate every line's balance mid-cycle). Without covering
        the whole cycle, that post-yield work would reject a perfectly
        valid entry over its own transient construction state. ``moves``
        is rebound to ``self`` (this method's own, unflagged recordset)
        before being returned, so the suppression never leaks to the
        caller.
        """
        container = {"records": self.env["journal_entry"]}
        flagged_self = self.with_context(skip_check_balanced_constrains=True)
        with flagged_self._sync_dynamic_lines(container):
            created = super(JournalEntry, flagged_self).create(vals_list)
            moves = self.browse(created.ids)
            container["records"] = moves
        self._check_balanced({"records": moves})
        return moves

    def write(self, vals):
        """Write, then validate balance once the tax/balancing sync has settled.

        See ``create()``'s and ``_check_balanced_constrains``'s
        docstrings for why the eager constrain is suppressed
        (``skip_check_balanced_constrains``) for the duration of the
        whole ``_sync_dynamic_lines`` cycle, not just ``super().write()``.
        """
        container = {"records": self}
        if self._disable_recursion(container, "journal_entry_sync_dynamic_lines"):
            return super().write(vals)
        flagged = container["records"].with_context(skip_check_balanced_constrains=True)
        container["records"] = flagged
        with flagged._sync_dynamic_lines(container):
            result = super(JournalEntry, flagged).write(vals)
        self._check_balanced({"records": self})
        return result
