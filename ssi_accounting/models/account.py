# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import contextlib
import itertools
import re
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain
from odoo.tools import SQL, Query

ACCOUNT_REGEX = re.compile(r"(?:(\S*\d+\S*))?(.*)")
ACCOUNT_CODE_NUMBER_REGEX = re.compile(r"(.*?)(\d*)(\D*?)$")

ACCOUNT_TYPE_SELECTION = [
    ("asset_receivable", "Receivable"),
    ("asset_cash", "Bank and Cash"),
    ("asset_current", "Current Assets"),
    ("asset_non_current", "Non-current Assets"),
    ("asset_prepayments", "Prepayments"),
    ("asset_fixed", "Fixed Assets"),
    ("liability_payable", "Payable"),
    ("liability_credit_card", "Credit Card"),
    ("liability_current", "Current Liabilities"),
    ("liability_non_current", "Non-current Liabilities"),
    ("equity", "Equity"),
    ("equity_unaffected", "Current Year Earnings"),
    ("income", "Income"),
    ("income_other", "Other Income"),
    ("expense", "Expenses"),
    ("expense_other", "Other Expenses"),
    ("expense_depreciation", "Depreciation"),
    ("expense_direct_cost", "Cost of Revenue"),
    ("off_balance", "Off-Balance Sheet"),
]


class AccountAccount(models.Model):
    """Chart of accounts entry, with Odoo 19 style multi-company support.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_account.py`` (class ``AccountAccount``,
    model ``account.account``), because that model lives inside the
    ``account`` module, which ``ssi_accounting`` must not depend on. Kept
    as ``account.account`` (not shortened to ``account``, unlike
    ``account.tag``'s rename from upstream's ``account.account.tag``):
    ``account_group.py`` (already merged) hardcodes the env-model check
    ``"account.account" not in self.env`` and the raw-SQL table name
    ``account_account`` in ``_adapt_accounts_for_account_groups``, so this
    model must keep that exact name for that already-shipped sync
    mechanism to start working, rather than staying a no-op forever.

    **Multi-company engine kept verbatim, not simplified**: ``company_ids``
    is a Many2many (one account can be shared by several companies), backed
    by ``code_store`` (a ``company_dependent`` ``Char``) and the computed
    ``code``/``placeholder_code`` fields built on top of it, including the
    ``_field_to_sql`` override that turns ``code`` into a JSONB lookup
    keyed by the *viewing* company's root company. This is a deliberate,
    load-bearing product decision (see the issue this was built for) --
    replacing it with a plain ``company_id`` Many2one later would be an
    expensive migration (new join table + ``code`` backfill into
    ``code_store``), so it must be right from the start.

    **Forward references to models that do not exist yet in this repo**
    (``account.move.line``, ``account.journal``, ``tax``,
    ``tax.repartition.line``) are guarded with ``"<model>" not in
    self.env`` / ``"<field>" in self._fields`` checks, exactly like
    ``account_group.py``'s ``_adapt_accounts_for_account_groups`` and
    ``res_currency.py``'s ``_has_accounting_entries`` already do. Each
    guard is a no-op today and starts doing real work the moment the
    corresponding unit lands, with no further change needed here:

    - ``account.move.line`` (journal items): ``used``, ``current_balance``,
      ``_toggle_reconcile_to_true``/``_toggle_reconcile_to_false``,
      ``_unlink_except_contains_journal_items``, the journal-item leg of
      ``_check_company_consistency``.
    - ``account.journal``: ``_check_journal_consistency`` (trimmed to the
      journal/account currency mismatch check; upstream's extra
      ``account.payment.method`` branches are dropped -- no payment method
      concept exists anywhere in this repo's planned scope).
    - ``tax`` / ``tax.repartition.line``: ``related_taxes_amount``,
      ``action_open_related_taxes``, ``_unlink_except_linked_to_tax_repartition_line``.

    **``tax_ids`` is deliberately NOT added in this unit.** A Many2many
    field needs a real comodel at registry-build time -- unlike a method
    body, it cannot be guarded at runtime -- so it must wait for the tax
    configuration unit to add it back via inheritance. ``_onchange_account_type``
    (whose only job upstream is clearing ``tax_ids`` for off-balance
    accounts) is kept but guarded on ``"tax_ids" in self._fields`` for the
    same reason, so it silently starts working once that field exists.

    **Deliberately dropped from the upstream model** (out of this issue's
    scope, see the "Tidak termasuk" section): the opening balance triplet
    (``opening_debit``/``opening_credit``/``opening_balance``) and
    ``_load_precommit_update_opening_move``; ``non_trade``;
    ``company_fiscal_country_code``; the partner-frequency heuristics
    (``_get_most_frequent_accounts_for_partner`` and the ``_order_to_sql``/
    ``name_search`` overrides built on top of it -- pure UX sugar for the
    invoice line account widget); ``name_create``; ``get_import_templates``;
    ``_merge_method``/``action_unmerge`` and the whole unmerge suite;
    ``_check_account_type_sales_purchase_journal`` and
    ``_check_account_is_bank_journal_bank_account`` (no ``sale``/
    ``purchase``/``bank`` journal types exist in this repo);
    ``_check_account_code`` (alphanumeric code format) and
    ``_check_reconcile``/``_constrains_reconcile`` (receivable/payable
    reconcile requirement, off-balance tax/reconcile guard) -- none of
    these are in the issue's enumerated "Constraint yang dipertahankan"
    list. ``description`` is dropped along with them, so
    ``_compute_display_name``/``_search_display_name`` below only compare
    ``code``/``name``, not a description snippet.
    """

    _name = "account.account"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Account"
    _order = "code"
    _check_company_auto = True
    _check_company_domain = models.check_companies_domain_parent_of

    name = fields.Char(
        string="Account Name",
        required=True,
        index="trigram",
        tracking=True,
        translate=True,
        help="Label of the account, shown throughout the chart of accounts.",
    )
    code_store = fields.Char(
        company_dependent=True,
        help="Internal, per-company storage for the account code. Read/write "
        "it through 'code' instead -- this field only exists so each "
        "company sharing this account can have its own code.",
    )
    code = fields.Char(
        size=64,
        tracking=True,
        compute="_compute_code",
        search="_search_code",
        inverse="_inverse_code",
        help="Account code as seen from the active company. Two companies "
        "sharing this account may each give it a different code.",
    )
    placeholder_code = fields.Char(
        string="Display code",
        compute="_compute_placeholder_code",
        search="_search_placeholder_code",
        help="Code shown when the active company has no code of its own for "
        "this account: falls back to another authorized company's code.",
    )
    active = fields.Boolean(
        default=True,
        tracking=True,
        help="Set active to false to hide the account without removing it.",
    )
    account_type = fields.Selection(
        selection=ACCOUNT_TYPE_SELECTION,
        string="Type",
        required=True,
        tracking=True,
        help="Used for information purposes and to derive internal_group and "
        "the default reconcile flag.",
    )
    internal_group = fields.Selection(
        selection=[
            ("equity", "Equity"),
            ("asset", "Asset"),
            ("liability", "Liability"),
            ("income", "Income"),
            ("expense", "Expense"),
            ("off", "Off Balance"),
        ],
        compute="_compute_internal_group",
        search="_search_internal_group",
        help="Broad accounting group derived from account_type.",
    )
    reconcile = fields.Boolean(
        string="Allow Reconciliation",
        tracking=True,
        compute="_compute_reconcile",
        store=True,
        readonly=False,
        precompute=True,
        help="Check this box if this account allows matching of journal items.",
    )
    include_initial_balance = fields.Boolean(
        string="Bring Accounts Balance Forward",
        compute="_compute_include_initial_balance",
        search="_search_include_initial_balance",
        help="Whether reports should consider journal items from the "
        "beginning of time instead of from the fiscal year only.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Account Currency",
        tracking=True,
        help="Forces all journal items in this account to have a specific "
        "currency. If no currency is set, entries can use any currency.",
    )
    company_currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_company_currency_id",
        help="Currency of the active company, used to display monetary "
        "fields on this account.",
    )
    note = fields.Text(
        string="Internal Notes",
        tracking=True,
        help="Free-form internal note about this account.",
    )
    company_ids = fields.Many2many(
        comodel_name="res.company",
        relation="account_company_rel",
        column1="account_id",
        column2="company_id",
        string="Companies",
        required=True,
        readonly=False,
        depends_context=("uid",),
        default=lambda self: self.env.company,
        help="Companies allowed to use this account. Each company may give "
        "the account its own code through the Mapping tab.",
    )
    code_mapping_ids = fields.One2many(
        comodel_name="account.code.mapping",
        inverse_name="account_id",
        help="Per-company view of this account's code, used by the Mapping tab.",
    )
    code_mapping_ids.write_sequence = 19
    tag_ids = fields.Many2many(
        comodel_name="account.tag",
        relation="account_tag_rel",
        column1="account_id",
        column2="tag_id",
        string="Tags",
        domain=[("applicability", "=", "accounts")],
        ondelete="restrict",
        tracking=True,
        help="Optional tags you may want to assign for custom reporting.",
    )
    group_id = fields.Many2one(
        comodel_name="account.group",
        compute="_compute_account_group",
        store=True,
        help="Account group whose code prefix range matches this account's "
        "code. Maintained automatically.",
    )
    root_id = fields.Many2one(
        comodel_name="account.root",
        compute="_compute_account_root",
        search="_search_account_root",
        help="First 1-2 digits grouping of this account's code.",
    )
    used = fields.Boolean(
        compute="_compute_used",
        search="_search_used",
        help="Whether this account already has journal items posted on it.",
    )
    current_balance = fields.Float(
        compute="_compute_current_balance",
        help="Posted balance of this account in the active company.",
    )
    related_taxes_amount = fields.Integer(
        compute="_compute_related_taxes_amount",
        help="Number of taxes whose repartition lines point to this account.",
    )
    display_mapping_tab = fields.Boolean(
        default=lambda self: len(self.env.user.company_ids) > 1,
        store=False,
        help="Technical field: whether the form should show the per-company "
        "code Mapping tab.",
    )

    def _field_to_sql(self, alias, field_expr, query=None):
        if field_expr == "internal_group":
            return SQL(
                "split_part(%s, '_', 1)",
                self._field_to_sql(alias, "account_type", query),
            )
        if field_expr == "code":
            return (
                self.with_company(self.env.company.root_id)
                .sudo()
                ._field_to_sql(alias, "code_store", query)
            )
        if field_expr == "placeholder_code":
            if "account_first_company" not in query._joins:
                query.add_join(
                    "LEFT JOIN",
                    "account_first_company",
                    SQL(
                        """(
                            SELECT DISTINCT ON (rel.account_id)
                                rel.account_id AS account_id,
                                rel.company_id AS company_id,
                                SPLIT_PART(res_company.parent_path, '/', 1)
                                    AS root_company_id,
                                res_company.name AS company_name
                            FROM account_company_rel rel
                            JOIN res_company
                                ON res_company.id = rel.company_id
                            WHERE rel.company_id IN %(authorized_company_ids)s
                        ORDER BY rel.account_id, company_id
                        )""",
                        authorized_company_ids=self.env.user._get_company_ids(),
                        to_flush=self._fields["company_ids"],
                    ),
                    SQL(
                        "account_first_company.account_id = %(account_id)s",
                        account_id=SQL.identifier(alias, "id"),
                    ),
                )
            return SQL(
                """
                    COALESCE(
                        %(code_store)s->>%(active_company_root_id)s,
                        %(code_store)s->>%(account_first_company_root_id)s
                            || ' (' || %(account_first_company_name)s || ')'
                    )
                """,
                code_store=SQL.identifier(alias, "code_store"),
                active_company_root_id=str(self.env.company.root_id.id),
                account_first_company_name=SQL.identifier(
                    "account_first_company", "company_name"
                ),
                account_first_company_root_id=SQL.identifier(
                    "account_first_company", "root_company_id"
                ),
                to_flush=self._fields["code_store"],
            )
        if field_expr == "root_id":
            return SQL(
                "SUBSTRING(%(placeholder_code)s, 1, 2)",
                placeholder_code=self._field_to_sql(alias, "placeholder_code", query),
            )
        return super()._field_to_sql(alias, field_expr, query)

    @api.constrains("company_ids", "account_type")
    def _check_company_consistency(self):
        if accounts_without_company := self.filtered(
            lambda a: not a.sudo().company_ids
        ):
            raise ValidationError(
                self.env._(
                    """
Context: Save account
Database ID: %(database_id)s
Problem: The following accounts have no company assigned:
%(accounts)s
Solution: Assign at least one company to every account
""",
                    database_id=",".join(str(a.id) for a in accounts_without_company),
                    accounts="\n".join(
                        f"- {a.display_name}" for a in accounts_without_company
                    ),
                )
            )
        self.invalidate_recordset(fnames=["company_ids"])
        if self.filtered(
            lambda a: a.account_type == "asset_cash" and len(a.company_ids) > 1
        ):
            raise ValidationError(
                self.env._(
                    """
Context: Save account
Database ID: %(database_id)s
Problem: Bank and Cash accounts cannot be shared between companies
Solution: Assign a single company to this account
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )
        if "account.move.line" not in self.env:
            return
        for companies, accounts in self.grouped(lambda a: a.company_ids).items():
            if (
                self.env["account.move.line"]
                .sudo()
                .search_count(
                    [
                        ("account_id", "in", accounts.ids),
                        "!",
                        ("company_id", "child_of", companies.ids),
                    ],
                    limit=1,
                )
            ):
                raise UserError(
                    self.env._(
                        """
Context: Update account companies
Database ID: %(database_id)s
Problem: This account already has journal items linked to a company being
    removed
Solution: Keep the company assigned, or first remove/reassign the journal
    items using it
""",
                        database_id=",".join(str(i) for i in accounts.ids),
                    )
                )

    @api.constrains("currency_id")
    def _check_journal_consistency(self):
        """Ensure the currency set on the journal matches the linked account.

        Trimmed from upstream: only the journal ``default_account_id`` leg
        is kept. Upstream's two extra ``account.payment.method``/
        ``account.payment.method.line`` branches are dropped -- payment
        methods are not a concept anywhere in this repo's planned scope.
        Guarded on ``account.journal``, which does not exist in this repo
        yet (lands together with journals in a later unit).
        """
        if not self or "account.journal" not in self.env:
            return
        self.env["account.account"].flush_model(["currency_id"])
        self.env["account.journal"].flush_model(["currency_id", "default_account_id"])
        self.env.cr.execute(
            """
            SELECT account.id, journal.id
              FROM account_journal journal
              JOIN res_company company ON company.id = journal.company_id
              JOIN account_account account ON account.id = journal.default_account_id
             WHERE journal.currency_id IS NOT NULL
               AND journal.currency_id != company.currency_id
               AND account.currency_id != journal.currency_id
               AND account.id IN %(accounts)s
            """,
            {"accounts": tuple(self.ids)},
        )
        res = self.env.cr.fetchone()
        if res:
            account = self.env["account.account"].browse(res[0])
            journal = self.env["account.journal"].browse(res[1])
            raise ValidationError(
                self.env._(
                    """
Context: Save account
Database ID: %(database_id)s
Problem: The foreign currency set on journal '%(journal)s' and account
    '%(account)s' must be the same
Solution: Align the account currency with its journal's currency
""",
                    database_id=account.id,
                    journal=journal.display_name,
                    account=account.display_name,
                )
            )

    @api.depends_context("company")
    @api.depends("code_store")
    def _compute_code(self):
        for record, record_root in zip(
            self, self.with_company(self.env.company.root_id).sudo(), strict=False
        ):
            record.code = record_root.code_store

    def _search_code(self, operator, value):
        return [
            (
                "id",
                "in",
                self.with_company(self.env.company.root_id)
                .with_context(active_test=False)
                .sudo()
                ._search([("code_store", operator, value)]),
            )
        ]

    def _inverse_code(self):
        for record, record_root in zip(
            self, self.with_company(self.env.company.root_id).sudo(), strict=False
        ):
            record_root.code_store = record.code
        self.invalidate_recordset(fnames=["code"], flush=False)
        self._compute_code()

    @api.depends_context("company")
    @api.depends("code")
    def _compute_placeholder_code(self):
        self.placeholder_code = False
        for record in self:
            if record.code:
                record.placeholder_code = record.code
            elif authorized_companies := (
                record.company_ids
                & self.env["res.company"].browse(self.env.user._get_company_ids())
            ).sorted("id"):
                company = authorized_companies[0]
                if code := record.with_company(company).code:
                    record.placeholder_code = f"{code} ({company.name})"

    def _search_placeholder_code(self, operator, value):
        if operator not in ("=ilike", "in"):
            return NotImplemented
        query = Query(self.env, self._table)
        placeholder_code_sql = self.env["account.account"]._field_to_sql(
            self._table, "placeholder_code", query
        )
        if operator == "in":
            query.add_where(SQL("%s IN %s", placeholder_code_sql, tuple(value)))
        else:
            query.add_where(SQL("%s ILIKE %s", placeholder_code_sql, value))
        return [("id", "in", query)]

    @api.depends_context("company")
    @api.depends("code")
    def _compute_account_root(self):
        for record in self:
            record.root_id = self.env["account.root"]._from_account_code(
                record.placeholder_code
            )

    def _search_account_root(self, operator, value):
        if operator not in ("in", "child_of", "any"):
            return NotImplemented
        if operator == "any":
            if (
                isinstance(value, Domain)
                and value.field_expr == "display_name"
                and value.operator == "in"
            ):
                roots = self.env["account.root"].browse(value.value)
            else:
                return NotImplemented
        else:
            roots = self.env["account.root"].browse(value)
        return Domain.OR(
            Domain(
                "placeholder_code",
                "=ilike",
                root.name
                + ("" if operator in ["in", "any"] and not root.parent_id else "%"),
            )
            for root in roots
        )

    def _search_panel_domain_image(
        self, field_name, domain, set_count=False, limit=False
    ):
        if field_name != "root_id" or set_count:
            return super()._search_panel_domain_image(
                field_name, domain, set_count, limit
            )
        domain = Domain(domain)
        if domain.is_false():
            return {}
        query_account = self.env["account.account"]._search(domain, limit=limit)
        code_alias = self.env["account.account"]._field_to_sql(
            self._table, "code", query_account
        )
        codes = self.env.execute_query(query_account.select(code_alias))
        return {
            (root := self.env["account.root"]._from_account_code(code)).id: {
                "id": root.id,
                "display_name": root.display_name,
            }
            for (code,) in codes
            if code
        }

    @api.depends_context("company")
    @api.depends("code")
    def _compute_account_group(self):
        accounts_with_code = self.filtered(lambda a: a.code)
        (self - accounts_with_code).group_id = False
        if not accounts_with_code:
            return
        codes = accounts_with_code.mapped("code")
        account_code_values = SQL(",".join(["(%s)"] * len(codes)), *codes)
        start_len = SQL("char_length(agroup.code_prefix_start)")
        end_len = SQL("char_length(agroup.code_prefix_end)")
        results = self.env.execute_query(
            SQL(
                """
                SELECT DISTINCT ON (account_code.code)
                       account_code.code,
                       agroup.id AS group_id
                  FROM (VALUES %(account_code_values)s) AS account_code (code)
             LEFT JOIN account_group agroup
                    ON agroup.code_prefix_start
                       <= LEFT(account_code.code, %(start_len)s)
                   AND agroup.code_prefix_end
                       >= LEFT(account_code.code, %(end_len)s)
                   AND agroup.company_id = %(root_company_id)s
              ORDER BY account_code.code, %(start_len)s DESC, agroup.id
                """,
                account_code_values=account_code_values,
                start_len=start_len,
                end_len=end_len,
                root_company_id=self.env.company.root_id.id,
            )
        )
        group_by_code = dict(results)
        for account in accounts_with_code:
            account.group_id = group_by_code[account.code]

    def _get_used_account_ids(self):
        if "account.move.line" not in self.env:
            return []
        rows = self.env.execute_query(
            SQL(
                """
                SELECT acc.id FROM account_account acc
                WHERE EXISTS (
                    SELECT 1 FROM account_move_line aml
                    WHERE aml.account_id = acc.id LIMIT 1
                )
                """
            )
        )
        return [r[0] for r in rows]

    def _search_used(self, operator, value):
        if operator not in ("in", "not in"):
            return NotImplemented
        return [("id", operator, self._get_used_account_ids())]

    def _compute_used(self):
        ids = set(self._get_used_account_ids())
        for record in self:
            record.used = record.id in ids

    @api.model
    def _search_new_account_code(self, start_code, cache=None):
        """Get an available account code for the active company.

        Kept verbatim from upstream so a future, separate chart-template
        module (``ssi_accounting_coa_id``, out of this issue's scope) can
        reuse it to auto-number accounts on import, without having to
        reimplement this logic.

        :param str start_code: the code to increment until an available
            one is found.
        :param set[str] cache: codes already known to be used (optional).
        :return: an available new account code for the active company.
        """
        if cache is None:
            cache = {start_code}

        def code_is_available(new_code):
            return new_code not in cache and not self.with_context(
                active_test=False
            ).sudo().search_count(
                [
                    ("code", "=", new_code),
                    "|",
                    ("company_ids", "parent_of", self.env.company.id),
                    ("company_ids", "child_of", self.env.company.id),
                ],
                limit=1,
            )

        if code_is_available(start_code):
            return start_code

        start_str, digits_str, end_str = ACCOUNT_CODE_NUMBER_REGEX.match(
            start_code
        ).groups()

        if digits_str != "":
            d, n = len(digits_str), int(digits_str)
            for num in range(n + 1, 10**d):
                new_code = f"{start_str}{num:0{d}}{end_str}"
                if code_is_available(new_code):
                    return new_code

        for num in range(99):
            new_code = f"{start_code}.copy{num and num + 1 or ''}"
            if code_is_available(new_code):
                return new_code

        raise UserError(
            self.env._(
                """
Context: Generate new account code
Database ID: %(database_id)s
Problem: Cannot generate an unused account code from %(start_code)s
Solution: Set the account code manually
""",
                database_id=",".join(str(i) for i in self.ids),
                start_code=start_code,
            )
        )

    @api.depends_context("company")
    def _compute_current_balance(self):
        if "account.move.line" not in self.env:
            self.current_balance = 0
            return
        balances = {
            account.id: balance
            for account, balance in self.env["account.move.line"]._read_group(
                domain=[
                    ("account_id", "in", self.ids),
                    ("parent_state", "=", "posted"),
                    ("company_id", "child_of", self.env.company.id),
                ],
                groupby=["account_id"],
                aggregates=["balance:sum"],
            )
        }
        for record in self:
            record.current_balance = balances.get(record.id, 0)

    @api.depends_context("company")
    def _compute_related_taxes_amount(self):
        if "tax" not in self.env:
            self.related_taxes_amount = 0
            return
        for record in self:
            record.related_taxes_amount = self.env["tax"].search_count(
                [
                    *self.env["tax"]._check_company_domain(self.env.company),
                    ("repartition_line_ids.account_id", "in", record.ids),
                ]
            )

    @api.depends_context("company")
    def _compute_company_currency_id(self):
        self.company_currency_id = self.env.company.currency_id

    def _get_internal_group(self, account_type):
        return account_type.split("_", maxsplit=1)[0]

    @api.depends("account_type")
    def _compute_internal_group(self):
        for account in self:
            account.internal_group = (
                account.account_type
                and account._get_internal_group(account.account_type)
            )

    def _search_internal_group(self, operator, value):
        if operator != "in":
            return NotImplemented
        return Domain.OR(
            Domain("account_type", "=like", self._get_internal_group(v) + "%")
            for v in value
        )

    @api.depends("account_type")
    def _compute_reconcile(self):
        for account in self:
            if account.internal_group in ("income", "expense", "equity"):
                account.reconcile = False
            elif account.account_type in ("asset_receivable", "liability_payable"):
                account.reconcile = True
            elif account.account_type in (
                "asset_cash",
                "liability_credit_card",
                "off_balance",
            ):
                account.reconcile = False

    @api.depends("account_type")
    def _compute_include_initial_balance(self):
        for account in self:
            account.include_initial_balance = (
                account.internal_group not in ("income", "expense")
                and account.account_type != "equity_unaffected"
            )

    def _search_include_initial_balance(self, operator, value):
        if operator != "in":
            return NotImplemented
        return [
            ("internal_group", "not in", ["income", "expense"]),
            ("account_type", "!=", "equity_unaffected"),
        ]

    @api.model
    def default_get(self, fields_list):
        """If typing a code into the name field, swap it into 'code'."""
        context = {}
        if "name" in fields_list or "code" in fields_list:
            default_name = self.env.context.get("default_name")
            default_code = self.env.context.get("default_code")
            if default_name and not default_code:
                with contextlib.suppress(ValueError):
                    default_code = int(default_name)
                if default_code:
                    default_name = False
                context.update(
                    {"default_name": default_name, "default_code": default_code}
                )
        defaults = super(AccountAccount, self.with_context(**context)).default_get(
            fields_list
        )
        if "code_mapping_ids" in fields_list and "code_mapping_ids" not in defaults:
            defaults["code_mapping_ids"] = [
                Command.create({"company_id": c.id}) for c in self.env.user.company_ids
            ]
        return defaults

    def _split_code_name(self, code_name):
        # We only want to split the name on the first word if there is a
        # digit in it
        code, name = ACCOUNT_REGEX.match(code_name or "").groups()
        return code, name.strip()

    @api.onchange("name")
    def _onchange_name(self):
        code, name = self._split_code_name(self.name)
        if code and not self.code:
            self.name = name
            self.code = code

    @api.onchange("account_type")
    def _onchange_account_type(self):
        """Clear ``tax_ids`` for off-balance accounts.

        ``tax_ids`` is deliberately not defined in this unit (see the
        class docstring). Guarded on field presence so this keeps being a
        no-op until the tax configuration unit adds ``tax_ids`` back via
        inheritance, at which point it starts working with no further
        change needed here.
        """
        if self.account_type == "off_balance" and "tax_ids" in self._fields:
            self.tax_ids = False

    @api.depends_context("company", "formatted_display_name")
    @api.depends("code", "name")
    def _compute_display_name(self):
        for account in self:
            if account.code:
                account.display_name = f"{account.code} {account.name}"
            else:
                account.display_name = account.name

    @api.model
    def _search_display_name(self, operator, value):
        if operator in Domain.NEGATIVE_OPERATORS:
            return NotImplemented
        if operator == "in":
            names = value
            return [
                "|",
                ("code", "in", [(name or "").split(" ")[0] for name in value]),
                ("name", "in", names),
            ]
        if isinstance(value, str):
            name = value or ""
            return [
                "|",
                ("code", "=like", name.split(" ")[0] + "%"),
                ("name", operator, name),
            ]
        return NotImplemented

    def action_open_related_taxes(self):
        self.ensure_one()
        if "tax" not in self.env:
            return {"type": "ir.actions.act_window_close"}
        related_tax_ids = (
            self.env["tax"]
            .search([("repartition_line_ids.account_id", "=", self.id)])
            .ids
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Taxes"),
            "res_model": "tax",
            "views": [[False, "list"], [False, "form"]],
            "domain": [("id", "in", related_tax_ids)],
        }

    @api.model_create_multi
    def create(self, vals_list):
        records_list = []
        # `company_ids` raw values are plain lists (Command tuples/ids), which
        # are not hashable -- `odoo.tools.groupby`'s dict-based grouping would
        # raise TypeError on them. `itertools.groupby` only compares
        # consecutive keys with `==`, so it stays correct here; this mirrors
        # upstream Odoo's own choice for the same reason.
        for (  # pylint: disable=bad-builtin-groupby
            company_ids,
            vals_list_for_company,
        ) in itertools.groupby(vals_list, lambda v: v.get("company_ids", [])):
            vals_list_for_company = list(vals_list_for_company)
            company_ids = self._fields["company_ids"].convert_to_cache(
                company_ids, self.browse()
            )
            companies = self.env["res.company"].browse(company_ids)
            if self.env.company in companies or not companies:
                companies = self.env.company | companies

            new_accounts = super(
                AccountAccount,
                self.with_context(
                    allowed_company_ids=companies.ids,
                    defer_account_code_checks=True,
                    default_code_mapping_ids=self.env.context.get(
                        "default_code_mapping_ids", []
                    ),
                ),
            ).create(vals_list_for_company)
            records_list.append(new_accounts)

        records = self.env["account.account"].union(*records_list)
        records._ensure_code_is_unique()
        return records

    def write(self, vals):
        if "reconcile" in vals:
            if vals["reconcile"]:
                self.filtered(lambda r: not r.reconcile)._toggle_reconcile_to_true()
            else:
                self.filtered(lambda r: r.reconcile)._toggle_reconcile_to_false()

        if vals.get("currency_id") and "account.move.line" in self.env:
            for account in self:
                if self.env["account.move.line"].search_count(
                    [
                        ("account_id", "=", account.id),
                        ("currency_id", "not in", (False, vals["currency_id"])),
                    ]
                ):
                    raise UserError(
                        self.env._(
                            """
Context: Update account currency
Database ID: %(database_id)s
Problem: This account already has journal entries in a different foreign
    currency
Solution: Keep the current currency, or first reassign the existing journal
    items
""",
                            database_id=account.id,
                        )
                    )

        res = super(
            AccountAccount,
            self.with_context(
                defer_account_code_checks=True,
                prefetch_fields=not any(f in vals for f in ["code", "account_type"]),
            ),
        ).write(vals)

        if (
            not self.env.context.get("defer_account_code_checks")
            and {
                "company_ids",
                "code",
                "code_mapping_ids",
            }
            & vals.keys()
        ):
            if "company_ids" in vals:
                self.invalidate_recordset(fnames=["company_ids"])
            self._ensure_code_is_unique()

        return res

    def _ensure_code_is_unique(self):
        """Check account codes per company.

        1. The code must be set for each of the account's companies.
        2. No child or parent company may have another account with the
           same code (same definition of availability as
           ``_search_new_account_code``; keep both in sync).
        """
        for account in self.sudo():
            for company in account.company_ids.root_id:
                if not account.with_company(company).code:
                    raise ValidationError(
                        self.env._(
                            """
Context: Save account
Database ID: %(database_id)s
Problem: The code must be set for company %(company)s
Solution: Set a code for every company this account belongs to
""",
                            database_id=account.id,
                            company=company.name,
                        )
                    )

        account_ids_to_check_by_company = defaultdict(list)
        for account in self.sudo():
            for company in account.company_ids:
                account_ids_to_check_by_company[company].append(account.id)

        for company, account_ids in account_ids_to_check_by_company.items():
            accounts = self.browse(account_ids).with_prefetch(self.ids).sudo()

            accounts_by_code = accounts.with_company(company).grouped("code")
            duplicate_codes = None
            if len(accounts_by_code) < len(accounts):
                duplicate_codes = [
                    code for code, recs in accounts_by_code.items() if len(recs) > 1
                ]
            elif (
                duplicates := self.with_company(company)
                .sudo()
                .with_context(active_test=False)
                .search_fetch(
                    [
                        ("code", "in", list(accounts_by_code)),
                        ("id", "not in", self.ids),
                        "|",
                        ("company_ids", "parent_of", company.ids),
                        ("company_ids", "child_of", company.ids),
                    ],
                    ["code_store"],
                )
            ):
                duplicate_codes = duplicates.mapped("code")
            if duplicate_codes:
                raise ValidationError(
                    self.env._(
                        """
Context: Save account
Database ID: %(database_id)s
Problem: Duplicate account code(s) in company %(company)s: %(codes)s
Solution: Choose a unique code for each account within the company
""",
                        database_id=",".join(str(i) for i in account_ids),
                        company=company.name,
                        codes=", ".join(duplicate_codes),
                    )
                )

    def _toggle_reconcile_to_true(self):
        """Toggle 'reconcile' False -> True.

        Lines with debit = credit = amount_currency = 0 are set reconciled.
        Guarded on ``account.move.line``, which does not exist yet.
        """
        if not self.ids or "account.move.line" not in self.env:
            return None
        self.env["account.move.line"].invalidate_model(
            ["amount_residual", "amount_residual_currency", "reconciled"]
        )
        query = """
            UPDATE account_move_line SET
                reconciled = CASE WHEN debit = 0 AND credit = 0 AND amount_currency = 0
                    THEN true ELSE false END,
                amount_residual = (debit-credit),
                amount_residual_currency = amount_currency
            WHERE full_reconcile_id IS NULL and account_id IN %s
        """
        self.env.cr.execute(query, [tuple(self.ids)])

    def _toggle_reconcile_to_false(self):
        """Toggle 'reconcile' True -> False.

        Disallowed if some lines are partially reconciled. Guarded on
        ``account.move.line``, which does not exist yet.
        """
        if not self.ids or "account.move.line" not in self.env:
            return None
        partial_lines_count = self.env["account.move.line"].search_count(
            [
                ("account_id", "in", self.ids),
                ("full_reconcile_id", "=", False),
                "|",
                ("matched_debit_ids", "!=", False),
                ("matched_credit_ids", "!=", False),
            ]
        )
        if partial_lines_count > 0:
            raise UserError(
                self.env._(
                    """
Context: Disable reconciliation on account
Database ID: %(database_id)s
Problem: Some journal items on this account are still partially reconciled
Solution: Fully reconcile or unreconcile those items first
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )

        self.env["account.move.line"].invalidate_model(
            ["amount_residual", "amount_residual_currency"]
        )
        query = """
            UPDATE account_move_line
                SET amount_residual = 0, amount_residual_currency = 0
            WHERE full_reconcile_id IS NULL AND account_id IN %s
        """
        self.env.cr.execute(query, [tuple(self.ids)])

    @api.ondelete(at_uninstall=False)
    def _unlink_except_contains_journal_items(self):
        if "account.move.line" not in self.env:
            return
        if (
            self.env["account.move.line"]
            .sudo()
            .search_count([("account_id", "in", self.ids)], limit=1)
        ):
            raise UserError(
                self.env._(
                    """
Context: Delete account
Database ID: %(database_id)s
Problem: This account contains journal items
Solution: Remove or reassign the journal items before deleting the account
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_linked_to_tax_repartition_line(self):
        if "tax.repartition.line" not in self.env:
            return
        if self.env["tax.repartition.line"].search_count(
            [("account_id", "in", self.ids)], limit=1
        ):
            raise UserError(
                self.env._(
                    """
Context: Delete account
Database ID: %(database_id)s
Problem: This account is set on the account mapping of a tax repartition
    line
Solution: Update the tax repartition line first, then delete the account
""",
                    database_id=",".join(str(i) for i in self.ids),
                )
            )
