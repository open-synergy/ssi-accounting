# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import os
import re

from odoo_yaml_test import YamlTransactionCase

from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestTaxSync(YamlTransactionCase):
    def test_tax_sync(self):
        self.run_yaml_scenario("test_data_tax_sync.yaml")

    def test_no_leftover_is_invoice_is_entry_definitions(self):
        """Python murni -- pemicu P1 (L-01: skenario YAML tidak menyasar
        record Odoo sama sekali, melainkan isi berkas sumber Python itu
        sendiri). Kriteria Penerimaan: "Pencarian is_invoice, is_entry,
        is_sale_document, is_purchase_document pada kedua berkas model
        tidak menemukan definisi tersisa" -- shim ``is_entry()``/
        ``is_invoice()`` (dan padanan upstream ``is_sale_document``/
        ``is_purchase_document``, yang tidak pernah diport sama sekali ke
        repo ini) wajib sudah dihapus total begitu unit sinkronisasi
        pajak ini mendarat.
        """
        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        checked_files = ("journal_entry.py", "journal_entry_item.py")
        forbidden_defs = (
            "is_invoice",
            "is_entry",
            "is_sale_document",
            "is_purchase_document",
        )
        for filename in checked_files:
            path = os.path.join(models_dir, filename)
            with open(path, encoding="utf-8") as source_file:
                source = source_file.read()
            for name in forbidden_defs:
                with self.subTest(filename=filename, name=name):
                    self.assertIsNone(
                        re.search(rf"^\s*def {name}\s*\(", source, re.MULTILINE),
                        f"'def {name}(' still defined in {filename}",
                    )
