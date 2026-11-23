# numcheck

Check that every financial figure in an AI-written memo is backed by the source document.

I built this after reading an internship posting for a company that makes AI agents for private equity deal teams. The posting lists "design and run evaluations that measure accuracy and reliability" as part of the job. When an agent turns a confidential information memorandum (CIM) into a memo, the mistakes that worry me are numeric ones that look fine: last year's EBITDA under this year's heading, EBITDA where Adjusted EBITDA was meant, or a table in thousands read as dollars. `numcheck` takes the source text and a list of claims (metric, period, value as written) and says for each one whether the source supports it, and if not, what kind of mistake it looks like and which source line shows it.

It is one file, about 390 lines of Python, with no dependencies.

## Results

`python bench.py` generates 400 synthetic documents (a financial table, an operating metrics table and a few sentences each) and 6,384 claims about them. 3,200 claims are correct and 3,184 carry one planted mistake. Three methods see the same text and claims:

- string match: the claimed number, as written, appears somewhere in the document
- any value: the claimed value equals some number in the document after unit scaling, ignoring which metric and period that number belongs to
- numcheck: the value must match the same metric and period, or be computable from them

| mistake | claims | string match | any value | numcheck |
|---|---|---|---|---|
| scale | 452 | 39.8% | 99.8% | 100.0% |
| period | 731 | 78.0% | 33.8% | 100.0% |
| metric | 783 | 74.8% | 28.5% | 100.0% |
| digit | 738 | 99.5% | 99.1% | 99.7% |
| fabricated | 480 | 98.8% | 97.5% | 100.0% |
| all wrong claims caught | 3184 | 79.9% | 66.6% | 99.9% |
| correct claims rejected | 3200 | 74.5% | 19.7% | 0.0% |

The mistakes are: the right digits at the wrong scale, the same metric from another year, a neighbouring metric from the same year, one changed or swapped digit, and a figure for a year or metric the document does not contain.

What the table shows me is that the two simple checks fail in opposite ways. String match rejects 74.5% of correct claims, because a memo says "$47.3M" where the table says "47,310". Matching on value alone fixes that but lets most period and metric mix-ups through (it catches 33.8% and 28.5% of them), because the wrong number really is in the document, just in another cell.

numcheck let 2 wrong claims through out of 3,184. Both were claims with more digits than the source prints ("$3,209K" against a sentence that says "$3.2 million"). I accept anything that rounds to the source figure, so these pass. I think that is defensible but it is a choice, not a proof that the claim is right.

When numcheck flags a claim it also names the kind of mistake. It named it correctly for 100.0% of scale mistakes, 100.0% of period mistakes and 98.7% of metric mistakes. The rest of the metric mistakes were still flagged, but as a wrong period, because the value also happened to match another year.

Speed on the last run was 3,418 claims per second including parsing. It moved between about 3,400 and 5,400 across runs on the same machine.

## Example

`python numcheck.py sample_cim.txt sample_claims.json` runs 11 claims against a CIM excerpt I wrote by hand (not from the generator). Part of the output:

```
supported         revenue                  FY2024  $58.6M         line 17 (revenue FY2024 = 58,644)
supported_derived adjusted_ebitda_margin   FY2024  16.0%          line 21 (adjusted_ebitda FY2024 = 9,383), line 17 (revenue FY2024 = 58,644)
wrong_metric      ebitda                   FY2024  $9.4M          this is adjusted_ebitda, not ebitda; source: line 21 (adjusted_ebitda FY2024 = 9,383)
wrong_period      gross_profit             FY2024  $15.6M         this is the FY2023 figure; source: line 19 (gross_profit FY2023 = 15,572)
unit_mismatch     revenue                  FY2023  $51,907        right digits, wrong scale by a factor of 1000; source: line 17 (revenue FY2023 = 51,907)
not_found         revenue                  FY2021  $41.5M         the source states no revenue for FY2021
```

From Python:

```python
from numcheck import Source, check

src = Source(open("sample_cim.txt").read())
v = check(src, "adjusted_ebitda", "FY2024", "$9.4 million")
print(v.status, v.ok, [f.line for f in v.evidence])
```

## How it works

Reading the source. A table starts at a line with two or more period headers (`FY2023`, `FY23`, `2023A`, `2024E`). The unit scale comes from a declaration such as `($ in thousands)` or `(USD in 000s)` on that line or within two lines above it. A table with no declaration is treated as counts, so customer and headcount rows never become dollar figures. Row labels are mapped to a canonical metric through an alias list (`Net Sales` to `revenue`, `Adj. EBITDA` to `adjusted_ebitda`). Sentences are read with a narrower pattern: a known metric name, a verb such as "was" or "reached", an amount, and a period in the same sentence. Every fact keeps its line number.

Tolerance. All values are exact fractions. A written number is taken to be correct to half a unit of its last digit, so "$47.3M" matches anything from 47.25 to 47.35 million and "$47,310K" must be within 500 dollars. When a table and a sentence both state a figure, the more precise one is used.

Derived figures. Margins and growth rates are often not printed. If a percentage claim has no printed match, it is computed from the printed inputs (for example Adjusted EBITDA over revenue) and the verdict cites both input lines.

Diagnosis. If the claimed slot does not match, the checker tries the same slot at a scale of a thousand, a million or a billion, then the same metric in other periods, then other metrics in the same period. The first match names the mistake. If nothing matches, the verdict is `value_mismatch` when the source has a figure for that slot and `not_found` when it does not.

## What did not work at first

My first table reader split columns on runs of two or more spaces. That passed every test I had. Then I collapsed whitespace in the benchmark documents to imitate text copied out of a PDF, and it rejected 57.3% of correct claims (1,834 of 3,200) because the tables no longer parsed. I changed it to peel value-like tokens off the right end of each line and treat the rest as the label. On the same collapsed documents it now rejects 0 of 3,200. There is a test for this.

## Limits

- The benchmark documents are synthetic, and I wrote both the generator and the parser. The 0.0% false rejection rate means the parser handles the layouts I thought of. It is not a prediction for real CIMs, which have merged headers, footnotes, quarterly and LTM columns, and tables that span pages.
- It reads plain text. It does not open PDFs or spreadsheets, and it is not connected to any product or model. The claims are structured input. Pulling claims out of memo prose is a separate step that I did not build.
- Sentence parsing is narrow. "representing a 19.2% margin" is not read as a fact, and a sentence with two periods assigns each amount to the nearest period before it, which can be wrong.
- Periods are fiscal years only. Metric names outside the alias list are kept under their own label, so a synonym I did not list comes back as `not_found`.
- A claim more precise than the source passes if it rounds to the source figure.

## Run it

```
python test_numcheck.py
python bench.py
python numcheck.py sample_cim.txt sample_claims.json
```

Python 3.9 or newer. Nothing to install.
