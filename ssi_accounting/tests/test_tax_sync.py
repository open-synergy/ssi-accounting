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
        """Run the YAML scenarios for the tax synchronisation engine."""
        self.run_yaml_scenario("test_data_tax_sync.yaml")

    def test_no_leftover_is_invoice_is_entry_definitions(self):
        """Pure Python -- trigger P1 (L-01: no YAML scenario targets an
        Odoo record at all here, only the Python source files' own
        contents). Acceptance criteria: "Searching for is_invoice,
        is_entry, is_sale_document, is_purchase_document across both
        model files finds no leftover definition" -- the ``is_entry()``/
        ``is_invoice()`` shims (and their upstream counterparts
        ``is_sale_document``/``is_purchase_document``, never ported to
        this repo at all) must be fully removed once this tax
        synchronisation unit lands.
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
