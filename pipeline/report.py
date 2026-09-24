"""Single-ticker report: 1-100 health bars, a fair-value range and a verdict."""
import math
import statistics

from .fundamentals import NORMAL_TAX

# Deposits, loans and insurance reserves make cash, debt and cash flow part of the business itself for
# banks, insurers and lenders, so cash-based measures are neither scored nor used to value them.
FINANCIAL_SECTOR = "Finance"
FINANCIAL_NOTE = ("Not scored for banks, insurers and other financial companies. Their cash and borrowing are part of "
                  "the business itself (deposits, loans, insurance reserves), so this measure would mislead.")


def is_financial(u):
    return u.get("sector") == FINANCIAL_SECTOR


UTILITIES = {"Electric Utilities: Central", "Power Generation", "Natural Gas Distribution", "Water Supply"}


def normal_tax_rate(u):
    # A REIT pays no corporate income tax on the profit it pays out, so its normal rate is zero. A utility's tax is
    # shaped every year by regulators and energy tax credits (PG&E paid less than nothing in each of 2022 to 2025),
    # so a benefit on its profit is normal (None).
    ind = u.get("industry")
    return 0.0 if ind == "Real Estate Investment Trusts" else None if ind in UTILITIES else NORMAL_TAX


def _lerp(x, pts):
    """Piecewise-linear map through [(x, score), ...] sorted by x, clamped to 1-100."""
    if x <= pts[0][0]:
        y = pts[0][1]
    elif x >= pts[-1][0]:
        y = pts[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= x <= x1:
                y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                break
    return int(round(max(1, min(100, y))))


def _pct_rank(value, population, lower_is_better):
    """Share of peers this company beats, as 1-100."""
    pop = [p for p in population if p is not None]
    if value is None or len(pop) < 5:
        return None
    worse = sum(1 for p in pop if (p > value if lower_is_better else p < value))
    ties = sum(1 for p in pop if p == value)
    return int(round(max(1, min(100, (worse + ties / 2) / len(pop) * 100))))


def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def multiple(x):
    return "n/a" if x is None else f"{x:.1f}x"


def dollars(x):
    return f"${x:,.2f}"


def money(x):
    x = abs(x)
    return f"${x / 1e9:,.1f}B" if x >= 1e9 else f"${x / 1e6:,.0f}M" if x >= 1e6 else f"${x:,.0f}"


def cents(x):
    n = round(abs(x) * 100)
    return f"{n} cent{'' if n == 1 else 's'}"


def profit(x):
    return money(x) if x >= 0 else f"a loss of {money(x)}"


def one_time_note(m):
    """Plain-English note on the one-time items left out of last year's profit, or None when there were none."""
    items = {i["kind"]: i for i in m.get("one_time") or []}
    if not items:
        return None
    parts = []
    if "discontinued" in items:
        parts.append(f"{money(items['discontinued']['amount'])} from businesses it sold or closed")
    if "gain" in items:
        parts.append(f"about {money(items['gain']['amount'])} of gains outside its main business, such as sales of assets or investments")
    tax = items.get("tax_benefit")
    if tax:
        parts.append(f"a one-time tax benefit of {money(tax['amount'])}")
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    ni = m["net_income"]
    head = f"Last year's profit of {money(ni)} included" if ni >= 0 else f"Last year's loss of {money(ni)} was reduced by"
    rate = f"a normal {tax['rate'] * 100:.0f}% rate" if tax and tax["rate"] else None
    how = (f"use profit before that benefit, taxed at {rate}" if rate and len(parts) == 1 else
           f"leave these out and tax the rest at {rate}" if rate else f"leave {'it' if len(parts) == 1 else 'these'} out")
    return f"{head} {listed}. Scores, screener checks and the fair value {how}, which gives {profit(m['adj_net_income'])}."


def band(score):
    return None if score is None else "strong" if score >= 70 else "fair" if score >= 40 else "weak"


def peer_table(universe, metrics):
    """Ratios for every company, used to rank each company against its industry."""
    table = {}
    for sym, u in universe.items():
        m = metrics.get(sym)
        if not m or not u.get("mcap"):
            continue
        rev, ni, adj = m.get("revenue"), m.get("net_income"), m.get("adj_net_income")
        ev = None if is_financial(u) or m.get("net_cash") is None else u["mcap"] - m["net_cash"]
        table[sym] = {
            "industry": u["industry"],
            "sector": u["sector"],
            "ps": u["mcap"] / rev if rev and rev > 0 else None,
            "ev_sales": ev / rev if ev and ev > 0 and rev and rev > 0 else None,
            # Peers are compared on reported earnings. Only one-time gains are taken out, never one-time losses,
            # so peer multiples on adjusted earnings would lean high and raise every company's estimate.
            "pe": u["mcap"] / ni if ni and ni > 0 else None,
            "adj_pe": u["mcap"] / adj if adj and adj > 0 else None,
            "fcf_yield": m["fcf"] / u["mcap"] if m.get("fcf") is not None else None,
            "net_margin": m.get("net_margin"),
            "growth": m.get("revenue_growth"),
        }
    return table


def peers_for(sym, table, minimum=8):
    me = table.get(sym)
    if not me:
        return [], None
    same_ind = [s for s, r in table.items() if r["industry"] == me["industry"] and s != sym]
    if len(same_ind) >= minimum:
        return same_ind, me["industry"]
    same_sec = [s for s, r in table.items() if r["sector"] == me["sector"] and s != sym]
    return same_sec, me["sector"]


def _median(values):
    v = sorted(x for x in values if x is not None)
    if len(v) < 5:
        return None
    # Trim the extremes so a handful of outliers do not drag the benchmark.
    k = len(v) // 10
    return statistics.median(v[k : len(v) - k] if k else v)


def health(u, m, price, high52, peers, table, news_overall, insider):
    """Returns grouped bars. Each: key, label, value (display), score 1-100, note (plain English)."""
    mcap = u.get("mcap")
    peer_rows = [table[s] for s in peers]
    me = table.get(u["symbol"], {})
    groups = []

    fin = is_financial(u)

    def bar(key, label, value, score, note, context=False):
        # Context bars are neither good nor bad, so they are drawn in neutral gray.
        return {"key": key, "label": label, "value": value, "score": score,
                "band": "neutral" if context else band(score), "note": note, "context": context}

    def not_scored(key, label):
        return {**bar(key, label, "n/a", None, FINANCIAL_NOTE), "band": "neutral"}

    # Valuation
    v = []
    ps = me.get("ps")
    s = _pct_rank(ps, [r["ps"] for r in peer_rows], lower_is_better=True)
    v.append(bar("ps", "Price to sales", multiple(ps), s,
                 "Not enough data to compare." if s is None else
                 f"Cheaper than {s}% of similar companies for each dollar of sales." if s >= 50 else
                 f"Pricier than {100 - s}% of similar companies for each dollar of sales."))
    pe = me.get("adj_pe")
    adjusted = bool(m.get("one_time"))
    if m.get("adj_net_income") is not None and m["adj_net_income"] <= 0:
        v.append(bar("pe", "Price to earnings", "Loss", 5,
                     "Without last year's one-time items the company lost money, so there are no earnings to value."
                     if adjusted and m["net_income"] > 0 else "The company lost money last year, so there are no earnings to value."))
    else:
        s = _pct_rank(pe, [r["pe"] for r in peer_rows], lower_is_better=True)
        v.append(bar("pe", "Price to earnings", multiple(pe), s,
                     ("Not enough data to compare." if s is None else
                      f"You pay less per dollar of profit than for {s}% of peers." if s >= 50 else
                      f"You pay more per dollar of profit than for {100 - s}% of peers.")
                     + (" Uses profit without last year's one-time items." if adjusted and pe is not None else "")))
    fy = me.get("fcf_yield")
    v.append(not_scored("fcf_yield", "Free cash flow yield") if fin else bar("fcf_yield", "Free cash flow yield", pct(fy), None if fy is None else _lerp(fy, [(-0.05, 1), (0, 15), (0.05, 55), (0.10, 85), (0.15, 100)]),
                 "Cash left after running the business, as a share of the company's price. Higher is better." if fy is None or fy > 0 else
                 "The business used more cash than it brought in last year."))
    groups.append({"name": "Valuation", "hint": "Is the price reasonable for what you get?", "bars": v})

    # Profitability
    p = []
    nm = m.get("adj_net_margin")
    reported = lambda x: f" That leaves out last year's one-time items. With them it was {pct(x)}." if adjusted and x is not None else ""
    p.append(bar("net_margin", "Net profit margin", pct(nm), None if nm is None else _lerp(nm, [(-0.2, 1), (0, 20), (0.08, 55), (0.15, 80), (0.25, 100)]),
                 ("No revenue data." if nm is None else f"Keeps {cents(nm)} of profit from every dollar of sales." if nm >= 0 else
                  f"Loses {cents(nm)} on every dollar of sales.") + reported(m.get("net_margin"))))
    roe = m.get("adj_roe")
    p.append(bar("roe", "Return on equity", pct(roe), None if roe is None else _lerp(roe, [(-0.1, 1), (0, 15), (0.10, 55), (0.20, 85), (0.30, 100)]),
                 "Profit earned on the money shareholders have in the business. 15% or more is strong." + reported(m.get("roe")) if roe is not None else
                 "Can't be measured because shareholder equity is zero or negative."))
    fm = (m["fcf"] / m["revenue"]) if m.get("fcf") is not None and m.get("revenue") else None
    p.append(not_scored("fcf_margin", "Cash conversion") if fin else bar("fcf_margin", "Cash conversion", pct(fm), None if fm is None else _lerp(fm, [(-0.1, 1), (0, 20), (0.08, 60), (0.15, 85), (0.25, 100)]),
                 "Share of each sales dollar that turns into real cash. Profits backed by cash are more trustworthy."))
    groups.append({"name": "Profitability", "hint": "Does the business make money?", "bars": p})

    # Growth
    g = []
    gr = m.get("revenue_growth")
    g.append(bar("growth", "Sales growth (1 year)", pct(gr), None if gr is None else _lerp(gr, [(-0.2, 1), (-0.05, 25), (0, 40), (0.10, 70), (0.25, 100)]),
                 "Not enough history." if gr is None else f"Sales {'grew' if gr >= 0 else 'shrank'} {abs(gr) * 100:.1f}% in the last fiscal year."))
    n, up = m.get("revenue_years", 0), m.get("revenue_up_years", 0)
    cons = None if n < 3 else _lerp(up / (n - 1), [(0, 10), (0.5, 45), (1, 90)]) + (10 if (m.get("revenue_cagr") or 0) > 0.05 else 0)
    g.append(bar("consistency", "Growth consistency", f"{up} of {n - 1} yrs" if n >= 2 else "n/a", None if cons is None else min(cons, 100),
                 "Not enough history." if n < 3 else f"Sales rose in {up} of the last {n - 1} years. Steady growth is easier to trust."))
    py, yc = m.get("profitable_years", 0), m.get("years_checked", 0)
    g.append(bar("profit_record", "Profit track record", f"{py} of {yc} yrs" if yc else "n/a", None if not yc else _lerp(py / yc, [(0, 5), (0.5, 40), (0.67, 65), (1, 95)]),
                 "Not enough history." if not yc else f"Profitable in {py} of the last {yc} years"
                 + (f", not counting one-time items. With them it was {m['profitable_years_reported']}." if m.get("profitable_years_reported", py) != py else ".")))
    groups.append({"name": "Growth", "hint": "Is the business getting bigger and steadier?", "bars": g})

    # Balance sheet
    b = []
    # Missing cash and debt tags both read as zero, which is no data rather than a balance.
    nc = m.get("net_cash") if m.get("cash") or m.get("total_debt") else None
    ncr = nc / mcap if nc is not None and mcap else None
    if fin:
        b.append(not_scored("net_cash", "Net cash vs. price"))
    else:
        b.append(bar("net_cash", "Net cash vs. price", pct(ncr), None if ncr is None else _lerp(ncr, [(-0.6, 1), (-0.2, 30), (0, 55), (0.15, 80), (0.35, 100)]),
                     "Cash and debt are not reported." if nc is None else
                     "Cash minus all debt, as a share of the company's price." + (" It has more cash than debt." if nc > 0 else " It owes more than it holds in cash.")))
    de = m.get("lt_debt_to_equity")
    if m.get("equity") is not None and m["equity"] <= 0:
        b.append(bar("de", "Debt to equity", "Negative equity", 3, "The company owes more than it owns. That is a serious warning sign."))
    else:
        b.append(bar("de", "Debt to equity", multiple(de), None if de is None else _lerp(de, [(0, 100), (0.25, 85), (0.5, 65), (1, 40), (2, 10)]),
                     "Not enough data." if de is None else "No long-term debt." if de == 0 else
                     "Long-term debt compared with what shareholders own. Under 0.5 is comfortable."))
    cr = m.get("current_ratio")
    b.append(bar("current", "Short-term cushion", multiple(cr), None if cr is None else _lerp(cr, [(0.5, 5), (1, 35), (1.5, 65), (2, 85), (3, 100)]),
                 "Not reported (common for banks and insurers)." if cr is None else
                 "Assets it can turn into cash within a year, divided by bills due within a year. Above 1.5 is comfortable."))
    groups.append({"name": "Balance sheet", "hint": "Could it survive a rough patch?", "bars": b})

    # Shareholders
    sh = []
    d = m.get("dilution")
    sh.append(bar("dilution", "Share count change", "n/a" if d is None else f"{d * 100:+.1f}%", None if d is None else _lerp(-d, [(-0.15, 5), (-0.05, 40), (0, 75), (0.03, 90), (0.06, 100)]),
                  "Not enough history." if d is None else
                  "Fewer shares than a year ago, so each share owns more of the company." if d < -0.002 else
                  "About the same number of shares as a year ago." if d <= 0.01 else
                  "More shares than a year ago, so each share owns a smaller piece."))
    if insider:
        tier = insider["tier"]
        sc = 95 if tier == 1 else 80 if tier == 2 else 62
        sh.append(bar("insiders", "Insider buying", f"${insider['total_value'] / 1e6:.1f}M" if insider["total_value"] >= 1e5 else f"${insider['total_value']:,.0f}", sc,
                      f"Insiders bought shares on the open market in the last {insider['window']} days."))
    else:
        sh.append(bar("insiders", "Insider buying", "None", 40, "No open-market insider purchases in the scanned window. Common and not a bad sign by itself."))
    groups.append({"name": "Shareholders", "hint": "Are owners being treated well?", "bars": sh})

    # Market mood (shown for context, excluded from the overall health score)
    mk = []
    dd = (1 - price / high52) if high52 and price else None
    mk.append(bar("drawdown", "Below 52-week high", pct(dd, 0), None if dd is None else _lerp(dd, [(0, 10), (0.1, 35), (0.25, 65), (0.5, 90), (0.7, 100)]),
                  "Higher score means further below its 1-year high: more out of favor. That can mean opportunity or real trouble.", context=True))
    mk.append(bar("news", "News tone", news_overall["label"].title() if news_overall["label"] != "none" else "No news", news_overall["score"],
                  news_overall["text"], context=True))
    groups.append({"name": "Market mood", "hint": "How the market feels right now. Shown for context, not counted in health.", "bars": mk})

    scores = [x["score"] for grp in groups for x in grp["bars"] if x["score"] is not None and not x["context"]]
    return groups, (round(sum(scores) / len(scores)) if scores else None)


def fair_value(u, m, price, peers, table):
    """Peer multiples plus a simple cash-flow model. Returns methods, range and verdict, or None when no
    reliable estimate exists. Earnings leave out last year's one-time items."""
    if not price or price <= 0:
        return None
    # The share count behind the market cap, so estimates line up with the peer multiples and the P/S bar.
    shares = u["mcap"] / price if u.get("mcap") else m.get("shares_out")
    if not shares or shares <= 0:
        return None
    fin = is_financial(u)
    peer_rows = [table[s] for s in peers]
    rev, ni = m.get("revenue"), m.get("adj_net_income")
    profitable = ni is not None and ni > 0
    nc = m.get("net_cash") or 0
    methods = []

    # A sales multiple assumes the company can earn what its peers earn on each dollar of sales, which says
    # little about one that loses money, so that company is valued on its cash flow alone.
    if rev and rev > 0 and profitable and fin:
        med_ps = _median([r["ps"] for r in peer_rows])
        if med_ps:
            methods.append({"name": "Peer price to sales", "value": med_ps * rev / shares,
                            "note": f"Similar companies trade at {med_ps:.1f}x sales."})
    elif rev and rev > 0 and profitable:
        # Enterprise value, so a peer's debt is not priced in as if it were sales. This company's own net
        # debt is then subtracted (or net cash added) to get back to what the shares are worth.
        med_ev = _median([r["ev_sales"] for r in peer_rows])
        value = (med_ev * rev + nc) / shares if med_ev else 0
        if value > 0:
            methods.append({"name": "Peer value to sales", "value": value,
                            "note": f"Similar companies are valued at {med_ev:.1f}x sales, counting their debt and cash."
                                    + (f" This company's net cash of {money(nc)} is added." if nc > 0 else
                                       f" This company's net debt of {money(nc)} is subtracted." if nc < 0 else "")})
    med_pe = _median([r["pe"] for r in peer_rows])
    if med_pe and profitable:
        methods.append({"name": "Peer price to earnings", "value": med_pe * ni / shares,
                        "note": f"Similar companies trade at {med_pe:.1f}x earnings."
                                + (f" Uses last year's profit without one-time items: {money(ni)} instead of {money(m['net_income'])}."
                                   if m.get("one_time") else "")})

    fcfs = [x for x in (m.get("fcf_history") or []) if x is not None]
    base = sum(fcfs) / len(fcfs) if fcfs else None
    if base and base > 0 and not fin:
        g = max(0.0, min(0.12, m.get("revenue_cagr") or m.get("revenue_growth") or 0.0))
        r, tg = 0.10, 0.025
        pv, cf = 0.0, base
        for year in range(1, 6):
            cf *= 1 + g
            pv += cf / (1 + r) ** year
        terminal = cf * (1 + tg) / (r - tg) / (1 + r) ** 5
        value = (pv + terminal + nc) / shares
        if value > 0:
            methods.append({"name": "Cash-flow model", "value": value,
                            "note": f"Average free cash flow of ${base / 1e6:,.0f}M growing {g * 100:.0f}% a year for 5 years, "
                                    f"then 2.5%, discounted at 10%, " + ("plus net cash." if nc >= 0 else "minus net debt.")})

    if not methods:
        return None
    vals = sorted(x["value"] for x in methods)
    mid = statistics.median(vals)
    # A value more than 50% away from the price needs two methods behind it, so one method can't stretch the
    # midpoint past that. With three methods the median always has a second method on its side.
    top = max(price * 1.5, vals[-2] if len(vals) > 1 else 0)
    bottom = min(price * 0.5, vals[1] if len(vals) > 1 else math.inf)
    held = "above" if mid > top else "below" if mid < bottom else None
    mid = min(max(mid, bottom), top)
    limit = None
    if held:
        far = f"more than 50% {held} the price"
        alone = vals[0] <= price * 1.5 if held == "above" else vals[-1] >= price * 0.5
        if len(vals) == 1:
            limit = f"Only one method could be used, and a value {far} needs a second method to agree, so the midpoint stops at 50% {held}."
        elif alone:
            limit = f"Only one of the two methods puts the value {far}. A gap that large needs both to agree, so the midpoint stops at 50% {held}."
        else:
            limit = f"Both methods put the value {far}, so the midpoint is the {'lower' if held == 'above' else 'higher'} of the two rather than their average."
    if len(vals) == 1:
        low, high = vals[0] * 0.85, vals[0] * 1.15
    elif len(vals) == 2:
        # Span both estimates, so the range never excludes a method listed beneath it.
        low, high = vals
    else:
        low, high = max(vals[0], mid * 0.75), min(vals[-1], mid * 1.25)
    low, high = min(low, mid * 0.9), max(high, mid * 1.1)
    verdict = "undervalued" if price < low else "overvalued" if price > high else "fair"

    spread = vals[-1] / vals[0]
    apart = f"within {max(5, math.ceil(round((spread - 1) * 20, 6)) * 5)}% of each other"
    of = "the two methods that suit a financial company" if fin else "the three methods"
    if len(vals) == 1 and not profitable:
        confidence, note = "low", (("Without last year's one-time items the company lost money"
                                    if (m.get("net_income") or 0) > 0 else "The company lost money last year")
                                   + ", so only the cash-flow model applies. Treat this range loosely.")
    elif len(vals) == 1:
        confidence, note = "low", f"Only one of {of} could be used, so treat this range loosely."
    elif spread > 2:
        confidence, note = "low", (f"The methods disagree widely (lowest {dollars(vals[0])}, highest {dollars(vals[-1])}), "
                                   "so treat this range loosely.")
    elif len(vals) == 3:
        confidence, note = "higher" if spread <= 1.5 else "moderate", f"All three methods were available and land {apart}."
    elif fin:
        confidence, note = "moderate", f"Both methods that suit a financial company were available and land {apart}."
    else:
        confidence, note = "moderate", f"Two of the three methods could be used, and they land {apart}."

    for x in methods:
        x["value"] = round(x["value"], 2)
    return {
        "methods": methods,
        "low": round(low, 2),
        "mid": round(mid, 2),
        "high": round(high, 2),
        "upside": mid / price - 1,
        "verdict": verdict,
        "confidence": confidence,
        "confidence_note": note,
        "limit_note": limit,
    }
