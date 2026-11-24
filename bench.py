"""Measure the checker on synthetic documents with injected mistakes.

Part one checks structured claims. Part two checks whole memos on three splits: the dev
split the binding rules were written against, and two held-out splits (see gen.py).

Structured claims:

Three methods see the same document text and the same claims:
  string   the claimed number, as written, appears somewhere in the document
  anyvalue the claimed value equals some number in the document after unit scaling,
           ignoring which metric and period that number belongs to
  numcheck value must match the same metric and period, or be computable from them
"""
import json
import re
import sys
import time
from collections import Counter, defaultdict

import memo
import numcheck
from gen import corpus, memo_corpus

SPLIT_SEEDS = [("dev", 7), ("heldout_a", 1009), ("heldout_b", 2027)]

EXPECT = {"scale": {numcheck.UNIT}, "period": {numcheck.PERIOD}, "metric": {numcheck.METRIC}}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def string_ok(text, claim):
    tokens = set(_NUM.findall(text))
    return _NUM.search(claim["value"]).group(0) in tokens


def anyvalue_ok(src, claim):
    a = numcheck.parse_amount(claim["value"])
    return any(f.kind == a.kind and abs(f.value - a.value) <= max(f.tol, a.tol) for f in src.facts)


def run(seed=7, docs=400):
    n = Counter()
    caught = defaultdict(Counter)
    diag = Counter()
    false_reject = Counter()
    statuses = Counter()
    spent = 0.0
    for text, _truth, claims in corpus(seed, docs):
        t0 = time.perf_counter()
        src = numcheck.Source(text)
        verdicts = [numcheck.check(src, c["metric"], c["period"], c["value"]) for c in claims]
        spent += time.perf_counter() - t0
        for c, v in zip(claims, verdicts):
            ok = {"numcheck": v.ok, "string": string_ok(text, c), "anyvalue": anyvalue_ok(src, c)}
            err = c["error"]
            n[err or "correct"] += 1
            statuses[v.status] += 1
            for name, passed in ok.items():
                if err is None:
                    false_reject[name] += not passed
                else:
                    caught[name][err] += not passed
            if err in EXPECT and v.status in EXPECT[err]:
                diag[err] += 1
    total = sum(n.values())
    wrong = total - n["correct"]
    res = {"seed": seed, "docs": docs, "claims": total, "correct_claims": n["correct"],
           "wrong_claims": wrong, "by_error": {}, "false_reject_pct": {}, "caught_pct": {},
           "diagnosis_pct": {}, "claims_per_second": round(total / spent),
           "numcheck_statuses": dict(statuses)}
    for name in ("string", "anyvalue", "numcheck"):
        res["false_reject_pct"][name] = round(100.0 * false_reject[name] / n["correct"], 1)
        res["caught_pct"][name] = round(100.0 * sum(caught[name].values()) / wrong, 1)
    for err in ("scale", "period", "metric", "digit", "fabricated"):
        res["by_error"][err] = {"n": n[err], **{name: round(100.0 * caught[name][err] / n[err], 1)
                                               for name in ("string", "anyvalue", "numcheck")}}
    for err in EXPECT:
        res["diagnosis_pct"][err] = round(100.0 * diag[err] / n[err], 1)
    return res


def run_memos(seed, docs, split):
    """Whole memos: scan, bind and check every number, then score against what was planted."""
    n = Counter()
    by_err = defaultdict(Counter)
    misses = []
    for text, memo_text, planted in memo_corpus(seed, docs, split):
        src = numcheck.Source(text)
        index = memo.SourceIndex(text, src)
        claims = {(c.line, c.col): c for c in memo.check_memo(text, memo_text)}
        want = {(f["line"], f["col"]) for f in planted}
        for f in planted:
            c = claims.get((f["line"], f["col"]))
            bound = c is not None and c.bound
            right_slot = bound and (c.metric, c.period) == (f["metric"], f["period"])
            passed = bound and c.passed
            review = c is None or not c.bound or c.status == memo.AMBIGUOUS
            seen = c is not None and c.amount is not None and bool(index.find(c.amount))
            n["figures"] += 1
            n["bound"] += bound
            n["bound_right"] += right_slot
            n["bound_wrong"] += bound and not right_slot
            n["weak"] += bool(bound and c.weak)
            if f["error"] is None:
                n["right"] += 1
                n["right_supported"] += passed
                n["right_unbound"] += review
                n["right_flagged"] += not review and not passed
                n["right_flagged_weak"] += bool(not review and not passed and c.weak)
                n["anyvalue_right_rejected"] += not seen
                if not review and not passed and len(misses) < 400:
                    misses.append(("false alarm", f, c.metric, c.period, c.status))
            else:
                n["wrong"] += 1
                n["wrong_flagged"] += not review and not passed
                n["wrong_unbound"] += review
                n["wrong_passed"] += passed
                n["anyvalue_wrong_caught"] += not seen
                by_err[f["error"]]["n"] += 1
                by_err[f["error"]]["flagged"] += not review and not passed
                by_err[f["error"]]["unbound"] += review
                by_err[f["error"]]["passed"] += passed
                if passed and len(misses) < 400:
                    misses.append(("wrong figure passed", f, c.metric, c.period, c.status))
        for key, c in claims.items():
            if key not in want:
                n["other_numbers"] += 1
                n["other_bound"] += c.bound
                n["other_flagged"] += bool(c.flagged)
                n["other_supported"] += bool(c.passed)

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else 0.0
    res = {"split": split, "seed": seed, "docs": docs, "counts": dict(n),
           "by_error": {k: dict(v) for k, v in sorted(by_err.items())},
           "pct": {
               "figures_bound_to_right_slot": pct(n["bound_right"], n["figures"]),
               "figures_bound_to_wrong_slot": pct(n["bound_wrong"], n["figures"]),
               "figures_left_unbound": pct(n["figures"] - n["bound"], n["figures"]),
               "wrong_figures_flagged": pct(n["wrong_flagged"], n["wrong"]),
               "wrong_figures_unbound": pct(n["wrong_unbound"], n["wrong"]),
               "wrong_figures_passed": pct(n["wrong_passed"], n["wrong"]),
               "right_figures_supported": pct(n["right_supported"], n["right"]),
               "right_figures_unbound": pct(n["right_unbound"], n["right"]),
               "right_figures_flagged": pct(n["right_flagged"], n["right"]),
               "anyvalue_wrong_figures_caught": pct(n["anyvalue_wrong_caught"], n["wrong"]),
               "anyvalue_right_figures_rejected": pct(n["anyvalue_right_rejected"], n["right"]),
               "other_numbers_bound": pct(n["other_bound"], n["other_numbers"]),
           }}
    return res, misses


def print_memos(r):
    c, p = r["counts"], r["pct"]
    print("%s split: seed %d, %d memos, %d planted figures (%d correct, %d wrong), %d other numbers"
          % (r["split"], r["seed"], r["docs"], c["figures"], c["right"], c["wrong"], c["other_numbers"]))
    print("  bound to the right metric and period: %.1f%%   wrong slot: %.1f%%   left unbound: %.1f%%"
          % (p["figures_bound_to_right_slot"], p["figures_bound_to_wrong_slot"], p["figures_left_unbound"]))
    print("  wrong figures:   flagged %.1f%%   for review %.1f%%   passed %.1f%% (%d)"
          % (p["wrong_figures_flagged"], p["wrong_figures_unbound"], p["wrong_figures_passed"],
             c.get("wrong_passed", 0)))
    print("  correct figures: supported %.1f%%   for review %.1f%%   flagged %.1f%% (%d, of which %d weak bindings)"
          % (p["right_figures_supported"], p["right_figures_unbound"], p["right_figures_flagged"],
             c.get("right_flagged", 0), c.get("right_flagged_weak", 0)))
    print("  any-value baseline: catches %.1f%% of wrong figures, rejects %.1f%% of correct ones"
          % (p["anyvalue_wrong_figures_caught"], p["anyvalue_right_figures_rejected"]))
    print("  other numbers (counts, market size, multiples): %d, of which %d were bound and %d flagged"
          % (c["other_numbers"], c.get("other_bound", 0), c.get("other_flagged", 0)))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    docs = int(args[0]) if args else 400
    r = run(docs=docs)
    print("Structured claims (numcheck.check only)")
    print("%d documents, %d claims (%d correct, %d wrong), seed %d"
          % (r["docs"], r["claims"], r["correct_claims"], r["wrong_claims"], r["seed"]))
    print()
    print("| mistake | claims | string match | any value | numcheck |")
    print("|---|---|---|---|---|")
    for err, row in r["by_error"].items():
        print("| %s | %d | %.1f%% | %.1f%% | %.1f%% |"
              % (err, row["n"], row["string"], row["anyvalue"], row["numcheck"]))
    print("| all wrong claims caught | %d | %.1f%% | %.1f%% | %.1f%% |"
          % (r["wrong_claims"], r["caught_pct"]["string"], r["caught_pct"]["anyvalue"],
             r["caught_pct"]["numcheck"]))
    print("| correct claims rejected | %d | %.1f%% | %.1f%% | %.1f%% |"
          % (r["correct_claims"], r["false_reject_pct"]["string"],
             r["false_reject_pct"]["anyvalue"], r["false_reject_pct"]["numcheck"]))
    print()
    print("numcheck named the right kind of mistake: " + ", ".join(
        "%s %.1f%%" % kv for kv in r["diagnosis_pct"].items()))
    print("speed: %d claims per second (parsing included)" % r["claims_per_second"])
    print()
    print("Whole memos (memo.py binds each number, numcheck checks it)")
    out = {"structured_claims": r, "memos": {}}
    for split, seed in SPLIT_SEEDS[:1] if "--dev-only" in sys.argv else SPLIT_SEEDS:
        m, misses = run_memos(seed, docs, split)
        print_memos(m)
        if "--misses" in sys.argv:
            for kind, f, metric, period, status in misses[:40]:
                print("    %s: planted %s -> bound %s %s, %s" % (kind, f, metric, period, status))
        out["memos"][m["split"]] = m
    if "--dev-only" not in sys.argv:
        with open("bench_results.json", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
