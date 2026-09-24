"""Builds every JSON file the site reads.

    python -m pipeline.build                 # full run (about an hour, mostly polite rate limits)
    python -m pipeline.build --limit 150     # quick sample for local testing
"""
import argparse
import datetime as dt
import json
import os
import random
import shutil
import time

from . import fundamentals, insiders, market, news, report, screener, universe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "data")
CACHE = os.path.join(ROOT, ".cache")


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
    with open(full, "w", encoding="utf-8") as fh:
        json.dump(_round(payload), fh, separators=(",", ":"), ensure_ascii=False)


def market_today():
    """Today's date in New York, so a late-evening run still counts as that trading day."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process a random sample of N tickers")
    ap.add_argument("--insider-days", type=int, default=int(os.environ.get("INSIDER_DAYS", 30)))
    ap.add_argument("--skip-news", action="store_true")
    args = ap.parse_args()
    today = market_today()

    step("Loading the list of US-listed stocks")
    uni = universe.load_universe()
    step(f"  {len(uni)} common stocks")

    step("Loading SEC financial statements")
    fund = fundamentals.load_fundamentals(today)
    metrics = {}
    for sym, u in uni.items():
        c = fund["companies"].get(u["cik"])
        if c:
            d = fundamentals.derive(c)
            if d:
                metrics[sym] = d
    step(f"  financials for {len(metrics)} companies")

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

    step("Running the sector screener")
    scr = screener.run(uni, metrics, prices, market.AnalystCounter())
    step(f"  {len(scr['results'])} companies passed")

    step(f"Scanning EDGAR for insider buying (last {args.insider_days} days)")
    cache = insiders.update_cache(os.path.join(CACHE, "insiders.json"), args.insider_days, today)
    ins = insiders.build(cache, uni if not args.limit else universe_all_for_insiders(uni), args.insider_days, today)
    step(f"  {len(ins['companies'])} companies with purchases, {len(ins['stakes'])} new 5%+ stakes")

    heads = {}
    if not args.skip_news:
        step(f"Fetching headlines for {len(symbols)} stocks")
        heads = news.all_headlines([(s, uni[s]["name"]) for s in symbols])

    step("Writing ticker reports")
    if os.path.isdir(os.path.join(OUT, "t")):
        shutil.rmtree(os.path.join(OUT, "t"))
    table = report.peer_table(uni, metrics)
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
            "fundamentals": {k: m[k] for k in ("fiscal_year", "fiscal_year_end", "balance_as_of", "revenue", "net_income",
                                               "fcf", "cash", "total_debt", "shares_out", "history")} if m else None,
            "health": groups,
            "health_score": health_score,
            "valuation": fv,
            "news": {"items": items, "overall": mood},
            "insiders": ic,
            "screener": next((r for r in scr["results"] if r["symbol"] == s), None) is not None,
        }
        write(f"t/{s}.json", doc)
        index.append([s, u["name"], u["sector"], round(u["mcap"] or 0), health_score, fv["verdict"] if fv else None])

    write("universe.json", index)
    write("screener.json", scr)
    write("insiders.json", ins)
    write("meta.json", {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "market_date": today.isoformat(),
        "last_filing_day": insiders._iso(max(cache["days_done"])) if cache["days_done"] else None,
        "filed_today": sum(1 for c in ins["companies"] if c["filed"] == today.isoformat()),
        "stocks": len(symbols),
        "with_financials": len(metrics),
        "screener_passed": len(scr["results"]),
        "insider_companies": len(ins["companies"]),
        "insider_tier1": sum(1 for c in ins["companies"] if c["tier"] == 1),
        "insider_tier2": sum(1 for c in ins["companies"] if c["tier"] == 2),
        "stakes": len(ins["stakes"]),
        "insider_days": args.insider_days,
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
