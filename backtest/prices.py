"""Prices and market values on a past date, and the 52 weeks' return after it, from the Yahoo weekly charts (data.py).

Yahoo's weekly bar is labeled with its week's Monday and closes on the week's last trading day, so the bar of the week
holding date D (a Friday) closes on D. Its "close" is adjusted for every stock split since, and its "adjclose" for
splits and dividends. On D itself nobody knew of the splits made after it, so the price the pipeline would have seen is
the close times every split ratio after D (a 10-for-1 split in June 2024 makes a September 2022 close of $12.16 the
$121.60 traded that day).

That holds only where each split Yahoo lists happened, on the day it lists, and Yahoo has applied it to the closes
before it, and where Yahoo has made no adjustment it doesn't list. clean() is a gate that checks every chart for this
and corrects it where it can (see clean): a split Yahoo lists but has not applied, one it lists on the wrong week, one
that never happened, and adjustments it made to the closes without listing anything (a spin-off, a reverse split). It
weighs four independent records against each other: the digits of the closes themselves (a price as traded is a whole
number of cents), the SEC's public float (the market value of the non-affiliates' shares on a stated day), the share
counts the company's filings give on either side of the split (and restate after it), and the jump the closes make
around the split. The first three never depend on how the stock moved.

The market value on D is a share count filed with the SEC before D times D's price (market_value, share_count): never
a count or a price from after D, so whether a listing can be screened depends on nothing that happened since.
"""
import datetime as dt
import math
import statistics

DAY = dt.timedelta(days=1)
WEEK = 7 * DAY

# ---------------------------------------------------------------------------------------------------------------------
# The price gate: splits Yahoo lists but has not applied (or has applied twice), lists on the wrong week, or lists
# though they never happened, and adjustments it made without listing them.

# Splits of at least this ratio either way are checked (a 3-for-2 split, a 1-for-1.5 reverse split). Smaller ones are
# stock dividends and spin-offs Yahoo files as splits (1.01 to 1.33), which a week's price move can hide.
SPLIT_CHECKED = 1.5
# The closes test (split_test): x, the move from the last close before a week to the close of the week, as a share of
# the move the split itself would make if Yahoo had not applied it (log close after / close before, over -log ratio).
# x near 0: applied, the closes agree with the split. Near 1: not applied, the closes before it are as traded. Near -1,
# for a split Yahoo lists twice in a week: applied twice. A price move in the week can pull x either way, so x must sit
# closer to one of these than to the next by SPLIT_MARGIN in log terms (at most SPLIT_MARGIN_SHARE of the split's own
# log ratio, for small ratios), and within SPLIT_MAX of the split's move past "not applied": then x below 0.5 less the
# margin is "applied" (however far below 0: reverse splits are often followed by heavy selling), above 0.5 plus the
# margin "not applied". A test that fits none is unclear.
SPLIT_MARGIN = 0.25
SPLIT_MARGIN_SHARE = 0.3
SPLIT_MAX = 0.5
TWIN_DAYS = 7  # two splits of the same ratio this close together are one split Yahoo lists twice
# A split Yahoo has not applied shows as a jump in the closes. It is looked for in the weeks from JUMP_WEEKS before the
# split's listed week to JUMP_WEEKS after it (Offerpad's 1-for-10 reverse split is listed on June 9, 2026, a week after
# its closes jump from $0.77 to $6.17), and a jump must show both from one close to the next and between the median
# closes of the LEVEL_WEEKS weeks either side, so that a spike of a week or two after a split Yahoo applied (Cingulate's
# of August 2024: $3.73 to $17.69, then $9.69, $7.89 and $5.92) isn't read as one.
JUMP_WEEKS = 3
LEVEL_WEEKS = 3
# A bar right at a split (its week or the next two) whose close sits off both its neighbours by the split's ratio is a
# close Yahoo left unadjusted (SILO's week of September 12, 2022: $2.55 between $149.25 and $107.45 after a 1-for-50
# reverse split): it is divided by the ratio, when it fits the ratio within GLITCH_TOL of its size.
GLITCH_TOL = 0.35
GLITCH_RUN = 2
# The tick test (grid_test): a US stock at $1 or more is quoted in whole cents, so its closes as traded are whole cents
# (an odd midpoint trade aside) and a close Yahoo has adjusted by a ratio it lists is a whole number of cents only once
# the ratio is taken back out. For a split, the closes of the GRID_WEEKS weeks before it are priced both ways, applied
# and not applied; of two readings one of which prices the closes as whole multiples of the other's (a 1-for-n reverse
# split applied, against not applied), the finer one is right if at least GRID_FIT of its closes of $1 or more are whole
# cents (and the chance of that under the coarser reading, 1 in n a close, is under GRID_TAIL), or if the coarser one
# puts GRID_MISS or fewer of its closes of $1 or more on whole cents while the finer one doesn't fail as well; the
# coarser one is right if it fits and the finer one fails. Each needs GRID_MIN closes of $1 or more. Of two readings
# neither of which is a multiple of the other (a 374-for-1000 split), the one that fits is right where the other fails.
# Artelo Biosciences' closes before its 1-for-3 reverse split of March 10, 2026 are all multiples of 27 cents (the
# split and the 1-for-9 after it taken back out, whole cents of $1.14 to $1.95): Yahoo applied it, though the closes
# then jumped from $33.84 to $71.73 the next week.
GRID_WEEKS = 26
GRID_MIN = 8
GRID_FIT = 0.8
GRID_MISS = 0.5
GRID_TAIL = 1e-3
# The float test: the public float (the market value of the shares held by non-affiliates on a stated day, from the
# company's 10-K) against the market value the chart gives that day, a share count dated within COUNT_NEAR_DAYS of it
# times the price then, as traded: R = float / (count x price). R is the non-affiliates' share of the company, so it
# is at most about 1; over every listing's floats from 2021 to 2025 its median is 0.91 and 90% lie between 0.32 and 1.04.
# A reading of a chart that puts R between FLOAT_TYPICAL is a typical one; below FLOAT_PLAUSIBLE[0] or above
# FLOAT_PLAUSIBLE[1] an implausible one. A float given in thousands of dollars (R from FLOAT_SLIP[0] to FLOAT_SLIP[1])
# is read in thousands; an R beyond that, or under FLOAT_FLOOR, is a figure in the wrong unit and isn't used.
FLOAT_TYPICAL = (0.25, 1.2)
FLOAT_PLAUSIBLE = (0.1, 1.35)
FLOAT_SLIP = (150.0, 2500.0)
FLOAT_FLOOR = 0.004
COUNT_NEAR_DAYS = 120
# The counts test (shares_test): a split changes every share count, and the filings after it restate the counts they
# repeat for dates before it (GAAP adjusts share counts for a split retroactively). A count a filing after the split
# gives for a date before it, against what the filings before the split gave for that date: changed by the split's
# ratio (within RESTATED_TOL), the split happened; unchanged in RESTATED_MIN filings or more and changed in none, it
# didn't (Sturm Ruger's counts after its listed 374-for-1000 split of October 24, 2025 repeat the earlier ones). Without
# such a count, the counts dated within STRADDLE_DAYS either side of the split: their ratio closer to the split's than
# to 1, by a quarter of the split's log ratio, or closer to 1 by as much.
RESTATED_TOL = 1.05
RESTATED_MIN = 2
STRADDLE_DAYS = 200
# The float pass for adjustments Yahoo made without listing them (find_adjustments): where every float before some day
# is implausible and those after it are not (and at least one is typical), by a level error of ADJUST_MIN or more
# either way, the closes before were scaled by Yahoo with no event listed (LGL Group's before the spin-off of M-tron on
# October 7, 2022; SITE Centers' before that of Curbline on October 1, 2024; Curis's before its 1-for-20 reverse split
# of September 2023). Its week is found from the digits of the closes (the week from which they are whole cents as
# they stand), its exact factor from the closes before it (the one factor that makes them whole cents), else from the
# floats. A cover-page count that falls by the factor (a whole one, within COUNT_SPLIT_TOL) between the floats makes it
# a reverse split (share counts are carried through it), else it is an adjustment to prices only (a spin-off).
ADJUST_MIN = 1.5
ADJUST_SPREAD = 1.6   # the floats on the wrong side must agree with each other within this factor
COUNT_SPLIT_TOL = 0.1
# An unlisted split Yahoo has not applied either (find_unlisted_jumps): consecutive cover-page counts that fall by a
# whole factor n of UNLISTED_MIN or more (within UNLISTED_TOL) within STRADDLE_DAYS, with no split listed within
# UNLISTED_DAYS of them, and a jump of the closes by n between them, both from one close to the next and between the
# median closes either side, within UNLISTED_JUMP of n in log terms (a cover-page count halving at a SPAC's redemptions
# beside a doubling of its price is no split): the closes before the jump are divided by 1/n and the split is listed
# at the jump's week.
UNLISTED_MIN = 3
UNLISTED_TOL = 0.1
UNLISTED_DAYS = 45
UNLISTED_JUMP = 0.2


def _day(ts, offset):
    return dt.datetime.fromtimestamp(ts + offset, dt.timezone.utc).date()


def _monday(day):
    return day - day.weekday() * DAY


def _ts(day, offset):
    """A timestamp that _day reads as `day` (the morning of it in the exchange's time)."""
    return int(dt.datetime(day.year, day.month, day.day, 14, tzinfo=dt.timezone.utc).timestamp()) - offset - 4 * 3600


def weeks(chart):
    """{Monday of the week: (timestamp, close, adjusted close)} of the chart's weekly bars with a close, the last bar of
    a week winning (Yahoo can add a partial bar for the current week)."""
    out = {}
    off = chart.get("gmtoffset") or 0
    adj = chart.get("adjclose") or []
    for i, (ts, c) in enumerate(zip(chart.get("ts") or [], chart.get("close") or [])):
        if c is None or ts is None or c <= 0:
            continue
        d = _day(ts, off)
        a = adj[i] if i < len(adj) else None
        out[_monday(d)] = (ts, c, a)
    return out


def splits(chart):
    """[(date, ratio)] of the chart's stock splits, oldest first (a 2-for-1 split is 2.0)."""
    off = chart.get("gmtoffset") or 0
    return sorted((_day(ts, off), num / den) for ts, num, den in chart.get("splits") or [] if num > 0 and den > 0)


def adjustments(chart):
    """[(date, factor)] of the adjustments to prices only that clean() found Yahoo made without listing them (a
    spin-off): the closes before `date` are the prices as traded divided by `factor`."""
    off = chart.get("gmtoffset") or 0
    return sorted((_day(ts, off), f) for ts, f in chart.get("adjustments") or [])


def split_factor(chart, after, upto=None):
    """The product of the split ratios dated after `after` (and on or before `upto`): what a share count of `after`
    is multiplied by to count the shares of `upto`."""
    return math.prod(r for d, r in splits(chart) if d > after and (upto is None or d <= upto))


def price_factor(chart, after):
    """What a close of `after`'s week is multiplied by to be the price traded then: every split ratio after it, and
    every adjustment to prices only (clean) after it."""
    return split_factor(chart, after) * math.prod(f for d, f in adjustments(chart) if d > after)


def split_test(x, lr, twin=False):
    """How many times Yahoo applied a split to the closes before it (1, 0, or 2 for a split listed twice), from x (see
    SPLIT_MARGIN) and its log ratio's size `lr`; None for unclear."""
    m = min(SPLIT_MARGIN, SPLIT_MARGIN_SHARE * abs(lr)) / (2 * abs(lr))
    if x > 1 + SPLIT_MAX or (twin and x < -1 - SPLIT_MAX):
        return None
    if x >= 0.5 + m:
        return 0
    if not twin:
        return 1 if x <= 0.5 - m else None
    if -0.5 + m <= x <= 0.5 - m:
        return 1
    return 2 if x <= -0.5 - m else None


def _cents(p):
    """Whether a price is a whole number of cents (float32 closes carry about 7 significant digits)."""
    q = p * 100
    return abs(q - round(q)) <= 1e-3 + 2e-7 * q


def _tail(k, n, q):
    """The chance of k or more successes in n trials of chance q."""
    return sum(math.comb(n, i) * q ** i * (1 - q) ** (n - i) for i in range(k, n + 1))


def _integer_ratio(r):
    """n where the ratio is n-for-1 or 1-for-n (else None)."""
    inv = r if r >= 1 else 1 / r
    n = round(inv)
    return n if n >= 2 and abs(inv - n) < 1e-6 * n else None


class _Work:
    """A chart's closes, adjusted closes and dividends as clean() corrects them."""

    def __init__(self, chart):
        self.off = chart.get("gmtoffset") or 0
        self.ts = list(chart.get("ts") or [])
        self.close = list(chart.get("close") or [])
        self.adj = list(chart.get("adjclose") or [])
        self.mondays = [_monday(_day(t, self.off)) if t is not None else None for t in self.ts]

    def weeks(self):
        return weeks({"ts": self.ts, "close": self.close, "adjclose": self.adj, "gmtoffset": self.off})

    def scale(self, before_monday, factor, only=None, closes_only=False):
        for i, m in enumerate(self.mondays):
            if m is None or (only is None and m >= before_monday) or (only is not None and m not in only):
                continue
            if self.close[i] is not None:
                self.close[i] = self.close[i] * factor
            if not closes_only and i < len(self.adj) and self.adj[i] is not None:
                self.adj[i] = self.adj[i] * factor


def _groups(chart):
    """One group per split Yahoo lists, a split listed twice within TWIN_DAYS (same ratio) making one group of two."""
    off = chart.get("gmtoffset") or 0
    groups = []
    for e in sorted(chart.get("splits") or [], key=lambda e: e[0]):
        if not (e[1] > 0 and e[2] > 0):
            continue
        d, r = _day(e[0], off), e[1] / e[2]
        g = groups[-1] if groups else None
        if g and abs(r / g["ratio"] - 1) < 1e-6 and (d - g["dates"][-1]).days <= TWIN_DAYS:
            g["dates"].append(d)
            g["entries"].append(e)
        else:
            groups.append({"ratio": r, "dates": [d], "entries": [e]})
    return groups


def _median_close(w, ks):
    return statistics.median(w[k][1] for k in ks)


def closes_test(w, g):
    """What the closes around a split say (see SPLIT_MARGIN, JUMP_WEEKS): {"verdict": "A" (applied), "U" (not applied)
    or None, "jump": the Monday of the week the closes jump by the split where Yahoo has not applied it, "x": the move
    over the listed week, "twin_k": for a split listed twice, how many times Yahoo applied it}. None where the chart has
    no close before the split (nothing to correct)."""
    keys = sorted(w)
    r, twin = g["ratio"], len(g["dates"]) > 1
    m, m_last = _monday(g["dates"][0]), _monday(g["dates"][-1])
    before = [k for k in keys if k < m]
    after = [k for k in keys if k > m_last] or ([m] if m in w else [])
    if not before:
        return None
    lr = -math.log(r)
    out = {"verdict": None, "jump": None, "x": None, "twin_k": None}
    if not after:
        return out
    x = math.log(w[after[0]][1] / w[before[-1]][1]) / lr
    xa = (math.log(w[after[0]][2] / w[before[-1]][2]) / lr) if w[after[0]][2] and w[before[-1]][2] else x
    out["x"] = round(x, 3)
    k, ka = split_test(x, lr, twin), split_test(xa, lr, twin)
    if twin:
        out["twin_k"] = k if k == ka else None
        return out
    # The jump of a split not applied, in the weeks around the listed one: both one close to the next and the median
    # closes either side.
    jumps = []
    for b in keys:
        if not (m - JUMP_WEEKS * WEEK <= b <= m_last + JUMP_WEEKS * WEEK):
            continue
        pre = [k2 for k2 in keys if k2 < b][-LEVEL_WEEKS:]
        post = [k2 for k2 in keys if k2 >= b][:LEVEL_WEEKS]
        if not pre or not post:
            continue
        x1 = math.log(w[b][1] / w[pre[-1]][1]) / lr
        xm = math.log(_median_close(w, post) / _median_close(w, pre)) / lr
        if split_test(x1, lr) == 0 and split_test(xm, lr) == 0:
            jumps.append((abs(x1 - 1), abs((b - m).days), b, round(x1, 3), round(xm, 3)))
    if jumps:
        _, _, b, x1, xm = min(jumps)
        out.update(verdict="U", jump=b, jump_x=x1, jump_xm=xm)
    elif k == 1 and ka == 1:
        out["verdict"] = "A"
    return out


def grid_test(w, m, r, later, prev=None):
    """What the digits of the closes before a split say (see GRID_WEEKS): "A" (applied), "U" (not applied) or None,
    with the counts behind it. `later` is what the closes are multiplied by for the splits after this one; `prev`, the
    date of the split listed before it, whose week and earlier the closes aren't read from."""
    ks = [k for k in sorted(w) if m - GRID_WEEKS * WEEK <= k < m and (prev is None or k > _monday(prev))]
    prices = {"A": [w[k][1] * r * later for k in ks], "U": [w[k][1] * later for k in ks]}
    fit = {}
    for h, ps in prices.items():
        big = [p for p in ps if p >= 1]
        fit[h] = (len(big), sum(1 for p in big if _cents(p)))
    detail = {h: list(v) for h, v in fit.items()}
    ok = lambda h: fit[h][0] >= GRID_MIN and fit[h][1] >= GRID_FIT * fit[h][0]
    bad = lambda h: fit[h][0] >= GRID_MIN and fit[h][1] <= GRID_MISS * fit[h][0]
    n = _integer_ratio(r)
    if n:
        # A reverse split applied prices the closes lower than not applied (finer); a forward split the other way.
        fine, coarse = ("A", "U") if r < 1 else ("U", "A")
        if ok(fine) and _tail(fit[fine][1], fit[fine][0], 1 / n) < GRID_TAIL:
            return fine, detail
        if bad(coarse) and not bad(fine):
            return fine, detail
        if bad(fine) and ok(coarse):
            return coarse, detail
        return None, detail
    for h, o in (("A", "U"), ("U", "A")):
        if ok(h) and bad(o) and _tail(fit[h][1], fit[h][0], 0.5) < GRID_TAIL:
            return h, detail
    return None, detail


def shares_test(counts, d, d_last, r, others=()):
    """What the share counts say of a split listed on `d` (to `d_last`, for one listed twice): "real", "none" or None,
    with the evidence (see RESTATED_TOL). `counts` is facts.Facts.history's; `others`, the (date, ratio) of the chart's
    other splits, since a filing made after a later split restates for both (TruGolf's 10-K of April 2026 gives its 2024
    diluted count a five-hundredth of what it was, for the 1-for-50 split of June 2025 and the 1-for-10 of March 2026).
    Real: a count for a date before the split restated by its ratio (times the ratios of the later splits made before
    the filing) in a filing after it, or the counts either side apart by about its ratio. None: either side's counts
    level ("straddle"), or, where they aren't apart by the ratio either, RESTATED_MIN counts or more repeated unchanged
    after it and none restated ("restatements")."""
    if not counts:
        return None, {}
    d0, d1 = d.toordinal(), d_last.toordinal()
    pre, post = {}, {}
    for source, start, date, val, filed in counts:
        if date >= d0:
            continue
        key = (source, start, date)
        if filed < d0:
            if key not in pre or filed >= pre[key][1]:
                pre[key] = (val, filed)
        elif filed > d1:
            post.setdefault(key, []).append((val, filed))
    restated = unchanged = 0
    for key, vals in post.items():
        if key not in pre or pre[key][0] < MIN_SHARES:
            continue
        for v, filed in vals:
            q = v / pre[key][0]
            later = math.prod(rr for dd, rr in others if d1 < dd.toordinal() <= filed)
            if abs(math.log(q) - math.log(r * later)) <= math.log(RESTATED_TOL):
                restated += 1
            elif abs(math.log(q)) <= math.log(RESTATED_TOL):
                unchanged += 1
    ev = {"restated": restated, "unchanged": unchanged}
    if restated:
        return "real", ev
    # The counts either side.
    near = [(date, val) for source, start, date, val, filed in counts if source != "diluted" and val >= MIN_SHARES]
    before = [(date, val) for source, start, date, val, filed in counts if source != "diluted" and val >= MIN_SHARES
              and d0 - STRADDLE_DAYS <= date < d0 and filed < d0]
    after = [(date, val) for date, val in near if d1 < date <= d1 + STRADDLE_DAYS]
    straddle = None
    if before and after:
        b, a = max(before)[1], min(after)[1]
        lq, lr = math.log(a / b), math.log(r)
        margin = 0.25 * abs(lr)
        ev["straddle"] = round(a / b, 4)
        if abs(lq - lr) + margin < abs(lq):
            straddle = "real"
        elif abs(lq) + margin < abs(lq - lr):
            straddle = "none"
    if straddle == "real":
        return "real", ev
    if unchanged >= RESTATED_MIN:
        return "none", dict(ev, by="restatements")
    if straddle == "none":
        return "none", dict(ev, by="straddle")
    return None, ev


def _count_near(counts, e, barriers):
    """The share count at day ordinal `e` for the float test: the cover page's dated nearest it within
    COUNT_NEAR_DAYS, else the balance sheet's, with no checked split (`barriers`, ordinals) between the count's date and
    `e`, nor between the date of a balance-sheet count and its filing (which may restate it for the split)."""
    best = None
    for source, start, date, val, filed in counts:
        if source == "diluted" or val < MIN_SHARES or abs(date - e) > COUNT_NEAR_DAYS:
            continue
        lo, hi = sorted((date, e))
        if any(lo < b <= hi for b in barriers):
            continue
        if source == "balance" and any(date < b <= filed for b in barriers):
            continue
        key = (source != "cover", abs(date - e), -filed)
        if best is None or key < best[0]:
            best = (key, val, date, source)
    return best and {"val": best[1], "date": best[2], "source": best[3]}


def float_ratios(chart, sec, barriers):
    """[{"date", "float", "count", "price", "R", "slip"}] for each public float (sec["floats"]) on a day the chart prices:
    R = float / (count x price as traded then); a float in thousands is read in thousands ("slip"), and one whose R is
    in no plausible unit is left out."""
    out = []
    first = min(weeks(chart), default=None)
    if not first:
        return out
    for e, val, *_ in sec.get("floats") or []:
        day = dt.date.fromordinal(e)
        if day < first:
            continue
        p = prices_at(chart, day)
        c = _count_near(sec.get("counts") or [], e, barriers)
        if not p or not c or not p["price"]:
            continue
        R, slip = val / (c["val"] * p["price"]), False
        if FLOAT_SLIP[0] <= R <= FLOAT_SLIP[1]:
            R, slip = R / 1000, True
        elif R > FLOAT_SLIP[1] or R < FLOAT_FLOOR:
            continue
        out.append({"date": day, "float": val, "count": c["val"], "count_date": dt.date.fromordinal(c["date"]).isoformat(),
                    "price": p["price"], "R": R, "slip": slip})
    return out


def _cls(R):
    if FLOAT_TYPICAL[0] <= R <= FLOAT_TYPICAL[1]:
        return "typical"
    if R < FLOAT_PLAUSIBLE[0] or R > FLOAT_PLAUSIBLE[1]:
        return "implausible"
    return "atypical"


def float_test(fl, d, level, r):
    """What the public floats before a split say of it: ("A" or "U", "strong" (two floats or more) or "weak" (one)) or
    None, and the floats' ratios both ways. A float counts for a reading where that reading puts its R in the typical
    range and the other in the implausible one; none may count the other way. `level` is the reading the floats' R were
    worked out on."""
    rows = []
    n = {"A": 0, "U": 0}
    for f in fl:
        if f["date"] >= d:
            continue
        RA = f["R"] if level != "U" else f["R"] / r
        RU = RA * r
        rows.append((f["date"].isoformat(), round(RA, 3), round(RU, 3)))
        if _cls(RA) == "typical" and _cls(RU) == "implausible":
            n["A"] += 1
        elif _cls(RU) == "typical" and _cls(RA) == "implausible":
            n["U"] += 1
    for h, o in (("A", "U"), ("U", "A")):
        if n[h] and not n[o]:
            return (h, "strong" if n[h] >= 2 else "weak"), rows
    return None, rows


def _decide(cl, gr, sh, fl, sh_by=None):
    """The status of a split from its tests: (status, how it was decided). Statuses: "applied", "applied, prices only"
    (Yahoo scaled the closes, but no share count changed), "not applied", "phantom" (listed, but it changed no share
    count, and the closes and prices need nothing of it), "unclear". The level (applied or not) goes by, in order: the
    tick test; the floats, where two or more agree; one float, where the closes say nothing else or say "not applied"
    (Yahoo applies nearly every split it lists, so one float that fits only the applied level outweighs the closes,
    while it takes two to overturn a reading of "applied"); the closes; and a split that changed no share count needs no
    adjustment. Counts that merely stay level across a split (shares_test's "straddle") can't tell a split that never
    happened from one beside a large issue of shares (Wheeler REIT's), so they make a phantom of a split the other tests
    already say was not applied, but nothing else: only counts restated as before (shares_test's "restatements") make
    a split Yahoo applied one for prices only, or a split no other test reads a phantom."""
    cv = (cl or {}).get("verdict")
    restated_none = sh == "none" and sh_by == "restatements"
    if gr and fl and fl[1] == "strong" and fl[0] != gr:
        return "unclear", "the tick test and the floats disagree"
    if gr:
        level, how = gr, "tick test"
    elif fl and fl[1] == "strong":
        level, how = fl[0], "floats"
    elif fl and (cv in (None, fl[0]) or fl[0] == "A"):
        level, how = fl[0], "one float"
    elif cv:
        level, how = cv, "closes"
    elif restated_none:
        level, how = "U", "share counts"
    else:
        return "unclear", "no test decides"
    if level == "A":
        return ("applied, prices only" if restated_none else "applied"), how
    if sh == "none":
        return "phantom", how + "; no share count changed"
    if (cl or {}).get("jump"):
        return "not applied", how
    if sh == "real" and cv == "A" and how in ("floats", "one float"):
        # A split that happened, whose closes run on across it: Yahoo applied it, and the floats' level error before
        # it is another adjustment's (SITE Centers' 1-for-4 reverse split of August 2024, two months before the
        # spin-off of Curbline, which Yahoo also adjusted the closes for, unlisted): left to find_adjustments.
        return "applied", "closes and share counts (the floats' level error is left to the unlisted adjustments)"
    if sh == "real":
        return "unclear", how + " says not applied, but the closes show no jump within %d weeks" % JUMP_WEEKS
    return "phantom", how + "; no jump in the closes, no share count known to change"


def _glitch_bars(work, m, r, report):
    """Bars right at a split left unadjusted (GLITCH_TOL)."""
    lr = -math.log(r)
    w = work.weeks()
    keys = sorted(w)
    near = [k2 for k2 in keys if m <= k2 <= m + GLITCH_RUN * WEEK]
    for start in range(len(near)):
        for n in range(1, GLITCH_RUN + 1):
            run = near[start:start + n]
            if len(run) < n:
                continue
            prev = [k2 for k2 in keys if k2 < run[0]]
            nxt = [k2 for k2 in keys if k2 > run[-1]]
            if not prev or not nxt:
                continue
            lo, hi = w[prev[-1]][1], w[nxt[0]][1]
            fits = all(abs(math.log(w[k2][1] / lo) + lr) <= GLITCH_TOL * abs(lr)
                       and abs(math.log(w[k2][1] / hi) + lr) <= GLITCH_TOL * abs(lr) for k2 in run)
            if fits:
                work.scale(None, 1 / r, only=set(run))
                report["bars"] += [{"week": k2.isoformat(), "ratio": r} for k2 in run]
                w = work.weeks()
                keys = sorted(w)
                break


def _apply(chart, statuses, sec, report=None):
    """The chart with the statuses (one per group of _groups, latest first decided) applied, and each group's tests on
    the closes as they stand once the later groups are applied. `statuses` maps a group's index to a forced level ("A"
    or "U") from the float pass. Returns (chart, [per group: dict of tests and status])."""
    work = _Work(chart)
    groups = _groups(chart)
    counts = (sec or {}).get("counts") or []
    kept, adjust, info = [], [], [None] * len(groups)
    later = []  # (date, price ratio) of the groups already decided, for the tick test of earlier ones
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        r, d, twin = g["ratio"], g["dates"][0], len(g["dates"]) > 1
        m = _monday(d)
        x = {"date": d.isoformat(), "ratio": r, "listed": len(g["dates"])}
        info[i] = x
        if abs(math.log(r)) < math.log(SPLIT_CHECKED):
            kept += g["entries"]  # small ratios are taken as Yahoo lists them
            later.append((d, r))
            x["status"] = "small"
            continue
        w = work.weeks()
        cl = closes_test(w, g)
        if cl is None:
            kept += g["entries"]
            later.append((d, r))
            x["status"] = "no close before"
            continue
        x["closes"] = cl
        if twin:
            k = cl["twin_k"]
            if k is None:
                x["status"] = "unclear"
                x["how"] = "listed twice; the closes don't tell how often applied"
                kept.append(g["entries"][0])
                later.append((d, r))
                continue
            if k != 1:
                work.scale(m, r ** (k - 1))
            kept.append(g["entries"][0])
            later.append((d, r))
            x["status"] = f"listed twice, applied {['never', 'once', 'twice'][k]}"
            x["applied"] = k
            _glitch_bars(work, m, r, report if report is not None else {"bars": []})
            continue
        lat = math.prod(f for dd, f in later if dd > d)
        # The tick test reads only the closes after the split before this one (whose factor isn't settled yet).
        prev = groups[i - 1]["dates"][-1] if i else None
        gr, gdetail = grid_test(w, min(m, cl.get("jump") or m), r, lat, prev)
        others = [(gg["dates"][0], gg["ratio"]) for j, gg in enumerate(groups) if j != i]
        sh, sdetail = shares_test(counts, d, g["dates"][-1], r, others)
        x.update(grid=gr, grid_detail=gdetail, shares=sh, shares_detail=sdetail)
        fl = statuses.get(i)
        x["floats"] = fl
        status, how = _decide(cl, gr, sh, fl, sdetail.get("by"))
        x.update(status=status, how=how)
        if status == "applied":
            kept.append(g["entries"][0])
            later.append((d, r))
            _glitch_bars(work, m, r, report if report is not None else {"bars": []})
        elif status == "applied, prices only":
            adjust.append([g["entries"][0][0], r])
            later.append((d, r))
        elif status == "not applied":
            b = cl["jump"]
            work.scale(b, 1 / r)
            kept.append([_ts(b, work.off), g["entries"][0][1], g["entries"][0][2]])
            later.append((b, r))
            x["moved_to"] = b.isoformat() if b != m else None
            _glitch_bars(work, b, r, report if report is not None else {"bars": []})
        elif status == "phantom":
            pass  # neither the closes nor the prices need it
        else:  # unclear: left as listed
            kept.append(g["entries"][0])
            later.append((d, r))
    out = dict(chart, close=work.close, adjclose=work.adj, splits=sorted(kept), adjustments=sorted(adjust))
    return out, info


def clean(chart, sec=None):
    """(the chart with its closes made to agree with its splits, what was done): the price gate.

    Each split Yahoo lists of SPLIT_CHECKED or more, latest first, is tested four ways: the tick test (grid_test: the
    digits of the closes before it, priced with and without it), the float test (float_test: the SEC's public floats
    before it, against the market value the chart gives on their day with and without it), the counts test
    (shares_test: whether the company's share counts changed by its ratio, or were restated for it), and the closes test
    (closes_test: whether the closes jump by its ratio within JUMP_WEEKS of its listed week). _decide weighs them. A
    split Yahoo has not applied gets the closes before its jump (close and adjusted close alike) divided by its ratio,
    and is listed at the jump's week; a phantom is dropped from the splits; a split Yahoo applied though it changed no
    share count is kept for prices only; a split listed twice is kept once (with the closes rescaled for how many times
    Yahoo applied it); an unclear one is left as listed. Then the adjustments Yahoo made without listing them:
    unlisted splits it didn't apply either (find_unlisted_jumps) and scalings of the closes it made with no event
    (find_adjustments), each found where the share counts and closes, or the floats and closes, show it.

    `sec` is facts.Facts.history's for the listing's company ({"floats", "counts"}); without it, only the closes and
    their digits are tested. The report: {"events": [one per listed split, with its tests and status], "fixed": [{"date",
    "ratio", "listed", "status", ...} of each split whose closes or listing changed], "bars": [{"week", "ratio"}],
    "unclear": [{"date", "ratio", "listed", "x"}], "adjustments": [{"date", "factor", "kind", ...}], "level_unknown":
    [{"from", "to", "factor"}] (days between which the price level can't be told)}."""
    report = {"events": [], "fixed": [], "bars": [], "unclear": [], "adjustments": [], "level_unknown": [],
              "float_breaks": []}
    if not chart or not chart.get("ts"):
        return chart, report
    sec = sec or {}
    forced = {}
    out, info = _apply(chart, forced, sec)
    # The float pass: each split's floats on the chart as the other splits leave it; repeated until nothing changes.
    if sec.get("floats"):
        for _ in range(3):
            barriers = _barriers(chart, out)
            fl = float_ratios(out, sec, barriers)
            new = {}
            for i, x in enumerate(info):
                if not x or x.get("status") in ("small", "no close before") or x["listed"] > 1:
                    continue
                level = "U" if x["status"] in ("not applied", "phantom") else "A"
                d = dt.date.fromisoformat(x["date"])
                if x.get("moved_to"):
                    d = min(d, dt.date.fromisoformat(x["moved_to"]))
                v, rows = float_test(fl, d, level, x["ratio"])
                if v:
                    new[i] = v
            if new == forced:
                break
            forced = new
            out, info = _apply(chart, forced, sec)
    rep = {"bars": []}
    out, info = _apply(chart, forced, sec, rep)
    report["bars"] = rep["bars"]
    if sec.get("floats"):
        fl = float_ratios(out, sec, _barriers(chart, out))
        for x in info:
            if x and x.get("status") not in (None, "small", "no close before") and x["listed"] == 1:
                level = "U" if x["status"] in ("not applied", "phantom") else "A"
                d = dt.date.fromisoformat(x["date"])
                if x.get("moved_to"):
                    d = min(d, dt.date.fromisoformat(x["moved_to"]))
                x["float_rows"] = float_test(fl, d, level, x["ratio"])[1]
    for x in info:
        if not x or x["status"] in ("small", "no close before"):
            continue
        ev = {k: v for k, v in x.items() if k not in ("closes", "grid_detail", "shares_detail")}
        ev["x"] = (x.get("closes") or {}).get("x")
        ev["closes"] = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in (x.get("closes") or {}).items()}
        ev["grid_detail"], ev["shares_detail"] = x.get("grid_detail"), x.get("shares_detail")
        report["events"].append(ev)
        if x["status"] == "unclear":
            report["unclear"].append({"date": x["date"], "ratio": x["ratio"], "listed": x["listed"], "x": ev["x"],
                                      "how": x.get("how")})
        elif x["status"] in ("not applied", "phantom", "applied, prices only") or (
                x["listed"] > 1) or x.get("moved_to"):
            report["fixed"].append({"date": x["date"], "ratio": x["ratio"], "listed": x["listed"],
                                    "status": x["status"], "how": x.get("how"), "moved_to": x.get("moved_to"),
                                    "applied": x.get("applied", 1 if x["status"].startswith("applied") else 0),
                                    "x": ev["x"]})
    out = find_unlisted_jumps(out, sec, report)
    if sec.get("floats"):
        out = find_adjustments(out, sec, report)
    return out, report


def _barriers(chart, out):
    """The day ordinals of every split listed or kept, which a share count must not be carried across for the float
    test."""
    days = {d.toordinal() for d, r in splits(chart) if abs(math.log(r)) >= math.log(SPLIT_CHECKED)}
    days |= {d.toordinal() for d, r in splits(out) if abs(math.log(r)) >= math.log(SPLIT_CHECKED)}
    days |= {d.toordinal() for d, f in adjustments(out)}
    return days


def find_unlisted_jumps(chart, sec, report):
    """Unlisted splits Yahoo has not applied either (UNLISTED_MIN): the closes before the jump divided by the split's
    factor, and the split listed at the jump's week (so share counts are carried through it)."""
    covers = {}
    for source, start, date, val, filed in (sec or {}).get("counts") or []:
        if source == "cover" and val >= MIN_SHARES:
            if date not in covers or filed >= covers[date][1]:
                covers[date] = (val, filed)
    ds = sorted(covers)
    listed = [d for d, r in splits(chart)]
    for a, b in zip(ds, ds[1:]):
        q = covers[a][0] / covers[b][0]
        n = round(q)
        if n < UNLISTED_MIN or abs(q / n - 1) > UNLISTED_TOL or _slip(q) or b - a > STRADDLE_DAYS:
            continue
        da, db = dt.date.fromordinal(a), dt.date.fromordinal(b)
        if any(da - UNLISTED_DAYS * DAY <= d <= db + UNLISTED_DAYS * DAY for d in listed):
            continue
        work = _Work(chart)
        w = work.weeks()
        g = {"ratio": 1 / n, "dates": [da + (db - da) / 2]}
        keys = sorted(w)
        lr = math.log(n)
        jumps = []
        for k in keys:
            if not (_monday(da) < k <= _monday(db) + WEEK):
                continue
            pre = [k2 for k2 in keys if k2 < k][-LEVEL_WEEKS:]
            post = [k2 for k2 in keys if k2 >= k][:LEVEL_WEEKS]
            if not pre or not post:
                continue
            x1 = math.log(w[k][1] / w[pre[-1]][1]) / lr
            xm = math.log(_median_close(w, post) / _median_close(w, pre)) / lr
            if abs(x1 - 1) <= UNLISTED_JUMP and abs(xm - 1) <= UNLISTED_JUMP:
                jumps.append((abs(x1 - 1), k, x1, xm))
        if not jumps:
            continue
        _, k, x1, xm = min(jumps)
        work.scale(k, n)
        off = work.off
        chart = dict(chart, close=work.close, adjclose=work.adj,
                     splits=sorted((chart.get("splits") or []) + [[_ts(k, off), 1.0, float(n)]]))
        listed.append(k)
        report["fixed"].append({"date": k.isoformat(), "ratio": 1 / n, "listed": 0, "status": "not applied, unlisted",
                                "how": f"cover-page counts {covers[a][0]:,.0f} ({da}) to {covers[b][0]:,.0f} ({db}); "
                                       f"the closes jump by {n} that week", "applied": 0, "x": round(x1, 3)})
    return chart


def _exact_factor(prices, k0):
    """The factor within 1.6 times either way of `k0` that makes the most of `prices` whole cents (GRID_FIT of at least
    GRID_MIN of them, by more than chance), or None."""
    ps = [p for p in prices if p > 0][-40:]
    if len(ps) < GRID_MIN:
        return None
    best = None
    for ref in ps[:3]:
        lo, hi = math.ceil(ref * k0 / 1.6 * 100), math.floor(ref * k0 * 1.6 * 100)
        if hi - lo > 20000:
            continue
        for c in range(lo, hi + 1):
            k = c / 100 / ref
            hits = sum(1 for p in ps if _cents(p * k))
            key = (hits, -abs(math.log(k / k0)))
            if best is None or key > best[0]:
                best = (key, k, hits)
    if not best or best[2] < GRID_FIT * len(ps) or _tail(best[2], len(ps), 0.01) >= GRID_TAIL:
        return None
    k = best[1]
    n = round(k) if k >= 1 else round(1 / k)
    if n >= 2 and abs((k if k >= 1 else 1 / k) / n - 1) < 1e-4:
        if k > 1:
            # Any whole-cent price stays whole cents times n: it shows only where the prices as they stand aren't.
            if sum(1 for p in ps if _cents(p)) > GRID_MISS * len(ps):
                return None
        elif _tail(best[2], len(ps), 1 / n) >= GRID_TAIL:
            return None  # whole multiples of n cents by chance
    return k


def find_adjustments(chart, sec, report):
    """Scalings of the closes Yahoo made without listing them (ADJUST_MIN): where every float before some day is
    implausible and the floats after are not, the closes before the week the digits place it in are priced at the
    level the floats show (an adjustment to prices only, or a split where a share count changes by the factor between
    the floats). Where the week can't be placed, the days between the two floats are left with no price level
    ("level_unknown") and the adjustment is dated at the later float."""
    fl = float_ratios(chart, sec, _barriers(chart, chart))
    fl.sort(key=lambda f: f["date"])
    cls = [_cls(f["R"]) for f in fl]
    for i in range(1, len(fl)):
        off, on = fl[:i], fl[i:]
        if not all(c == "implausible" for c in cls[:i]) or "implausible" in cls[i:] or "typical" not in cls[i:]:
            continue
        ro, rn = [f["R"] for f in off], [f["R"] for f in on]
        if max(ro) / min(ro) > ADJUST_SPREAD:
            continue
        k = statistics.median(ro) / statistics.median(rn)  # the prices before are this many times too low
        if abs(math.log(k)) < math.log(ADJUST_MIN):
            return chart
        a, b = off[-1]["date"], on[0]["date"]
        w = weeks(chart)
        pf = lambda day: price_factor(chart, day)
        keys = sorted(w)
        # The traded prices before the interval (the wrong side) give the exact factor where one makes them whole cents.
        before = [w[x][1] * pf(x) for x in keys if a - GRID_WEEKS * WEEK <= x <= a]
        kx = _exact_factor(before, k)
        # A cover-page count that changes by a whole factor near the floats' between them (a split Yahoo applied but
        # didn't list: Curis's 118 million shares of July 2023 against 5.9 million in October).
        covers = {}
        for source, start, date, val, filed in sec.get("counts") or []:
            if source == "cover" and val >= MIN_SHARES and a.toordinal() - 30 <= date <= b.toordinal() + 30:
                if date not in covers or filed >= covers[date][1]:
                    covers[date] = (val, filed)
        covers = sorted((date, v) for date, (v, _) in covers.items())
        kind, cw = "price", None
        for (d1, v1), (d2, v2) in zip(covers, covers[1:]):
            # Only a fall: share counts rise by whole factors at mergers and offerings too (a SPAC's merger).
            q = v2 / v1
            n = round(1 / q)
            if q >= 1 or n < 2 or abs(1 / q / n - 1) > COUNT_SPLIT_TOL or _slip(q):
                continue
            if abs(math.log(q) - math.log(k)) > math.log(1.5):
                continue
            kind, cw = "split", (dt.date.fromordinal(d1), dt.date.fromordinal(d2))
            if not kx:
                kx = 1 / n
            break
        if kind == "price" and kx and _integer_ratio(kx):
            # An exact whole factor with the counts falling (or rising) by more than half of it between the floats:
            # a split, with shares issued or bought back beside it (Magenta Therapeutics' 1-for-16 reverse split at
            # its merger with Dianthus in September 2023).
            moves = [v2 / v1 for (d1, v1), (d2, v2) in zip(covers, covers[1:])]
            if any((q < 1) == (kx < 1) and abs(math.log(q)) >= 0.5 * abs(math.log(kx)) for q in moves):
                kind = "split"
        factor = kx or k
        # Its week: the latest week up to which the prices at the corrected level are whole cents (or, without an
        # exact factor, are not whole cents as they stand) and from which they are not.
        # Without an exact factor, only prices of $1 or more say anything by their digits.
        span = [x for x in keys if a <= x <= b and (kx or w[x][1] * pf(x) >= 1)]
        if kx:
            y = [1 if _cents(w[x][1] * pf(x) * kx) else 0 for x in span]
        else:
            y = [0 if _cents(w[x][1] * pf(x)) else 1 for x in span]
        week, best = None, None
        for j in range(len(span) + 1):
            s = sum(v - 0.5 for v in y[:j])
            if best is None or s >= best:
                best, week = s, j
        # The weeks before the earlier float are on the wrong side too: they must look it.
        lead = [x for x in keys if a - 8 * WEEK <= x < a and (kx or w[x][1] * pf(x) >= 1)]
        if kx:
            ly = [1 if _cents(w[x][1] * pf(x) * kx) else 0 for x in lead]
        else:
            ly = [0 if _cents(w[x][1] * pf(x)) else 1 for x in lead]
        pre_y, post_y = ly + y[:week], y[week:]
        clear = (len(post_y) >= 2 and sum(post_y) <= 0.3 * len(post_y)
                 and len(pre_y) >= 4 and sum(pre_y) >= 0.75 * len(pre_y))
        t = span[week] if clear and week < len(span) else None
        if cw and t and not (cw[0] < t + WEEK and t <= cw[1] + WEEK):
            t = None  # the digits and the counts disagree on where it falls
        entry = {"factor": factor, "kind": kind, "exact": bool(kx), "floats_wrong": [(f["date"].isoformat(),
                 round(f["R"], 3)) for f in off], "floats_right": [(f["date"].isoformat(), round(f["R"], 3)) for f in on],
                 "counts": cw and [x.isoformat() for x in cw]}
        # Corroborated by the digits (an exact factor, or the week placed), the counts, or two floats or more far out
        # (over 2 or under 0.05) with two typical ones after.
        far = len(off) >= 2 and all(f["R"] > 2 or f["R"] < 0.05 for f in off) \
            and sum(1 for c in cls[i:] if c == "typical") >= 2
        if not (kx or t or cw or far):
            report["float_breaks"].append(dict(entry, why="one float, not borne out by the closes' digits or the counts"))
            return chart
        lo, hi = (cw if cw else (a, b))
        when = t or hi
        entry["date"] = when.isoformat()
        entry["placed"] = "the digits of the closes" if t else "not placed"
        if not t:
            report["level_unknown"].append({"from": lo.isoformat(), "to": hi.isoformat(), "factor": factor})
        off_ts = chart.get("gmtoffset") or 0
        if kind == "split":
            n = _integer_ratio(factor) or (factor if factor >= 1 else 1 / factor)
            num, den = (float(n), 1.0) if factor > 1 else (1.0, float(n))
            # A split ratio r scales the closes before it by r for the price (price_factor): factor = r.
            chart = dict(chart, splits=sorted((chart.get("splits") or []) + [[_ts(when, off_ts), num, den]]))
        else:
            chart = dict(chart, adjustments=sorted((chart.get("adjustments") or []) + [[_ts(when, off_ts), factor]]))
        report["adjustments"].append(entry)
        return chart
    return chart


def unclear_for(report, day):
    """The unclear splits (clean) that touch the year's closes to `day` or the 52 weeks after it, dated after the start
    of the 52 weeks before it and up to 52 weeks after it: the listing is left out of `day`. A split dated before then
    touches nothing of `day`; one dated after the 52 weeks only the price level on `day` (level_unknown_for)."""
    start, end = day - 53 * WEEK, day + 52 * WEEK
    return [u for u in report.get("unclear") or [] if start < dt.date.fromisoformat(u["date"]) <= end]


def level_unknown_for(report, day):
    """Why the price level on `day` can't be told, or None: an unclear split dated after the 52 weeks after it (the
    closes either side of `day` agree with each other whatever it was, but the price on `day` is off by its ratio if it
    was misread), or `day` between two floats an unlisted adjustment fell between that the digits couldn't place."""
    end = day + 52 * WEEK
    late = [u for u in report.get("unclear") or [] if dt.date.fromisoformat(u["date"]) > end]
    if late:
        return {"why": "split_unclear_later", "splits": late}
    for x in report.get("level_unknown") or []:
        if dt.date.fromisoformat(x["from"]) <= day < dt.date.fromisoformat(x["to"]):
            return {"why": "adjustment_unplaced", "between": x}
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Prices and returns.

def prices_at(chart, day):
    """What market.price_history would have returned on `day`, from weekly closes: {"price": the close of `day`'s week,
    "high52"/"low52": the highest/lowest weekly close of the 52 weeks to it, "weekly": those 52 closes as [[timestamp,
    close]], "splits": the year's splits}, all as traded then (not adjusted for later splits). None without a close that
    week."""
    w = weeks(chart)
    monday = _monday(day)
    if monday not in w:
        return None
    f = price_factor(chart, day)
    year = sorted(k for k in w if monday - 51 * WEEK <= k <= monday)
    # A price-only adjustment inside the year (a spin-off) scales the closes before it as Yahoo showed them that day.
    closes = [[w[k][0], round(w[k][1] * f, 4)] for k in year]
    vals = [c for _, c in closes]
    return {"price": round(w[monday][1] * f, 4), "high52": max(vals), "low52": min(vals), "weekly": closes,
            "splits": [(d.isoformat(), r) for d, r in splits(chart) if day - 365 * DAY < d <= day],
            "adj_close": w[monday][1], "split_after": f}


# The dividends a year's adjusted closes imply (the adjusted close's ratio to the close at the end over that at the
# start: 1 plus the year's dividends reinvested) are those of a sane year between these. Outside them, the adjusted
# closes are taken for an error unless the chart lists dividends of that size: at least DIVIDEND_LISTED of the share of
# the price the adjusted closes take out (1 less 1 over the factor), each dividend as a share of the close of the week
# before it (Elme Communities' liquidating distribution of $14.67 in January 2026, VirnetX's special dividend of $20 in
# April 2023). New Fortress Energy's adjusted closes of 2023 imply a factor of 3.27, 69% of the price paid out, against
# 1% its chart's dividends pay: its dividends are on the basis of a 1-for-50 reverse split its closes didn't take, and
# its adjusted closes were worked out from them.
DIVIDEND_FACTOR = (0.98, 1.5)
DIVIDEND_LISTED = 0.5


def total_return(chart, day, weeks_ahead=52, detail=False):
    """The total return (adjusted closes, dividends reinvested) from `day`'s week close to the close `weeks_ahead`
    weeks later, or None where either is missing or an adjusted close is nothing or less (Yahoo's error). Where the
    adjusted closes imply dividends outside DIVIDEND_FACTOR that the chart's own dividends don't account for
    (DIVIDEND_LISTED), the return is the closes' plus the dividends the chart lists for the 52 weeks, each reinvested
    at the close of the week before it less the dividend (as the adjusted closes reinvest them). With `detail`,
    (return, how)."""
    w = weeks(chart)
    a = _monday(day)
    b = a + weeks_ahead * WEEK
    if a not in w or b not in w:
        return (None, None) if detail else None
    (ta, ca, aa), (tb, cb, ab) = w[a], w[b]
    off = chart.get("gmtoffset") or 0
    keys = sorted(w)
    listed, paid = 1.0, 0.0
    for ts, amount in chart.get("dividends") or []:
        d = _monday(_day(ts, off))
        if a < d <= b and amount and amount > 0:
            prev = [k for k in keys if k < d]
            if prev:
                p = w[prev[-1]][1]
                paid += amount / p
                if amount < p:
                    listed *= p / (p - amount)  # reinvested at the price without it: P / (P - D)
    how = "adjusted closes"
    if not aa or not ab or aa <= 0 or ab <= 0:
        return (None, "an adjusted close of nothing or less") if detail else None  # Yahoo's error (NFE's of 2022)
    implied = (ab / cb) / (aa / ca)
    if DIVIDEND_FACTOR[0] <= implied <= DIVIDEND_FACTOR[1] or (
            implied > DIVIDEND_FACTOR[1] and paid >= DIVIDEND_LISTED * (1 - 1 / implied)):
        r = ab / aa - 1
        return (r, how) if detail else r
    how = f"closes and listed dividends (adjusted closes imply a dividend factor of {implied:.3f}, "           f"{100 * (1 - 1 / implied):.0f}% of the price paid out; the chart's dividends pay {100 * paid:.0f}%)"
    r = cb / ca * listed - 1
    return (r, how) if detail else r


# ---------------------------------------------------------------------------------------------------------------------
# Share counts and market values.

# A count dated more than this many days before the date is too old to use (about 15 months: a company that files once
# a year gives a new count at least every 12 to 13 months). Tilly's newest cover page count without a share class was
# from 2015 (11.98 million shares against 31.1 million diluted in 2022): it had since given one per class, which the
# SEC's bulk file leaves out.
MAX_AGE_DAYS = 457
# Counts within this factor of each other agree; a count this far below the company's other counts is a filing mistake
# (a count given in thousands, one share class of several, a decimal slip).
AGREE = 3.0
# A count far above the others that is about a thousand (or a million, a billion) times them, within this factor, is a
# count given in single shares where the filing meant thousands (Hecla Mining's 10-Qs of 2023 and 2024 give 617 to 630
# billion shares on their cover pages, a thousand times its 620 million).
SLIP_TOL = 1.25
# A count whose market value would be under this share of the company's latest book value is a filing mistake: a count
# given in thousands (Hub Group's diluted count of 33,940 for June 2022, a thousandth of its 33.9 million shares).
# Among the listings valued from counts that agree with each other on September 30, 2022, one was priced under 2% of
# its book value.
BOOK_FLOOR = 0.01
# A count under this many shares is a placeholder, not the listing's count: a company files 100 or 1,000 shares before
# the offering or merger that lists it (Paramount Skydance's cover pages of 2025 before the merger give 1,000; QVC
# Group's of 2026 give 1). The fewest shares a listing on the dates was valued from otherwise: 37,051 (ZNB, September
# 2025, after a 1-for-25 reverse split).
MIN_SHARES = 10_000
# One share class of several: where the diluted count and the count earnings per share imply agree within WHOLE_AGREE
# (both count every class the profit is divided among), the count chosen is under WHOLE_SHARE of them, and the public
# float filed before the date says so too (the chosen count makes the float more than the market value, past
# FLOAT_PLAUSIBLE, and the diluted one doesn't, within FLOAT_TYPICAL), the chosen count is of one class only and the
# diluted count is used instead (HEICO's cover pages give the count of one of its two classes, 39% to 45% of the whole,
# within the 3x the counts are otherwise grouped by; its float is twice the market value they give). Without the float's
# word the diluted count isn't taken: the weighted averages of a company that sold pre-funded warrants count the shares
# the warrants buy, which are not yet issued (Immunic's and Quince Therapeutics', 3 to 6 times the shares on their cover
# pages in 2026, with floats that fit the cover pages).
WHOLE_AGREE = 1.1
WHOLE_SHARE = 0.6
SOURCES = ("cover", "balance", "diluted")
SOURCE_NAMES = {"cover": "the cover page (dei:EntityCommonStockSharesOutstanding)",
                "balance": "the balance sheet (us-gaap:CommonStockSharesOutstanding)",
                "diluted": "the diluted weighted average (us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding)"}


def is_us(u, loc):
    """Whether a listing may be a US company: Nasdaq's country the United States or blank (as the screener takes it),
    and the SEC's location (`loc`, as the frames give it) a US state or unknown. The screener and its US watch lists
    also need a known US location (screener._us_listing), which only a company with SEC figures has."""
    return u.get("country") in ("United States", "") and (not loc or loc.startswith("US"))


def _slip(q):
    lg = abs(math.log10(q))
    return any(abs(lg - k) <= math.log10(SLIP_TOL) for k in (3, 6, 9))


def share_count(counts, chart, day, price=None, public_float=None):
    """(the share count to value a listing with on `day`, how it was chosen) from `counts` (facts.Facts.share_counts:
    what its filings gave before `day`), or (None, why not).

    Every count dated at most MAX_AGE_DAYS before `day` is carried through the splits between it and `day`: the newest
    three cover pages, the newest balance-sheet count, the newest diluted weighted average and the count the newest
    earnings per share imply (profit over earnings per share, a check on the others that is never used itself). A split
    between a count's date and the day it was filed may or may not be in it (Alphabet's 10-Q of July 2022 gives its June
    30 balance-sheet count after the 20-for-1 split of July 15, as companies restate share counts for a split made
    before they report; CTO Realty's of July 2022 gives its June 30 counts before its split of July 1): such a count is
    taken on whichever basis agrees better with the counts no split separates from their filing, and on its filing
    date's basis where there are none.

    The counts are grouped into those that agree (each within AGREE of the next, in order of size), and the group with
    the most counts is the reference (ties to the group holding more of the four kinds, cover page, balance sheet,
    diluted and implied by earnings per share; no reference if still tied). The count used is the newest of the newest
    cover page's, the balance sheet's and the diluted one (on the same date, in that order; a cover page is usually dated
    after the balance sheet it comes with, but one that gives its count per share class leaves only an older cover page
    without one) that is either in the reference group, or above every count outside its own group by more than AGREE,
    no older than any of them and no thousand-fold slip (SLIP_TOL): shares issued since the older counts, as for a
    company that sold many times its shares in a quarter, or one that just went public, whose earlier counts are from
    before.
    And, with `price`, whose market value is at least BOOK_FLOOR of the latest book value (stockholders' equity, where
    positive and dated within MAX_AGE_DAYS). Any other count is a filing mistake: zero, far below the others (a count in
    thousands, one share class of several, a decimal slip), a thousand-fold slip, or a market value under BOOK_FLOOR of
    book. A count under MIN_SHARES is a placeholder and left out from the start. Where the diluted count and the count
    earnings per share imply agree within WHOLE_AGREE, the count chosen is under WHOLE_SHARE of the diluted one, and
    `public_float` ((date, value), filed before `day`) is implausibly large against the chosen count and typical against
    the diluted one, the chosen one is of one share class and the diluted count is used ("whole": true). Returns
    (shares, {"source", "date",
    "filed", "form", "age_days", "issued", "basis", "others", "agree", "whole"}) or (None, {"why": "no_recent_count" |
    "counts_disagree", "counts": ...})."""
    pool, fixed, book = [], [], None
    for c in counts:
        end = dt.date.fromisoformat(c["date"])
        if c["val"] is None or c["val"] <= 0 or (day - end).days > MAX_AGE_DAYS:
            continue
        if c["source"] not in ("book", "eps") and c["val"] < MIN_SHARES:
            continue  # a placeholder (MIN_SHARES), as if it weren't there
        if c["source"] == "book":
            book = c["val"]
            continue
        filed = dt.date.fromisoformat(c["filed"])
        by_date = c["val"] * split_factor(chart, end, day)
        by_filed = c["val"] * split_factor(chart, filed, day)
        x = dict(c, by_date=by_date, by_filed=by_filed)
        pool.append(x)
        if by_date == by_filed:
            fixed.append(by_date)
    if not any(x["source"] in SOURCES for x in pool):
        return None, {"why": "no_recent_count"}
    ref = statistics.median(fixed) if fixed else None
    for x in pool:
        if x["by_date"] == x["by_filed"]:
            x["v"], x["basis"] = x["by_date"], "date"
        elif ref is None:
            x["v"], x["basis"] = x["by_filed"], "filed"
        else:
            x["v"], x["basis"] = min((x["by_date"], "date"), (x["by_filed"], "filed"),
                                     key=lambda t: abs(math.log(t[0] / ref)))
    order = sorted(pool, key=lambda x: x["v"])
    groups = [[order[0]]]
    for x in order[1:]:
        if x["v"] <= AGREE * groups[-1][-1]["v"]:
            groups[-1].append(x)
        else:
            groups.append([x])
    group_of = {id(x): i for i, g in enumerate(groups) for x in g}
    most = max(len(g) for g in groups)
    top = [i for i, g in enumerate(groups) if len(g) == most]
    if len(top) > 1:
        kinds = {i: len({x["source"] for x in groups[i]}) for i in top}
        top = [i for i in top if kinds[i] == max(kinds.values())]
    reference = top[0] if len(top) == 1 else None
    first = {}
    for x in pool:
        if x.get("rank", 0) == 0 and x["source"] in SOURCES and x["source"] not in first:
            first[x["source"]] = x
    eps = next((x for x in pool if x["source"] == "eps"), None)
    dil = first.get("diluted")
    # Newest first; on the same date, the cover page's, then the balance sheet's, then the diluted one.
    names = sorted(first, key=lambda n: (first[n]["date"], -SOURCES.index(n)), reverse=True)
    below_book = []
    for name in names:
        x = first.get(name)
        if not x:
            continue
        i = group_of[id(x)]
        others = [o for j, g in enumerate(groups) if j != i for o in g]
        issued = bool(others) and all(x["v"] > AGREE * o["v"] for o in others) \
            and x["date"] >= max(o["date"] for o in others) \
            and not _slip(x["v"] / statistics.median(o["v"] for o in others))
        if not (i == reference or issued):
            continue
        whole = False
        if name != "diluted" and dil and eps and abs(math.log(dil["v"] / eps["v"])) <= math.log(WHOLE_AGREE) \
                and x["v"] < WHOLE_SHARE * dil["v"] and public_float:
            fday, fval = public_float
            p = prices_at(chart, fday)
            if p and p["price"]:
                pe = p["price"] / split_factor(chart, fday, day)  # the float's day's price, on `day`'s share basis
                if fval / (x["v"] * pe) > FLOAT_PLAUSIBLE[1] and fval / (dil["v"] * pe) <= FLOAT_TYPICAL[1]:
                    x, name, whole = dil, "diluted", True  # one share class of several: the whole company's count
        if price and book and book > 0 and x["v"] * price < BOOK_FLOOR * book:
            below_book.append(name)
            continue
        return x["v"], {"source": name, "date": x["date"], "filed": x["filed"], "form": x.get("form"),
                        "age_days": (day - dt.date.fromisoformat(x["date"])).days,
                        "issued": issued and i != reference and not whole, "basis": x["basis"],
                        "others": len(pool) - 1, "agree": len(groups[group_of[id(x)]]) - 1, "whole": whole,
                        "date_typo": x.get("date_typo")}
    return None, {"why": "counts_disagree", "below_book": below_book,
                  "counts": [(x["source"], round(x["v"]), x["date"]) for x in pool]}


def market_value(u, chart, p, counts, day, loc, public_float=None):
    """(market value on `day`, how it was worked out, the share count's details) for listing `u` with prices `p`
    (prices_at) and `counts` (facts.Facts.share_counts as of `day`): share_count's count times `day`'s price as traded.
    How: the count's source ("cover", "balance", "diluted"), or why there is no market value: "none_outside_us" for a
    listing that is not a US company (is_us; its listing may be depositary shares that each hold several of the shares
    its filings count, so its count doesn't fit its price), "none_no_recent_count" for no count dated within
    MAX_AGE_DAYS, "none_counts_disagree" for counts that leave every one of them a likely mistake. A listing with no
    market value can't pass the screener or enter a watch list, as in the pipeline."""
    if not is_us(u, loc):
        return None, "none_outside_us", {}
    shares, how = share_count(counts, chart, day, p["price"], public_float)
    if shares is None:
        return None, "none_" + how["why"], how
    return shares * p["price"], how["source"], dict(how, shares=round(shares))
