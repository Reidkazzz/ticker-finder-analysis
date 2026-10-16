# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Individual investors researching US stocks on their own: people new to the market who want to learn why a stock looks
cheap or risky, and experienced self-directed investors who want a fast, dense screen. The site serves both: data-dense
by default, with the plain-English explanation of every number kept one click or hover away rather than removed.
They visit on desktop monitors and laptops for research sessions and on phones to check a list or a single stock.

## Product Purpose

TFA (Ticker Finder Analysis) finds smaller US companies that pass fundamental quality checks and may be trading at
attractive prices, and explains exactly why. Three tools:

- **Sector screener:** which small US companies are cheap, profitable and low on debt right now, ranked by a 100-point
  score, with separate research lists (companies under 1x net cash with their cash runway, banks and insurers under
  book value, overpriced giants, industries out of favor, insider buying).
- **Insider buying:** who inside a company is buying its stock with their own money.
- **Ticker report:** for any listed US stock, a health score, a fair-value verdict with its methods, peers, financial
  history, insider trades and news.

Success: a visitor can scan the ranked list, understand why a company scores what it does, and open its report to
check the reasoning, without ever being shown a number the filings don't support.

## Positioning

Every figure comes from the companies' own SEC filings, and every claim a page makes is literally true for that data.
The 100-point score is fully transparent (each component's points shown), the verdict's methods and their backtested
track record are disclosed in plain words, including where the model does not predict well. It is an estimate, not a
price target, and not investment advice.

## Operating Context

- Static site on GitHub Pages; a GitHub Actions job rebuilds all data every weekday evening (no servers, no API keys).
- The front end reads JSON the Python pipeline writes: data/meta.json, universe.json, screener.json, insiders.json,
  and data/t/<SYMBOL>.json per company.
- Pages: home (index.html), screener.html, report.html, insiders.html; shared assets in site/assets.

## Capabilities and Constraints

- Preserve the methodology exactly: screening rules, thresholds, score weights, verdict methods and eligibility are
  set by the pipeline and changed only with evidence, never by a visual redesign.
- Preserve existing URLs, query parameters and the data contract with the pipeline.
- Never fabricate prices, metrics, rankings, testimonials, user counts or performance claims; label missing or stale
  data as such.
- Visible copy uses plain English, no en or em dashes, and never implies a high score or "undervalued" verdict
  guarantees returns.
- No build step: plain HTML, CSS and JavaScript modules.

## Brand Commitments

- Name: TFA (Ticker Finder Analysis). The previous name "Ticker Finder" and its mark are retired.
- Voice: concise, confident, plain English; explains rather than hypes.

## Evidence on Hand

- Real data for about 5,400 US listings, refreshed nightly from SEC filings (EDGAR XBRL), Nasdaq listings and prices.
- The point-in-time backtest of the fair-value verdicts (2022 to 2025) described in README.md.
- No testimonials, user numbers, press or partnerships exist; none may be implied.

## Product Principles

1. Truth before polish: a number or sentence that isn't literally supported by the data doesn't ship, however good
   it looks.
2. Show the reasoning: every score, verdict and flag can be opened to see what produced it.
3. Dense, then explained: the data leads; the explanation is always one step away.
4. The ranked list is the product: everything else supports finding and checking a company on it.

## Accessibility & Inclusion

WCAG AA contrast, full keyboard navigation, labelled controls and visible focus states; usable on phones without
horizontal page overflow.
