.. image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
   :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
   :alt: License: AGPL-3

==========
Accounting
==========

Core accounting module for PT. Simetri Sinergi Indonesia (SSI). This module builds a
pure journal entry accounting core, independent of Odoo's native ``account`` module —
it does not depend on, and will never depend on, the ``account`` addon.

This unit only lays down the module skeleton and its own security groups. Models,
views, and menus are added incrementally by later units.


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
  - ``group_accounting_manager`` — implies ``group_accounting_user``. Reserved for
    users who may additionally configure the chart of accounts, journals, and taxes
    once those models exist.

  Both groups belong to the ``Accounting`` module category.


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
