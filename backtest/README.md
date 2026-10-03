# Screener backtest

Reruns the screener as it would have run on the last Friday of September 2022, 2023, 2024 and 2025 (2022-09-30,
2023-09-29, 2024-09-27, 2025-09-26), on the SEC facts filed before each date and that week's prices, and measures how
the companies it passed, its score, its flags and its lists did over the next 52 weeks against the average listed
stock. It runs the pipeline's own code, unchanged, for any version of it: a variant is a folder holding a `pipeline`
package plus any overrides of module attributes.

## Running it

The downloads and checks of today's data, once:

```
python -m backtest.data all --fund-pkl <the live run's pickled load_fundamentals output, optional>
python -m backtest.validate frames
python -m backtest.validate market
python -m backtest.validate today --replay-dir <folder with the live run's uni.pkl, metrics.pkl> --live-dir <folder with its screener.json>
```

The variants (from the repository's root, Python 3.12; `--code .` is the checkout's own `pipeline/`, and `719a878`
the code before the September 2026 screener update), then the comparisons, the README's
tables and the checks of the past dates (which count each variant's passers and list members, so they come last):

```
python -m backtest.screener run --code 719a878 --label before
python -m backtest.screener run --code 719a878 --label before_margin3 --set 'screener.RULES["min_net_margin"]=0.03'
python -m backtest.screener run --code . --label after_oldweights --set 'screener.SCORE_WEIGHTS={"ps": 20, "fcf": 15, "out_of_favor": 8, "undiscovered": 7, "under_book": 0, "insiders_buying": 0}'
python -m backtest.screener run --code . --label after_pb --set 'screener.SCORE_WEIGHTS={"insiders_buying": 0}'
python -m backtest.screener run --code . --label after
python -m backtest.screener compare before before_margin3 after_oldweights after_pb after --chain
python -m backtest.screener tables before before_margin3 after_oldweights after_pb after --readme
python -m backtest.validate past
```

- `before`: the screener at 719a878 (profit margin rule 8%).
- `before_margin3`: the same with the margin rule at 3% (part 1 of the update alone).
- `after_oldweights`: the updated code with the old score weights (cheap vs. sales 20, cash generation 15, out of
  favor 8, few analysts 7, and no points for under book value or insider buying). Its passes and scores must equal
  `before_margin3`'s on every date; only the watch lists, industries and flags it adds may differ.
- `after_pb`: the updated code with its new weights but no insider points (price to book scored, the weights
  rebalanced).
- `after`: the updated code as it is, insider points included.

The main branch's code on one date (see "The main branch" under Checks): a copy of `origin/main`'s `pipeline/` with
this worktree's `pipeline/screener.py` over it (`merged_main`), and a second copy in which build.py and fundamentals.py
are also merged with this worktree's changes to them, as merging the branch would (`merged_full`):

```
mkdir -p <A> && git archive origin/main pipeline | tar -x -C <A> && cp pipeline/screener.py <A>/pipeline/
python -m backtest.screener run --code <A> --label merged_main --dates 2025-09-26
B=$(git merge-base HEAD origin/main)
mkdir -p <F> && git archive origin/main pipeline | tar -x -C <F> && cp pipeline/screener.py <F>/pipeline/
for f in build.py fundamentals.py; do
  git show $B:pipeline/$f | tr -d '\r' > <F>/base.tmp; tr -d '\r' < pipeline/$f > <F>/ours.tmp
  tr -d '\r' < <F>/pipeline/$f > <F>/theirs.tmp
  git merge-file -p <F>/ours.tmp <F>/base.tmp <F>/theirs.tmp > <F>/pipeline/$f
done
python -m backtest.screener run --code <F> --label merged_full --dates 2025-09-26
```

`--code` takes a folder holding a `pipeline` package or a commit (made into `.cache/backtest/code/<commit>` with
`git archive`). `--set` takes `module.ATTR=<json>`: a key (`screener.RULES["min_net_margin"]=0.03`) or a JSON object
given for a dict (`screener.SCORE_WEIGHTS={"insiders_buying": 0}`) updates that dict in place, any other value
replaces the attribute. `--dates` picks other dates (each needs its insider data set and charts that reach 52 weeks
past it). `--metrics-only` works out and keeps each date's metrics for the code and stops, so that later runs of any
variant of that code take seconds. `compare <base> <others>` measures each other variant against the base; with
`--chain` each one against the one before it, with a summary of every step in `chain_<labels>.txt`. `tables <labels>`
prints the tables of the results below and keeps them in `tables_<labels>.md`; with `--readme` it also puts them into
this file between the results markers, in place of what was there. Scoring a variant whose payloads hold the industry
listings takes a minute or two (the bootstrap intervals over thousands of listings).

Each date runs in its own process with the variant's root first on `sys.path`. What does not depend on the variant's
code is kept per date in `.cache/backtest/state/<date>/`: the listings, prices and market values (`market.json`) and
the 52-week returns (`returns.json`). Each is kept under a key and rebuilt when the key changes, the old file moved
aside as `market.<its key>.json` (`returns.<its key>.json`): `market.json`'s key (`asof.market_key`) hashes the
backtest code it is worked out by (`backtest/prices.py`, `facts.py`, `data.py` and `asof.market_state` itself),
`loc.json`, the snapshot of today's listings, the facts index (its tags and the companyfacts.zip it was built from) and
the charts (how many, their total size, the newest change); `returns.json`'s (`screener.returns_key`) is that key and
`returns_at`'s own code. The backtest's earlier versions are kept beside them: `market_v1.json` and `market_v2.json`
(the first two runs), `market_v3.json` and `returns_v3.json` (the version before the share counts and split corrections
of the second review), `market_v4.json` and `returns_v4.json` (the version before the price gate below), with those
versions' results in `.cache/backtest/results/v3/` and `v4/`. The derived metrics are kept per key of everything they
are worked out from: the variant's `fundamentals.py`, `report.py`, `filers.py`, `build.py` and `reit_types.py` and any
`--set` on them, the harness's own `backtest/facts.py` and `backtest/asof.py`, `loc.json`, the SIC codes kept in the
shared `sic.json` for the date's companies, and the date's listings but for their prices and market values
(`asof.metrics_key`). The insider purchases are kept per hash of `insiders.py`, the harness's `backtest/form345.py` and
the date's listings and prices. A variant that only changes `screener.py` reruns in seconds; a new version of the
fundamentals takes about a minute a date. Neither changes with the market values: the metrics use them only to order
their lookups, every one of which is made, and the screener takes no market value from the insider purchases. A variant
whose code asks the SEC for a tag the index lacks stops, the index gets the new tags in one pass over the zip (about 3
minutes), and the date reruns.

Results go to `.cache/backtest/results/`: `<label>_<date>.payload.json` (the screener's payload and run details),
`<label>_<date>.json` (the figures below), `<label>_pooled.json` and the plain-text report `<label>.txt`;
`compare <base> <other>` writes `compare_<base>_vs_<other>.json` and `.txt` with the companies each variant added and
dropped; `compare --chain` also `chain_<labels>.txt`; the checks write `validate_<check>.json` (and
`validate_past.txt`).

## What it measures

A company's return is its total return (dividends reinvested, Yahoo's adjusted close) from the date's weekly close to
the close 52 weeks later, on the chart as the price gate below leaves it. Where the adjusted closes imply a year's
dividends of a factor under 0.98 (1 plus the dividends reinvested: under 0.98 is a negative dividend), or over 1.5 while
the chart's own dividends don't pay at least half the share of the price the adjusted closes take out (each dividend as
a share of the close of the week before it), the adjusted closes are taken for an error and the return is the closes'
plus the chart's dividends, each reinvested at the close of the week before it less the dividend, as adjusted closes
reinvest them (`prices.total_return`). That is so for 4 returns of the 18,891 (`validate_past.txt`): New Fortress
Energy's from 2023-09-29, whose adjusted closes imply a factor of 3.27, 69% of the price paid out, against the 2% its
chart's dividends pay (its dividends are on the basis of a 1-for-50 reverse split its closes didn't take), and CBRE
Global Real Estate Income Fund's from 2023-09-29, 2024-09-27 and 2025-09-26 (factors of 1.52 to 1.56, 34% to 36% of the
price, against the 14% to 15% its dividends pay, beside a 1-for-3 reverse split of September 2026 that Yahoo hasn't
applied to its closes). Large dividends the chart lists keep the adjusted closes (Elme Communities' liquidating
distribution of $14.67 in January 2026, VirnetX's special dividend of $20 in April 2023). An adjusted close of nothing
or less (Yahoo's error) leaves the listing without a return, as before.

Each date's returns are winsorized at that date's 1st and 99th percentiles across every listing with both closes, and a
company's relative return is its winsorized return less the equal-weighted average of all of them. For each variant and
date, and pooled over the four dates (company-date pairs):

- how many companies pass, and the mean and median relative return of those with a return, with 95% bootstrap
  intervals over companies (2,000 resamples), and the share that beat the median stock;
- the Spearman rank correlation of the score with the relative return among the companies that pass, with its
  interval, and the top 10 by score against the rest (the difference of their means, with an interval that resamples
  each group);
- for each flag any passer has on any date, the passing companies with it against those without, and the difference;
- for each list the payload holds (`net_cash_list`, `financials_list` and its banks and other companies apart,
  `giants`, the unverified companies, and the listings in the industries marked out of favor), its size and relative
  return;
- for code that measures industries, the market's median listing's distance below its 52-week high, and the industries
  marked out of favor under the screener's rule (median listing 25% or more below its high) and under a market-relative
  rule (median listing at least 10 points further below its high than the market's median listing), with the passers
  in those industries against the rest and the listings in them;
- against a base variant, the companies added and dropped and how they did beside the base's passers, whether the
  passes and scores are the same, and the change in the score's Spearman on the companies both pass, with a paired
  interval (each resample scores the same companies both ways).

Every group (passers, a list, a flag's passers with it or without, the companies added) also gets its median, its mean
without the returns capped at the 1st or 99th percentile ("without capped"), and its trimmed mean, without its single
largest return either way ("trimmed", which names the company left out), with an interval in which each resample leaves
out its own largest return; every difference between two groups also gets the same difference without capped returns.
"Without capped" leaves out only returns at a cap: a return inside the caps stays in however large it is (BTCS, at +270
points in 2024, is under that date's 99th percentile, and carries 2024's list of 15 financial companies under book value
from +6.2 points without it to +23.8), which the trimmed mean shows.

Pooled over the dates, a mean counts each company once for each date it is in the group, and every interval resamples
each date's companies within that date (so every resample keeps each date's number of companies). A pooled difference
between two groups (a flag's passers with it and without, the top 10 and the rest, the companies a step added and the
earlier passers, passers in marked industries and elsewhere) is the weighted average of each date's own difference,
each date weighted by n1 x n2 / (n1 + n2) of its two groups, so that a flag that marks nearly every passer on a date
when passers did well can't look good by that date alone. Every interval treats companies as independent, which they
are not quite: companies in one industry move together, so the intervals of the industry listings (thousands of
listings, but only 20 to 110 industries a date) are much too narrow, and those of the passers somewhat too narrow.

## Method

**SEC figures as of each date.** The pipeline reads the SEC's frames API (one line item, one period, every company's
latest figure) and companyconcept API (one company's every figure for one item). Both are rebuilt from the SEC's bulk
`companyfacts.zip` (every fact of every filing, each with the date filed), keeping only facts filed strictly before the
date (`backtest/facts.py`):

- companyconcept: the company's facts under the tag filed before the date, in the file's order; none is the API's 404.
- frames: each fact gets the frame period the SEC labels it with. The SEC puts the label (`frame`) on the last-filed
  copy of a span only, so it is carried to every earlier copy of the same span in the same tag; a span no copy of which
  is labeled in its tag takes the label another tag of the company gives the same span, and failing that the rule the
  labels follow: an instant belongs to the calendar quarter holding the day 45 days before it (`CY2023Q2I`), a duration
  of 80 to 100 days to the calendar quarter holding the day 45 days before its end (`CY2023Q2`), one of 335 to 395 days
  to the calendar year of the day 180 days before its end (`CY2023`). On the 9.1M labeled facts of the 202 tags the
  pipeline asks for, the rule gives the SEC's own label for 99.7% (9,099,964 of 9,124,079); the rest are spans of
  other lengths the SEC also labels, which carry their own label. (Nearest calendar quarter end, ties to the later,
  misses the SEC's labels for instants dated February 14, which it files under the fourth quarter.) Per company and
  period the last filed fact wins, ties going to the larger accession number, as the frames API does; a span the SEC
  labels in that tag today goes before one it doesn't, since today's frame shows it is the span the SEC takes for that
  period once both are filed.
- Rows are shaped like the frames API's (`cik`, `entityName`, `loc`, `accn`, `start`, `end`, `val`). `loc` is not in
  companyfacts: it is today's, from the live pipeline's last load_fundamentals output (the live frames, 8,820
  companies) and, for companies missing there, from five live frames of 2021 to 2024 year ends (765 more)
  (`python -m backtest.data loc`, `.cache/backtest/loc.json`). `entityName` is companyfacts' (today's name).

Only the tags the pipeline asks for, and those the market values and the price gate are worked out from, are indexed:
the harness runs `load_fundamentals` once with every frame answered as missing to record the frames it asks for, and
the companyconcept tags are among those and its `FACTS_TAGS`. 202 tags for the code at 719a878, 23.7M facts ending in
2014 or later (earlier facts are never read for these dates), built in one 3-minute pass over the zip's members (never
extracted); 3 more for the market values (earnings per share, below: 1.7M facts in a 43-second pass); and the public
float (`dei:EntityPublicFloat`, below: 82,085 facts in a 48-second pass); 206 tags, 1.4 GB in `.cache/backtest/facts/`.
A fact dated after the day it was filed has a typo in its date (Natural Alternatives' diluted count dated 2031, Amrep's
cover page dated 2033): the share counts and floats take it as of its filing date (`Facts.dated_records`), so it ages
like any other count instead of never growing old (2, 3, 6 and 5 of the counts used on the four dates).

**The pipeline's own code** (`backtest/asof.py`). `fundamentals.load_fundamentals(today=date)` and build.py's metrics
loop (its own statements, cut from `build.main` from the `load_fundamentals` call through the loop that calls
`fundamentals.derive`, run as they are: SIC codes, the derive call and its arguments, the blank-check and stale-basis
checks) run with `pipeline.net` replaced: the frames, companyconcept and companyfacts URLs are answered from the rebuilt
data, the SEC company records (submissions) are fetched live once each and kept in `.cache/backtest/shared/`, and any
other request raises and fails the run. Every module-level cache path under the root's `.cache/` goes to a folder per
date and code, emptied before each run, so the live caches are never touched and dates never mix; `registrations.json`
and `sic.json` go to `.cache/backtest/shared/`, seeded from this worktree's own caches, since they are today's anyway.
The pipeline's time budgets never bind: every lookup is answered locally, a date takes about 30 seconds.

The main branch of September 30, 2026 (`origin/main` at e94594f) moves the derive call and its checks into a function
(`build.company_metrics`) and adds a step after the metrics loop that reads the revenue of companies that tag it under
no label the pipeline reads from their income statements (`statements.read`: each filing's `FilingSummary.xml` and
statement pages, fetched live from EDGAR as they stand today). The harness cuts `build.main` from the
`load_fundamentals` call through the loop that calls either `fundamentals.derive` or `company_metrics`, so that step,
which comes after it, is never run: nothing in companyfacts.zip can make those pages point in time. The companies whose
revenue only that step reads stay without figures in the backtest, as they were before it (APA Corporation, whose
"Total revenues" the main branch reads from its income statement, has none on 2025-09-26); the run says so in its log,
the metrics record it (`statements_step_skipped` in the payload's `metrics_log`), and a `statements.read` call inside
the cut stops the run. How the main branch's code fares is under "The main branch" in Checks.

**Listings and prices** (`backtest/prices.py`). The listings on a date are today's (a snapshot of
`universe.load_universe`) that had a weekly close that week on Yahoo. Yahoo's weekly bar closes on the week's last
trading day and its close is adjusted for every later split, so the price on the date is that close times every split
ratio after the date (the split events of the chart). `high52` is the highest weekly close of the 52 weeks to the date
(the live pipeline uses Yahoo's intraday 52-week high, so drawdowns here are a little smaller), `weekly` those 52
closes in `market.price_history`'s format, on the same basis.

That holds only where every split Yahoo lists happened, in the week it lists it, and Yahoo has applied it to the closes
before it, and where Yahoo has scaled no closes without listing why. A gate (`prices.clean`) checks every chart for this
before any price, market value or return is taken from it. Each split of a ratio of 1.5 or more either way (smaller
ones are stock dividends and spin-offs Yahoo files as splits, taken as listed), latest first, is read four ways, and
`_decide` takes the first of them that gives a reading, in this order:

1. the digits of the closes (the tick test, `grid_test`): a US stock at $1 or more trades in whole cents, so the
   closes of the 26 weeks before a split (after the split before it), priced as traded once with the split applied and
   once without, are whole cents on the right reading only. Of two readings one of which is a whole multiple of the
   other (a 1-for-n reverse split applied or not), the finer is right where 80% or more of its closes of $1 or more are
   whole cents and the chance of that on the coarser reading (1 in n a close) is under 1 in 1,000, or where the coarser
   puts half or fewer of its closes on whole cents and the finer doesn't; the coarser is right where it fits and the
   finer puts half or fewer on whole cents. Each reading needs 8 closes of $1 or more. Of two readings neither of which
   is a multiple of the other (Sturm Ruger's listed 374-for-1000 split), the one that fits is right where the other
   puts half or fewer on whole cents and its own fit is beyond chance (under 1 in 1,000 at one in two a close).
   Artelo Biosciences' closes before its 1-for-3 reverse split of March 10, 2026 are all multiples of 27 cents (that
   split and the 1-for-9 after it taken out, whole cents from $1.14 to $1.95), so Yahoo applied it, though the closes
   then jumped from $33.84 to $71.73 the next week;
2. the SEC's public float (`float_test`, `dei:EntityPublicFloat`: the market value of the shares held by non-affiliates
   on the day a 10-K states, usually its second quarter's end), from every filing that gives one, against the market
   value the chart gives that day: the cover page's share count dated nearest it within 120 days (else the balance
   sheet's), with no split of 1.5 or more between the two days, times the price then, read with the split and without
   it. R = float / market value is the non-affiliates' share, at most about 1 (over every listing's floats from 2021
   to 2025, median 0.91, 90% between 0.32 and 1.04). A float counts for a reading where that reading puts R between
   0.25 and 1.2 and the other under 0.1 or over 1.35. Two floats or more that agree (none the other way) decide. One
   float decides where the closes (test 3) say nothing or the same, and always where it fits only the split applied:
   Yahoo applies nearly every split it lists, so one float that fits only the applied level outweighs a jump in the
   closes, while it takes two to overturn the closes' reading of "applied". The tick test and two floats or more that
   disagree make a split unclear. A float given in thousands of dollars (R from 150 to 2,500) is read in thousands; an
   R beyond that, or under 0.004, isn't used;
3. the closes either side of the split (`closes_test`): a jump by the split's ratio, both from one close to the next and
   between the median closes of the three weeks either side, anywhere from three weeks before the listed week to three
   weeks after it, is a split Yahoo has not applied (and its week the week it took effect); none, with the closes and
   the adjusted closes running on across the listed week, is a split Yahoo applied. This is the test the previous
   version had alone, on one pair of closes at the listed week, which a two-week spike after a split Yahoo had applied
   could fool (Cingulate's of August 2024, whose closes on Yahoo's chart go $2.44, $17.69, $9.69, $7.89, $5.92);
4. the share counts (`shares_test`): a split changes every share count, and the filings after it restate the counts
   they repeat for dates before it, so a count a later filing gives for a date before the split, changed by its ratio
   (within 5%, times the ratios of any later split made before that filing), shows it happened, as do counts either
   side of it (within 200 days) apart by about its ratio; counts repeated unchanged in two filings or more after it,
   with none restated ("restatements"), or counts either side level ("straddle"), show it didn't.

A split read as applied is taken as listed, or, where the share counts were repeated unchanged after it (restatements),
as a scaling of prices only, which share counts aren't carried through: a spin-off Yahoo files as a split, such as XPO's
of RXO in November 2022 or Dell's of VMware in November 2021, or a change in the shares a depositary share holds. One
read as not applied has the closes before its jump (close and adjusted close alike) divided by its ratio and is listed
at the jump's week. One read as not applied whose share counts didn't change, or that shows no jump in the closes and
no count known to change, never happened and is dropped (a phantom); one read as not applied by the floats whose counts
did change and whose closes run on across it is taken as applied (SITE Centers' 1-for-4 reverse split of August 2024,
whose floats' level error is the unlisted spin-off adjustment below); one read as not applied with a count change but
no jump is unclear. Counts merely level either side ("straddle") can't tell a split that never happened from one beside
a large issue of shares (Wheeler REIT's), so they make a phantom only of a split another test already reads as not
applied. A split listed twice in a week is kept once, with the closes rescaled for how many times Yahoo applied it (the
closes test across the week); one no test reads is unclear. A bar right at a split whose close sits off both its
neighbours by the split's ratio (a close Yahoo left unadjusted) is divided by it.

Then the scalings Yahoo made without listing anything:

- unlisted splits it didn't apply either (`find_unlisted_jumps`): consecutive cover-page counts that fall by a whole
  factor of 3 or more (within 10%) within 200 days, with no split listed within 45 days of them, and a jump of the
  closes by that factor between them (from one close to the next and between the median closes either side, both
  within a fifth of its log): the closes before the jump are divided by it and the split is listed at the jump's week;
- unlisted scalings it did apply (`find_adjustments`): where every float before some day is implausible and those
  after are not (at least one typical), the ones before agreeing within 1.6x, by a level error of 1.5 or more either
  way, Yahoo scaled the closes before with no event listed. The digits place it (the week from which the closes are
  whole cents as they stand) and give its exact factor (the one that makes the closes before it whole cents); a
  cover-page count that falls by a whole factor near the floats' between them makes it a split (share counts carried
  through it), else it scales prices only (a spin-off). It takes the digits, such a count, or two floats or more far
  out (R over 2 or under 0.05) with two typical ones after to bear it out; a break of one float that none of these bears
  out is left as it is and listed. Where the digits can't place one that is borne out, the days between the two
  floats have no price level and the listings get no market value on a date between them.

What the gate found over the 5,390 charts (1,141 with splits; 1,855 splits listed, 1,712 of them of 1.5 or more either
way), from `python -m backtest.validate past`:

- 1,608 splits applied as listed; 42 applied to prices only (spin-offs such as XPO's, Dell's, DuPont's of Qnity in
  November 2025 and Liberty Global's of November 2024, and changes in what a depositary share holds, such as Amarin's
  in April 2025); 12 listed twice (8 applied twice: SVRN, RETO, XRTX, CENN, ENVB, ALM, SPRB, RVSN; 4 applied once:
  EDU, MIND, ATCH, and Covenant Logistics' 2-for-1 split at the turn of 2025, so CVLG on September 30, 2022 was $28.70,
  not $57.40);
- 22 not applied, 4 of them in another week than listed (Offerpad's 1-for-10 reverse split is listed on June 9, 2026
  and taken in the week of June 1, where its closes jump; AKAN, BURU and VIP's of August 2021); among them BRTX, GTBP,
  NFE, OPTT, NRSN, JL, YAAS and IGR, so Ocean Power Technologies on September 30, 2022 was $0.82 and BioRestorative
  Therapies' 2025 return is -91% (not +78%);
- 2 phantoms: Sturm Ruger's 374-for-1000 split of October 24, 2025, which Ruger never made (the tick test: 26 of 26
  closes before it whole cents as they stand, 1 of 26 with it applied; 6 of its share counts repeated unchanged by
  the filings after it, none restated; two floats agree), and U Power's (UCAR) 1-for-10 of April 2026;
- 8 unclear: AZ, BORR, HUBC, IPDN, PAMT, PPCB, WIMI, ZCMD (no test reads them; PPCB's one float says not applied, but
  its closes show no jump within 3 weeks and its counts changed). All but IPDN and PAMT are of companies outside the
  US, which get no market value anyway;
- of the single splits, the tick test read 697, two floats or more 362, one float 149, the closes 465, the share counts
  1 and the closes with the share counts 1;
- 5 bars Yahoo left unadjusted at a split corrected;
- 6 unlisted splits Yahoo didn't apply (BCTX, BTLN, DUKR, GITS, IDAI, NYC), each a fall of the cover-page counts by 6 to
  24 times with a jump of the closes by the same factor;
- 7 unlisted scalings Yahoo did apply, each placed by the digits of the closes: to prices only, LGL Group's (x2.652 on
  October 3, 2022, the spin-off of M-tron on October 7), SITE Centers' (x3.361 on September 30, 2024, the spin-off of
  Curbline on October 1), Adeia's (x2.303, October 2022), BV Financial's (x1.531, July 2023) and Addentax's (x0.106,
  August 2022); splits, Curis's 1-for-20 of September 2023 and Dianthus Therapeutics' 1-for-16 at its merger the same
  month;
- 33 breaks of a single float left as they are, borne out by neither the digits nor the counts.

Against the version before the gate (`market_v4.json`, `returns_v4.json`), 10, 10, 7 and 5 market values a date moved
by more than 1.5x (among them ARTL, CING, OPAD, RGR and SITC, back to their traded levels; HEI and HEI.A, below) and 14,
10, 6 and 5 prices changed; 1, 5, 4 and 6 returns changed (among them NFE's of 2023, below, and the returns of the
unlisted splits: GITS's 2024 return is -47% where it was +859%). Three of the previous version's corrections were wrong
and are undone: Cingulate's 1-for-12 of August 2024, Artelo's 1-for-3 of March 2026 and MicroCloud Hologram's 1-for-10
of February 2024 read as applied now (the tick test for all three, with two floats for Cingulate's), so CING on
September 30, 2022 was $1.07 (not $12.84).

A listing whose chart has an unclear split dated after the start of the 52 weeks before a date and up to 52 weeks after
it (where it would touch the year's closes, the price on the date through the splits after it, or the return) is left
out of that date, listings and returns alike: 3, 1, 3 and 5 listings on the four dates (BORR, IPDN and PAMT; IPDN; AZ,
PPCB and WIMI; AZ, HUBC, PPCB, WIMI and ZCMD). One dated later only leaves the price on the date unknown (the closes of
the date's two years agree with each other whatever it was): the listing keeps its return and gets no market value
(`none_level_unknown`: 5, 5, 2 and 0 listings, all outside the US). The gate reads charts and filings from after a
date, but on how the data were recorded (the digits of the closes, the floats and counts the filings give), never on
how the stock did, with these exceptions: the closes test reads the closes of the split's own weeks, and so do the
tests that place an unlisted split by the jump in the closes and rescale a split listed twice (GITS's unlisted 1-for-18
of January 2025, SPRB's twice-listed split of August 2025); for an event inside a date's 52 weeks after, those weeks are
part of the year being measured (the counts that follow cover only listed splits) (of the splits of 1.5 or more inside them,
of listings with a return, the closes decided 81, 77, 89 and 152 a date, one of 2023's with the counts; the tick test,
the floats and the counts the rest, 163, 252, 293 and 414). Leaving out a listing for an unclear split rests on
whether its split can be read, not on how it did; but P.A.M. Transport (PAMT) is left out of September 30, 2022 for its
2-for-1 split of March 2022, which the tick test nearly reads as applied (26 of 26 closes whole cents applied, 14 of 26
without it, just over the half that would decide), and it may have passed the screener then (its return over the year,
on Yahoo's chart: -30%, against +3.9% for the average listing).

**Market values** (`prices.share_count`, `Facts.share_counts`). The market value on a date is a share count filed with
the SEC before the date times the date's price as traded. Nothing from after the date enters it, neither today's share
count nor a later price, so whether a listing can be screened rests on its filings before the date alone (and on the
price gate above, which reads later charts and filings only for how the closes were recorded). The counts:

- the cover page's count of shares outstanding (`dei:EntityCommonStockSharesOutstanding`) of each of the newest three
  filings that give one, as of the date the filing gives it for. The SEC's bulk file keeps only counts given without a
  share class, so a company whose cover page gives one count per class has none here (none of the 143,962 cover pages
  indexed for today's listed companies gives two counts for one date: nothing is added up over classes);
- the newest balance-sheet count (`us-gaap:CommonStockSharesOutstanding`);
- the newest diluted weighted average count (`us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding`, the shortest
  period ending at the latest date);
- as a check only, never used itself, the count the newest earnings per share imply (profit to common shareholders,
  else net income, over diluted earnings per share, else basic and diluted, else basic, for the same period, where
  earnings per share is 5 cents or more either way); and the newest stockholders' equity, as book value.

A count dated over 457 days (about 15 months) before the date is too old to use: Tilly's newest cover page count without
a share class was from 2015 (11.98 million shares against 30.2 million diluted in 2022). A count under 10,000 shares is
a placeholder (a company files 100 or 1,000 shares before the offering or merger that lists it: Paramount Skydance's
cover pages of 2025). A count dated after the day it was filed is taken as of its filing date (see "SEC figures" above).
Each count is carried through the splits between its date and the date; a split between a count's date and the day it
was filed may or may not be in it (companies restate counts for a split made before they report, as Alphabet's June 30,
2022 balance-sheet count already is for its 20-for-1 split of July 2022; CTO Realty's of June 30, 2022 is not for its
split of July 1), so such a count is taken on whichever basis agrees better with the counts no split separates from
their filing, and on its filing date's basis where there are none.

The counts are grouped into those that agree (each within 3x of the next, in order of size). The group with the most
counts is the reference (ties to the group holding more kinds of count; none if still tied). The count used is the
newest of the cover page's, the balance sheet's and the diluted one (on the same date, in that order) that is in the
reference group, or that is above every count outside its own group by over 3x, no older than any of them and no
thousand-fold slip (about 1,000 or 1,000,000 times them, within 25%): shares issued since the older counts, as for QXO,
whose cover page of August 2024 gives 409 million shares against 0.66 million in its June balance sheet, or a company
that just went public, whose earlier counts are from before. And its market value must be at least 1% of book value
(where positive). Any other count is a filing mistake, and the next is tried: one far below the others (a count in
thousands: Citizens & Northern's cover page of August 2022 gives 15,493 shares, against 15.5 million in its balance
sheet; one share class of several; a decimal slip), a thousand-fold slip (Hecla Mining's cover pages of 2024 give 630
billion shares, against 622 million diluted), or a market value under 1% of book (Hub Group's diluted count for the
quarter to June 2022 is 33,935, a thousandth of its shares). Treating any count outside a third to 3 times the others' median as a
mistake, as first planned, would have valued QXO from its June count, at about $10 million against the $971 million
of cash in that balance sheet, and the companies that sold many times their shares in a quarter, which "Under 1x cash"
turns on, at their old counts; so a count far above the others is taken as issuance unless it is a thousand-fold
slip.

One share class of several within the 3x: where the diluted count and the count earnings per share imply agree within
10% (both count every class the profit is divided among), the count chosen is under 60% of the diluted one, and the
public float filed before the date says so too (the float would be over 1.35 times the market value on the chosen
count, and is at most 1.2 times it on the diluted one), the chosen count is of one class and the diluted count is used.
HEICO's cover pages give the count of one of its two classes, 39% to 45% of the whole: on 2023-09-29 to 2025-09-26 HEI
and HEI.A are valued on the diluted count (on 2022-09-30 the float filed before it is dated April 30, 2021, before its
chart begins, so the one class's count stands), and so is the listing now named Dragonfly Energy (DFLI) on 2022-09-30.
The float has to say so: the weighted averages of a company that sold pre-funded warrants count the shares the warrants
buy, which aren't issued yet (Immunic's and Quince Therapeutics' are 3 to 6 times the shares on their cover pages in
2026, and their floats fit the cover pages); in a one-off run of the rule without the float's word, 17 listings on
2026-09-25 would have been valued at 1.7 to 12 times Nasdaq's market value.

Listings that are not US companies (Nasdaq's country neither the United States nor blank, or an SEC location outside
the US) get no market value: many trade depositary shares that each hold several of the shares their filings count,
and which ones do, and how many each holds, isn't in the filings the backtest reads. Only the giants list could take
them (the screener and the other lists need a US company); the giants list here is of US companies only. 784, 836, 906
and 1,052 listings on the four dates, 342 to 567 of them with SEC figures (in `after`'s metrics); in the lists of the
version before the second review (`results/v3/`) they were one giant a date (HDB, HDB, SHOP, SHOP).

Of the US companies with an SEC location (what the screener can take: 3,324, 3,404, 3,518 and 3,659 on the four
dates):

| date | cover page | balance sheet | diluted | no count within 457 days | counts disagree | age of the counts used, every valued listing: median / 90th pct / most (days) |
|---|---:|---:|---:|---:|---:|---|
| 2022-09-30 | 2,776 | 58 | 313 | 169 | 8 | 61 / 92 / 457 |
| 2023-09-29 | 2,890 | 58 | 321 | 127 | 8 | 60 / 91 / 456 |
| 2024-09-27 | 3,010 | 52 | 335 | 115 | 6 | 58 / 89 / 455 |
| 2025-09-26 | 3,123 | 54 | 360 | 113 | 9 | 57 / 88 / 413 |

Of every valued listing, 88.2% to 88.6% a date took the cover page, 1.5% to 1.8% the balance sheet and 9.8% to 10.2% the
diluted count; 16, 17, 27 and 45 counts were taken as shares issued since the others, and 3 to 6 a date on their filing
date's split basis. Of `before_margin3`'s passers, 114, 81, 63 and 66 took the cover page, 1, 4, 2 and 0 the balance
sheet and 22, 14, 11 and 11 the diluted count; the oldest count any of them used was 153, 118, 363 and 149 days old. The
US companies left without a count within 457 days did worse than the valued ones over the next year by 0.4 to 13.5
points on average (-0.03, -5.9, -4.7 and -9.5 points against +0.4, +2.3, -1.8 and +4.0), far less than the -55 to -100
points of the listings the version before the second review left out for a price fall after the date. Among the US
companies left without a count are companies with two share classes whose filings give every count per class, which the
bulk file leaves out: the passers of that version that lost their market value were Haverty's, Bel Fuse, Biglari
Holdings, Ingles Markets and Oil-Dri (valued then from today's count) and Hub Group (its diluted count in thousands,
above).

A diluted count is of the shares the company's profit is divided among: for a company whose founders hold units of an
operating partnership beside the listed class (an "Up-C"), only the listed class. Those companies are valued below
the whole company's worth (see the market check below); with no count per class in the bulk file, the backtest can't
add the units. That flatters the price to sales of the passers among them (GEN Restaurant Group in 2024, RMR Group
from 2023, whose 2022 diluted count still held its parent's units), and it put UWM Holdings on the banks and insurers
list on three dates in the version before the screener measured book value without minority holders' stakes where a
company tags only that total (fundamentals' equity_includes_minority): its book then counted the founder's units.

Against the version before the second review (`market_v3.json`, which took today's share count where a cover page
failed tests built on today's count and left out listings whose price fell over 20-fold by September 2026): 97, 107,
112 and 113 market values a date moved by more than 1.5x in the second review, 70 to 79 of them listings valued from
today's count before.

The first run of the backtest took today's share count for every listing without a usable cover page: it valued the
heaviest issuers at hundreds to millions of times their market value then (Empery Digital at nearly $80 trillion on
September 30, 2022). `build.split_market_values` is not run: the counts already are the filings'.

**Insider purchases** (`backtest/form345.py`), for code whose `screener.run` takes `insiders=`. The SEC's insider
transaction data sets (`{2022..2025}q3_form345.zip`) hold every Form 3, 4 and 5 as tables. Each Form 4 (not 4/A, as the
pipeline's daily scan reads form type 4 only) filed in the 30 days before the date that records an open-market purchase
is turned back into the XML parts `insiders.parse_form4` reads (issuer, reporting owners and roles, non-derivative
transactions and holdings in the form's order, with their ownership footnote ids), and the pipeline's own `parse_form4`
and `insiders.build` merge co-filers, add up each buyer's stake change and tier each company, exactly as every night.
The screener gets `{symbol: {"tier", "avg_price", "total_value"}}`. About 10,000 Form 4s a window (9,837 to 10,274);
271 to 382 listed companies with purchases per date, 118 to 156 of them in tiers 1 and 2 (the live run of 2026-09-29
had 357 and 156).

**Analyst counts** are not point in time (Yahoo gives today's only): every variant gets an analyst counter that knows
none, so "Few analysts" gives nobody points and nobody is flagged undiscovered, before and after alike.

## What is not point in time

- **Listings**: today's. Companies delisted, acquired or bankrupt since a date are missing from it (survivorship bias,
  which flatters every list by an amount the backtest can't measure), and companies listed since are left out by
  having no close then.
- **Sector and industry labels**: today's Nasdaq labels (the Finance exclusion, REIT labels, industry groups), so a
  company that has since changed business is listed under its business today: AI Financial (AIFC) was JanOne, an
  appliance recycler, in 2023, and is labeled a financial company on that date. One company with a capped return can
  carry a small list's mean (in the results of the version before the second review AIFC, at +260 points, lifted the
  mean of 2023's list of 30 other financial companies under book value from +8.6 points without it to +17.0), which is
  why every list also shows its median, its mean without capped returns and its trimmed mean.
- **SIC codes and SEC registrations** (where a company is incorporated and based): today's SEC records.
- **`loc`**, the location in the frames: today's (see above). With Nasdaq's country (today's), it also decides which
  listings get a market value at all.
- **Analyst counts**: none at all (see above).
- **Company names**: today's.
- **Charts, and the floats and share counts the price gate reads**: today's charts, and every filing in
  companyfacts.zip, including those made after the date. The gate reads them for how the closes were recorded, not
  for how a stock did (see "Listings and prices" for the exceptions, the closes of a split's own weeks, and for the
  3, 1, 3 and 5 listings left out a date for an unclear split).
- **The main branch's income statement step**: never run (see "The pipeline's own code").
- **Which span the frames take for a period** when a company filed two that fit it: the one the SEC labels in that tag
  today goes first (see "SEC figures as of each date"), a label the SEC may have given after the date.

## Checks

`python -m backtest.validate frames` rebuilds nine frames as of today (yearly, quarterly and instant; dollars, shares
and a ratio) and compares them with the live frames API company by company. `python -m backtest.validate market`
rebuilds the market values of a recent Friday (2026-09-25) and compares them with Nasdaq's in the snapshot of today's
listings. `python -m backtest.validate today` runs the code at 719a878 through the harness as of the live run's market
date (2026-09-29) with that run's own listings and market values and no price histories, and compares the screener's
passes with the live `screener.json` and every company's derived metrics with the live run's. `python -m
backtest.validate past` checks the four dates themselves, which the other three, all as of 2026, can't: the price gate
over every chart, the source and age of every share count, each date's cover-page counts against the same companies'
balance-sheet and diluted counts, and every valued listing's market value against the SEC's public float. The figures
are in `.cache/backtest/results/` (`validate_frames.json`, `validate_market.json`, `validate_today.json`,
`validate_past.json` and `validate_past.txt`), as of October 1, 2026 (the past dates as of October 3, 2026), and every
figure below is in them but the main branch's, which are in its runs' own results:

- **Frames** (rebuilt as of 2026-10-01, the nine frames together): 39,367 companies in the live frames, 39,096 in the
  rebuilt ones, 39,034 in both. For 38,844 of those (99.5%) the rebuilt row is the live one (value, filing, start and
  end); for 39,027 (99.98%) the value is. Of the 190 rows that differ, 185 take the live value from a filing made after
  companyfacts.zip was built and 5 give the same value from another filing's copy. 333 companies are only in the live
  frames (all from filings made after the zip), 62 only in the rebuilt ones (27 whose fact the SEC labels for the
  period in companyfacts.zip but leaves out of today's frame, 35 placed by another tag's label or the rule).
- **Market values** (2026-09-25, against Nasdaq's scaled to that Friday's close): of the 3,235 valued from the cover
  page, the median ratio is 1.000, 94.3% are within 2%, 96.9% within 10% and 98.3% within 25%; of the 59 from the
  balance sheet, 72.9%, 91.5% and 91.5%; of the 398 from the diluted count, 42.2%, 71.9% and 78.6%. Among the diluted
  counts furthest below Nasdaq's are Up-C companies (GEN Restaurant Group, Smith Douglas Homes, MarketWise, OPAL Fuels,
  UWM, TWFG, at 16% to 24% of Nasdaq's), whose diluted count is of the listed class only. The cover-page counts
  furthest off are of companies that issued many shares in a merger after their latest filing (Korsana Biosciences and
  Host Digital, with a reverse split in September and August 2026, and Katapult, at 1.5% to 6% of Nasdaq's), and of
  listings whose reverse split between that Friday and the snapshot puts the scaling itself off by about the split's
  ratio (Matinas BioPharma, 15.0, and Brightline Interactive, 7.8, whose closes jump that week with no split listed;
  VerifyMe, 10.0, its split listed on September 29).
- **The pipeline as of 2026-09-29**: the harness's screener passes the live run's 21 companies exactly and leaves out
  the same 3 as unverified (BLBD, HNRG, SMPL). Derived metrics: 4,116 listings with figures against the live run's
  4,114, 4,113 in both; all 18 compared fields (fiscal year and its end, balance sheet date, revenue, net income with
  and without one-time items, margin, free cash flow, cash, debt, net cash, equity, debt to equity, sales growth,
  profitable years, years checked, shares, location) agree for 3,983 (96.8%). Of the other 130, the live run read a
  newer filing for 118 (made after companyfacts.zip was built) and 12 differ otherwise (AMCX, CIA, ESTC, FLUX, FNGR,
  IDA, IOTR, NEON, OFAL, PUBM, SORA, STI), none of them a screener pass.
- **The past dates**: the price gate's findings, the sources and ages of the counts and the listings left out are
  given under "Listings and prices" and "Market values" above. Cover-page counts against the same companies'
  balance-sheet counts (10,129 company-dates over the four dates, each carried to the date on its filing date's split
  basis): median ratio 1.000, within 10% for 91.7% to 95.0% a date, within 2x for 98.1% to 99.1%; of the 145 outside
  2x, 116 have the cover page newer and larger (shares issued after the balance sheet's date), 16 a split between the
  balance sheet's date and its filing, 8 a thousand-fold slip in one of the two and 5 a balance-sheet count larger than
  the cover page's (Kforce's, twice, and Jack in the Box's take in treasury shares). Against the diluted counts
  (11,212): within 10% for 85.3% to 90.2% a date, within 2x for 95.9% to 97.2%; of the 358 outside 2x, 159 are
  thousand-fold slips (142 of them a diluted count a thousandth of the cover page's, given in thousands), 156 cover
  pages newer and larger (a weighted average lags issuance), 26 a split between the diluted count's period and its
  filing and 17 a diluted count larger than the cover page's (the units of an operating partnership counted as if
  exchanged, for Cohen & Company and Finance of America; convertible securities and warrants). Of the 272 cover pages
  over 2x larger than, and newer than, the other count, the next cover page (filed after the date, read for this check
  only) agrees with them within 1.5x for 205, gives more shares still for 60 and fewer for 5 (2 have none), so these
  are shares issued, not mistakes. Over the four dates, the US companies left without a count did as given under
  "Market values"; the 3, 1, 3 and 5 listings left out for an unclear split had, on Yahoo's charts as they are,
  returns of +114%, +64% and -30% (2022), -83% (2023), +278%, +303,233% and -52% (2024) and -33% to -100% (2025).
- **The public float** (`float_check`): each valued listing's market value on a date against the public float of the
  newest day within 300 days before it, from whichever 10-K gives it (filed before the date or after: a check, never an
  input), as R = float / (the count used on the date x the price on the float's day, on the date's share basis). R is
  the non-affiliates' share of the company, so it should sit a little under 1. 3,004, 3,055, 3,157 and 3,295 listings
  compared on the four dates; median R 0.901, 0.904, 0.897 and 0.872; 5th to 95th percentile 0.33 to 1.03, 0.33 to
  1.01, 0.33 to 1.01 and 0.30 to 1.03; within 0.4 to 2.5 for 90.5%, 91.6%, 91.4% and 90.9%; over 2.5 for 64, 42, 44 and
  29 (42, 32, 29 and 15 of them floats given in thousands of dollars), under 0.4 for 221, 214, 227 and 271. Every
  passer or list member of any variant 2.5x or more off is listed in `validate_past.txt` with its cause (26, 27, 17 and
  14 a date, 84 in all, 55 companies), and none is left unexplained: 47 are companies whose affiliates hold most of
  the shares (R under 0.5 at most of their floats since 2021, or on every date they are valued on: Global Industrial,
  CompX, Kronos Worldwide, Enact and Arhaus among them); 22 are floats in the wrong unit (19 given in thousands of
  dollars, First American's in millions, Warrior Met Coal's and Dianthus's a thousand and a million times too small);
  2 are share counts given as the float (Sadot 2022, RPC 2023); 2 repeat the year before's float (Century Therapeutics
  and Greenlane, 2025); and 11 are labeled by the check as one year's float out of line with the same company's floats
  of the other dates, at which the price level and the count fit (R 0.14 to 0.39 for nine of them: VIP, RPID, OPRT in
  2022, VBIO, NKTX, MYSE in 2023, GRI, BJDX, AIFA in 2024), though three of those are shares issued after the float's
  date instead: VBIO, GRI and BJDX issued 2.4 to 4.9 times their shares between June 30 and the August cover page the
  date uses, both counts in the same 10-Q, and at the June 30 count R is 0.94 to 0.97 (the check compares with the
  nearest cover page, not the count of the float's own date). The other two are far above: Gaxos.ai's float of $270
  million for June 2024, against $6.5 million and $9.7 million the years either side, and PMGC Holdings' of $101
  million for June 2025, against $7.6 million the year before, each for a company its count and price put at about $3
  million that day: both typos in the float.
- **The main branch** (the commands under "Running it"; `merged_main_2025-09-26.*` and `merged_full_2025-09-26.*` in
  the results). On 2025-09-26, `origin/main` (e94594f) with this worktree's screener.py over it runs through the
  harness (the statements step skipped, as above) and passes the same 77 companies as `after`, in the same order, with
  the same scores, flags and figures; the same 9 are unverified, and the financials list (46), the giants (28) and the
  industries are the same. Its derived metrics agree with `after`'s for all 3,889 listings with figures in every field
  but one, `cash_and_st_investments`, which this worktree's fundamentals.py adds and the main branch's lacks, so its
  "Under 1x cash" list is empty (43 for `after`). With build.py and fundamentals.py also merged with this worktree's
  changes (`git merge-file`, no conflicts), the metrics, passes, scores, flags and every list are identical to
  `after`'s: merging the main branch changes nothing in the backtest on that date, since the one thing it adds to the
  figures is the step the harness can't run.

## The review of October 1, 2026

A review of the version before this one (`market_v4.json`, `results/v4/`) found ten defects; this version fixes them,
the seventh in part. The figures under "Method", "Checks" and "Results" are this version's.

1. **A split that never happened** (Sturm Ruger's 374-for-1000 of October 24, 2025, which made every earlier price
   0.374 of what it was: RGR is a passer on 2022-09-30 and 2025-09-26). Fixed by the price gate's tick test and share
   counts: the split is a phantom and dropped. RGR on 2022-09-30 is $50.79 (not $19.00); its score falls from 82 to 75
   on 2022-09-30 (`before` and `before_margin3`) and from 56 to 40 on 2025-09-26 (`before_margin3`); no variant's
   passes change.
2. **Three wrong corrections** (Cingulate's 1-for-12 of August 2024, Artelo's 1-for-3 of March 2026 and MicroCloud
   Hologram's 1-for-10 of February 2024, which a spike in the closes after the split made the old one-pair test read
   as not applied). Fixed by the tick test and the floats, which read all three as applied; the closes test now needs
   a jump both from one close to the next and between the median closes either side.
3. **A misdated split and unlisted spin-off adjustments** (Offerpad's 1-for-10 listed on June 9, 2026, a week after its
   closes jump, which left its earlier prices 10 times too low: $0.121 on 2022-09-30 for $1.21; LGL Group's and
   SITE Centers' closes, scaled by Yahoo for the spin-offs of M-tron and Curbline with no event listed). Fixed by the
   floats (Offerpad's, read as not applied, placed at the jump within three weeks of the listed week) and by the
   float pass for unlisted adjustments, placed and sized by the digits of the closes; the same pass found 5 more.
   Limit: a level break shown by one float only, which neither the digits nor a count change bears out, is left as it
   is (33 charts), and a split no test reads stays unclear (8).
4. **One share class taken for the company** (HEICO's cover pages count one of its two classes, within the 3x the
   counts are grouped by). Fixed by the one-class rule under "Market values", which needs the diluted count, the count
   earnings per share imply and the float filed before the date to agree. Limit: it can't fire without a float filed
   before the date that the chart can price (HEI on 2022-09-30), and Up-C companies stay valued on their listed class.
5. **Counts dated after their filing** (typos such as Natural Alternatives' diluted count dated 2031, which never aged
   out). Fixed: taken as of their filing date. Limit: with that count aged out, Natural Alternatives is valued on
   2024-09-27 from its May 2024 10-Q cover page, 3,280,037 shares, a typo for the 6,200,869 the same filing's balance
   sheet gives, which is within the 3x the counts are grouped by: about half its value that day (it is on no list).
6. **Corrupt adjusted closes** (New Fortress Energy's of 2023, which imply 69% of the price paid out in dividends).
   Fixed by the dividend-factor test under "What it measures", with the closes and the listed dividends instead (NFE's
   2023 return is -70%, not -2%). Limit: a year whose adjusted closes are wrong by a factor between 0.98 and 1.5 is
   taken as it is.
7. **Unclear splits judged on later moves** (the old rule left a listing out of a date for an unclear split dated any
   time after the start of the 52 weeks before it, and the only test then read the closes around the split, so being
   left out could follow from how the stock moved; the README said it didn't). Fixed in part: a split is now unclear
   only where the tick test, the floats and the counts don't read it either; one dated after the date's 52 weeks after
   leaves the listing's return in and only its market value out; one inside the two years still leaves the listing
   out, and a split inside the 52 weeks after may still be read by its closes (see "Listings and prices", with PAMT).
8. **One return inside the caps carrying a small list** (BTCS, +270 points in 2024). Fixed: every group also shows its
   trimmed mean, without its single largest return either way, and names the company left out.
9. **Figures the checks didn't produce**. Fixed for the checks: every figure under "Checks" is in the checks' files
   (but the main branch's, in its runs' results), now including the US-only returns of listings without a count, the
   next cover pages, the cover pages giving two counts and the float check. The past dates' counts and shares under
   "Listings and prices" and "Market values" are in `validate_past.txt` too, but for the listings outside the US with
   SEC figures (from `after`'s metrics) and the one-off run of the one-class rule without the float; the examples
   quoted there are from the filings and charts themselves, the facts index's sizes from `facts/manifest.json` and the
   insider counts from the dates' insider state files.
10. **Stale state**: `market.json` and `returns.json` weren't keyed to the code they are worked out by, nor the insider
    purchases to `backtest/form345.py`. Fixed: they are keyed and rebuilt on a change (see "Running it").

## Results

Every figure rests on four 52-week periods in four different markets (the median listing was 37.9% below its 52-week
high on September 30, 2022 and 13.3% on September 27, 2024), so a pooled figure counts each company once for each date
it passes, and nothing here is more than four years of evidence. Survivorship (companies delisted since are missing)
flatters every list, and no company gets the few-analysts points in any variant. The full reports are in
`.cache/backtest/results/`: `<label>.txt` for each variant, `compare_<a>_vs_<b>.txt` for each step and
`chain_<labels>.txt`, which lists every company each step added with its relative return. The block below is written
by `python -m backtest.screener tables <labels> --readme` from those files and holds only the variants it names.

<!-- results:begin (written by `python -m backtest.screener tables before before_margin3 after_oldweights after_pb after --readme`; edit the code, not this block) -->

The tables below are the output of `python -m backtest.screener tables before before_margin3 after_oldweights after_pb
after --readme`, over the variants before, before_margin3, after_oldweights, after_pb, after.

Returns are in points against the average listed stock; intervals are 95%. "Capped" counts the returns at the 1st or
99th percentile the returns are winsorized at, and "without capped" is the mean of the rest: a return inside the caps
stays in, however large (BTCS's +270 points in 2024 is under the 99th percentile). "Trimmed" is the mean without the
group's single largest return either way (the company left out is named), so that no one company carries a small group.
Pooled means count each company once for each date it is in the group, with intervals that resample each date's
companies within that date; a pooled difference between two groups is the weighted average of each date's own difference
(each date weighted by n1 x n2 / (n1 + n2) of its two groups), with the same resampling.

### Passes and how they did

| variant | date | passes | mean relative return [95% CI] | median | without capped | trimmed | beat the median stock |
|---|---|---:|---|---:|---|---|---:|
| before | 2022-09-30 | 57 | +20.5 [+10.0, +31.0] | +16.6 | none capped | +18.5 [+8.2, +29.2] (without BBW) | 72% |
| before | 2023-09-29 | 40 | -2.9 [-17.0, +12.1] | -6.8 | none capped | -6.1 [-17.9, +9.8] (without TAYD) | 45% |
| before | 2024-09-27 | 26 | -1.9 [-21.2, +20.1] | -24.7 | none capped | -8.5 [-25.6, +14.9] (without METC) | 38% |
| before | 2025-09-26 | 18 | +1.9 [-13.1, +15.6] | +0.8 | none capped | +5.8 [-10.3, +18.7] (without BBW) | 50% |
| before | pooled | 141 | +7.4 [+0.1, +14.9] | +2.3 | none capped | +6.2 [-0.8, +13.8] (without METC) | 55% |
| before_margin3 | 2022-09-30 | 137 | +18.1 [+11.1, +25.6] | +13.2 | +17.0 [+10.1, +23.6] (1 capped) | +17.0 [+10.2, +24.5] (without ANF) | 70% |
| before_margin3 | 2023-09-29 | 99 | +2.6 [-5.9, +11.6] | -4.4 | none capped | +1.3 [-6.8, +10.5] (without TAYD) | 47% |
| before_margin3 | 2024-09-27 | 76 | -2.6 [-16.9, +14.3] | -21.4 | none capped | -8.1 [-19.0, +9.0] (without PSIX) | 46% |
| before_margin3 | 2025-09-26 | 77 | +13.3 [+4.4, +22.6] | -0.4 | none capped | +11.3 [+2.9, +20.7] (without HBB) | 60% |
| before_margin3 | pooled | 389 | +9.2 [+4.5, +14.4] | +2.6 | +8.7 [+4.1, +13.5] (1 capped) | +8.1 [+3.8, +13.5] (without PSIX) | 58% |

after_oldweights, after_pb, after: the same companies on every date as the variant before each, so the same figures.

### Each step against the one before it

| step | date | passes | added | dropped | same passes | scores that differ | added: mean [CI] | added: median | added: trimmed | earlier passers: mean [CI] | added less earlier [CI] | the same without capped [CI] |
|---|---|---|---:|---:|---|---|---|---:|---|---|---|---|
| before -> before_margin3 | 2022-09-30 | 57 -> 137 | 80 | 0 | no | 0 of 57 | +16.5 [+7.3, +26.1] | +11.2 | +14.4 [+5.6, +24.2] (without ANF) | +20.5 [+10.0, +31.0] | -4.0 [-17.9, +9.8] | -6.1 [-19.8, +7.8] |
| before -> before_margin3 | 2023-09-29 | 40 -> 99 | 59 | 0 | no | 0 of 40 | +6.3 [-4.9, +17.5] | -3.1 | +4.4 [-6.6, +15.9] (without MPTI) | -2.9 [-17.0, +12.1] | +9.2 [-8.8, +26.8] | +9.2 [-8.8, +26.8] |
| before -> before_margin3 | 2024-09-27 | 26 -> 76 | 50 | 0 | no | 0 of 26 | -3.0 [-20.9, +18.8] | -14.0 | -11.4 [-23.1, +10.8] (without PSIX) | -1.9 [-21.2, +20.1] | -1.2 [-30.5, +28.4] | -1.2 [-30.5, +28.4] |
| before -> before_margin3 | 2025-09-26 | 18 -> 77 | 59 | 0 | no | 0 of 18 | +16.8 [+6.5, +27.4] | -0.4 | +14.2 [+4.5, +25.4] (without HBB) | +1.9 [-13.1, +15.6] | +14.9 [-2.2, +33.6] | +14.9 [-2.2, +33.6] |
| before -> before_margin3 | pooled | 141 -> 389 | 248 | 0 | no | 0 of 141 | +10.2 [+3.9, +16.7] | +2.7 | +8.6 [+3.1, +15.2] (without PSIX) | +7.4 [+0.1, +14.9] | +3.1 [-6.9, +12.7] | +2.3 [-7.3, +12.1] |
| before_margin3 -> after_oldweights | 2022-09-30 | 137 -> 137 | 0 | 0 | yes | 0 of 137 | n/a | n/a | n/a | +18.1 [+11.1, +25.6] | n/a | n/a |
| before_margin3 -> after_oldweights | 2023-09-29 | 99 -> 99 | 0 | 0 | yes | 0 of 99 | n/a | n/a | n/a | +2.6 [-5.9, +11.6] | n/a | n/a |
| before_margin3 -> after_oldweights | 2024-09-27 | 76 -> 76 | 0 | 0 | yes | 0 of 76 | n/a | n/a | n/a | -2.6 [-16.9, +14.3] | n/a | n/a |
| before_margin3 -> after_oldweights | 2025-09-26 | 77 -> 77 | 0 | 0 | yes | 0 of 77 | n/a | n/a | n/a | +13.3 [+4.4, +22.6] | n/a | n/a |
| before_margin3 -> after_oldweights | pooled | 389 -> 389 | 0 | 0 | yes | 0 of 389 | n/a | n/a | n/a | +9.2 [+4.5, +14.4] | n/a | n/a |
| after_oldweights -> after_pb | 2022-09-30 | 137 -> 137 | 0 | 0 | yes | 123 of 137 | n/a | n/a | n/a | +18.1 [+11.1, +25.6] | n/a | n/a |
| after_oldweights -> after_pb | 2023-09-29 | 99 -> 99 | 0 | 0 | yes | 91 of 99 | n/a | n/a | n/a | +2.6 [-5.9, +11.6] | n/a | n/a |
| after_oldweights -> after_pb | 2024-09-27 | 76 -> 76 | 0 | 0 | yes | 66 of 76 | n/a | n/a | n/a | -2.6 [-16.9, +14.3] | n/a | n/a |
| after_oldweights -> after_pb | 2025-09-26 | 77 -> 77 | 0 | 0 | yes | 71 of 77 | n/a | n/a | n/a | +13.3 [+4.4, +22.6] | n/a | n/a |
| after_oldweights -> after_pb | pooled | 389 -> 389 | 0 | 0 | yes | 351 of 389 | n/a | n/a | n/a | +9.2 [+4.5, +14.4] | n/a | n/a |
| after_pb -> after | 2022-09-30 | 137 -> 137 | 0 | 0 | yes | 4 of 137 | n/a | n/a | n/a | +18.1 [+11.1, +25.6] | n/a | n/a |
| after_pb -> after | 2023-09-29 | 99 -> 99 | 0 | 0 | yes | 4 of 99 | n/a | n/a | n/a | +2.6 [-5.9, +11.6] | n/a | n/a |
| after_pb -> after | 2024-09-27 | 76 -> 76 | 0 | 0 | yes | 1 of 76 | n/a | n/a | n/a | -2.6 [-16.9, +14.3] | n/a | n/a |
| after_pb -> after | 2025-09-26 | 77 -> 77 | 0 | 0 | yes | 0 of 77 | n/a | n/a | n/a | +13.3 [+4.4, +22.6] | n/a | n/a |
| after_pb -> after | pooled | 389 -> 389 | 0 | 0 | yes | 9 of 389 | n/a | n/a | n/a | +9.2 [+4.5, +14.4] | n/a | n/a |

### How the score ranks the passers' returns

Spearman of the score with the relative return among the companies that pass; the top 10 by score less the rest, in
points. The change is on the same companies, with a paired interval.

| variant | date | passes | Spearman [CI] | change from the step before [CI] | top 10 less the rest [CI] | the same without capped [CI] |
|---|---|---:|---|---|---|---|
| before | 2022-09-30 | 57 | -0.167 [-0.432, +0.112] |  | -14.5 [-42.5, +18.8] | -14.5 [-42.5, +18.8] |
| before | 2023-09-29 | 40 | -0.011 [-0.315, +0.301] |  | -8.2 [-40.3, +23.9] | -8.2 [-40.3, +23.9] |
| before | 2024-09-27 | 26 | -0.151 [-0.531, +0.263] |  | -6.6 [-49.5, +42.8] | -6.6 [-49.5, +42.8] |
| before | 2025-09-26 | 18 | -0.032 [-0.511, +0.475] |  | +13.9 [-13.7, +41.8] | +13.9 [-13.7, +41.8] |
| before | pooled | 141 | -0.023 [-0.179, +0.145] |  | -6.1 [-23.4, +12.2] | -6.1 [-23.4, +12.2] |
| before_margin3 | 2022-09-30 | 137 | -0.096 [-0.267, +0.076] | +0.000 [+0.000, +0.000] (on the 57 both pass) | -20.8 [-43.6, +0.4] | -19.5 [-41.4, +2.2] |
| before_margin3 | 2023-09-29 | 99 | -0.172 [-0.347, +0.012] | +0.000 [+0.000, +0.000] (on the 40 both pass) | -26.8 [-45.2, -6.7] | -26.8 [-45.2, -6.7] |
| before_margin3 | 2024-09-27 | 76 | -0.152 [-0.374, +0.085] | +0.000 [+0.000, +0.000] (on the 26 both pass) | -32.1 [-59.4, -6.7] | -32.1 [-59.4, -6.7] |
| before_margin3 | 2025-09-26 | 77 | -0.014 [-0.243, +0.230] | +0.000 [+0.000, +0.000] (on the 18 both pass) | -1.2 [-21.5, +21.1] | -1.2 [-21.5, +21.1] |
| before_margin3 | pooled | 389 | -0.079 [-0.175, +0.024] | +0.000 [+0.000, +0.000] (on the 141 both pass) | -20.3 [-31.5, -9.2] | -20.0 [-31.1, -8.4] |
| after_oldweights | 2022-09-30 | 137 | -0.096 [-0.267, +0.076] | +0.000 [+0.000, +0.000] | -20.8 [-43.6, +0.4] | -19.5 [-41.4, +2.2] |
| after_oldweights | 2023-09-29 | 99 | -0.172 [-0.347, +0.012] | +0.000 [+0.000, +0.000] | -26.8 [-45.2, -6.7] | -26.8 [-45.2, -6.7] |
| after_oldweights | 2024-09-27 | 76 | -0.152 [-0.374, +0.085] | +0.000 [+0.000, +0.000] | -32.1 [-59.4, -6.7] | -32.1 [-59.4, -6.7] |
| after_oldweights | 2025-09-26 | 77 | -0.014 [-0.243, +0.230] | +0.000 [+0.000, +0.000] | -1.2 [-21.5, +21.1] | -1.2 [-21.5, +21.1] |
| after_oldweights | pooled | 389 | -0.079 [-0.175, +0.024] | +0.000 [+0.000, +0.000] | -20.3 [-31.5, -9.2] | -20.0 [-31.1, -8.4] |
| after_pb | 2022-09-30 | 137 | -0.045 [-0.210, +0.123] | +0.051 [+0.001, +0.104] | -20.7 [-43.4, +0.7] | -19.4 [-41.6, +2.2] |
| after_pb | 2023-09-29 | 99 | -0.148 [-0.323, +0.049] | +0.024 [-0.049, +0.095] | -10.6 [-32.5, +12.6] | -10.6 [-32.5, +12.6] |
| after_pb | 2024-09-27 | 76 | -0.102 [-0.322, +0.130] | +0.050 [-0.026, +0.127] | +1.1 [-40.3, +50.0] | +1.1 [-40.3, +50.0] |
| after_pb | 2025-09-26 | 77 | +0.019 [-0.199, +0.258] | +0.032 [-0.044, +0.109] | +1.2 [-20.2, +25.0] | +1.2 [-20.2, +25.0] |
| after_pb | pooled | 389 | -0.040 [-0.135, +0.065] | +0.039 [+0.008, +0.072] | -7.5 [-21.9, +7.8] | -7.2 [-20.5, +8.5] |
| after | 2022-09-30 | 137 | -0.039 [-0.204, +0.129] | +0.006 [-0.002, +0.018] | -20.7 [-43.4, +0.7] | -19.4 [-41.6, +2.2] |
| after | 2023-09-29 | 99 | -0.154 [-0.329, +0.043] | -0.006 [-0.036, +0.022] | -10.6 [-32.5, +12.6] | -10.6 [-32.5, +12.6] |
| after | 2024-09-27 | 76 | -0.097 [-0.318, +0.136] | +0.005 [+0.000, +0.019] | +1.1 [-40.3, +50.0] | +1.1 [-40.3, +50.0] |
| after | 2025-09-26 | 77 | +0.019 [-0.199, +0.258] | +0.000 [+0.000, +0.000] | +1.2 [-20.2, +25.0] | +1.2 [-20.2, +25.0] |
| after | pooled | 389 | -0.038 [-0.135, +0.066] | +0.002 [-0.005, +0.009] | -7.5 [-21.9, +7.8] | -7.2 [-20.5, +8.5] |

### Flags (after)

| flag | date | passers with it | with: mean [CI] | with: median | with: without capped | with: trimmed | without: mean [CI] | without: median | with less without [CI] | the same without capped [CI] |
|---|---|---:|---|---:|---|---|---|---:|---|---|
| above_upper_band | 2022-09-30 | 0 | n/a | n/a | n/a | n/a | +18.1 [+11.1, +25.6] | +13.2 | n/a | n/a |
| above_upper_band | 2023-09-29 | 4 | -13.1 [-41.2, +6.4] | -3.5 | none capped | +1.2 [-36.4, +7.8] (without HDSN) | +3.2 [-5.9, +12.1] | -4.4 | -16.3 [-46.5, +5.9] | -16.3 [-46.5, +5.9] |
| above_upper_band | 2024-09-27 | 2 | +1.1 [-48.6, +50.8] | +1.1 | none capped | under 3 | -2.7 [-16.7, +14.3] | -21.4 | +3.9 [-57.4, +63.9] | +3.9 [-57.4, +63.9] |
| above_upper_band | 2025-09-26 | 4 | +33.0 [+7.4, +65.5] | +26.2 | none capped | +16.9 [-1.7, +60.2] (without NHC) | +12.2 [+3.5, +21.7] | -1.1 | +20.8 [-7.1, +53.1] | +20.8 [-7.1, +53.1] |
| above_upper_band | pooled | 10 | +8.2 [-12.2, +29.0] | +6.4 | none capped | +0.1 [-17.4, +24.1] (without NHC) | +9.2 [+4.7, +14.2] | +2.6 | +2.5 [-19.3, +24.1] | +2.5 [-19.3, +24.1] |
| below_insider_price | 2022-09-30 | 3 | +28.8 [+5.8, +67.2] | +13.4 | none capped | +9.6 [+5.8, +67.2] (without PVH) | +17.9 [+10.9, +25.3] | +12.5 | +10.9 [-14.5, +45.6] | +12.1 [-12.9, +49.9] |
| below_insider_price | 2023-09-29 | 3 | +7.0 [-35.2, +66.1] | -9.9 | none capped | -22.5 [-35.2, +66.1] (without EML) | +2.4 [-6.3, +11.1] | -4.2 | +4.6 [-36.7, +62.1] | +4.6 [-36.7, +62.1] |
| below_insider_price | 2024-09-27 | 0 | n/a | n/a | n/a | n/a | -2.6 [-16.9, +14.3] | -21.4 | n/a | n/a |
| below_insider_price | 2025-09-26 | 0 | n/a | n/a | n/a | n/a | +13.3 [+4.4, +22.6] | -0.4 | n/a | n/a |
| below_insider_price | pooled | 6 | +17.9 [-9.2, +47.4] | +9.6 | none capped | +8.0 [-13.7, +43.5] (without PVH) | +9.0 [+4.3, +14.1] | +2.3 | +7.7 [-19.4, +37.5] | +8.4 [-19.3, +37.6] |
| industry_out_of_favor | 2022-09-30 | 126 | +16.5 [+9.3, +23.7] | +11.7 | +15.2 [+8.3, +22.2] (1 capped) | +15.2 [+8.1, +22.5] (without ANF) | +36.5 [+8.3, +64.4] | +49.0 | -20.0 [-50.4, +9.0] | -21.3 [-50.5, +9.4] |
| industry_out_of_favor | 2023-09-29 | 25 | -6.2 [-21.7, +9.1] | -12.1 | none capped | -3.7 [-20.5, +10.2] (without FWRD) | +5.5 [-4.6, +16.2] | -2.5 | -11.7 [-30.0, +7.1] | -11.7 [-30.0, +7.1] |
| industry_out_of_favor | 2024-09-27 | 13 | -1.2 [-24.4, +25.0] | -21.2 | none capped | -10.6 [-28.2, +17.7] (without BBW) | -2.9 [-18.7, +15.5] | -21.6 | +1.8 [-27.7, +32.8] | +1.8 [-27.7, +32.8] |
| industry_out_of_favor | 2025-09-26 | 31 | +15.2 [-0.9, +32.5] | -1.1 | none capped | +10.2 [-4.6, +28.3] (without HBB) | +12.0 [+2.2, +22.5] | +2.0 | +3.1 [-15.2, +23.0] | +3.1 [-15.2, +23.0] |
| industry_out_of_favor | pooled | 195 | +12.2 [+6.5, +18.3] | +9.6 | +11.4 [+5.7, +17.4] (1 capped) | +11.4 [+5.8, +17.5] (without ANF) | +6.1 [-1.1, +14.2] | -2.5 | -5.9 [-16.9, +5.5] | -6.1 [-17.0, +6.1] |
| insiders_buying | 2022-09-30 | 4 | +53.1 [+9.6, +97.9] | +40.3 | none capped | +28.8 [+5.8, +88.5] (without PLPC) | +17.1 [+10.1, +24.5] | +11.9 | +36.0 [-9.0, +87.2] | +37.3 [-7.1, +85.3] |
| insiders_buying | 2023-09-29 | 4 | -3.8 [-35.7, +40.8] | -22.5 | none capped | -27.1 [-35.9, +32.3] (without EML) | +2.8 [-6.3, +11.7] | -4.1 | -6.7 [-40.8, +37.4] | -6.7 [-40.8, +37.4] |
| insiders_buying | 2024-09-27 | 1 | +9.6 | +9.6 | none capped | under 3 | -2.8 [-17.5, +13.3] | -21.6 | +12.4 | +12.4 |
| insiders_buying | 2025-09-26 | 0 | n/a | n/a | n/a | n/a | +13.3 [+4.4, +22.6] | -0.4 | n/a | n/a |
| insiders_buying | pooled | 9 | +23.0 [-2.5, +50.8] | +9.6 | none capped | +10.1 [-9.4, +42.1] (without PLPC) | +8.8 [+4.0, +13.7] | +2.3 | +14.5 [-11.1, +44.3] | +15.1 [-11.0, +43.0] |
| out_of_favor | 2022-09-30 | 100 | +18.3 [+9.5, +27.1] | +11.9 | +16.7 [+8.7, +25.1] (1 capped) | +16.7 [+8.2, +25.6] (without ANF) | +17.7 [+5.7, +29.6] | +15.5 | +0.6 [-14.5, +14.7] | -1.0 [-15.7, +13.2] |
| out_of_favor | 2023-09-29 | 40 | -10.6 [-25.1, +4.6] | -27.4 | none capped | -13.6 [-25.9, +3.2] (without CCLD) | +11.5 [+0.9, +22.2] | +3.5 | -22.1 [-39.3, -4.1] | -22.1 [-39.3, -4.1] |
| out_of_favor | 2024-09-27 | 32 | -11.8 [-28.5, +8.3] | -23.5 | none capped | -17.5 [-32.2, +3.2] (without METC) | +4.0 [-15.6, +27.9] | -10.5 | -15.9 [-44.6, +11.7] | -15.9 [-44.6, +11.7] |
| out_of_favor | 2025-09-26 | 36 | +15.0 [+2.2, +28.1] | +7.6 | none capped | +10.7 [+0.3, +24.5] (without HBB) | +11.8 [-0.5, +24.7] | -1.6 | +3.2 [-14.0, +21.3] | +3.2 [-14.0, +21.3] |
| out_of_favor | pooled | 208 | +7.5 [+1.3, +13.7] | +2.1 | +6.7 [+0.9, +12.9] (1 capped) | +6.7 [+0.5, +12.9] (without ANF) | +11.0 [+3.9, +18.8] | +3.5 | -8.4 [-18.2, +0.8] | -8.9 [-18.7, +1.0] |
| under_book | 2022-09-30 | 24 | +13.2 [-4.5, +31.4] | +8.4 | none capped | +9.1 [-7.2, +28.2] (without PR) | +19.2 [+11.3, +26.9] | +13.4 | -6.0 [-25.0, +13.0] | -4.5 [-23.4, +14.8] |
| under_book | 2023-09-29 | 18 | -0.9 [-21.9, +21.2] | -10.7 | none capped | -7.2 [-25.7, +16.2] (without CCLD) | +3.3 [-6.3, +12.4] | -4.1 | -4.2 [-26.8, +19.3] | -4.2 [-26.8, +19.3] |
| under_book | 2024-09-27 | 14 | -31.7 [-47.6, -14.1] | -39.2 | none capped | -27.3 [-44.6, -13.3] (without FLYE) | +3.9 [-12.4, +24.1] | -10.5 | -35.7 [-60.3, -11.1] | -35.7 [-60.3, -11.1] |
| under_book | 2025-09-26 | 12 | +5.2 [-9.9, +20.2] | +3.5 | none capped | +1.8 [-13.4, +18.1] (without APLE) | +14.8 [+4.9, +25.0] | -0.4 | -9.6 [-27.3, +8.4] | -9.6 [-27.3, +8.4] |
| under_book | pooled | 68 | -1.2 [-10.3, +9.1] | -10.0 | none capped | -2.8 [-11.8, +7.6] (without PR) | +11.3 [+6.0, +16.9] | +4.8 | -12.2 [-23.7, -1.7] | -11.7 [-22.2, -0.4] |

Every listing with tier 1 or 2 insider buying in the 30 days before, passing or not: 538 company-dates, mean +3.1 [-2.7,
+8.9], median -5.9, without capped -1.5 [-6.3, +3.5] (10 capped), trimmed +2.4 [-3.4, +8.1] (without DFDV), 49% beat the
median stock.

### Watch lists (after)

| list | date | listed | with a return | mean [CI] | median | without capped | trimmed | beat the median stock |
|---|---|---:|---:|---|---:|---|---|---:|
| Under 1x cash | 2022-09-30 | 91 | 91 | -31.1 [-41.8, -19.5] | -45.6 | -31.2 [-41.9, -20.1] (4 capped) | -33.5 [-43.7, -21.7] (without TNGX) | 21% |
| Under 1x cash | 2023-09-29 | 110 | 110 | -2.5 [-20.8, +16.8] | -34.1 | -12.8 [-27.1, +3.1] (11 capped) | -5.0 [-23.4, +14.6] (without TIL) | 35% |
| Under 1x cash | 2024-09-27 | 82 | 82 | -21.8 [-39.0, -3.6] | -38.2 | -19.6 [-35.8, -1.6] (2 capped) | -26.5 [-41.8, -8.0] (without AMLX) | 33% |
| Under 1x cash | 2025-09-26 | 43 | 43 | +9.3 [-18.8, +41.1] | -24.8 | -7.0 [-29.7, +18.7] (4 capped) | +3.4 [-24.8, +35.9] (without SPRB) | 42% |
| Under 1x cash | pooled | 326 | 326 | -13.8 [-22.7, -4.3] | -36.7 | -19.1 [-26.9, -10.6] (21 capped) | -14.9 [-23.6, -5.3] (without AMLX) | 31% |
| Banks and insurers under book value | 2022-09-30 | 63 | 63 | +3.1 [-5.2, +10.3] | +7.0 | none capped | +4.5 [-3.9, +11.4] (without VIP) | 59% |
| Banks and insurers under book value | 2023-09-29 | 109 | 109 | +17.2 [+9.4, +25.0] | +17.9 | +15.0 [+8.1, +21.5] (1 capped) | +15.0 [+8.3, +23.0] (without AIFC) | 74% |
| Banks and insurers under book value | 2024-09-27 | 64 | 64 | +7.4 [-1.3, +18.3] | +3.2 | none capped | +3.2 [-2.8, +14.3] (without BTCS) | 73% |
| Banks and insurers under book value | 2025-09-26 | 45 | 45 | +24.4 [+14.8, +34.4] | +25.5 | none capped | +22.4 [+13.2, +32.6] (without OPHC) | 87% |
| Banks and insurers under book value | pooled | 281 | 281 | +13.0 [+8.7, +17.7] | +14.3 | +12.1 [+8.0, +16.4] (1 capped) | +12.1 [+8.1, +16.8] (without BTCS) | 73% |
| of which banks (tangible book) | 2022-09-30 | 28 | 28 | -12.8 [-21.5, -4.6] | -7.6 | none capped | -10.5 [-19.5, -3.0] (without MCHB) | 29% |
| of which banks (tangible book) | 2023-09-29 | 80 | 80 | +19.1 [+12.8, +25.5] | +19.7 | none capped | +20.5 [+13.6, +25.8] (without PNBK) | 79% |
| of which banks (tangible book) | 2024-09-27 | 49 | 49 | +2.4 [-4.3, +9.0] | +1.3 | none capped | +1.2 [-5.1, +8.2] (without C) | 73% |
| of which banks (tangible book) | 2025-09-26 | 29 | 29 | +36.9 [+27.9, +47.2] | +31.9 | none capped | +34.3 [+25.7, +44.9] (without OPHC) | 100% |
| of which banks (tangible book) | pooled | 186 | 186 | +12.7 [+8.7, +16.6] | +14.5 | none capped | +12.1 [+8.4, +16.1] (without OPHC) | 73% |
| of which others (book) | 2022-09-30 | 35 | 35 | +15.8 [+4.6, +26.0] | +21.6 | none capped | +18.8 [+7.2, +27.5] (without VIP) | 83% |
| of which others (book) | 2023-09-29 | 29 | 29 | +12.1 [-11.9, +38.9] | +7.2 | +3.3 [-15.8, +22.0] (1 capped) | +3.3 [-15.9, +31.2] (without AIFC) | 62% |
| of which others (book) | 2024-09-27 | 15 | 15 | +23.8 [-3.5, +64.6] | +6.8 | none capped | +6.2 [-7.5, +49.9] (without BTCS) | 73% |
| of which others (book) | 2025-09-26 | 16 | 16 | +1.7 [-14.8, +17.1] | +4.5 | none capped | +6.7 [-11.9, +18.2] (without EXOD) | 62% |
| of which others (book) | pooled | 95 | 95 | +13.6 [+3.6, +24.4] | +13.8 | +11.0 [+1.9, +20.7] (1 capped) | +10.8 [+2.1, +21.7] (without BTCS) | 72% |
| Overpriced giants | 2022-09-30 | 5 | 5 | +57.7 [+10.2, +119.5] | +36.2 | +26.9 [+1.9, +52.2] (1 capped) | +26.9 [-3.0, +104.1] (without NVDA) | 80% |
| Overpriced giants | 2023-09-29 | 8 | 8 | +34.9 [+1.6, +77.7] | +29.3 | none capped | +16.8 [-5.4, +65.8] (without NVDA) | 75% |
| Overpriced giants | 2024-09-27 | 18 | 18 | -1.4 [-13.9, +12.4] | -3.8 | none capped | -6.2 [-15.3, +8.5] (without AVGO) | 61% |
| Overpriced giants | 2025-09-26 | 28 | 28 | +3.2 [-13.4, +20.7] | -2.0 | none capped | -0.7 [-16.8, +17.4] (without CRWD) | 54% |
| Overpriced giants | pooled | 59 | 59 | +10.7 [-0.6, +22.5] | +0.1 | +7.8 [-3.2, +18.7] (1 capped) | +7.8 [-3.2, +19.7] (without NVDA) | 61% |
| Unverified (left out, listed) | 2022-09-30 | 9 | 9 | -3.4 [-31.9, +32.4] | -16.4 | none capped | -18.3 [-35.5, +22.0] (without VRA) | 22% |
| Unverified (left out, listed) | 2023-09-29 | 6 | 6 | -10.0 [-34.8, +17.3] | -8.9 | none capped | -22.6 [-39.2, +10.2] (without KRO) | 33% |
| Unverified (left out, listed) | 2024-09-27 | 9 | 9 | -2.4 [-30.5, +27.6] | -13.4 | none capped | -13.0 [-33.5, +22.1] (without PPIH) | 44% |
| Unverified (left out, listed) | 2025-09-26 | 9 | 9 | +17.0 [-15.0, +53.2] | +22.4 | none capped | +3.1 [-19.1, +43.8] (without KRT) | 67% |
| Unverified (left out, listed) | pooled | 33 | 33 | +1.2 [-13.5, +17.1] | -12.8 | none capped | -2.7 [-17.3, +13.8] (without KRT) | 42% |
| Listings in out-of-favor industries (screener's rule) (110 of 128 industries) | 2022-09-30 | 3848 | 3847 | +0.6 [-1.0, +2.2] | +1.8 | -0.2 [-1.6, +1.3] (82 capped) | +0.5 [-1.1, +2.1] (without AAOI) | 52% |
| Listings in out-of-favor industries (screener's rule) (43 of 128 industries) | 2023-09-29 | 1979 | 1979 | -1.6 [-4.7, +1.4] | -5.4 | -3.7 [-6.5, -1.0] (57 capped) | -1.8 [-4.8, +1.3] (without ADMA) | 46% |
| Listings in out-of-favor industries (screener's rule) (23 of 129 industries) | 2024-09-27 | 1039 | 1039 | -10.3 [-15.2, -4.8] | -26.2 | -12.1 [-16.4, -7.5] (29 capped) | -10.7 [-15.6, -5.2] (without ALMU) | 41% |
| Listings in out-of-favor industries (screener's rule) (43 of 130 industries) | 2025-09-26 | 1939 | 1938 | -7.8 [-10.8, -4.7] | -18.4 | -10.1 [-12.9, -7.2] (50 capped) | -7.9 [-10.9, -4.9] (without ANRO) | 40% |
| Listings in out-of-favor industries (screener's rule) (219 of 515 industries) | pooled | 8805 | 8803 | -3.0 [-4.4, -1.7] | -5.4 | -4.5 [-5.8, -3.4] (218 capped) | -3.1 [-4.4, -1.8] (without ALMU) | 47% |
| Listings in industries 10+ pts below the market (24 of 128 industries) | 2022-09-30 | 1396 | 1396 | -10.9 [-13.8, -7.9] | -13.1 | -11.6 [-14.3, -8.7] (38 capped) | -11.1 [-14.0, -8.0] (without AKBA) | 41% |
| Listings in industries 10+ pts below the market (20 of 128 industries) | 2023-09-29 | 1128 | 1128 | -6.5 [-10.9, -1.8] | -17.3 | -8.5 [-12.4, -4.3] (38 capped) | -6.7 [-11.1, -2.1] (without ADMA) | 39% |
| Listings in industries 10+ pts below the market (32 of 129 industries) | 2024-09-27 | 1566 | 1566 | -5.2 [-9.3, -0.8] | -20.0 | -8.8 [-12.2, -5.1] (44 capped) | -5.5 [-9.6, -1.1] (without ALMU) | 43% |
| Listings in industries 10+ pts below the market (35 of 130 industries) | 2025-09-26 | 1602 | 1601 | -6.0 [-9.5, -2.5] | -16.7 | -8.7 [-11.8, -5.5] (47 capped) | -6.2 [-9.6, -2.7] (without ANRO) | 42% |
| Listings in industries 10+ pts below the market (111 of 515 industries) | pooled | 5692 | 5691 | -7.1 [-9.0, -5.2] | -17.4 | -9.4 [-11.0, -7.7] (167 capped) | -7.2 [-9.1, -5.3] (without ALMU) | 41% |

### Industries out of favor: the screener's rule against the market rule (after)

| date | market's median listing below its high | industries measured | rule | its threshold | industries marked | passers in them | in: mean [CI] | elsewhere: mean [CI] | in less elsewhere [CI] | the same without capped [CI] |
|---|---:|---:|---|---:|---:|---:|---|---|---|---|
| 2022-09-30 | 37.9% | 128 | screener's (25%+ below high) | 25.0% | 110 | 126 | +16.5 [+9.3, +23.7] | +36.5 [+8.3, +64.4] | -20.0 [-50.4, +9.0] | -21.3 [-50.5, +9.4] |
| 2022-09-30 | 37.9% | 128 | market (10+ pts below the market) | 47.9% | 24 | 30 | +8.9 [-8.3, +27.5] | +20.7 [+12.9, +28.6] | -11.9 [-30.2, +7.3] | -17.8 [-32.8, -2.0] |
| 2023-09-29 | 23.3% | 128 | screener's (25%+ below high) | 25.0% | 43 | 25 | -6.2 [-21.7, +9.1] | +5.5 [-4.6, +16.2] | -11.7 [-30.0, +7.1] | -11.7 [-30.0, +7.1] |
| 2023-09-29 | 23.3% | 128 | market (10+ pts below the market) | 33.3% | 20 | 9 | +0.8 [-24.1, +28.1] | +2.7 [-6.4, +12.2] | -2.0 [-29.6, +28.1] | -2.0 [-29.6, +28.1] |
| 2024-09-27 | 13.3% | 129 | screener's (25%+ below high) | 25.0% | 23 | 13 | -1.2 [-24.4, +25.0] | -2.9 [-18.7, +15.5] | +1.8 [-27.7, +32.8] | +1.8 [-27.7, +32.8] |
| 2024-09-27 | 13.3% | 129 | market (10+ pts below the market) | 23.3% | 32 | 26 | -11.5 [-26.9, +4.8] | +2.0 [-17.5, +25.3] | -13.5 [-40.0, +11.3] | -13.5 [-40.0, +11.3] |
| 2025-09-26 | 17.7% | 130 | screener's (25%+ below high) | 25.0% | 43 | 31 | +15.2 [-0.9, +32.5] | +12.0 [+2.2, +22.5] | +3.1 [-15.2, +23.0] | +3.1 [-15.2, +23.0] |
| 2025-09-26 | 17.7% | 130 | market (10+ pts below the market) | 27.7% | 35 | 24 | +12.7 [-2.1, +29.0] | +13.6 [+2.7, +24.7] | -0.9 [-19.5, +18.0] | -0.9 [-19.5, +18.0] |
| pooled |  | 515 | screener's (25%+ below high) |  | 219 | 195 | +12.2 [+6.5, +18.3] | +6.1 [-1.1, +14.2] | -5.9 [-16.9, +5.5] | -6.1 [-17.0, +6.1] |
| pooled |  | 515 | market (10+ pts below the market) |  | 111 | 89 | +3.1 [-5.5, +12.3] | +10.9 [+5.5, +16.8] | -8.3 [-19.8, +2.9] | -10.3 [-20.7, +0.5] |

<!-- results:end -->
