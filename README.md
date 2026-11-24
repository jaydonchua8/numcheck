# numcheck

Check every number in an AI-written deal memo against the source document, and say which source line each decision rests on.

This is an independent prototype that I built on my own. It is not affiliated with, endorsed by or reviewed by Zarna or any other company. All documents and memos in this repository are synthetic or written by hand for the repository. It has not been run on a real CIM.

## The problem

When a model turns a confidential information memorandum (CIM) into a one-pager, the mistakes that are hardest to see are numeric ones that look fine: last year's EBITDA under this year's heading, EBITDA where Adjusted EBITDA was meant, a table in thousands read as dollars, a loss written as a profit. Checking whether the number "appears in the document" does not catch these, because the wrong number usually does appear in the document, in another cell.

The first version of this project checked structured claims, meaning a list of (metric, period, value) that someone had already pulled out of the memo. That skipped the hard part. A memo is prose and tables, and deciding which metric and period a number in a sentence refers to is where most of the risk is. This version reads the memo itself.

## Why I built it with Zarna in mind

I read Zarna's public material before deciding what to build. These are the things I verified, and nothing here describes how their system works inside, because I do not know.

- Their site lists "One-pagers and IC memos with scorecard and financial snapshot" and "Teaser and CIM screening against thesis" among the things their agents produce, and says "Every answer carries its sources" and "every field carrying its source" (https://www.zarnaai.com/).
- Their YC launch post says the product converts CIM financials into diligence model structures and drafts IC materials in the firm's format (https://www.ycombinator.com/launches/OmK-zarna-ai-associates-for-private-capital-markets).
- Their AI Software Engineering Intern posting lists "Design and run evaluations that measure accuracy and reliability" and "Develop agents that understand documents, emails, and other unstructured business data" (https://www.ycombinator.com/companies/zarna/jobs/XFa8DgK-ai-software-engineering-intern).
- A post on their blog says their clients have "no appetite for risk in output or performance" (https://www.zarnaai.com/blog/how-to-build-agents-for-private-equity).

So the question I picked is narrow: given a source document and a memo drafted from it, is each financial figure in the memo backed by the source, and where. That is one evaluation someone would want to run on a one-pager's financial snapshot. I assumed, without knowing, that a check like this is useful as an automated eval or as a pre-review step.

## What it does

`memo.py` takes a source text file and a memo (markdown or plain text) and gives every number in the memo exactly one status:

- `supported` or `supported_derived`: the source states this metric for this period with this value, or the value can be computed from printed figures (margins, year-over-year growth, CAGR). The source lines are cited.
- `wrong_period`, `wrong_metric`, `unit_mismatch`, `wrong_sign`, `value_mismatch`, `not_found`: the number is tied to a metric and period and the source says something else. The first four name the likely mistake and cite the line that shows it.
- `ambiguous_binding`: the sentence did not pin the number to one metric and period, my best guess failed, and the value is right for another metric and period that the same sentence names. It is sent to review and not called an error.
- `unbound_in_source` or `unbound_not_in_source`: the number could not be tied to a metric and period at all (a customer count, a market size, a multiple, a figure with no period). It is listed with the source lines where the same value appears, or with a note that it appears nowhere.

The rule I held to is that a number is never silently skipped and never passed on a guess. Anything the checker is unsure about goes to a person with the reason.

## Architecture

Five modules, all standard library, in one flat directory.

| file | role |
|---|---|
| `numcheck.py` | Parses amounts as exact fractions with a rounding tolerance. Reads tables and sentences in the source into facts (metric, period, value, line). Checks one structured claim and diagnoses the mistake. |
| `memo.py` | Scans the memo for numbers, binds each to a metric and period from the words around it, calls `numcheck.check`, and handles what cannot be bound. Command line and JSON output. |
| `gen.py` | Synthetic source documents and memos with planted mistakes, in a dev split and two held-out splits. |
| `bench.py` | Runs the benchmark and writes `bench_results.json`. |
| `demo.py` | Runs the sample memo against the sample CIM excerpt and prints a grouped report. |

Flow for one memo: source text goes to `numcheck.Source` (facts by metric and period, plus a list of places where the document disagrees with itself). Memo text goes to `memo.extract_claims` (tables are read with the same table reader as the source, prose is split into sentences and bound number by number). Each bound claim goes to `numcheck.check`. Each unbound number goes to a value index over the whole source.

## Key decisions and tradeoffs

**Exact arithmetic and rounding.** Values are `Fraction`s. A written number is taken to be correct to half a unit of its last digit, so "$58.6M" matches 58,644 thousand and "$58,610K" does not. When a table and a sentence both state a figure, the more precise one decides. A coarser restatement only counts as an alternative when the two cannot both be right, and then the verdict carries a note that the source disagrees with itself.

**Binding is rule based, and it reports its own uncertainty.** I used rules and not a model because the checker has to be deterministic and has to explain itself. The cost is coverage: rules only read the sentence shapes I wrote them for. To keep that from turning into false alarms, a binding is marked weak when it rests on a guess (a period carried from an earlier sentence, one metric name followed by a list of numbers), and a weak binding that fails is downgraded to `ambiguous_binding` when the sentence itself names a slot that fits. A strong binding is never downgraded. "Revenue grew from $51.9M in FY2023 to $51.9M in FY2024" is flagged even though FY2023 is in the sentence.

**Unbound is a result, not a failure.** "Revenue rose to $58.6M in FY2024 from $51.9M" does not say which period $51.9M is. I leave it unbound and tell the reviewer the value matches revenue FY2023 on line 17. Guessing "the prior year" would be right most of the time and would make the miss invisible the rest of the time.

**Percentages need wording.** A percentage next to "revenue" could be a margin, a growth rate or a share of revenue. It is only bound when the sentence says which ("margin", "grew", "year over year", "CAGR"). "41% of sales" with no margin metric in front of it stays unbound.

**Layouts the first version skipped.** LTM and quarter columns, "fiscal 2024" and "2024A" period names, footnote marks on row labels ("Adjusted EBITDA (1)", "Net Sales*"), footnote text under a table, sign errors, and a source that states the same figure two ways.

## Install and usage

Nothing to install. I wrote it for Python 3.9 or newer and ran it on 3.13.

```
python demo.py
python memo.py sample_cim.txt sample_memo.md
python memo.py sample_cim.txt sample_memo.md --json
python memo.py sample_cim.txt sample_memo.md --strict
python numcheck.py sample_cim.txt sample_claims.json
python bench.py
python -m unittest
```

`memo.py` exits with 1 when any number is flagged, 0 when none is, and 2 on bad arguments or unreadable input. `--strict` also exits with 1 when a number appears nowhere in the source. `bench.py` took between 20 seconds and a minute on my machine.

From Python:

```python
import memo, numcheck

claims = memo.check_memo(numcheck.read_text("sample_cim.txt"), numcheck.read_text("sample_memo.md"))
for c in claims:
    print(c.line, c.text, c.metric, c.period, c.status, c.note)
```

## Example

`sample_cim.txt` is a CIM excerpt and `sample_memo.md` is a one-pager, both written by hand for this repository. The memo has six mistakes written into it. This is part of what `python demo.py` prints:

```
FLAGGED: the source says something else (4)
  memo line 9: $15.6M  [gross_profit FY2024]
      "...1.9M in the prior year. Gross profit reached $15.6M in FY2024, which is a gr..."
      wrong_period: this is the FY2023 figure; source: line 19 (gross_profit FY2023 = 15,572)
  memo line 9: $9.4M  [ebitda FY2024]
      "...which is a gross margin of 31.0%. EBITDA of $9.4M in FY2024 compares with..."
      wrong_metric: this is adjusted_ebitda, not ebitda; source: line 21 (adjusted_ebitda FY2024 = 9,383)
  memo line 15: $0.3M  [net_income FY2022]
      "| Net income | $0.3M | $1.0M | $2.4M |"
      wrong_sign: right size, opposite sign; source: line 22 (net_income FY2022 = (318))
  memo line 16: $612  [capex FY2022]
      "| Capex | $612 | $0.8M | $0.7M |"
      unit_mismatch: right digits, wrong scale by a factor of 1000; source: line 23 (capex FY2022 = 612)

REVIEW: in the memo but nowhere in the source (3)
  memo line 5: 220  [not tied to a metric and period]
      "...The Company was founded in 1987 and employs 220 people. Vendor managed i..."
      unbound_not_in_source: a count with no currency or percent sign; this value appears nowhere in the source
  ...

32 numbers in the memo. 20 supported, 4 flagged, 8 left for review. Nothing was skipped.
```

The source says 212 employees, so the 220 is the fifth mistake. It is caught as a value that appears nowhere in the source, not as a flagged claim, because headcount is not a metric the checker binds. The sixth mistake is not caught as a mistake: the "$80 million" valuation is my own invention in the memo and it also lands in the "nowhere in the source" list, which is the most the checker can say about it.

## Tests and verified results

`python -m unittest` runs 65 tests (28 in `test_numcheck.py`, 37 in `test_memo.py`). They pass with `python -W error -m unittest` on Python 3.13. They cover amount parsing, table and sentence reading, each binding rule, the cases where the checker must not guess, the command line including missing and binary input, and small synthetic corpora.

Every number below is from `python bench.py` on my machine, and `bench_results.json` holds the full output. All data is synthetic and I wrote both the generator and the checker, so read these as a measure of the rules against phrasing I could think of, not as a prediction for real documents.

**Structured claims** (400 documents, 6,384 claims, 3,184 with a planted mistake). This is the benchmark from the first version, rerun on the current code.

| mistake | claims | string match | any value | numcheck |
|---|---|---|---|---|
| scale | 452 | 39.8% | 99.8% | 100.0% |
| period | 731 | 78.0% | 33.8% | 100.0% |
| metric | 783 | 74.8% | 28.5% | 100.0% |
| digit | 738 | 99.5% | 99.1% | 99.7% |
| fabricated | 480 | 98.8% | 97.5% | 100.0% |
| all wrong claims caught | 3184 | 79.9% | 66.6% | 99.9% |
| correct claims rejected | 3200 | 74.5% | 19.7% | 0.0% |

"String match" asks whether the number as written appears in the document. "Any value" asks whether the value appears anywhere after unit scaling. Speed was between about 1,800 and 3,400 claims per second across runs on the same machine.

**Whole memos** (400 memos per split, each with about 18 planted figures of which about a third are wrong, plus distractor numbers such as customer counts and asking prices).

| | dev | held-out A | held-out B |
|---|---|---|---|
| planted figures | 6,914 | 7,422 | 7,391 |
| bound to the right metric and period | 100.0% | 96.1% | 83.4% |
| bound to the wrong one | 0.0% | 0.0% | 4.2% |
| left for review | 0.0% | 3.9% | 12.4% |
| wrong figures flagged | 100.0% | 95.5% | 85.7% |
| wrong figures sent to review | 0.0% | 4.5% | 12.8% |
| wrong figures passed | 0.0% | 0.0% | 1.4% (35 of 2,483) |
| correct figures supported | 100.0% | 96.4% | 83.6% |
| correct figures sent to review | 0.0% | 3.6% | 12.1% |
| correct figures flagged | 0.0% | 0.0% | 4.3% (211 of 4,908) |
| any-value baseline: wrong figures caught | 55.4% | 54.9% | 56.1% |
| distractor numbers flagged | 0 of 1,661 | 0 of 1,882 | 0 of 1,644 |

How to read the three columns:

- **Dev** is the set of sentence templates I wrote the binding rules against. 100% here only shows the rules do what I meant.
- **Held-out A** uses different templates, labels, number styles and seed, and source tables with an LTM column and footnote marks. The table shows the current code. On the first run, before I changed anything, it bound 84.7% of figures to the right slot, flagged 14.8% of correct figures (732 of 4,934) and passed 0.6% of wrong ones (15 of 2,488). Three sentence shapes caused all of the wrong bindings: "rose to X in FY2024 from Y", "A, B and C for FY2024 were X, Y and Z", and "versus Y a year earlier". I fixed those with general rules (comparison figures with no period stay unbound, metric lists are read in order, relative periods can trail a number) and added the `ambiguous_binding` downgrade. After that, A is no longer held out. The first-run numbers cannot be reproduced from the final code.
- **Held-out B** is a third set of templates that I wrote after freezing the binding rules, ran once, and did not tune on. This is the honest number. All 211 false flags and 33 of the 35 passed mistakes come from one sentence shape, "FY2024 sales totaled X, of which Y dropped to gross profit", where the rules attach Y to sales. The other 2 passed mistakes are figures written with more digits than the source prints. Most of the review share comes from "with margins of X% and Y%" and "Between FY2023 and FY2024, sales moved from X to Y", which the rules decline to bind.

What I take from this: on phrasing the rules have not seen, about one figure in eight goes to review, about 4% of correct figures are wrongly flagged, and about 1.4% of planted mistakes get through, and each new sentence shape can move those numbers. The any-value baseline catches a little over half of the mistakes on every split.

## Limitations and next steps

- Everything is synthetic or hand-written. Real CIMs have merged headers, tables that span pages, multiple LTM columns, pro forma and adjusted variants of the same metric, and restated figures. I have not tested on any.
- Input is plain text. There is no PDF, Excel or PowerPoint reader. Text copied from a PDF with single-spaced columns is handled, but layout extraction is its own problem.
- The binding rules are English sentence patterns. Held-out B shows the failure mode: a sentence shape I did not anticipate can produce a confident wrong binding. The next thing I would try is using a language model to propose the (metric, period) for each number and keeping this checker as the deterministic verifier, then measuring both binders on the same held-out sets.
- Metric names come from a fixed alias list of 32 names. A synonym outside it is kept under its own label and comes back as `not_found` or unbound.
- A dated LTM column is treated as "LTM" with no date. Two LTM columns in one document would be merged and reported as a conflict.
- Parallel period lists ("$51.9M and $58.6M in FY2023 and FY2024") are not zipped. The second figure comes back as `ambiguous_binding`.
- A claim written with more digits than the source prints passes if it rounds to the source figure.
- It checks numbers only. It says nothing about whether the memo's wording, names or conclusions are supported.
