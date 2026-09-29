"""Builds every JSON file the site reads.

    python -m pipeline.build                 # full run (about an hour, mostly polite rate limits)
    python -m pipeline.build --limit 150     # quick sample for local testing
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import shutil
import time

from . import fundamentals, insiders, market, net, news, report, screener, universe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "data")
CACHE = os.path.join(ROOT, ".cache")

# SIC codes (the SEC's industry codes) of financial companies, which pick their peer group, and of property companies,
# where code 6798 marks a REIT. A company's code rarely changes, so each is looked up once (one request) and rechecked
# every SIC_REFRESH_DAYS, a few per run.
SIC_CACHE = os.path.join(CACHE, "sic.json")
SIC_REFRESH_DAYS = 180
SIC_REFRESH_PER_RUN = 40
SIC_MINUTES = 6
# A bank's own figures in its report's fundamentals (see main).
BANK_FIELDS = ("net_interest_income", "noninterest_income", "noninterest_expense", "provision", "efficiency_ratio",
               "adj_net_income_common", "adj_eps", "tangible_equity", "tce_ratio", "adj_roa", "adj_roe_common",
               "adj_rotce", "returns_as_of", "tce_unread")
# A report says when its newest quarter of revenue ended longer ago than this (quarters_stale), rather than presenting
# years-old quarters as the latest: most companies file a 10-Q within 45 days of a quarter's end and a 10-K within 90
# days of a year's, so nine months means at least two quarterly reports missed.
STALE_QUARTER_DAYS = 270


def _round(o):
    if isinstance(o, float):
        if o != o or o in (float("inf"), float("-inf")):
            return None
        return round(o, 4) if abs(o) < 1000 else round(o)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v) for v in o]
    return o


def write(path, payload):
    full = os.path.join(OUT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_round(payload), fh, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, full)  # a run killed by the step timeout must not leave a truncated file to deploy


def market_today():
    """The New York trading day this run belongs to.

    Six hours are taken off first, so an evening run that GitHub starts late, after midnight in
    New York, still counts as that day instead of labeling its filings with tomorrow's date.
    """
    try:
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)
    return (now - dt.timedelta(hours=6)).date()


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sic_codes(ciks, today, minutes=SIC_MINUTES):
    """{cik: SIC code} from the SEC company records, cached in .cache/sic.json, for financial and property companies
    and utilities Nasdaq files under another sector (report.wants_sic). Companies not yet looked up come first, then the
    oldest lookups are rechecked. Stops at the time budget or when the SEC throttles. A financial company still without
    a code is matched within its Nasdaq sector instead, a REIT Nasdaq doesn't label as one can still show by its filings
    (report.is_reit), and a utility is matched within Nasdaq's sector (report.peer_sector)."""
    try:
        with open(SIC_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    stale = (today - dt.timedelta(days=SIC_REFRESH_DAYS)).isoformat()
    todo = [c for c in ciks if str(c) not in cache]
    todo += sorted((c for c in ciks if str(c) in cache and cache[str(c)].get("checked", "") < stale),
                   key=lambda c: cache[str(c)].get("checked", ""))[:SIC_REFRESH_PER_RUN]
    stop, done = time.monotonic() + minutes * 60, 0
    for cik in todo:
        if time.monotonic() > stop:
            break
        try:
            code = net.sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").get("sic") or None
        except net.NotFound:
            code = None
        except net.Throttled:
            break
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            continue
        cache[str(cik)] = {"sic": code, "checked": today.isoformat()}
        done += 1
    if done:
        os.makedirs(CACHE, exist_ok=True)
        tmp = SIC_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, separators=(",", ":"))
        os.replace(tmp, SIC_CACHE)
    missing = sum(1 for c in ciks if str(c) not in cache)
    step(f"  SIC codes: {done} looked up, {len(ciks) - missing} of {len(ciks)} financial, property and utility companies "
         "known")
    return {c: cache[str(c)]["sic"] for c in ciks if cache.get(str(c), {}).get("sic")}


# A stock split after a company's latest SEC cover page can leave the listing's market value on the wrong share count:
# on September 2026 data Nasdaq's figure for Monster Beverage implied 2,939M shares after its 2-for-1 split of August 11,
# while its July 31 cover showed 979.5M shares, so 1,959M after the split (and its 984M diluted shares for 2025, 1,969M).
# Where a forward split came after the cover's date and the cover and the year's diluted share count, each carried
# through the splits since, agree within SPLIT_AGREE, a market value implying a count more than SPLIT_GAP away from the
# cover's is replaced by the cover's count times the price. Two SEC counts that agree are the better witness; companies
# whose cover counts one share class only, or depositary shares, don't pass the agreement test. Reverse splits are left
# alone: on the same data all 13 listings the test would have changed after one were small companies that had issued
# many shares since their last filing (New Fortress Energy's debt exchange, fuboTV's merger), which only the listing's
# count has. Listings Nasdaq places outside the United States are left alone too: a foreign company's cover page and
# diluted count are of its ordinary shares, which agree, while its listing may trade depositary shares that each hold
# several (HDFC Bank's hold 3, so after its 1-for-1 bonus issue of September 2025 the test would have tripled its value).
SPLIT_AGREE = 1.10
SPLIT_GAP = 1.25
# The size above which a US listing without financial figures is named in the run's log (a registrant change, a
# revenue tag not read), so a gap in the largest companies is seen rather than silently left without a verdict.
WARN_MCAP = 10e9


def split_market_values(uni, metrics, prices):
    """Resets the market value (uni[s]["mcap"]) of listings whose share count is off after a stock split (SPLIT_GAP),
    and returns [(symbol, shares the old market value implied, shares now)]."""
    fixed = []
    for s, u in uni.items():
        m, splits = metrics.get(s) or {}, (prices.get(s) or {}).get("splits") or []
        cover, diluted, since = m.get("shares_out"), m.get("diluted_shares"), m.get("shares_as_of")
        if not (splits and cover and diluted and since and u.get("mcap") and u.get("price"))                 or u.get("country") != "United States":
            continue
        after = lambda day: math.prod(r for d, r in splits if day and d > day)
        f_cover = after(since)
        if f_cover == 1.0 or any(r < 1 for d, r in splits if d > since):
            continue
        now, check = cover * f_cover, diluted * after(m.get("fiscal_year_end"))
        listed = u["mcap"] / u["price"]
        if abs(math.log(now / check)) <= math.log(SPLIT_AGREE) and abs(math.log(listed / now)) > math.log(SPLIT_GAP):
            u["mcap"] = now * u["price"]
            fixed.append((s, listed, now))
    return fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process a random sample of N tickers")
    ap.add_argument("--insider-days", type=int, default=int(os.environ.get("INSIDER_DAYS", 30)))
    ap.add_argument("--insider-minutes", type=float, default=float(os.environ.get("INSIDER_MINUTES", 75)),
                    help="stop scanning older filing days after this long; the next run picks up where it left off")
    ap.add_argument("--skip-news", action="store_true")
    args = ap.parse_args()
    today = market_today()

    step("Loading the list of US-listed stocks")
    uni = universe.load_universe()
    step(f"  {len(uni)} common stocks")

    step("Loading SEC financial statements")
    # The listed companies' market values, which order the lookups of their quarterly figures in their own filings.
    fund = fundamentals.load_fundamentals(today, {u["cik"]: u.get("mcap") or 0 for u in uni.values()})
    # Industry codes before the financials are worked out, since code 6798 marks a REIT (report.is_reit).
    sic = sic_codes(sorted({u["cik"] for u in uni.values() if report.wants_sic(u) and u["cik"] in fund["companies"]}), today)
    metrics = {}
    for sym, u in uni.items():
        c = fund["companies"].get(u["cik"])
        if c:
            # The debt check is for companies outside finance: a broker's or insurer's interest is paid on customer
            # balances and funding, not on debt the reader missed. Nor does gross profit mean anything for them.
            d = fundamentals.derive(c, report.normal_tax_rate(u, c, sic.get(u["cik"])),
                                    bank=report.is_bank(u, sic.get(u["cik"])), check_debt=not report.is_financial(u),
                                    gross=not report.is_financial(u))
            if d:
                if d.get("reit"):
                    d["reit_type"] = report.REIT_TYPES.get(u["cik"])  # its property type, where reit_types knows it
                metrics[sym] = d
    step(f"  financials for {len(metrics)} companies, {sum(1 for m in metrics.values() if m.get('reit'))} of them REITs"
         f" and {sum(1 for m in metrics.values() if m.get('bank'))} banks")
    gaps = sorted(((u["mcap"], s) for s, u in uni.items() if s not in metrics and (u.get("mcap") or 0) >= WARN_MCAP
                   and u.get("country") == "United States"), reverse=True)
    if gaps:
        step(f"  WARNING: no financial figures for {len(gaps)} US listings worth ${WARN_MCAP / 1e9:.0f}B or more: "
             + ", ".join(f"{s} (CIK {uni[s]['cik']})" for _, s in gaps))

    symbols = sorted(uni)
    if args.limit:
        random.seed(7)
        symbols = sorted(random.sample(symbols, min(args.limit, len(symbols))))
        uni = {s: uni[s] for s in symbols}
        metrics = {s: m for s, m in metrics.items() if s in uni}

    step(f"Loading price history for {len(symbols)} stocks")
    prices = market.price_histories(symbols)
    for s, p in prices.items():
        if p and p.get("price"):
            old = uni[s]["price"]
            uni[s]["price"] = p["price"]
            if uni[s].get("mcap") and old:
                uni[s]["mcap"] = uni[s]["mcap"] * p["price"] / old
    step(f"  prices for {sum(1 for p in prices.values() if p)} stocks")
    for s, listed, filed in split_market_values(uni, metrics, prices):
        step(f"  {s}: market value set from {filed / 1e6:,.0f}M shares (its SEC filings, after the stock split since) "
             f"rather than the {listed / 1e6:,.0f}M the listing's market value implied")

    step("Running the sector screener")
    scr = screener.run(uni, metrics, prices, market.AnalystCounter())
    step(f"  {len(scr['results'])} companies passed")

    step(f"Scanning EDGAR for insider buying (last {args.insider_days} days)")
    cache_path = os.path.join(CACHE, "insiders.json")
    try:
        cache, _ = insiders.update_cache(cache_path, args.insider_days, today, time_budget=args.insider_minutes * 60)
    except Exception as e:  # never lose the screener and reports because one source misbehaved
        step(f"  Insider scan stopped early: {e!r}. Publishing the filings collected so far.")
        cache = insiders.load_cache(cache_path)
    ins = insiders.build(cache, uni if not args.limit else universe_all_for_insiders(uni), args.insider_days, today)
    pending = insiders.pending_days(cache, args.insider_days, today)
    step(f"  {len(ins['companies'])} companies with purchases, {len(ins['stakes'])} new 5%+ stakes"
         + (f"; {len(pending)} day{'s' if len(pending) != 1 else ''} still to scan" if pending else ""))

    heads = {}
    if not args.skip_news:
        step(f"Fetching headlines for {len(symbols)} stocks")
        heads = news.all_headlines([(s, uni[s]["name"]) for s in symbols])

    step("Matching each company with similar companies")
    table = report.peer_table(uni, metrics, sic)

    step("Writing ticker reports")
    # Reports go to a fresh folder that replaces the old one only once every file is written.
    for leftover in ("t_new", "t_old"):
        shutil.rmtree(os.path.join(OUT, leftover), ignore_errors=True)
    ins_by_sym = {c["symbol"]: c for c in ins["companies"] if c.get("symbol")}
    index = []
    for s in symbols:
        u, m, p = uni[s], metrics.get(s), prices.get(s) or {}
        price = p.get("price") or u["price"]
        items = heads.get(s, [])
        mood = news.overall(items)
        ic = ins_by_sym.get(s)
        insider_info = {"tier": ic["tier"], "total_value": ic["total_value"], "window": args.insider_days} if ic else None
        peers, peer_group = report.peers_for(s, table)
        groups, health_score, fv = None, None, None
        if m:
            groups, health_score = report.health(u, m, price, p.get("high52"), peers, table, mood, insider_info)
            fv = report.fair_value(u, m, price, peers, table)
        dropped = fv.pop("dropped", []) if fv else []
        doc = {
            "symbol": s,
            "name": u["name"],
            "exchange": u["exchange"],
            "sector": u["sector"],
            "industry": u["industry"],
            "price": price,
            "mcap": u["mcap"],
            "high52": p.get("high52"),
            "low52": p.get("low52"),
            "weekly": p.get("weekly") or [],
            "peer_group": peer_group,
            "peer_count": len(peers),
            # How the peers were chosen, in one sentence, and the peers themselves with the multiples behind the
            # valuation (empty when the company has no usable sales figure to match on). A non-financial company's
            # peers and peer_multiples also carry price to sales ("ps"), the measure its health check ranks.
            "peer_basis": report.peer_basis(s, table, peers),
            "peers": report.peer_list(s, table, peers),
            "peer_multiples": report.peer_multiples(s, table, peers, dropped,
                                                    [x["name"] for x in fv["methods"] if x.get("counted", True)]
                                                    if fv and fv.get("methods") else None),
            # normal_tax_rate and tax_basis: the rate one-time items and an unusual tax bill are measured against,
            # and where it comes from ("own", "statutory" or "reit"; see fundamentals._normal_rate). For a REIT, "reit"
            # is its kind ("property", valued on funds from operations, or "mortgage"; see report.reit_kind), "ffo" and
            # "adj_ffo" its funds from operations as the filings give them and without one-time items (the one the
            # valuation and scores use), "ffo_parts" how FFO comes from net income, and "reit_type" its property type
            # (reit_types; null where not listed). Its history entries also carry ffo and adj_ffo, and one_time_note then
            # explains FFO (report.ffo_note).
            # A bank (report.is_bank) has "bank": true, and its revenue is net interest income plus noninterest income
            # (fundamentals._bank_year), with the parts, noninterest expense and the efficiency ratio (noninterest
            # expense over revenue without one-time securities gains and losses), its earnings left to common
            # shareholders without one-time items (adj_net_income_common, and per diluted share, adj_eps), tangible
            # common equity (tangible_equity) and its share of tangible assets (tce_ratio), and return on assets, on
            # common equity and on tangible common equity measured on the balance sheet dated returns_as_of. tce_unread
            # says why tangible common equity is null where a line it needs is unknown ("preferred" or "year_end").
            "fundamentals": {**{k: m[k] for k in ("fiscal_year", "fiscal_year_end", "balance_as_of", "revenue", "net_income",
                                                  "adj_net_income", "one_time", "pretax_income", "income_tax",
                                                  "normal_tax_rate", "tax_basis",
                                                  "fcf", "cash", "total_debt", "shares_out", "history")},
                             **({"reit": report.reit_kind(m),
                                 **{k: m.get(k) for k in ("reit_type", "ffo", "adj_ffo", "ffo_parts")}}
                                if m.get("reit") else {}),
                             **({"bank": True, **{k: m.get(k) for k in BANK_FIELDS}} if m.get("bank") else {}),
                             # Why the debt read looks far too small (fundamentals._debt_check), or null.
                             "debt_doubt": m.get("debt_doubt"),
                             # Gross profit and gross margin for the fiscal year (and in each history entry, where
                             # known), where it comes from ("GrossProfit", the company's own line, or the cost of
                             # revenue tag taken from revenue), and the same for the trailing twelve months to ttm_end;
                             # none of them, nor a history entry's gross_profit, for financial companies and REITs.
                             # ttm_prior_revenue is the revenue of the four quarters a year before the twelve months,
                             # where each of the four has its growth.
                             # "quarters": up to 12 quarters of revenue, oldest first, each {start, end, fiscal_quarter,
                             # revenue, gross_profit, derived (a fourth quarter worked out as the year less its first
                             # three), yoy (growth on the quarter a year before), extra_days (how many days longer
                             # than that quarter it ran, where 5 or more), yoy_withheld (why a quarter with revenue and
                             # a year-ago quarter with revenue has no growth: "restated", "unchecked" or "no_base")},
                             # with null figures for a quarter the filings don't give soundly, and dates_estimated on a
                             # blank quarter whose dates are estimated (fundamentals._quarter_series).
                             # sales_doubt: the sales figure looks incomplete (report.sales_doubtful), so the report
                             # shows no quarters. quarters_stale: the newest quarter ended more than STALE_QUARTER_DAYS
                             # before the market date.
                             **{k: m.get(k) for k in ("gross_profit", "gross_margin", "gross_basis", "ttm_revenue",
                                                      "ttm_prior_revenue", "ttm_gross_profit", "ttm_gross_margin",
                                                      "ttm_end", "quarters")},
                             "sales_doubt": report.sales_doubtful(m),
                             "quarters_stale": bool(m.get("quarters")) and m["quarters"][-1]["end"]
                             < (today - dt.timedelta(days=STALE_QUARTER_DAYS)).isoformat(),
                             "one_time_note": report.one_time_note(m)} if m else None,
            "health": groups,
            "health_score": health_score,
            "valuation": fv,
            "news": {"items": items, "overall": mood},
            "insiders": ic,
            "insider_window": args.insider_days,
            "screener": next((r for r in scr["results"] if r["symbol"] == s), None) is not None,
        }
        write(f"t_new/{s}.json", doc)
        index.append([s, u["name"], u["sector"], round(u["mcap"] or 0), health_score, fv["verdict"] if fv else None])

    # Renames are instant but deleting thousands of files is not, so the old folder goes last.
    if os.path.isdir(os.path.join(OUT, "t")):
        os.replace(os.path.join(OUT, "t"), os.path.join(OUT, "t_old"))
    os.replace(os.path.join(OUT, "t_new"), os.path.join(OUT, "t"))
    shutil.rmtree(os.path.join(OUT, "t_old"), ignore_errors=True)
    write("universe.json", index)
    write("screener.json", scr)
    write("insiders.json", ins)
    write("meta.json", {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "market_date": today.isoformat(),
        "last_filing_day": insiders._iso(max(cache["days_done"])) if cache["days_done"] else None,
        # Tier 1 and 2 only, the same companies the home page counts next to it.
        "filed_today": sum(1 for c in ins["companies"] if c["tier"] <= 2 and c["filed"] == today.isoformat()),
        "stocks": len(symbols),
        "with_financials": len(metrics),
        "screener_passed": len(scr["results"]),
        "insider_companies": len(ins["companies"]),
        "insider_tier1": sum(1 for c in ins["companies"] if c["tier"] == 1),
        "insider_tier2": sum(1 for c in ins["companies"] if c["tier"] == 2),
        "stakes": len(ins["stakes"]),
        "insider_days": args.insider_days,
        "insider_pending": pending,
        "sample": bool(args.limit),
    })
    step("Done")


def universe_all_for_insiders(sample):
    # In sample mode, insider data still maps every filer to its listing, so reload the full list.
    full = universe.load_universe()
    full.update(sample)
    return full


if __name__ == "__main__":
    main()
