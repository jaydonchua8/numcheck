"""Measure the checker on synthetic documents with injected mistakes.

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

import numcheck
from gen import corpus

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


def main():
    docs = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    r = run(docs=docs)
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
    json.dump(r, open("bench_results.json", "w"), indent=1)


if __name__ == "__main__":
    main()
