# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestMixinSequenceNumber(YamlTransactionCase):
    def test_mixin_sequence_number(self):
        """Run the YAML scenarios exercising 'mixin.sequence_number'.

        "mixin.sequence_number" is an AbstractModel and cannot be
        instantiated directly. This suite exercises it through the
        concrete fixture model "test.mixin_sequence_number", bundled
        inside this module (see models/test_mixin_sequence_number.py)
        so the mixin is self-testing.
        """
        self.run_yaml_scenario("test_data_mixin_sequence_number.yaml")

    def test_deduce_sequence_number_reset_detects_periodicity(self):
        """Pure Python -- trigger P1 (L-01: `action: call` discards a
        method's return value; `call` has no `save_as`, so
        `_deduce_sequence_number_reset`'s return value cannot be captured
        from YAML at all).

        `_deduce_sequence_number_reset` returns the periodicity string
        ('year'/'month'/'never'/...) that decides how the number resets --
        this is the core capability that sets this mixin apart from
        `mixin.sequence`. Verified directly here because there is no
        record-level side effect a YAML `assert` could observe instead.
        """
        model = self.env["test.mixin_sequence_number"]
        self.assertEqual(model._deduce_sequence_number_reset("TEST/2026/00001"), "year")
        self.assertEqual(
            model._deduce_sequence_number_reset("TEST/2026/01/00001"), "month"
        )
        self.assertEqual(model._deduce_sequence_number_reset("TEST/00001"), "never")
