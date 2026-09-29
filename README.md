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
| Net profit margin | 8% or more |
| Location | headquartered **and** incorporated in a US state (rules out ADRs and offshore shells) |
| Viability | equity above zero, free cash flow above zero, profitable in 2 of the last 3 years, sales not down more than 5% |

Flags, not rules: **Out of favor** (25%+ below the 52-week high) and **Undiscovered** (two or fewer analysts). Passing companies are ranked by a 0 to 100 score, and every point is itemized on the page.

### 2. Insider buying

- Sources: SEC **Form 4** (executives, directors, 10%+ owners) and **Schedule 13D** (new 5%+ holders who may seek influence), read from EDGAR's daily index.
- Only open-market purchases (transaction code `P`) count. Option exercises, grants and sales are excluded.
- Shows who bought, their role, the dollar amount, the price paid, and how today's price compares.
- **Tier 1:** $10M+ bought in the last 30 days. **Tier 2:** $1M+ total, or any buyer whose stake grew 20%+.
- Sorted newest filing first. Each weekday's filings are analyzed that evening.

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

Financials use each company's latest fiscal year and latest balance sheet. A company that moved to a new SEC registrant (Exxon Mobil's 2026 holding company) keeps the old registrant's annual reports until the new one files its own (`fundamentals.SUCCESSORS`). Where a forward stock split came after a company's latest SEC cover page and the listing's market value still reflects the old share count, the market value is rebuilt from the cover page's count carried through the split. A listing without financial figures says why on its report, from its SEC filing record (`pipeline/filers.py`, kept in `.cache/filers.json`): no yearly report filed yet (its quarterly revenue from its 10-Qs is shown, with no yearly figure worked out from it), yearly reports under international accounting rules or in another currency, a fund or a blank-check company, or yearly revenue under labels the site doesn't read. Each run's log lists the US listings worth $10B or more without figures, and warns about those whose reason isn't an expected one.

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
site/                static site served by GitHub Pages
  index.html         cover page
  screener.html, insiders.html, report.html
  assets/            CSS and JavaScript, no build step
```

## Disclaimer

Educational research tool. Nothing here is investment advice, a recommendation or a price target. Data can be late, incomplete or wrong. Always read the underlying filings and do your own research.
