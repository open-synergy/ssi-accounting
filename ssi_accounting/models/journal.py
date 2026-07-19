# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

JOURNAL_TYPE_SELECTION = [
    ("general", "Miscellaneous"),
    ("situation", "Opening/Closing"),
]

# Leading letter used by `_get_next_journal_default_code` so two journal
# types started fresh in the same company do not immediately collide.
JOURNAL_TYPE_CODE_PREFIX = {
    "general": "M",
    "situation": "O",
}


class AccountJournal(models.Model):
    """Where journal entries post to, and how they are numbered/defaulted.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_journal.py`` (class
    ``AccountJournal``, model ``account.journal``), because that model
    lives inside the ``account`` module, which ``ssi_accounting`` must
    not depend on.

    **`type` deliberately shrunk to two values** (see the backlog issue
    this was built for): ``general`` (Miscellaneous) and ``situation``
    (Opening/Closing). Upstream's ``sale``/``purchase``/``bank``/``cash``
    types, and everything that only exists to serve them, are dropped:
    ``type_control_ids``/``account_control_ids``, every payment-method
    field, ``bank_account_id``/``bank_acc_number``/``bank_id``,
    ``profit_account_id``/``loss_account_id``, invoice reference/refund
    sequence fields, the mail alias fields, ``restrict_mode_hash_table``,
    ``non_deductible_account_id``, ``country_code``, ``accounting_date``,
    and ``has_invalid_statements``. Adding a type back later is additive
    and does not require touching what is kept here.

    ``entry_count`` (always 0 until ``account.move`` lands) replaces the
    whole upstream dashboard (``account_journal_dashboard.py``): a single
    smart button to the journal's entries is enough for this repo's scope
    -- the KPI/graph dashboard widget is not ported at all.

    Every method below that references ``account.move`` (a model that
    does not exist yet in this repo) is guarded on ``"account.move" not
    in self.env``, exactly like ``account.py`` and ``account_group.py``
    already guard their own forward references -- each guard is a no-op
    today and starts doing real work the moment ``account.move`` lands,
    with no further change needed here.
    """

    _name = "account.journal"
    _description = "Journal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sequence, type, code"
    _check_company_auto = True

    _code_company_uniq = models.Constraint(
        "UNIQUE (code, company_id)",
        "Another journal with that code already exists in this company.",
    )

    name = fields.Char(
        required=True,
        translate=True,
        tracking=True,
        help="Label of the journal, shown throughout the accounting app.",
    )
    code = fields.Char(
        string="Short Code",
        size=5,
        required=True,
        tracking=True,
        compute="_compute_code",
        readonly=False,
        store=True,
        precompute=True,
        copy=False,
        help="Short, unique-per-company code used to prefix journal "
        "entry numbers. Auto-generated when left empty.",
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help="Set active to false to hide the journal without removing it.",
    )
    type = fields.Selection(
        selection=JOURNAL_TYPE_SELECTION,
        required=True,
        tracking=True,
        help="Miscellaneous for day-to-day manual entries, Opening/Closing "
        "for fiscal year opening and closing entries.",
    )
    sequence = fields.Integer(
        default=10,
        help="Used to order journals in lists.",
    )
    company_id = fields.Many2one(
        string="Company",
        comodel_name="res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="Company this journal belongs to.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Currency",
        help="Forces all journal entries of this journal to use a "
        "specific currency. Leave empty to allow any currency.",
    )
    default_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Default Account",
        check_company=True,
        copy=False,
        help="Account used as the counterpart when a journal entry line "
        "does not specify one. Auto-created when left empty.",
    )
    default_account_type = fields.Char(
        compute="_compute_default_account_type",
        help="Kept for naming symmetry with upstream's Many2one field of "
        "the same name. This module only has the generic 'general' "
        "journal category, so it is reduced to a constant label.",
    )
    suspense_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Suspense Account",
        check_company=True,
        default=lambda self: self.env.company.account_journal_suspense_account_id,
        help="Account used to post amounts that cannot be immediately "
        "reconciled. Defaults to the company's suspense account.",
    )
    journal_group_ids = fields.Many2many(
        comodel_name="account.journal.group",
        string="Journal Groups",
        domain="[('company_id', '=', company_id)]",
        check_company=True,
        help="Groups this journal belongs to, used to filter the journal list.",
    )
    sequence_override_regex = fields.Text(
        help="Technical field read by 'mixin.sequence_number' (once a "
        "concrete numbered document is wired to a journal) to enforce a "
        "custom sequence composition the mixin would otherwise "
        "misinterpret.",
    )
    entry_count = fields.Integer(
        compute="_compute_entry_count",
        help="Number of journal entries posted on this journal. Always "
        "0 until 'account.move' is added in a later unit.",
    )

    @api.depends("company_id")
    def _compute_code(self):
        for journal in self:
            if not journal.code:
                journal.code = journal._get_next_journal_default_code(
                    journal.type, journal.company_id
                )

    @api.model
    def _get_next_journal_default_code(self, journal_type="general", company=None):
        """Return an unused, exactly-5-character code for a new journal.

        :param journal_type: 'general' or 'situation' -- selects the
            leading letter of the generated code (see
            ``JOURNAL_TYPE_CODE_PREFIX``) so two journal types started
            fresh in the same company do not immediately collide.
        :param company: company the code must be unique within;
            defaults to the active company.
        :return: an available 5-character code, e.g. 'M0001'.
        """
        company = company or self.env.company
        prefix = JOURNAL_TYPE_CODE_PREFIX.get(journal_type, "J")
        used_codes = set(
            self.sudo()
            .with_context(active_test=False)
            .search([("company_id", "=", company.id)])
            .mapped("code")
        )
        for num in range(1, 10000):
            code = f"{prefix}{num:04d}"
            if code not in used_codes:
                return code
        raise UserError(
            self.env._(
                """
Context: Generate new journal code
Database ID: %(database_id)s
Problem: Cannot generate an unused journal code for company %(company)s
Solution: Set the journal code manually
""",
                database_id=",".join(str(i) for i in self.ids),
                company=company.name,
            )
        )

    def _compute_default_account_type(self):
        for journal in self:
            journal.default_account_type = "general"

    @api.depends_context("company")
    def _compute_entry_count(self):
        if "account.move" not in self.env:
            self.entry_count = 0
            return
        for journal in self:
            journal.entry_count = self.env["account.move"].search_count(
                [("journal_id", "=", journal.id)]
            )

    def action_open_journal_entries(self):
        self.ensure_one()
        if "account.move" not in self.env:
            return {"type": "ir.actions.act_window_close"}
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Journal Entries"),
            "res_model": "account.move",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("journal_id", "=", self.id)],
        }

    @api.constrains("type", "default_account_id")
    def _check_type_default_account_id_type(self):
        for journal in self:
            if journal.default_account_id.account_type in (
                "asset_receivable",
                "liability_payable",
            ):
                raise ValidationError(
                    self.env._(
                        """
Context: Save journal
Database ID: %(database_id)s
Problem: The default account of a journal cannot be of type Receivable
    or Payable
Solution: Pick a different default account
""",
                        database_id=journal.id,
                    )
                )

    @api.constrains("company_id")
    def _check_company_consistency(self):
        if not self or "account.move" not in self.env:
            return
        self.env["account.move"].flush_model(["journal_id", "company_id"])
        self.flush_model(["company_id"])
        self.env.cr.execute(
            """
            SELECT move.id
              FROM account_move move
              JOIN account_journal journal ON journal.id = move.journal_id
             WHERE move.journal_id IN %(journal_ids)s
               AND move.company_id != journal.company_id
            """,
            {"journal_ids": tuple(self.ids)},
        )
        if self.env.cr.fetchone():
            raise UserError(
                self.env._(
                    """
Context: Update journal company
Database ID: %(database_id)s
Problem: This journal already has journal entries linked to a different
    company
Solution: Keep the current company, or first reassign the journal
    entries
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )

    @api.depends("name", "currency_id", "company_id.currency_id")
    def _compute_display_name(self):
        for journal in self:
            name = journal.name
            if (
                journal.currency_id
                and journal.currency_id != journal.company_id.currency_id
            ):
                name = f"{name} ({journal.currency_id.name})"
            journal.display_name = name

    @api.model
    def _prepare_liquidity_account_vals(self, company, code, vals):
        """Build vals for the account auto-created as a journal's default account.

        Trimmed from upstream ``AccountJournal._prepare_liquidity_account_vals``:
        this repo has no 'liquidity' account_type category (no bank/cash
        journal types), so the created account always uses the generic
        'asset_current' type instead of branching on journal type.
        """
        return {
            "name": vals.get("name"),
            "code": code,
            "account_type": "asset_current",
            "currency_id": vals.get("currency_id"),
            "company_ids": [Command.set(company.ids)],
        }

    def _create_default_account(self, company, vals):
        account_model = self.env["account.account"].with_company(company)
        code = account_model._search_new_account_code("MISC000")
        account_vals = self._prepare_liquidity_account_vals(company, code, vals)
        return self.env["account.account"].create(account_vals)

    @api.model
    def _fill_missing_values(self, vals):
        """Fill in company and default account before create().

        Trimmed from upstream ``AccountJournal._fill_missing_values``: no
        bank/cash/payment-method branches (this repo's ``type`` selection
        only has ``general``/``situation``), only company + default
        account.
        """
        company = (
            self.env["res.company"].browse(vals["company_id"])
            if vals.get("company_id")
            else self.env.company
        )
        vals["company_id"] = company.id
        if not vals.get("default_account_id"):
            vals["default_account_id"] = self._create_default_account(company, vals).id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._fill_missing_values(vals)
        return super().create(vals_list)

    def write(self, vals):
        if "company_id" in vals and "account.move" in self.env:
            for journal in self:
                if journal.company_id.id != vals["company_id"] and self.env[
                    "account.move"
                ].sudo().search_count([("journal_id", "=", journal.id)], limit=1):
                    raise UserError(
                        self.env._(
                            """
Context: Update journal company
Database ID: %(database_id)s
Problem: This journal already contains journal entries, therefore its
    company cannot be changed
Solution: Keep the current company, or create a new journal in the
    target company instead
""",
                            database_id=journal.id,
                        )
                    )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_contains_journal_entries(self):
        if "account.move" not in self.env:
            return
        if (
            self.env["account.move"]
            .sudo()
            .search_count([("journal_id", "in", self.ids)], limit=1)
        ):
            raise UserError(
                self.env._(
                    """
Context: Delete journal
Database ID: %(database_id)s
Problem: This journal already has journal entries
Solution: Remove or reassign the journal entries before deleting the
    journal
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )

    def copy_data(self, default=None):
        default = dict(default or {})
        vals_list = super().copy_data(default=default)
        for journal, vals in zip(self, vals_list, strict=True):
            # `code` is `copy=False`, so `super().copy_data()` already
            # excludes it -- pop it defensively anyway. Leaving the key
            # OUT of vals (not merely falsy) is required for
            # `_compute_code` (precompute) to run and assign a fresh,
            # unique code: if 'code' were present in vals at all (even as
            # False/None), the ORM would treat it as an explicit user
            # value and write NULL straight to the NOT NULL column,
            # bypassing the compute entirely.
            vals.pop("code", None)
            vals["name"] = self.env._("%(name)s (copy)", name=journal.name)
        return vals_list
