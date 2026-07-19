# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Accounting",
    "version": "19.0.1.2.0",
    "website": "https://simetri-sinergi.id",
    "author": "OpenSynergy Indonesia, PT. Simetri Sinergi Indonesia",
    "contributors": [
        "Andhitia Rama <andhitia.r@gmail.com>",
    ],
    "license": "AGPL-3",
    "installable": True,
    "application": True,
    "depends": [
        "base",
        "mail",
        "product",
        "ssi_master_data_mixin",
    ],
    "data": [
        "security/ssi_accounting_security.xml",
        "security/ir.model.access.csv",
        "security/ir_rule/account_account.xml",
        "security/ir_rule/account_group.xml",
        "security/ir_rule/journal.xml",
        "security/ir_rule/journal_group.xml",
        "menu.xml",
        "views/account_views.xml",
        "views/account_group_views.xml",
        "views/account_tag_views.xml",
        "views/journal_views.xml",
        "views/journal_group_views.xml",
        "views/res_company_views.xml",
    ],
}
