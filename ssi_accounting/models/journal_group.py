# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields, models


class AccountJournalGroup(models.Model):
    """Named subset of journals, used to filter journals in list/reports.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_journal.py`` (class
    ``AccountJournalGroup``, model ``account.journal.group``), because
    that model lives inside the ``account`` module, which
    ``ssi_accounting`` must not depend on.

    Unlike upstream, ``company_id`` defaults to ``env.company`` -- not
    ``env.company.root_id`` -- per this issue's design decision: this
    repo has not built out any parent/root company cascading for
    accounting configuration models, so scoping a new journal group to
    the exact active company keeps the behaviour predictable.
    """

    _name = "account.journal.group"
    _description = "Journal Group"
    _check_company_auto = True

    _name_company_uniq = models.Constraint(
        "UNIQUE (name, company_id)",
        "Another journal group with that name already exists in this company.",
    )

    name = fields.Char(
        required=True,
        translate=True,
        help="Label of the journal group, used to filter journals by "
        "group in the journal list and in reports.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
        help="Company that owns this journal group.",
    )
    excluded_journal_ids = fields.Many2many(
        comodel_name="account.journal",
        string="Excluded Journals",
        domain="[('company_id', '=', company_id)]",
        check_company=True,
        help="Journals hidden from view whenever this group is the "
        "active journal filter.",
    )
    sequence = fields.Integer(
        default=10,
        help="Used to order journal groups when filtering journals.",
    )
