# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.tools import format_list
from odoo.tools.misc import format_date

MONTH_SELECTION = [
    ("1", "January"),
    ("2", "February"),
    ("3", "March"),
    ("4", "April"),
    ("5", "May"),
    ("6", "June"),
    ("7", "July"),
    ("8", "August"),
    ("9", "September"),
    ("10", "October"),
    ("11", "November"),
    ("12", "December"),
]


class ResCompany(models.Model):
    """Phase-1 accounting configuration fields on the company record.

    Adapted (field-wise) from Odoo core
    ``addons/account/models/company.py`` (class ``ResCompany``), because
    that extension lives inside the ``account`` module, which
    ``ssi_accounting`` must not depend on. Only the fields needed by the
    tax engine and by the chart-of-accounts models added in this unit are
    carried over here.

    Lock dates are deliberately reduced to two of upstream's five
    (``fiscalyear_lock_date`` and ``tax_lock_date``): ``sale_lock_date``,
    ``purchase_lock_date`` and ``hard_lock_date`` exist upstream to lock
    sale/purchase documents and to provide an irreversible hard lock, but
    this repo has no sale/purchase documents and no hard-lock requirement
    yet. ``_get_lock_date_violations``/``_format_lock_dates`` below are
    shrunk to match: they only ever check these two dates, and they skip
    upstream's per-user exception framework (``account.lock_exception``),
    which does not exist in this repo's scope.
    """

    _inherit = "res.company"

    account_price_include = fields.Selection(
        string="Default Sales Price Include",
        selection=[
            ("tax_included", "Tax Included"),
            ("tax_excluded", "Tax Excluded"),
        ],
        default="tax_excluded",
        required=True,
        help="Default on whether the sales price used on products and "
        "invoices of this company includes its taxes.",
    )
    tax_calculation_rounding_method = fields.Selection(
        selection=[
            ("round_per_line", "Round per Line"),
            ("round_globally", "Round per Tax"),
        ],
        default="round_per_line",
        required=True,
        help="Whether the tax amount is rounded on each line, or only "
        "once for the whole document.",
    )
    fiscalyear_last_day = fields.Integer(
        string="Fiscal Year Last Day",
        default=31,
        required=True,
        help="Day of the month the fiscal year ends on, combined with "
        "Fiscal Year Last Month.",
    )
    fiscalyear_last_month = fields.Selection(
        string="Fiscal Year Last Month",
        selection=MONTH_SELECTION,
        default="12",
        required=True,
        help="Month the fiscal year ends on, combined with Fiscal Year Last Day.",
    )
    fiscalyear_lock_date = fields.Date(
        string="Global Lock Date",
        help="Any entry up to and including that date will be postponed "
        "to a later time, in accordance with its journal's sequence.",
    )
    tax_lock_date = fields.Date(
        string="Tax Return Lock Date",
        help="Any entry with taxes up to and including that date will be "
        "postponed to a later time, in accordance with its journal's "
        "sequence.",
    )
    currency_exchange_journal_id = fields.Many2one(
        string="Exchange Difference Journal",
        comodel_name="account.journal",
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
        help="Journal used to post automatic currency exchange difference entries.",
    )
    income_currency_exchange_account_id = fields.Many2one(
        string="Gain Exchange Rate Account",
        comodel_name="account.account",
        help="Account used to post the gain when reconciling entries "
        "in different currencies.",
    )
    expense_currency_exchange_account_id = fields.Many2one(
        string="Loss Exchange Rate Account",
        comodel_name="account.account",
        help="Account used to post the loss when reconciling entries "
        "in different currencies.",
    )
    account_journal_suspense_account_id = fields.Many2one(
        string="Journal Suspense Account",
        comodel_name="account.account",
        help="Default suspense account proposed on new journals of this company.",
    )

    def _get_lock_date_violations(self, accounting_date, fiscalyear=True, tax=True):
        """Get the lock dates affecting ``accounting_date``.

        Shrunk from upstream to the two lock dates this company record
        exposes (see class docstring) -- no per-user exception lookup, no
        parent-company cascading.

        :param accounting_date: the accounting date to check.
        :param bool fiscalyear: whether to check ``fiscalyear_lock_date``.
        :param bool tax: whether to check ``tax_lock_date``.
        :return: a list of ``(lock_date, lock_date_field)`` tuples, not
            ordered chronologically.
        """
        self.ensure_one()
        locks = []

        if not accounting_date:
            return locks

        lock_date_fields = [
            ("fiscalyear_lock_date", fiscalyear),
            ("tax_lock_date", tax),
        ]
        for field, to_check in lock_date_fields:
            if not to_check:
                continue
            lock_date = self[field]
            if lock_date and accounting_date <= lock_date:
                locks.append((lock_date, field))

        return locks

    @api.model
    def _format_lock_dates(self, lock_dates):
        """Format a list of lock dates as a human-readable string.

        :param lock_dates: list of ``(lock_date, lock_date_field)`` tuples.
        :return: a (localized) string listing all the lock date fields
            and their values.
        """
        return format_list(
            self.env,
            [
                f"{self.fields_get([field])[field]['string']} "
                f"({format_date(self.env, lock_date)})"
                for lock_date, field in sorted(lock_dates)
            ],
        )
