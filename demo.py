"""Demo: check a hand-written one-page memo against a hand-written CIM excerpt.

    python demo.py

Both sample files are made up for this repository. The memo has six mistakes written
into it on purpose. The demo prints what the checker flags, what it leaves for a person
to review, and what it accepts, with the source line each decision rests on.
"""
import os
import sys

import memo

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    source = memo.nc.read_text(os.path.join(HERE, "sample_cim.txt"))
    text = memo.nc.read_text(os.path.join(HERE, "sample_memo.md"))
    memo_lines = text.splitlines()
    claims = memo.check_memo(source, text)

    def show(title, picked, with_context=False):
        print("%s (%d)" % (title, len(picked)))
        for c in picked:
            slot = "%s %s" % (c.metric, c.period) if c.bound else "not tied to a metric and period"
            print("  memo line %d: %s  [%s]" % (c.line, c.text.strip(), slot))
            if with_context:
                line = memo_lines[c.line - 1]
                a, b = max(0, c.col - 45), min(len(line), c.col + len(c.text) + 25)
                print("      \"%s%s%s\"" % ("..." if a else "", line[a:b].strip(), "..." if b < len(line) else ""))
            print("      %s: %s" % (c.status, c.note))
        print()

    show("FLAGGED: the source says something else", [c for c in claims if c.flagged], True)
    show("REVIEW: in the memo but nowhere in the source",
         [c for c in claims if c.status == memo.UNBOUND_UNSEEN], True)
    show("REVIEW: could not be tied to one metric and period, value does appear in the source",
         [c for c in claims if c.status in (memo.UNBOUND_SEEN, memo.AMBIGUOUS)])
    ok = [c for c in claims if c.passed]
    print("SUPPORTED (%d), each with the source line it matches. First five:" % len(ok))
    for c in ok[:5]:
        print("  memo line %d: %-8s %s %s <- %s" % (c.line, c.text.strip(), c.metric, c.period, c.note))
    print()
    s = memo.summarize(claims)
    print("%d numbers in the memo. %d supported, %d flagged, %d left for review. Nothing was skipped."
          % (s["numbers"], s["supported"], s["flagged"], s["numbers"] - s["supported"] - s["flagged"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
