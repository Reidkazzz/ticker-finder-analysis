"""How the screener's picks did over the 52 weeks after each backtest date, and the command line.

    python -m backtest.screener run --code <root> --label <name> [--dates 2022-09-30 ...] [--set module.ATTR=<json>]
    python -m backtest.screener compare <base label> <other label> [...] [--chain]
    python -m backtest.screener report <label>
    python -m backtest.screener tables <label> ... [--readme]
    python -m backtest.screener run --code <root> --label <name> --metrics-only

`run` starts one process per date (asof.py) with the variant's code root first on sys.path, then scores what it
returned; a variant is a code root plus any number of --set overrides of module attributes, e.g.
    --set 'screener.RULES["min_net_margin"]=0.03'     (one key of a dict)
    --set 'screener.SCORE_WEIGHTS={"ps": 20, "fcf": 15}' (a JSON object updates those keys of a dict)
    --set 'screener.EXCLUDED_SECTORS={}'              (any other value replaces the attribute)
For the code at a past commit, --code <commit> makes and uses .cache/backtest/code/<commit> (git archive).

`compare` measures each other variant against the base: the companies it added and dropped and how they did beside the
base's own passers, whether the passes and scores are the same, and how the score's rank correlation with the return
changed on the companies both pass. With --chain, each variant is measured against the one before it instead
(`compare a b c` is then a -> b, b -> c), and a summary of every step goes to chain_<a>_..._<c>.txt.

Every return is the stock's total return (dividends included) from the date's weekly close to the close 52 weeks later,
winsorized at that date's 1st and 99th percentiles across every listing with both closes, less the equal-weighted
average of those winsorized returns: "relative return" below, in points. Intervals are 95% bootstrap intervals over
companies (2,000 resamples, fixed seed); pooled over the dates, each date's companies are resampled within that date,
so every resample keeps each date's number of companies. A pooled mean counts every company-date; a pooled difference
between two groups is the weighted average of each date's own difference (group_diff). Every group's figures also give
its median, its mean without the returns capped at the winsorizing percentiles ("without capped": a return inside the
caps stays in, however large), and its trimmed mean, without its single largest return either way ("trimmed"), so that
no one company carries a small group.
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time

from . import CACHE, DATES, RESULTS_DIR, ROOT, STATE_DIR, code_root

BOOTSTRAP = 2000
SEED = 7
TOP = 10
# The watch lists a payload can hold, with their names in the reports.
LISTS = {"net_cash_list": "Under 1x cash", "financials_list": "Banks and insurers under book value",
         "giants": "Overpriced giants", "unverified": "Unverified (left out, listed)"}
# The alternative out-of-favor test for an industry, measured beside the screener's own (an industry whose median
# listing is RULES["out_of_favor_drawdown"], 25%, or more below its 52-week high): an industry whose median listing is
# at least INDUSTRY_GAP further below its 52-week high than the whole market's median listing is. A fixed 25% marks most
# industries in a broad slump (97 of 133 on September 29, 2026, when the median listing was 29.6% below its high) and
# hardly any in a boom; measured against the market, it marks the industries that fell further than the rest, whatever
# the market did.
INDUSTRY_GAP = 0.10
# Every list the reports measure: the watch lists, the financial companies split by the book they are measured on
# (banks on tangible book, the others on book), and the listings of the industries each rule marks out of favor.
LIST_NAMES = {"net_cash_list": LISTS["net_cash_list"], "financials_list": LISTS["financials_list"],
              "financials_list_banks": "  of which banks (tangible book)",
              "financials_list_others": "  of which others (book)",
              "giants": LISTS["giants"], "unverified": LISTS["unverified"],
              "industries_own": "Listings in out-of-favor industries (screener's rule)",
              "industries_vs_market": f"Listings in industries {100 * INDUSTRY_GAP:.0f}+ pts below the market"}
INDUSTRY_RULES = ("industries_own", "industries_vs_market")
BOOT = ("import sys; sys.path.insert(0, {root!r}); from backtest import asof; asof.main()")


# ---------------------------------------------------------------------------------------------------------------------
# Returns and the date's market.

def returns_key(market_key):
    """The key returns.json is kept under: the market's (asof.market_key, which covers prices.py) and returns_at's own
    code."""
    import hashlib
    import inspect
    return hashlib.sha256((str(market_key) + inspect.getsource(returns_at)).encode()).hexdigest()[:12]


def returns_at(day):
    """{symbol: {"raw", "rel", "capped", "how"}} for every listing on `day` with a close then and 52 weeks later, and
    the date's market figures (STATE_DIR/<day>/returns.json, made once per returns_key), on the charts market.json's
    listings were priced from (prices.clean, through the price gate). "capped" is 1 for a return at or above the 99th
    percentile the returns are winsorized at, -1 at or below the 1st, else 0; "how" says where a return isn't the
    adjusted closes' (prices.total_return)."""
    from . import asof, data, prices
    from .facts import Facts
    facts = Facts()
    market = asof.market_state(day, facts)  # rebuilt first if made under another key
    path = os.path.join(STATE_DIR, day.isoformat(), "returns.json")
    key = returns_key(market.get("key"))
    out = asof.load_keyed(path, key)
    if out is not None:
        return out
    listed = market["universe"]
    raw, hows = {}, {}
    for s, u in listed.items():
        chart = prices.clean(data.load_chart(s) or {}, facts.history(u["cik"]))[0]
        r, how = prices.total_return(chart, day, detail=True)
        if r is not None:
            raw[s] = r
            if how != "adjusted closes":
                hows[s] = how
    vals = sorted(raw.values())
    q = statistics.quantiles(vals, n=100, method="inclusive")
    lo, hi = q[0], q[98]
    wins = {s: min(max(r, lo), hi) for s, r in raw.items()}
    avg = statistics.fmean(wins.values())
    out = {"date": day.isoformat(), "key": key, "listed": len(listed), "with_return": len(raw), "p1": lo, "p99": hi,
           "average": avg, "median": statistics.median(vals), "not_adjusted_closes": hows,
           "stocks": {s: {"raw": raw[s], "rel": wins[s] - avg, "capped": 1 if raw[s] >= hi else -1 if raw[s] <= lo
                          else 0} for s in raw}}
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    return out


_MARKET = {}


def market_at(day):
    """The date's listings and prices (asof.market_state's STATE_DIR/<day>/market.json), read once."""
    if day not in _MARKET:
        with open(os.path.join(STATE_DIR, day, "market.json"), encoding="utf-8") as fh:
            _MARKET[day] = json.load(fh)
    return _MARKET[day]


# ---------------------------------------------------------------------------------------------------------------------
# Statistics.
#
# A group of companies is a list of items (relative return, raw return, capped: see returns_at). Pooled over the dates,
# a statistic takes one such list per date, and every resample of the bootstrap draws within each date as many as that
# date has, so a pooled interval keeps each date's number of companies.

def items(stocks, syms):
    """The items of the listings `syms` that have a return in `stocks` (returns_at's): (relative return, raw return,
    capped, symbol)."""
    return [(stocks[s]["rel"], stocks[s]["raw"], stocks[s].get("capped", 0), s) for s in syms if s in stocks]


def _interval(vals):
    """The 2.5th and 97.5th percentiles of bootstrap values, or None with too few to tell."""
    vals = sorted(v for v in vals if v is not None)
    if len(vals) < BOOTSTRAP // 2:
        return None
    return [vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]]


def _boot_groups(groups, stat):
    """A 95% bootstrap interval of stat(indexes): each resample draws, within each group of indexes (one group per date
    when pooling), as many indexes as the group has, with replacement."""
    groups = [list(g) for g in groups if g]
    if sum(len(g) for g in groups) < 3:
        return None
    rng = random.Random(SEED)
    return _interval(stat([i for g in groups for i in rng.choices(g, k=len(g))]) for _ in range(BOOTSTRAP))


def _mean_ci(groups):
    """The interval of the mean of value groups (one per date), resampled within each group."""
    groups = [sorted(g) for g in groups if g]  # the same companies in any order draw the same resamples
    if sum(len(g) for g in groups) < 2:
        return None
    rng = random.Random(SEED)
    n = sum(len(g) for g in groups)
    return _interval(sum(sum(rng.choices(g, k=len(g))) for g in groups) / n for _ in range(BOOTSTRAP))


def _trimmed(vals):
    """The mean without the single largest value either way (None under 3 values)."""
    if len(vals) < 3:
        return None
    i = max(range(len(vals)), key=lambda j: abs(vals[j]))
    return (sum(vals) - vals[i]) / (len(vals) - 1)


def _trimmed_ci(groups):
    """The interval of the trimmed mean (_trimmed) of value groups, resampled within each group; each resample leaves
    out its own largest value."""
    groups = [sorted(g) for g in groups if g]
    if sum(len(g) for g in groups) < 3:
        return None
    rng = random.Random(SEED)
    return _interval(_trimmed([v for g in groups for v in rng.choices(g, k=len(g))]) for _ in range(BOOTSTRAP))


def _median_ci(groups):
    groups = [sorted(g) for g in groups if g]
    if sum(len(g) for g in groups) < 2:
        return None
    rng = random.Random(SEED)
    return _interval(statistics.median([v for g in groups for v in rng.choices(g, k=len(g))])
                     for _ in range(BOOTSTRAP))


def group_summary(groups, medians=None):
    """n, mean and median relative return with bootstrap intervals, the share beating the median stock (with the
    date's median raw return in `medians`, one per group), the same mean without the returns capped at the
    winsorizing percentiles ("n_capped", "mean_uncapped" and its interval), and the mean without the group's single
    largest return either way ("mean_trimmed", its interval, and "trimmed_out": that company and its return), so that
    neither a few capped outliers nor one large return inside the caps can carry a small list. `groups` holds one list
    of items per date (resampled within each)."""
    groups = [list(g) for g in groups]
    every = [x for g in groups for x in g]
    out = {"n": len(every)}
    if not every:
        return out
    rel = [[x[0] for x in g] for g in groups]
    out.update(mean=statistics.fmean(x[0] for x in every), median=statistics.median(x[0] for x in every),
               mean_ci=_mean_ci(rel), median_ci=_median_ci(rel))
    unc = [[x[0] for x in g if not x[2]] for g in groups]
    out["n_capped"] = sum(1 for x in every if x[2])
    if any(unc):
        out["mean_uncapped"] = statistics.fmean(v for g in unc for v in g)
        out["mean_uncapped_ci"] = _mean_ci(unc)
    if len(every) >= 3:
        top = max(every, key=lambda x: abs(x[0]))
        out["mean_trimmed"] = _trimmed([x[0] for x in every])
        out["mean_trimmed_ci"] = _trimmed_ci(rel)
        out["trimmed_out"] = [top[3] if len(top) > 3 else None, top[0]]
    if medians is not None and all(m is not None for m in medians):
        out["beat_median"] = sum(1 for g, m in zip(groups, medians) for x in g if x[1] > m) / len(every)
    return out


def summary(group, median_raw=None):
    """group_summary of one date's group of items."""
    return group_summary([group], None if median_raw is None else [median_raw])


def _weighted_diff(pairs):
    """The weighted average over dates of each date's difference of means, each date weighted by n_a * n_b / (n_a +
    n_b) (the weight that pools a difference within strata): (estimate, weights) of `pairs` of value lists, dates
    with an empty group left out."""
    num = den = 0.0
    for a, b in pairs:
        if a and b:
            w = len(a) * len(b) / (len(a) + len(b))
            num += w * (sum(a) / len(a) - sum(b) / len(b))
            den += w
    return num / den if den else None


def _diff_ci(pairs):
    pairs = [(sorted(a), sorted(b)) for a, b in pairs if a and b]
    if not pairs or sum(len(a) for a, _ in pairs) < 2 or sum(len(b) for _, b in pairs) < 2:
        return None
    rng = random.Random(SEED)
    boots = []
    for _ in range(BOOTSTRAP):
        boots.append(_weighted_diff([(rng.choices(a, k=len(a)), rng.choices(b, k=len(b))) for a, b in pairs]))
    return _interval(boots)


def group_diff(pairs):
    """Group a's mean relative return less group b's, pooled within dates: `pairs` holds one (a items, b items) per
    date, and the estimate is the weighted average of each date's difference (_weighted_diff; a single date gives its
    plain difference), with a 95% interval that resamples each group within each date. Also the same without the
    capped returns ("diff_uncapped", "ci_uncapped") and each date's own difference ("by_date"). None when no date has
    both groups."""
    rel = [([x[0] for x in a], [x[0] for x in b]) for a, b in pairs]
    if not any(a and b for a, b in rel):
        return None
    unc = [([x[0] for x in a if not x[2]], [x[0] for x in b if not x[2]]) for a, b in pairs]
    return {"diff": _weighted_diff(rel), "ci": _diff_ci(rel), "n": [sum(len(a) for a, _ in rel),
                                                                    sum(len(b) for _, b in rel)],
            "dates": sum(1 for a, b in rel if a and b),
            "by_date": [None if not (a and b) else statistics.fmean(a) - statistics.fmean(b) for a, b in rel],
            "diff_uncapped": _weighted_diff(unc), "ci_uncapped": _diff_ci(unc)}


def mean_diff(a, b):
    """group_diff of one date's two groups of items: the mean of `a` less the mean of `b`, with a 95% bootstrap interval
    that resamples each group on its own. None when either is empty."""
    return group_diff([(a, b)])


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return None if not sx or not sy else sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


def spearman_ci(xs, ys, groups=None):
    """Spearman's rank correlation of xs with ys and its 95% bootstrap interval over the pairs (resampled within each
    of `groups`, lists of indexes, where given): {"rho", "ci", "n"}."""
    if len(xs) < 3:
        return {"rho": None, "ci": None, "n": len(xs)}
    stat = lambda idx: spearman([xs[i] for i in idx], [ys[i] for i in idx])
    groups = [sorted(g, key=lambda i: ys[i]) for g in groups or [range(len(xs))]]  # an order every variant shares
    return {"rho": spearman(xs, ys), "ci": _boot_groups(groups, stat), "n": len(xs)}


def spearman_change(a, b, ys, groups=None):
    """How much better score `b` ranks the same companies' returns `ys` than score `a` does: Spearman(b, ys) less
    Spearman(a, ys), with a paired 95% bootstrap interval (each resample of companies scores both)."""
    def stat(idx):
        ra = spearman([a[i] for i in idx], [ys[i] for i in idx])
        rb = spearman([b[i] for i in idx], [ys[i] for i in idx])
        return None if ra is None or rb is None else rb - ra
    if len(ys) < 3:
        return {"n": len(ys), "change": None, "ci": None}
    groups = [sorted(g, key=lambda i: ys[i]) for g in groups or [range(len(ys))]]
    return {"n": len(ys), "a": spearman(a, ys), "b": spearman(b, ys), "change": stat(range(len(ys))),
            "ci": _boot_groups(groups, stat)}


# ---------------------------------------------------------------------------------------------------------------------
# Lists and industries.

def industry_rules(payload):
    """For code whose screener measures industries (the payload's "industries"): the market's median drawdown on the
    date, over every listing with a close and a 52-week high (as the screener measures each industry's listings), and
    the industries each rule marks out of favor: the screener's own (in the payload) and the market-relative one
    (INDUSTRY_GAP). None for code without industries."""
    scr = payload["payload"]
    if not scr.get("industries"):
        return None
    st = market_at(payload["date"])
    dds = []
    for s, p in st["prices"].items():
        price, high = p.get("price") or st["universe"][s].get("price"), p.get("high52")
        if price and high:
            dds.append(1 - price / high)
    mkt = statistics.median(dds)
    inds = scr["industries"]
    return {"market_median_drawdown": mkt, "listings_measured": len(dds), "industries_measured": len(inds),
            "own_threshold": scr["rules"]["out_of_favor_drawdown"], "market_threshold": mkt + INDUSTRY_GAP,
            "marked": {"industries_own": sorted(i["industry"] for i in inds if i.get("out_of_favor")),
                       "industries_vs_market": sorted(i["industry"] for i in inds
                                                      if i["median_drawdown"] >= mkt + INDUSTRY_GAP)}}


def list_members(payload, rules=None):
    """{list key: [symbols]} of every list LIST_NAMES names that the payload's code makes."""
    scr = payload["payload"]
    out = {k: [x["symbol"] for x in scr[k]] for k in LISTS if k in scr}
    if "financials_list" in scr:
        out["financials_list_banks"] = [x["symbol"] for x in scr["financials_list"] if x.get("kind") == "bank"]
        out["financials_list_others"] = [x["symbol"] for x in scr["financials_list"] if x.get("kind") != "bank"]
    rules = rules if rules is not None else industry_rules(payload)
    if rules:
        listed = market_at(payload["date"])["universe"]
        for key in INDUSTRY_RULES:
            bad = set(rules["marked"][key])
            out[key] = [s for s, u in listed.items() if u.get("industry") in bad]
    return {k: out[k] for k in LIST_NAMES if k in out}


# ---------------------------------------------------------------------------------------------------------------------
# One variant on one date.

def evaluate(payload, rets, base=None, flags=None):
    """The date's figures for one variant's payload (asof.run), against `base`'s payload where given. `flags` names
    every flag to measure (by default those some passer has that day; score() gives those of every date, so a flag no
    passer has on one date still counts that date's passers as without it when pooled)."""
    scr = payload["payload"]
    stocks, med = rets["stocks"], rets["median"]
    its = lambda syms: items(stocks, syms)
    rows = scr["results"]
    got = [r for r in rows if r["symbol"] in stocks]
    rel = [stocks[r["symbol"]]["rel"] for r in got]
    out = {"date": payload["date"], "label": payload["label"], "listings": payload.get("listings"),
           "with_figures": payload.get("with_figures"), "passed": len(rows), "passed_with_return": len(got),
           "market": {k: rets[k] for k in ("listed", "with_return", "average", "median", "p1", "p99")},
           "passers": summary(its(r["symbol"] for r in got), med)}
    scores = [r.get("score") for r in got]
    if all(s is not None for s in scores):
        sp = spearman_ci(scores, rel)
        out["score_spearman"], out["score_spearman_ci"] = sp["rho"], sp["ci"]
        ranked = sorted(got, key=lambda r: -r["score"])
        top, rest = its(r["symbol"] for r in ranked[:TOP]), its(r["symbol"] for r in ranked[TOP:])
        out["top10"], out["rest"], out["top10_minus_rest"] = summary(top), summary(rest), mean_diff(top, rest)
    flags = flags if flags is not None else sorted({f for r in rows for f in r.get("flags") or []})
    out["flags"] = {}
    for f in flags:
        w = its(r["symbol"] for r in got if f in (r.get("flags") or []))
        wo = its(r["symbol"] for r in got if f not in (r.get("flags") or []))
        out["flags"][f] = {"with": summary(w), "without": summary(wo), "with_minus_without": mean_diff(w, wo),
                           "flagged": sum(1 for r in rows if f in (r.get("flags") or []))}
    ins = payload.get("insiders")
    if ins is not None:
        # Tier 1 or 2 open-market buying in the 30 days before (form345.py), whether or not the code used it: among the
        # companies that pass, and among every listing.
        buying = sorted(s for s, c in ins.items() if c["tier"] <= 2)  # sorted: a set's order changes between runs
        w, wo = its(r["symbol"] for r in got if r["symbol"] in buying), its(r["symbol"] for r in got
                                                                             if r["symbol"] not in buying)
        out["insider_buying"] = {"passers_with": summary(w), "passers_without": summary(wo),
                                 "with_minus_without": mean_diff(w, wo), "all_listings": summary(its(buying), med)}
    rules = industry_rules(payload)
    out["lists"] = {}
    for key, syms in list_members(payload, rules).items():
        out["lists"][key] = {"size": len(syms), **summary(its(syms), med)}
    if rules:
        # Each rule's industries, and the passers in them against the rest: the screener's own rule is the passers'
        # industry_out_of_favor flag; the market-relative one is worked out here from the same industry medians.
        out["industry_rules"] = {k: v for k, v in rules.items() if k != "marked"}
        for key in INDUSTRY_RULES:
            bad = set(rules["marked"][key])
            out["lists"][key]["industries"] = len(bad)
            out["lists"][key]["of"] = rules["industries_measured"]
            inn = its(r["symbol"] for r in got if r.get("industry") in bad)
            outn = its(r["symbol"] for r in got if r.get("industry") not in bad)
            out["industry_rules"][key] = {"industries": len(bad), "names": sorted(bad),
                                          "passers_flagged": sum(1 for r in rows if r.get("industry") in bad),
                                          "passers_in": summary(inn), "passers_out": summary(outn),
                                          "in_minus_out": mean_diff(inn, outn)}
    buying = {s for s, c in (ins or {}).items() if c["tier"] <= 2}
    out["picks"] = [{"symbol": r["symbol"], "score": r.get("score"), "flags": r.get("flags"),
                     "industry": r.get("industry"),
                     "insider_buying": r["symbol"] in buying if ins is not None else None,
                     "rel": stocks.get(r["symbol"], {}).get("rel"), "raw": stocks.get(r["symbol"], {}).get("raw"),
                     "capped": stocks.get(r["symbol"], {}).get("capped")}
                    for r in rows]
    if base is not None:
        brows = base["payload"]["results"]
        mine = {r["symbol"] for r in rows}
        theirs = {r["symbol"] for r in brows}
        for k, syms in (("added", sorted(mine - theirs)), ("dropped", sorted(theirs - mine))):
            out[k] = {"symbols": syms, "rel": {s: stocks[s]["rel"] for s in syms if s in stocks},
                      **summary(its(syms), med)}
        bsyms = [r["symbol"] for r in brows]
        out["base_passers"] = summary(its(bsyms), med)
        out["added_minus_base_passers"] = mean_diff(its(out["added"]["symbols"]), its(bsyms))
        out["same_passes"] = mine == theirs
        bscore = {r["symbol"]: r.get("score") for r in brows}
        out["score_changes"] = [[r["symbol"], bscore[r["symbol"]], r.get("score")] for r in rows
                                if r["symbol"] in bscore and bscore[r["symbol"]] != r.get("score")]
        out["scores_compared"] = sum(1 for r in rows if r["symbol"] in bscore)
        both = [r for r in got if r["symbol"] in bscore and bscore[r["symbol"]] is not None
                and r.get("score") is not None]
        out["spearman_change"] = spearman_change([bscore[r["symbol"]] for r in both], [r["score"] for r in both],
                                                 [stocks[r["symbol"]]["rel"] for r in both])
        out["base"] = base["label"]
    return out


def pooled(evals, rets_by_date, payloads, bases=None):
    """The same figures over every date together (company-date pairs; each return is already relative to its date's
    average stock). Every interval resamples each date's companies within that date. A mean pools every company-date;
    a difference between two groups (a flag's passers with it and without, the top 10 by score and the rest, the
    companies a variant added and the base's passers, passers in marked industries and elsewhere) pools each date's
    own difference (group_diff), so a date on which every passer is flagged can't make the flag look good or bad by
    that date's market alone."""
    passers, meds = [], []
    top, rest, scores, srel, sgroups = [], [], [], [], []
    flags, lists, insider, ind = {}, {}, {}, {}
    added, dropped, base_items, pa, pb, py, pgroups = [], [], [], [], [], [], []
    for e in evals:
        d = e["date"]
        stocks = rets_by_date[d]["stocks"]
        med = rets_by_date[d]["median"]
        meds.append(med)
        its = lambda syms: items(stocks, syms)
        got = [p for p in e["picks"] if p["rel"] is not None]
        passers.append(its(p["symbol"] for p in got))
        if all(p["score"] is not None for p in got):
            sgroups.append(range(len(scores), len(scores) + len(got)))
            scores += [p["score"] for p in got]
            srel += [p["rel"] for p in got]
            ranked = sorted(got, key=lambda p: -p["score"])
            top.append(its(p["symbol"] for p in ranked[:TOP]))
            rest.append(its(p["symbol"] for p in ranked[TOP:]))
        for f in e["flags"]:
            x = flags.setdefault(f, {"pairs": [], "flagged": 0})
            x["pairs"].append((its(p["symbol"] for p in got if f in (p["flags"] or [])),
                               its(p["symbol"] for p in got if f not in (p["flags"] or []))))
            x["flagged"] += e["flags"][f]["flagged"]
        rules = industry_rules(payloads[d])
        for key, syms in list_members(payloads[d], rules).items():
            x = lists.setdefault(key, {"size": 0, "groups": [], "meds": [], "industries": 0, "of": 0})
            x["size"] += len(syms)
            x["groups"].append(its(syms))
            x["meds"].append(med)
        if rules:
            for key in INDUSTRY_RULES:
                bad = set(rules["marked"][key])
                x = ind.setdefault(key, {"pairs": [], "industries": 0, "of": 0, "flagged": 0})
                x["pairs"].append((its(p["symbol"] for p in got if p.get("industry") in bad),
                                   its(p["symbol"] for p in got if p.get("industry") not in bad)))
                x["industries"] += len(bad)
                x["of"] += rules["industries_measured"]
                x["flagged"] += e["industry_rules"][key]["passers_flagged"]
                lists[key]["industries"] += len(bad)
                lists[key]["of"] += rules["industries_measured"]
        if "insider_buying" in e:
            ib = payloads[d].get("insiders") or {}
            buying = sorted(s for s, c in ib.items() if c["tier"] <= 2)
            x = insider.setdefault("x", {"pairs": [], "all": [], "meds": []})
            x["pairs"].append((its(p["symbol"] for p in got if p["symbol"] in buying),
                               its(p["symbol"] for p in got if p["symbol"] not in buying)))
            x["all"].append(its(buying))
            x["meds"].append(med)
        if "added" in e:
            bsyms = [r["symbol"] for r in bases[d]["payload"]["results"]]
            added.append(its(e["added"]["symbols"]))
            dropped.append(its(e["dropped"]["symbols"]))
            base_items.append(its(bsyms))
            bscore = {r["symbol"]: r.get("score") for r in bases[d]["payload"]["results"]}
            both = [p for p in got if p["symbol"] in bscore and bscore[p["symbol"]] is not None
                    and p["score"] is not None]
            pgroups.append(range(len(py), len(py) + len(both)))
            pa += [bscore[p["symbol"]] for p in both]
            pb += [p["score"] for p in both]
            py += [p["rel"] for p in both]
    out = {"passed": sum(e["passed"] for e in evals), "passers": group_summary(passers, meds)}
    if scores:
        sp = spearman_ci(scores, srel, sgroups)
        out["score_spearman"], out["score_spearman_ci"] = sp["rho"], sp["ci"]
        per = [e.get("score_spearman") for e in evals if e.get("score_spearman") is not None]
        out["score_spearman_mean_of_dates"] = statistics.fmean(per) if per else None
        out["top10"], out["rest"] = group_summary(top), group_summary(rest)
        out["top10_minus_rest"] = group_diff(list(zip(top, rest)))
    out["flags"] = {f: {"with": group_summary([a for a, _ in x["pairs"]]),
                        "without": group_summary([b for _, b in x["pairs"]]),
                        "with_minus_without": group_diff(x["pairs"]), "flagged": x["flagged"]}
                    for f, x in flags.items()}
    out["lists"] = {}
    for k, x in lists.items():
        out["lists"][k] = {"size": x["size"], **group_summary(x["groups"], x["meds"]),
                           **({"industries": x["industries"], "of": x["of"]} if k in INDUSTRY_RULES else {})}
    if ind:
        out["industry_rules"] = {k: {"industries": x["industries"], "of": x["of"], "passers_flagged": x["flagged"],
                                     "passers_in": group_summary([a for a, _ in x["pairs"]]),
                                     "passers_out": group_summary([b for _, b in x["pairs"]]),
                                     "in_minus_out": group_diff(x["pairs"])} for k, x in ind.items()}
    if insider:
        x = insider["x"]
        out["insider_buying"] = {"passers_with": group_summary([a for a, _ in x["pairs"]]),
                                 "passers_without": group_summary([b for _, b in x["pairs"]]),
                                 "with_minus_without": group_diff(x["pairs"]),
                                 "all_listings": group_summary(x["all"], x["meds"])}
    if any("added" in e for e in evals):
        out["added"], out["dropped"] = group_summary(added, meds), group_summary(dropped, meds)
        out["base_passers"] = group_summary(base_items, meds)
        out["added_minus_base_passers"] = group_diff(list(zip(added, base_items)))
        out["same_passes"] = all(e["same_passes"] for e in evals)
        out["score_changes"] = sum(len(e["score_changes"]) for e in evals)
        out["scores_compared"] = sum(e["scores_compared"] for e in evals)
        out["spearman_change"] = spearman_change(pa, pb, py, pgroups)
        out["base"] = evals[0].get("base")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Reports.

def _pts(x):
    return "   n/a" if x is None else f"{100 * x:+6.1f}"


def _ci_txt(ci):
    return "" if not ci else f" [{100 * ci[0]:+.1f}, {100 * ci[1]:+.1f}]"


def _rho(x, ci=None):
    return "n/a" if x is None else f"{x:+.3f}" + ("" if not ci else f" [{ci[0]:+.3f}, {ci[1]:+.3f}]")


def _diff(d):
    return "n/a" if not d or d.get("diff") is None else f"{_pts(d['diff']).strip()}{_ci_txt(d.get('ci'))}"


def _diff_unc(d):
    return "n/a" if not d or d.get("diff_uncapped") is None else \
        f"{_pts(d['diff_uncapped']).strip()}{_ci_txt(d.get('ci_uncapped'))}"


def _line(name, s, width=40):
    if not s or not s.get("n"):
        return f"  {name:<{width}} n=0"
    txt = f"  {name:<{width}} n={s['n']:<4} mean {_pts(s.get('mean'))}{_ci_txt(s.get('mean_ci'))}  " \
          f"median {_pts(s.get('median'))}{_ci_txt(s.get('median_ci'))}"
    if s.get("beat_median") is not None:
        txt += f"  beat median stock {100 * s['beat_median']:.0f}%"
    if s.get("n_capped"):
        txt += f"  capped {s['n_capped']}, mean without them {_pts(s.get('mean_uncapped')).strip()}" \
               f"{_ci_txt(s.get('mean_uncapped_ci'))}"
    if s.get("mean_trimmed") is not None:
        txt += f"  trimmed (without {s['trimmed_out'][0]}) {_pts(s['mean_trimmed']).strip()}" \
               f"{_ci_txt(s.get('mean_trimmed_ci'))}"
    return txt


def report_text(label, evals, pool, meta):
    out = [f"Screener backtest: {label}", "=" * (19 + len(label)), "",
           f"Code root: {meta.get('root')}", f"Overrides: {', '.join(meta.get('sets') or []) or 'none'}",
           "Relative return: 52-week total return, winsorized at each date's 1st/99th percentiles, less the "
           "equal-weighted average listed stock's, in points. Intervals: 95% bootstrap over companies (pooled: "
           "resampled within each date). 'Capped': returns at those percentiles; 'without them': the mean of the rest. "
           "Pooled differences: the weighted average of each date's difference (n1 x n2 / (n1 + n2)).",
           "Analyst counts are not point in time: no company gets 'Few analysts' points or the 'undiscovered' flag.", ""]
    for e in evals + [dict(pool, date="All four dates" if len(evals) == 4 else "All dates")]:
        out.append(f"-- {e['date']} " + "-" * 60)
        if "market" in e:
            m = e["market"]
            out.append(f"  listings {e['listings']}, with figures {e['with_figures']}, with a 52-week return "
                       f"{m['with_return']}; average stock {100 * m['average']:+.1f}%, median stock "
                       f"{100 * m['median']:+.1f}% (winsorized at {100 * m['p1']:+.0f}% / {100 * m['p99']:+.0f}%)")
        out.append(f"  passed: {e['passed']}" + (f" ({e['passed_with_return']} with a return)"
                                                 if "passed_with_return" in e else ""))
        out.append(_line("passers", e["passers"]))
        if e.get("score_spearman") is not None:
            extra = (f" (mean of the dates {e['score_spearman_mean_of_dates']:+.3f})"
                     if e.get("score_spearman_mean_of_dates") is not None else "")
            out.append(f"  score vs relative return, Spearman: {_rho(e['score_spearman'], e.get('score_spearman_ci'))}"
                       f"{extra}")
            out.append(_line(f"top {TOP} by score", e.get("top10")))
            out.append(_line("the rest", e.get("rest")))
            out.append(f"  top {TOP} less the rest: {_diff(e.get('top10_minus_rest'))}; without capped "
                       f"{_diff_unc(e.get('top10_minus_rest'))}")
        for f, d in sorted(e["flags"].items()):
            out.append(_line(f"flag {f}: with", d["with"]))
            out.append(_line(f"flag {f}: without", d["without"]))
            out.append(f"  flag {f}: with less without {_diff(d.get('with_minus_without'))}; without capped "
                       f"{_diff_unc(d.get('with_minus_without'))}")
        if "insider_buying" in e:
            d = e["insider_buying"]
            out.append(_line("insider buying (tier 1-2): passers", d["passers_with"]))
            out.append(_line("no insider buying: passers", d["passers_without"]))
            out.append(f"  insider buying: passers with less without {_diff(d.get('with_minus_without'))}; without "
                       f"capped {_diff_unc(d.get('with_minus_without'))}")
            out.append(_line("insider buying: every listing", d["all_listings"]))
        if e.get("industry_rules"):
            ir = e["industry_rules"]
            if "market_median_drawdown" in ir:
                out.append(f"  industries: the median listing {100 * ir['market_median_drawdown']:.1f}% below its "
                           f"52-week high; {ir['industries_measured']} industries measured; the screener's rule "
                           f"({100 * ir['own_threshold']:.0f}%+) marks {ir['industries_own']['industries']}, the "
                           f"market rule ({100 * ir['market_threshold']:.1f}%+) marks "
                           f"{ir['industries_vs_market']['industries']}")
            for key in INDUSTRY_RULES:
                d = ir[key]
                name = "screener's rule" if key == "industries_own" else "market rule"
                out.append(_line(f"{name}: passers in marked industries", d["passers_in"]))
                out.append(_line(f"{name}: passers elsewhere", d["passers_out"]))
                out.append(f"  {name}: in less elsewhere {_diff(d.get('in_minus_out'))}; without capped "
                           f"{_diff_unc(d.get('in_minus_out'))}")
        for k, d in e["lists"].items():
            name = LIST_NAMES.get(k, k)
            extra = f" ({d['industries']} of {d['of']} industries)" if "industries" in d else ""
            out.append(_line(f"{name}{extra}", d, 60) + f"  (listed {d['size']})")
        if "base" in e:
            out.append(f"  against {e['base']}: same passes {'yes' if e['same_passes'] else 'no'}; scores differ for "
                       f"{len(e['score_changes']) if isinstance(e['score_changes'], list) else e['score_changes']} "
                       f"of {e['scores_compared']} companies both pass")
            if isinstance(e["score_changes"], list) and e["score_changes"]:
                out.append("    " + ", ".join(f"{s} {a}->{b}" for s, a, b in e["score_changes"][:40]))
            for k in ("added", "dropped"):
                syms = e[k].get("symbols")
                out.append(_line(f"{k} vs {e.get('base')}", e[k])
                           + (f"  {', '.join(syms[:25])}{' ...' if len(syms) > 25 else ''}" if syms else ""))
            out.append(_line(f"{e['base']}'s passers", e["base_passers"]))
            out.append(f"  added less {e['base']}'s passers: {_diff(e.get('added_minus_base_passers'))}; without "
                       f"capped {_diff_unc(e.get('added_minus_base_passers'))}")
            sc = e.get("spearman_change") or {}
            if sc.get("change") is not None:
                out.append(f"  Spearman on the {sc['n']} companies both pass: {e['base']} {_rho(sc['a'])}, this "
                           f"{_rho(sc['b'])}, change {_rho(sc['change'], sc['ci'])}")
        if "picks" in e:
            picks = sorted(e["picks"], key=lambda p: -(p["score"] or 0))
            out.append("  picks (score, relative return): " + ", ".join(
                f"{p['symbol']} {p['score']} {_pts(p['rel']).strip()}" for p in picks))
        out.append("")
    return "\n".join(out)


def _paths(label, day):
    return (os.path.join(RESULTS_DIR, f"{label}_{day}.payload.json"), os.path.join(RESULTS_DIR, f"{label}_{day}.json"))


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(path + ".tmp", path)


def score(label, dates, base=None):
    """Evaluates a variant's payloads on `dates` (against `base`'s where given), writes RESULTS_DIR/<label>_<date>.json,
    <label>_pooled.json and the report <label>.txt (compare_<base>_vs_<label>.json/.txt with a base). Returns (report
    text, the dates' evaluations, the pooled figures)."""
    evals, rets, payloads, bases = [], {}, {}, {}
    for d in dates:
        payloads[d] = _load(_paths(label, d)[0])
    flags = sorted({f for d in dates for r in payloads[d]["payload"]["results"] for f in r.get("flags") or []})
    for d in dates:
        ep = _paths(label, d)[1]
        rets[d] = returns_at(dt.date.fromisoformat(d))
        bases[d] = _load(_paths(base, d)[0]) if base else None
        e = evaluate(payloads[d], rets[d], bases[d], flags)
        if not base:
            _dump(ep, e)
        evals.append(e)
    pool = pooled(evals, rets, payloads, bases if base else None)
    meta = {"root": payloads[dates[0]]["root"], "sets": payloads[dates[0]]["sets"]}
    name = f"compare_{base}_vs_{label}" if base else label
    if base:
        _dump(os.path.join(RESULTS_DIR, f"{name}.json"),
              {"dates": {e["date"]: {k: v for k, v in e.items() if k != "picks"} for e in evals},
               "pooled": pool, **meta})
    else:
        _dump(os.path.join(RESULTS_DIR, f"{label}_pooled.json"), {**pool, **meta, "dates": dates})
    text = report_text(f"{label} (against {base})" if base else label, evals, pool, meta)
    with open(os.path.join(RESULTS_DIR, f"{name}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return text, evals, pool


def chain_text(labels, steps, own):
    """The summary of a chain of variants: for each step (a variant against the one before it), the passes, the
    companies added and dropped and how they did beside the earlier variant's passers, and how the score ranks the
    passers' returns before and after, per date and pooled. `steps` is [(base, label, evals, pool)], `own` {label:
    (evals, pool)} from each variant's own scoring."""
    out = [f"Screener backtest chain: {' -> '.join(labels)}", "",
           "Relative return: 52-week total return, winsorized at each date's 1st/99th percentiles, less the "
           "equal-weighted average listed stock's, in points. Intervals: 95% bootstrap over companies (pooled: "
           "resampled within each date; a pooled difference is the weighted average of each date's difference). "
           "Spearman: rank correlation of the score with the relative return among the companies that pass.", ""]
    for base, label, evals, pool in steps:
        out.append(f"== {base} -> {label} " + "=" * 50)
        rows = evals + [dict(pool, date="pooled")]
        out.append(f"  {'date':<11} {'passes':>9} {'added':>5} {'dropped':>7} {'same':>5} {'scores differ':>14}   "
                   f"added: mean rel [CI]         {base} passers: mean rel [CI]   added less those [CI]")
        bown, lown = own[base], own[label]
        for e in rows:
            d = e["date"]
            bp = next((x["passed"] for x in bown[0] if x["date"] == d), bown[1]["passed"])
            n_add = len(e["added"]["symbols"]) if "symbols" in e["added"] else e["added"]["n"]
            n_drop = len(e["dropped"]["symbols"]) if "symbols" in e["dropped"] else e["dropped"]["n"]
            sc = len(e["score_changes"]) if isinstance(e["score_changes"], list) else e["score_changes"]
            differ = f"{sc} of {e['scores_compared']}"
            a, b = e["added"], e["base_passers"]
            out.append(f"  {d:<11} {bp:>4}->{e['passed']:<4} {n_add:>5} {n_drop:>7} "
                       f"{'yes' if e['same_passes'] else 'no':>5} {differ:>14}   "
                       f"n={a.get('n', 0):<3} {_pts(a.get('mean'))}{_ci_txt(a.get('mean_ci')):<16} "
                       f"n={b.get('n', 0):<3} {_pts(b.get('mean'))}{_ci_txt(b.get('mean_ci')):<16} "
                       f"{_diff(e.get('added_minus_base_passers'))}")
        for e in evals:
            if e["added"]["symbols"]:
                out.append(f"    added {e['date']}: " + ", ".join(
                    f"{s} {_pts(e['added']['rel'].get(s)).strip()}" for s in e["added"]["symbols"]))
            if e["dropped"]["symbols"]:
                out.append(f"    dropped {e['date']}: " + ", ".join(
                    f"{s} {_pts(e['dropped']['rel'].get(s)).strip()}" for s in e["dropped"]["symbols"]))
        out.append("")
        out.append(f"  {'date':<11} {'n':>4}  Spearman {base:<26} Spearman {label:<26} change on common [CI]"
                   f"             top {TOP} less rest: {base} / {label}")
        own_b = {x["date"]: x for x in bown[0]}
        own_l = {x["date"]: x for x in lown[0]}
        own_b["pooled"], own_l["pooled"] = bown[1], lown[1]
        for e in rows:
            d = e["date"]
            sc = e.get("spearman_change") or {}
            xb, xl = own_b[d], own_l[d]
            rb = _rho(xb.get("score_spearman"), xb.get("score_spearman_ci"))
            rl = _rho(xl.get("score_spearman"), xl.get("score_spearman_ci"))
            out.append(f"  {d:<11} {xl['passers']['n']:>4}  {rb:<35} {rl:<35} "
                       f"{_rho(sc.get('change'), sc.get('ci')):<33} "
                       f"{_diff(xb.get('top10_minus_rest'))} / {_diff(xl.get('top10_minus_rest'))}")
        out.append("")
    return "\n".join(out)


def chain(labels, dates):
    """Each variant against the one before it (compare files for each step) and the chain's summary."""
    own = {}
    for lab in labels:
        _, evals, pool = score(lab, dates)
        own[lab] = (evals, pool)
    steps = []
    for base, lab in zip(labels, labels[1:]):
        _, evals, pool = score(lab, dates, base=base)
        steps.append((base, lab, evals, pool))
    text = chain_text(labels, steps, own)
    with open(os.path.join(RESULTS_DIR, f"chain_{'_'.join(labels)}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return text


def _m(s):
    """'mean [interval]' of a summary, in points."""
    return "n/a" if not s or not s.get("n") else f"{_pts(s.get('mean')).strip()}{_ci_txt(s.get('mean_ci'))}"


def _md(s):
    return "n/a" if not s or not s.get("n") else _pts(s.get("median")).strip()


def _unc(s):
    """'mean without the capped returns [interval] (how many capped)', or the plain mean's figure where none is."""
    if not s or not s.get("n"):
        return "n/a"
    if not s.get("n_capped"):
        return "none capped"
    if s.get("mean_uncapped") is None:
        return f"all {s['n_capped']} capped"
    return f"{_pts(s['mean_uncapped']).strip()}{_ci_txt(s.get('mean_uncapped_ci'))} ({s['n_capped']} capped)"


def _trim(s):
    """'trimmed mean [interval] (without the company left out)'."""
    if not s or not s.get("n"):
        return "n/a"
    if s.get("mean_trimmed") is None:
        return "under 3"
    return f"{_pts(s['mean_trimmed']).strip()}{_ci_txt(s.get('mean_trimmed_ci'))} (without {s['trimmed_out'][0]})"


def _beat(s):
    return "n/a" if not s or s.get("beat_median") is None else f"{100 * s['beat_median']:.0f}%"


README = os.path.join(ROOT, "backtest", "README.md")
README_BEGIN = "<!-- results:begin"
README_END = "<!-- results:end -->"


def tables(labels, dates):
    """The README's result tables, in Markdown, from the JSON files `run` and `compare --chain` wrote for `labels`
    (in chain order; the last one is the variant whose flags, lists and industries are tabled)."""
    own = {lab: ([_load(_paths(lab, d)[1]) for d in dates], _load(os.path.join(RESULTS_DIR, f"{lab}_pooled.json")))
           for lab in labels}
    steps = {(a, b): _load(os.path.join(RESULTS_DIR, f"compare_{a}_vs_{b}.json")) for a, b in zip(labels, labels[1:])}
    rows = lambda lab: [(e["date"], e) for e in own[lab][0]] + [("pooled", own[lab][1])]
    out = ["Returns are in points against the average listed stock; intervals are 95%. \"Capped\" counts the returns "
           "at the 1st or 99th percentile the returns are winsorized at, and \"without capped\" is the mean of the "
           "rest: a return inside the caps stays in, however large (BTCS's +270 points in 2024 is under the 99th "
           "percentile). \"Trimmed\" is the mean without the group's single largest return either way (the company "
           "left out is named), so that no one company carries a small group. Pooled means count each company once for "
           "each date it is in the group, with intervals that resample each date's companies within that date; a "
           "pooled difference between two groups is the weighted average of each date's own difference (each date "
           "weighted by n1 x n2 / (n1 + n2) of its two groups), with the same resampling.", "",
           "### Passes and how they did", "",
           "| variant | date | passes | mean relative return [95% CI] | median | without capped | trimmed | "
           "beat the median stock |",
           "|---|---|---:|---|---:|---|---|---:|"]
    # A variant that passes the same companies on every date as the one before it has the same figures: named once.
    same = [lab for i, lab in enumerate(labels) if i and steps[(labels[i - 1], lab)]["pooled"]["same_passes"]]
    for lab in labels:
        if lab in same:
            continue
        for d, e in rows(lab):
            out.append(f"| {lab} | {d} | {e['passed']} | {_m(e['passers'])} | {_md(e['passers'])} | "
                       f"{_unc(e['passers'])} | {_trim(e['passers'])} | {_beat(e['passers'])} |")
    if same:
        out += ["", f"{', '.join(same)}: the same companies on every date as the variant before each, so the same "
                    "figures."]
    if steps:
        out += ["", "### Each step against the one before it", "",
                "| step | date | passes | added | dropped | same passes | scores that differ | added: mean [CI] | "
                "added: median | added: trimmed | earlier passers: mean [CI] | added less earlier [CI] | "
                "the same without capped [CI] |",
                "|---|---|---|---:|---:|---|---|---|---:|---|---|---|---|"]
    for (a, b), c in steps.items():
        for d, e in [(d, c["dates"][d]) for d in dates] + [("pooled", c["pooled"])]:
            before = next((x["passed"] for x in own[a][0] if x["date"] == d), own[a][1]["passed"])
            n_add = len(e["added"]["symbols"]) if "symbols" in e["added"] else e["added"]["n"]
            n_drop = len(e["dropped"]["symbols"]) if "symbols" in e["dropped"] else e["dropped"]["n"]
            sc = len(e["score_changes"]) if isinstance(e["score_changes"], list) else e["score_changes"]
            same_p = "yes" if e["same_passes"] else "no"
            out.append(f"| {a} -> {b} | {d} | {before} -> {e['passed']} | {n_add} | {n_drop} | {same_p} | "
                       f"{sc} of {e['scores_compared']} | {_m(e['added'])} | {_md(e['added'])} | {_trim(e['added'])} | "
                       f"{_m(e['base_passers'])} | {_diff(e.get('added_minus_base_passers'))} | "
                       f"{_diff_unc(e.get('added_minus_base_passers'))} |")
    out += ["", "### How the score ranks the passers' returns", "",
            "Spearman of the score with the relative return among the companies that pass; the top 10 by score "
            "less the rest, in points. The change is on the same companies, with a paired interval.", "",
            "| variant | date | passes | Spearman [CI] | change from the step before [CI] | "
            "top 10 less the rest [CI] | the same without capped [CI] |",
            "|---|---|---:|---|---|---|---|"]
    for i, lab in enumerate(labels):
        for d, e in rows(lab):
            ch = ""
            if i:
                c = steps[(labels[i - 1], lab)]
                x = c["dates"][d] if d != "pooled" else c["pooled"]
                sc = x.get("spearman_change") or {}
                # Against a variant that passed other companies, the scores of those both pass are compared.
                ch = _rho(sc.get("change"), sc.get("ci")) + ("" if x["same_passes"] else
                                                             f" (on the {sc.get('n')} both pass)")
            out.append(f"| {lab} | {d} | {e['passed']} | {_rho(e.get('score_spearman'), e.get('score_spearman_ci'))} | "
                       f"{ch} | {_diff(e.get('top10_minus_rest'))} | {_diff_unc(e.get('top10_minus_rest'))} |")
    last = labels[-1]
    if own[last][1]["flags"]:
        out += ["", f"### Flags ({last})", "",
                "| flag | date | passers with it | with: mean [CI] | with: median | with: without capped | "
                "with: trimmed | without: mean [CI] | without: median | with less without [CI] | "
                "the same without capped [CI] |",
                "|---|---|---:|---|---:|---|---|---|---:|---|---|"]
    for f in sorted(own[last][1]["flags"]):
        for d, e in rows(last):
            x = e["flags"].get(f)
            if x:
                out.append(f"| {f} | {d} | {x['flagged']} | {_m(x['with'])} | {_md(x['with'])} | {_unc(x['with'])} | "
                           f"{_trim(x['with'])} | {_m(x['without'])} | {_md(x['without'])} | "
                           f"{_diff(x.get('with_minus_without'))} | {_diff_unc(x.get('with_minus_without'))} |")
    ins = own[last][1].get("insider_buying")
    if ins:
        out += ["", "Every listing with tier 1 or 2 insider buying in the 30 days before, passing or not: "
                    f"{ins['all_listings']['n']} company-dates, mean {_m(ins['all_listings'])}, median "
                    f"{_md(ins['all_listings'])}, without capped {_unc(ins['all_listings'])}, trimmed "
                    f"{_trim(ins['all_listings'])}, {_beat(ins['all_listings'])} beat the median stock."]
    keys = [k for k in LIST_NAMES if k in own[last][1]["lists"]]
    if keys:
        out += ["", f"### Watch lists ({last})", "",
                "| list | date | listed | with a return | mean [CI] | median | without capped | trimmed | "
                "beat the median stock |",
                "|---|---|---:|---:|---|---:|---|---|---:|"]
    for k in keys:
        for d, e in rows(last):
            x = e["lists"].get(k)
            if x:
                extra = f" ({x['industries']} of {x['of']} industries)" if "industries" in x else ""
                out.append(f"| {LIST_NAMES[k].strip()}{extra} | {d} | {x['size']} | {x['n']} | {_m(x)} | {_md(x)} | "
                           f"{_unc(x)} | {_trim(x)} | {_beat(x)} |")
    if any(e.get("industry_rules") for _, e in rows(last)):
        out += ["", f"### Industries out of favor: the screener's rule against the market rule ({last})", "",
                "| date | market's median listing below its high | industries measured | rule | its threshold | "
                "industries marked | passers in them | in: mean [CI] | elsewhere: mean [CI] | in less elsewhere [CI] | "
                "the same without capped [CI] |",
                "|---|---:|---:|---|---:|---:|---:|---|---|---|---|"]
    for d, e in rows(last):
        ir = e.get("industry_rules") or {}
        for key, name, thr_key in (("industries_own", "screener's (25%+ below high)", "own_threshold"),
                                   ("industries_vs_market", f"market ({100 * INDUSTRY_GAP:.0f}+ pts below the market)",
                                    "market_threshold")):
            x = ir.get(key)
            if not x:
                continue
            mkt = f"{100 * ir['market_median_drawdown']:.1f}%" if "market_median_drawdown" in ir else ""
            thr = f"{100 * ir[thr_key]:.1f}%" if thr_key in ir else ""
            of = ir.get("industries_measured", x.get("of"))
            out.append(f"| {d} | {mkt} | {of} | {name} | {thr} | {x['industries']} | {x['passers_flagged']} | "
                       f"{_m(x['passers_in'])} | {_m(x['passers_out'])} | {_diff(x.get('in_minus_out'))} | "
                       f"{_diff_unc(x.get('in_minus_out'))} |")
    return "\n".join(out)


def write_readme(text, labels):
    """Puts `text` (tables) into backtest/README.md between its results markers, in place of what was there."""
    with open(README, encoding="utf-8") as fh:
        doc = fh.read()
    i, j = doc.find(README_BEGIN), doc.find(README_END)
    if i < 0 or j < i:
        raise SystemExit(f"{README} has no {README_BEGIN} ... {README_END} markers")
    import textwrap
    cmd = f"python -m backtest.screener tables {' '.join(labels)} --readme"
    # Prose wrapped at the README's 120 columns; table rows and headings as they are.
    wrap = lambda line: textwrap.fill(line, 120, break_on_hyphens=False, break_long_words=False)
    body = "\n".join(line if line.startswith(("|", "#")) or len(line) <= 120 else wrap(line)
                     for line in text.split("\n"))
    intro = wrap(f"The tables below are the output of `{cmd}`, over the variants {', '.join(labels)}.")
    block = f"{README_BEGIN} (written by `{cmd}`; edit the code, not this block) -->\n\n{intro}\n\n{body}\n\n"
    with open(README + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(doc[:i] + block + doc[j:])
    os.replace(README + ".tmp", README)


def run_variant(code, label, dates, sets, keep=False, metrics_only=False):
    """Runs a variant on each date, one process per date (asof.py), adding tags to the facts index when its code asks
    for new ones, then scores it. With `metrics_only`, each date stops once its metrics are worked out and kept, and
    nothing is scored: a later run of any variant with the same metrics code then takes seconds."""
    from . import asof, facts
    # Absolute, since each date's process runs in CACHE: "--code ." means the folder the command was given in.
    root = os.path.abspath(code) if os.path.isdir(code) else code_root(code)
    if not os.path.isfile(os.path.join(root, "pipeline", "__init__.py")):
        raise SystemExit(f"{root} holds no pipeline package")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    for d in dates:
        pp, _ = _paths(label, d)
        if keep and os.path.exists(pp):
            continue
        for attempt in range(3):
            logp = os.path.join(RESULTS_DIR, f"{label}_{d}.log")
            cmd = [sys.executable, "-c", BOOT.format(root=ROOT), "--code", root, "--date", d, "--label", label]
            for s in sets:
                cmd += ["--set", s]
            if metrics_only:
                cmd.append("--metrics-only")
            started = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] {label} {d}: running (log {logp})", flush=True)
            with open(logp, "a", encoding="utf-8") as fh:
                rc = subprocess.run(cmd, cwd=CACHE, stdout=fh, stderr=subprocess.STDOUT).returncode
            if rc == asof.EXIT_MISSING_TAGS:
                with open(os.path.join(STATE_DIR, d, "missing_tags.json"), encoding="utf-8") as fh:
                    facts.build_index(json.load(fh))
                continue
            if rc:
                raise SystemExit(f"{label} {d} failed (exit {rc}); see {logp}")
            print(f"  done in {time.time() - started:.0f}s", flush=True)
            break
    if metrics_only:
        return f"metrics kept for {label} on {', '.join(dates)}"
    return score(label, dates)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--code", required=True, help="a folder holding a pipeline package, or a commit")
    r.add_argument("--label", required=True)
    r.add_argument("--dates", nargs="+", default=DATES)
    r.add_argument("--set", action="append", default=[], help="module.ATTR=<json> or module.ATTR[key]=<json>")
    r.add_argument("--keep", action="store_true", help="reuse payloads already written for this label")
    r.add_argument("--metrics-only", action="store_true",
                   help="only work out and keep each date's metrics for this code (no screener, no scoring)")
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("others", nargs="+")
    c.add_argument("--dates", nargs="+", default=DATES)
    c.add_argument("--chain", action="store_true", help="measure each variant against the one before it")
    p = sub.add_parser("report")
    p.add_argument("label")
    p.add_argument("--dates", nargs="+", default=DATES)
    t = sub.add_parser("tables", help="the README's result tables from a chain already compared")
    t.add_argument("labels", nargs="+")
    t.add_argument("--dates", nargs="+", default=DATES)
    t.add_argument("--readme", action="store_true",
                   help="also put the tables into backtest/README.md between its results markers")
    args = ap.parse_args()
    if args.cmd == "run":
        print(run_variant(args.code, args.label, args.dates, args.set, args.keep, args.metrics_only))
    elif args.cmd == "compare" and args.chain:
        print(chain([args.base] + args.others, args.dates))
    elif args.cmd == "compare":
        for o in args.others:
            print(score(o, args.dates, base=args.base)[0])
    elif args.cmd == "tables":
        text = tables(args.labels, args.dates)
        with open(os.path.join(RESULTS_DIR, f"tables_{'_'.join(args.labels)}.md"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        if args.readme:
            write_readme(text, args.labels)
        print(text)
    else:
        print(score(args.label, args.dates)[0])


if __name__ == "__main__":
    main()
