"""Revenue read from a company's own income statement, for listed companies whose results the SEC's data gives without
any revenue figure under the tags the site reads (fundamentals.DURATION's revenue tags).

The SEC's frames and company facts carry only facts tagged under the standard taxonomies and without a breakdown, so a
company that tags its revenue under a label of its own, or only broken down by product or business, has results but no
sales there: APA Corporation's "Total revenues" line ($8,920M for 2025, as its 10-K's income statement shows) is tagged
only for its oil and gas products, beside a total of its own that adds derivative and divestiture gains ($9,220M);
Tredegar tags its sales ($722.9M) under a label of its own; Meritage Homes its homebuilding and financial services
revenue only by business; Moelis its revenue as investment banking revenue, a standard tag the site doesn't read for
every company. On September 2026 data that left 54 listings, APA the largest at $15B, without scores or a fair value
for a revenue label not read (filers.kind's "revenue_unread").

For such a company (build.py picks them: a listing without figures whose fiscal years have net income but no revenue),
the income statement of its recent annual and quarterly reports is read as the SEC renders it from each filing's XBRL:
the filing's FilingSummary.xml names the statement's page (R2.htm, R4.htm, ...), which lists each line's XBRL element,
the product or business it is broken down by, its figures for each period and the scale they are shown in. The revenue
line is picked by its element (_revenue_line), each column's dates and scale are checked against the company's own
tagged figures (_dates), and a fiscal year's figure is used only where the same statement's net income is the one the
SEC's data gives for that year (apply). Two small requests a filing, kept in CACHE for good, so a run reads only
filings made since.
"""
import datetime as dt
import html
import itertools
import json
import os
import re
import time

from . import fundamentals, net

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "statements.json")
# Bumped when what is read from a statement changes, so the filings kept in CACHE are read again.
VERSION = 4
# The time a run may spend reading statements; the rest wait for the next run.
MINUTES = 6
# How many annual reports are read (each gives two or three fiscal years), and the quarterly reports filed since the
# oldest of them: about twelve quarters, the most a report shows (fundamentals.QUARTERS_SHOWN), each with the quarter a
# year before.
ANNUALS = 3
ANNUAL = ("10-K", "10-KT", "20-F", "40-F")
QUARTERLY = ("10-Q",)
# A company is looked up again (its list of filings, one request) RECHECK_DAYS after it last was, or at once when the
# SEC's data holds a fiscal year newer than its latest annual report read or, where that report has a revenue line, a
# balance sheet newer than any period read (a new quarterly report).
RECHECK_DAYS = 30
RETRY_DAYS = 45
# The most days after a quarter ends that a company takes to file its report of it (the SEC allows 40 or 45), with a
# few to spare.
FILING_DAYS = 50

# A revenue line's element names revenue or sales, and none of these, which mark costs, gains, taxes, parts of revenue
# and totals that add other income (APA's "Total revenues and other", apa:RevenuesAndOther; Tredegar's "Total revenues,
# net of other expenses"). "Ratio" and "Member" count only as words of their own in the element's name, so revenue
# from operations, collaborations or memberships is revenue.
REVENUE_NAME = re.compile(r"Revenue|Sales", re.I)
NOT_REVENUE = re.compile(
    r"Cost|Expense|Deferred|Receivable|Payable|Proceeds|PerShare|Unearned|Unbilled|Reserve|Allowance|Commission|"
    r"Comprehensive|Gain|Loss|FairValue|IncomeTax|SalesTax|ExciseTax|SalesAndExcise|Discount|Return|Rebate|Percent|"
    r"(?-i:Ratio)|Backlog|Remaining|Obligation|AndOther|NetOfOther|Days|Number|Price|ProForma|Abstract|Axis|"
    r"(?-i:Member)(?!ship)|Intersegment|Elimination|Proportion", re.I)
COST = re.compile(r"Cost|Expense", re.I)
# Lines a revenue total may take off the lines above it, whatever sign the statement shows them with: the excise taxes
# between Brown-Forman's sales and its net sales, the discounts between Auddia's revenues and its revenues net, Safe
# Bulkers' commissions (its charterers' among them).
DEDUCTION = re.compile(r"Tax|Excise|Discount|Concession|Return|Allowance|Rebate|Commission|Deduction|Less", re.I)
# Lines that are other income rather than revenue, which a revenue total may never add (a total of "revenues" that adds
# derivative and divestiture gains, as APA's "Total revenues and other" does), and lines whose element names income of
# the kind a company may count as revenue, which may never sit between the revenue line picked and the costs below it
# unless they are other income: rent, royalties or fees beside product sales mean the sales line is only part of it.
OTHER_INCOME = re.compile(r"Gain|Loss|Derivative|FairValue|Divestiture|Nonoperating|OtherIncome|Impairment", re.I)
INCOME_LIKE = re.compile(r"Income|Revenue|Sales|Royalt|Fee|Rent|Interest|Dividend|Commission", re.I)
# The net income lines that tie a statement's column to a fiscal year the SEC's data gives (apply), and the lines whose
# tagged figures date each column and confirm its scale (_dates), tried in this order.
NET_INCOME = ("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
              "NetIncomeLossAvailableToCommonStockholdersDiluted")
ANCHORS = NET_INCOME + ("OperatingIncomeLoss",
                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                        "IncomeLossFromContinuingOperations", "CostsAndExpenses", "OperatingExpenses")
# The most of those looked up for one company in a run (one request each).
ANCHOR_LOOKUPS = 3
# The most breakdowns of one revenue element worked through (_dimensional): each is checked against the sums of every
# subset of the others.
MAX_SECTIONS = 12
SCALES = {"Thousands": 1e3, "Millions": 1e6, "Billions": 1e9}
MONTHS = {m: i + 1 for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                          "Dec"))}


class _OutOfTime(Exception):
    """The run's time for reading statements is spent (a network timeout is another matter: that company is tried
    again next run, and the rest are read)."""


def _archive(cik, accn, name):
    return net.sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{name}").decode(
        "utf-8", "replace")


def statement_page(summary):
    """(title, page) of the income statement among a filing's statements (its FilingSummary.xml): the first statement
    of operations, income, earnings or loss, or where the company has only a statement of comprehensive income or loss
    (Microbot Medical's "Consolidated Statements of Comprehensive Loss"), that one. None where there is none."""
    found = []
    for rep in re.findall(r"<Report[ >].*?</Report>", summary, flags=re.S):
        get = lambda t: html.unescape((re.search(f"<{t}>(.*?)</{t}>", rep, flags=re.S) or [None, ""])[1]).strip()
        name, page = get("ShortName").lower(), get("HtmlFileName")
        if get("MenuCategory") != "Statements" or not page.endswith(".htm"):
            continue
        if re.search(r"parenthetical|balance sheet|financial condition|financial position|cash flow|changes in|"
                     r"stockholders|shareholders|members|partners|net assets|equity|deficit|segment", name):
            continue
        if re.search(r"operations|income|earnings|loss|profit", name):
            found.append((get("ShortName"), page, "comprehensive" in name and not re.search(
                r"operations|earnings|and comprehensive|income and|loss and", name)))
    first = [f for f in found if not f[2]] or found
    return first[0][:2] if first else None


def _number(text):
    """A cell's figure as shown ("$ (1,234)" is -1234), or None for a blank cell."""
    t = re.sub(r"\[\d+\]|\$|\s|\xa0", "", text)
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = float(t.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


def _date(text):
    m = re.search(r"([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", text)
    if not m or m.group(1) not in MONTHS:
        return None
    return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))


def parse(page):
    """A statement page (R<n>.htm) as {"title", "currency", "scale", "columns": [(months, last day) or None], "rows":
    [{"cls", "prefix", "name", "section", "label", "values"}]}: each line's XBRL element (prefix and name), the
    breakdown it sits in (None, or the heading's element and label, such as ("srt_ProductOrServiceAxis=
    us-gaap_OilAndGasMember", "Oil and gas")), its label and its figures as shown (the scale not applied), None where
    blank. "cls" ends in "u" on a total line, which the page underlines. None where the page is laid out in a way this
    doesn't read (other than two heading rows: the periods, then their last days, each period once)."""
    page = re.sub(r"<script.*?</script>", "", page, flags=re.S)
    table = re.search(r'<table class="report".*?</table>', page, flags=re.S)
    if not table:
        return None
    heads, rows, section, marks = [], [], None, set()
    # The title's cell spans the label column and, on a statement with footnotes, the column of footnote marks beside
    # it (APA's 10-Qs of 2024), which each row's figures follow. Marks may also sit in a column of their own beside a
    # period's figures (class "fn", which the period's heading spans too: FRNM's 10-K for 2025), left out below.
    first = re.search(r"<th([^>]*)>", table.group(0))
    span = first and re.search(r'colspan="(\d+)"', first.group(1))
    lead = int(span.group(1)) if span else 1
    for cls, tr in re.findall(r'<tr(?: class="(\w+)")?>(.*?)</tr>', table.group(0), flags=re.S):
        ths = re.findall(r"<th([^>]*)>(.*?)</th>", tr, flags=re.S)
        if ths:
            heads.append([(int((re.search(r'colspan="(\d+)"', a) or [0, 1])[1]),
                           " ".join(html.unescape(re.sub(r"<[^>]+>", " ", c.replace("<br>", " | "))).split()))
                          for a, c in ths])
            continue
        tds = re.findall(r"<td([^>]*)>(.*?)</td>", tr, flags=re.S)
        ref = re.search(r"defref_([^']+)'", tr)
        if not tds or not ref:
            continue
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", tds[0][1])).split())
        if cls == "rh":
            section = (ref.group(1), label)
            continue
        prefix, _, name = ref.group(1).partition("_")
        marks |= {k for k, (a, _) in enumerate(tds[lead:]) if 'class="fn"' in a}
        rows.append({"cls": cls or "", "prefix": prefix, "name": name, "section": section, "label": label,
                     "values": [_number(html.unescape(re.sub(r"<[^>]+>", "", c))) for _, c in tds[lead:]]})
    if len(heads) != 2 or not heads[0]:
        return None
    title = heads[0][0][1]
    scale = re.search(r"\$ in (Thousands|Millions|Billions)", title) or re.search(r"\bIn (Thousands|Millions|Billions)",
                                                                                   title)
    groups = []
    for span, text in heads[0][1:]:
        m = re.search(r"(\d+) (Months|Weeks) Ended", text)
        groups += [None if not m else int(m.group(1)) if m.group(2) == "Months" else round(int(m.group(1)) * 12 / 52)
                   ] * span
    ends = [e for span, text in heads[1] for e in [_date(text)] * span]
    if len(groups) != len(ends):
        return None
    keep = [k for k in range(len(groups)) if k not in marks]
    # Columns broken down by a heading of their own ("Dec. 31, 2025 | Parent [Member]" beside "Dec. 31, 2025") would give
    # one period twice, the part's figures as well as the whole's, so such a page isn't read.
    periods = [(groups[k], ends[k]) for k in keep if groups[k] and ends[k]]
    if len(set(periods)) != len(periods) or any("[Member]" in text for _, text in heads[1]):
        return None
    for r in rows:
        r["values"] = [r["values"][k] if k < len(r["values"]) else None for k in keep]
    return {"title": title.split(" - ")[0].strip(), "currency": (re.search(r" - ([A-Z]{3}) \(", title) or [0, None])[1],
            "scale": SCALES[scale.group(1)] if scale else 1.0,
            "columns": [(groups[k], ends[k]) if groups[k] and ends[k] else None for k in keep], "rows": rows}


def _is_revenue(row):
    return bool(REVENUE_NAME.search(row["name"])) and not NOT_REVENUE.search(row["name"])


def _sums(total, parts):
    """Whether `total` (a line's figures) is the sum of `parts` in every column, blanks counting as nothing."""
    return any(total) and all(abs((t or 0) - sum(p[k] or 0 for p in parts)) < 0.5 for k, t in enumerate(total))


def _block_total(rows, i, val):
    """Whether total line rows[i] adds only revenue lines: those above it back to the last heading or total are all
    revenue lines and add up to it. Safe Bulkers' "Net revenues" takes its commissions off its revenues, and Tredegar's
    total adds its other income, so neither is."""
    block = []
    for r in reversed(rows[:i]):
        if r["section"] != rows[i]["section"] or r["name"].endswith("Abstract") or r["cls"].endswith("u"):
            break
        if any(v is not None for v in val(r)):
            block.append(r)
    return bool(block) and all(_is_revenue(r) for r in block) and _sums(val(rows[i]), [val(r) for r in block])


def _has(r, val):
    return any(v is not None for v in val(r))


def _adds_up(total, lines):
    """Whether a total line's figures (`total`) are those of `lines` (rows, with their figures under "v") above it, in
    every column: as shown, or with each deduction (DEDUCTION, other than a revenue line: sales "including assessed
    tax") taken off whatever sign it is shown with."""
    cut = lambda r: bool(DEDUCTION.search(r["name"])) and not _is_revenue(r)
    net = [[-abs(x) if x is not None and cut(r) else x for x in r["v"]] for r in lines]
    return bool(lines) and (_sums(total, [r["v"] for r in lines]) or _sums(total, net))


def _more_revenue(main, at, val):
    """Whether the lines below main[at] (the revenue line picked; `main` the statement's lines with figures outside any
    breakdown), before the first cost or expense line, hold more of the company's revenue: right below it (after other
    income only), a line naming income of a kind that may be revenue (INCOME_LIKE, other than other income); or a total
    adding the pick to lines other than other income. Tredegar's other income between its sales and its "Total
    revenues, net of other expenses" is neither; rent income below product sales, adding up to a "Total revenues and
    other income", is both. Moelis' professional fees, among expenses not named as such, are neither."""
    lines = []
    for r in main[at + 1:]:
        if COST.search(r["name"]):
            return False
        if r["cls"].endswith("u"):
            return _sums(val(r), [val(main[at])] + [val(x) for x in lines]) \
                and any(not OTHER_INCOME.search(x["name"]) for x in lines)
        if all(OTHER_INCOME.search(x["name"]) for x in lines) and INCOME_LIKE.search(r["name"]) \
                and not OTHER_INCOME.search(r["name"]):
            return True
        lines.append(r)
    return False


def revenue_line(p, cols):
    """The statement's revenue line, {"label", "element", "sections" (the breakdowns it is shown under, [] for a line
    without one), "members" (the same as XBRL axis and member, "axis=member"), "values" (as shown, one per column in
    `cols`)}, and None; or None and why there is none: "none" (no
    revenue line with figures) or "ambiguous" (which one, or whether it is all of revenue, can't be told).

    Every figure is one the statement shows; revenue given only in parts with no line for their total (Meritage Homes'
    homebuilding and financial services revenue) is never added up here.

    A line without a breakdown comes first. The statement's first total line is its revenue where it is a revenue line
    (REVENUE_NAME) adding up every line above it, none of them other income (OTHER_INCOME), less any deductions
    (_adds_up): Legacy Housing's "Total net revenue"
    adds its loan interest to its product sales, Brown-Forman's "Net sales" takes excise taxes off its sales. Otherwise
    the only revenue line, or the one that adds up the others, or a total line adding only the revenue lines above it
    (_block_total), where every line with figures above it is a revenue line: revenue below a lender's interest income is
    only part of what it earns (Granite Point's revenue from property it took over). A total line that adds other lines
    is left out where there is another revenue line (Tredegar's "Total revenues, net of other expenses", beside its
    sales). The line must be at least as large as every other revenue line on the statement but those it nets, so a
    part of revenue never passes for the whole (Alpha Metallurgical's coal revenues, beside its total revenues). Every
    other revenue line must be one of its parts, or a breakdown of its element or a part's (Picard Medical's revenues
    by product and rentals); no line in a total it is may be a cost (a net figure after voyage costs is no revenue,
    though commissions and taxes may come off); nor may the lines below it hold more revenue (_more_revenue); and it
    must show some revenue above nothing.

    A statement whose revenue is shown only broken down (APA's by product, Proficient Auto Logistics' for the company
    since its listing and its predecessor before) has it read from its one revenue element's breakdowns (_dimensional),
    where that heads the statement too (_heads)."""
    rows = p["rows"]
    val = lambda r: [r["values"][k] if k < len(r["values"]) else None for k in cols]
    good = lambda r: _is_revenue(r) and _has(r, val) and all(v is None or v >= 0 for v in val(r))
    rev = [(i, r) for i, r in enumerate(rows) if good(r)]
    outside = [(i, r) for i, r in rev if r["section"] is None]
    plain = [(i, r) for i, r in outside
             if not (r["cls"].endswith("u") and len(outside) > 1 and not _block_total(rows, i, val))]
    broken = [r for _, r in rev if r["section"] is not None]
    main = [r for r in rows if r["section"] is None and _has(r, val)]
    first = next((k for k, r in enumerate(main) if r["cls"].endswith("u")), None)
    pick, block = None, []
    if first is not None and good(main[first]) \
            and not any(not _is_revenue(r) and (OTHER_INCOME.search(r["name"])
                            or COST.search(r["name"])) for r in main[:first]) \
            and _adds_up(val(main[first]), [{"name": r["name"], "v": val(r)} for r in main[:first]]):
        pick, block = main[first], main[:first]
    elif plain:
        vals = [val(r) for _, r in plain]
        if len(plain) == 1:
            at, pick = plain[0]
        else:
            at, pick = next(((i, r) for (i, r), v in zip(plain, vals) if _sums(v, [x for x in vals if x is not v])),
                            None) or next(((i, r) for i, r in plain if r["cls"].endswith("u") and _block_total(rows, i, val)),
                                          (None, None))
        if pick is None or not all(_is_revenue(r) for r in rows[:at] if r["section"] is None and _has(r, val)):
            return None, "ambiguous"
        # Its parts: the other plain lines it adds up, and the revenue lines above it that a total adds.
        block = [r for _, r in plain if r is not pick] + [r for i, r in outside if i < at]
    if pick is not None:
        # Any other revenue line is one of its parts, or a breakdown of its element or of a part's: revenue shown apart
        # below the costs (a homebuilder's financial services revenue, by itself or broken down by segment) is more of
        # it, which a total above the costs leaves out.
        own = {(r["prefix"], r["name"]) for r in [pick] + block}
        if any(r is not pick and not any(r is x for x in block) for _, r in outside) \
                or any((r["prefix"], r["name"]) not in own for r in broken):
            return None, "ambiguous"
        if any(any(b is not None and (v is None or v < b) for v, b in zip(val(pick), val(r)))
               for _, r in rev if r is not pick and not any(r is x for x in block)):
            return None, "ambiguous"
        if _more_revenue(main, next(k for k, r in enumerate(main) if r is pick), val) \
                or not any((v or 0) > 0 for v in val(pick)):
            return None, "ambiguous"
        return {"label": pick["label"], "element": f"{pick['prefix']}:{pick['name']}", "sections": [], "members": [],
                "values": val(pick)}, None
    if not broken:
        return None, "none"
    elements = {(r["prefix"], r["name"]) for r in broken}
    found = len(elements) == 1 and _dimensional(broken, val)
    if not found or not _heads(rows, found["values"], val):
        return None, "ambiguous"
    prefix, name = elements.pop()
    return {**found, "element": f"{prefix}:{name}"}, None


def _member(ref):
    """A breakdown's heading element as the page names it ("srt_ProductOrServiceAxis=us-gaap_OilAndGasMember") in XBRL's
    own form ("srt:ProductOrServiceAxis=us-gaap:OilAndGasMember")."""
    return "=".join(x.replace("_", ":", 1) for x in ref.split("="))


def _dimensional(rows, val):
    """{"label", "sections", "members", "values"} for one revenue element shown only broken down, or None: the one
    breakdown that
    is the sum of others and no other's part (APA's "Oil and gas": its production revenue plus its sales of oil and gas
    it bought, each exactly, in every column), where every other breakdown on its axis is its part and those on other
    axes no larger; or, where several are no other's part on one axis but never more than one of them has a figure in
    a column, each column's one figure (Proficient Auto Logistics' revenue since its listing, and its predecessor's in
    the columns before). None where breakdowns would have to be added up."""
    secs = {}
    for r in rows:
        secs.setdefault(r["section"], (r, val(r)))
    keys = list(secs)
    if len(keys) > MAX_SECTIONS:
        return None
    parts = {}
    for k in keys:
        others = [o for o in keys if o != k]
        for n in range(2, len(others) + 1):
            for combo in itertools.combinations(others, n):
                if _sums(secs[k][1], [secs[o][1] for o in combo]):
                    parts.setdefault(k, set()).update(combo)
    roots = [k for k in keys if not any(k in s for s in parts.values())]
    axis = lambda k: k[0].split("=")[0]
    columns = range(len(secs[keys[0]][1]))
    top = [k for k in roots if k in parts]
    if len(top) == 1:
        # Breakdowns on another axis (by segment, or by a related party) cut the same revenue another way.
        t, v = top[0], secs[top[0]][1]
        if all(axis(k) != axis(t) and all(x is None or (y is not None and x <= y) for x, y in zip(secs[k][1], v))
               for k in roots if k != t):
            return {"label": secs[t][0]["label"], "sections": [t[1]], "members": [_member(t[0])], "values": v}
        return None
    if len(roots) >= 2 and len({axis(k) for k in roots}) == 1 \
            and all(sum(secs[k][1][c] is not None for k in roots) <= 1 for c in columns):
        return {"label": secs[roots[0]][0]["label"], "sections": [k[1] for k in roots],
                "members": [_member(k[0]) for k in roots],
                "values": [next((secs[k][1][c] for k in roots if secs[k][1][c] is not None), None) for c in columns]}
    return None


def _heads(rows, values, val):
    """Whether revenue shown only broken down (`values`, per column) heads the statement: the statement's first total
    line outside any breakdown, less every line with figures above it, comes to it in every column (APA's "Total
    revenues and other", $9,220M for 2025, less its gains and losses on derivatives and divestitures, $300M, is its
    $8,920M of revenue), where each of those lines is other income (OTHER_INCOME: lease income or fees there would be
    revenue beside the breakdown's); or no line outside a breakdown has figures before the first cost or expense line
    (Proficient Auto Logistics' statement is broken down whole, into itself and its predecessor). Nelnet's revenue from
    servicing loans, shown below its interest income, does neither."""
    main = [r for r in rows if r["section"] is None and _has(r, val)]
    first = next((k for k, r in enumerate(main) if r["cls"].endswith("u")), None)
    if first is not None and all(OTHER_INCOME.search(r["name"]) for r in main[:first]) \
            and _sums(val(main[first]), [val(r) for r in main[:first]] + [values]):
        return True
    return not main or bool(COST.search(main[0]["name"]))


def _dates(cik, accn, p, cols, figures):
    """{column: (first day, last day)} for each column in `cols` whose period one of the company's own tagged figures
    from this filing gives (fundamentals._tag_figures): a line on the statement (ANCHORS, in order) whose figure in
    the column, times the page's scale, is the figure tagged for a period of this filing with the column's last day
    and a length that fits its months (to the rounding of the scale). So the scale read is confirmed, and each period
    has its exact first day, which the page doesn't give. `figures` keeps each line's tagged figures ({tag: list}) for
    the company; a column no line dates is left out. Net income comes first, the line most companies tag without a
    breakdown (Proficient Auto Logistics shows every line for itself and its predecessor apart, and tags its own net
    income alone too)."""
    out = {}
    anchors = sorted((r for r in p["rows"] if r["prefix"] == "us-gaap" and r["name"] in ANCHORS),
                     key=lambda r: ANCHORS.index(r["name"]))
    for k in cols:
        months, end = p["columns"][k]
        for row in anchors:
            tag = row["name"]
            if k >= len(row["values"]) or row["values"][k] is None:
                continue
            if tag not in figures:
                if len(figures) >= ANCHOR_LOOKUPS:
                    continue
                try:
                    figures[tag] = fundamentals._tag_figures(cik, tag)
                except (net.NotFound, ValueError):
                    figures[tag] = []
            shown = row["values"][k] * p["scale"]
            f = next((f for f in figures[tag] if f.get("accn") == accn and f.get("end") == end.isoformat()
                      and f.get("start") and abs((end - dt.date.fromisoformat(f["start"])).days + 1 - months * 30.44) <= 20
                      and abs(f["val"] - shown) <= max(0.5 * p["scale"], 0.5)), None)
            if f:
                out[k] = (f["start"], f["end"])
                break
    return out


def read_filing(cik, accn, form, filed, figures):
    """What one filing's income statement gives, as kept in CACHE: {"v", "form", "filed", "status" ("ok", or why not:
    "no_statement", "layout", "currency", "none", "ambiguous", "undated"), "title", "years" (the last days of the fiscal
    years it gives), and for "ok" "line" (the revenue line's label, element and breakdowns), "scale" and "periods":
    [[first day, last day, revenue, [each net income line's figure]]] for each column of three months or a year that
    the company's own figures date (_dates)}. Figures are in dollars."""
    base = {"v": VERSION, "form": form, "filed": filed}
    try:
        found = statement_page(_archive(cik, accn, "FilingSummary.xml"))
        page = found and _archive(cik, accn, found[1])
    except net.NotFound:
        found = None
    if not found:
        return {**base, "status": "no_statement"}
    p = parse(page)
    if p is None:
        return {**base, "status": "layout", "title": found[0]}
    cols = [k for k, c in enumerate(p["columns"]) if c and c[0] in (3, 12)]
    base.update(title=found[0], years=sorted(p["columns"][k][1].isoformat() for k in cols if p["columns"][k][0] == 12))
    if p["currency"] != "USD":
        return {**base, "status": "currency", "currency": p["currency"]}
    line, why = revenue_line(p, cols)
    if line is None:
        return {**base, "status": why}
    dates = _dates(cik, accn, p, cols, figures)
    ni = [[r["values"][k] if k < len(r["values"]) else None for k in cols] for r in p["rows"]
          if r["prefix"] == "us-gaap" and r["name"] in NET_INCOME]
    periods = [[*dates[k], round(line["values"][j] * p["scale"]), [round(x[j] * p["scale"]) for x in ni if x[j] is not None]]
               for j, k in enumerate(cols) if k in dates and line["values"][j] is not None]
    if not periods:
        return {**base, "status": "undated"}
    return {**base, "status": "ok", "scale": p["scale"],
            "line": {k: line[k] for k in ("label", "element", "sections", "members")},
            "periods": periods}


def _filings(cik):
    """([(accession number, form, date filed)] of the company's latest ANNUALS annual reports, newest first, and of its
    quarterly reports filed since the oldest of them), those with machine-readable figures only. Amendments are left
    out: most carry a page or two."""
    recent = (net.sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("filings") or {}).get(
        "recent") or {}
    rows = [r[:3] for r in zip(recent.get("accessionNumber") or [], recent.get("form") or [],
                               recent.get("filingDate") or [], recent.get("isXBRL") or []) if r[3]]
    annual = [r for r in rows if r[1] in ANNUAL][:ANNUALS]
    return annual, [r for r in rows if r[1] in QUARTERLY and annual and r[2] >= annual[-1][2]]


def _results_end(company):
    """The last day of the newest fiscal year with net income in a fundamentals record, or ""."""
    return max([s["end"] for s in (company.get("annual") or {}).values() if s.get("net_income") is not None
                and s.get("end")] + [""])


def read(need, companies, today, minutes=MINUTES):
    """{cik: what the company's statements give (_gather)} for the CIKs in `need` ({cik: market value}; `companies` the
    fundamentals records) that have been looked up, from CACHE and the filings read this run. Due companies (RECHECK_DAYS)
    go the most valuable first; each one's latest annual report is read first and its other reports only where that
    has a revenue line. Stops at the time budget or when the SEC throttles; the rest wait for the next run."""
    try:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    filings, known = cache.setdefault("filings", {}), cache.setdefault("companies", {})
    old = (today - dt.timedelta(days=RECHECK_DAYS)).isoformat()
    recent = (today - dt.timedelta(days=RETRY_DAYS)).isoformat()
    # A filing is read again when what is read from it has changed (VERSION), or when its periods couldn't be dated
    # and it was filed lately, since the SEC's companyconcept answers run days behind its filings (fundamentals'
    # _quarter_series).
    again = lambda f: f.get("v") != VERSION or f.get("status") == "undated" and (f.get("filed") or "") >= recent

    def due(cik):
        k = known.get(str(cik))
        first = filings.get(k["filings"][0]) if k and k["filings"] else None
        if not k or k["checked"] < old or any(again(filings.get(a) or {}) for a in k["filings"]):
            return True
        if first is None:
            return False  # no annual report with machine-readable figures when last looked up
        c = companies.get(cik) or {}
        if first.get("years") and _results_end(c) > max(first["years"]):
            return True  # a fiscal year newer than its latest annual report read
        # A balance sheet newer than any period read means a quarterly report to read, until the time it may take to
        # file one has passed since (a quarter whose statement couldn't be dated would otherwise be due every run).
        ends = [pr[1] for a in k["filings"] for pr in (filings.get(a) or {}).get("periods") or []]
        as_of = (c.get("latest") or {}).get("as_of") or ""
        return first.get("status") == "ok" and as_of > max(ends + [""]) \
            and k["checked"] < (dt.date.fromisoformat(as_of) + dt.timedelta(days=FILING_DAYS)).isoformat()

    todo = sorted((c for c in need if due(c)), key=lambda c: -(need[c] or 0))
    stop, looked, read_now, blocked, failed = time.monotonic() + minutes * 60, 0, 0, False, 0
    fresh = set()  # filings read this run, kept though their company's reading was cut short
    for cik in todo:
        if time.monotonic() > stop:
            break
        try:
            annual, quarterly = _filings(cik)
            figures, keep = {}, []
            for n, (accn, form, filed) in enumerate(annual + quarterly):
                if time.monotonic() > stop:
                    raise _OutOfTime
                if again(filings.get(accn) or {}):
                    filings[accn] = read_filing(cik, accn, form, filed, figures)
                    fresh.add(accn)
                    read_now += 1
                keep.append(accn)
                if n == 0 and filings[accn]["status"] != "ok":
                    break  # no revenue line read in its latest annual report, so none of the rest is read
            known[str(cik)] = {"checked": today.isoformat(), "filings": keep}
            looked += 1
        except net.Throttled:
            blocked = True
            break
        except _OutOfTime:
            break
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            failed += 1
            continue
    if looked or read_now:
        # Filings no longer among any company's latest reports are dropped, so the file stays the size of what is used.
        used = {a for k in known.values() for a in k["filings"]} | fresh
        for a in [a for a in filings if a not in used]:
            del filings[a]
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            tmp = CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, CACHE)
        except OSError:
            pass
    # A company whose filings aren't all read as this version reads them (a re-read after VERSION changed, cut short by
    # the time budget or the SEC) gets nothing until they are, so a reading VERSION was changed to stop is never used.
    fresh_read = lambda c: all((filings.get(a) or {}).get("v") == VERSION for a in known[str(c)]["filings"])
    out = {c: _gather([{**filings[a], "accn": a} for a in known[str(c)]["filings"] if a in filings])
           for c in need if str(c) in known and fresh_read(c)}
    print(f"  Income statements of {len(need)} listings with results but no revenue under the tags read: {looked} "
          f"looked up, {read_now} filings read" + (" (the SEC throttled the rest)" if blocked else "")
          + (f", {failed} failed" if failed else "")
          + f", {sum(1 for c in need if c not in out)} still to look up or read again; a revenue line in "
          f"{sum(1 for g in out.values() if g['latest'] and g['latest']['status'] == 'ok')}", flush=True)
    return out


def _gather(reads):
    """What a company's filings read (its latest annual report first) give: {"latest" (that report's accession number,
    form, date filed, status, statement title, revenue line, fiscal years and a currency other than dollars, or None),
    "years" ({last day: [first day, last day, revenue,
    [net income lines], accession number, scale]} for each fiscal year, from the latest filing giving it), "rows" ([first
    day, last day, revenue, accession number, date filed] of every quarter and year read, oldest filing first, as
    fundamentals' "concept" rows), "filings" ({accession number: {"form", "filed", "title", "line"}}), "cut" ([date
    filed, accession number] of the last filing made before a restatement, below, or ["", ""])}. Its years are from
    the filings made since that one only, as are the quarters apply sets."""
    latest = next(({k: r.get(k) for k in ("accn", "form", "filed", "status", "title", "line", "years", "currency")}
                   for r in reads if r["form"] in ANNUAL), None)
    years, rows, info = {}, [], {}
    ok = sorted((r for r in reads if r["status"] == "ok"), key=lambda r: (r["filed"], r["accn"]))
    # A filing that gives another figure for a period than a later one does (by more than fundamentals.RESTATED_GAP)
    # was made before a restatement, as was every filing before it, so the periods only they give are on the old basis
    # and are left out: Tredegar restated its years and quarters without Terphane, sold in November 2024, from its 10-K
    # for 2024, so its 2021 and its quarters of 2023, given only by its reports of 2024 and before, go. Only figures of
    # one line (element and breakdowns) are compared, since apply never sets figures of two lines side by side.
    gave, cut = {}, ("", "")
    for r in ok:
        line = (r["line"].get("element"), tuple(r["line"].get("members") or ()))
        for start, end, value, _ in r["periods"]:
            old = gave.get((line, start, end))
            if old and abs(value - old[0]) > fundamentals.RESTATED_GAP * max(abs(value), abs(old[0])):
                cut = max(cut, old[1])
            gave[(line, start, end)] = (value, (r["filed"], r["accn"]))
    # Every filing's rows are kept all the same, for the footings that keep a restatement out of a worked-out quarter
    # (fundamentals' _quarter_series): a restatement a later filing has yet to reach (Tredegar's 10-Q of November 2024,
    # before any filing gave its third quarter of 2024 again) is only told by them.
    for r in ok:
        info[r["accn"]] = {k: r.get(k) for k in ("form", "filed", "title", "line")}
        for start, end, value, ni in r["periods"]:
            rows.append([start, end, value, r["accn"], r["filed"]])
            if (r["filed"], r["accn"]) > cut and (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days >= 300:
                years[end] = [start, end, value, ni, r["accn"], r["scale"]]
    return {"latest": latest, "years": years, "rows": sorted(rows, key=lambda r: (r[4], r[3], r[1], r[0])),
            "filings": info, "cut": list(cut)}


def apply(company, got):
    """A copy of a fundamentals record with each fiscal year's revenue from the company's income statements (`got`,
    read()), under fundamentals.STATEMENT_REVENUE, and their quarters, or None where none applies. A year takes the
    figure of the latest statement giving the same dates (within fundamentals.SEAM_DAYS) whose net income, on one of its
    net income lines, is the year's in the record, to the rounding of the statement's scale, so a figure is never set
    beside another period's or another company's results (Proficient Auto Logistics' statement gives its predecessor's
    2023 beside its own years). None as well where the newest year with results in the record gets none, which would
    leave the company valued on an older year. Other years and every quarter are taken only from the same line
    (element and breakdowns) as the newest year's. Quarters and the rows kept for their footings (fundamentals'
    _quarter_series) start with the oldest year set, so a predecessor's quarters before it (Proficient Auto
    Logistics' third quarter of 2023, in its 10-Q of that quarter in 2024) never pass for the company's."""
    if not got or not got.get("years"):
        return None
    D, seam = dt.date.fromisoformat, fundamentals.SEAM_DAYS
    near = lambda a, b: bool(a and b) and abs((D(a) - D(b)).days) <= seam
    tag = fundamentals.STATEMENT_REVENUE
    # The line a filing's revenue was read from, as element and breakdowns.
    line = lambda accn: (lambda l: (l.get("element"), tuple(l.get("members") or ())))(
        (got["filings"].get(accn) or {}).get("line") or {})
    figs = {}
    for y, s in (company.get("annual") or {}).items():
        ni = s.get("net_income")
        if ni is None or not s.get("end"):
            continue
        fig = next((f for e, f in got["years"].items() if near(e, s["end"])
                    and (not s.get("start") or near(f[0], s["start"]))), None)
        if fig is not None and any(abs(x - ni) <= max(0.5 * fig[5], 0.5) for x in fig[3]):
            figs[y] = fig
    newest = max((y for y, s in (company.get("annual") or {}).items() if s.get("net_income") is not None), default=None)
    if newest not in figs:
        return None
    # Every year and quarter set comes from the same line as the newest year's (its element, and its breakdowns where it
    # is shown broken down), so a company that changed its line, or whose quarterly reports lay revenue out another way,
    # never gets figures of two kinds side by side.
    key = line(figs[newest][4])
    annual, used = dict(company.get("annual") or {}), set()
    for y, fig in figs.items():
        if line(fig[4]) != key:
            continue
        s, (start, end, value, _, accn, _) = annual[y], fig
        annual[y] = dict(s, revenue=value, revenue_tag=tag, revenue_tags={tag: value},
                         revenue_accns={**(s.get("revenue_accns") or {}), tag: accn}, start=start, end=end)
        used.add(y)
    # A year whose revenue was tagged under another line, as some are where a company moved its revenue to a label of
    # its own (filers.kind sends those whose newest year is unread here), has it left out rather than set beside these.
    for y, s in annual.items():
        if y not in used and s.get("revenue") is not None:
            annual[y] = dict(s, revenue=None, revenue_tag=None, revenue_tags={})
    since = min(D(annual[y]["start"]) for y in used) - dt.timedelta(days=seam)
    rows = [list(r) for r in got["rows"] if D(r[0]) >= since and line(r[3]) == key]
    quarters = {k: dict(v) for k, v in (company.get("quarters") or {}).items()}
    latest, cut = {}, tuple(got.get("cut") or ("", ""))
    for start, end, value, accn, filed in rows:
        # A quarter only filings made before a restatement give is on the old basis (_gather), so it is left blank.
        if (filed, accn) > cut and \
                fundamentals.QUARTER_DAYS[0] <= (D(end) - D(start)).days + 1 <= fundamentals.QUARTER_DAYS[1]:
            latest[(start, end)] = {"val": value, "accn": accn}  # rows run oldest filing first, so the latest wins
    for key, fact in latest.items():
        quarters.setdefault(key, {})[tag] = fact
    return {**company, "annual": annual, "quarters": quarters,
            "concept": {**(company.get("concept") or {}), tag: rows}}


def source(got, fiscal_year_end):
    """Where a company's fiscal year's revenue was read (the report's revenue_source): {"label" (the income statement's
    line, as the filing names it), "element" (its XBRL element, "prefix:name"), "sections" (the breakdowns it is shown
    under, [] for none), "members" (the same as XBRL axis and member), "statement" (the statement's title), "form",
    "filed", "accn" (the filing's accession number)} of the filing that gave the year ending `fiscal_year_end`, or
    None."""
    fig = (got or {}).get("years", {}).get(fiscal_year_end)
    info = fig and got["filings"].get(fig[4])
    if not info:
        return None
    # The filing's own words, which the report shows, with any dash as a plain hyphen (the site's copy has none).
    plain = lambda t: re.sub(r"\s*[‒-―]\s*", " - ", t) if isinstance(t, str) else t
    line = info["line"]
    return {**line, "label": plain(line["label"]), "sections": [plain(s) for s in line.get("sections") or []],
            "statement": plain(info["title"]), "form": info["form"], "filed": info["filed"], "accn": fig[4]}
