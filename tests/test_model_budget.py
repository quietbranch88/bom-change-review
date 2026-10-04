"""Independent accounting invariants; amounts are synthetic, not provider prices."""

from decimal import Decimal
import unittest

from model_budget import Budget, BudgetError


class BudgetTests(unittest.TestCase):
    def test_reservation_and_exact_decimal_settlement(self):
        budget = Budget("0.02")
        first = budget.reserve("0.006")
        budget.reserve("0.006")
        self.assertEqual(budget.snapshot()["held"], "0.012")
        budget.settle(first, "0.001")
        self.assertEqual(budget.snapshot()["spent"], "0.001")
        self.assertEqual(budget.snapshot()["held"], "0.006")
        self.assertEqual(budget.snapshot()["available"], "0.013")

    def test_exact_limit_admitted_but_next_reservation_denied(self):
        budget = Budget("0.006")
        budget.reserve("0.006")
        with self.assertRaisesRegex(BudgetError, "model_budget_exhausted"):
            budget.reserve("0.000001")
        self.assertEqual(budget.snapshot()["held"], "0.006")

    def test_unknown_does_not_refund_and_blocks_new_work(self):
        budget = Budget("0.02")
        budget.unknown(budget.reserve("0.006"))
        self.assertEqual(budget.snapshot()["held"], "0.006")
        self.assertEqual(budget.snapshot()["available"], "0.014")
        with self.assertRaisesRegex(BudgetError, "model_budget_blocked"):
            budget.reserve("0.006")

    def test_invalid_cost_retains_exposure(self):
        for value in (None, True, -1, "NaN", "Infinity", "private provider body"):
            with self.subTest(value=value):
                budget = Budget("0.02")
                with self.assertRaisesRegex(BudgetError, "model_cost_unknown"):
                    budget.settle(budget.reserve("0.006"), value)
                self.assertEqual(budget.snapshot()["held"], "0.006")
                self.assertTrue(budget.blocked)
                self.assertEqual(budget.spent, Decimal(0))

    def test_overrun_records_observed_charge_not_an_imaginary_cap(self):
        budget = Budget("0.02")
        with self.assertRaisesRegex(BudgetError, "model_cost_overrun"):
            budget.settle(budget.reserve("0.006"), "0.03")
        self.assertEqual(budget.snapshot()["spent"], "0.03")
        self.assertEqual(budget.snapshot()["available"], "0")
        self.assertTrue(budget.overrun)
        with self.assertRaisesRegex(BudgetError, "model_budget_blocked"):
            budget.reserve("0.001")
