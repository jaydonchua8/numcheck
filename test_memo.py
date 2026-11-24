import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import memo
import numcheck as nc
from gen import memo_corpus

HERE = os.path.dirname(os.path.abspath(__file__))
CIM = nc.read_text(os.path.join(HERE, "sample_cim.txt"))
MEMO = nc.read_text(os.path.join(HERE, "sample_memo.md"))


def rows(text, source=CIM):
    return [(c.text.strip(), c.metric, c.period, c.status) for c in memo.check_memo(source, text)]


class Scanning(unittest.TestCase):
    def test_number_kinds(self):
        got = [(n.text.strip(), n.kind) for n in memo.scan_numbers(
            "$58.6M, 58.6 million, 16.0%, 12 percent, 1,450 customers, 8.5x, ($0.3M), -$2.1mm")]
        self.assertEqual(got, [("$58.6M", "usd"), ("58.6 million", "usd"), ("16.0%", "pct"),
                               ("12 percent", "pct"), ("1,450", "count"), ("8.5x", "multiple"),
                               ("($0.3M)", "usd"), ("-$2.1mm", "usd")])

    def test_negative_forms(self):
        vals = [n.amount.value for n in memo.scan_numbers("($0.3M) and -$2.1mm")]
        self.assertEqual(vals, [-300000, -2100000])

    def test_digits_inside_words_and_periods_are_not_numbers(self):
        sent = "In FY2024 and Q3 2025 the 3rd plant shipped SKU-77b."
        marks = [(a, b) for a, b, _, _ in memo._periods(sent)]
        self.assertEqual(memo.scan_numbers(sent, marks), [])

    def test_founding_year_is_not_a_period(self):
        self.assertEqual(memo._periods("The Company was founded in 2009."), [])
        self.assertEqual([m[2] for m in memo._periods("Revenue in 2009 was low.")], ["FY2009"])


class Binding(unittest.TestCase):
    def test_two_metrics_one_period(self):
        self.assertEqual(rows("In FY2024, revenue was $58.6M and Adjusted EBITDA was $9.4M."), [
            ("$58.6M", "revenue", "FY2024", "supported"),
            ("$9.4M", "adjusted_ebitda", "FY2024", "supported")])

    def test_from_to_with_trailing_periods(self):
        self.assertEqual(rows("Revenue grew from $51.9M in FY2023 to $58.6M in FY2024."), [
            ("$51.9M", "revenue", "FY2023", "supported"),
            ("$58.6M", "revenue", "FY2024", "supported")])

    def test_margin_in_parentheses_and_share_of_revenue(self):
        got = rows("FY2024 Adj. EBITDA was $9.4M (16.0% margin), or 16.0% of revenue.")
        self.assertEqual([g[1] for g in got], ["adjusted_ebitda", "adjusted_ebitda_margin", "adjusted_ebitda_margin"])
        self.assertEqual(got[1][3], "supported_derived")

    def test_metric_after_the_number(self):
        self.assertEqual(rows("The Company generated $58.6M of revenue and $9.4M of Adjusted EBITDA in FY2024."), [
            ("$58.6M", "revenue", "FY2024", "supported"),
            ("$9.4M", "adjusted_ebitda", "FY2024", "supported")])

    def test_metric_list_is_read_in_order(self):
        for sent in ["Revenue and gross profit were $58.6M and $18.2M, respectively, in FY2024.",
                     "Revenue, gross profit and Adjusted EBITDA for FY2024 were $58.6M, $18.2M and $9.4M."]:
            got = rows(sent)
            self.assertEqual([g[1] for g in got][:2], ["revenue", "gross_profit"], sent)
            self.assertTrue(all(g[3] == "supported" for g in got), sent)

    def test_growth_and_decline(self):
        self.assertEqual(rows("Revenue grew 13.0% in FY2024.")[0][1:], ("revenue_growth", "FY2024", "supported"))
        c = memo.check_memo(CIM, "Revenue declined 13.0% in FY2024.")[0]
        self.assertEqual((c.amount.value, c.status), (-13, "wrong_sign"))

    def test_relative_period(self):
        got = rows("Revenue increased 13.0% in FY2024, from $51.9M in the prior year.")
        self.assertEqual(got[1], ("$51.9M", "revenue", "FY2023", "supported"))
        got = rows("FY2024 revenue of $58.6M was up from $51.9M a year earlier.")
        self.assertEqual(got[1], ("$51.9M", "revenue", "FY2023", "supported"))

    def test_cagr(self):
        got = rows("The revenue CAGR from FY2022 to FY2024 was 12.6%.")
        self.assertEqual(got, [("12.6%", "revenue_cagr", "FY2022-FY2024", "supported_derived")])
        self.assertEqual(rows("The revenue CAGR from FY2022 to FY2024 was 14.0%.")[0][3], "value_mismatch")

    def test_heading_gives_the_period_to_bullets(self):
        got = rows("## FY2024 snapshot\n\n- Revenue: $58.6M\n- Adj. EBITDA: $9.4M (16.0% margin)\n")
        self.assertEqual([(g[1], g[2]) for g in got], [
            ("revenue", "FY2024"), ("adjusted_ebitda", "FY2024"), ("adjusted_ebitda_margin", "FY2024")])

    def test_period_carried_from_earlier_sentence_is_marked_weak(self):
        cs = memo.check_memo(CIM, "Revenue was $58.6M in FY2024. Gross profit was $18.2M.")
        self.assertEqual((cs[1].period, cs[1].status), ("FY2024", "supported"))
        self.assertIn("carried over", cs[1].weak)

    def test_memo_table_cells(self):
        got = rows("| Metric | FY2023 | FY2024 |\n|---|---|---|\n| Revenue | $51.9M | $58.6M |\n| Capex | $840 | $0.7M |\n")
        self.assertEqual(got, [("$51.9M", "revenue", "FY2023", "supported"),
                               ("$58.6M", "revenue", "FY2024", "supported"),
                               ("$840", "capex", "FY2023", "unit_mismatch"),
                               ("$0.7M", "capex", "FY2024", "supported")])


class NotGuessing(unittest.TestCase):
    """Numbers the text does not pin down must be reported, not passed and not called wrong."""

    def test_counts_multiples_and_market_size_are_unbound(self):
        text = ("Harbor serves 1,450 customers and employs 220 people. The market is $3.2 billion. "
                "We would pay 8.5x Adjusted EBITDA.")
        got = {g[0]: g[3] for g in rows(text)}
        self.assertEqual(got, {"1,450": "unbound_in_source", "220": "unbound_not_in_source",
                               "$3.2 billion": "unbound_in_source", "8.5x": "unbound_not_in_source"})

    def test_share_of_revenue_is_not_a_margin(self):
        got = rows("In FY2024 revenue was $58.6M, and vendor programs were 41% of sales.")
        self.assertEqual(got[1][1:], (None, None, "unbound_in_source"))

    def test_percentage_without_margin_or_growth_wording(self):
        self.assertEqual(rows("FY2024 revenue was 62% recurring.")[0][3], "unbound_not_in_source")

    def test_comparison_figure_without_a_period(self):
        got = rows("Revenue rose to $58.6M in FY2024 from $51.9M.")
        self.assertEqual(got[0], ("$58.6M", "revenue", "FY2024", "supported"))
        self.assertEqual(got[1][1:], (None, None, "unbound_in_source"))
        note = memo.check_memo(CIM, "Revenue rose to $58.6M in FY2024 from $51.9M.")[1].note
        self.assertIn("revenue FY2023", note)   # the reviewer is told where the value does appear

    def test_no_period_anywhere(self):
        self.assertEqual(rows("Revenue was $58.6M.")[0][1:], (None, None, "unbound_in_source"))

    def test_weak_binding_that_fails_is_ambiguous_not_flagged(self):
        cs = memo.check_memo(CIM, "Revenue was $51.9M and $58.6M in FY2023 and FY2024.")
        self.assertEqual(cs[1].status, memo.AMBIGUOUS)
        self.assertFalse(cs[1].flagged)
        self.assertIn("revenue FY2024", cs[1].note)

    def test_weak_binding_with_no_matching_alternative_stays_flagged(self):
        cs = memo.check_memo(CIM, "Revenue was $51.9M and $77.7M in FY2023 and FY2024.")
        self.assertTrue(cs[1].flagged)

    def test_strong_binding_is_never_downgraded(self):
        # the FY2023 value under an explicit FY2024 is an error even though FY2023 is named too
        cs = memo.check_memo(CIM, "Revenue grew from $51.9M in FY2023 to $51.9M in FY2024.")
        self.assertEqual(cs[1].status, "wrong_period")

    def test_table_cell_with_no_unit_is_unbound(self):
        got = rows("| Metric | FY2023 | FY2024 |\n| Revenue | 51.9 | 58.6 |\n")
        self.assertEqual({g[3] for g in got}, {"unbound_not_in_source"})

    def test_empty_and_numberless_memos(self):
        self.assertEqual(memo.check_memo(CIM, ""), [])
        self.assertEqual(memo.check_memo(CIM, "No figures were available.\n"), [])

    def test_source_with_no_tables(self):
        cs = memo.check_memo("Nothing useful here.\n", "In FY2024, revenue was $58.6M.")
        self.assertEqual(cs[0].status, "not_found")


class SampleMemo(unittest.TestCase):
    def setUp(self):
        self.claims = memo.check_memo(CIM, MEMO)

    def test_every_number_gets_exactly_one_status(self):
        s = memo.summarize(self.claims)
        self.assertEqual(s["numbers"], 32)
        self.assertEqual(s["supported"] + s["flagged"] + s["ambiguous_binding"]
                         + s["unbound_in_source"] + s["unbound_not_in_source"], s["numbers"])

    def test_planted_mistakes(self):
        flagged = [(c.text, c.metric, c.period, c.status) for c in self.claims if c.flagged]
        self.assertEqual(flagged, [
            ("$15.6M", "gross_profit", "FY2024", "wrong_period"),
            ("$9.4M", "ebitda", "FY2024", "wrong_metric"),
            ("$0.3M", "net_income", "FY2022", "wrong_sign"),
            ("$612", "capex", "FY2022", "unit_mismatch")])
        unseen = [c.text for c in self.claims if c.status == memo.UNBOUND_UNSEEN]
        self.assertEqual(unseen, ["220", "8.5x", "$80 million"])

    def test_columns_point_at_the_number(self):
        lines = MEMO.splitlines()
        for c in self.claims:
            self.assertEqual(lines[c.line - 1][c.col:c.col + len(c.text)], c.text)

    def test_json_output(self):
        data = memo.to_json(self.claims)
        json.dumps(data)
        first = next(d for d in data if d["status"] == "supported")
        self.assertEqual((first["metric"], first["period"], first["source_lines"]), ("revenue", "FY2024", [17]))


class CommandLine(unittest.TestCase):
    def run_main(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = memo.main(["memo.py", *args])
        return code, out.getvalue()

    def test_exit_code_1_when_something_is_flagged(self):
        code, out = self.run_main(os.path.join(HERE, "sample_cim.txt"), os.path.join(HERE, "sample_memo.md"))
        self.assertEqual(code, 1)
        self.assertIn("32 numbers in the memo: 20 supported, 4 flagged", out)

    def test_clean_memo_exits_0_and_strict_fails_on_unseen_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("In FY2024, revenue was $58.6M. The Company employs 220 people.\n")
            src = os.path.join(HERE, "sample_cim.txt")
            self.assertEqual(self.run_main(src, path)[0], 0)
            self.assertEqual(self.run_main(src, path, "--strict")[0], 1)
            code, out = self.run_main(src, path, "--json")
            self.assertEqual(json.loads(out)["summary"]["supported"], 1)

    def test_bad_arguments_and_missing_files(self):
        self.assertEqual(self.run_main()[0], 2)
        self.assertEqual(self.run_main("a", "b", "--nope")[0], 2)
        code, out = self.run_main("/nonexistent/source.txt", "/nonexistent/memo.md")
        self.assertEqual(code, 2)
        self.assertIn("could not read input", out)

    def test_binary_input_is_rejected_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.bin")
            with open(path, "wb") as f:
                f.write(b"\xff\xfe\x00\x81")
            self.assertEqual(self.run_main(path, path)[0], 2)


class SyntheticMemos(unittest.TestCase):
    def score(self, split, seed, docs):
        n = {"right": 0, "right_flagged": 0, "wrong": 0, "wrong_passed": 0, "other_flagged": 0, "missing": 0}
        for text, memo_text, planted in memo_corpus(seed, docs, split):
            claims = {(c.line, c.col): c for c in memo.check_memo(text, memo_text)}
            want = {(f["line"], f["col"]) for f in planted}
            for f in planted:
                c = claims.get((f["line"], f["col"]))
                if c is None:
                    n["missing"] += 1
                elif f["error"] is None:
                    n["right"] += 1
                    n["right_flagged"] += c.flagged
                else:
                    n["wrong"] += 1
                    n["wrong_passed"] += c.passed
            n["other_flagged"] += sum(c.flagged for k, c in claims.items() if k not in want)
        return n

    def test_dev_split(self):
        n = self.score("dev", 11, 40)
        self.assertGreater(n["right"] + n["wrong"], 500)
        self.assertEqual((n["missing"], n["right_flagged"], n["wrong_passed"], n["other_flagged"]), (0, 0, 0, 0))

    def test_scanner_finds_every_planted_number_on_held_out_text(self):
        for split in ("heldout_a", "heldout_b"):
            n = self.score(split, 5, 30)
            self.assertEqual(n["missing"], 0, split)
            self.assertEqual(n["other_flagged"], 0, split)

    def test_held_out_b_error_rates_stay_within_what_the_readme_reports(self):
        # Not a target I tuned to: a guard so a later change that makes unseen phrasing
        # worse is noticed. The README reports 4.3% and 1.4% on the full split.
        n = self.score("heldout_b", 2027, 60)
        self.assertLess(n["right_flagged"] / n["right"], 0.07)
        self.assertLess(n["wrong_passed"] / n["wrong"], 0.03)


if __name__ == "__main__":
    unittest.main()
