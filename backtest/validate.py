"""Checks of the point-in-time rebuild against today's live data, before any backtest figure is trusted.

    python -m backtest.validate frames            # (a) rebuilt frames as of today against the live frames API
    python -m backtest.validate market            # (c) rebuilt market values against Nasdaq's, on a recent Friday
    python -m backtest.validate today --replay-dir <dir> --live-dir <dir>
                                                  # (b) the pipeline as of the live run's date, through the harness,
                                                  #     against that run's screener.json and derived metrics
    python -m backtest.validate past [--labels <variant> ...]
                                                  # (d) share counts, splits and market values on each backtest date

(a) asks the live frames API for a few frames (FRAMES: yearly, quarterly and instant, in dollars, shares and a ratio)
and compares them, company by company, with the same frames rebuilt from companyfacts.zip as of today.

(c) rebuilds the market values of a recent Friday from the share counts filed before it (prices.share_count) and
compares them with Nasdaq's in the snapshot of today's listings.

(d) checks the past dates themselves, where (a) to (c) can't see: the price gate over every chart (prices.clean) and
what it changed; how many listings (and each variant's passers and list members) took each source of share count and
how old the counts were; each date's market values of the companies valued from their cover page against the same
companies valued from their balance-sheet or diluted count, with the far ones sorted by why; every valued listing's
market value against the SEC's public float of the time, with every passer and list member 2.5x or more off explained;
the market values, prices and returns against the previous version's; the returns not taken from the adjusted closes;
and the relative returns of the listings left without a market value, by reason.

(b) runs the 719a878 pipeline through the harness (asof.py) as of the live run's market date, with that run's own
listings and market values (the replay's uni.pkl) and no price histories, as the replay did, and compares the
screener's passes with the live screener.json and every company's derived metrics with the replay's metrics.pkl.
Results go to RESULTS_DIR/validate_frames.json, validate_today.json, validate_market.json, validate_past.json and
validate_past.txt.
"""
import argparse
import datetime as dt
import json
import math
import os
import pickle
import statistics
import subprocess
import sys
from collections import Counter

from . import CACHE, RESULTS_DIR, ROOT, STATE_DIR, code_root, use_tools_code

FRAMES = [
    ("us-gaap", "Revenues", "USD", "CY2024"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2025"),
    ("us-gaap", "NetIncomeLoss", "USD", "CY2025"),
    ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD", "CY2023"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2026Q2"),
    ("us-gaap", "StockholdersEquity", "USD", "CY2026Q2I"),
    ("us-gaap", "Assets", "USD", "CY2025Q4I"),
    ("dei", "EntityCommonStockSharesOutstanding", "shares", "CY2026Q2I"),
    ("us-gaap", "EffectiveIncomeTaxRateContinuingOperations", "pure", "CY2024"),
]
LIVE_DATE = "2026-09-29"  # the live run's market date (meta.json)
FIELDS = ["fiscal_year", "fiscal_year_end", "balance_as_of", "revenue", "net_income", "adj_net_income",
          "adj_net_margin", "fcf", "cash", "total_debt", "net_cash", "equity", "lt_debt_to_equity", "revenue_growth",
          "profitable_years", "years_checked", "shares_out", "loc"]


def _close(a, b, rel=0.005):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(a - b) <= rel * max(abs(a), abs(b)) or abs(a - b) < 1e-9


def facts_ord(day):
    return dt.date.fromisoformat(day).toordinal()


def frames_check(day=None):
    """(a): {frame: counts} and examples of every kind of difference."""
    use_tools_code()
    from pipeline import net
    from . import data
    from .facts import Facts
    day = day or dt.date.today()
    facts, loc = Facts(), data.load_loc()
    built = facts.frames(day, FRAMES, loc)
    out = {}
    for job in FRAMES:
        tax, tag, unit, period = job
        live = {d["cik"]: d for d in net.sec_json(f"https://data.sec.gov/api/xbrl/frames/{tax}/{tag}/{unit}/{period}.json")["data"]}
        mine = {r[0]: {"accn": r[1], "start": r[2], "end": r[3], "val": r[4]} for r in built[job]}
        both = live.keys() & mine.keys()
        same = [c for c in both if mine[c]["val"] == live[c]["val"] and mine[c]["accn"] == live[c]["accn"]
                and mine[c]["end"] == live[c]["end"] and mine[c]["start"] == live[c].get("start")]
        diff = sorted(both - set(same))
        # Why each company only the live frame has is missing: its filing isn't in companyfacts.zip at all (filed after
        # the file was made), or the fact is there but was left out.
        only_live, why = sorted(live.keys() - mine.keys()), Counter()
        for c in only_live:
            recs = facts.records(f"{tax}/{tag}", c)
            accns = {r[5] for r in recs}
            from .facts import accn_int
            why["filing not in companyfacts.zip" if accn_int(live[c]["accn"]) not in accns else "fact in the file"] += 1
        diff_why = Counter()
        for c in diff:
            recs = facts.records(f"{tax}/{tag}", c)
            from .facts import accn_int
            la = accn_int(live[c]["accn"])
            if la not in {r[5] for r in recs}:
                diff_why["live value from a filing not in companyfacts.zip"] += 1
            elif mine[c]["val"] == live[c]["val"]:
                diff_why["same value, another filing's copy"] += 1
            else:
                diff_why["other"] += 1
        # Why each company only the rebuilt frame has is there: the SEC labels its fact for the period in companyfacts
        # and the live frame still leaves it out, or no copy of the span is labeled in that tag and the rule placed it.
        from .facts import SAME_TAG, accn_int as _ai
        extra_why = Counter()
        for c in mine.keys() - live.keys():
            r = next((r for r in facts.records(f"{tax}/{tag}", c) if r[5] == _ai(mine[c]["accn"])
                      and r[3] == facts_ord(mine[c]["end"])), None)
            extra_why["labeled in companyfacts, not in the live frame" if r and r[11] >= SAME_TAG
                      else "no copy labeled in that tag (placed by another tag's label or the rule)"] += 1
        out["/".join(job)] = {
            "live": len(live), "rebuilt": len(mine), "both": len(both), "same": len(same),
            "same_value": sum(1 for c in both if mine[c]["val"] == live[c]["val"]),
            "differ": len(diff), "differ_why": dict(diff_why), "only_live": len(only_live), "only_live_why": dict(why),
            "only_rebuilt": len(mine.keys() - live.keys()), "only_rebuilt_why": dict(extra_why),
            "examples": {"differ": [(c, mine[c], {k: live[c].get(k) for k in ("accn", "start", "end", "val")})
                                    for c in diff[:5]],
                         "only_live": [(c, {k: live[c].get(k) for k in ("accn", "start", "end", "val")})
                                       for c in only_live[:5]],
                         "only_rebuilt": [(c, mine[c]) for c in sorted(mine.keys() - live.keys())[:5]]}}
        print(f"{'/'.join(job)}: live {len(live)}, rebuilt {len(mine)}, in both {len(both)}, identical {len(same)} "
              f"({100 * len(same) / max(1, len(both)):.2f}%), same value "
              f"{out['/'.join(job)]['same_value']}; only live {len(only_live)} {dict(why)}; only rebuilt "
              f"{len(mine.keys() - live.keys())} {dict(extra_why)}; differ {dict(diff_why)}", flush=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "validate_frames.json"), "w", encoding="utf-8") as fh:
        json.dump({"as_of": day.isoformat(), "frames": out}, fh, indent=1, default=str)
    return out


def today_check(replay_dir, live_dir, label="validate_today"):
    """(b): runs the harness as of LIVE_DATE on the replay's universe, then compares."""
    from . import asof
    from .screener import BOOT
    root = code_root("719a878")
    out = os.path.join(RESULTS_DIR, f"{label}_{LIVE_DATE}.payload.json")
    logp = os.path.join(RESULTS_DIR, f"{label}_{LIVE_DATE}.log")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    cmd = [sys.executable, "-c", BOOT.format(root=ROOT), "--code", root, "--date", LIVE_DATE, "--label", label,
           "--universe-pickle", os.path.join(replay_dir, "uni.pkl"), "--no-prices", "--out", out]
    with open(logp, "a", encoding="utf-8") as fh:
        rc = subprocess.run(cmd, cwd=CACHE, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc == asof.EXIT_MISSING_TAGS:
        raise SystemExit("tags missing from the index: run the backtest once first")
    if rc:
        raise SystemExit(f"harness failed; see {logp}")
    with open(out, encoding="utf-8") as fh:
        mine = json.load(fh)
    with open(os.path.join(live_dir, "screener.json"), encoding="utf-8") as fh:
        live = json.load(fh)
    p_mine = [r["symbol"] for r in mine["payload"]["results"]]
    p_live = [r["symbol"] for r in live["results"]]
    u_mine = [r["symbol"] for r in mine["payload"]["unverified"]]
    u_live = [r["symbol"] for r in live["unverified"]]
    res = {"passes": {"live": p_live, "harness": p_mine, "only_live": sorted(set(p_live) - set(p_mine)),
                      "only_harness": sorted(set(p_mine) - set(p_live))},
           "unverified": {"live": u_live, "harness": u_mine}}
    # Every company's derived metrics against the replay's (the live frames API, the live run's night).
    mhash = mine["hashes"]["metrics"]
    with open(os.path.join(STATE_DIR, LIVE_DATE, f"metrics_today_{mhash}.pkl"), "rb") as fh:
        ours = pickle.load(fh)["metrics"]
    with open(os.path.join(replay_dir, "metrics.pkl"), "rb") as fh:
        theirs = pickle.load(fh)
    both = sorted(ours.keys() & theirs.keys())
    fields = {}
    for f in FIELDS:
        same = sum(1 for s in both if _close(ours[s].get(f), theirs[s].get(f)))
        fields[f] = {"same": same, "of": len(both)}
    whole = [s for s in both if all(_close(ours[s].get(f), theirs[s].get(f)) for f in FIELDS)]
    newer = [s for s in both if (theirs[s].get("balance_as_of") or "") > (ours[s].get("balance_as_of") or "")
             or (theirs[s].get("fiscal_year_end") or "") > (ours[s].get("fiscal_year_end") or "")]
    res["metrics"] = {"harness": len(ours), "live": len(theirs), "both": len(both),
                      "only_live": sorted(theirs.keys() - ours.keys()), "only_harness": sorted(ours.keys() - theirs.keys()),
                      "all_fields_same": len(whole), "fields": fields,
                      "live_newer_filing": len(newer),
                      "differ_not_newer": sorted(set(both) - set(whole) - set(newer))}
    # Where each pass or miss differs: the screened fields of the companies in only one list.
    detail = {}
    for s in res["passes"]["only_live"] + res["passes"]["only_harness"]:
        detail[s] = {f: [ours.get(s, {}).get(f), theirs.get(s, {}).get(f)] for f in FIELDS}
    res["pass_differences"] = detail
    with open(os.path.join(RESULTS_DIR, "validate_today.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "pass_differences"}, indent=1, default=str)[:6000])
    return res


def market_check(day=dt.date(2026, 9, 25)):
    """(c): market values rebuilt for a recent Friday (prices.share_count's count from filings before it times its
    close) against Nasdaq's market value in the snapshot of today's listings, scaled to that Friday's close."""
    from . import asof, data, prices
    from .facts import Facts
    snap = data.load_universe()
    state = asof.market_state(day, Facts(), snap)
    ratios, far, by_basis = [], [], {}
    for s, u in state["universe"].items():
        n = snap.get(s) or {}
        if u.get("mcap_basis") not in prices.SOURCES or not u.get("mcap") or not n.get("mcap") or not n.get("price"):
            continue
        r = u["mcap"] / (n["mcap"] * u["price"] / n["price"])
        ratios.append(r)
        b = by_basis.setdefault(u["mcap_basis"], [])
        b.append(r)
        if not 0.9 <= r <= 1.1:
            far.append((s, u["mcap_basis"], r))
    within = lambda rs, x: sum(1 for r in rs if 1 / (1 + x) <= r <= 1 + x) / len(rs) if rs else None
    ratios.sort()
    q = lambda f: ratios[int(f * (len(ratios) - 1))]
    out = {"date": day.isoformat(), "basis": state["basis"], "left_out": state["left_out"], "compared": len(ratios),
           "median_ratio": q(0.5), "p05": q(0.05), "p95": q(0.95),
           "within_2pct": within(ratios, 0.02), "within_10pct": within(ratios, 0.1),
           "within_25pct": within(ratios, 0.25),
           "by_basis": {k: {"compared": len(v), "median_ratio": statistics.median(v), "within_2pct": within(v, 0.02),
                            "within_10pct": within(v, 0.1), "within_25pct": within(v, 0.25)}
                        for k, v in by_basis.items()},
           "outside_10pct_examples": [(s, b, float(f"{r:.4g}")) for s, b, r in
                                      sorted(far, key=lambda x: abs(math.log(x[2])), reverse=True)[:40]]}
    with open(os.path.join(RESULTS_DIR, "validate_market.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "outside_10pct_examples"}, indent=1))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# (d) Past dates: the price gate, share counts and market values as of each backtest date.

# A listing's public float is "far" from the market value the backtest gives it where R (float over market value) is
# over FLOAT_FAR or under 1 / FLOAT_FAR: every passer and list member that far is listed with why.
FLOAT_FAR = 2.5
# The float compared on a date: the newest dated within this many days before it (usually the second quarter's end
# before it), from whichever 10-K gives it, filed before the date or after: a check of the price level and the share
# count the date's market value rests on, as of the float's own day, and never an input to it.
FLOAT_DAYS = 300
# Why a passer's or list member's float is far off, where the checks of _float_why can't tell, from reading the
# filings: {symbol: why}.
FLOAT_NOTES = {}


def _quantiles(vals, qs=(0.5, 0.9, 1.0)):
    vals = sorted(vals)
    return {f"p{int(100 * q)}": vals[min(len(vals) - 1, int(q * (len(vals) - 1)))] for q in qs} if vals else {}


def split_check(facts, snap):
    """The price gate (prices.clean, with each company's public floats and share counts) over every chart: how many
    splits it checks (SPLIT_CHECKED or more), each status and what decided it, what it corrected (splits not applied,
    listed on the wrong week, listed but never made, listed twice, applied for prices only; unlisted splits and
    adjustments; bars left unadjusted), what it can't read, and, for each split it doesn't take as listed, whether the
    floats before it agree with the reading taken."""
    from . import data, prices
    out = {"charts": 0, "with_splits": 0, "splits": 0, "checked": 0, "status": Counter(), "decided_by": Counter(),
           "fixed": Counter(), "fixed_list": [], "bars": 0, "unclear": [], "adjustments": [], "level_unknown": [],
           "float_breaks": [], "floats_on_corrections": Counter(), "floats_against": [], "events_by_symbol": {}}
    for name in sorted(os.listdir(os.path.join(CACHE, "charts"))):
        sym = name[:-5].replace("-", ".") if name[:-5] not in snap else name[:-5]
        chart = data.load_chart(sym)
        if not chart:
            continue
        out["charts"] += 1
        evs = prices.splits(chart)
        if evs:
            out["with_splits"] += 1
            out["splits"] += len(evs)
            out["checked"] += sum(1 for _, r in evs if abs(math.log(r)) >= math.log(prices.SPLIT_CHECKED))
        u = snap.get(sym)
        sec = facts.history(u["cik"]) if u else None
        _, rep = prices.clean(chart, sec)
        for e in rep["events"]:
            out["status"][e["status"]] += 1
            if e["listed"] == 1:
                how = (e.get("how") or "").split(";")[0].split(" says")[0].split(" (")[0]
                out["decided_by"][how] += 1
                out["events_by_symbol"].setdefault(sym, []).append((e["date"], e["status"], how))
        for f in rep["fixed"]:
            kind = f["status"] + (" (on another week)" if f.get("moved_to") else "")
            out["fixed"][kind] += 1
            out["fixed_list"].append((sym, f["date"], round(f["ratio"], 4), kind, f.get("how"), f.get("moved_to")))
        for e in rep["events"]:
            if e["status"] in ("not applied", "phantom", "applied, prices only") or e.get("moved_to"):
                rows = e.get("float_rows") or []
                level = 1 if e["status"] in ("not applied", "phantom") else 0  # the reading taken: R under "U" or "A"
                taken = [r[2 if level else 1] for r in rows]
                if not taken:
                    out["floats_on_corrections"]["no float before it"] += 1
                elif all(prices.FLOAT_PLAUSIBLE[0] <= x <= prices.FLOAT_PLAUSIBLE[1] for x in taken):
                    out["floats_on_corrections"]["every float before it plausible on the reading taken"] += 1
                else:
                    out["floats_on_corrections"]["a float before it implausible on the reading taken"] += 1
                    out["floats_against"].append((sym, e["date"], round(e["ratio"], 4), e["status"], rows))
        out["bars"] += len(rep["bars"])
        out["unclear"] += [(sym, x["date"], round(x["ratio"], 4), x.get("how")) for x in rep["unclear"]]
        out["adjustments"] += [(sym, a["date"], round(a["factor"], 4), a["kind"], a["placed"], a["floats_wrong"],
                                a["floats_right"]) for a in rep["adjustments"]]
        out["level_unknown"] += [(sym, x["from"], x["to"], round(x["factor"], 4)) for x in rep["level_unknown"]]
        out["float_breaks"] += [(sym, round(b["factor"], 3), b["floats_wrong"], b["floats_right"])
                                for b in rep["float_breaks"]]
    for k in ("status", "decided_by", "fixed", "floats_on_corrections"):
        out[k] = dict(out[k])
    return out


def _alt_counts(facts, cik, day, chart):
    """The newest balance-sheet and diluted counts dated within MAX_AGE_DAYS of `day`, carried to it through the splits
    since their filing (share_count's basis where nothing tells it otherwise), with whether a split falls between their
    date and their filing."""
    from . import prices
    out = {}
    for c in facts.share_counts(cik, day):
        if c["source"] not in ("balance", "diluted") or not c["val"] or c["val"] <= 0:
            continue
        end, filed = dt.date.fromisoformat(c["date"]), dt.date.fromisoformat(c["filed"])
        if (day - end).days > prices.MAX_AGE_DAYS:
            continue
        out[c["source"]] = {"v": c["val"] * prices.split_factor(chart, filed, day), "date": c["date"],
                            "split_in_filing": prices.split_factor(chart, end, filed) != 1}
    return out


def _why(ratio, cover, alt, source):
    """Why a cover-page count and a balance-sheet or diluted count of the same company far apart differ."""
    from . import prices
    if prices._slip(ratio):
        return "a thousand-fold slip in one of the two"
    if alt["split_in_filing"]:
        return "a split between the other count's date and its filing"
    if ratio > 1 and cover["date"] > alt["date"]:
        return ("shares issued after the other count's date" if source == "balance" else
                "shares issued during or after the diluted count's period (a weighted average lags issuance)")
    if ratio < 1:
        return ("the cover page counts fewer: the other count takes in more (treasury shares, an operating "
                "partnership's units, convertible securities)")
    return "other"


def _next_cover(hist, day, chart, used):
    """The cover-page count of the first filing made on or after `day` (read for this check only), carried back to
    `day`'s basis through the splits between, against the count used: "agrees" within 1.5x, "more shares still",
    "fewer", or "none"."""
    from . import prices
    later = sorted((filed, date, val) for source, start, date, val, filed in hist["counts"]
                   if source == "cover" and filed >= day.toordinal() and val >= prices.MIN_SHARES)
    if not later:
        return "none"
    filed, date, val = later[0]
    v = val / prices.split_factor(chart, day, dt.date.fromordinal(date))
    q = v / used
    return "agrees within 1.5x" if 1 / 1.5 <= q <= 1.5 else "more shares still" if q > 1.5 else "fewer"


def _float_at(facts, cik, day):
    """The public float of the newest date within FLOAT_DAYS before `day` (from the last filing that gives it, filed
    whenever), and the one of the date before that: (date ordinal, value, previous value), or None."""
    from .facts import FLOAT_TAG
    rows = {}
    for r in facts.dated_records(FLOAT_TAG, cik):
        if r[1] != 0 or r[2] or not r[4] or r[4] <= 0:
            continue
        if r[3] not in rows or (r[6], r[5]) > rows[r[3]][1]:
            rows[r[3]] = (r[4], (r[6], r[5]))
    ends = sorted(e for e in rows if day.toordinal() - FLOAT_DAYS <= e <= day.toordinal())
    if not ends:
        return None
    e = ends[-1]
    prev = max((x for x in rows if x < e), default=None)
    return e, rows[e][0], (rows[prev][0] if prev is not None else None)


def _float_why(sym, R, F, prev_F, R_e, q, low_years, R_half, shares_near=None):
    """Why a listing's float is far from the market value the backtest gives it (FLOAT_FAR)."""
    for k, unit in ((1e3, "thousands"), (1e6, "millions")):
        if 0.1 <= R / k <= 2.5:
            return f"the float is given in {unit} of dollars (R / {k:,.0f} = {R / k:.2f})"
        if 0.1 <= R * k <= 2.5:
            return f"the float is given in {unit} of its dollars, as a figure {k:,.0f} times too small (R x {k:,.0f} = " \
                   f"{R * k:.2f})"
    if shares_near and abs(F / shares_near - 1) < 0.01:
        return "the float given is a share count (the shares outstanding), not dollars"
    if prev_F and F == prev_F:
        return "the 10-K repeats the year before's float"
    if R_half is not None and 1 / FLOAT_FAR <= R_half <= 1.2:
        return (f"the float is dated at the fiscal year's end, but fits the price of six months before, the second "
                f"quarter's end it is meant for (R = {R_half:.2f} then)")
    if R_e is not None and 1 / FLOAT_FAR <= R_e <= FLOAT_FAR and abs(math.log(q)) >= math.log(1.5):
        return (f"shares {'issued' if q > 1 else 'retired'} between the float's date and the count used ({q:.2f}x): "
                f"with the count of the float's date, R = {R_e:.2f}")
    if R < 1 / FLOAT_FAR and (R_e is None or R_e < 1 / FLOAT_FAR) and low_years:
        return f"affiliates hold most of the shares: R under 0.5 at most of its floats since 2021 ({low_years})"
    return FLOAT_NOTES.get(sym, "unexplained")


def float_check(facts, st, day, members):
    """Each valued listing's market value on `day` against the SEC's public float as of then (_float_at, filed before
    `day`): R = float / (the count used on `day` x the price on the float's date, on `day`'s share basis). The
    distribution, and every listing in `members` ({symbol: [where]}) that is FLOAT_FAR off, with why."""
    from . import data, prices
    rows, far, by_symbol = [], [], {}
    for s, u in st["universe"].items():
        if u["mcap_basis"] not in prices.SOURCES:
            continue
        got = _float_at(facts, u["cik"], day)
        if not got:
            continue
        e, F, prev_F = got
        eday = dt.date.fromordinal(e)
        raw = data.load_chart(s)
        sec = facts.history(u["cik"])
        chart, _ = prices.clean(raw, sec)
        p = prices.prices_at(chart, eday)
        if not p or not p["price"]:
            continue
        pe = p["price"] / prices.split_factor(chart, eday, day)  # on `day`'s share basis
        shares = st["shares"][s]["shares"]
        R = F / (shares * pe)
        rows.append(R)
        by_symbol[s] = round(R, 4)
        if not (R > FLOAT_FAR or R < 1 / FLOAT_FAR) or s not in members:
            continue
        barriers = prices._barriers(raw, chart)
        c = prices._count_near(sec["counts"], e, barriers)
        R_e = q = None
        if c:
            R_e = F / (c["val"] * p["price"])
            q = shares / (c["val"] * prices.split_factor(chart, dt.date.fromordinal(c["date"]), day))
        yrs = [f for f in prices.float_ratios(chart, sec, barriers)]
        low = ", ".join(f"{f['date'].year} {f['R']:.2f}" for f in yrs) if yrs and statistics.median(
            f["R"] for f in yrs) < 0.5 else ""
        # The same float at the price of six months before (a float meant for the second quarter's end, dated at the
        # fiscal year's).
        R_half = None
        ph = prices.prices_at(chart, eday - dt.timedelta(days=183)) if eday.month in (12, 1) else None
        if ph and ph["price"]:
            R_half = F / (shares * ph["price"] / prices.split_factor(chart, eday - dt.timedelta(days=183), day))
        far.append({"symbol": s, "R": round(R, 3), "float": F, "float_date": eday.isoformat(), "shares": shares,
                    "source": u["mcap_basis"], "price_float_date": round(pe, 4), "R_at_float_date_count":
                    R_e and round(R_e, 3), "count_change": q and round(q, 3), "in": members[s],
                    "why": _float_why(s, R, F, prev_F, R_e, q or 1, low, R_half, c and c["val"])})
    rows.sort()
    q = lambda f: rows[int(f * (len(rows) - 1))] if rows else None
    return {"compared": len(rows), "median": q(0.5), "p05": q(0.05), "p10": q(0.10), "p25": q(0.25),
            "p75": q(0.75), "p90": q(0.90), "p95": q(0.95),
            "within": sum(1 for r in rows if 1 / FLOAT_FAR <= r <= FLOAT_FAR) / len(rows) if rows else None,
            "over": sum(1 for r in rows if r > FLOAT_FAR), "under": sum(1 for r in rows if r < 1 / FLOAT_FAR),
            "in_thousands": sum(1 for r in rows if 150 <= r <= 2500), "by_symbol": by_symbol,
            "far_members": sorted(far, key=lambda x: (x["why"] == "unexplained", x["symbol"]), reverse=True)}


def past_check(dates=None, labels=None):
    """(d): for each backtest date, from its market.json (asof.market_state) and returns.json: the price gate over
    every chart and what it changed on the date; how many listings and passers took each source of share count and how
    old the counts were; the market values of companies valued from their cover page against the same companies valued
    from their balance-sheet or diluted count, with the far ones sorted by why (and, for cover pages far above the
    other count, what the next cover page says); every valued listing's market value against the SEC's public float,
    with every passer or list member far off explained; how the market values and returns compare with the backtest's
    previous version (market_v4.json, returns_v4.json); the returns not taken from the adjusted closes; and the relative
    returns of the listings left without a market value, by reason, over every listing and over the US companies."""
    from . import DATES, asof, data, prices
    from .facts import Facts
    from .screener import LISTS, list_members, returns_at
    dates = dates or DATES
    facts, snap, loc = Facts(), data.load_universe(), data.load_loc()
    labels = labels if labels is not None else sorted(
        {n[:-len(f"_{dates[0]}.payload.json")] for n in os.listdir(RESULTS_DIR)
         if n.endswith(f"_{dates[0]}.payload.json") and not n.startswith("validate")})
    out = {"labels": labels, "dates": {}}
    out["splits"] = split_check(facts, snap)
    # The cover pages indexed for today's listed companies, and how many give two counts or more for their latest date
    # (one per share class; the bulk file keeps only counts given without a class, so none should).
    from .facts import COVER_TAG
    filings, multi = 0, 0
    for cik in sorted({u["cik"] for u in snap.values()}):
        by = {}
        for r in facts.records(COVER_TAG, cik):
            if r[1] == 1:
                by.setdefault((r[6], r[5]), []).append(r)
        for rows in by.values():
            filings += 1
            end = max(r[3] for r in rows)
            multi += len({r[4] for r in rows if r[3] == end}) > 1
    out["covers"] = {"filings": filings, "two_counts_for_latest_date": multi}
    for d in dates:
        day = dt.date.fromisoformat(d)
        st = asof.market_state(day, facts, snap)
        rr_all = returns_at(day)
        rets = rr_all["stocks"]
        uni, shares = st["universe"], st.get("shares") or {}
        # The listings the screener and its US lists can take: Nasdaq's country the US or blank, and an SEC location
        # in the US (only companies with SEC figures have one).
        us = [s for s, u in uni.items() if u.get("country") in ("United States", "")
              and (loc.get(u["cik"]) or "").startswith("US")]
        rec = {"listings": len(uni), "basis": dict(Counter(u["mcap_basis"] for u in uni.values())),
               "us_companies": len(us), "us_basis": dict(Counter(uni[s]["mcap_basis"] for s in us)),
               "left_out": st.get("left_out"), "left_out_listings": st.get("left_out_listings"),
               "level_unknown": sorted(s for s, u in uni.items() if u["mcap_basis"] == "none_level_unknown")}
        # (a) Sources and ages.
        valued = [s for s, u in uni.items() if u["mcap_basis"] in prices.SOURCES]
        rec["valued"] = len(valued)
        rec["share_of_valued"] = {k: sum(1 for s in valued if uni[s]["mcap_basis"] == k) / len(valued)
                                  for k in prices.SOURCES}
        rec["ages"] = {k: _quantiles([shares[s]["age_days"] for s in valued if uni[s]["mcap_basis"] == k])
                       for k in prices.SOURCES}
        rec["ages"]["all"] = _quantiles([shares[s]["age_days"] for s in valued])
        rec["ages_us"] = _quantiles([shares[s]["age_days"] for s in us if s in shares and uni[s]["mcap_basis"]
                                     in prices.SOURCES])
        rec["issued"] = sum(1 for s in valued if shares[s].get("issued"))
        rec["filed_basis"] = sum(1 for s in valued if shares[s].get("basis") == "filed")
        rec["whole"] = sorted(s for s in valued if shares[s].get("whole"))
        rec["date_typo"] = sorted((s, shares[s]["source"], shares[s]["date_typo"]) for s in valued
                                  if shares[s].get("date_typo"))
        rec["passers"] = {}
        members = {}
        for lab in labels:
            pp = os.path.join(RESULTS_DIR, f"{lab}_{d}.payload.json")
            if not os.path.exists(pp):
                continue
            with open(pp, encoding="utf-8") as fh:
                payload = json.load(fh)
            if (payload.get("hashes") or {}).get("market") != st.get("key"):
                rec["passers"][lab] = "payload made on another version of the market values"
                continue
            groups = {"passers": [r["symbol"] for r in payload["payload"]["results"]]}
            groups.update({k: v for k, v in list_members(payload).items() if k in LISTS})
            for g, syms in groups.items():
                for s in syms:
                    members.setdefault(s, []).append(f"{lab}:{g}")
            rec["passers"][lab] = {g: {"n": len(syms), **{k: sum(1 for s in syms if uni[s]["mcap_basis"] == k)
                                                          for k in prices.SOURCES},
                                       "max_age_days": max((shares[s]["age_days"] for s in syms if s in shares),
                                                           default=None)}
                                   for g, syms in groups.items()}
        # (b) What the gate changed on this date, and what read the splits of the 52 weeks after it (whose closes test
        # reads the closes of the split's own weeks, part of how the stock did after the date).
        rec["splits_fixed"] = {s: v for s, v in (st.get("splits_fixed") or {}).items()}
        end = day + 52 * prices.WEEK
        rec["splits_in_window_read_by"] = dict(Counter(
            how for s in uni if s in rets for d0, status, how in out["splits"]["events_by_symbol"].get(s, [])
            if day < dt.date.fromisoformat(d0) <= end))
        rec["gate_members"] = sorted(s for s in rec["splits_fixed"] if s in members)
        # (c) Cover-page counts against the same companies' balance-sheet and diluted counts.
        cons = {}
        for s in valued:
            if uni[s]["mcap_basis"] != "cover":
                continue
            sec = facts.history(snap[s]["cik"])
            chart, _ = prices.clean(data.load_chart(s), sec)
            cov = shares[s]
            for src, alt in _alt_counts(facts, snap[s]["cik"], day, chart).items():
                ratio = cov["shares"] / alt["v"]
                x = cons.setdefault(src, {"ratios": [], "far": []})
                x["ratios"].append(ratio)
                if not 0.5 <= ratio <= 2:
                    why = _why(ratio, cov, alt, src)
                    nxt = _next_cover(sec, day, chart, cov["shares"]) if why.startswith("shares issued") else None
                    x["far"].append((s, round(ratio, 3), cov["date"], alt["date"], why, nxt))
        rec["consistency"] = {}
        for src, x in cons.items():
            rs = x["ratios"]
            within = lambda f: sum(1 for r in rs if 1 / f <= r <= f) / len(rs)
            rec["consistency"][src] = {
                "compared": len(rs), "median_ratio": statistics.median(rs), "within_10pct": within(1.1),
                "within_25pct": within(1.25), "within_2x": within(2.0), "outside_2x": len(x["far"]),
                "why": dict(Counter(f[4] for f in x["far"])),
                "next_cover": dict(Counter(f[5] for f in x["far"] if f[5])), "far": x["far"]}
        # (d) The public float.
        rec["floats"] = float_check(facts, st, day, members)
        # (e) Against the previous version of the market values and returns.
        old_path = os.path.join(STATE_DIR, d, "market_v4.json")
        if os.path.exists(old_path):
            with open(old_path, encoding="utf-8") as fh:
                old = json.load(fh)["universe"]
            both = [s for s in uni if s in old]
            moved = [s for s in both if uni[s]["mcap"] and old[s]["mcap"]
                     and not 1 / 1.5 <= uni[s]["mcap"] / old[s]["mcap"] <= 1.5]
            pchg = [s for s in both if abs(uni[s]["price"] - old[s]["price"]) > 1e-6 * max(1, old[s]["price"])]
            rec["vs_v4"] = {"both": len(both),
                            "valued_both": sum(1 for s in both if uni[s]["mcap"] and old[s]["mcap"]),
                            "moved_over_1_5x": sorted(moved),
                            "price_changed": sorted(pchg),
                            "valued_now_only": sorted(s for s in both if uni[s]["mcap"] and not old[s]["mcap"]),
                            "valued_before_only": sorted(s for s in both if old[s]["mcap"] and not uni[s]["mcap"]),
                            "only_old": sorted(set(old) - set(uni)), "only_new": sorted(set(uni) - set(old))}
        old_rets = os.path.join(STATE_DIR, d, "returns_v4.json")
        if os.path.exists(old_rets):
            with open(old_rets, encoding="utf-8") as fh:
                orr = json.load(fh)["stocks"]
            rec["returns_vs_v4"] = sorted((s, round(orr[s]["raw"], 3), round(rets[s]["raw"], 3)) for s in rets
                                          if s in orr and abs(rets[s]["raw"] - orr[s]["raw"]) > 1e-6)
        rec["returns_not_adjusted_closes"] = rr_all.get("not_adjusted_closes")
        rec["market_returns"] = {k: rr_all[k] for k in ("listed", "with_return", "average", "median", "p1", "p99")}
        # (f) Returns of the listings with no market value, by reason, over every listing and over the US companies;
        # and of those left out (on their charts as Yahoo gives them, which the gate couldn't read).
        rr = {}
        for scope, syms in (("all", list(uni)), ("us", us)):
            x = {}
            for why in sorted({uni[s]["mcap_basis"] for s in syms}):
                mine = [s for s in syms if uni[s]["mcap_basis"] == why]
                rel = [rets[s]["rel"] for s in mine if s in rets]
                x["valued" if why in prices.SOURCES else why] = x.get(
                    "valued" if why in prices.SOURCES else why, []) + rel
            rr[scope] = {k: {"with_return": len(v), "mean_rel": statistics.fmean(v) if v else None,
                             "median_rel": statistics.median(v) if v else None} for k, v in x.items()}
        raw = [prices.total_return(data.load_chart(s), day) for s in (st.get("left_out_listings") or {})]
        rr["left_out_split_unclear"] = {"n": len(raw), "raw_returns_as_yahoo_gives": [None if r is None else round(r, 3)
                                                                                      for r in raw]}
        rec["unvalued_returns"] = rr
        out["dates"][d] = rec
    # A far float left unexplained above, against the same listing's R on the other dates: under 0.5 on every date it
    # is valued on (two or more), the non-affiliates' share of a company whose affiliates hold most of it; over FLOAT_FAR
    # on every date, shares the float counts and the count leaves out; within 0.4 to FLOAT_FAR on every other date, one
    # year's float out of line with the rest, while the price level and count fit those.
    for d in dates:
        for x in out["dates"][d]["floats"]["far_members"]:
            if x["why"] != "unexplained":
                continue
            rs = {d2: out["dates"][d2]["floats"]["by_symbol"][x["symbol"]] for d2 in dates
                  if x["symbol"] in out["dates"][d2]["floats"]["by_symbol"]}
            others = [r for d2, r in rs.items() if d2 != d]
            listed = ", ".join(f"{d2[:4]} {r:.2f}" for d2, r in rs.items())
            if len(rs) >= 2 and all(r < 0.5 for r in rs.values()):
                x["why"] = f"R under 0.5 on every date it is valued on ({listed}): affiliates hold most of the shares"
            elif len(rs) >= 2 and all(r > FLOAT_FAR for r in rs.values()):
                x["why"] = f"R over {FLOAT_FAR} on every date it is valued on ({listed}): the float takes in shares the "                            f"count leaves out"
            elif others and all(1 / FLOAT_FAR <= r <= FLOAT_FAR for r in others):
                x["why"] = (f"one year's float out of line with the company's others ({listed}): the price level and "
                            f"the count fit the floats of the other dates")
    for d in dates:
        out["dates"][d]["floats"]["far_members"].sort(key=lambda x: (x["why"] == "unexplained", x["symbol"]),
                                                      reverse=True)
    out["splits"]["unclear_by_date"] = {d: sorted(out["dates"][d]["left_out_listings"] or {}) for d in dates}
    with open(os.path.join(RESULTS_DIR, "validate_past.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    text = past_text(out)
    print(text)
    with open(os.path.join(RESULTS_DIR, "validate_past.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return out


def past_text(out):
    pct = lambda x: "n/a" if x is None else f"{100 * x:.1f}%"
    pts = lambda x: "n/a" if x is None else f"{100 * x:+.1f}"
    sp = out["splits"]
    lines = [f"Price gate over {sp['charts']} charts: {sp['with_splits']} with splits, {sp['splits']} splits, "
             f"{sp['checked']} checked (a ratio of 1.5 or more either way)",
             f"  statuses: {sp['status']}", f"  single splits decided by: {sp['decided_by']}",
             f"  changed: {sp['fixed']}; {sp['bars']} bars left unadjusted at a split corrected",
             f"  floats before each split not taken as listed: {sp['floats_on_corrections']}; against the reading "
             f"taken: {sp['floats_against']}"]
    for s, d, r, kind, how, moved in sp["fixed_list"]:
        lines.append(f"    {s} {d} {r} {kind}{' to ' + moved if moved else ''} ({how})")
    lines.append(f"  unclear ({len(sp['unclear'])}): " + "; ".join(f"{s} {d} {r} ({how})" for s, d, r, how in
                                                                 sp["unclear"]))
    lines.append(f"  unlisted adjustments ({len(sp['adjustments'])}):")
    for s, d, f, kind, placed, wrong, right in sp["adjustments"]:
        lines.append(f"    {s} {d} x{f} {kind}, placed by {placed}; floats before {wrong}, after {right[:3]}")
    lines.append(f"  price level unknown between ({len(sp['level_unknown'])}): " + "; ".join(
        f"{s} {a} to {b} (x{f})" for s, a, b, f in sp["level_unknown"]))
    lines.append(f"  float breaks left as they are ({len(sp['float_breaks'])}, one float each, borne out by neither "
                 f"the closes' digits nor the counts): " + "; ".join(f"{s} x{f}" for s, f, _, _ in sp["float_breaks"]))
    c = out.get("covers") or {}
    lines.append(f"Cover pages indexed for today's listed companies: {c.get('filings')}; giving two counts or more for "
                 f"their latest date: {c.get('two_counts_for_latest_date')}")
    lines.append("")
    for d, rec in out["dates"].items():
        lines.append(f"== {d}: {rec['listings']} listings; {rec['valued']} valued; basis {rec['basis']}; "
                     f"left out {rec['left_out']}")
        lines.append(f"  left out for a split no test can read: {sorted(rec['left_out_listings'] or {})}; no price "
                     f"level: {rec['level_unknown']}")
        lines.append(f"  US companies with an SEC location (what the screener can take): {rec['us_companies']}; "
                     f"basis {rec['us_basis']}")
        lines.append("  share of valued listings by source: " + ", ".join(
            f"{k} {pct(v)}" for k, v in rec["share_of_valued"].items()))
        lines.append("  count ages in days (median / 90th pct / max): " + ", ".join(
            f"{k} {a.get('p50')}/{a.get('p90')}/{a.get('p100')}" for k, a in rec["ages"].items() if a)
            + f"; US companies {rec['ages_us'].get('p50')}/{rec['ages_us'].get('p90')}/{rec['ages_us'].get('p100')}")
        lines.append(f"  counts taken as shares issued since the others: {rec['issued']}; on their filing date's split "
                     f"basis: {rec['filed_basis']}; the diluted count for the whole company over one class's "
                     f"({len(rec['whole'])}): {rec['whole']}; a count dated after its filing, taken as of its filing "
                     f"({len(rec['date_typo'])}): {rec['date_typo']}")
        for lab, g in rec["passers"].items():
            if isinstance(g, str):
                lines.append(f"  {lab}: {g}")
                continue
            lines.append(f"  {lab}: " + "; ".join(
                f"{k} {v['n']} (" + ", ".join(f"{s} {v[s]}" for s in ("cover", "balance", "diluted")) +
                f", oldest {v['max_age_days']} days)" for k, v in g.items()))
        lines.append(f"  gate changes on this date's passers and list members: {rec['gate_members']}")
        lines.append(f"  splits of 1.5 or more in the 52 weeks after, of listings with a return, read by: "
                     f"{rec['splits_in_window_read_by']}")
        for src, c in rec["consistency"].items():
            lines.append(f"  cover vs {src}: {c['compared']} compared, median ratio {c['median_ratio']:.3f}, within 10% "
                         f"{pct(c['within_10pct'])}, within 25% {pct(c['within_25pct'])}, within 2x "
                         f"{pct(c['within_2x'])}; outside 2x {c['outside_2x']}: {c['why']}; the next cover page of "
                         f"those taken as shares issued: {c['next_cover']}")
        f = rec["floats"]
        lines.append(f"  public float / market value: {f['compared']} compared, median {f['median']:.3f} (5th to 95th "
                     f"pct {f['p05']:.3f} to {f['p95']:.3f}; 25th to 75th {f['p25']:.3f} to {f['p75']:.3f}); within "
                     f"{1 / FLOAT_FAR:.1f} to {FLOAT_FAR} {pct(f['within'])}; over {FLOAT_FAR}: {f['over']} (of which "
                     f"{f['in_thousands']} floats in thousands); under {1 / FLOAT_FAR:.1f}: {f['under']}")
        lines.append(f"  passers and list members {FLOAT_FAR}x or more off ({len(f['far_members'])}):")
        for x in f["far_members"]:
            lines.append(f"    {x['symbol']} R {x['R']} ({x['source']}, float {x['float']:,.0f} on {x['float_date']}) "
                         f"[{', '.join(x['in'][:4])}{' ...' if len(x['in']) > 4 else ''}]: {x['why']}")
        if "vs_v4" in rec:
            v = rec["vs_v4"]
            lines.append(f"  against the previous version: {v['valued_both']} valued in both, "
                         f"{len(v['moved_over_1_5x'])} market values moved over 1.5x {v['moved_over_1_5x']}; prices "
                         f"changed {len(v['price_changed'])} {v['price_changed']}; valued now only "
                         f"{v['valued_now_only']}; valued before only {v['valued_before_only']}; listings only before "
                         f"{v['only_old']}; only now {v['only_new']}")
        if "returns_vs_v4" in rec:
            lines.append(f"  returns changed from the previous version ({len(rec['returns_vs_v4'])}): "
                         + ", ".join(f"{s} {a:+.3f} -> {b:+.3f}" for s, a, b in rec["returns_vs_v4"]))
        nac = rec.get("returns_not_adjusted_closes") or {}
        lines.append(f"  returns not from the adjusted closes ({len(nac)}): " + "; ".join(
            f"{s} ({h})" for s, h in sorted(nac.items())))
        m = rec["market_returns"]
        lines.append(f"  the market: {m['with_return']} listings with a return, average {pts(m['average'])} "
                     f"(winsorized at {pts(m['p1'])} and {pts(m['p99'])}), median {pts(m['median'])}")
        for scope in ("all", "us"):
            lines.append(f"  relative returns by basis ({'every listing' if scope == 'all' else 'US companies'}): "
                         + "; ".join(f"{k} n={v['with_return']} mean {pts(v.get('mean_rel'))} median "
                                     f"{pts(v.get('median_rel'))}" for k, v in rec["unvalued_returns"][scope].items()))
        lo = rec["unvalued_returns"]["left_out_split_unclear"]
        lines.append(f"  left out for a split no test can read: {lo['n']}, raw returns on Yahoo's charts "
                     f"{lo['raw_returns_as_yahoo_gives']}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["frames", "today", "market", "past"])
    ap.add_argument("--replay-dir", help="the folder with the live run's replay: uni.pkl, metrics.pkl")
    ap.add_argument("--live-dir", help="the folder with the live run's published screener.json")
    ap.add_argument("--as-of", help="for frames: the date to rebuild as of (default today)")
    ap.add_argument("--labels", nargs="*", help="for past: the variants whose passers and lists to count (default: "
                                                "every variant with payloads)")
    args = ap.parse_args()
    if args.what == "market":
        market_check()
    elif args.what == "past":
        past_check(labels=args.labels)
    elif args.what == "frames":
        frames_check(dt.date.fromisoformat(args.as_of) if args.as_of else None)
    else:
        if not (args.replay_dir and args.live_dir):
            raise SystemExit("today needs --replay-dir and --live-dir")
        today_check(args.replay_dir, args.live_dir)


if __name__ == "__main__":
    main()
