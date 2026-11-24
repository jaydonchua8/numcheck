"""memo: find every number in a memo, tie it to a metric and a period, and check it.

numcheck.py checks a structured claim. A memo is prose, so this module does the step in
between: it scans the memo for numbers, binds each one to (metric, period) using the words
around it, and hands the bound ones to numcheck. A number it cannot bind is never passed
and never called wrong. It is reported as unbound, together with whether the same value
appears anywhere in the source, so a reviewer sees every number in the memo exactly once.

Standard library only.
"""
import json
import re
import sys
from dataclasses import dataclass

import numcheck as nc

UNBOUND_SEEN, UNBOUND_UNSEEN = "unbound_in_source", "unbound_not_in_source"
AMBIGUOUS = "ambiguous_binding"

# ---------------------------------------------------------------- scanning

_NUMBER = re.compile(
    r"(?<![\w.,$])(?P<open>\()?(?P<neg>[-−])?(?P<cur>\$\s?|USD\s?)?(?P<neg2>-)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?%|\s?percent\b|x\b|mm\b|bn\b|[kmb]\b|\s(?:thousand|million|billion)\b)?"
    r"(?(open)\))(?![\w])(?![.,]\d)", re.I)
_METRIC = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(
        re.escape(a).replace(r"adj\ ", r"adj\.?\ ")
        for a in sorted(nc._ALIAS_TO_METRIC, key=len, reverse=True) if "%" not in a and "(" not in a)
    + r")(?![A-Za-z])", re.I)
_RELATIVE = re.compile(r"\b(?:the\s+)?(?:prior|previous|preceding)\s+(?:fiscal\s+)?year\b|\ba\s+year\s+earlier\b", re.I)
_NOT_A_PERIOD = re.compile(r"\b(?:founded|established|incorporated|formed|since|acquired)\s+(?:in\s+)?$", re.I)
_SENTENCE = re.compile(r"(?<=[.!?;])(?<!Adj\.)(?<!vs\.)(?<!approx\.)\s+(?=[A-Z(\[$])")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]\s+|\d{1,2}[.)]\s+|#{1,6}\s+(?:\d{1,2}[.)]\s+)?|>\s+)")

_UP = r"grew|grow|grows|growth|growing|increase[ds]?|increasing|rose|rising|up|higher|gain(?:ed)?|expanded"
_DOWN = r"decline[ds]?|declining|decrease[ds]?|decreasing|fell|falling|down|lower|contracted|dropped|shrank"
_CUE_BEFORE = re.compile(r"\b(?P<up>%s)\b|\b(?P<down>%s)\b" % (_UP, _DOWN), re.I)
_CUE_AFTER = re.compile(
    r"^\s*(?:\w+\s+){0,2}?(?:(?P<up>growth|increase|gain|higher)|(?P<down>decline|decrease|drop|lower)"
    r"|(?P<yoy>yoy|y/y|year[- ]over[- ]year|year[- ]on[- ]year|from|over|versus|vs\.?|above|below))(?![\w/])", re.I)
_MARGIN_AFTER = re.compile(r"^\s*(?:\w+\.?\s+){0,3}?margin\b", re.I)
_SHARE_AFTER = re.compile(r"^\s+of\s+(?:net\s+|total\s+)?(?:revenues?|sales)\b", re.I)
_CLAUSE_BREAK = re.compile(r",\s+and\s|;|\b(?:while|whereas|but|although)\b", re.I)
_LIST_GAP = re.compile(r"\s*(?:,|and|,\s*and|or)\s*", re.I)
_CAGR = re.compile(r"\bCAGR\b|compound(?:ed)?\s+annual", re.I)
_COMPARE = re.compile(r"\b(?:from|versus|vs\.?|compared\s+(?:with|to)|against|up\s+from|down\s+from)\s*$", re.I)
_TRAIL = re.compile(r"\b(?:in|for|during)\s+(?:the\s+)?$", re.I)
_OF_METRIC = re.compile(r"^\s+(?:of|in)\s+(?:\w+\s+){0,1}$", re.I)


@dataclass
class Number:
    start: int
    end: int
    text: str
    amount: object   # numcheck.Amount, or None for multiples such as 8.5x
    kind: str        # usd, pct, count or multiple


@dataclass
class Claim:
    line: int            # 1-based line in the memo
    col: int             # 0-based column of the number in that line
    text: str            # the number as written
    kind: str
    amount: object = None
    metric: str = None
    period: str = None
    weak: str = ""       # why the binding is uncertain, empty when it is direct
    why: str = ""        # why it could not be bound
    verdict: object = None
    alternatives: object = None   # (metrics, periods) the sentence names, kept for weak bindings
    status: str = ""
    note: str = ""

    @property
    def bound(self):
        return self.metric is not None and self.period is not None

    @property
    def passed(self):
        return self.status in (nc.SUPPORTED, nc.DERIVED)

    @property
    def flagged(self):
        """Bound, checked and found wrong. Unbound and ambiguous numbers are for review instead."""
        return self.bound and not self.passed and self.status != AMBIGUOUS


def scan_numbers(text, skip=()):
    """Every number in text, left to right. skip is a list of (start, end) spans to ignore."""
    out = []
    for m in _NUMBER.finditer(text):
        if any(a < m.end("num") and m.start("num") < b for a, b in skip):
            continue
        suf = (m.group("suf") or "").strip().lower()
        cur = bool(m.group("cur"))
        raw = m.group(0)
        if suf == "x":
            out.append(Number(m.start(), m.end(), raw, None, "multiple"))
            continue
        body = raw.replace("percent", "%").replace(" %", "%")
        if not cur and suf not in ("%", "percent") and suf and len(suf) == 1:
            continue   # "5 k" or "3 b" with no currency sign is not an amount
        kind = "pct" if suf in ("%", "percent") else ("usd" if cur or suf else "count")
        amt = nc.parse_amount(body.replace("$ ", "$"), 1, kind)
        if amt is None:
            continue
        out.append(Number(m.start(), m.end(), raw, amt, amt.kind))
    return out


def _periods(sent):
    """Explicit and relative period mentions in a sentence: [start, end, period]."""
    marks = []
    for m in nc._PERIOD_PROSE.finditer(sent):
        p = nc.canon_period(m.group(0))
        bare_year = re.fullmatch(r"20\d{2}", m.group(0))
        if p and not (bare_year and _NOT_A_PERIOD.search(sent[: m.start()])):
            marks.append([m.start(), m.end(), p, False])
    anchor = next((p for _, _, p, _ in marks if nc.prior_period(p)), None)
    for m in _RELATIVE.finditer(sent):
        if anchor:
            marks.append([m.start(), m.end(), nc.prior_period(anchor), True])
    marks.sort()
    return marks


# ---------------------------------------------------------------- binding

def _bind_sentence(sent, context):
    """Yield (Number, metric, period, weak, why) for every number in one sentence.

    context is the period inherited from the heading or an earlier sentence:
    (period, how) or None.
    """
    marks = _periods(sent)
    relative = {k for k, m in enumerate(marks) if m[3]}
    periods = [m[:3] for m in marks]
    numbers = scan_numbers(sent, [(a, b) for a, b, _ in periods])
    metrics = [(m.start(), m.end(), nc.canon_metric(m.group(0))) for m in _METRIC.finditer(sent)]

    # Periods that belong to one number only: "$51.9M in FY2023".
    trailing = {}
    compared = set()
    for i, n in enumerate(numbers):
        nxt = numbers[i + 1].start if i + 1 < len(numbers) else len(sent)
        for k, (a, b, p) in enumerate(periods):
            if n.end <= a < nxt:
                pre = sent[n.end:a]
                if (_TRAIL.search(pre) and len(pre.split()) <= 6 and not re.search(r"[;:]", pre)) \
                        or (k in relative and not pre.strip()):   # "$51.9M a year earlier"
                    trailing[i] = k
                    if _COMPARE.search(sent[: n.start]):
                        compared.add(k)
                break
    owned = set(trailing.values())

    # "A and B were $x and $y, respectively", and "A, B and C were $x, $y and $z": a list of
    # metric names followed by a list of numbers of the same length is read in order.
    zipped = {}
    listed_metrics = set()
    if numbers:
        lead = [m for m in metrics if m[1] <= numbers[0].start]
        run = lead[-1:]
        for m in reversed(lead[:-1]):
            if not _LIST_GAP.fullmatch(sent[m[1]:run[0][0]]):
                break
            run.insert(0, m)
        same = [0]
        for i in range(1, len(numbers)):
            if numbers[i].kind != numbers[0].kind or not _LIST_GAP.fullmatch(sent[numbers[i - 1].end:numbers[i].start]):
                break
            same.append(i)
        if len(run) >= 2 and len(run) == len(same):
            zipped = {i: run[j][2] for j, i in enumerate(same)}
        elif re.search(r"\brespectively\b", sent, re.I):
            same = [i for i, n in enumerate(numbers) if n.kind == numbers[0].kind]
            if len(lead) >= 2 and len(lead) == len(same):
                zipped = {i: lead[j][2] for j, i in enumerate(same)}
        if len(run) >= 2 and not zipped:
            listed_metrics = {m[1] for m in run}

    for i, n in enumerate(numbers):
        if n.kind in ("multiple", "count"):
            yield n, None, None, "", ("a multiple, not a figure" if n.kind == "multiple"
                                      else "a count with no currency or percent sign")
            continue
        prev_end = numbers[i - 1].end if i else 0
        next_start = numbers[i + 1].start if i + 1 < len(numbers) else len(sent)
        after = sent[n.end:next_start]
        weak = ""

        # ---- metric
        before_m = [m for m in metrics if m[1] <= n.start]
        after_m = [m for m in metrics if m[0] >= n.end]
        metric = None
        if i in zipped:
            metric = zipped[i]
        elif n.kind == "usd" and after_m and _OF_METRIC.match(sent[n.end:after_m[0][0]] + " "):
            metric = after_m[0][2]            # "$58.6M of revenue"
        elif before_m and not _CLAUSE_BREAK.search(sent[before_m[-1][1]:n.start]):
            metric = before_m[-1][2]
            if before_m[-1][1] < prev_end and _LIST_GAP.fullmatch(sent[prev_end:n.start]):
                weak = "one metric name is followed by a list of numbers"
            elif before_m[-1][1] in listed_metrics:
                weak = "a list of metric names precedes this number"
            elif n.start - before_m[-1][1] > 70:
                weak = "the metric name is far from the number"
        elif after_m and after_m[0][0] < next_start:
            metric = after_m[0][2]            # "At $58.6M, FY2024 revenue was a record"
        if metric is None:
            yield n, None, None, "", "no metric name in the sentence"
            continue

        # ---- what a percentage means
        if n.kind == "pct":
            base = metric
            is_rate = metric.endswith(("_margin", "_growth"))
            gap = sent[max(prev_end, before_m[-1][1] if before_m else 0):n.start]
            cue_b = None
            for cue_b in _CUE_BEFORE.finditer(gap):
                pass
            cue_a = _CUE_AFTER.match(after)
            share = _SHARE_AFTER.match(after)
            if _CAGR.search(gap) or _CAGR.search(after[:40]):
                metric = (base[: -len("_growth")] if base.endswith("_growth") else base) + "_cagr"
            elif share and (is_rate or base + "_margin" not in nc.RATIOS):
                yield n, None, None, "", "a share of revenue, not a margin of a known metric"
                continue
            elif (share or _MARGIN_AFTER.match(after)) and not is_rate:
                named = [m for m in after_m if m[0] < n.end + 40 and m[2].endswith("_margin")]
                metric = named[0][2] if named else base + "_margin"
            elif is_rate:
                pass                          # "gross margin of 30.5%", "revenue growth of 13.0%"
            elif cue_b or cue_a:
                metric = base + "_growth"
                down = (cue_b and cue_b.group("down")) or (cue_a and cue_a.group("down"))
                if down and n.amount.value > 0:
                    n = Number(n.start, n.end, n.text, nc.Amount(-n.amount.value, n.amount.tol, "pct"), "pct")
            else:
                yield n, None, None, "", "a percentage with no margin or growth wording"
                continue

        # ---- period
        period = None
        if metric.endswith("_cagr"):
            years = sorted({p for _, _, p in periods if re.match(r"FY\d{4}$", p)})
            if len(years) >= 2:
                period = "%s-%s" % (years[0], years[-1])
            else:
                yield n, None, None, "", "a CAGR without a start and end year"
                continue
        elif i in trailing:
            period = periods[trailing[i]][2]
        else:
            # "$51.9M and $7.9M in FY2023": a number shares the period of the next one in a list
            j = i
            while j + 1 < len(numbers) and j not in trailing and \
                    _LIST_GAP.fullmatch(sent[numbers[j].end:numbers[j + 1].start]):
                j += 1
            free_before = [k for k, (a, b, p) in enumerate(periods) if b <= n.start and k not in compared]
            free_after = [k for k, (a, b, p) in enumerate(periods) if a >= n.end and k not in compared]
            if j != i and j in trailing:
                period = periods[trailing[j]][2]
            elif _COMPARE.search(sent[prev_end:n.start]):
                # "rose to $58.6M in FY2024 from $51.9M": the second figure is about some
                # other period, and the sentence does not say which
                yield n, metric, None, "", "a comparison figure with no period of its own"
                continue
            elif free_before:
                period = periods[free_before[-1]][2]
                if free_before[-1] in owned and len({p for _, _, p in periods}) > 1:
                    weak = weak or "the nearest period is attached to another number"
            elif free_after:
                period = periods[free_after[0]][2]
            elif context:
                period = context[0]
                weak = weak or context[1]
            else:
                yield n, metric, None, "", "no period in the sentence or the heading"
                continue
        yield n, metric, period, weak, ""


def _candidates(sent, context):
    """Every (metric, period) pair a sentence names, for weakly bound numbers."""
    ms = {nc.canon_metric(m.group(0)) for m in _METRIC.finditer(sent)}
    ps = {m[2] for m in _periods(sent)} | ({context[0]} if context else set())
    return ms, ps


def extract_claims(memo_text):
    """Every number in the memo as a Claim, bound to (metric, period) where the text allows."""
    lines = memo_text.splitlines()
    claims = []

    # Tables in the memo (a financial snapshot) are read with the same reader as the source.
    facts, used = [], set()
    nc._read_tables(lines, facts, used)
    cols = {}
    for f in facts:
        col = lines[f.line - 1].find(f.raw, cols.get(f.line, 0))
        cols[f.line] = col + len(f.raw)
        a = nc.Amount(f.value, f.tol, f.kind)
        if f.kind == "count" and "$" in f.raw:
            a = nc.Amount(f.value, f.tol, "usd")    # "$612" in a table with no scale is 612 dollars
        c = Claim(f.line, col, f.raw, a.kind, a, f.metric, f.period)
        if a.kind == "count":
            c.metric = c.period = None
            c.why = "table cell with no unit: the table declares no scale and the cell has no suffix"
        claims.append(c)

    heading = None
    para = None
    for i, line in enumerate(lines):
        if not line.strip():
            para = None
            continue
        if i in used:
            continue
        m = _LIST_MARK.match(line)
        offset = m.end() if m else 0
        body = line[offset:]
        is_heading = line.lstrip().startswith("#") or (
            body.strip().endswith(":") and len(body.split()) <= 8) or re.fullmatch(r"\*\*[^*]+\*\*:?", body.strip())
        if set(body.strip()) <= set("-=|: "):
            continue
        pos = 0
        for sent in _SENTENCE.split(body):
            start = body.find(sent, pos)
            pos = start + len(sent)
            ctx = para or heading
            for n, metric, period, weak, why in _bind_sentence(sent, ctx):
                c = Claim(i + 1, offset + start + n.start, n.text, n.kind, n.amount,
                          metric if period else None, period, weak, why)
                if weak:
                    c.alternatives = _candidates(sent, ctx)
                claims.append(c)
            explicit = [m[2] for m in _periods(sent) if not m[3]]
            if is_heading:
                heading = (explicit[0], "") if len(set(explicit)) == 1 else None
                para = None
            elif explicit:
                para = (explicit[0], "period carried over from an earlier sentence") \
                    if len(set(explicit)) == 1 else None
    claims.sort(key=lambda c: (c.line, c.col))
    return claims


# ---------------------------------------------------------------- checking

class SourceIndex:
    """Every number in the source by value, for numbers the memo does not tie to a metric."""

    def __init__(self, text, source):
        self.entries = []   # (Amount, line, label)
        for f in source.facts:
            self.entries.append((nc.Amount(f.value, f.tol, f.kind), f.line, "%s %s" % (f.metric, f.period)))
        for i, line in enumerate(text.splitlines()):
            skip = [(m.start(), m.end()) for m in nc._PERIOD_PROSE.finditer(line)
                    if not _NOT_A_PERIOD.search(line[: m.start()])]
            for n in scan_numbers(line, skip):
                if n.amount is not None:
                    self.entries.append((n.amount, i + 1, ""))

    def find(self, amount):
        hits, seen = [], set()
        for a, line, label in self.entries:
            if a.kind == amount.kind and abs(a.value - amount.value) <= max(a.tol, amount.tol):
                if (line, label) not in seen and not (label == "" and any(l == line and lab for l, lab in seen)):
                    seen.add((line, label))
                    hits.append((line, label))
        return hits


def check_memo(source_text, memo_text):
    """Return the memo's claims with a status on each one."""
    source = nc.Source(source_text)
    index = SourceIndex(source_text, source)
    claims = extract_claims(memo_text)
    for c in claims:
        if c.bound:
            c.verdict = nc.check(source, c.metric, c.period, c.text, c.amount)
            c.status, c.note = c.verdict.status, c.verdict.note
            if c.weak and not c.verdict.ok and c.alternatives:
                # The binding was a guess. If the value is right for another metric and period
                # that the same sentence names, do not call it an error: ask for a review.
                for m in sorted(c.alternatives[0]):
                    for p in sorted(c.alternatives[1]):
                        alt = nc.check(source, m, p, c.text, c.amount)
                        if alt.ok and c.status != AMBIGUOUS:
                            c.status = AMBIGUOUS
                            c.note = "does not match %s %s, but matches %s %s, which the sentence also names: %s" % (
                                c.metric, c.period, alt.metric, alt.period, alt.note)
            continue
        hits = index.find(c.amount) if c.amount is not None else []
        c.status = UNBOUND_SEEN if hits else UNBOUND_UNSEEN
        where = "; ".join("line %d%s" % (l, " (%s)" % lab if lab else "") for l, lab in hits[:3])
        c.note = "%s; %s" % (c.why, ("same value at source " + where) if hits
                             else "this value appears nowhere in the source")
    return claims


def summarize(claims):
    ok = sum(1 for c in claims if c.passed)
    flagged = sum(1 for c in claims if c.flagged)
    return {"numbers": len(claims), "supported": ok, "flagged": flagged,
            "ambiguous_binding": sum(c.status == AMBIGUOUS for c in claims),
            "unbound_in_source": sum(c.status == UNBOUND_SEEN for c in claims),
            "unbound_not_in_source": sum(c.status == UNBOUND_UNSEEN for c in claims),
            "weak_bindings": sum(1 for c in claims if c.weak)}


def to_json(claims):
    return [{"line": c.line, "col": c.col, "text": c.text, "metric": c.metric, "period": c.period,
             "status": c.status, "weak_binding": c.weak or None, "note": c.note,
             "source_lines": sorted({f.line for f in c.verdict.evidence}) if c.verdict else []}
            for c in claims]


def render(claims):
    out = []
    for c in claims:
        slot = "%s %s" % (c.metric, c.period) if c.bound else "-"
        out.append("memo line %-3d %-16s %-32s %-21s %s%s" % (
            c.line, c.text.strip(), slot, c.status, c.note,
            " [weak binding: %s]" % c.weak if c.weak else ""))
    s = summarize(claims)
    out.append("")
    out.append("%d numbers in the memo: %d supported, %d flagged, %d ambiguous, %d not tied to a "
               "metric and period (%d of those appear nowhere in the source)" % (
                   s["numbers"], s["supported"], s["flagged"], s["ambiguous_binding"],
                   s["unbound_in_source"] + s["unbound_not_in_source"], s["unbound_not_in_source"]))
    return "\n".join(out)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    if len(args) != 2 or flags - {"--json", "--strict"}:
        print("usage: python memo.py source.txt memo.md [--json] [--strict]")
        return 2
    try:
        source_text, memo_text = nc.read_text(args[0]), nc.read_text(args[1])
    except (OSError, UnicodeDecodeError) as e:
        print("could not read input: %s" % e)
        return 2
    claims = check_memo(source_text, memo_text)
    if "--json" in flags:
        print(json.dumps({"summary": summarize(claims), "claims": to_json(claims)}, indent=1))
    else:
        print(render(claims))
    s = summarize(claims)
    if s["flagged"] or ("--strict" in flags and s["unbound_not_in_source"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
