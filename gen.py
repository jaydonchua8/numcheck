"""Synthetic deal documents with known figures, and claims and memos with known mistakes.

Everything here is made up. The generator keeps its own table of true values. The checker
never sees it: it only gets the rendered text and the claims or the memo.

Three splits exist for memos. The dev split is what the binding rules in memo.py were
written against. The held-out splits use different seeds, sentence templates, labels and
number styles, and source tables with an LTM column and footnote marks. Held-out A was run
once, then used to fix the rules. Held-out B was written after that and never tuned on.
"""
import random
import re
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


def make_doc(rng, heldout=False):
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
    ltm = heldout and rng.random() < 0.6
    if ltm:
        for m in rows:
            rows[m].append(int(round(rows[m][-1] * rng.uniform(1.01, 1.12))))

    truth = {}
    for m in rows:
        for p, u in zip(periods + ["LTM"], rows[m]):
            if m != prose_only or p != "LTM":
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
    if ltm:
        heads.append("LTM %s-%02d" % (rng.choice(["Mar", "Jun", "Sep"]), (last + 1) % 100))
    foot = rng.choice(["", " (1)", "*"]) if heldout else ""
    scale_txt = rng.choice(["($ in millions)", "(USD in millions)"]) if millions else \
        rng.choice(["($ in thousands)", "(USD in 000s)", "($ in 000s)"])
    pipes = rng.random() < 0.4
    scale_in_head = rng.random() < 0.5
    show_margin = rng.choice([None, None, "adjusted_ebitda_margin", "gross_margin"])

    table_rows = [[scale_txt if scale_in_head else ""] + heads]
    for m in ["revenue", "gross_profit", "ebitda", "adjusted_ebitda", "net_income", "capex"]:
        if m == prose_only:
            continue
        table_rows.append([rng.choice(LABELS[m]) + (foot if m == "adjusted_ebitda" else "")]
                          + [_cell(u, millions) for u in rows[m]])
        if show_margin and MARGINS[show_margin] == m:
            pc = [100.0 * a / b for a, b in zip(rows[m], rows["revenue"])]
            table_rows.append([MARGIN_LABEL[show_margin]] + [_cell(x, False, True) for x in pc])
    ops = [["", *heads],
           ["Customers"] + ["{:,}".format(rng.randint(80, 9000)) for _ in heads],
           ["Employees"] + ["{:,}".format(rng.randint(40, 2500)) for _ in heads]]

    def render(trs):
        if pipes:
            return ["| " + " | ".join(r) + " |" for r in trs]
        return [r[0].ljust(28) + "".join(c.rjust(12) for c in r[1:]) for r in trs]

    fin = ["Summary Financials" + ("" if scale_in_head else " " + scale_txt)] + render(table_rows)
    if foot:
        fin.append("%s Adjusted for one-time items of $%.1f million." % (foot.strip(), rng.uniform(0.2, 3)))
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
    return text, truth, {"periods": periods, "millions": millions, "ltm": ltm}


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
    slots = sorted(k for k in truth if k[1] != "LTM")
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


# ---------------------------------------------------------------- memos

KEYS = {"rev": "revenue", "gp": "gross_profit", "ebitda": "ebitda", "adj": "adjusted_ebitda",
        "ni": "net_income", "capex": "capex"}
MARGIN_OF = {"gross_profit": "gross_margin", "adjusted_ebitda": "adjusted_ebitda_margin",
             "ebitda": "ebitda_margin", "net_income": "net_margin"}

# Slots: {L.rev} label, {P} {Q} this and the prior fiscal year, {rev@p} dollars, {adj%p} margin,
# {rev^p} growth over the prior year, {UP.rev} "increased" or "declined", {rev@ltm} LTM dollars.
DEV = {
    "labels": {"rev": ["revenue", "Revenue", "net revenue"], "gp": ["gross profit", "Gross profit"],
               "ebitda": ["EBITDA", "reported EBITDA"], "adj": ["Adjusted EBITDA", "Adj. EBITDA"],
               "ni": ["net income", "Net income"], "capex": ["capex", "capital expenditures"]},
    "periods": ["FY%d", "FY%02d"],
    "styles": ["M1", "million1"],
    "templates": [
        "In {P}, {L.rev} was {rev@p} and {L.adj} was {adj@p}.",
        "{L.rev} grew from {rev@q} in {Q} to {rev@p} in {P}.",
        "{P} {L.adj} was {adj@p}, a {adj%p} margin.",
        "{L.gp} reached {gp@p} in {P}, which is a gross margin of {gp%p}.",
        "{L.rev} {UP.rev} {rev^p} in {P}, from {rev@q} in the prior year.",
        "The Company generated {rev@p} of revenue and {adj@p} of Adjusted EBITDA in {P}.",
        "{L.rev} and {L.gp} were {rev@p} and {gp@p}, respectively, in {P}.",
        "- {L.ebitda} ({P}): {ebitda@p}",
        "{L.capex} was {capex@p} in {P}, compared with {capex@q} in {Q}.",
        "{L.ni} was {ni@p} in {P}.",
        "{L.adj} margin was {adj%p} in {P} and {adj%q} in {Q}.",
    ],
    "distractors": [
        "Founded in {year}, the Company serves {n} customers across {k} branches.",
        "Management estimates an addressable market of ${m} million.",
        "Recurring contracts represent {x}% of revenue.",
        "We would expect to pay roughly {mult}x Adjusted EBITDA.",
    ],
}
HELDOUT = {
    "labels": {"rev": ["net sales", "revenues", "total revenue"], "gp": ["gross profit"],
               "ebitda": ["EBITDA"], "adj": ["adjusted EBITDA", "Adj. EBITDA"],
               "ni": ["net income"], "capex": ["capital expenditures", "Capex"]},
    "periods": ["fiscal %d", "FY%d", "FY%02d"],
    "styles": ["million1", "mm2", "K"],
    "templates": [
        "{L.rev} for {P} came in at {rev@p}, versus {rev@q} a year earlier.",
        "{P} saw {L.rev} of {rev@p} ({rev^p} year over year) and {L.adj} of {adj@p} ({adj%p} margin).",
        "On {rev@p} of {P} revenue, the business earned {adj@p} of Adjusted EBITDA.",
        "{L.gp} rose to {gp@p} in {P} from {gp@q}.",
        "**{L.capex}:** {capex@p} in {P} ({capex@q} in {Q})",
        "Margins held up: {L.adj} margin was {adj%p} for {P}.",
        "{L.ni} totaled {ni@p} for {P} after {ni@q} in {Q}.",
        "{L.rev}, {L.gp} and {L.adj} for {P} were {rev@p}, {gp@p} and {adj@p}.",
        "With {L.ebitda} at {ebitda@p}, {P} was the strongest year in the period reviewed.",
        "Compared with {Q}, {P} {L.rev} {UP.rev} {rev^p} to {rev@p}.",
        "LTM {L.rev} stands at {rev@ltm}.",
    ],
    "distractors": [
        "The top 10 customers account for {x}% of sales.",
        "Headcount stands at {n} employees after {k} hires in {P}.",
        "The seller is seeking roughly ${m} million, or {mult}x {P} Adjusted EBITDA.",
        "The Company was established in {year} and moved to its current site in 2016.",
    ],
}
# Written after the binding rules were frozen, and run once. See the README.
HELDOUT_B = {
    "labels": {"rev": ["sales", "net revenue", "Revenue"], "gp": ["gross profit"],
               "ebitda": ["reported EBITDA", "EBITDA"], "adj": ["Adjusted EBITDA", "Adj EBITDA"],
               "ni": ["net income"], "capex": ["capex"]},
    "periods": ["FY%d", "fiscal year %d", "%d"],
    "styles": ["M1", "million1", "full"],
    "templates": [
        "{P} {L.rev} totaled {rev@p}, of which {gp@p} dropped to gross profit.",
        "{L.adj} improved to {adj@p} ({adj%p} of revenue) in {P}.",
        "Between {Q} and {P}, {L.rev} moved from {rev@q} to {rev@p}.",
        "{L.ni} for {P} was {ni@p}; {L.capex} was {capex@p}.",
        "The business posted {L.ebitda} of {ebitda@p} in {P}, against {ebitda@q} the prior year.",
        "- {P} {L.gp}: {gp@p} ({gp%p} gross margin)",
        "{L.rev} growth was {rev^p} in {P}.",
        "In {P} the Company spent {capex@p} on capital expenditures.",
        "{L.adj} was {adj@q} in {Q} and {adj@p} in {P}, with margins of {adj%q} and {adj%p}.",
        "LTM {L.adj} of {adj@ltm} compares with {adj@p} in {P}.",
    ],
    "distractors": [
        "Roughly {x}% of {P} revenue came from customers under contract.",
        "The plant runs {k} lines and employs {n} people.",
        "Bids are due in {k} days and the banker expects offers above ${m} million.",
        "Management's {P} budget assumes {x}% utilization.",
    ],
}
SPLITS = {"dev": (DEV, False), "heldout_a": (HELDOUT, True), "heldout_b": (HELDOUT_B, True)}
_SLOT = re.compile(r"\{([^}]+)\}")
_VALUE_SLOT = re.compile(r"^(\w+)([@%^])(p|q|ltm)$")


def _true_value(truth, metric, period, kind):
    """The value a correct memo would state, from the printed figures only."""
    if kind == "@":
        return truth.get((metric, period))
    rev = truth.get(("revenue", period))
    v = truth.get((metric, period))
    if kind == "%":
        return None if v is None or not rev else v / rev * 100
    prev = truth.get((metric, "FY%d" % (int(period[2:]) - 1))) if period.startswith("FY") else None
    return None if v is None or prev is None or prev <= 0 else (v / prev - 1) * 100


def _slot_name(metric, kind):
    return metric if kind == "@" else MARGIN_OF[metric] if kind == "%" else metric + "_growth"


def _wrong(rng, truth, info, metric, period, kind, style):
    """A wrong value for the slot as (text, error), or None if no clean mistake exists."""
    true = _true_value(truth, metric, period, kind)
    others = [q for q in info["periods"] if q != period]
    err = rng.choice(["scale", "period", "metric", "digit"] if kind == "@" else ["period", "metric", "digit"])
    alt = None
    if err == "period":
        alt = _true_value(truth, metric, rng.choice(others), kind)
    elif err == "metric":
        sib = SIBLING[metric] if kind != "%" else {"gross_profit": "adjusted_ebitda",
                                                   "adjusted_ebitda": "ebitda", "ebitda": "adjusted_ebitda",
                                                   "net_income": "ebitda"}[metric]
        alt = _true_value(truth, sib, period, kind)
    elif err == "digit" and kind != "@":
        alt = true + Fraction(rng.choice([-1, 1]) * rng.randint(3, 60), 10)
    if kind == "@":
        if err == "scale":
            text = fmt_usd(true / 1000, style) if rng.random() < 0.5 else fmt_usd(true / 1000, "full")
        elif err == "digit":
            step = 100000 if info["millions"] else 1000
            text = fmt_usd(_mutate_digits(rng, int(true / step)) * step, style)
        else:
            text = None if alt is None else fmt_usd(alt, style)
    else:
        text = None if alt is None else "%.1f%%" % float(alt)
    if text is None or _is_true(text, true):
        return None
    return text, err


def make_memo(rng, truth, info, split, sentences=7, error_rate=0.35):
    """Return (memo_text, planted). planted lists every figure with its line, column,
    the slot it is about and the mistake it carries (None when it is correct)."""
    periods = info["periods"]
    pstyle = rng.choice(split["periods"])

    def ptext(p):
        if p == "LTM":
            return "LTM"
        y = int(p[2:])
        return pstyle % (y % 100 if "%02d" in pstyle else y)

    lines, planted = [], []

    def emit(template, p, lead=""):
        q = "FY%d" % (int(p[2:]) - 1)
        role = {"p": p, "q": q, "ltm": "LTM"}
        # every value slot must exist in the source for this sentence to be usable
        need = [_VALUE_SLOT.match(t) for t in _SLOT.findall(template)]
        vals = {}
        for m in filter(None, need):
            metric = KEYS[m.group(1)]
            v = _true_value(truth, metric, role[m.group(3)], m.group(2))
            if v is None or (m.group(2) == "^" and "{UP." not in template and v < 0):
                return False
            vals[m.group(0)] = v
        if ("{Q}" in template or "prior year" in template or "year earlier" in template) and q not in periods:
            return False
        out, pos, row = lead, 0, len(lines) + 1
        for m in _SLOT.finditer(template):
            out += template[pos:m.start()]
            pos = m.end()
            tok = m.group(1)
            vm = _VALUE_SLOT.match(tok)
            if tok == "P":
                out += ptext(p)
            elif tok == "Q":
                out += ptext(q)
            elif tok.startswith("L."):
                out += rng.choice(split["labels"][tok[2:]])
            elif tok.startswith("UP."):
                g = _true_value(truth, KEYS[tok[3:]], p, "^")
                out += rng.choice(["increased", "grew"]) if g >= 0 else rng.choice(["declined", "fell"])
            else:
                metric, kind, per = KEYS[vm.group(1)], vm.group(2), role[vm.group(3)]
                style = rng.choice(split["styles"])
                true = vals[tok]
                shown = abs(true) if kind == "^" and "{UP." in template else true
                text = fmt_usd(shown, style) if kind == "@" else "%.1f%%" % float(shown)
                error = None
                if rng.random() < error_rate and per != "LTM" and shown == true:
                    w = _wrong(rng, truth, info, metric, per, kind, style)
                    if w:
                        text, error = w
                planted.append({"line": row, "col": len(out), "text": text,
                                "metric": _slot_name(metric, kind), "period": per, "error": error})
                out += text
        lines.append(out + template[pos:])
        return True

    def distractor():
        t = rng.choice(split["distractors"])
        lines.append(t.format(year=rng.randint(1995, 2012), n="{:,}".format(rng.randint(120, 9000)),
                              k=rng.randint(2, 40), m="{:,}".format(rng.randint(150, 4000)),
                              x=rng.randint(15, 80), mult="%.1f" % rng.uniform(5, 12),
                              P=ptext(periods[-1])))

    lines += ["# Deal summary (synthetic)", "", "## Business", ""]
    distractor()
    lines += ["", "## Financial summary", ""]
    made = tries = 0
    order = []
    while made < sentences and tries < 60:
        tries += 1
        if not order:   # go through the templates in a random order before repeating one
            order = rng.sample(split["templates"], len(split["templates"]))
        t = order.pop()
        p = rng.choice(periods[1:] if len(periods) > 1 else periods)
        if "@ltm" in t and not info.get("ltm"):
            continue
        if emit(t, p):
            made += 1
            if rng.random() < 0.25:
                distractor()
            lines.append("")
    if rng.random() < 0.5:   # a snapshot table
        cols = periods[-3:]
        lines += ["## Snapshot", "", "| Metric | " + " | ".join(ptext(c) for c in cols) + " |",
                  "|---|" + "---|" * len(cols)]
        for key in ["rev", "adj", "ni"]:
            metric = KEYS[key]
            if any((metric, c) not in truth for c in cols):
                continue
            out = "| %s |" % rng.choice(split["labels"][key])
            for c in cols:
                style = rng.choice(split["styles"])
                text, error = fmt_usd(truth[(metric, c)], style), None
                if rng.random() < error_rate:
                    w = _wrong(rng, truth, info, metric, c, "@", style)
                    if w:
                        text, error = w
                out += " "
                planted.append({"line": len(lines) + 1, "col": len(out), "text": text,
                                "metric": metric, "period": c, "error": error})
                out += text + " |"
            lines.append(out)
    return "\n".join(lines) + "\n", planted


def memo_corpus(seed, docs, split="dev"):
    """Yield (source_text, memo_text, planted) for one split: dev, heldout_a or heldout_b."""
    rng = random.Random(seed)
    split, heldout = SPLITS[split]
    for _ in range(docs):
        text, truth, info = make_doc(rng, heldout=heldout)
        memo, planted = make_memo(rng, truth, info, split)
        yield text, memo, planted
