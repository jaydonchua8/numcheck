"""numcheck: check that every financial figure an AI wrote is backed by the source document.

A claim is (metric, period, value as written). The checker reads the source text,
binds every number it can to a metric, a period and a unit scale, and then decides
whether the claim is supported, and if not, what kind of mistake it looks like.

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


def canon_metric(label):
    s = label.lower().replace("adj.", "adj").replace("&", "and")
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%() ]", " ", s)).strip()
    if s in _ALIAS_TO_METRIC:
        return _ALIAS_TO_METRIC[s]
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


_PERIOD_CELL = re.compile(r"^(?:FY\s?'?(?P<a>20\d{2}|\d{2})|(?P<b>20\d{2}))\s?[AEPB]?$", re.I)
_PERIOD_PROSE = re.compile(r"\b(?:FY\s?'?(20\d{2}|\d{2})|(20\d{2}))[AE]?\b")


def canon_period(text):
    """'FY21', '2021A', 'FY2021E' -> 'FY2021'. None if it is not a period."""
    m = _PERIOD_CELL.match(text.strip())
    if not m:
        return None
    y = m.group("a") or m.group("b")
    return "FY" + (y if len(y) == 4 else "20" + y)


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
        cells = _cells(lines[i])
        periods = [(k, canon_period(c)) for k, c in enumerate(cells)]
        periods = [(k, p) for k, p in periods if p]
        if len(periods) < 2:
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
UNIT, PERIOD, METRIC, VALUE, NOT_FOUND, BAD = (
    "unit_mismatch", "wrong_period", "wrong_metric", "value_mismatch", "not_found", "unparseable")


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
            prev = "FY%d" % (int(period[2:]) - 1) if re.match(r"FY\d{4}$", period) else None
            a, b = self.direct(base, period, "usd"), self.direct(base, prev, "usd")
            if a and b and b.value:
                return (a.value / b.value - 1) * 100, [a, b]
        return None

    def lookups(self, metric, period, kind):
        """All (value, tol, evidence, is_derived) the source gives for this slot."""
        out = []
        f = self.direct(metric, period, kind)
        if f:
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


def check(source, metric, period, text):
    """Check one claim against a Source. Returns a Verdict."""
    metric_c = canon_metric(metric)
    period_c = canon_period(period) or period
    amt = parse_amount(text)
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

    others = list(source.metrics) + (list(RATIOS) + ["revenue_growth"] if kind == "pct" else [])
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


def check_all(text, claims):
    src = Source(text)
    return [check(src, c["metric"], c["period"], c["value"]) for c in claims]


def main(argv):
    if len(argv) != 3:
        print("usage: python numcheck.py source.txt claims.json")
        return 2
    text = open(argv[1], encoding="utf-8").read()
    claims = json.load(open(argv[2], encoding="utf-8"))
    bad = 0
    for v in check_all(text, claims):
        bad += not v.ok
        print("%-17s %-24s %-7s %-14s %s" % (v.status, v.metric, v.period, v.text, v.note))
    print("%d of %d claims flagged" % (bad, len(claims)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
