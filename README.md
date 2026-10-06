# Ticker Finder & Analysis

Find undervalued US stocks and understand exactly why. Three tools, built on SEC filings and written for people who are new to the market.

| Tool | Question it answers |
|---|---|
| **Sector screener** | Which small US companies are cheap, profitable and low on debt right now? |
| **Insider buying** | Who inside a company is buying its stock with their own money? |
| **Ticker report** | Is this one stock healthy, and is its price fair? |

The site is static HTML on GitHub Pages. A GitHub Actions job refreshes all the data every weekday evening, so there are no servers to run and no API keys to manage.

## The three tools

### 1. Sector screener

A company passes only if it clears every check:

| Check | Rule |
|---|---|
| Price to sales | under 2 |
| Market value | under $3B (bonus points under $2B) |
| Debt | long-term debt / equity under 0.5. Best: no debt. Next best: more cash than debt |
| Net profit margin | 3% or more, without one-time items (8% until September 2026) |
| Location | headquartered **and** incorporated in a US state (rules out ADRs and offshore shells) |
| Viability | equity above zero, free cash flow above zero, profitable in 2 of the last 3 years, sales not down more than 5% |

Flags, not rules (none keeps a company off the list):

| Flag | Meaning |
|---|---|
| Out of favor | 25%+ below its 52-week high |
| Industry out of favor | the median company in its Nasdaq industry is 25%+ below its 52-week high (industries with at least 5 companies that have a year of weekly prices, each counted once) |
| Undiscovered | two or fewer analysts publish estimates |
| Under book value | market value under book value (shareholders' equity less preferred stock) |
| Insiders buying | Tier 1 or 2 open-market insider buying in the insider page's window (30 days) |
| Below insiders' price | Tier 1 or 2 insider buying, and the price is under the average price those insiders paid |
| Above upper band, wait for a pullback | the price is above the upper Bollinger band: the average of the last 20 weekly closes plus two standard deviations of them |

Passing companies are ranked by a 0 to 100 score, and every point is itemized on the page. The weights are `SCORE_WEIGHTS` in `pipeline/screener.py`:

| Part | Points |
|---|---|
| Cheap vs. sales | up to 15: full at a price to sales of 0, falling evenly to none at 2 |
| Debt | 20 for no debt, 14 for more cash than debt, up to 8 for low debt (none at the limit) |
| Profit margin | up to 15: full at 30% or more |
| Cash generation | up to 10: full when free cash flow is 15% or more of market value |
| Sales growth | up to 10: full at 20% or more |
| Under book value | up to 10: full under 1x book value, falling evenly to none at 2x |
| Under $2B | 5 |
| Out of favor | up to 5: full at 50% or more below the 52-week high |
| Few analysts | 5 for none, 3 for one or two |
| Insiders buying | 5 for Tier 1 or 2 insider buying |

Beside the ranked list, the page shows the industries out of favor, each with its median price to sales against the whole market's, and three watch lists that don't change the ranked list or its scores:

- **Under 1x cash:** US companies outside finance whose market value is below their net cash: cash and short-term investments less all debt, preferred stock and minority holders' stakes in subsidiaries, from the latest balance sheet. Other bills (payables, leases) are not subtracted, and the page says so. Long-term investments don't count, and a company whose debt can't be read reliably, or whose shareholders' equity is zero or less, is left out. Profit isn't required, so each shows its free cash flow and its cash runway: cash and short-term investments over the latest fiscal year's cash burn (negative free cash flow), shown only when that fiscal year ended within 15 months of the balance sheet.
- **Banks and insurers under book value:** profitable US financial companies (not REITs, business development companies, or the land developers Nasdaq files under Finance) priced under book value. A bank is measured on tangible book value (equity less preferred stock, goodwill and other intangible assets), with the profit and return on equity left to common shareholders; any other on book value (equity less preferred stock), with its profit left to common shareholders (after preferred dividends) and return on that book. Profit and return on equity must both be positive, without one-time items, in a fiscal year that ended within 15 months of the balance sheet.
- **Overpriced giants:** companies worth over $90B priced at more than 10 times their sales over the last 12 months, or their latest fiscal year where quarterly figures aren't available (not lenders, REITs, whose sales are rent, nor companies whose sales figure looks incomplete), shown as the kind of stock to avoid.

### How the screener has done

The screener was rerun as it would have run on the last Friday of September 2022, 2023, 2024 and 2025, on the SEC filings made before each date and that week's prices, through the pipeline's own code (`backtest/`). Each passing company's total return over the next 52 weeks was compared with the average listed stock's. "Before" is the screener before the September 2026 update, with the 8% margin rule; "after" is this one.

| | Before | After |
|---|---|---|
| Companies passing (2022, 2023, 2024, 2025) | 57, 40, 26, 18 | 137, 99, 76, 77 |
| Their next year against the average stock, mean of all four years | +7.4 points (95% interval -0.1 to +14.9), median +2.3 | +9.2 (+4.5 to +14.4), median +2.6 |
| How well the score ranks the passers' returns (rank correlation) | -0.023 | -0.038 |

What each change did:

- **The 3% margin rule** let in 248 more company-years, which did about as well as the companies that passed before: +10.2 points against +7.4, a gap of +3.1 (-6.9 to +12.7). The list is nearly three times as long and no worse, but no better either.
- **The score** did not rank next year's returns in any version: a correlation near zero before the update and after it. The new weights with price to book ranked them slightly better than the old weights on the same companies (+0.039, interval +0.008 to +0.072), within the noise of four years; the insider points changed almost nothing (+0.002), since only 9 company-years had Tier 1 or 2 insider buying.
- **Flags.** Passers under book value did worse than the other passers, by 12.2 points (-23.7 to -1.7) over 68 company-years, so the 10 points the score gives them are not borne out by these four years. Passers with insider buying (9) or trading below the insiders' price (6), and those above the upper band (10), are too few to judge. Across all 538 listings with Tier 1 or 2 insider buying, passing or not, the next year averaged +3.1 points (-2.7 to +8.9), median -5.9. Passers in out-of-favor industries did 5.9 points worse than the rest (-16.9 to +5.5).
- **Watch lists.** Banks and insurers under book value did well: +13.0 points (+8.7 to +17.7), median +14.3, with 73% beating the median stock, banks and others alike. Companies under 1x cash did badly: -13.8 points (-22.7 to -4.3), median -36.7, only 31% beating the median stock; 226 of the 326 company-years were health care companies, and 235 were burning cash. The overpriced giants did not lag: +10.7 points (-0.6 to +22.5), median +0.1, carried by Nvidia in 2022 and 2023.

The test has limits: four years only; today's listings, so companies delisted or bankrupt since are missing, which flatters every list; today's sector and industry labels and SEC registrations; no analyst counts on any date. `backtest/README.md` has the method, every table and how to rerun it.

### 2. Insider buying

- Sources: SEC **Form 4** (executives, directors, 10%+ owners) and **Schedule 13D** (new 5%+ holders who may seek influence), read from EDGAR's daily index.
- Only open-market purchases (transaction code `P`) count. Option exercises, grants and sales are excluded.
- Shows who bought, their role, the dollar amount, the price paid, and how today's price compares.
- **Tier 1:** $10M+ bought in the last 30 days. **Tier 2:** $1M+ total, or any buyer whose stake grew 20%+.
- Sorted newest filing first. Each weekday's filings are analyzed that evening.
- **Screener check:** each company with figures, outside Finance, is measured against the sector screener's rules: it passes (it is on the screener's list), misses one rule (named), misses several (each named), or misses none but has a rule that could not be checked. A rule is only called missed when the company's figures show it: a figure the filings don't give (free cash flow where capital spending isn't read, an earlier year of sales, debt that can't be read reliably) makes the rule "not checked" instead, with the reason. Where each company is incorporated and based is looked up with the SEC, as the screener does for its own list. A filter shows only companies that pass, or pass or miss by one.

### 3. Ticker report

- **News:** recent headlines, each labeled bullish, bearish or neutral with the reason, plus an overall read. Labels come from transparent phrase rules (see `pipeline/news.py`), not a black box.
- **Health:** 17 measures scored 1 to 100. Raw amounts are scored through the ratio that gives them meaning (cash becomes net cash as a share of market value, share count becomes dilution, revenue becomes growth and consistency). Valuation bars rank the company against its industry peers, and so does gross margin (the share of sales left after the direct cost of what was sold), which banks, insurers, other financial companies and REITs don't get.
- **Revenue by quarter:** the last 12 quarters of revenue, each with its growth on the same quarter a year before, and gross profit and gross margin for the fiscal year and the last 12 months. Quarters are the company's own fiscal quarters, matched to its fiscal years by their dates. Companies rarely report a fourth quarter on its own, so it is worked out as the year's revenue less its first three quarters and marked as such. The SEC's bulk data keeps only the latest figure for each period, which a later filing may have restated (after a business was sold, say), so each company's own 10-Q and 10-K figures are also looked up (one request per revenue tag and gross profit tag, kept between runs and looked up again only after a new filing) to tell which filings restated earlier ones. A fourth quarter, a growth rate, a 12-month total or a worked-out gross profit is only worked out from figures on one footing: where the latest figures mix footings, a fourth quarter comes from the latest filings that give the year and its first three quarters on one footing, and growth is left out with the reason shown. A quarter is left blank rather than guessed when its figures don't check out against the rest of its year. The fair value and the screener still use yearly figures.
- **Verdict:** undervalued, fair or overvalued, with an estimated fair-value range. A company outside finance is valued on its peers' value to sales (enterprise value per dollar of sales, applied to its own sales, less its net debt, minority holders' stakes in its subsidiaries and preferred stock) and a cash-flow model. Its peers' price to earnings is shown for comparison but not counted (it still counts where it is the only estimate): in the backtest below, leaving it out made the calls slightly better at ranking the next year's returns, a small gain found among 20 changes tried on the same four dates, so it may not hold up. Peers come from the company's Nasdaq sector, except that a company Nasdaq gives a utility industry under another sector is compared with utilities when its SEC industry code says it is one (MGE Energy, AES). The midpoint is the median of the estimates counted, a midpoint more than 50% from the price needs two independent estimates to agree, and the range spans the estimates, at least 10% either side of the midpoint. It is an estimate, not a price target, and not investment advice.
- **Cash-flow model:** free cash flow counted after stock-based pay (and after software and construction spending some companies report on their own lines) and before interest, which is added back after tax on up to 10% of the debt read from the latest balance sheet, since that debt is subtracted at the end (interest expense stands in where a company reports no interest paid). One-off payments inside operating cash flow are added back: a working capital outflow in one of the model's years that is above 5% of sales, three times the company's usual swing in the years before (at least four of them) and larger than any outflow it had then, less that usual swing (Coca-Cola's $6.0B deposit with the IRS in 2024 and its $6.1B final payment for fairlife in 2025; outflows of a similar size in earlier years, as at IMAX and Teva, mean it recurs and nothing is added back). It needs the newest fiscal year's free cash flow: where that year's can't be read from the filings, the model is left out rather than run on older years. It must be positive in each of the last three years it can read. Its level is the middle of those years' share of sales applied to the latest sales, or the middle year's cash flow itself when the sales figures look incomplete or jump from year to year. It grows at the average of last year's sales growth and the yearly rate over the last three years, each kept between 0% and 12%, for 5 years, then moves evenly to 2.5% by year 10, and is discounted at 8% for companies worth $10B or more, 9% from $2B, 10% from $300M and 11% below, the riskiest. The 8% is roughly the return investors expected from a large company's stock in January 2026: a 10-year US Treasury yield of about 4.2% plus Damodaran's implied equity risk premium of about 4.2%, rounded down, with a point added per size step for the extra risk of smaller companies. The rates are fixed: a rate by each company's beta that moved with bond yields (CAPM) made the backtest slightly worse, and one with a beta of 1 for every company was no better. Net debt, minority holders' stakes and preferred stock are then subtracted; long-term marketable securities count as cash. It is left out when net debt is more than the company's market value or half the business value the model finds, since the value left for shareholders would then swing on small changes in the forecast, and when free cash flow comes out larger than sales. A company that lost money and has no cash-flow value gets no verdict; its report says why and compares its price to sales with its peers'.
- **REITs** are valued on funds from operations (FFO), the profit measure REITs report: net income left to common shareholders plus depreciation and amortization, less gains on selling property, plus property write-downs. It is worked out from SEC filings, so it can differ from the FFO a REIT reports. A REIT that owns property is compared only with other such REITs, preferably of its own property type, and valued on peer price to FFO and peer value to sales, with no cash-flow model; its health check scores FFO margin, return on equity on FFO and its FFO track record. A REIT that mostly lends, or whose leases are booked as loans, is valued on peer price to earnings, and a lender also on peer price to book; for a lender both are its common shareholders' (earnings after preferred dividends, book value less preferred stock), and price to book is left out where it pays preferred dividends but its filings don't give the amount of its preferred stock. Timber REITs are valued as other companies are. A REIT whose debt can't be read from its filings gets no debt measures and no value to sales, and a REIT or a bank whose share count changed after its latest SEC filing gets no fair value.
- **Lenders that tag no revenue line** get one from their income statement, read one way for every year: a consumer or business lender's net interest income plus noninterest income (Synchrony Financial), with one-time gains on selling a business or securities left out of its growth, and a mortgage REIT's net interest income (Annaly, AGNC). A mortgage REIT's gains and losses on its securities and hedges stay in its earnings. They are compared only with other financial companies and never valued on sales, and they don't take peer places of companies that aren't read this way.
- **Business development companies** (Ares Capital and other funds listed as companies that lend to private companies) have their total investment income as revenue, are compared only with each other, and are valued on peer price to net investment income (interest, dividends and fees less costs, before gains and losses on investments, after any preferred dividends) and peer price to book (net asset value less preferred stock). One that doesn't tag its net investment income is valued on price to book alone. Their verdicts, like those of the lenders above, weren't part of the backtest below.
- **Banks** are handled the way bank analysts handle them. Revenue is net interest income plus noninterest income. They are compared only with other banks, matched on return on tangible equity, return on assets, efficiency and size, and valued on peer price to tangible book value and peer price to earnings, with no sales multiple or cash-flow model. Their health check scores return on equity, return on assets, the efficiency ratio and tangible equity to assets instead of profit margin, cash flow and debt measures. A bank gets no fair value when it lost money, when its newest full year of results is more than a year old, when its share count changed after its latest balance sheet, or when an acquisition's loan-loss allowance swamped last year's earnings, and its tangible book value is left blank when it pays preferred dividends but its filings don't give the amount of its preferred stock.

### How accurate is it?

The model was rerun as it would have run on four past dates (the last Friday of September 2022, 2023, 2024 and 2025), using only the SEC facts filed before each date, and each call was compared with the stock's total return over the next 52 weeks relative to the average listed company. The midpoint's upside against that return, as a rank correlation:

| As of | Companies valued | Rank correlation | Undervalued minus overvalued, average return |
|---|---|---|---|
| Sep 2022 | 2,068 | +0.078 | +5.5 points |
| Sep 2023 | 2,093 | -0.073 | -4.3 points |
| Sep 2024 | 2,138 | +0.012 | +2.1 points |
| Sep 2025 | 2,228 | +0.072 | +5.7 points |

Taken together that is close to no predictive power (+0.022 on average). The model before its latest change scored +0.051, -0.072, +0.009 and +0.060 on the same test (+0.012 on average); the difference comes from no longer counting price to earnings outside finance (the input fixes made alongside it moved the average by -0.003), a change picked from 20 tried on these same four dates, so this table flatters it somewhat. By kind of company: for banks (price to tangible book value and price to earnings) the calls worked on every date, the undervalued ones beating the overvalued ones by 5.5 to 16 points; for other financial companies on three dates of four; for REITs and for companies outside finance, most of the list, they did not. Among companies worth $10B or more the calls ranked the next year's returns against other large companies slightly better than that (+0.040 on average), but the best-known companies called overvalued (Nvidia, Eli Lilly, Costco, Walmart and others) went on to beat other large companies in 21 of 33 cases. Each report says this in plain words under its verdict. The test has limits: it only covers companies listed today (those delisted, acquired or bankrupt since are missing), four years in which large companies beat small ones, today's sector labels, and past market values rebuilt from today's share counts.

## Data sources

| Data | Source |
|---|---|
| Financial statements | SEC XBRL frames API (`data.sec.gov`) |
| Incorporation state, HQ, industry code (SIC) | SEC company submissions |
| Form 4 and 13D filings | EDGAR daily form index |
| Listings, sector, industry, market value | Nasdaq's public stock screener |
| Prices, 52-week range, stock splits, analyst count | Yahoo Finance public endpoints |
| Headlines | Yahoo Finance RSS, Google News RSS as a fallback |

Financials use each company's latest fiscal year and latest balance sheet, read from the current calendar quarter too, so a company whose quarter ended in its first months (Oracle's August 2026) shows that balance sheet and the share count on that report's cover. Where a later full fiscal year's yearly report gives results but no revenue the site can read, the company gets no figures rather than an older year's shown as current, and its report says why (Cullinan Therapeutics, whose last year with revenue was 2022); its own income statements are read for the revenue first. A year's revenue is read from the first of the usual revenue labels the company uses, unless the same filing shows another to be its revenue: a label at $0 gives way to one above it (Flowserve tags its "Revenues" as $0 beside $4.73B of sales), one that the filing's gross profit and cost of sales don't add up to gives way to one they do (Molson Coors' $11.14B of net sales after excise taxes, not its $13.04B before them), and revenue from customer contracts gives way to a total that also counts what a lender earns on its loans or a utility under its regulators' programs (American Express's $72.23B of revenue net of interest expense, not its $41.30B from customer contracts). A company that tags only its whole group's profit, minority holders' share included, has that share taken off where its market value counts only its listed shares, the ones its earnings per share are on (Interactive Brokers' 2025: $984M of the group's $4.36B, the rest going to the holders of its operating company's other units), and the market value is then set against the whole business's sales through the listed shares' share of the profit. Where the market value also counts the other holders' shares, as Nasdaq's does for TWFG, the whole group's profit is the one that matches it and stays. Debt is read from the balance sheet's broad debt lines, its lines for each kind of debt (notes, credit lines, convertibles, secured, senior and unsecured debt, loans), and a total of all of it where a company tags only that (KB Home's $1.97B of notes and loans payable, Aflac's $8.73B of debt with finance leases), the largest reading counting; those totals, like the kinds below, count only where they are within total liabilities. Kinds some companies tag only under their noncurrent and current lines (Chewy's secured debt and senior notes) make a reading of their own rather than being added to the others, since companies that tag both often give the same debt under each, so a company whose debt is spread over both kinds can still show less than its balance sheet. A latest balance sheet that tags no debt, where recent ones showed debt of at least a tenth of total liabilities that wasn't repaid (the liabilities didn't fall with it, and no balance sheet since tagged it as none), leaves the debt unknown rather than zero (Lennar's of May 2026, whose notes are tagged only by segment). So does debt that is mostly a finance arm's, lending to the company's own customers and dealers, which methods that subtract debt would count as funding the business they value: CNH Industrial's $22.0B of its $26.0B (`fundamentals.FINANCE_ARMS`), as the debt check already does for General Motors, Caterpillar, Ford, Deere and PACCAR. Free cash flow is operating cash flow less capital spending, read from the usual capex lines and, where a company tags none of them, from its payments for other productive assets, capital improvements or machinery and equipment (Verizon's $17.0B of 2025 capital spending, Corning's, Dominion Energy's, Ralph Lauren's), unless that figure is under a quarter of the year's depreciation and so only a piece of it. Where no capital spending can be read but the company must have some (it owns assets that wear out), free cash flow is left unread rather than taken to be its whole operating cash flow. Financial companies and REITs aren't read from those extra lines, since a broker's or insurer's operating cash flow moves with its trading book and policyholders' money, and a REIT's spending on improvements sits beside the buildings it develops and buys; nor are aircraft or equipment bought to lease out. Cash is read from the latest balance sheet's cash line, or where a company tags none, its cash with restricted cash and that of any business held for sale (Trane Technologies' $1.32B at June 2026). A latest balance sheet that tags no cash at all, where recent ones held some, leaves the cash and net cash unknown rather than zero (BorgWarner's, whose $2.45B is tagged only under a name of its own), so its net cash isn't scored and the methods that add it are left out. A company that moved to a new SEC registrant (Exxon Mobil's 2026 holding company) keeps the old registrant's annual reports until the new one files its own (`fundamentals.SUCCESSORS`). Where a forward stock split came after a company's latest SEC cover page and the listing's market value still reflects the old share count, the market value is rebuilt from the cover page's count carried through the split. A listing without financial figures says why on its report, from its SEC filing record (`pipeline/filers.py`, kept in `.cache/filers.json`): no yearly report filed yet (its quarterly revenue from its 10-Qs is shown, with no yearly figure worked out from it), yearly reports under international accounting rules or in another currency, a fund or a blank-check company, or yearly revenue under labels the site doesn't read. Each run's log lists the US listings worth $10B or more without figures, and warns about those whose reason isn't an expected one.

## Automatic updates

`.github/workflows/update.yml` runs at 03:15 UTC Tuesday through Saturday. That is 11:15pm New York time in summer and 10:15pm in winter, Monday through Friday, after EDGAR stops accepting filings at 10pm. Each run:

1. analyzes that day's Form 4 and 13D filings and rechecks the previous day for late arrivals,
2. refreshes prices, financials, the screener, headlines and every ticker report,
3. publishes the site to GitHub Pages.

Filings and results are cached between runs, so pushing a design change redeploys in about a minute without redoing the analysis. To force a fresh analysis, open **Actions**, choose **Analyze and publish**, then **Run workflow**. The first run backfills 30 days of filings and takes about 90 minutes. Later runs take about 45 minutes.

**SEC contact (recommended):** the SEC asks automated tools to identify themselves with a contact email. Add a repository secret named `SEC_USER_AGENT` with a value like `Your Name you@yourdomain.com` (Settings, then Secrets and variables, then Actions). The SEC rejects github.com addresses. Without the secret, the pipeline sends a placeholder contact.

GitHub pauses scheduled workflows in public repositories after 60 days with no commits. Any push turns them back on.

## Run it locally

Requires Python 3.10+. No packages to install.

```bash
python -m pipeline.build --limit 300      # quick sample of 300 stocks
python -m pipeline.build                  # everything, about 45 minutes after the first run
python -m http.server 8123 --directory site
```

Then open http://localhost:8123.

The screener backtest (see "How the screener has done") downloads its own data once (about 1.5 GB, and about 3 GB in `.cache/backtest/` with the indexes it builds), then reruns any version of the screener on the four past dates, from a commit or from this checkout:

```bash
python -m backtest.data all
python -m backtest.screener run --code 719a878 --label before
python -m backtest.screener run --code . --label mine
python -m backtest.screener compare before mine
```

`backtest/README.md` has every command, the method and its limits.

## Project layout

```
pipeline/            Python, standard library only
  build.py           runs every step and writes site/data/
  universe.py        US-listed common stocks
  fundamentals.py    SEC financial statements -> ratios
  market.py          prices, 52-week range, analyst count
  screener.py        tool 1
  insiders.py        tool 2
  news.py            headline labeling rules
  report.py          tool 3: health scores and fair value
  filers.py          why a listing has no figures, from its SEC filing record
  reit_types.py      REIT property types by SEC company number, for picking REIT peers
backtest/            the screener as it would have run on past dates (point in time), and how it did since
site/                static site served by GitHub Pages
  index.html         cover page
  screener.html, insiders.html, report.html
  assets/            CSS and JavaScript, no build step
```

## Disclaimer

Educational research tool. Nothing here is investment advice, a recommendation or a price target. Data can be late, incomplete or wrong. Always read the underlying filings and do your own research.
