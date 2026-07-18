# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestMixinSequenceNumber(YamlTransactionCase):
    def test_mixin_sequence_number(self):
        # "mixin.sequence_number" is an AbstractModel and cannot be
        # instantiated directly. This suite exercises it through the
        # concrete fixture model "test.mixin_sequence_number", bundled
        # inside this module (see models/test_mixin_sequence_number.py)
        # so the mixin is self-testing.
        self.run_yaml_scenario("test_data_mixin_sequence_number.yaml")

    def test_deduce_sequence_number_reset_detects_periodicity(self):
        """Python murni — pemicu P1 (L-01: `action: call` membuang nilai
        balik method; tidak ada `save_as` pada `call`, jadi nilai balik
        `_deduce_sequence_number_reset` sama sekali tidak bisa ditangkap
        dari YAML).

        `_deduce_sequence_number_reset` mengembalikan string periodisitas
        ('year'/'month'/'never'/...) yang menentukan bagaimana nomor
        direset — inilah kemampuan inti yang membedakan mixin ini dari
        `mixin.sequence`. Diverifikasi langsung di sini karena tidak ada
        efek samping pada record yang bisa di-assert lewat YAML.
        """
        model = self.env["test.mixin_sequence_number"]
        self.assertEqual(
            model._deduce_sequence_number_reset("TEST/2026/00001"), "year"
        )
        self.assertEqual(
            model._deduce_sequence_number_reset("TEST/2026/01/00001"), "month"
        )
        self.assertEqual(model._deduce_sequence_number_reset("TEST/00001"), "never")
