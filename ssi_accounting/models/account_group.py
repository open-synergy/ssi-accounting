# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountGroup(models.Model):
    """Hierarchical grouping of accounts by code prefix range.

    Ported (behaviour-wise) from Odoo core
    ``addons/account/models/account_account.py`` (class ``AccountGroup``,
    model ``account.group``), because that model lives inside the
    ``account`` module, which ``ssi_accounting`` must not depend on.

    ``_adapt_accounts_for_account_groups`` is the method that keeps
    ``account.account.group_id`` in sync with the group hierarchy whenever
    a group's code prefix range changes. The ``account`` model itself is
    out of scope for this unit (it lands in a later one), so the method
    guards for its absence and is a no-op until then -- it starts doing
    real work the moment ``account.account`` is registered, with no
    further change needed here.
    """

    _name = "account.group"
    _description = "Account Group"
    _order = "code_prefix_start"
    _parent_store = True

    parent_id = fields.Many2one(
        string="Parent",
        comodel_name="account.group",
        index=True,
        ondelete="cascade",
        readonly=True,
        help="Closest account group whose code prefix range fully "
        "contains this group's range. Recomputed automatically whenever "
        "a code prefix changes.",
    )
    parent_path = fields.Char(
        index=True,
        help="Materialized path used to browse the group hierarchy "
        "efficiently. Maintained automatically by the ORM.",
    )
    name = fields.Char(
        required=True,
        translate=True,
        help="Label of the account group, shown together with its code prefix range.",
    )
    code_prefix_start = fields.Char(
        string="From Code Prefix",
        compute="_compute_code_prefix_start",
        readonly=False,
        store=True,
        precompute=True,
        help="Starting account code prefix covered by this group. "
        "Defaults to the ending prefix when left empty.",
    )
    code_prefix_end = fields.Char(
        string="To Code Prefix",
        compute="_compute_code_prefix_end",
        readonly=False,
        store=True,
        precompute=True,
        help="Ending account code prefix covered by this group. "
        "Defaults to the starting prefix when left empty.",
    )
    company_id = fields.Many2one(
        string="Company",
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
        help="Company that owns this account group. Every account group "
        "is strictly scoped to one company.",
    )

    @api.depends("code_prefix_start")
    def _compute_code_prefix_end(self):
        for group in self:
            if not group.code_prefix_end or (
                group.code_prefix_start
                and group.code_prefix_end < group.code_prefix_start
            ):
                group.code_prefix_end = group.code_prefix_start

    @api.depends("code_prefix_end")
    def _compute_code_prefix_start(self):
        for group in self:
            if not group.code_prefix_start or (
                group.code_prefix_end
                and group.code_prefix_start > group.code_prefix_end
            ):
                group.code_prefix_start = group.code_prefix_end

    @api.constrains("code_prefix_start", "code_prefix_end")
    def _check_code_prefix_length(self):
        for group in self.sudo():
            if not group._check_code_prefix_length_condition():
                raise ValidationError(
                    group.env._(
                        """
Context: Save account group
Database ID: %(database_id)s
Problem: Starting code prefix "%(start)s" and ending code prefix
    "%(end)s" do not have the same length
Solution: Make sure both code prefixes have the same number of
    characters
""",
                        database_id=group.id,
                        start=group.code_prefix_start,
                        end=group.code_prefix_end,
                    )
                )

    def _check_code_prefix_length_condition(self):
        self.ensure_one()
        start_length = len(self.code_prefix_start or "")
        end_length = len(self.code_prefix_end or "")
        return start_length == end_length

    @api.depends("code_prefix_start", "code_prefix_end", "name")
    def _compute_display_name(self):
        for group in self:
            prefix = group.code_prefix_start and str(group.code_prefix_start)
            if prefix and group.code_prefix_end != group.code_prefix_start:
                prefix += "-" + str(group.code_prefix_end)
            group.display_name = " ".join(filter(None, [prefix, group.name]))

    @api.constrains("code_prefix_start", "code_prefix_end")
    def _constraint_prefix_overlap(self):
        self.flush_model()
        for group in self.sudo():
            if not group._constraint_prefix_overlap_condition():
                raise ValidationError(
                    group.env._(
                        """
Context: Save account group
Database ID: %(database_id)s
Problem: Code prefix range %(start)s-%(end)s overlaps with another
    account group of the same length in the same company
Solution: Adjust the code prefix range so it does not overlap with an
    existing account group
""",
                        database_id=group.id,
                        start=group.code_prefix_start,
                        end=group.code_prefix_end,
                    )
                )

    def _constraint_prefix_overlap_condition(self):
        self.ensure_one()
        query = """
            SELECT other.id FROM account_group this
            JOIN account_group other
              ON char_length(other.code_prefix_start)
                 = char_length(this.code_prefix_start)
             AND other.id != this.id
             AND other.company_id = this.company_id
             AND (
                other.code_prefix_start <= this.code_prefix_start
                AND this.code_prefix_start <= other.code_prefix_end
                OR
                other.code_prefix_start >= this.code_prefix_start
                AND this.code_prefix_end >= other.code_prefix_start
            )
            WHERE this.id = %(id)s
        """
        self.env.cr.execute(query, {"id": self.id})
        return not self.env.cr.fetchall()

    def _sanitize_vals(self, vals):
        if (
            vals.get("code_prefix_start")
            and "code_prefix_end" in vals
            and not vals["code_prefix_end"]
        ):
            del vals["code_prefix_end"]
        if (
            vals.get("code_prefix_end")
            and "code_prefix_start" in vals
            and not vals["code_prefix_start"]
        ):
            del vals["code_prefix_start"]
        return vals

    @api.constrains("parent_id")
    def _check_parent_not_circular(self):
        for group in self.sudo():
            if not group._check_parent_not_circular_condition():
                raise ValidationError(
                    group.env._(
                        """
Context: Save account group
Database ID: %(database_id)s
Problem: The parent group hierarchy is recursive
Solution: Pick a parent group that is not one of this group's own
    descendants
""",
                        database_id=group.id,
                    )
                )

    def _check_parent_not_circular_condition(self):
        self.ensure_one()
        return not self._has_cycle()

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create([self._sanitize_vals(vals) for vals in vals_list])
        groups._adapt_accounts_for_account_groups()
        groups._adapt_parent_account_group()
        return groups

    def write(self, vals):
        res = super().write(self._sanitize_vals(vals))
        if "code_prefix_start" in vals or "code_prefix_end" in vals:
            self._adapt_accounts_for_account_groups()
            self._adapt_parent_account_group()
        return res

    def unlink(self):
        for record in self:
            if "account.account" in self.env:
                account_ids = self.env["account.account"].search(
                    [("group_id", "=", record.id)]
                )
                account_ids.write({"group_id": record.parent_id.id})
            children_ids = self.env["account.group"].search(
                [("parent_id", "=", record.id)]
            )
            children_ids.write({"parent_id": record.parent_id.id})
        return super().unlink()

    def _adapt_accounts_for_account_groups(self, account_ids=None):
        """Keep ``account.account.group_id`` consistent with this group.

        Finds and sets the most specific group matching the code of each
        account: the one with the longest prefixes whose range contains
        the account's code. No-op while ``account.account`` is not
        registered yet -- see the class docstring.
        """
        if "account.account" not in self.env:
            return
        company_ids = account_ids.company_id.ids if account_ids else self.company_id.ids
        account_ids = account_ids.ids if account_ids else []
        if not company_ids and not account_ids:
            return
        self.env["account.group"].flush_model()
        self.env["account.account"].flush_model()

        account_where_clause = ""
        where_params = [tuple(company_ids)]
        if account_ids:
            account_where_clause = "AND account.id IN %s"
            where_params.append(tuple(account_ids))

        self._cr.execute(
            f"""
            WITH candidates_account_groups AS (
                SELECT
                    account.id AS account_id,
                    ARRAY_AGG(
                        agroup.id
                        ORDER BY char_length(agroup.code_prefix_start) DESC,
                                 agroup.id
                    ) AS group_ids
                FROM account_account account
                LEFT JOIN account_group agroup
                    ON agroup.code_prefix_start
                       <= LEFT(account.code, char_length(agroup.code_prefix_start))
                    AND agroup.code_prefix_end
                        >= LEFT(account.code, char_length(agroup.code_prefix_end))
                    AND agroup.company_id = account.company_id
                WHERE account.company_id IN %s {account_where_clause}
                GROUP BY account.id
            )
            UPDATE account_account
            SET group_id = rel.group_ids[1]
            FROM candidates_account_groups rel
            WHERE account_account.id = rel.account_id
            """,
            where_params,
        )
        self.env["account.account"].invalidate_model(["group_id"])

    def _adapt_parent_account_group(self):
        """Ensure consistency of the hierarchy of account groups.

        Finds and sets the most specific parent for each group: the one
        with the longest prefixes whose range fully contains the child's.
        """
        if not self:
            return
        self.env["account.group"].flush_model()
        query = """
            WITH relation AS (
                SELECT DISTINCT FIRST_VALUE(parent.id) OVER (
                           PARTITION BY child.id
                           ORDER BY child.id,
                                    char_length(parent.code_prefix_start) DESC
                       ) AS parent_id,
                       child.id AS child_id
                  FROM account_group parent
                  JOIN account_group child
                    ON char_length(parent.code_prefix_start)
                       < char_length(child.code_prefix_start)
                   AND parent.code_prefix_start
                       <= LEFT(child.code_prefix_start,
                               char_length(parent.code_prefix_start))
                   AND parent.code_prefix_end
                       >= LEFT(child.code_prefix_end,
                               char_length(parent.code_prefix_end))
                   AND parent.id != child.id
                   AND parent.company_id = child.company_id
                 WHERE child.company_id IN %(company_ids)s
            )
            UPDATE account_group child
               SET parent_id = relation.parent_id
              FROM relation
             WHERE child.id = relation.child_id;
        """
        self.env.cr.execute(query, {"company_ids": tuple(self.company_id.ids)})
        self.env["account.group"].invalidate_model(["parent_id"])
        self.env["account.group"].search(
            [("company_id", "in", self.company_id.ids)]
        )._parent_store_update()
