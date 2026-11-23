"""Synthetic deal documents with known figures, and claims with known mistakes.

The generator keeps its own table of true values. The checker never sees it: it only
gets the rendered text and the claims.
"""
import random
from fractions import Fraction

from numcheck import parse_amount

LABELS = {
    "revenue": ["Revenue", "Net Revenue", "Total Revenue", "Net Sales", "Sales"],
    "gross_profit": ["Gross Profit"],
    "ebitda": ["EBITDA", "Reported EBITDA"],
    "adjusted_ebitda": ["Adjusted EBITDA", "Adj. EBITDA"],
    "net_income": ["Net Income", "Net Income (Loss)"],
    "capex": ["Capex", "Capital Expenditures"],
}
SIBLING = {
    "revenue": "gross_profit", "gross_profit": "revenue", "ebitda": "adjusted_ebitda",
    "adjusted_ebitda": "ebitda", "net_income": "ebitda", "capex": "net_income",
}
MARGINS = {"gross_margin": "gross_profit", "adjusted_ebitda_margin": "adjusted_ebitda",
           "ebitda_margin": "ebitda"}
MARGIN_SIB = {"gross_margin": "adjusted_ebitda_margin", "adjusted_ebitda_margin": "ebitda_margin",
              "ebitda_margin": "adjusted_ebitda_margin"}
MARGIN_LABEL = {"gross_margin": "Gross Margin", "adjusted_ebitda_margin": "Adjusted EBITDA Margin",
                "ebitda_margin": "EBITDA Margin"}


def _cell(units, millions, pct=False):
    """Render a table cell. units is thousands (int) or tenths of millions (int)."""
    if pct:
        return "%.1f%%" % units
    s = ("%.1f" % (abs(units) / 10)) if millions else "{:,}".format(abs(units))
    return "(%s)" % s if units < 0 else s


def make_doc(rng):
    """Return (text, truth, info). truth maps (metric, period) -> dollars as printed."""
    millions = rng.random() < 0.3
    n = rng.randint(3, 5)
    last = rng.randint(2024, 2026)
    years = list(range(last - n + 1, last + 1))
    rev = rng.uniform(9000, 400000)
    rows = {m: [] for m in LABELS}
    gm, am = rng.uniform(0.25, 0.7), rng.uniform(0.08, 0.3)
    for _ in years:
        adj = rev * am * rng.uniform(0.9, 1.1)
        ebitda = adj * rng.uniform(0.80, 0.97)
        vals = {
            "revenue": rev, "gross_profit": rev * gm * rng.uniform(0.95, 1.05),
            "adjusted_ebitda": adj, "ebitda": ebitda,
            "net_income": ebitda * rng.uniform(0.3, 0.6) - rev * rng.choice([0, 0, 0.06]),
            "capex": rev * rng.uniform(0.01, 0.06),
        }
        for m, x in vals.items():
            rows[m].append(int(round(x / 100)) if millions else int(round(x)))
        rev *= 1 + rng.uniform(-0.05, 0.30)
    unit = 100000 if millions else 1000   # dollars per integer step in rows
    periods = ["FY%d" % y for y in years]
    prose_only = "capex" if rng.random() < 0.35 else None

    truth = {}
    for m in rows:
        for p, u in zip(periods, rows[m]):
            truth[(m, p)] = Fraction(u * unit)
    if prose_only:  # only the last year is stated, in a sentence
        for p in periods[:-1]:
            del truth[(prose_only, p)]
        v = round(float(truth[(prose_only, periods[-1])]) / 1e6, 1)
        truth[(prose_only, periods[-1])] = Fraction(str(v)) * 10**6

    style = rng.choice(["FY%d", "%dA", "FY%02d"])
    def head(y, i):
        if style == "FY%02d":
            return "FY%02d" % (y % 100)
        s = style % y
        if i == len(years) - 1 and rng.random() < 0.5:
            s = s.rstrip("A") + "E"
        return s
    heads = [head(y, i) for i, y in enumerate(years)]
    scale_txt = rng.choice(["($ in millions)", "(USD in millions)"]) if millions else \
        rng.choice(["($ in thousands)", "(USD in 000s)", "($ in 000s)"])
    pipes = rng.random() < 0.4
    scale_in_head = rng.random() < 0.5
    show_margin = rng.choice([None, None, "adjusted_ebitda_margin", "gross_margin"])

    table_rows = [[scale_txt if scale_in_head else ""] + heads]
    for m in ["revenue", "gross_profit", "ebitda", "adjusted_ebitda", "net_income", "capex"]:
        if m == prose_only:
            continue
        table_rows.append([rng.choice(LABELS[m])] + [_cell(u, millions) for u in rows[m]])
        if show_margin and MARGINS[show_margin] == m:
            pc = [100.0 * a / b for a, b in zip(rows[m], rows["revenue"])]
            table_rows.append([MARGIN_LABEL[show_margin]] + [_cell(x, False, True) for x in pc])
    ops = [["", *heads],
           ["Customers"] + ["{:,}".format(rng.randint(80, 9000)) for _ in years],
           ["Employees"] + ["{:,}".format(rng.randint(40, 2500)) for _ in years]]

    def render(trs):
        if pipes:
            return ["| " + " | ".join(r) + " |" for r in trs]
        return [r[0].ljust(28) + "".join(c.rjust(12) for c in r[1:]) for r in trs]

    fin = ["Summary Financials" + ("" if scale_in_head else " " + scale_txt)] + render(table_rows)
    opsb = ["Key Operating Metrics"] + render(ops)
    lp = periods[-1]
    r_m = float(truth[("revenue", lp)]) / 1e6
    a_m = float(truth[("adjusted_ebitda", lp)]) / 1e6
    prose = ["The Company was founded in %d and serves over %s customers. Management estimates "
             "an addressable market of $%d million." % (rng.randint(1975, 2012),
                                                         "{:,}".format(rng.randint(100, 5000)),
                                                         rng.randint(200, 9000))]
    if rng.random() < 0.6:
        prose.append("In %s, revenue was $%.1f million and Adjusted EBITDA reached $%.1f million."
                     % (lp, r_m, a_m))
    if prose_only:
        prose.append("Capital expenditures were $%.1f million in %s."
                     % (float(truth[(prose_only, lp)]) / 1e6, lp))
    blocks = [fin, opsb, prose]
    rng.shuffle(blocks)
    text = "\n\n".join("\n".join(b) for b in blocks) + "\n"
    return text, truth, {"periods": periods, "millions": millions}


def fmt_usd(v, style):
    v = float(v)
    sign = "-" if v < 0 else ""
    a = abs(v)
    if style == "M1":
        return "%s$%.1fM" % (sign, a / 1e6)
    if style == "million1":
        return "%s$%.1f million" % (sign, a / 1e6)
    if style == "mm2":
        return "%s$%.2fmm" % (sign, a / 1e6)
    if style == "K":
        return "%s$%sK" % (sign, "{:,.0f}".format(a / 1e3))
    return "%s$%s" % (sign, "{:,.0f}".format(a))


STYLES = ["M1", "million1", "mm2", "K", "full"]
PRECISE = ["mm2", "K", "full"]


def _is_true(text, true_value):
    a = parse_amount(text)
    return true_value is not None and abs(a.value - true_value) <= a.tol


def _margin(truth, metric, p):
    n, d = truth.get((MARGINS[metric], p)), truth.get(("revenue", p))
    return None if n is None or not d else n / d * 100


def _mutate_digits(rng, n):
    s = list(str(abs(n)))
    for _ in range(20):
        t = s[:]
        if len(t) > 1 and rng.random() < 0.5:
            i = rng.randrange(len(t) - 1)
            t[i], t[i + 1] = t[i + 1], t[i]
        else:
            i = rng.randrange(len(t))
            t[i] = rng.choice([c for c in "0123456789" if c != t[i]])
        if t != s and t[0] != "0":
            return int("".join(t)) * (-1 if n < 0 else 1)
    return n + 7


def make_claims(rng, truth, info, per_doc=16):
    """Half the claims are right. The rest carry one labelled mistake each."""
    periods = info["periods"]
    slots = sorted(truth)
    out = []

    def add(metric, period, value, error):
        out.append({"metric": metric, "period": period, "value": value, "error": error})

    for k in range(per_doc):
        want_pct = rng.random() < 0.25
        correct = k % 2 == 0
        if want_pct:
            metric = rng.choice(sorted(MARGINS))
            p = rng.choice(periods)
            true = _margin(truth, metric, p)
            if true is None:
                continue
            if correct:
                add(metric, p, "%.1f%%" % float(true), None)
                continue
            err = rng.choice(["period", "metric", "digit"])
            if err == "period":
                alt = _margin(truth, metric, rng.choice([q for q in periods if q != p]))
            elif err == "metric":
                alt = _margin(truth, MARGIN_SIB[metric], p)
            else:
                alt = true + Fraction(rng.choice([-1, 1]) * rng.randint(3, 60), 10)
            if alt is None:
                continue
            text = "%.1f%%" % float(alt)
            if not _is_true(text, true):
                add(metric, p, text, err)
            continue

        metric, p = rng.choice(slots)
        true = truth[(metric, p)]
        if correct:
            add(metric, p, fmt_usd(true, rng.choice(STYLES)), None)
            continue
        err = rng.choice(["scale", "period", "metric", "digit", "fabricated"])
        text = None
        if err == "scale":
            text = rng.choice([fmt_usd(true / 1000, "full"), fmt_usd(true * 1000, "M1").replace("M", "B")
                               if abs(true) < 10**6 else fmt_usd(true / 1000, "full"),
                               fmt_usd(true / 1000, "M1").replace("M", "K")])
            # "$47.3K" for a true $47.3M, "$47,310" for a true $47,310K
            if text.endswith("K"):
                text = "%s$%.1fK" % ("-" if true < 0 else "", abs(float(true)) / 1e6)
        elif err == "period":
            qs = [q for q in periods if q != p and (metric, q) in truth]
            if qs:
                text = fmt_usd(truth[(metric, rng.choice(qs))], rng.choice(STYLES))
        elif err == "metric":
            alt = truth.get((SIBLING[metric], p))
            if alt is not None:
                text = fmt_usd(alt, rng.choice(STYLES))
        elif err == "digit":
            step = 100000 if info["millions"] else 1000
            text = fmt_usd(_mutate_digits(rng, int(true / step)) * step,
                           "M1" if info["millions"] else "K")
        else:
            how = rng.choice(["period", "metric", "value"])
            if how == "period":
                p = "FY%d" % (int(periods[0][2:]) - rng.randint(1, 2))
                text = fmt_usd(true * Fraction(rng.randint(60, 95), 100), "M1")
            elif how == "metric":
                metric = "free_cash_flow"
                text = fmt_usd(true * Fraction(rng.randint(30, 80), 100), "M1")
            else:
                text = fmt_usd(true * Fraction(rng.choice([55, 70, 85, 120, 140, 165]), 100), "M1")
        if text is None or _is_true(text, truth.get((metric, p))):
            continue   # the "mistake" happens to be true, so it is not a mistake
        add(metric, p, text, err)
    return out


def corpus(seed, docs):
    rng = random.Random(seed)
    for _ in range(docs):
        text, truth, info = make_doc(rng)
        yield text, truth, make_claims(rng, truth, info)
