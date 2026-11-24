"""numcheck: check that every financial figure an AI wrote is backed by the source document.

A claim is (metric, period, value as written). The checker reads the source text,
binds every number it can to a metric, a period and a unit scale, and then decides
whether the claim is supported, and if not, what kind of mistake it looks like.

This module checks structured claims. memo.py pulls the claims out of memo prose.

Standard library only.
"""
import json
import re
import sys
from dataclasses import dataclass, field
from fractions import Fraction

# ---------------------------------------------------------------- amounts

SCALES = {
    "k": 10**3, "thousand": 10**3, "thousands": 10**3, "000s": 10**3,
    "m": 10**6, "mm": 10**6, "mn": 10**6, "million": 10**6, "millions": 10**6,
    "b": 10**9, "bn": 10**9, "billion": 10**9, "billions": 10**9,
}
_AMOUNT = re.compile(
    r"^\s*(?P<neg>[-\u2212(]?)\s*(?P<cur>\$|USD\s*)?\s*(?P<neg2>-?)"
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*"
    r"(?P<suf>%|[A-Za-z]+)?\s*\)?\s*$"
)


@dataclass
class Amount:
    value: Fraction   # dollars, or percentage points, or a plain count
    tol: Fraction     # half a unit of the last written digit
    kind: str         # "usd", "pct" or "count"


def parse_amount(text, default_scale=1, default_kind="usd"):
    """'$47.3M' -> Amount(47300000, 50000, 'usd'). Returns None if it is not a number."""
    m = _AMOUNT.match(text)
    if not m:
        return None
    num = m.group("num").replace(",", "")
    decimals = len(num.split(".")[1]) if "." in num else 0
    value = Fraction(num)
    suf = (m.group("suf") or "").lower()
    if suf == "%":
        scale, kind = 1, "pct"
    elif suf in SCALES:
        scale, kind = SCALES[suf], "usd"
    elif suf == "":
        scale, kind = default_scale, default_kind
    else:
        return None
    if m.group("neg") or m.group("neg2"):
        value = -value
    tol = Fraction(1, 2) * Fraction(1, 10**decimals) * scale
    return Amount(value * scale, tol, kind)


# ---------------------------------------------------------------- metric names

ALIASES = {
    "revenue": ["revenue", "revenues", "net revenue", "total revenue", "sales", "net sales"],
    "gross_profit": ["gross profit"],
    "adjusted_ebitda": ["adjusted ebitda", "adj ebitda"],
    "ebitda": ["ebitda", "reported ebitda"],
    "net_income": ["net income", "net income (loss)", "net income loss"],
    "capex": ["capex", "capital expenditures", "capital expenditure"],
    "gross_margin": ["gross margin", "gross profit margin", "gross margin %"],
    "adjusted_ebitda_margin": ["adjusted ebitda margin", "adj ebitda margin", "adjusted ebitda margin %"],
    "ebitda_margin": ["ebitda margin", "ebitda margin %"],
    "net_margin": ["net margin", "net income margin"],
    "revenue_growth": ["revenue growth", "% growth", "growth %", "yoy growth", "revenue growth %"],
}
_ALIAS_TO_METRIC = {a: k for k, v in ALIASES.items() for a in v}
# metric -> (numerator, denominator) for ratios the document may not print
RATIOS = {
    "gross_margin": ("gross_profit", "revenue"),
    "adjusted_ebitda_margin": ("adjusted_ebitda", "revenue"),
    "ebitda_margin": ("ebitda", "revenue"),
    "net_margin": ("net_income", "revenue"),
}


_FOOTNOTE = re.compile(r"(?:\s*(?:\(\d{1,2}\)|\[\d{1,2}\]|\*+|[\u00b9\u00b2\u00b3\u2020]))+\s*$")


def canon_metric(label):
    s = _FOOTNOTE.sub("", label)   # "Adjusted EBITDA (1)" and "Net Sales*" are footnote marks
    s = s.lower().replace("adj.", "adj").replace("&", "and")
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%() ]", " ", s)).strip()
    if s in _ALIAS_TO_METRIC:
        return _ALIAS_TO_METRIC[s]
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


# One period as it appears in a header cell or in a sentence. Kept as one pattern so the
# table reader and memo.py agree on what a period is.
PERIOD_PAT = (
    r"(?:(?:FY|fiscal(?:\s+year)?)\s?'?(?:20\d{2}|\d{2})[AEPB]?"
    r"|20\d{2}[AEPB]?"
    r"|(?:LTM|TTM)(?:\s+(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?[-'\s]?\d{2,4}"
    r"|Q[1-4]\s?'?\d{2,4}))?"
    r"|Q[1-4]\s?'?(?:20\d{2}|\d{2})|[1-4]Q\s?'?(?:20\d{2}|\d{2}))"
)
_FY = re.compile(r"^(?:(?:FY|fiscal(?:\s+year)?)\s?'?(?P<a>20\d{2}|\d{2})|(?P<b>20\d{2}))\s?[AEPB]?$", re.I)
_LTM = re.compile(r"^(?:LTM|TTM)(?:\s+.+)?$", re.I)
_QTR = re.compile(r"^(?:Q(?P<q1>[1-4])\s?'?(?P<y1>20\d{2}|\d{2})|(?P<q2>[1-4])Q\s?'?(?P<y2>20\d{2}|\d{2}))$", re.I)
_PERIOD_PROSE = re.compile(r"(?<![\w$.,])" + PERIOD_PAT + r"(?![\w%])(?![.,]\d)", re.I)
_HEADER_TAIL = re.compile(r"(?:\s+" + PERIOD_PAT + r"){2,}\s*$", re.I)


def _year(y):
    return y if len(y) == 4 else "20" + y


def canon_period(text):
    """'FY21', '2021A', 'fiscal 2021' -> 'FY2021'; 'LTM Jun-25' -> 'LTM'; '3Q25' -> '2025Q3'.

    None if it is not a period. A dated LTM column keeps no date: a document with two
    different LTM columns would be merged, which Source.conflicts would then report.
    """
    t = text.strip()
    m = _FY.match(t)
    if m:
        return "FY" + _year(m.group("a") or m.group("b"))
    m = _QTR.match(t)
    if m:
        return "%sQ%s" % (_year(m.group("y1") or m.group("y2")), m.group("q1") or m.group("q2"))
    if _LTM.match(t):
        return "LTM"
    return None


def prior_period(period):
    """The period one year earlier, or None when that is not defined (LTM)."""
    m = re.match(r"^FY(\d{4})$", period or "")
    if m:
        return "FY%d" % (int(m.group(1)) - 1)
    m = re.match(r"^(\d{4})Q([1-4])$", period or "")
    if m:
        return "%dQ%s" % (int(m.group(1)) - 1, m.group(2))
    return None


# ---------------------------------------------------------------- reading the source

@dataclass
class Fact:
    metric: str
    period: str
    value: Fraction
    tol: Fraction
    kind: str
    line: int      # 1-based line in the source
    raw: str       # the text as printed
    where: str     # "table" or "prose"


_SCALE_DECL = re.compile(
    r"(?:\$|USD|US\$|dollars)\s*(?:in\s*)?(thousands|millions|billions|000s|mm|k|m)\b"
    r"|in\s+(?:\$|USD)\s*(thousands|millions|billions|000s|mm|k|m)\b", re.I)
_EMPTY = {"", "-", "\u2013", "\u2014", "n/a", "na", "nm", "n.m."}
_VALUE_TOKEN = re.compile(
    r"^(?:\(?-?\$?\d[\d,]*(?:\.\d+)?\)?[%x]?|-|\u2013|\u2014|n/a|na|nm|n\.m\."
    r"|(?:FY'?)?(?:20)?\d{2}[AEPB]?)$", re.I)


def _cells(line):
    if "|" in line:
        parts = [c.strip() for c in line.split("|")]
        if parts and parts[0] == "" and line.lstrip().startswith("|"):
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        return parts
    # No pipes: peel value-like tokens off the right end, the rest is the label.
    # Splitting on runs of two or more spaces breaks on text copied out of a PDF,
    # where columns often arrive separated by a single space.
    toks = line.split()
    k = len(toks)
    while k > 0 and _VALUE_TOKEN.match(toks[k - 1]):
        k -= 1
    return [" ".join(toks[:k])] + toks[k:]


def _header_periods(line):
    """Canonical periods of a table header line, or [] if the line is not one."""
    if "|" in line:
        return [p for p in (canon_period(c) for c in _cells(line)) if p]
    m = _HEADER_TAIL.search(" " + line)
    if not m:
        return []
    return [canon_period(x.group(0)) for x in _PERIOD_PROSE.finditer(m.group(0))]


def _scale_near(lines, i):
    """Scale declared on the header line or up to two lines above it."""
    for j in (i, i - 1, i - 2):
        if j < 0:
            break
        m = _SCALE_DECL.search(lines[j])
        if m:
            return SCALES[(m.group(1) or m.group(2)).lower()]
        if j != i and re.search(r"\d", lines[j]) and len(_cells(lines[j])) > 2:
            break  # ran into the previous table
    return None


def _read_tables(lines, facts, used):
    i = 0
    while i < len(lines):
        periods = list(enumerate(_header_periods(lines[i])))
        if len(periods) < 2 or None in [p for _, p in periods]:
            i += 1
            continue
        scale = _scale_near(lines, i)
        ncol = len(periods)
        used.add(i)
        i += 1
        while i < len(lines) and lines[i].strip():
            row = _cells(lines[i])
            if set(lines[i].strip()) <= set("-=+| "):
                i += 1
                continue
            if len(row) > ncol + 1 and "|" not in lines[i]:
                # a label that ends in a number, such as "Top 10 Customers"
                row = [" ".join(row[: len(row) - ncol])] + row[-ncol:]
            if len(row) != ncol + 1:
                break
            metric = canon_metric(row[0])
            got = False
            for (_, period), cell in zip(periods, row[1:]):
                if cell.lower() in _EMPTY:
                    continue
                if cell.endswith("x"):
                    continue
                a = parse_amount(cell, scale or 1, "usd" if scale else "count")
                if a is None:
                    continue
                facts.append(Fact(metric, period, a.value, a.tol, a.kind, i + 1, cell, "table"))
                got = True
            if got:
                used.add(i)
            i += 1


_names = sorted(_ALIAS_TO_METRIC, key=len, reverse=True)
_PROSE = re.compile(
    r"(?<![A-Za-z])(?P<name>" + "|".join(re.escape(n).replace(r"adj\ ", r"adj\.?\ ") for n in _names if "%" not in n) + r")"
    r"\s+(?:of|was|were|is|reached|totaled|totalled|grew\s+to|increased\s+to|declined\s+to|fell\s+to)"
    r"\s+(?:approximately\s+|roughly\s+|about\s+)?"
    r"(?P<amt>\(?-?\$\s?[\d,]+(?:\.\d+)?\s*(?:thousand|million|billion|mm|bn|[KMB])?\b\)?|\d+(?:\.\d+)?%)",
    re.I)


def _read_prose(lines, facts, used):
    for i, line in enumerate(lines):
        if i in used:
            continue
        for sent in re.split(r"(?<=[.!?;])\s+(?=[A-Z])", line):
            marks = [(m.start(), canon_period(m.group(0))) for m in _PERIOD_PROSE.finditer(sent)]
            marks = [(pos, p) for pos, p in marks if p]
            if not marks:
                continue
            for m in _PROSE.finditer(sent):
                a = parse_amount(m.group("amt").replace("$ ", "$"))
                if a is None:
                    continue
                before = [p for pos, p in marks if pos < m.start()]
                period = before[-1] if before else marks[0][1]
                metric = canon_metric(m.group("name"))
                if a.kind == "pct" and metric in ("revenue",):
                    continue  # "revenue grew to 12%" style sentences are not amounts
                facts.append(Fact(metric, period, a.value, a.tol, a.kind, i + 1, m.group("amt"), "prose"))


def read_source(text):
    """Return every (metric, period, value) the document states, with line numbers."""
    lines = text.splitlines()
    facts, used = [], set()
    _read_tables(lines, facts, used)
    _read_prose(lines, facts, used)
    return facts


# ---------------------------------------------------------------- checking

SUPPORTED, DERIVED = "supported", "supported_derived"
UNIT, PERIOD, METRIC, VALUE, NOT_FOUND, BAD, SIGN = (
    "unit_mismatch", "wrong_period", "wrong_metric", "value_mismatch", "not_found", "unparseable",
    "wrong_sign")


@dataclass
class Verdict:
    status: str
    metric: str
    period: str
    text: str
    evidence: list = field(default_factory=list)   # Facts the decision rests on
    note: str = ""

    @property
    def ok(self):
        return self.status in (SUPPORTED, DERIVED)


class Source:
    def __init__(self, text):
        self.facts = read_source(text)
        self.by = {}
        for f in self.facts:
            self.by.setdefault((f.metric, f.period), []).append(f)
        for v in self.by.values():
            v.sort(key=lambda f: f.tol)   # most precise statement first
        self.periods = sorted({f.period for f in self.facts})
        self.metrics = sorted({f.metric for f in self.facts})
        # Slots the document states twice with values that cannot both be right.
        self.conflicts = {}
        for key, fs in self.by.items():
            for a in fs:
                for b in fs:
                    if a.kind == b.kind and a.line < b.line and abs(a.value - b.value) > a.tol + b.tol:
                        self.conflicts.setdefault(key, []).append((a, b))

    def direct(self, metric, period, kind):
        for f in self.by.get((metric, period), []):
            if f.kind == kind:
                return f
        return None

    def derived(self, metric, period):
        """Value of a ratio or growth rate computed from printed figures: (value, [facts])."""
        if metric in RATIOS:
            n = self.direct(RATIOS[metric][0], period, "usd")
            d = self.direct(RATIOS[metric][1], period, "usd")
            if n and d and d.value:
                return n.value / d.value * 100, [n, d]
        if metric.endswith("_growth"):
            base = metric[: -len("_growth")]
            prev = prior_period(period)
            a, b = self.direct(base, period, "usd"), self.direct(base, prev, "usd")
            if a and b and b.value > 0:
                return (a.value / b.value - 1) * 100, [a, b]
        if metric.endswith("_cagr"):
            m = re.match(r"^FY(\d{4})-FY(\d{4})$", period)
            if m and int(m.group(2)) > int(m.group(1)):
                base = metric[: -len("_cagr")]
                a = self.direct(base, "FY" + m.group(1), "usd")
                b = self.direct(base, "FY" + m.group(2), "usd")
                if a and b and a.value > 0 and b.value > 0:
                    years = int(m.group(2)) - int(m.group(1))
                    rate = (float(b.value / a.value) ** (1.0 / years) - 1) * 100
                    return Fraction(rate).limit_denominator(10**9), [a, b]
        return None

    def lookups(self, metric, period, kind):
        """All (value, tol, evidence, is_derived) the source gives for this slot."""
        out = []
        for f in self.by.get((metric, period), []):
            # The most precise statement decides. A coarser one ("$58.6 million" next to a
            # table cell of 58,644) is only an alternative when the two cannot both be right.
            if f.kind == kind and (not out or abs(f.value - out[0][0]) > f.tol + out[0][1]):
                out.append((f.value, f.tol, [f], False))
        if kind == "pct":
            d = self.derived(metric, period)
            if d:
                out.append((d[0], Fraction(0), d[1], True))
        return out


def _close(a, b, tol):
    return abs(a - b) <= tol


def _cite(ev):
    return ", ".join("line %d (%s %s = %s)" % (f.line, f.metric, f.period, f.raw) for f in ev)


def check(source, metric, period, text, amount=None):
    """Check one claim against a Source. Returns a Verdict.

    amount lets a caller that has already parsed the value (memo.py) pass it in.
    """
    v = _check(source, metric, period, text, amount)
    clash = source.conflicts.get((v.metric, v.period))
    if clash and v.status != BAD:
        a, b = clash[0]
        v.note += "; note: the source disagrees with itself here (line %d says %s, line %d says %s)" % (
            a.line, a.raw, b.line, b.raw)
    return v


def _check(source, metric, period, text, amount=None):
    metric_c = canon_metric(metric)
    period_c = canon_period(period) or period
    amt = amount if amount is not None else parse_amount(text)
    v = Verdict(BAD, metric_c, period_c, text)
    if amt is None:
        v.note = "could not read the claimed value"
        return v
    kind = amt.kind
    here = source.lookups(metric_c, period_c, kind)

    for val, tol, ev, is_derived in here:
        if _close(amt.value, val, max(amt.tol, tol)):
            v.status, v.evidence = (DERIVED if is_derived else SUPPORTED), ev
            v.note = _cite(ev)
            return v

    for val, tol, ev, _ in here:
        if amt.value and _close(-amt.value, val, max(amt.tol, tol)):
            v.status, v.evidence = SIGN, ev
            v.note = "right size, opposite sign; source: %s" % _cite(ev)
            return v

    if kind == "usd":
        for val, tol, ev, _ in here:
            for f in (10**3, 10**6, 10**9):
                if val and (_close(amt.value * f, val, max(amt.tol * f, tol))
                            or _close(amt.value, val * f, max(amt.tol, tol * f))):
                    v.status, v.evidence = UNIT, ev
                    v.note = "right digits, wrong scale by a factor of %d; source: %s" % (f, _cite(ev))
                    return v

    for p in source.periods:
        if p == period_c:
            continue
        for val, tol, ev, _ in source.lookups(metric_c, p, kind):
            if _close(amt.value, val, max(amt.tol, tol)):
                v.status, v.evidence = PERIOD, ev
                v.note = "this is the %s figure; source: %s" % (p, _cite(ev))
                return v

    others = list(source.metrics) + (
        list(RATIOS) + [m + "_growth" for m in source.metrics if not m.endswith(("_margin", "_growth"))]
        if kind == "pct" else [])
    for m in dict.fromkeys(others):
        if m == metric_c:
            continue
        for val, tol, ev, _ in source.lookups(m, period_c, kind):
            if _close(amt.value, val, max(amt.tol, tol)):
                v.status, v.evidence = METRIC, ev
                v.note = "this is %s, not %s; source: %s" % (m, metric_c, _cite(ev))
                return v

    if here:
        v.status, v.evidence = VALUE, here[0][2]
        v.note = "source says otherwise: %s" % _cite(here[0][2])
    else:
        v.status = NOT_FOUND
        v.note = "the source states no %s for %s" % (metric_c, period_c)
    return v


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def check_all(text, claims):
    src = Source(text)
    return [check(src, c["metric"], c["period"], c["value"]) for c in claims]


def main(argv):
    if len(argv) != 3:
        print("usage: python numcheck.py source.txt claims.json")
        return 2
    try:
        text = read_text(argv[1])
        claims = json.loads(read_text(argv[2]))
    except (OSError, ValueError) as e:
        print("could not read input: %s" % e)
        return 2
    bad = 0
    for v in check_all(text, claims):
        bad += not v.ok
        print("%-17s %-24s %-7s %-14s %s" % (v.status, v.metric, v.period, v.text, v.note))
    print("%d of %d claims flagged" % (bad, len(claims)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
