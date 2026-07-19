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
``account.journal.group``, the tax configuration models ``tax_group``,
``tax`` and its child ``tax.repartition_line`` (plus their standalone
computation engine), and the journal entry itself: ``journal_entry`` and its
child ``journal_entry.item``, including its posting/numbering/state machine
and the synchronisation that turns a taxed line into real tax journal items.
A posted entry can also be reversed through the ``journal_entry_reversal``
wizard, producing a mirror entry tied back to the original. Journal item
reconciliation, including automatic currency exchange difference entries for
cross-currency matches, is also in place.


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
* ``journal_entry`` and its child ``journal_entry.item`` are ported (behaviour-wise)
  from Odoo core's ``account.move``/``account.move.line``, because those models
  live inside the ``account`` module, which ``ssi_accounting`` must not depend
  on. Renamed to ``journal_entry``/``journal_entry.item`` per this unit's
  Keputusan Desain (unlike ``account.journal``/``account.account``, they are
  free to follow the plain-underscore SSI naming convention: nothing in this
  module hardcodes the dotted placeholder name they used to be forward-referenced
  by). Reconciliation and exchange-rate differences remain separate, later units.
* ``journal_entry`` inherits ``mixin.sequence_number`` (``_sequence_field =
  "name"``, ``_sequence_date_field = "date"``, ``_sequence_index =
  "journal_id"``) to get its statutory number: gapless per journal, resetting
  yearly, assigned only on posting (a draft entry's ``name`` stays empty;
  ``name_placeholder`` previews it). **A future-dated entry posts
  immediately instead of being queued** -- unlike upstream Odoo's
  ``account.move``, ``auto_post``/``soft``-posting and the whole
  recurring-entry chain are dropped entirely here. The access check
  ``_post`` performs is ``ssi_accounting.group_accounting_user`` (upstream
  checks ``account.group_account_invoice``, which cannot exist in a module
  that never depends on ``account``). Locked accounting dates
  (``fiscalyear_lock_date``/``tax_lock_date``) are not rejected: exactly
  like upstream, the entry's date is pushed forward to the next open period
  and a note is logged on the entry, matching this repo's structured error
  message convention. ``button_draft`` guards on ``has_reconciled_entries``
  (forward-declared the same defensive way as ``account.account``'s
  ``_toggle_reconcile_to_true`` -- always ``False`` until a later
  reconciliation unit adds the fields it reads) and never clears ``name``.
  ``made_sequence_gap`` is likewise declared but not maintained by any
  cascade in this unit -- the exchange-difference posting cascade upstream
  triggers from ``_post`` is a later multi-currency unit's job.
* ``amount_total_debit``/``amount_total_credit`` are ``journal_entry``'s only
  aggregate fields -- the whole invoice-flavoured amount block
  (``amount_untaxed``/``amount_tax``/``amount_total``/``amount_residual`` and
  their ``*_signed`` variants, ``tax_totals``, ``payment_state``, ...) is
  dropped, along with ``auto_post``/recurring entries, the hash chain, and
  every field prefixed ``invoice_``/``statement_``/``payment_``. ``move_type``
  is dropped too, along with ``is_entry()``/``is_invoice()``: those two were
  kept as shims (returning ``True``/``False``) only until the tax
  synchronisation unit's ported code (behaviour-gated on them) landed; now
  that it has, every branch that depended on them was pruned and the shims
  themselves were deleted.
* ``journal_entry.item`` keeps ``product_id``/``quantity``/``price_unit``/
  ``price_subtotal`` (a deliberate product decision), but purely as
  informational fields: the tax base for an ``entry``-typed line is
  ``amount_currency``, never ``quantity * price_unit`` -- exactly upstream
  Odoo 19's own behaviour for ``account.move`` lines of ``move_type ==
  'entry'``. ``product_uom_id`` is dropped (safe: ``tax``'s base-line builder
  already falls back to ``uom.uom`` when it is absent). ``display_type``
  stays required, shrunk to ``product``/``tax``/``line_section``/``line_note``
  -- it is the base line vs. tax line discriminator the tax engine relies on.
* ``account_id`` is deliberately **not** ``required=True``. Exactly upstream
  ``account.move.line``'s own mechanism, its necessity is enforced by two
  ``models.Constraint`` SQL ``CHECK``\ s ported verbatim (table name aside):
  ``_check_accountable_required_fields`` (an account is required for
  ``product``/``tax`` rows, optional for ``line_section``/``line_note``) and
  ``_check_non_accountable_fields_null`` (``line_section``/``line_note`` rows
  must carry no account and no ``debit``/``credit``/``amount_currency``). A
  plain ``required=True`` on the field would have wrongly forced every
  cosmetic section/note row to carry an account too.
* ``debit``/``credit``/``balance``/``amount_currency`` stay consistent
  whichever of the three is filled first. Unlike a first attempt at this
  unit (which made ``balance`` a compute+inverse pair *over*
  ``debit``/``credit``, with ``amount_currency`` inverse writing into
  ``balance``), the field actually kept as the primary, directly writable
  one is ``balance`` -- mirroring upstream ``account.move.line`` exactly:
  ``debit``/``credit`` are a compute+inverse pair depending on ``balance``,
  and ``amount_currency`` is a compute+inverse pair also depending on
  ``balance``. An inverse method assigning into another field that itself
  only has an ``inverse`` (not a plain ``@api.depends`` compute) does not
  reliably cascade during ``create()``: filling only ``amount_currency`` on
  a new line left ``debit``/``credit`` at their stale default under the
  first attempt, silently producing an unbalanced entry (caught by CI, not
  by this issue's own SQL-``CHECK`` review). Depending on ``balance`` via
  ``@api.depends`` instead of a second inverse hop is what makes every fill
  direction cascade correctly, exactly like upstream. **One further
  deviation from upstream's exact field kwargs, also caught by CI:**
  ``debit``/``credit``/``amount_currency`` are deliberately **not**
  ``precompute=True`` here, unlike upstream. A ``precompute=True`` field
  without an explicit value is computed during ``create()``'s pre-insert
  pass, *before* that same call's given-field inverses (where ``balance``
  gets its real value when only ``amount_currency`` was supplied) have
  run -- so a precomputed ``debit``/``credit`` would bake in a value
  derived from ``balance`` still at its default, too early to see the
  correct one. Only ``balance`` keeps ``precompute=True``: its compute is
  a self-referential no-op whenever a value already exists, so it is
  immune to this ordering trap regardless of fill direction.
* ``_check_balanced``/``_get_unbalanced_moves`` are ported verbatim from
  upstream, raw SQL included (only the table names changed). Unbalanced
  entries are rejected with an SSI-formatted error unless the journal has a
  ``suspense_account_id``, in which case ``_sync_dynamic_lines``/
  ``_sync_unbalanced_lines`` (a framework scaffold, not a verbatim port --
  its stack is trimmed to this single auto-balancing stage) adds/adjusts a
  line against that account instead.
* Several ``account.account``/``account.journal``/``tax``/``res.currency``
  guards that used to be written against the placeholder model names
  ``account.move``/``account.move.line`` (before this unit's Keputusan
  Desain settled on ``journal_entry``/``journal_entry.item``) now work for
  real: ``account.account``'s ``used``, ``current_balance``, and the delete
  guard; ``account.journal``'s ``entry_count``,
  ``action_open_journal_entries``, the company-consistency check, and the
  delete guard; ``tax``'s ``is_used``; ``res.currency``'s
  ``_has_accounting_entries``. **Two guards stay no-ops on purpose**,
  because reconciliation is still out of scope:
  ``account.account._toggle_reconcile_to_true``/``_toggle_reconcile_to_false``
  are guarded on ``"reconciled" not in self.env["journal_entry.item"]._fields``
  (field-precision, not just model-presence -- the same pattern
  ``_onchange_account_type`` already uses for ``tax_ids``), and will start
  working once a later reconciliation unit adds that field. The delete guard
  on ``tax.repartition_line`` continues to work for real, unaffected by this
  unit. Dropped entirely (out of this issue's scope): the opening balance
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
  fields are deliberately **not** added here — they belong to a future
  reconciliation unit, out of scope for both this and the journal entry unit.
* ``res.currency`` is inherited only to add ``_has_accounting_entries()`` and a
  ``write()`` guard that refuses to deactivate a currency already used on journal
  items. Unlike upstream (which guards a rounding-precision decrease), this guard
  specifically blocks **deactivation**. It reads ``journal_entry.item``, which
  now exists (see below), so this guard works for real.
* ``tax_group``, ``tax`` and its child ``tax.repartition_line`` are ported
  (behaviour-wise) from Odoo core's ``account.tax.group``/``account.tax``/
  ``account.tax.repartition.line``, because those models live inside the
  ``account`` module, which ``ssi_accounting`` must not depend on. Their
  configuration, ``compute_all``/tax-computation engine, and the
  synchronisation that turns a taxed ``journal_entry.item`` into real tax
  lines (see the ``journal_entry`` bullet above) are all in place; fiscal
  position/cash basis/tax closing/tax report remain separate, later units.
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
* Setting ``tax_ids`` on a ``product``-typed ``journal_entry.item`` now
  creates/updates/deletes matching ``tax``-typed lines automatically, ported
  (behaviour-wise) from upstream ``account.move``/``account.move.line``'s
  ``_sync_tax_lines`` and its ``_get_rounded_base_and_tax_lines``/
  ``_prepare_product_base_line_for_taxes_computation``/
  ``_prepare_tax_line_for_taxes_computation`` supporting cast.
  ``journal_entry``'s sync stack (``_sync_dynamic_lines``) grew a second
  stage alongside the existing auto-balancing one, entered so the tax
  stage's new/updated/deleted lines exist before the balance is
  recomputed. ``journal_entry.item`` gained a ``write()`` override (with
  the same recursion guard ``journal_entry.write()`` uses) purely so
  editing a line directly -- the only way this repo's own form edits an
  existing row -- also triggers the sync; upstream needs the equivalent
  override on ``account.move.line`` for the same reason.
* ``quantity``/``price_unit``/``discount`` never drive the tax base: a
  ``journal_entry.item``'s ``amount_currency`` is always what upstream
  calls the ``'total_excluded'`` base, and ``sign`` is always ``1`` --
  neither this repo's tax engine nor its journal entry model has an
  invoice-direction concept to derive a sign from. As a direct
  consequence, a ``price_include_override="tax_included"`` tax is
  computed **as if excluded** here (the amount typed is always net of
  tax, added on top), never extracted from a tax-inclusive gross figure.
* ``tax._prepare_tax_line_for_taxes_computation`` (the engine-side
  counterpart of the pre-existing ``_prepare_base_line_for_taxes_computation``,
  for an *existing* tax line rather than a base line) is added by this
  unit -- the prior tax-computation-engine unit deliberately left it
  unported, since nothing yet produced "existing tax line" dicts for it
  to convert; this unit is that first consumer.
* A posted ``journal_entry`` can be reversed through the ``journal_entry_reversal``
  wizard (header button "Reverse Entry"), which builds a mirror entry whose lines
  carry the opposite ``balance``/``amount_currency``, linked back through
  ``reversed_entry_id``/``reversal_move_ids``. Unlike upstream ``account.move``,
  there is no ``move_type`` to swap: the reverse repartition lines are selected
  through ``journal_entry.is_refund``, a plain boolean the wizard sets explicitly
  on the mirror entry -- never derived from ``state`` or any document-type concept.
  ``refund_method`` on the wizard keeps only ``cancel``/``modify`` (upstream's third
  value, ``refund``, is specific to invoice credit notes and dropped): ``cancel``
  additionally reconciles the mirror entry against the original through
  ``_reconcile_reversed_moves`` -- guarded as a forward reference on
  ``"reconciled" in journal_entry.item._fields`` until a later reconciliation unit
  adds ``reconcile()``/``amount_residual`` (not a declared dependency of this unit),
  the same defensive pattern already used elsewhere in this module; ``modify``
  additionally opens a fresh, editable draft copy of the original's lines.
* Journal item reconciliation is added through two new models, ``reconcile_partial``
  and ``reconcile_full``, ported (behaviour-wise) from Odoo core's
  ``account.partial.reconcile``/``account.full.reconcile``, plus the whole
  ``reconcile()`` chain on ``journal_entry.item`` (ported from
  ``account.move.line``). ``journal_entry.item`` gains ``reconciled``,
  ``amount_residual``/``amount_residual_currency``, ``full_reconcile_id``,
  ``matched_debit_ids``/``matched_credit_ids`` and ``matching_number`` --
  the field the two ``_toggle_reconcile_to_true``/``_toggle_reconcile_to_false``
  guards on ``account.account`` (forward-declared by an earlier unit) and the
  ``_reconcile_reversed_moves`` guard on ``journal_entry`` (forward-declared
  by the reversal unit) were all waiting on; they now work for real, with no
  change needed at their own call sites.
* Reconciliation is intentionally narrower than upstream: it only ever
  matches items on an account with ``reconcile = True`` (no exemption for
  ``asset_cash``/``liability_credit_card`` -- that exemption exists upstream
  solely for bank-statement reconciliation, out of this unit's scope), only
  within a single company, and only on items whose journal entry is
  ``posted`` (upstream additionally allows a draft entry through). Selisih
  kurs (currency exchange difference) entry generation was deferred to a
  later, dedicated currency unit at this point -- see below for how it is
  actually populated. Cash-basis tax entries and ``account.reconcile.model``
  (automatic bank statement matching) are out of scope entirely.
* The reconciliation UI for this unit is a **multi-select "Reconcile"/
  "Unreconcile" header button pair on the Journal Items list view**
  (``action_reconcile``/``action_remove_move_reconcile``), plus a
  ``matching_number`` column and a "Reconciled"/"Unreconciled" search
  filter -- a deliberate, first-wave choice, **not** the OWL Bankrec
  reconciliation widget upstream Odoo ships. Matching lines to reconcile is
  entirely manual (select rows on the same, reconciliation-enabled account,
  then click "Reconcile"); there is no suggested-match assistant.
* Reconciling two journal items recorded in the same foreign currency at
  different rates now books an automatic currency exchange difference entry,
  ported (behaviour-wise) from Odoo core's own
  ``_get_exchange_journal``/``_get_exchange_account``/
  ``_prepare_exchange_difference_move_vals``/``_create_exchange_difference_moves``.
  The exchange move always posts to ``res.company.currency_exchange_journal_id``
  (a ``general``-typed journal); a positive residual books to
  ``expense_currency_exchange_account_id`` (loss), a negative one to
  ``income_currency_exchange_account_id`` (gain) -- both configured on the
  company's "Currency Exchange" settings, added earlier alongside
  ``account_journal_suspense_account_id``. Missing any of the three raises an
  SSI-formatted configuration error. The exchange move is posted immediately
  (both original lines are already posted, per the narrower reconciliation
  scope above) and reconciled against the residual it fixes right away, so
  both original items end up with a zero ``amount_residual`` in the company
  currency. ``reconcile_partial.exchange_move_id``/``journal_entry.
  exchange_diff_partial_ids`` link a partial to the exchange entry it
  produced, and ``journal_entry.item.exchange_move_ids`` mirrors it back onto
  the reconciled lines. Undoing the reconciliation (removing the partial that
  produced it) reverses the exchange entry through the same
  ``journal_entry_reversal``-flavoured cascade ``reconcile_partial.unlink()``
  already uses for a plain reconciliation.


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
