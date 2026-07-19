# Copyright 2026 OpenSynergy Indonesia
# Copyright 2026 PT. Simetri Sinergi Indonesia
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo_yaml_test import YamlTransactionCase

from odoo import Command
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestTaxComputation(YamlTransactionCase):
    """Python murni -- pemicu P2 (L-04: tidak ada toleransi float di YAML;
    seluruh assert di sini adalah nilai moneter hasil perhitungan pajak) dan
    P11 (L-12: matriks kasus rujukan >= 5 varian, assert identik hanya
    input berbeda) dari `python-escape-hatch.md`.

    Tidak ada skenario YAML yang menyertai file ini -- mesin perhitungan
    pajak (`compute_all`/`_get_tax_details`/`_add_tax_details_in_base_line`/
    ...) beroperasi murni di atas `dict`, tanpa membaca/menulis record,
    sehingga tak ada apa pun untuk `action: assert` sasar lewat lookup
    `REC:`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.currency = cls.env.company.currency_id
        cls.tax_group = cls.env["tax_group"].create({"name": "VAT Group"})

    def _create_tax(self, **vals):
        vals.setdefault("tax_group_id", self.tax_group.id)
        return self.env["tax"].create(vals)

    def test_reference_cases_percent_fixed_division_group(self):
        """Tabel kasus rujukan: persen, tetap (fixed), division, dan group.

        Kriteria Penerimaan: "Pajak persen, tetap, division, dan group
        menghasilkan angka yang sama dengan Odoo 19 pada tabel kasus
        rujukan". Angka yang diharapkan adalah aritmetika pajak yang sama
        persis dengan formula `_eval_tax_amount_price_excluded`/
        `_eval_tax_amount_fixed_amount` yang diport dari Odoo 19 --
        dipilih dengan angka bersih (tanpa sisa desimal) supaya tidak ada
        ambiguitas pembulatan yang bisa menyamarkan sebuah bug port.
        """

        def one_percent_tax(name, amount, sequence=1):
            return self._create_tax(
                name=name,
                amount_type="percent",
                amount=amount,
                sequence=sequence,
            )

        percent_11 = one_percent_tax("VAT 11%", 11.0)
        fixed_500 = self._create_tax(
            name="Stamp Duty 500", amount_type="fixed", amount=500.0
        )
        division_20 = self._create_tax(
            name="Division 20%", amount_type="division", amount=20.0
        )
        group_child_a = one_percent_tax("Child A 5%", 5.0)
        group_child_b = one_percent_tax("Child B 6%", 6.0)

        cases = [
            # (label, taxes, price_unit, quantity,
            #  expected_excluded, expected_tax, expected_included)
            ("percent 11%, qty 1", percent_11, 100.0, 1.0, 100.0, 11.0, 111.0),
            ("percent 11%, qty 2", percent_11, 250.0, 2.0, 500.0, 55.0, 555.0),
            ("fixed 500, qty 3", fixed_500, 100.0, 3.0, 300.0, 1500.0, 1800.0),
            ("division 20%", division_20, 80.0, 1.0, 80.0, 20.0, 100.0),
            (
                "percent 20%, fractional price",
                one_percent_tax("VAT 20%", 20.0),
                50.25,
                1.0,
                50.25,
                10.05,
                60.30,
            ),
            (
                "group of two percent children",
                group_child_a + group_child_b,
                200.0,
                1.0,
                200.0,
                22.0,
                222.0,
            ),
        ]

        for (
            label,
            taxes,
            price_unit,
            quantity,
            expected_excluded,
            expected_tax,
            expected_included,
        ) in cases:
            with self.subTest(case=label):
                result = taxes.compute_all(price_unit, quantity=quantity)
                self.assertAlmostEqual(
                    result["total_excluded"], expected_excluded, places=2
                )
                self.assertAlmostEqual(
                    sum(t["amount"] for t in result["taxes"]),
                    expected_tax,
                    places=2,
                )
                self.assertAlmostEqual(
                    result["total_included"], expected_included, places=2
                )

    def test_price_included_tax_gives_correct_base_and_amount(self):
        """Kriteria Penerimaan: "Pajak price-included menghasilkan basis
        dan nominal yang benar".
        """
        tax = self._create_tax(
            name="VAT 10% Included",
            amount_type="percent",
            amount=10.0,
            price_include_override="tax_included",
        )
        result = tax.compute_all(110.0)
        self.assertAlmostEqual(result["total_excluded"], 100.0, places=2)
        self.assertAlmostEqual(
            sum(t["amount"] for t in result["taxes"]), 10.0, places=2
        )
        self.assertAlmostEqual(result["total_included"], 110.0, places=2)

    def test_include_base_amount_raises_subsequent_tax_base(self):
        """Kriteria Penerimaan: "Kaskade `include_base_amount` menaikkan
        basis pajak berikutnya".

        `tax_base` (fixed, 10, `include_base_amount`) is evaluated first
        (sequence 1) and pushes its own tax amount into `tax_percent`'s
        (sequence 2) base: 100 + 10 = 110, so the 10% tax amounts to 11
        instead of 10.
        """
        tax_base = self._create_tax(
            name="Fixed Surcharge",
            amount_type="fixed",
            amount=10.0,
            include_base_amount=True,
            sequence=1,
        )
        tax_percent = self._create_tax(
            name="VAT 10%",
            amount_type="percent",
            amount=10.0,
            sequence=2,
        )
        result = (tax_base + tax_percent).compute_all(100.0)
        amounts = sorted(t["amount"] for t in result["taxes"])
        self.assertAlmostEqual(amounts[0], 10.0, places=2)
        self.assertAlmostEqual(amounts[1], 11.0, places=2)
        self.assertAlmostEqual(result["total_excluded"], 100.0, places=2)
        self.assertAlmostEqual(result["total_included"], 121.0, places=2)

    def test_negative_factor_repartition_line_has_correct_sign(self):
        """Kriteria Penerimaan: "Repartition berfaktor negatif menghasilkan
        nominal bertanda benar".

        A 5% tax split 150%/-50% across two 'tax' repartition lines (they
        still sum to 100%, satisfying the existing 100% constraint) has
        `has_negative_factor` True: the base engine (`_get_tax_details`)
        computes a normal tax amount of 5.0 (5% of 100) and mirrors an
        exact negated reverse-charge entry of -5.0, independently of any
        repartition factor -- repartition factors only decide how *each
        sign's own total* is split across that sign's own lines (and
        `_add_accounting_data_to_base_line_tax_details`'s delta-smoothing
        reconciles the nominal, factor-scaled amount back to that exact
        per-sign total). With a single line per sign here, this means the
        positive-factor (150%) line's exposed amount is +5.0 and the
        negative-factor (-50%) line's is -5.0, whatever the exact split
        chosen, as long as the two factors still add up to 100%.
        """
        tax = self._create_tax(
            name="Reverse Charge 5%",
            amount_type="percent",
            amount=5.0,
            repartition_line_base_ids=[
                Command.create(
                    {
                        "repartition_type": "tax",
                        "document_type": "base",
                        "factor_percent": 150,
                    }
                ),
                Command.create(
                    {
                        "repartition_type": "tax",
                        "document_type": "base",
                        "factor_percent": -50,
                    }
                ),
            ],
        )
        self.assertTrue(tax.has_negative_factor)

        result = tax.compute_all(100.0)
        amounts_by_reverse_charge = {
            t["is_reverse_charge"]: t["amount"] for t in result["taxes"]
        }
        self.assertEqual(len(amounts_by_reverse_charge), 2)
        self.assertAlmostEqual(amounts_by_reverse_charge[False], 5.0, places=2)
        self.assertAlmostEqual(amounts_by_reverse_charge[True], -5.0, places=2)
        self.assertAlmostEqual(result["total_excluded"], 100.0, places=2)
        self.assertAlmostEqual(result["total_included"], 100.0, places=2)

    def test_round_per_line_and_round_globally_differ_on_three_lines(self):
        """Kriteria Penerimaan: "`round_per_line` dan `round_globally`
        memberi hasil berbeda pada kasus tiga baris 11%".

        Three identical lines (price_unit=10.15, 11%) each round their own
        tax amount to 1.12 under 'round_per_line' (3 * 1.12 = 3.36), while
        'round_globally' rounds the true total once (3 * 1.1165 = 3.3495
        -> 3.35). The two expected totals are derived independently, from
        plain arithmetic plus the company currency's own rounding rule --
        not by calling the ported engine a second time -- so this remains
        a meaningful check of the engine, not a tautology.
        """
        tax = self._create_tax(
            name="VAT 11% Rounding", amount_type="percent", amount=11.0
        )
        price_unit = 10.15
        rate = 0.11
        currency = self.currency

        expected_round_per_line = currency.round(currency.round(price_unit * rate) * 3)
        expected_round_globally = currency.round(price_unit * rate * 3)
        self.assertNotAlmostEqual(
            expected_round_per_line, expected_round_globally, places=2
        )

        for rounding_method, expected_total in (
            ("round_per_line", expected_round_per_line),
            ("round_globally", expected_round_globally),
        ):
            with self.subTest(rounding_method=rounding_method):
                company = self.env.company
                company.tax_calculation_rounding_method = rounding_method
                base_lines = [
                    self.env["tax"]._prepare_base_line_for_taxes_computation(
                        None,
                        tax_ids=tax,
                        currency_id=currency,
                        price_unit=price_unit,
                        quantity=1.0,
                    )
                    for _ in range(3)
                ]
                self.env["tax"]._add_tax_details_in_base_lines(base_lines, company)
                self.env["tax"]._round_base_lines_tax_details(base_lines, company)

                total_tax = sum(
                    tax_data["tax_amount"]
                    for base_line in base_lines
                    for tax_data in base_line["tax_details"]["taxes_data"]
                )
                self.assertAlmostEqual(total_tax, expected_total, places=2)

    def test_base_line_field_fallback_does_not_raise_on_missing_field(self):
        """Kriteria Penerimaan: "Baris yang tidak memiliki suatu field
        tetap terproses berkat fallback
        `_get_base_line_field_value_from_record`".

        Negatif (Skenario Uji): a plain dict 'record' missing the
        'product_uom_id' key entirely must not raise `AttributeError` --
        if the fallback is broken, building the base line itself raises.
        """
        tax = self._create_tax(
            name="VAT 11% Fallback", amount_type="percent", amount=11.0
        )
        record = {"price_unit": 50.0, "quantity": 2.0}

        base_line = self.env["tax"]._prepare_base_line_for_taxes_computation(
            record, tax_ids=tax, currency_id=self.currency
        )

        self.assertEqual(base_line["product_uom_id"], self.env["uom.uom"])
        self.assertEqual(base_line["product_id"], self.env["product.product"])
        self.assertEqual(base_line["price_unit"], 50.0)
        self.assertEqual(base_line["quantity"], 2.0)

        self.env["tax"]._add_tax_details_in_base_line(base_line, self.env.company)
        self.assertAlmostEqual(
            base_line["tax_details"]["raw_total_excluded_currency"],
            100.0,
            places=2,
        )

    def test_full_pipeline_prepares_tax_lines_across_two_lines(self):
        """`_prepare_tax_lines`/`_add_accounting_data_in_base_lines_tax_details`
        (both explicitly named in the issue's Keputusan Desain) are only
        ever meaningful once run at the end of the full multi-line
        pipeline: `_add_tax_details_in_base_lines` ->
        `_round_base_lines_tax_details` ->
        `_add_accounting_data_in_base_lines_tax_details` ->
        `_prepare_tax_lines`. Two identical price-included lines are used
        so both collapse into a single proposed tax line (same tax,
        account, partner, currency), exercising the 'included' rounding
        mode of `_round_tax_details_base_lines` along the way -- a mode
        `compute_all` (which skips `_round_base_lines_tax_details`
        entirely) never reaches.
        """
        tax = self._create_tax(
            name="VAT 10% Included Pipeline",
            amount_type="percent",
            amount=10.0,
            price_include_override="tax_included",
        )
        company = self.env.company
        base_lines = [
            self.env["tax"]._prepare_base_line_for_taxes_computation(
                None,
                tax_ids=tax,
                currency_id=self.currency,
                price_unit=110.0,
                quantity=1.0,
            )
            for _ in range(2)
        ]
        self.env["tax"]._add_tax_details_in_base_lines(base_lines, company)
        self.env["tax"]._round_base_lines_tax_details(base_lines, company)
        self.env["tax"]._add_accounting_data_in_base_lines_tax_details(
            base_lines, company
        )
        result = self.env["tax"]._prepare_tax_lines(base_lines, company)

        self.assertEqual(len(result["base_lines_to_update"]), 2)
        for _base_line, amounts in result["base_lines_to_update"]:
            self.assertAlmostEqual(amounts["balance"], 100.0, places=2)

        self.assertEqual(len(result["tax_lines_to_add"]), 1)
        tax_line = result["tax_lines_to_add"][0]
        self.assertAlmostEqual(tax_line["balance"], 20.0, places=2)
        self.assertAlmostEqual(tax_line["tax_base_amount"], 200.0, places=2)
        self.assertEqual(result["tax_lines_to_delete"], [])
        self.assertEqual(result["tax_lines_to_update"], [])

    def test_handle_price_include_false_forces_total_excluded(self):
        """`handle_price_include=False` treats `price_unit` as already
        excluded, bypassing the tax's own `price_include` configuration
        -- exercises the price-excluded evaluation pass
        (`_ascending_process_price_excluded_taxes_batch`) even for a
        price-included tax, instead of the price-included one
        (`_descending_process_price_included_taxes_batch`) `compute_all`
        would otherwise take.
        """
        tax = self._create_tax(
            name="VAT 10% Included, Forced Excluded",
            amount_type="percent",
            amount=10.0,
            price_include_override="tax_included",
        )
        result = tax.compute_all(100.0, handle_price_include=False)
        self.assertAlmostEqual(result["total_excluded"], 100.0, places=2)
        self.assertAlmostEqual(
            sum(t["amount"] for t in result["taxes"]), 10.0, places=2
        )
        self.assertAlmostEqual(result["total_included"], 110.0, places=2)

    def test_multiple_positive_repartition_lines_split_proportionally(self):
        """Two positive-factor 'tax' repartition lines (70%/30%, still
        summing to 100%) split a single tax amount across two exposed
        entries in `compute_all`'s ``taxes`` list, exercising the
        multi-line branch of the delta-smoothing loop in
        `_add_accounting_data_to_base_line_tax_details` (the negative-
        factor test above only ever has one repartition line per sign).
        """
        tax = self._create_tax(
            name="VAT 10% Split 70/30",
            amount_type="percent",
            amount=10.0,
            repartition_line_base_ids=[
                Command.create(
                    {
                        "repartition_type": "tax",
                        "document_type": "base",
                        "factor_percent": 70,
                    }
                ),
                Command.create(
                    {
                        "repartition_type": "tax",
                        "document_type": "base",
                        "factor_percent": 30,
                    }
                ),
            ],
        )
        result = tax.compute_all(100.0)
        amounts = sorted(t["amount"] for t in result["taxes"])
        self.assertEqual(len(amounts), 2)
        self.assertAlmostEqual(amounts[0], 3.0, places=2)
        self.assertAlmostEqual(amounts[1], 7.0, places=2)
        self.assertAlmostEqual(
            sum(t["amount"] for t in result["taxes"]), 10.0, places=2
        )
