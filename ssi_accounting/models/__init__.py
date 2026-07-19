# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from . import (
    account,
    account_code_mapping,
    account_group,
    account_root,
    account_tag,
    journal,
    # 'mixin_sequence_number' MUST be imported before any model that
    # inherits 'mixin.sequence_number' ('journal_entry' below) -- Odoo
    # resolves '_inherit' against models already registered at that
    # point in module-import order, not by dependency graph.
    mixin_sequence_number,
    journal_entry,
    journal_entry_item,
    journal_group,
    reconcile_full,
    reconcile_partial,
    res_company,
    res_currency,
    tax,
    tax_group,
    tax_repartition_line,
    test_mixin_sequence_number,
)
