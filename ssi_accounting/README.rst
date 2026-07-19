.. image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
   :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
   :alt: License: AGPL-3

==========
Accounting
==========

Core accounting module for PT. Simetri Sinergi Indonesia (SSI). This module builds a
pure journal entry accounting core, independent of Odoo's native ``account`` module —
it does not depend on, and will never depend on, the ``account`` addon.

This module lays down the module skeleton, its own security groups, the
chart-of-accounts support models (``account.root``, ``account.group``,
``account.tag``), phase-1 accounting configuration on ``res.company``, a
deactivation guard on ``res.currency``, and the chart of accounts itself:
``account.account`` (Odoo 19 style multi-company, with a Chart of Accounts menu)
and its companion ``account.code.mapping``. It also has ``account.journal``/
``account.journal.group``, and the tax configuration models ``tax_group``,
``tax`` and its child ``tax.repartition_line``. Journal entries and the tax
computation engine are added incrementally by later units.


Design decisions
=================

* This module implements SSI's accounting core as journal-entry based models,
  completely independent from Odoo's native ``account`` module. The ``account``
  addon is **never** added to ``depends`` — not now, nor in any future unit.
* Because this module does not depend on ``account``, it cannot reuse
  ``account.group_account_user`` / ``account.group_account_manager`` — those groups
  will never exist here. This module therefore owns its own security groups:

  - ``group_accounting_user`` — implies ``base.group_user``. Accountants need this
    group to create and post journal entries.
  - ``group_accounting_manager`` — implies ``group_accounting_user``. Grants full
    access to the accounting configuration models: ``account.group``,
    ``account.tag``, ``account.journal``, ``account.journal.group``,
    ``tax_group``, ``tax`` (and its child ``tax.repartition_line``).

  Both groups belong to the ``Accounting`` module category.
* ``account.root``, ``account.group`` are ported (behaviour-wise) from Odoo core's
  ``account`` module, since ``ssi_accounting`` cannot depend on it. They keep their
  upstream ``_name`` (dotted, e.g. ``account.group``) rather than the SSI
  underscore-only model naming convention, so that any future code written against
  vanilla Odoo's chart-of-accounts API keeps working unmodified against this module.
  ``account.root`` is a computed, tableless model (``_auto = False``) and therefore
  has no ``ir.rule`` and no menu entry. It still gets one read-only
  ``ir.model.access`` row (matching what upstream Odoo ships for it), because
  Odoo's module loader logs a warning -- treated as a CI failure by this repo's
  strict log checking -- for any model with zero access rules.
* ``account.tag`` is a **renamed** port of upstream ``account.account.tag``
  (renamed to follow the same short naming convention as ``account.root`` /
  ``account.group``), with ``applicability`` reduced to ``accounts``/``taxes``
  (upstream's ``products`` value is dropped — no product-tax relationship in this
  repo's scope), and with ``report_expression_id``/``balance_negate`` and the whole
  ``account.report`` integration dropped entirely. **Consequence:** once
  ``tax.repartition_line.tag_ids`` exists in a later unit, it will be a plain grid
  tag without any report-derived sign semantics — the "+"/"-" prefix shown by
  ``display_name`` for tax tags is a self-contained heuristic (based on whether the
  tag's own name already starts with a sign), not derived from a report expression.
* ``account.account`` is ported (behaviour-wise) from Odoo core's ``account.account``,
  keeping its upstream dotted name (not shortened like ``account.tag``) because
  ``account.group._adapt_accounts_for_account_groups`` already hardcodes
  ``"account.account"``/``account_account`` — it now becomes active for real
  (previously a no-op, see below). The Odoo 19 multi-company engine is kept
  **verbatim, not simplified**: ``company_ids`` (Many2many) backed by ``code_store``
  (a ``company_dependent`` ``Char``), the computed ``code``/``placeholder_code``
  fields, and the ``_field_to_sql`` override that turns ``code`` into a per-company
  JSONB lookup. ``account.code.mapping`` (the "Mapping" tab on the account form) is
  ported alongside it, unchanged from upstream apart from pointing at this module's
  own ``account.account``.
* ``account.account`` still does **not** have ``tax_ids``: a Many2many field
  needs a real comodel at registry-build time, so it must wait for a later unit
  to add it back via inheritance. ``_onchange_account_type`` (whose only job
  upstream is clearing ``tax_ids``) stays guarded on ``"tax_ids" in
  self._fields`` for that reason. ``related_taxes_amount``/
  ``action_open_related_taxes`` only needed the ``tax`` model itself (not
  ``tax_ids``) and now work for real, now that ``tax`` exists.
* Several ``account.account`` constraints/computes are guarded no-ops until later
  units land, exactly like ``account.group``/``res.currency`` above: ``used``,
  ``current_balance``, the reconcile-toggle guards, and the delete guard on
  ``account.move.line`` (journal items — lands with ``journal_entry``); the
  journal/account currency-mismatch check on ``account.journal`` (lands with
  ``journal``). The delete guard on ``tax.repartition_line`` now works for real,
  now that model exists (see below). Dropped entirely (out of this issue's
  scope): the opening balance
  triplet, ``non_trade``, the partner-frequency heuristics behind the invoice line
  account widget, ``name_create``, ``get_import_templates``, and the whole
  merge/unmerge suite.
* ``account.group._adapt_accounts_for_account_groups`` (kept from upstream to keep
  ``account.account.group_id`` in sync with the group hierarchy) is no longer a
  no-op now that ``account.account`` exists.
* ``res.company`` gains six phase-1 accounting fields: ``account_price_include``,
  ``tax_calculation_rounding_method``, ``fiscalyear_last_day``,
  ``fiscalyear_last_month``, ``fiscalyear_lock_date``, ``tax_lock_date``. Lock dates
  are intentionally reduced to these two (of upstream's five) — ``sale_lock_date``,
  ``purchase_lock_date`` and ``hard_lock_date`` are dropped because this repo has no
  sale/purchase documents and no hard-lock requirement (yet). Exchange difference
  fields are deliberately **not** added here — they will follow once ``journal``
  exists.
* ``res.currency`` is inherited only to add ``_has_accounting_entries()`` and a
  ``write()`` guard that refuses to deactivate a currency already used on journal
  items. Unlike upstream (which guards a rounding-precision decrease), this guard
  specifically blocks **deactivation**. It reads ``account.move.line``, which does
  not exist yet in this repo's scope, so the guard is a no-op (deactivation always
  succeeds) until that model is added alongside ``journal``.
* ``tax_group``, ``tax`` and its child ``tax.repartition_line`` are ported
  (behaviour-wise) from Odoo core's ``account.tax.group``/``account.tax``/
  ``account.tax.repartition.line``, because those models live inside the
  ``account`` module, which ``ssi_accounting`` must not depend on. This unit is
  **configuration only** — no ``compute_all``/tax-computation engine, no
  synchronisation of tax lines on a journal entry, and no fiscal
  position/cash basis/tax closing/tax report; those are separate, later units.
* Upstream's invoice-flavoured vocabulary is neutralised throughout, per this
  unit's binding design decision: ``invoice_repartition_line_ids`` →
  ``repartition_line_base_ids``, ``refund_repartition_line_ids`` →
  ``repartition_line_reverse_ids``, ``document_type`` values ``invoice``/
  ``refund`` → ``base``/``reverse``, ``invoice_label`` → ``label``. The
  base/reverse split itself is kept (not collapsed into one list), because a
  journal entry can be reversed and the reversal must use the oppositely
  signed repartition group.
* ``tax.type_tax_use`` defaults to ``none`` (upstream defaults to ``sale``):
  this repo has no sale/purchase documents to filter a tax selector by.
* When a tax is created, two ``base`` repartition lines (one
  ``repartition_type="base"``, one ``repartition_type="tax"``) and two
  ``reverse`` lines following the same pattern are generated automatically,
  each group totalling a 100% factor — enforced by a constraint raising an
  SSI-formatted ``ValidationError`` when violated.
* ``tax.repartition_line`` is a pure child of ``tax`` (``ondelete="cascade"``):
  it gets no group, menu, standalone view, or ``ir.rule`` of its own — its
  ``ir.model.access`` rows are written together with ``tax``'s own rows, and
  it is only ever reached through ``tax.repartition_line_base_ids``/
  ``repartition_line_reverse_ids``.
* Dropped entirely from upstream's ``account.tax`` (out of this issue's
  scope): ``fiscal_position_ids``/``original_tax_ids``/``replacing_tax_ids``/
  ``is_domestic`` (fiscal position), ``tax_exigibility``/
  ``cash_basis_transition_account_id`` (cash basis), ``analytic`` (no
  analytic accounting concept yet), ``tax_scope`` (no goods/services product
  distinction here), ``invoice_legal_notes`` (no invoice document),
  ``repartition_lines_str`` (chatter-tracking helper, out of scope).
  Dropped from ``account.tax.group``: ``advance_tax_payment_account_id``
  (tax closing entry, out of scope), ``pos_receipt_label`` (no Point of Sale
  integration). Dropped from ``account.tax.repartition.line``:
  ``use_in_tax_closing`` (same reason as ``advance_tax_payment_account_id``).


Installation
============

To install this module, you need to:

1.  Clone the branch 19.0 of the repository https://github.com/open-synergy/ssi-accounting
2.  Add the path to this repository in your configuration (addons-path)
3.  Update the module list (Must be on developer mode)
4.  Go to menu *Apps -> Apps -> Main Apps*
5.  Search For *Accounting*
6.  Install the module


Bug Tracker
===========

Bugs are tracked on `GitHub Issues
<https://github.com/open-synergy/ssi-accounting/issues>`_. In case of trouble, please
check there if your issue has already been reported. If you spotted it first,
help us smash it by providing detailed and welcomed feedback.


Credits
=======

Contributors
------------

* Andhitia Rama <andhitia.r@gmail.com>

Maintainer
----------

.. image:: https://simetri-sinergi.id/logo.png
   :alt: PT. Simetri Sinergi Indonesia
   :target: https://simetri-sinergi.id

This module is maintained by the PT. Simetri Sinergi Indonesia.
