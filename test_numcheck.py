import json
import os
import re
import unittest
from fractions import Fraction

import numcheck as nc
from gen import corpus

HERE = os.path.dirname(os.path.abspath(__file__))
CIM = nc.read_text(os.path.join(HERE, "sample_cim.txt"))
CLAIMS = json.loads(nc.read_text(os.path.join(HERE, "sample_claims.json")))


class Amounts(unittest.TestCase):
    def test_scales(self):
        for text, value in [("$47.3M", 47300000), ("$47.3 million", 47300000), ("$47.31mm", 47310000),
                            ("$47,310K", 47310000), ("$47,310,000", 47310000), ("$1.2B", 1200000000)]:
            self.assertEqual(nc.parse_amount(text).value, value, text)

    def test_tolerance_is_half_the_last_digit(self):
        self.assertEqual(nc.parse_amount("$47.3M").tol, 50000)
        self.assertEqual(nc.parse_amount("$47,310K").tol, 500)
        self.assertEqual(nc.parse_amount("19.2%").tol, Fraction(1, 20))

    def test_negatives_and_percent(self):
        self.assertEqual(nc.parse_amount("(318)", 1000).value, -318000)
        self.assertEqual(nc.parse_amount("-$0.3M").value, -300000)
        self.assertEqual(nc.parse_amount("12.3%").kind, "pct")

    def test_not_a_number(self):
        self.assertIsNone(nc.parse_amount("n/a"))
        self.assertIsNone(nc.parse_amount("about nine"))


class Names(unittest.TestCase):
    def test_periods(self):
        for t in ["FY2023", "FY23", "2023A", "FY2023E", "fy 2023"]:
            self.assertEqual(nc.canon_period(t), "FY2023", t)
        self.assertIsNone(nc.canon_period("1,450"))
        self.assertIsNone(nc.canon_period("23"))

    def test_metric_aliases(self):
        self.assertEqual(nc.canon_metric("Adj. EBITDA"), "adjusted_ebitda")
        self.assertEqual(nc.canon_metric("Reported EBITDA"), "ebitda")
        self.assertEqual(nc.canon_metric("Net Sales"), "revenue")
        self.assertEqual(nc.canon_metric("Net Income (Loss)"), "net_income")
        self.assertEqual(nc.canon_metric("Active Customers"), "active_customers")


class Reading(unittest.TestCase):
    def setUp(self):
        self.src = nc.Source(CIM)

    def test_table_scale_and_line(self):
        f = self.src.direct("revenue", "FY2024", "usd")
        self.assertEqual((f.value, f.line, f.where), (58644000, 17, "table"))

    def test_prose_fact_is_read_but_table_is_preferred(self):
        facts = self.src.by[("revenue", "FY2024")]
        self.assertEqual([f.where for f in facts], ["table", "prose"])
        self.assertEqual(facts[1].value, 58600000)

    def test_operating_table_is_not_dollars(self):
        f = self.src.by[("active_customers", "FY2024")][0]
        self.assertEqual((f.kind, f.value), ("count", 1450))

    def test_na_cell_skipped_and_parentheses_negative(self):
        self.assertNotIn(("revenue_growth", "FY2022"), self.src.by)
        self.assertEqual(self.src.direct("net_income", "FY2022", "usd").value, -318000)

    def test_distractor_numbers_are_not_facts(self):
        values = {f.value for f in self.src.facts if f.kind == "usd"}
        self.assertNotIn(Fraction(3200000000), values)   # addressable market


class Checking(unittest.TestCase):
    def test_sample_verdicts(self):
        got = [v.status for v in nc.check_all(CIM, CLAIMS)]
        self.assertEqual(got, [
            "supported", "supported", "supported_derived", "supported", "supported",
            "wrong_metric", "wrong_period", "unit_mismatch", "wrong_period", "not_found",
            "wrong_period"])

    def test_evidence_points_at_source_lines(self):
        v = nc.check(nc.Source(CIM), "adjusted_ebitda_margin", "FY2024", "16.0%")
        self.assertEqual(sorted(f.line for f in v.evidence), [17, 21])

    def test_rounding_boundary(self):
        src = nc.Source(CIM)
        self.assertTrue(nc.check(src, "gross_profit", "FY2022", "$13.4M").ok)
        self.assertEqual(nc.check(src, "gross_profit", "FY2022", "$13.5M").status, "value_mismatch")
        self.assertEqual(nc.check(src, "gross_profit", "FY2022", "$13,430K").status, "value_mismatch")

    def test_growth_needs_prior_year(self):
        v = nc.check(nc.Source(CIM), "gross_profit_growth", "FY2022", "9.0%")
        self.assertEqual(v.status, "not_found")
        self.assertTrue(nc.check(nc.Source(CIM), "gross_profit_growth", "FY2023", "16.2%").ok)

    def test_unparseable_claim(self):
        self.assertEqual(nc.check(nc.Source(CIM), "revenue", "FY2024", "strong").status, "unparseable")

    def test_same_verdicts_when_columns_are_single_spaced(self):
        squeezed = "\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in CIM.splitlines())
        self.assertEqual([v.status for v in nc.check_all(squeezed, CLAIMS)],
                         [v.status for v in nc.check_all(CIM, CLAIMS)])

    def test_pipe_table_and_scale_in_header(self):
        doc = "| ($ in millions) | 2023A | 2024E |\n| Total Revenue | 12.4 | 15.0 |\n| Top 10 Share | 41% | 39% |\n"
        src = nc.Source(doc)
        self.assertTrue(nc.check(src, "revenue", "FY2024", "$15.0M").ok)
        self.assertEqual(nc.check(src, "revenue", "FY2024", "$15.0K").status, "unit_mismatch")
        self.assertEqual(nc.check(src, "revenue", "FY2024", "$12.4M").status, "wrong_period")

    def test_label_ending_in_a_number(self):
        doc = "($ in thousands) FY2023 FY2024\nRevenue 100 200\nTop 10 Customers 40 90\n"
        self.assertEqual(nc.Source(doc).direct("top_10_customers", "FY2024", "usd").value, 90000)


class RealisticLayouts(unittest.TestCase):
    """Things a first version skipped: LTM and quarter columns, footnote marks, sign errors,
    a document that disagrees with itself, and compound growth."""

    DOC = ("Summary Financials ($ in millions)\n"
           "                      FY2023   FY2024   LTM Jun-25   Q2 2025\n"
           "Net Sales (1)           51.9     58.6         60.1      15.8\n"
           "Adj. EBITDA*             7.8      9.4          9.9       2.6\n"
           "Net Income (Loss)      (0.3)      2.4          2.6       0.7\n"
           "(1) Net of returns of $1.2 million.\n"
           "\n"
           "In FY2024, revenue was $58.9 million.\n")

    def setUp(self):
        self.src = nc.Source(self.DOC)

    def test_ltm_and_quarter_columns(self):
        self.assertEqual(nc.canon_period("LTM Jun-25"), "LTM")
        self.assertEqual(nc.canon_period("TTM"), "LTM")
        self.assertEqual(nc.canon_period("3Q25"), "2025Q3")
        self.assertEqual(nc.canon_period("fiscal year 2024"), "FY2024")
        self.assertTrue(nc.check(self.src, "revenue", "LTM", "$60.1M").ok)
        self.assertTrue(nc.check(self.src, "adjusted_ebitda", "Q2 2025", "$2.6M").ok)
        self.assertEqual(nc.check(self.src, "revenue", "LTM", "$58.6M").status, "wrong_period")

    def test_footnote_marks_do_not_change_the_metric(self):
        self.assertEqual(nc.canon_metric("Adjusted EBITDA (1)"), "adjusted_ebitda")
        self.assertEqual(nc.canon_metric("Net Sales*"), "revenue")
        self.assertEqual(nc.canon_metric("Net Income (Loss)"), "net_income")
        self.assertEqual(self.src.direct("revenue", "FY2023", "usd").value, 51900000)

    def test_footnote_text_is_not_a_table_row(self):
        self.assertNotIn(Fraction(1200000), {f.value for f in self.src.facts})

    def test_wrong_sign(self):
        self.assertEqual(nc.check(self.src, "net_income", "FY2023", "$0.3M").status, "wrong_sign")
        self.assertTrue(nc.check(self.src, "net_income", "FY2023", "($0.3M)").ok)

    def test_source_that_disagrees_with_itself(self):
        self.assertIn(("revenue", "FY2024"), self.src.conflicts)
        for text in ("$58.6M", "$58.9M"):   # either statement supports a claim, with a warning
            v = nc.check(self.src, "revenue", "FY2024", text)
            self.assertTrue(v.ok, text)
            self.assertIn("disagrees with itself", v.note)
        self.assertEqual(nc.check(self.src, "revenue", "FY2024", "$58.7M").status, "value_mismatch")

    def test_coarser_restatement_does_not_loosen_the_check(self):
        # sample_cim.txt prints 58,644 in the table and "$58.6 million" in a sentence
        src = nc.Source(CIM)
        self.assertNotIn(("revenue", "FY2024"), src.conflicts)
        self.assertEqual(nc.check(src, "revenue", "FY2024", "$58,610K").status, "value_mismatch")

    def test_cagr_and_growth(self):
        self.assertTrue(nc.check(self.src, "revenue_cagr", "FY2023-FY2024", "12.9%").ok)
        self.assertEqual(nc.check(self.src, "revenue_cagr", "FY2024-FY2023", "12.9%").status, "not_found")
        self.assertEqual(nc.check(self.src, "revenue_growth", "LTM", "2.6%").status, "not_found")
        # growth from a loss is not defined, so it is not computed
        self.assertEqual(nc.check(self.src, "net_income_growth", "FY2024", "900.0%").status, "not_found")

    def test_sentence_with_two_periods_is_not_a_table_header(self):
        src = nc.Source("Between FY2023 and FY2024 the Company added 2 branches.\nRevenue 10 20\n")
        self.assertEqual(src.facts, [])


class Synthetic(unittest.TestCase):
    def test_small_corpus(self):
        wrong_passed = right_failed = n = 0
        for text, _truth, claims in corpus(seed=3, docs=40):
            src = nc.Source(text)
            for c in claims:
                v = nc.check(src, c["metric"], c["period"], c["value"])
                n += 1
                if c["error"] is None:
                    right_failed += not v.ok
                elif v.ok:
                    # the only accepted "mistakes" may be claims finer than the source prints
                    f = v.evidence[0]
                    self.assertGreater(f.tol, nc.parse_amount(c["value"]).tol)
                    wrong_passed += 1
        self.assertGreater(n, 500)
        self.assertEqual(right_failed, 0)
        self.assertLessEqual(wrong_passed, 2)


if __name__ == "__main__":
    unittest.main()
