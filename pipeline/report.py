"""Single-ticker report: 1-100 health bars, a fair-value range and a verdict."""
import bisect
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
# Nasdaq lists some REITs under these instead of "Real Estate Investment Trusts" (Invitation Homes and Terreno Realty
# under Real Estate, Sunstone Hotel Investors under Hotels/Resorts).
PROPERTY_INDUSTRIES = {"Real Estate", "Building operators", "Hotels/Resorts"}


def normal_tax_rate(u, company=None):
    """A REIT pays no corporate income tax on the profit it pays out, so its normal rate is zero. A utility's tax is
    shaped every year by regulators and energy tax credits (PG&E paid less than nothing in each of 2022 to 2025),
    so a benefit on its profit is normal (None). `company` is the fundamentals record, which identifies a REIT that
    Nasdaq doesn't label as one."""
    ind = u.get("industry")
    if ind == "Real Estate Investment Trusts" or ind in PROPERTY_INDUSTRIES and _untaxed(company):
        return 0.0
    return None if ind in UTILITIES else NORMAL_TAX


def _untaxed(company):
    """Whether a company paid next to no income tax (under 5% of pretax income) in each of at least three profitable
    years. For a property owner that means a REIT. On September 2026 data it picked out seven REITs Nasdaq lists
    under other industries (Curbline, Gladstone Commercial, Getty Realty, Innovative Industrial Properties, Invitation
    Homes, Sunstone Hotel Investors, Terreno Realty) and one small company that is not one (TDH Holdings)."""
    ok = []
    for s in ((company or {}).get("annual") or {}).values():
        ni = s.get("net_income")
        base = s.get("pretax") if s.get("pretax") is not None else ni
        if ni is not None and ni > 0 and base and base > 0:
            ok.append(abs(s.get("income_tax") or 0) <= 0.05 * base)
    return len(ok) >= 3 and all(ok)


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
    """Plain-English note on the one-time items left out of last year's profit, or None when there were none.

    Gains taken out and charges added back are named separately: "Last year's profit of $57M included a $248M gain
    on selling a business or assets, and was reduced by a $1.6B write-down of goodwill (...). Scores, screener checks
    and the fair value use profit without them, which gives $1.1B." fundamentals.KINDS lists what each kind covers.
    Where minority holders own part of the business, the note says that only the company's own share counts.
    """
    items = m.get("one_time") or []
    if not items:
        return None
    phrases = {
        "goodwill_impairment": "a {} write-down of goodwill (the premium it paid for past acquisitions)",
        "impairment": "{} of write-downs of other assets",
        "sale_gain": "a {} gain on selling a business or assets",
        "sale_loss": "a {} loss on selling a business or assets",
        "securities_gain": "a {} gain on investments",
        "securities_loss": "a {} loss on investments",
        "debt_gain": "a {} gain on paying off debt for less than it owed",
        "debt_loss": "a {} loss on paying off debt early",
        "gain": "about {} of gains outside its main business, such as sales of assets or investments",
        "discontinued": "{} of profit from businesses it sold or closed",
        "discontinued_loss": "{} of losses from businesses it sold or closed",
        "tax_benefit": "a tax bill {} below normal",
        "tax_charge": "a tax bill {} above normal",
    }
    listed = lambda parts: parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    gains, charges, tax = [], [], None
    for i in sorted(items, key=lambda i: i["kind"] == "gain"):  # its long phrase reads best last
        text = phrases.get(i["kind"], "{} of one-time items")
        if i.get("reported"):  # only part of the item counts as one-time
            noun = text[2:] if text.startswith("a ") else text[6:] if text.startswith("about ") else text
            phrase = f"{money(i['amount'])} of its " + noun.format(money(i["reported"]))
        else:
            phrase = text.format(money(i["amount"]))
        if i["kind"] in ("tax_benefit", "tax_charge"):
            tax = i
            if i.get("cause") == "valuation_allowance":
                phrase += (", mostly from counting past losses as future tax savings" if i["kind"] == "tax_benefit"
                           else ", mostly from writing off tax savings it no longer expects to use")
        # Older items carry no effect, and they were all gains taken out.
        (charges if i.get("effect", -i["amount"]) > 0 else gains).append(phrase)
    ni = m["net_income"]
    if ni >= 0:
        clauses = ([f"included {listed(gains)}"] if gains else []) + ([f"was reduced by {listed(charges)}"] if charges else [])
        head = f"Last year's profit of {money(ni)}"
    else:
        clauses = ([f"included {listed(charges)}"] if charges else []) + ([f"was made smaller by {listed(gains)}"] if gains else [])
        head = f"Last year's loss of {money(ni)}"
    how = ""
    if tax and (tax.get("adj_pretax") or 0) > 0:
        rate = tax["normal_tax"] / tax["adj_pretax"]
        how = (f", with tax at a normal {rate * 100:.0f}% of profit" if 0.05 <= rate <= 0.4
               else ", with tax about as low as in its other years" if rate < 0.05
               else ", with tax about as high as in its other years")
    them = "it" if len(items) == 1 else "them"
    # Where minority holders own part of the business, only the company's own share of an item moves its profit.
    share = min((i["share"] for i in items if i.get("share") is not None), default=None)
    what = "that item" if len(items) == 1 else "the items other than tax" if tax else "these items"
    owned = (f" Minority holders own part of the business, so only its {share * 100:.0f}% share of {what} counts."
             if share is not None else "")
    return (f"{head} {', and '.join(clauses)}.{owned} Scores, screener checks and the fair value use profit without "
            f"{them}{how}, which gives {profit(m['adj_net_income'])}.")


def band(score):
    return None if score is None else "strong" if score >= 70 else "fair" if score >= 40 else "weak"


# Peers. Each company is compared with the PEER_COUNT companies in its sector whose own numbers are closest on
# what drives a sales or earnings multiple: operating and net margin, free cash flow margin, sales growth over one
# and three years, size (sales) and sales per dollar of assets, with a small preference for its own Nasdaq
# industry (INDUSTRY_GAP). Nothing that depends on the share price is used, so a company's own valuation can't
# pull in expensive or cheap look-alikes. Each business counts once: the company's other share classes are left
# out, and so is a second share class of any peer. Tested leave-one-out on September 2026 data (every company's
# multiple predicted from its peers' median, the company itself left out), against Nasdaq's industry labels:
#   value to sales, profitable companies (1,701): typical miss a factor of 1.45 instead of 1.74 (median absolute
#     log error 0.369 vs 0.552), within 25% for 37% of companies instead of 27%
#   price to earnings (2,051): factor 1.51 instead of 1.60 (0.412 vs 0.471), within 25% for 33% instead of 31%
# Value to sales improved in every sector. Price to earnings got worse in four, none by more than chance could
# explain at 95%: energy (73 companies, 0.49 vs 0.44), telecommunications (32, 0.83 vs 0.67), utilities (85, 0.20
# vs 0.19; midstream pipelines listed as gas distribution are matched with electric utilities) and basic materials
# (15, 0.36 vs 0.35). SEC SIC codes in place of Nasdaq's labels, regression-based multiples and nearest matches
# across the whole market were all less accurate than this.
# Sales per dollar of assets was the figure that helped most: it separates low-margin, fast-turnover businesses
# (lead generation, staffing, distribution) from software companies with similar margins.
PEER_COUNT = 10
PEER_FEATURES = ("op_margin", "net_margin", "fcf_margin", "growth", "growth_3y", "size", "turnover")
# Added to the distance (a mean squared gap in percentile rank) of a candidate from another Nasdaq industry. It
# keeps homebuilders with homebuilders and water utilities with water utilities when their numbers are close, yet
# lets a mislabeled company find its real look-alikes. Chosen on half the companies (split by CIK) and confirmed
# on the other half. On the final September 2026 data it lowered the price to earnings error by 0.008 (95% interval
# -0.009 to +0.022) and the value to sales error by 0.007 (-0.006 to +0.024), so it costs no accuracy, and it raised
# the share of peers from the company's own industry from 28% to 42%. Not used inside SEC groups (below), where it
# made price to sales worse.
INDUSTRY_GAP = 0.01
# Earnings multiple the peers are compared on: "adj_pe" (last year's profit without one-time items, the same basis
# as the company's own estimate) or "pe" (as reported). On September 2026 data, with one-time charges added back as
# well as gains taken out, adjusted peers predicted each company's price more accurately (median absolute log error
# 0.420 vs 0.435 over 1,982 companies, difference -0.016 with a 95% interval of -0.031 to -0.002; the same in each
# half of the companies split by CIK) and with less bias (the median estimate 2.3% below the market, against 3.0%).
# Valuing both the company and its peers on adjusted rather than reported profit was more accurate still for the
# 566 profitable companies whose own profit was adjusted (0.491 vs 0.569, interval -0.123 to -0.030).
PEER_PE = "adj_pe"
# A peer missing a figure the company has counts as this far apart on it (squared gap in percentile rank, 0.3).
_MISSING_GAP = 0.09
# Nasdaq's catch-all groups hold unrelated companies, so they are matched against the whole market outside finance.
_CATCH_ALL = {"Other", "Miscellaneous"}
# Nasdaq's Finance sector mixes banks, insurers, lenders and brokers, whose sales mean different things, so a
# financial company is matched within its SEC industry group (the first two digits of its SIC code) when that
# group has more than _MIN_GROUP members. For price to sales of profitable financial companies whose sales pass
# sales_doubtful() (265), the median absolute log error was 0.34 to 0.38 for 6 to 15 peers, against 0.39 to 0.43
# matching across the whole sector and 0.79 with Nasdaq's industry labels. Four in five of group 61 are SIC 6199,
# "finance services" (card issuers, crypto miners and exchanges, neobanks), hence its long name.
FINANCIAL_GROUPS = {"60": "banks", "61": "lenders and other financial services companies",
                    "62": "brokers, exchanges and asset managers", "63": "insurers",
                    "64": "insurance brokers", "65": "real estate companies", "67": "holding and investment companies"}
_MIN_GROUP = 20
_SECTOR_WORDS = {"Industrials": "industrial companies", "Utilities": "utilities", "Finance": "financial companies"}


class PeerTable(dict):
    """{symbol: row}, plus an index of which companies can be compared with which."""
    index = None


def sales_doubtful(m):
    """True when the sales figure read from the SEC data can't be the whole of the company's sales: operating
    profit, or profit without one-time items, came out larger than sales. In September 2026 data that flagged 90
    companies, 70 of them financial (a bank's fee income alone read as its sales, for example), with few just below
    the line (6 between 80% and 100%). Their sales multiples and margins would be far off, so the
    report neither shows nor uses them."""
    rev = m.get("revenue")
    return bool(rev and rev > 0 and ((m.get("op_margin") or 0) > 1 or (m.get("adj_net_margin") or 0) > 1))


DOUBT_NOTE = ("Its sales figure in the SEC data looks incomplete, since profit came out larger than sales, so it isn't "
              "valued on sales.")
DOUBT_BAR_NOTE = ("Not measured. The sales figure in the company's SEC data looks incomplete, since profit came out "
                  "larger than sales.")


def _fin_group(code):
    """Peer group key for a financial company's SIC code, or None."""
    return code[:2] if code[:2] in FINANCIAL_GROUPS else None


def peer_table(universe, metrics, sic=None):
    """Ratios and matching figures for every company. `sic` is {cik: SIC code} for financial companies."""
    table = PeerTable()
    sic = sic or {}
    for sym, u in universe.items():
        m = metrics.get(sym)
        if not m or not u.get("mcap"):
            continue
        rev, ni, adj = m.get("revenue"), m.get("net_income"), m.get("adj_net_income")
        ev = None if is_financial(u) or m.get("net_cash") is None else u["mcap"] - m["net_cash"]
        doubt = sales_doubtful(m)
        has_rev = bool(rev and rev > 0) and not doubt
        assets = m.get("total_assets")
        code = str(sic.get(u.get("cik")) or "")
        table[sym] = {
            "name": u.get("name"),
            "cik": u.get("cik"),
            "industry": u["industry"],
            "sector": u["sector"],
            "group": _fin_group(code) if is_financial(u) else None,
            "sales_doubt": doubt,
            "ps": u["mcap"] / rev if has_rev else None,
            "ev_sales": ev / rev if ev and ev > 0 and has_rev else None,
            "pe": u["mcap"] / ni if ni and ni > 0 else None,
            "adj_pe": u["mcap"] / adj if adj and adj > 0 else None,
            "fcf_yield": m["fcf"] / u["mcap"] if m.get("fcf") is not None else None,
            "net_margin": m.get("net_margin"),
            "growth": m.get("revenue_growth"),
            # A sales figure that can't be right leaves nothing to match on, so the company falls back to its
            # Nasdaq industry label and is never another company's peer.
            "features": {
                "op_margin": m.get("op_margin") if has_rev else None,
                "net_margin": m.get("adj_net_margin") if has_rev else None,
                "fcf_margin": m["fcf"] / rev if has_rev and m.get("fcf") is not None else None,
                "growth": m.get("revenue_growth") if has_rev else None,
                "growth_3y": m.get("revenue_cagr") if has_rev else None,
                "size": math.log(rev) if has_rev else None,
                "turnover": rev / assets if has_rev and assets and assets > 0 else None,
            },
        }
    # Each figure becomes a percentile rank across all companies, so margins, growth and size weigh the same
    # and a few extreme values can't dominate the distance.
    for f in PEER_FEATURES:
        vals = sorted(r["features"][f] for r in table.values() if r["features"][f] is not None)
        for r in table.values():
            v = r["features"][f]
            r.setdefault("rank", []).append(
                None if v is None else (bisect.bisect_left(vals, v) + bisect.bisect_right(vals, v)) / 2 / len(vals))
    return table


def _index(table):
    idx = getattr(table, "index", None)
    if idx is None:
        idx = {"sector": {}, "group": {}, "industry": {},
               # The whole market, for companies in Nasdaq's catch-all sectors. Financial companies are left out:
               # their sales mean something else, and no value to sales is worked out for them.
               "market": [s for s, r in table.items() if r["sector"] != FINANCIAL_SECTOR]}
        for s, r in table.items():
            idx["sector"].setdefault(r["sector"], []).append(s)
            idx["industry"].setdefault(r["industry"], []).append(s)
            if r.get("group"):
                idx["group"].setdefault(r["group"], []).append(s)
        if isinstance(table, PeerTable):
            table.index = idx
    return idx


def _pool(me, idx):
    """(candidate symbols, how they were chosen) for one company."""
    g = me.get("group")
    if g and len(idx["group"].get(g, ())) > _MIN_GROUP:
        return idx["group"][g], ("group", g)
    if me["sector"] in _CATCH_ALL:
        return idx["market"], ("market", None)
    return idx["sector"].get(me["sector"], []), ("sector", me["sector"])


def _one_per_company(symbols, table, own_cik):
    """The symbols in order, skipping the company's own share classes and any second class of a company already
    listed, so no business counts twice."""
    seen, out = {own_cik}, []
    for s in symbols:
        c = table[s]["cik"]
        if c in seen:
            continue
        if c is not None:
            seen.add(c)
        out.append(s)
    return out


def _label_peers(me, table, idx):
    same = _one_per_company(idx["industry"].get(me["industry"], []), table, me["cik"])
    if len(same) >= 8:
        return same, me["industry"]
    return _one_per_company(idx["sector"].get(me["sector"], []), table, me["cik"]), me["sector"]


def peers_for(sym, table, count=PEER_COUNT):
    """(peer symbols, closest first; the group they were drawn from). A profitable company is compared only with
    profitable companies, so every peer has an earnings multiple. A company without sales has nothing to match
    on, so it falls back to Nasdaq's industry label (or its sector when fewer than 8 companies share the label)."""
    me = table.get(sym)
    if not me:
        return [], None
    idx = _index(table)
    mine = me.get("rank") or []
    if me["features"]["size"] is None:
        return _label_peers(me, table, idx)
    pool, (kind, key) = _pool(me, idx)
    # Profitable as fair_value() counts it: without last year's one-time items.
    profitable = me.get("adj_pe") is not None
    scored = []
    for s in pool:
        r = table[s]
        if r["cik"] == me["cik"] or r["features"]["size"] is None or (profitable and r.get(PEER_PE) is None):
            continue
        d = n = 0.0
        for a, b in zip(mine, r["rank"]):
            if a is not None:
                d += (a - b) ** 2 if b is not None else _MISSING_GAP
                n += 1
        dist = d / n if n else 1.0
        if r["industry"] != me["industry"] and kind != "group":
            dist += INDUSTRY_GAP
        scored.append((dist, s))
    scored.sort()
    group = _group_name(key).capitalize() if kind == "group" else "All US stocks" if kind == "market" else key
    return _one_per_company([s for _, s in scored], table, me["cik"])[:count], group


def _group_name(key):
    return FINANCIAL_GROUPS[key]


def peer_basis(sym, table, peers):
    """One plain sentence on how the peers were chosen, or None without peers."""
    me = table.get(sym)
    if not me or not peers:
        return None
    idx = _index(table)
    if me["features"]["size"] is None:
        _, label = _label_peers(me, table, idx)
        why = ("Its sales figure in the SEC data looks incomplete, so it can't be matched on its numbers."
               if me.get("sales_doubt") else "Without sales figures, the company can't be matched on its numbers.")
        return f"Compared with the {len(peers)} other companies Nasdaq lists under {label}. {why}"
    _, (kind, key) = _pool(me, idx)
    who = (_group_name(key) if kind == "group" else "US-listed companies outside finance" if kind == "market"
           else _SECTOR_WORDS.get(key, f"{key.lower()} companies"))
    f = dict(zip(PEER_FEATURES, me["rank"]))
    traits = [t for t, keys in (("profit margins", ("op_margin", "net_margin")), ("cash flow", ("fcf_margin",)),
                                ("sales growth", ("growth", "growth_3y")), ("size", ("size",)),
                                ("sales per dollar of assets", ("turnover",))) if any(f[k] is not None for k in keys)]
    listed = traits[0] if len(traits) == 1 else ", ".join(traits[:-1]) + " and " + traits[-1]
    profitable = "profitable " if me.get("adj_pe") is not None else ""
    prefer = ", with a preference for its own industry" if INDUSTRY_GAP and kind != "group" else ""
    return f"Compared with the {len(peers)} {profitable}{who} closest to it in {listed}{prefer}."


def peer_list(sym, table, peers, limit=PEER_COUNT):
    """The peers as shown on the report: ticker, name and the multiples their medians come from ("ps" too for a
    non-financial company, whose health check ranks price to sales). Empty for a company matched only by its
    industry label, whose peer list is the whole label rather than a closest few."""
    me = table.get(sym)
    if not me or me["features"]["size"] is None:
        return []
    key = _sales_key(me)
    return [{"symbol": s, "name": table[s]["name"], "sales": table[s][key], "pe": table[s].get(PEER_PE),
             **({} if key == "ps" else {"ps": table[s]["ps"]})}
            for s in peers[:limit]]


def _sales_key(me):
    # The sales multiple fair_value() uses: price to sales for financial companies, value to sales otherwise.
    return "ps" if me["sector"] == FINANCIAL_SECTOR else "ev_sales"


def peer_multiples(sym, table, peers):
    """Column labels, the company's own multiples and the peer medians fair_value() uses, for the peer list on
    the report, or None without a list. A non-financial company also gets a price to sales column, since its
    health check ranks price to sales while its fair value uses value to sales."""
    me = table.get(sym)
    if not me or me["features"]["size"] is None or not peers:
        return None
    key = _sales_key(me)
    fin = key == "ps"
    profitable = me.get("adj_pe") is not None
    rows = [table[s] for s in peers]
    note = []
    if not fin:
        note.append("Price to sales, which the health check ranks, looks at the share price alone. Value to sales, "
                    "which the fair value uses, also counts each company's debt and cash.")
    if PEER_PE == "adj_pe":
        note.append("Earnings leave out last year's one-time items.")
    elif me.get("pe") != me.get("adj_pe"):
        note.append("Peers' earnings are as reported. This company's leave out last year's one-time items, as its "
                    "fair value does.")
    if profitable:
        note.append("The fair value uses the median of the " + ("price to sales" if fin else "value to sales")
                    + " and price to earnings columns.")
    else:
        note.append("These are the companies the price to sales bar in the health check compares it with. The fair "
                    "value doesn't use them, since the company lost money.")
    gaps = []
    if not fin and any(r["ev_sales"] is None for r in rows + [me]):
        gaps.append("in value to sales it means the company holds more cash than its market value")
    if me.get("adj_pe") is None or any(r.get(PEER_PE) is None for r in rows):
        gaps.append("in price to earnings it means a loss")
    if gaps:
        note.append("Where a cell says n/a, " + " and ".join(gaps) + ".")
    return {
        "median": {"sales": _median([r[key] for r in rows]), "pe": _median([r.get(PEER_PE) for r in rows]),
                   **({} if fin else {"ps": _median([r["ps"] for r in rows])})},
        "sales_label": "Price to sales" if fin else "Value to sales",
        "pe_label": "Price to earnings",
        **({} if fin else {"ps_label": "Price to sales"}),
        "note": " ".join(note),
        "company": {"sales": me[key], "pe": me.get("adj_pe"), **({} if fin else {"ps": me["ps"]})},
    }


def _median(values):
    """Median of at least 5 values, else None. (Trimming the same number of values from each end, as this once
    did, never moves a median.)"""
    v = [x for x in values if x is not None]
    return statistics.median(v) if len(v) >= 5 else None


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

    def not_scored(key, label, note=FINANCIAL_NOTE):
        return {**bar(key, label, "n/a", None, note), "band": "neutral"}

    # A sales figure that can't be right would put every sales-based bar far off, so those are left out.
    doubt = sales_doubtful(m)
    no_sales = lambda key, label: not_scored(key, label, DOUBT_BAR_NOTE)

    # Valuation
    v = []
    ps = me.get("ps")
    s = _pct_rank(ps, [r["ps"] for r in peer_rows], lower_is_better=True)
    v.append(no_sales("ps", "Price to sales") if doubt else bar("ps", "Price to sales", multiple(ps), s,
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
        s = _pct_rank(pe, [r[PEER_PE] for r in peer_rows], lower_is_better=True)
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
    p.append(no_sales("net_margin", "Net profit margin") if doubt else bar("net_margin", "Net profit margin", pct(nm), None if nm is None else _lerp(nm, [(-0.2, 1), (0, 20), (0.08, 55), (0.15, 80), (0.25, 100)]),
                 ("No revenue data." if nm is None else f"Keeps {cents(nm)} of profit from every dollar of sales." if nm >= 0 else
                  f"Loses {cents(nm)} on every dollar of sales.") + reported(m.get("net_margin"))))
    roe = m.get("adj_roe")
    p.append(bar("roe", "Return on equity", pct(roe), None if roe is None else _lerp(roe, [(-0.1, 1), (0, 15), (0.10, 55), (0.20, 85), (0.30, 100)]),
                 "Profit earned on the money shareholders have in the business. 15% or more is strong." + reported(m.get("roe")) if roe is not None else
                 "Can't be measured because shareholder equity is zero or negative."))
    fm = (m["fcf"] / m["revenue"]) if m.get("fcf") is not None and m.get("revenue") else None
    p.append(not_scored("fcf_margin", "Cash conversion") if fin else no_sales("fcf_margin", "Cash conversion") if doubt else bar("fcf_margin", "Cash conversion", pct(fm), None if fm is None else _lerp(fm, [(-0.1, 1), (0, 20), (0.08, 60), (0.15, 85), (0.25, 100)]),
                 "Share of each sales dollar that turns into real cash. Profits backed by cash are more trustworthy."))
    groups.append({"name": "Profitability", "hint": "Does the business make money?", "bars": p})

    # Growth
    g = []
    gr = m.get("revenue_growth")
    g.append(no_sales("growth", "Sales growth (1 year)") if doubt else bar("growth", "Sales growth (1 year)", pct(gr), None if gr is None else _lerp(gr, [(-0.2, 1), (-0.05, 25), (0, 40), (0.10, 70), (0.25, 100)]),
                 "Not enough history." if gr is None else f"Sales {'grew' if gr >= 0 else 'shrank'} {abs(gr) * 100:.1f}% in the last fiscal year."))
    n, up = m.get("revenue_years", 0), m.get("revenue_up_years", 0)
    cons = None if n < 3 else _lerp(up / (n - 1), [(0, 10), (0.5, 45), (1, 90)]) + (10 if (m.get("revenue_cagr") or 0) > 0.05 else 0)
    g.append(no_sales("consistency", "Growth consistency") if doubt else bar("consistency", "Growth consistency", f"{up} of {n - 1} yrs" if n >= 2 else "n/a", None if cons is None else min(cons, 100),
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
    doubt = sales_doubtful(m)

    # A sales multiple assumes the company can earn what its peers earn on each dollar of sales, which says
    # little about one that loses money, so that company is valued on its cash flow alone. Nor is it used when
    # the sales figure itself can't be right.
    if doubt:
        pass
    elif rev and rev > 0 and profitable and fin:
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
    med_pe = _median([r[PEER_PE] for r in peer_rows])
    if med_pe and profitable:
        methods.append({"name": "Peer price to earnings", "value": med_pe * ni / shares,
                        "note": f"Similar companies trade at {med_pe:.1f}x earnings."
                                + (f" Uses last year's profit without one-time items: {money(ni)} instead of {profit(m['net_income'])}."
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
    # A value more than 50% away from the price needs two independent estimates behind it, so one can't stretch
    # the midpoint past that. The peer estimates count as one: they come from the same similar companies, matched
    # on profit margins, so their sales and earnings multiples mostly agree by construction (on September 2026
    # data they landed within 1.5x of each other for 57% of companies, against 31% with Nasdaq's industry peers,
    # while the share of each within 1.5x of the cash-flow model rose only from 37% to 39% and from 33% to 35%).
    # So the midpoint passes 50% only when the cash-flow model and the peer estimates (their geometric mean) are
    # both past it. Then, as before, three methods keep their median, which always has a second method on its
    # side, and two keep the nearer one. A company valued on peer multiples alone (every financial company, and
    # others without positive average cash flow) therefore stays within 50% of the price.
    peer_vals = [x["value"] for x in methods if x["name"].startswith("Peer")]
    other = [x["value"] for x in methods if not x["name"].startswith("Peer")]
    kinds = sorted(([math.exp(statistics.fmean(math.log(v) for v in peer_vals))] if peer_vals else []) + other)
    top = max(price * 1.5, vals[-2]) if len(kinds) > 1 and kinds[0] > price * 1.5 else price * 1.5
    bottom = min(price * 0.5, vals[1]) if len(kinds) > 1 and kinds[-1] < price * 0.5 else price * 0.5
    held = "above" if mid > top else "below" if mid < bottom else None
    mid = min(max(mid, bottom), top)
    limit = None
    if held:
        far = f"more than 50% {held} the price"
        stop = f"so the midpoint stops at 50% {held}"
        past = (lambda v: v > price * 1.5) if held == "above" else (lambda v: v < price * 0.5)
        if len(vals) == 1:
            limit = f"Only one method could be used, and a value {far} needs a second method to agree, {stop}."
        elif len(kinds) == 1:
            limit = (f"Both estimates come from similar companies' multiples, and a value {far} needs an independent "
                     f"method to agree, {stop}.")
        elif len(vals) == 2 and all(past(v) for v in vals):
            limit = (f"Both methods put the value {far}, so the midpoint is the {'lower' if held == 'above' else 'higher'} "
                     "of the two rather than their average.")
        elif len(vals) == 2:
            limit = f"Only one of the two methods puts the value {far}. A gap that large needs both to agree, {stop}."
        elif past(other[0]):
            limit = (f"Only the cash-flow model puts the value {far}, not the similar companies' multiples taken "
                     f"together. A gap that large needs both to agree, {stop}.")
        else:
            limit = (f"Only the similar companies' multiples put the value {far}, not the cash-flow model. They "
                     f"count as one estimate, since they come from the same companies, and a gap that large needs "
                     f"a second one to agree, {stop}.")
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
        confidence, note = "moderate", (f"Both methods that suit a financial company were available and land {apart}, "
                                        "though both rest on the same similar companies.")
    elif len(peer_vals) == 2:
        confidence, note = "moderate", (f"Two of the three methods could be used, and they land {apart}, though both "
                                        "rest on the same similar companies.")
    else:
        confidence, note = "moderate", f"Two of the three methods could be used, and they land {apart}."
    if doubt and profitable:
        note = f"{DOUBT_NOTE} {note}"

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
