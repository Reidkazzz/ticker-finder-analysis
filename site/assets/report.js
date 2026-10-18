import { asShown, attachSearch, chrome, closeWords, date, displayName, esc, healthBand, icon, load, localToday, longDate, meter, money, paintDataStatus, pct, price, searchMarkup, signClass, vsPaid } from "./common.js";
import { ledger } from "./ledger.js";

// The report has its own search box at the top of the page, which opens a report in place.
chrome({ search: false });

const host = document.querySelector("[data-report]");
const startHead = document.querySelector("[data-start]");
const searchHost = document.getElementById("report-search");
searchHost.innerHTML = searchMarkup("report-q", "Another ticker or company");
const searchInput = searchHost.querySelector("input");
attachSearch(searchHost.querySelector("form"), {
  onPick: (sym) => {
    searchInput.value = "";
    searchInput.blur();
    go(sym);
  },
});

const TITLE = "TFA";
const thisYear = localToday().slice(0, 4);
// A date with its year only when it isn't this year's: "Oct 12", "Dec 18, 2025".
const day = (iso) => (!iso ? "" : iso.slice(0, 4) === thisYear ? date(iso) : longDate(iso));
const fullDate = longDate;
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
const andList = (a) => (a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
// One decimal for millions and billions ($109.4B, $375.0M), floored like money() so a figure never reads larger.
const amount = (n) => money(n, Math.abs(n) >= 1e6 ? { digits: 1 } : {});

// ---- Navigation in place: search picks, peer links and the popular list open a report without reloading. ----

// Each history entry carries an id, so Back and Forward return to where that report was scrolled.
const newId = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
let entry = history.state?.id || newId();
const initialY = history.state?.y || 0;
try { history.replaceState({ ...(history.state || {}), id: entry }, ""); } catch {}
const scrolls = new Map();
let restoring = true; // scroll events while a report is swapped in don't count as the reader's position
let current; // the symbol shown, or null on the start page
let shown = null; // the report shown, whose price chart its span buttons redraw
addEventListener("scroll", () => { if (!restoring) scrolls.set(entry, scrollY); }, { passive: true });
addEventListener("pagehide", () => {
  try { history.replaceState({ ...(history.state || {}), id: entry, y: scrollY }, ""); } catch {}
});

function go(sym) {
  scrolls.set(entry, scrollY);
  try { history.replaceState({ ...(history.state || {}), id: entry, y: scrollY }, ""); } catch {}
  entry = newId();
  history.pushState({ id: entry }, "", `?t=${encodeURIComponent(sym)}`);
  show(sym, { y: 0, focus: true });
}

addEventListener("popstate", (e) => {
  const t = new URLSearchParams(location.search).get("t");
  const sym = t ? t.toUpperCase() : null;
  // A jump to a #fragment (the skip link) changes the entry but not the report.
  if (sym === (current ?? null) && current !== undefined) {
    if (e.state?.id) entry = e.state.id;
    return;
  }
  entry = e.state?.id || newId();
  route({ y: scrolls.get(entry) ?? e.state?.y ?? 0, focus: true });
});

// A jump within the report scrolls without touching the address, since a #fragment there would add an entry.
function jumpTo(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: "start" });
  if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
}

host.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const jump = e.target.closest("[data-jump]");
  if (jump) {
    e.preventDefault();
    jumpTo(jump.dataset.jump);
    return;
  }
  const link = e.target.closest("a[data-sym]");
  if (link) {
    e.preventDefault();
    go(link.dataset.sym);
    return;
  }
  // The charts' period: by fiscal year or by quarter.
  const fc = e.target.closest("[data-fc]");
  if (fc) {
    const sec = fc.closest("section");
    sec.querySelectorAll("[data-fc]").forEach((b) => b.setAttribute("aria-pressed", String(b === fc)));
    sec.querySelectorAll("[data-fc-grid]").forEach((g) => { g.hidden = g.dataset.fcGrid !== fc.dataset.fc; });
    return;
  }
  // The price chart's span: redrawn from the same weekly closes.
  const span = e.target.closest("[data-span]");
  if (span && shown) {
    const panel = span.closest(".r-price");
    panel.outerHTML = pricePanel(shown, span.dataset.span);
    host.querySelector(`.r-price [data-span="${span.dataset.span}"]`)?.focus();
  }
});

// ---- Fair value ----

const VERDICT = { undervalued: "Undervalued", fair: "Fairly valued", overvalued: "Overvalued" };
const NEWS_TAG = { bullish: "tag-up", bearish: "tag-down", neutral: "", none: "" };
const CONFIDENCE = {
  higher: "All three methods were available. They can still disagree, so compare the three estimates above before leaning on the range.",
  moderate: "Two methods were available.",
  low: "Only one method was available, so treat this range loosely.",
};

const noteBox = (n) => `<p class="notice r-note">${icon("info")}<span>${esc(n)}</span></p>`;

// Where the one-time items note (or, for a REIT, how its FFO comes from its net income) is shown, once: in the
// screener's ledger, which prints it, for a company on the list; beside the fair value it changes; else by the
// profit bars; else under the financial history; else in the notes.
function oneTimeHome(r) {
  const f = r.fundamentals;
  if (!f?.one_time_note) return null;
  if (r.screener === true) return "screener";
  if (r.valuation && VERDICT[r.valuation.verdict]) return "verdict";
  if (r.health?.length) return "health";
  if (f.history?.length) return "history";
  return "notes";
}

// The model's track record by kind of company, from a point-in-time backtest of this code on four past dates (the
// README's "How accurate is it?"): rerun on the filings public then, against each stock's next 12 months.
const TESTED = "We re-ran this model as it would have run in late September of 2022, 2023, 2024 and 2025, using only the SEC filings public at the time, and followed each stock's return over the next 12 months, dividends included.";
const LISTED = "The test could only include companies still listed today, and four years is a short record.";
const TRACK_RECORD = {
  bank: `${TESTED} For banks it helped: in each of the four years, the banks it called undervalued did better than those it called overvalued, by 5.5 to 16 percentage points, about 9 on average. ${LISTED}`,
  financial: `${TESTED} For financial companies other than banks the record is mixed: the ones it called undervalued did better than those it called overvalued in three of the four years, by 3 to 19 percentage points, but worse in the other, by 9.5. ${LISTED}`,
  reit: `${TESTED} For REITs the verdicts did not help: in three of the four years, the REITs it called undervalued did worse than those it called overvalued. Read the verdict as how the price compares with similar REITs, not as a forecast. ${LISTED}`,
  // Lenders whose revenue is read from net interest income and business development companies (revenue_basis) had no
  // figures when the backtest ran, so none of them was in it.
  untested: "How well the verdicts work for this kind of company hasn't been measured yet. We re-ran this model as it would have run in late September of 2022, 2023, 2024 and 2025, using only the SEC filings public at the time, and followed each stock's return over the next 12 months, but lenders whose revenue is read from their net interest income, and business development companies, had no figures on this site then, so none was in that test. Read the verdict as how today's price compares with similar companies, not as a prediction.",
  other: `${TESTED} For companies outside finance, most of those valued here, the verdicts barely predicted which stocks would do better. The ones called undervalued beat those called overvalued by 0.5 to 5 percentage points in three of the years and trailed them by 5.5 in the other, close to even overall. Calls on the very largest companies were no better: the best-known ones it called overvalued, such as Nvidia and Eli Lilly, more often went on to beat other large companies than to trail them. Read the verdict as how today's price compares with similar companies and with a simple forecast of the company's cash, not as a prediction. ${LISTED}`,
};

// Which of a lender's two peer estimates its fair value used ([price to earnings, price to book]); both where it has no
// fair value, whose explanation then names the methods that would apply.
function lenderMethods(r) {
  const names = (r.valuation?.methods || []).map((m) => m.name);
  if (!names.length) return [true, true];
  return [names.includes("Peer price to earnings"), names.includes("Peer price to book")];
}

// What kind of company the fair value treats this as, which decides the methods it uses (report.fair_value).
function valuationKind(r) {
  const f = r.fundamentals || {};
  if (f.bank) return "bank";
  if (f.reit === "property") return "reit";
  // A company whose revenue is read the way a lender's or a business development company's is (revenue_basis) is valued
  // as a financial one whatever sector Nasdaq files it under (report.financial).
  if (r.sector === "Finance" || f.reit === "mortgage" || f.revenue_basis) return "financial";
  return "other";
}

// A plain-English account of how the estimate is made and how well it has done, under the methods. The discount rates
// and growth limits repeat report.py's CF_RATES, CF_SMALL_RATE, CF_RATE_BASIS, CF_GROWTH_CAP and CF_TERMINAL, and the
// track record comes from the point-in-time backtest described in the README. `none`: the company has figures but no
// fair value, so the explanation names the methods that would apply, and is left out where none would.
function howItWorks(r, none = false) {
  const kind = valuationKind(r);
  const item = (title, text) => `<li><b>${title}</b><span>${text}</span></li>`;
  // What report.fair_value checks before using each method: value to sales needs sales that look whole, a profit
  // without one-time items, and the debt and cash read (debt_doubt null, cash not null); the cash-flow model needs
  // the debt and cash read. A company with a fair value is described in full.
  const f = r.fundamentals || {};
  const unread = f.debt_doubt != null || f.cash === null;
  const bySales = !none || !(unread || f.sales_doubt || !(f.revenue > 0) || !(f.adj_net_income > 0));
  const byCash = !none || !unread;
  if (kind === "other" && !bySales && !byCash) return "";
  const peersLine = kind === "bank"
    ? "The 10 US-listed banks most like this one in return on tangible equity, return on assets, efficiency and size."
    : kind === "reit"
      ? "The 10 US-listed REITs that own property most like this one, preferring its own property type."
      : "The 10 US-listed companies most like this one in profit margins, cash flow, sales growth, size and sales per dollar of assets, preferring its own industry.";
  const items = [item("Similar companies", `${r.peer_basis ? esc(r.peer_basis) : peersLine} Each estimate that starts with Peer applies their median multiple to this company's own figures: what the stock would be worth if the market priced it like them.`)];
  if (kind === "other") {
    if (bySales) items.push(item("Value to sales", "What each similar company's whole business costs, its debt included and its cash taken off, per dollar of yearly sales. That multiple times this company's sales, less its debt and any other claims that come before its shares, plus its cash, gives the value of its shares."));
    if (byCash) {
      items.push(item("Cash-flow model", "Starts from free cash flow: the cash the business brings in each year after paying for its operations, its equipment and the stock it pays staff with. The cash flow grows at the company's recent sales growth, kept between 0% and 12% a year, for 5 years, then slows evenly to 2.5% a year, about the pace of inflation, by year 10 and keeps that pace after."));
      items.push(item("The discount rate", "Money that arrives later is worth less than money today, so each future year's cash is shrunk by a discount rate: the yearly return investors want for the risk of owning the stock. For companies worth $10B or more it is 8%: about 4% from safe US government bonds plus about 4% for the extra risk of stocks, per Damodaran's January 2026 figures. Smaller companies are riskier, so the rate goes up a point for each step down in size: 9% from $2B, 10% from $300M and 11% below that. It is not moved with daily bond yields: in our tests, a rate that moved with bond yields and with how much each stock moves with the market did not make the verdicts more accurate."));
    }
    // Only where the estimates show a price to earnings: for comparison when other estimates count, or counted when
    // it is the only one (report.fair_value, PE_COUNTS). Data written before PE_COUNTS has no "counted", and there a
    // price to earnings beside other estimates counted with them, so it gets neither item.
    const shown = r.valuation?.methods || [];
    const pe = shown.find((m) => m.name === "Peer price to earnings");
    if (pe && pe.counted === false) {
      items.push(item("Price to earnings, shown for comparison", "Similar companies' price to earnings appears under the estimates but is not counted. In our tests on 2022 to 2025 data, leaving it out made the verdicts for companies outside finance slightly better at telling which stocks would do better, since one year's profit swings more than sales and cash flow. The gain was small."));
    } else if (pe && shown.length === 1) {
      items.push(item("Price to earnings", "Similar companies' price to earnings, applied to this company's profit, is the only estimate that could be used here, so it counts. Where value to sales or the cash-flow model can be used, price to earnings is shown for comparison only."));
    }
  } else if (kind === "bank") {
    items.push(item("Price to tangible book and price to earnings", "Tangible book value is what the shareholders own, less goodwill and other intangible assets. Banks are valued this way and on their earnings, not on sales or a cash-flow model, since their borrowing is their raw material."));
  } else if (kind === "reit") {
    items.push(item("Price to FFO and value to sales", "Funds from operations (FFO) is the profit measure REITs report: net income with property depreciation added back and property sales left out."));
  } else if (r.fundamentals?.revenue_basis === "investment_income") {
    // The methods its fair value used, where it has one: both, or one where the other can't be worked out.
    const [pe, pb] = lenderMethods(r);
    const on = pe && !pb ? "price to net investment income" : pb && !pe ? "price to book" : "price to net investment income and price to book";
    items.push(item(cap(on), `A business development company is a fund listed as a company that lends to and invests in private companies. Its borrowing and its loans are the business itself, so it is valued on similar business development companies' ${on}, with no sales multiple or cash-flow model. Net investment income is what its loans and stakes earn in interest, dividends and fees, less its costs, before the gains and losses on them that swing its net income from year to year. Book value is its net asset value: the loans and stakes it holds as its books carry them, less what it owes. Both are counted after any preferred stock, which is owed to preferred shareholders first.`));
  } else if (r.fundamentals?.revenue_basis && r.fundamentals.reit === "mortgage") {
    const [pe, pb] = lenderMethods(r);
    const on = pe && !pb ? "price to earnings" : pb && !pe ? "price to book" : "price to earnings and price to book";
    items.push(item(cap(on), `A REIT that lends or holds mortgage bonds pays for them mostly with borrowed money, so it is valued on similar REITs that lend: their ${on} (book value being its loans and securities as its books carry them, less what it owes), with no sales multiple or cash-flow model. Its gains and losses on its investments and hedges count in its earnings, since they are part of its business. Earnings and book value are counted after any preferred stock, which is owed to preferred shareholders first.`));
  } else if (r.fundamentals?.revenue_basis === "bank") {
    items.push(item("Price to earnings", "A lender whose revenue is its net interest income (interest earned less interest paid) plus fees is valued on similar companies' price to earnings. Most lenders it is compared with count their interest before what they pay on their borrowing, so their sales aren't on the same footing and no sales multiple is used, nor a cash-flow model, since borrowing is a lender's raw material."));
  } else {
    items.push(item("Price to sales and price to earnings", "Insurers, lenders and other financial companies borrow and invest as part of the business, so they are valued on similar companies' price to sales and price to earnings (a mortgage REIT on price to earnings, and a lender also on price to book), with no cash-flow model."));
  }
  items.push(item("The verdict", "The midpoint is the middle of the estimates counted (with two, their average). A midpoint more than 50% above or below the price needs two independent estimates to agree, and estimates from the same similar companies count as one. The range spans the estimates and is at least 10% either side of the midpoint. Undervalued means today's price is below the range, overvalued means above it."));
  items.push(item("How well has this worked?", r.fundamentals?.revenue_basis ? TRACK_RECORD.untested : TRACK_RECORD[kind]));
  return `<details class="disclose v-how">
    <summary>${icon("question", "lead-i")}${none ? "How a fair value is worked out for this company" : "How this fair value is worked out"}${icon("caret-down", "caret")}</summary>
    <div class="body"><ul class="how-list">${items.join("")}</ul></div>
  </details>`;
}

// The fair-value range on a price scale: the band from low to high, its midpoint, and today's price. The gap from the
// midpoint to the price is drawn in the sign of the figure the verdict line states. Labels are placed by fitRange().
function rangeViz(v, px) {
  const lo = Math.min(v.low, px) * 0.85, hi = Math.max(v.high, px) * 1.15;
  const at = (n) => ((n - lo) / (hi - lo)) * 100;
  const mid = v.mid != null ? at(v.mid) : null;
  const p = at(px);
  const gap = mid == null ? "" : `<span class="rv-gap ${v.upside > 0 ? "up from-right" : "down"}" style="left:${Math.min(mid, p).toFixed(2)}%;width:${Math.abs(p - mid).toFixed(2)}%"></span>`;
  return `<div class="rv" data-rv role="img" aria-label="${esc(`Estimated fair value ${price(v.low)} to ${price(v.high)}${v.mid != null ? `, midpoint ${price(v.mid)}` : ""}; today's price ${price(px)}`)}">
    <span class="rv-px" style="--x:${p.toFixed(2)}%" aria-hidden="true">Price ${price(px)}</span>
    <div class="rv-track" aria-hidden="true">
      <span class="rv-band" style="left:${at(v.low).toFixed(2)}%;width:${(at(v.high) - at(v.low)).toFixed(2)}%"></span>
      ${gap}
      ${mid == null ? "" : `<span class="rv-mid" style="left:${mid.toFixed(2)}%"></span>`}
      <span class="rv-mark" style="left:${p.toFixed(2)}%;--from:${(mid ?? p).toFixed(2)}%"></span>
    </div>
    <span class="rv-lo" style="--x:${at(v.low).toFixed(2)}%" aria-hidden="true">${price(v.low)}</span>
    <span class="rv-hi" style="--x:${at(v.high).toFixed(2)}%" aria-hidden="true">${price(v.high)}</span>
  </div>`;
}

// Puts each label of a range over its point, kept inside the scale and apart from each other: the low and high labels
// move apart where they would touch, and the high one drops to a second line where the scale is too narrow for both.
function fitRange(rv) {
  const W = rv.clientWidth;
  if (!W) return;
  const xOf = (el) => (parseFloat(el.style.getPropertyValue("--x")) / 100) * W;
  const inside = (cx, w) => Math.max(0, Math.min(W - w, cx - w / 2));
  const place = (el, x) => { el.style.left = `${x.toFixed(1)}px`; el.style.transform = "none"; };
  const pxEl = rv.querySelector(".rv-px");
  place(pxEl, inside(xOf(pxEl), pxEl.offsetWidth));
  const a = rv.querySelector(".rv-lo"), b = rv.querySelector(".rv-hi");
  const wa = a.offsetWidth, wb = b.offsetWidth, gap = 12;
  let xa = inside(xOf(a), wa), xb = inside(xOf(b), wb);
  rv.classList.remove("rv-stack");
  if (xa + wa + gap > xb) {
    const m = (xOf(a) + xOf(b)) / 2;
    xa = m - gap / 2 - wa;
    xb = m + gap / 2;
    if (xa < 0) { xb -= xa; xa = 0; }
    if (xb + wb > W) { xa -= xb + wb - W; xb = W - wb; }
    if (xa < 0) {
      rv.classList.add("rv-stack");
      xa = inside(xOf(a), wa);
      xb = inside(xOf(b), wb);
    }
  }
  place(a, xa);
  place(b, xb);
}
const rangeObserver = window.ResizeObserver ? new ResizeObserver((es) => es.forEach((e) => fitRange(e.target))) : null;
function fitRanges() {
  host.querySelectorAll("[data-rv]").forEach((rv) => { fitRange(rv); rangeObserver?.observe(rv); });
  // The labels' widths change once the web font arrives.
  document.fonts?.ready.then(() => host.querySelectorAll("[data-rv]").forEach(fitRange));
}

// The heading where there is no verdict: a fair value withheld for a stated reason, a company the site doesn't value
// (coverage, from its SEC filings), or one whose figures gave no estimate.
const NOT_VALUED = new Set(["fund", "blank_check", "foreign", "currency"]);
const NOT_YET = new Set(["no_annual", "annual_untagged"]);
function noVerdictWord(r) {
  if (r.valuation?.withheld) return "No fair value estimate";
  const kind = !r.valuation && r.coverage?.kind;
  if (kind && NOT_VALUED.has(kind)) return "Not valued";
  if (kind && NOT_YET.has(kind)) return "Not enough data yet";
  if (!kind && r.fundamentals) return "No fair value estimate";
  return "Not enough data";
}

function verdictPanel(r, home) {
  const v = r.valuation;
  const note = home === "verdict" ? noteBox(r.fundamentals.one_time_note) : "";
  if (!v || !VERDICT[v.verdict]) {
    // Older data has neither a reason nor coverage, so it gets the general explanation.
    // The heading already says there is no estimate, so a reason that opens by saying so starts after it.
    const why = v?.withheld ? esc(v.withheld.replace(/^(?:There is no fair value estimate|No fair value is shown)\.\s*/, ""))
      : r.coverage?.note ? esc(r.coverage.note)
      : r.fundamentals ? "None of the valuation methods for this kind of company could be worked out from its figures."
      : `There is not enough reliable data to estimate a fair value. This happens when we could not find a full year of financials in the company's SEC filings,
      which is common for new listings and foreign companies, foreign banks among them. It also happens when a company is losing money and has no positive cash flow to value,
      since comparing it with others on sales says little about what it is worth.`;
    return `<section class="frame r-verdict is-none" aria-labelledby="verdict-h">
      <h2 class="v-word" id="verdict-h"><span class="sr-only">Fair value: </span>${noVerdictWord(r)}</h2>
      <p class="v-line">${why}</p>${note}
      ${r.fundamentals && !r.coverage ? howItWorks(r, true) : ""}
    </section>`;
  }
  const up = v.upside;
  const conf = v.confidence_note || CONFIDENCE[v.confidence] || "";
  const peers = r.peers?.length && v.methods.some((m) => m.name.startsWith("Peer"));
  return `<section class="frame r-verdict" aria-labelledby="verdict-h">
    <h2 class="v-word" id="verdict-h"><span class="sr-only">Fair value verdict: </span>${VERDICT[v.verdict]}</h2>
    <p class="v-line">Estimated fair value <b>${price(v.low)} to ${price(v.high)}</b>.${up == null ? "" : ` The midpoint of <b>${price(v.mid)}</b> is
      <b class="${signClass(up)}">${pct(Math.abs(up), 0)} ${up >= 0 ? "above" : "below"}</b> today's price.`}${v.limit_note ? ` ${esc(v.limit_note)}` : ""}</p>
    ${r.price ? rangeViz(v, r.price) : ""}
    <ul class="v-methods" aria-label="The estimates behind the range">${v.methods.map((m) => `<li${m.counted === false ? ` class="ref"` : ""}>
      <div class="m-top"><span class="m-name">${esc(m.name)}${m.counted === false ? ` <span class="tag tag-line">For comparison</span>` : ""}</span><span class="m-v">${price(m.value)}</span></div>
      <p>${esc(m.note)}</p></li>`).join("")}</ul>
    ${peers ? `<p class="v-fine"><a href="#peers" data-jump="peers">See the ${r.peers.length} similar companies</a> behind the peer estimates.</p>` : ""}
    ${note}
    <p class="v-fine">${esc(conf)} This is an estimate, not a price target, and not investment advice.</p>
    ${howItWorks(r)}
  </section>`;
}

// ---- Price ----

let chartSeq = 0;
const isoOf = (unix) => new Date(unix * 1000).toISOString().slice(0, 10);

// The price chart's spans, back from its last point: [key, button, days back (null: since January 1), words].
const SPANS = [["3m", "3M", 91, "3 months"], ["6m", "6M", 182, "6 months"], ["ytd", "YTD", null, "this year"], ["1y", "1Y", 366, "12 months"]];
// Weekly closes as a line, spaced by week (the last point is the latest price, a few days after the last weekly close).
// The heading and the change say the span the points actually cover: the span chosen, or since the first point where
// the stock has fewer weeks of prices than that. A span with fewer than two points can't be chosen.
function pricePanel(r, span = "1y") {
  const all = (r.weekly || []).filter((p) => p && p[1] != null);
  const charted = all.length >= 4;
  const endT = all.length ? all[all.length - 1][0] : 0;
  const startOf = ([, , d]) => (d == null ? Date.UTC(new Date(endT * 1000).getUTCFullYear(), 0, 1) / 1000 : endT - d * 86400);
  const inSpan = (s) => all.filter((p) => p[0] >= startOf(s));
  const spec = SPANS.find((s) => s[0] === span) || SPANS[3];
  const pts = spec[0] === "1y" ? all : inSpan(spec);
  const fromHigh = r.high52 && r.price ? r.price / r.high52 - 1 : null;
  // The note on the 52-week range speaks of the chart, so it shows only with one.
  const stats = `<div class="p-side"><dl class="p-stats">
      <div><dt>52-week low</dt><dd>${price(r.low52)}</dd></div>
      <div><dt>52-week high</dt><dd>${price(r.high52)}</dd></div>
      <div><dt>From 52-week high</dt><dd class="${signClass(asShown(fromHigh, 0))}">${fromHigh == null ? "n/a" : pct(fromHigh, 0, true)}</dd></div>
    </dl>${charted ? `
    <p class="p-note">The 52-week low and high are from daily prices, so they can fall outside the weekly closes the chart plots.</p>` : ""}</div>`;
  if (!charted) {
    return `<section class="frame r-price" aria-labelledby="price-h">
      <div class="p-head"><h2 id="price-h">Price</h2></div>
      <p class="r-none">Too few weekly prices to draw a chart yet${pts.length ? ` (${pts.length} so far)` : ""}.</p>${stats}</section>`;
  }
  const W = 600, H = 170, pad = 6;
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const x = (i) => (i / (pts.length - 1)) * W;
  const y = (v) => pad + (1 - (v - lo) / (hi - lo || 1)) * (H - pad * 2);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const first = ys[0], last = ys[ys.length - 1];
  const change = last / first - 1;
  const tone = last >= first ? "up" : "down";
  const d0 = isoOf(pts[0][0]), d1 = isoOf(pts[pts.length - 1][0]);
  // The span is whole when its first point falls within a week of where it begins (a year: 350 days of points).
  const whole = spec[0] === "1y" ? (pts[pts.length - 1][0] - pts[0][0]) / 86400 >= 350 : pts[0][0] - startOf(spec) <= 7 * 86400;
  const title = !whole ? `Price since ${fullDate(d0)}` : spec[0] === "ytd" ? "Price this year" : `Price, last ${spec[3]}`;
  const over = !whole ? `since ${date(d0)}` : spec[0] === "ytd" ? "this year" : `over ${spec[3]}`;
  const spans = `<div class="seg p-spans" role="group" aria-label="Span of the chart">${SPANS.map((s) => {
    const n = s[0] === "1y" ? all.length : inSpan(s).length;
    return `<button class="seg-btn" type="button" data-span="${s[0]}" aria-pressed="${s[0] === spec[0]}"${n < 2 ? " disabled" : ""}>${s[1]}</button>`;
  }).join("")}</div>`;
  const id = `pc-fade-${++chartSeq}`;
  const lastY = y(last);
  return `<section class="frame r-price" aria-labelledby="price-h">
    <div class="p-head"><h2 id="price-h">${title}</h2><span class="p-chg ${signClass(asShown(change, 1))}">${pct(change, 1, true)} <span class="p-over">${over}</span></span></div>
    ${spans}
    <figure class="pc">
      <div class="pc-plot" role="img" aria-label="${esc(`Weekly closing price from ${fullDate(d0)} to ${fullDate(d1)}: ${price(first)} to ${price(last)}, ${pct(change, 1, true)}`)}">
        <svg class="pc-svg ${tone}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true" focusable="false">
          <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" class="pc-stop" stop-opacity=".14"/><stop offset="1" class="pc-stop" stop-opacity="0"/></linearGradient></defs>
          <line class="pc-base" x1="0" x2="${W}" y1="${y(first).toFixed(1)}" y2="${y(first).toFixed(1)}" vector-effect="non-scaling-stroke"/>
          <path d="${line}L${W},${H}L0,${H}Z" fill="url(#${id})"/>
          <path class="pc-line" d="${line}" vector-effect="non-scaling-stroke"/>
        </svg>
        <span class="pc-dot ${tone}" style="left:100%;top:${((lastY / H) * 100).toFixed(2)}%" aria-hidden="true"></span>
      </div>
      <figcaption class="pc-foot" aria-hidden="true">
        <span><small>${esc(fullDate(d0))}</small>${price(first)}</span>
        <span class="pc-end"><small>${esc(fullDate(d1))}</small>${price(last)}</span>
      </figcaption>
    </figure>
    ${stats}
  </section>`;
}

// ---- Screener score: the ledger the screener shows for a company on its list. ----

function scoreSection(r, sc, meta, home) {
  if (r.screener !== true) return "";
  const f = r.fundamentals;
  const sector = r.sector;
  const href = `screener.html${sector ? `?sector=${encodeURIComponent(sector)}` : ""}`;
  const row = sc?.results?.find((x) => x.symbol === r.symbol);
  const head = (count) => `<div class="sec-head"><h2 id="score-h">Screener score</h2>${count ? `<span class="count">${count}</span>` : ""}</div>`;
  if (!row || !Array.isArray(row.score_parts)) {
    return `<section class="sec" id="score" aria-labelledby="score-h">${head("")}
      <p class="sec-lede">${esc(r.symbol)} passes all eight rules of the sector screener, but its score could not be loaded just now. <a href="${href}">Open the screener</a> to see it, or reload this page.</p>
      ${home === "screener" ? noteBox(f.one_time_note) : ""}</section>`;
  }
  const ctx = { rules: sc.rules, industries: sc.industries, insiderDays: meta?.insider_days ?? null };
  const n = sc.results.length;
  const higher = sc.results.filter((x) => x.score > row.score).length;
  const inSector = sc.sectors?.find((s) => s.name === row.sector)?.passed ?? sc.results.filter((x) => x.sector === row.sector).length;
  // Here the ledger's headings sit under this section's h2.
  const body = ledger(row, ctx, { link: false, level: 3 });
  const own = home === "screener" && row.one_time_note !== f.one_time_note ? noteBox(f.one_time_note) : "";
  return `<section class="sec" id="score" aria-labelledby="score-h">${head(`${esc(String(row.score))} of 100 points`)}
    <p class="sec-lede">Passes all eight rules of the sector screener. Of the ${n} companies that pass, ${higher ? `${higher} score${higher === 1 ? "s" : ""} higher` : "none scores higher"}.</p>
    <p class="r-go"><a class="text-link" href="${href}">${row.sector ? `See the ${inSector} ${esc(row.sector)} ${inSector === 1 ? "company" : "companies"} that pass` : "Open the screener"}${icon("arrow-right")}</a></p>
    <div class="frame r-ledger">${body}</div>
    ${own}
    <p class="r-fine">A high score means a company clears the rules by a wide margin. It is not a forecast and doesn't mean the stock will do well.</p>
  </section>`;
}

// ---- Revenue by quarter ----

// Revenue by quarter (fundamentals.quarters: up to 12 quarters, oldest first, back to back; a quarter the filings don't
// give soundly has null figures) with gross profit and gross margin for the fiscal year and the last 12 months. Reports
// written before these fields existed have no "quarters" key and get no section. Gross figures show only where the
// health check has a gross margin bar: banks, insurers, other financial companies and REITs have none.
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthYear = (iso) => (iso ? `${MON[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}` : "");
const shortMonth = (iso) => `${MON[+iso.slice(5, 7) - 1]} ’${iso.slice(2, 4)}`;
// Growth on the year-ago quarter: one decimal below 100%, whole percents above, in the columns, the table and the
// sentences under the chart alike.
const gDigits = (g) => (Math.abs(g) >= 1 ? 0 : 1);
const growth = (g) => (g == null ? "" : pct(g, gDigits(g), true));
// Why a quarter with revenue has no growth figure (fundamentals._quarter_series' yoy_withheld), or null where its
// year-ago quarter isn't there.
const WITHHELD = {
  restated: "Not compared with the same quarter a year before: a later filing restated one of the two (for example after a business was sold), so they aren't on the same footing.",
  unchecked: "Not compared with the same quarter a year before: the two figures come from different filings that couldn't be checked against each other.",
  no_base: "Not compared with the same quarter a year before, which had no revenue.",
};

// Round axis steps (1, 2, 2.5 or 5 times a power of ten) giving three or four gridlines above zero, and always at
// least two gridlines, so the plot has a height.
function ticks(lo, hi) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / 3.5;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  const out = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
  if (out.length < 2 || out[out.length - 1] < hi) out.push(+(out[out.length - 1] + step).toPrecision(12));
  return out;
}

function axisLabel(v, top) {
  if (v === 0) return "$0";
  const [d, u] = top >= 1e12 ? [1e12, "T"] : top >= 1e9 ? [1e9, "B"] : top >= 1e6 ? [1e6, "M"] : [1e3, "K"];
  return `${v < 0 ? "-" : ""}$${+(Math.abs(v) / d).toFixed(2)}${u}`;
}

// A quarter's name for its tooltip and the table: its fiscal quarter where the pipeline could number it, and its dates,
// or only the month it ended around where they are estimated (a blank quarter in a gap of two or more).
const quarterName = (q) => q.dates_estimated ? `Quarter ended around ${monthYear(q.end)}`
  : `${q.fiscal_quarter ? `Fiscal Q${q.fiscal_quarter}, ` : "Quarter "}${fullDate(q.start)} to ${fullDate(q.end)}`;

// What "revenue" means for a company whose revenue is read from a lender's income statement or a business development
// company's investment income (fundamentals.revenue_basis; older data has none).
function revenueWords(f) {
  return f.bank || f.revenue_basis === "bank" ? "revenue (interest earned less interest paid, plus fees)"
    : f.revenue_basis === "net_interest" ? "revenue (net interest income: interest earned less interest paid)"
    : f.revenue_basis === "investment_income" ? "revenue (total investment income: the interest, dividends and fees its loans and stakes earn)"
    : "revenue";
}

function quarterSection(r) {
  const f = r.fundamentals;
  if (!f || !Array.isArray(f.quarters)) return "";
  const qs = f.quarters;
  // Nothing to plot unless some quarter had revenue above zero, and nothing is shown where the sales figure looks
  // incomplete (the health check then measures no sales-based figure either).
  const plotted = !f.sales_doubt && qs.some((q) => q.revenue > 0);
  const gbar = (r.health || []).flatMap((g) => g.bars).find((b) => b.key === "gross_margin");
  if (!plotted && !gbar) return "";
  const shown = qs.filter((q) => q.revenue != null);
  const count = plotted ? `${qs.length} quarter${qs.length === 1 ? "" : "s"} to ${monthYear(qs[qs.length - 1].end)}` : "";
  const head = `<div class="sec-head"><h2 id="quarters-h">Revenue by quarter</h2>${count ? `<span class="count">${count}</span>` : ""}</div>
    <p class="sec-lede">${plotted ? `Each bar is one quarter's ${revenueWords(f)}, from the company's SEC filings, with its growth on the same quarter a year before.`
      : "Revenue and gross margin from the company's SEC filings."}</p>`;
  const none = f.sales_doubt ? "Quarter-by-quarter revenue isn't shown, since the sales figure in the company's SEC data looks incomplete: profit came out larger than sales."
    : qs.length ? "Quarter-by-quarter revenue isn't shown, since the company reported no revenue in these quarters."
    : "Quarter-by-quarter figures aren't available for this company. Companies based outside the US often file only yearly reports with the SEC, and a quarter is left out when its figures in the filings don't check out.";
  const chart = plotted && shown.length ? quarterChart(qs, f.quarters_stale ? f : null) : `<p class="r-none">${none}</p>`;
  return `<section class="sec" id="quarters" aria-labelledby="quarters-h">${head}
    <div class="frame q-panel">${chart}${quarterStats(f, plotted, gbar)}${plotted ? quarterTable(qs, !!gbar && qs.some((q) => q.gross_profit != null)) : ""}</div>
  </section>`;
}

// `stale`: the fundamentals, where the newest quarter is old (quarters_stale), else null.
function quarterChart(qs, stale) {
  const vals = qs.filter((q) => q.revenue != null).map((q) => q.revenue);
  const t = ticks(Math.min(0, ...vals), Math.max(...vals));
  const lo = t[0], hi = t[t.length - 1];
  const at = (v) => ((v - lo) / (hi - lo)) * 100; // % up from the plot's bottom
  const base = at(0);
  const n = qs.length;
  const derived = qs.some((q) => q.derived && q.revenue != null);
  const odd = qs.some((q) => q.extra_days && q.yoy != null);
  const restated = qs.some((q) => q.yoy_withheld === "restated");
  const unchecked = qs.some((q) => q.yoy_withheld === "unchecked");
  // The summary names the first and last quarters that have a figure, and says how many have none.
  const withRev = qs.filter((q) => q.revenue != null);
  const first = withRev[0], last = withRev[withRev.length - 1];
  const blanks = n - withRev.length;
  const summary = `${n === 1 ? `Revenue in the quarter ended ${monthYear(last.end)}: ${amount(last.revenue)}`
    : `Revenue for the ${n} quarters to ${monthYear(qs[n - 1].end)}${withRev.length > 1
    ? `, from ${amount(first.revenue)} in the quarter ended ${monthYear(first.end)} to ${amount(last.revenue)} in the quarter ended ${monthYear(last.end)}${last.yoy != null ? `, ${asShown(last.yoy, gDigits(last.yoy)) === 0 ? "about the same as" : `${last.yoy > 0 ? "up" : "down"} ${pct(Math.abs(last.yoy), gDigits(last.yoy))} on`} a year before` : ""}`
    : `: ${amount(last.revenue)} in the quarter ended ${monthYear(last.end)}`}`}.${blanks ? ` ${blanks} ${blanks === 1 ? "quarter has" : "quarters have"} no figure.` : ""} The table below lists every quarter.`;
  // The newest quarter the site has can be older than the company's latest filings, whose year end or balance sheet
  // the notes give.
  const newest = qs[n - 1].end;
  const later = stale && [stale.fiscal_year_end, stale.balance_as_of].filter((d) => d && d.slice(0, 7) > newest.slice(0, 7)).sort().pop();
  const cols = qs.map((q, i) => {
    const name = quarterName(q);
    const align = i < 2 ? " left" : i >= n - 2 ? " right" : "";
    const label = `<span class="qc-x"><b>${q.fiscal_quarter ? `Q${q.fiscal_quarter}` : ""}</b>${shortMonth(q.end)}</span>`;
    if (q.revenue == null) {
      const why = "The company's SEC filings don't give this quarter's revenue, or it was left out because its figures didn't check out.";
      return `<div class="qc-col blank">
        <div class="qc-area"><span class="qc-na">n/a</span></div>${label}
        <span class="qc-val">n/a</span><span class="qc-yoy"></span>
        <span class="qc-tip${align}"><b>Not available</b><span>${esc(name)}</span><span>${why}</span></span></div>`;
    }
    const v = q.revenue;
    const bar = `--base:${base.toFixed(2)}%;--h:${Math.abs(at(v) - base).toFixed(2)}%`;
    const gm = q.gross_profit != null && v > 0 ? q.gross_profit / v : null;
    // A change that reads 0% has no direction to name.
    const yoyText = q.yoy == null ? WITHHELD[q.yoy_withheld] || "No growth figure, since the same quarter a year before isn't available."
      : asShown(q.yoy, gDigits(q.yoy)) === 0 ? "About the same as the same quarter a year before."
      : `${q.yoy > 0 ? "Up" : "Down"} ${pct(Math.abs(q.yoy), gDigits(q.yoy))} on the same quarter a year before.`;
    const extra = q.extra_days && q.yoy != null ? ` It ran ${Math.abs(q.extra_days)} days ${q.extra_days > 0 ? "longer" : "shorter"} than that quarter, since the company's fiscal year sometimes has an extra week, which moves growth by several points.` : "";
    const tip = `<span class="qc-tip${align}"><b>${amount(v)}</b><span>${esc(name)}</span>
      <span>${yoyText}${extra}</span>${gm != null ? `<span>Gross margin ${pct(gm, 1)}.</span>` : ""}${q.derived ? `<span>Worked out as the fiscal year's total less its first three quarters.</span>` : ""}</span>`;
    return `<div class="qc-col${q.derived ? " derived" : ""}">
      <div class="qc-area"><div class="qc-bar${v < 0 ? " neg" : ""}" style="${bar}"></div></div>${label}
      <span class="qc-val">${amount(v)}</span>
      <span class="qc-yoy ${signClass(asShown(q.yoy, gDigits(q.yoy)))}${extra ? " adj" : ""}">${growth(q.yoy)}</span>${tip}</div>`;
  }).join("");
  const grid = t.map((v) => `<span class="qc-grid${v === 0 ? " zero" : ""}" style="bottom:${at(v)}%"></span>`).join("");
  const axis = t.map((v) => `<span style="bottom:${at(v)}%">${axisLabel(v, Math.max(Math.abs(lo), hi))}</span>`).join("");
  const keys = [derived && `<li><span class="qk derived" aria-hidden="true"></span>Fourth quarter worked out as the fiscal year's total less its first three quarters, since companies rarely report it on its own</li>`,
    odd && `<li><span class="qk adj" aria-hidden="true"></span>Growth over a quarter of a different length, where a fiscal year had an extra week</li>`,
    restated && `<li><span class="qk none" aria-hidden="true"></span>No growth shown where a later filing restated one of the two quarters (for example after a business was sold), so they aren't on the same footing</li>`,
    unchecked && `<li><span class="qk none" aria-hidden="true"></span>No growth shown where the two quarters' figures come from filings that couldn't be checked against each other</li>`].filter(Boolean);
  // The columns are a picture of the table below, which holds the same figures for keyboards and screen readers; the
  // hover notes are for a mouse.
  return `<figure class="qc-fig">
      <div class="qc" style="--n:${n}" aria-hidden="true">
        <div class="qc-axis">${axis}</div>
        <div class="qc-plot"><div class="qc-grids">${grid}</div><div class="qc-cols">${cols}</div></div>
      </div>
      <figcaption class="qc-cap">${esc(summary)}</figcaption>
    </figure>
    ${stale ? `<p class="qc-stale">${icon("warning")}<span>The newest quarter whose revenue this site could read ended ${monthYear(newest)}, so these figures are not current.${later ? ` The company's SEC filings run to at least ${monthYear(later)}.` : ""}</span></p>` : ""}
    <p class="qc-note">Quarters are the company's own fiscal quarters, labeled with the month each ended.</p>
    ${keys.length ? `<ul class="qc-keys">${keys.join("")}</ul>` : ""}`;
}

// Revenue over the last 12 months, and gross profit and gross margin for the fiscal year and the last 12 months.
function quarterStats(f, plotted, gbar) {
  const tiles = [];
  const tile = (k, v, s) => `<div><dt>${k}</dt><dd>${v}</dd>${s ? `<dd class="s">${s}</dd>` : ""}</div>`;
  const fyEnd = f.fiscal_year_end;
  const newer = f.ttm_end && (!fyEnd || f.ttm_end > fyEnd);
  if (plotted && f.ttm_revenue != null) {
    // Against the four quarters a year before, which the pipeline gives only where each quarter's growth is known, so
    // the two totals are on one footing.
    const g = f.ttm_prior_revenue > 0 ? f.ttm_revenue / f.ttm_prior_revenue - 1 : null;
    tiles.push(tile("Revenue, last 12 months", amount(f.ttm_revenue),
      `12 months to ${monthYear(f.ttm_end)}${g != null ? `, ${g >= 0 ? "up" : "down"} ${pct(Math.abs(g), 1)} on the 12 months before` : ""}`));
  } else if (plotted && f.revenue != null) {
    tiles.push(tile("Revenue", amount(f.revenue), fyEnd ? `Fiscal year to ${monthYear(fyEnd)}` : "Latest fiscal year"));
  }
  let fine = "";
  if (gbar) {
    const fy = fyEnd ? `Fiscal year to ${monthYear(fyEnd)}` : "Latest fiscal year";
    // From the dollar figures where both are there, since the margins are stored rounded to four places and would
    // otherwise round differently from the health check's (48.65% there, 48.6% here).
    const ratio = (gp, rev, m) => (gp != null && rev > 0 ? gp / rev : m);
    const gm = f.sales_doubt ? null : ratio(f.gross_profit, f.revenue, f.gross_margin);
    const tm = ratio(f.ttm_gross_profit, f.ttm_revenue, f.ttm_gross_margin);
    if (gm != null) {
      tiles.push(tile("Gross margin", pct(gm, 1), `${fy}, gross profit of ${amount(f.gross_profit)}`));
      if (newer && tm != null) {
        const d = tm - gm;
        tiles.push(tile("Gross margin, last 12 months", pct(tm, 1),
          `12 months to ${monthYear(f.ttm_end)}, gross profit of ${amount(f.ttm_gross_profit)}${Math.abs(d) >= 0.001 ? `, ${Math.abs(d * 100).toFixed(1)} points ${d > 0 ? "above" : "below"} the fiscal year` : ""}`));
      }
      fine = `Gross margin is the share of sales left after the direct cost of making or buying what was sold, before spending on research, marketing and running the company.${f.gross_basis && f.gross_basis !== "GrossProfit" ? " The company's filings show no gross profit line, so it is worked out as sales less cost of sales." : ""}`;
    } else {
      tiles.push(tile("Gross margin", "Not measured", ""));
      fine = esc((gbar.note || "").replace(/^Not measured\.\s*/, ""));
    }
  }
  if (!tiles.length) return "";
  return `<dl class="q-stats">${tiles.join("")}</dl>${fine ? `<p class="q-fine">${fine}</p>` : ""}`;
}

// The same figures as a table: the chart's text alternative, and the only place quarterly gross profit is listed.
function quarterTable(qs, gross) {
  const rows = [...qs].reverse().map((q) => {
    const gm = q.gross_profit != null && q.revenue > 0 ? q.gross_profit / q.revenue : null;
    const when = q.dates_estimated ? `ended around ${esc(monthYear(q.end))}` : `ended ${esc(fullDate(q.end))}`;
    return `<tr><th scope="row">${q.fiscal_quarter ? `Q${q.fiscal_quarter}, ` : ""}${when}${q.derived && q.revenue != null ? ` <span class="tag">Worked out</span>` : ""}</th>
      <td>${q.revenue == null ? `<span class="faint">n/a</span>` : amount(q.revenue)}</td>
      <td class="${signClass(asShown(q.yoy, gDigits(q.yoy)))}">${q.yoy != null ? growth(q.yoy) : q.revenue != null && q.yoy_withheld ? `<span class="faint">Not compared</span>` : ""}${q.extra_days && q.yoy != null ? ` <span class="faint">(${q.extra_days > 0 ? "+" : "-"}${Math.abs(q.extra_days)} days)</span>` : ""}</td>
      ${gross ? `<td>${q.gross_profit == null ? `<span class="faint">n/a</span>` : amount(q.gross_profit)}</td><td>${gm == null ? `<span class="faint">n/a</span>` : pct(gm, 1)}</td>` : ""}</tr>`;
  }).join("");
  return `<details class="disclose q-table">
    <summary>${icon("list-checks", "lead-i")}Every quarter as a table${icon("caret-down", "caret")}</summary>
    <div class="body"><div class="table-wrap scrolls"><table class="data r-table">
      <caption class="sr-only">Revenue by fiscal quarter, newest first</caption>
      <thead><tr><th scope="col">Fiscal quarter</th><th scope="col">Revenue</th><th scope="col">Growth on a year before</th>${gross ? `<th scope="col">Gross profit</th><th scope="col">Gross margin</th>` : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="q-fine">Newest first. A quarter is n/a where the company's SEC filings don't give its revenue, or it was left out because its figures didn't check out. Growth is not compared where the quarter a year before had no revenue, or a later filing restated one of the two, so they aren't on the same footing.</p></div>
  </details>`;
}

// ---- Financial history: each fiscal year's figures (fundamentals.history), oldest first, on one zero line. ----

function historySection(r, home) {
  const f = r.fundamentals;
  const hist = (f?.history || []).filter((h) => h && h.year != null).sort((a, b) => a.year - b.year);
  if (!hist.length) return "";
  // One column for every year from the first to the last, so a year the data lacks shows as n/a, not as no gap.
  const byYear = new Map(hist.map((h) => [h.year, h]));
  const cols = [];
  for (let y = hist[0].year; y <= hist[hist.length - 1].year; y++) cols.push(byYear.get(y) || { year: y });
  const gaps = cols.filter((c) => !byYear.has(c.year)).map((c) => String(c.year));
  const kind = valuationKind(r);
  const has = (k) => hist.some((h) => h[k] != null);
  const ffoDiffers = has("adj_ffo") && hist.some((h) => h.adj_ffo != null && h.adj_ffo !== h.ffo);
  const ROWS = [
    ["revenue", "Revenue", true],
    ["gross_profit", "Gross profit", false],
    ["net_income", "Net income", true],
    ["fcf", "Free cash flow", true],
    ["ffo", "FFO", false],
    ["adj_ffo", "FFO without one-time items", false],
  ];
  const rows = ROWS.filter(([k]) => has(k) && (k !== "adj_ffo" || ffoDiffers));
  const missing = ROWS.filter(([k, , core]) => core && !has(k)).map(([, label]) => label.toLowerCase());
  if (!rows.length) return "";
  // Each row on its own scale from zero, so a bar compares a figure across the years (net income's bars aren't
  // flattened by revenue's), with negatives below the row's zero line.
  const scale = (k) => {
    const vals = hist.map((h) => h[k]).filter((v) => v != null);
    const top = Math.max(0, ...vals), bottom = Math.min(0, ...vals);
    const span = top - bottom || 1;
    return { span, zero: (top / span) * 100 };
  };
  const cell = (v, { span, zero }) => {
    if (v == null) return `<td class="fh-na"><span class="faint">n/a</span></td>`;
    const h = Math.max((Math.abs(v) / span) * 100, v ? 1.5 : 0);
    const where = v >= 0 ? `bottom:${(100 - zero).toFixed(2)}%` : `top:${zero.toFixed(2)}%`;
    return `<td><span class="fh-v${v < 0 ? " down" : ""}">${money(v)}</span><span class="fh-bar" aria-hidden="true"><span class="fh-zero" style="top:${zero.toFixed(2)}%"></span><span class="fh-fill${v < 0 ? " neg" : ""}" style="${where};height:${h.toFixed(2)}%"></span></span></td>`;
  };
  // The claim the table backs: revenue from the first year to the last, and the years with a profit.
  const revs = hist.filter((h) => h.revenue != null);
  const a = revs[0], b = revs[revs.length - 1];
  const ni = hist.filter((h) => h.net_income != null);
  const pos = ni.filter((h) => h.net_income > 0).length;
  const claims = [];
  if (a && b && a !== b) {
    // Worded from the figures as shown, which cut digits, so two that read the same are never said to rise or fall.
    const ma = money(a.revenue), mb = money(b.revenue);
    claims.push(ma === mb ? `Revenue was ${b.revenue === a.revenue ? "" : "about "}${ma} in both fiscal ${a.year} and fiscal ${b.year}.`
      : `Revenue ${b.revenue > a.revenue ? "rose" : "fell"} from ${ma} in fiscal ${a.year} to ${mb} in fiscal ${b.year}.`);
  }
  // Where some columns have no net income, the count says how many years it covers.
  const withNi = ni.length < cols.length ? `${ni.length} years with a figure` : null;
  if (ni.length > 1 && withNi) {
    claims.push(`Net income was positive in ${pos === ni.length ? `all ${withNi}` : `${pos === 0 ? "none" : pos} of the ${withNi}`}.`);
  } else if (ni.length > 1) {
    claims.push(`Net income was positive in ${pos === ni.length ? `all ${ni.length}` : pos === 0 ? "none" : `${pos}`} of ${pos === ni.length || pos === 0 ? "these years" : `these ${ni.length} years`}.`);
  } else if (ni.length === 1) {
    claims.push(`Net income was ${ni[0].net_income > 0 ? "positive" : ni[0].net_income < 0 ? "negative" : "zero"} in fiscal ${ni[0].year}.`);
  }
  // A year is labeled by the calendar year the SEC's data files it under (fundamentals._years), which for a year that
  // doesn't end in December can be a year before the company's own name for it: Strattec's year to June 2026 is 2025.
  const lastYear = hist[hist.length - 1].year;
  // A 52 or 53-week year that ends in the first days of January is named for the year before, as its label is.
  const ended = f.fiscal_year === lastYear && f.fiscal_year_end && +f.fiscal_year_end.slice(0, 4) > lastYear
    && f.fiscal_year_end.slice(5) > "01-07" ? f.fiscal_year_end : null;
  const notes = [
    `Fiscal years from the company's SEC filings, oldest first.${ended ? ` Each is labeled by the calendar year the SEC's data files it under, which can be a year before the company's own name for it: the year labeled ${lastYear} ended ${fullDate(ended)}.` : ""}`,
    // The SEC's yearly data files each year under one calendar year, and can leave a label with none for the company
    // (a 52 or 53-week year ending near June 30), though its quarters may still show that year above.
    gaps.length ? `The SEC's yearly data files no year of this company's under ${andList(gaps)}, so ${gaps.length > 1 ? "those columns are" : "that column is"} n/a.` : "",
    revenueWords(f) !== "revenue" ? `Revenue here is its ${revenueWords(f).replace(/^revenue \(|\)$/g, "")}.` : "",
    has("net_income") ? "Net income is as reported, one-time items included." : "",
    has("fcf") ? `Free cash flow is cash from operations less capital spending.${kind === "bank" || kind === "financial" ? " It isn't scored for banks, insurers and other financial companies, whose cash and borrowing are part of the business itself." : ""}` : "",
    has("ffo") ? `FFO (funds from operations) is net income with property depreciation added back and property sales left out${ffoDiffers ? "; the row under it also leaves out other one-time items" : ""}.` : "",
    missing.length ? `No ${andList(missing)} figures for these years.` : "",
  ].filter(Boolean);
  const yearsLabel = hist.length > 1 ? `Fiscal ${hist[0].year} to ${hist[hist.length - 1].year}` : `Fiscal ${hist[0].year}`;
  // The figures as small charts, by fiscal year and, where the filings give quarters, by quarter; the table under them
  // holds every figure.
  const years = yearCharts(f, cols, has);
  const quarters = quarterCharts(r);
  const toggle = years.length && quarters.length ? `<div class="seg fc-toggle" role="group" aria-label="Charts by">
      <button class="seg-btn" type="button" data-fc="year" aria-pressed="true">By fiscal year</button>
      <button class="seg-btn" type="button" data-fc="quarter" aria-pressed="false">By quarter</button></div>` : "";
  const grids = `${years.length ? `<div class="fc-grid" data-fc-grid="year">${years.join("")}</div>` : ""}
    ${quarters.length ? `<div class="fc-grid" data-fc-grid="quarter"${years.length ? " hidden" : ""}>${quarters.join("")}
      <p class="fc-more">${(f.quarters || []).some((q) => q.derived && q.revenue != null) ? "A hollow bar is a fourth quarter worked out as the fiscal year's total less its first three quarters, since companies rarely report it on its own. " : ""}Net income and free cash flow are charted by fiscal year only, the way this site reads them from the filings.</p></div>` : ""}`;
  return `<section class="sec" id="history" aria-labelledby="history-h">
    <div class="sec-head"><h2 id="history-h">Financials</h2><span class="count">${yearsLabel}</span>${toggle}</div>
    ${claims.length ? `<p class="sec-lede">${claims.join(" ")}</p>` : ""}
    ${grids}
    <ul class="r-fine-list">${notes.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    ${home === "history" ? noteBox(f.one_time_note) : ""}
    <details class="disclose fh-details"${years.length ? "" : " open"}>
      <summary>${icon("list-checks", "lead-i")}Every figure by fiscal year, as a table${icon("caret-down", "caret")}</summary>
      <div class="table-wrap fh-wrap scrolls"><table class="data r-table fh">
        <caption class="sr-only">Yearly figures by fiscal year, oldest first</caption>
        <thead><tr><th scope="col"><span class="sr-only">Figure</span></th>${cols.map((h) => `<th scope="col">${esc(String(h.year))}</th>`).join("")}</tr></thead>
        <tbody>${rows.map(([k, label]) => `<tr><th scope="row">${label}</th>${(() => { const s = scale(k); return cols.map((h) => cell(h[k], s)).join(""); })()}</tr>`).join("")}</tbody>
      </table></div>
    </details>
  </section>`;
}

// ---- Small charts: one figure across fiscal years or quarters ----

// A card with one figure's bars from zero (red below it), its latest value and the change on the one before, an axis of
// round steps and a label under each bar. `pts`: [{ x, x2, v, name, hollow }], oldest first, v null where there is no
// figure. `share`: the figure is a share of sales (a margin), so its change is in percentage points. The bars are a
// picture of the table under the cards, which holds the same figures for keyboards and screen readers.
function chartCard({ title, period, pts, share = false }) {
  const withV = pts.filter((p) => p.v != null);
  if (withV.length < 2) return "";
  const t = ticks(Math.min(0, ...withV.map((p) => p.v)), Math.max(0, ...withV.map((p) => p.v)));
  const lo = t[0], hi = t[t.length - 1];
  const at = (v) => ((v - lo) / (hi - lo)) * 100;
  const base = at(0);
  const fmt = (v) => (share ? pct(v, 1) : money(v));
  const axis = (v) => (share ? `${+(v * 100).toFixed(1)}%` : axisLabel(v, Math.max(Math.abs(lo), hi)));
  // The latest figure against the one before it: growth where the one before was above zero, points for a margin.
  const last = withV[withV.length - 1], prev = withV[withV.length - 2];
  let change = "";
  if (share) {
    const d = Math.round(last.v * 1000) / 10 - Math.round(prev.v * 1000) / 10;
    change = `<span class="${signClass(d)}">${d > 0 ? "+" : ""}${d.toFixed(1)} pts</span> vs ${esc(prev.short)}`;
  } else if (prev.v > 0) {
    const g = last.v / prev.v - 1;
    change = `<span class="${signClass(asShown(g, 1))}">${pct(g, 1, true)}</span> vs ${esc(prev.short)}`;
  }
  const cols = pts.map((p) => {
    const label = `<span class="fc-x">${esc(p.x)}${p.x2 ? `<small>${esc(p.x2)}</small>` : ""}</span>`;
    if (p.v == null) return `<div class="fc-col blank" title="${esc(`${p.name}: no figure`)}"><div class="fc-area"><span class="fc-na">n/a</span></div>${label}</div>`;
    const bar = `--base:${base.toFixed(2)}%;--h:${Math.abs(at(p.v) - base).toFixed(2)}%`;
    return `<div class="fc-col${p.hollow ? " hollow" : ""}" title="${esc(`${p.name}: ${fmt(p.v)}`)}"><div class="fc-area"><div class="fc-bar${p.v < 0 ? " neg" : ""}" style="${bar}"></div></div>${label}</div>`;
  }).join("");
  const grid = t.map((v) => `<span class="fc-line${v === 0 ? " zero" : ""}" style="bottom:${at(v)}%"></span>`).join("");
  const ax = t.map((v) => `<span style="bottom:${at(v)}%">${axis(v)}</span>`).join("");
  const said = `${title}, ${period.toLowerCase()}: ${withV.map((p) => `${p.name} ${fmt(p.v)}`).join(", ")}`;
  return `<figure class="fc">
    <figcaption class="fc-head"><span class="fc-t">${esc(title)}</span><span class="fc-p">${esc(period)}</span></figcaption>
    <p class="fc-now"><b class="${last.v < 0 ? "down" : ""}">${fmt(last.v)}</b><span class="fc-of">${esc(last.short)}</span>${change ? `<span class="fc-chg">${change}</span>` : ""}</p>
    <div class="fc-chart" role="img" aria-label="${esc(said)}">
      <div class="fc-axis" aria-hidden="true">${ax}</div>
      <div class="fc-plot" aria-hidden="true"><div class="fc-grids">${grid}</div><div class="fc-cols" style="--n:${pts.length}">${cols}</div></div>
    </div>
  </figure>`;
}

// The cards for each fiscal year, from the same figures as the table: revenue, gross profit, net income, free cash flow,
// FFO for a REIT, and gross and net margin worked out from them.
function yearCharts(f, cols, has) {
  const pt = (c, v) => ({ x: String(c.year), v: v ?? null, name: `Fiscal ${c.year}`, short: `fiscal ${c.year}` });
  const share = (a, b) => (a != null && b != null && b > 0 ? a / b : null);
  const cards = [
    chartCard({ title: "Revenue", period: "By fiscal year", pts: cols.map((c) => pt(c, c.revenue)) }),
    has("gross_profit") ? chartCard({ title: "Gross profit", period: "By fiscal year", pts: cols.map((c) => pt(c, c.gross_profit)) }) : "",
    chartCard({ title: "Net income", period: "By fiscal year, as reported", pts: cols.map((c) => pt(c, c.net_income)) }),
    chartCard({ title: "Free cash flow", period: "By fiscal year", pts: cols.map((c) => pt(c, c.fcf)) }),
    has("ffo") ? chartCard({ title: "FFO", period: "By fiscal year", pts: cols.map((c) => pt(c, c.ffo)) }) : "",
    has("gross_profit") ? chartCard({ title: "Gross margin", period: "By fiscal year", share: true, pts: cols.map((c) => pt(c, share(c.gross_profit, c.revenue))) }) : "",
    chartCard({ title: "Net margin", period: "By fiscal year, as reported", share: true, pts: cols.map((c) => pt(c, share(c.net_income, c.revenue))) }),
  ].filter(Boolean);
  return cards;
}

// The cards for each quarter: revenue, and gross profit and margin where the health check measures a gross margin (not
// for banks, insurers, other financial companies and REITs). A fourth quarter worked out from the year's total less its
// first three is drawn hollow, as in "Revenue by quarter".
function quarterCharts(r) {
  const f = r.fundamentals;
  const qs = f && Array.isArray(f.quarters) ? f.quarters : [];
  if (f?.sales_doubt || !qs.some((q) => q.revenue > 0)) return [];
  const gross = (r.health || []).flatMap((g) => g.bars).some((b) => b.key === "gross_margin") && qs.some((q) => q.gross_profit != null);
  // Each bar is labeled with its fiscal quarter, and the calendar year under the first quarter that ends in a new one.
  const series = (fn) => qs.map((q, i) => ({
    x: q.fiscal_quarter ? `Q${q.fiscal_quarter}` : "", x2: i === 0 || q.end.slice(0, 4) !== qs[i - 1].end.slice(0, 4) ? q.end.slice(0, 4) : "",
    v: fn(q) ?? null, name: quarterName(q), short: `${monthYear(q.end)} quarter`, hollow: q.derived,
  }));
  return [
    chartCard({ title: "Revenue", period: "By quarter", pts: series((q) => q.revenue) }),
    gross ? chartCard({ title: "Gross profit", period: "By quarter", pts: series((q) => q.gross_profit) }) : "",
    gross ? chartCard({ title: "Gross margin", period: "By quarter", share: true, pts: series((q) => (q.gross_profit != null && q.revenue > 0 ? q.gross_profit / q.revenue : null)) }) : "",
  ].filter(Boolean);
}

// ---- Health check ----

// A bar with no score says why in the bar's place: the first sentence of its note where the note gives the reason
// ("Not scored for banks...", "Not measured.", "Not enough history."), "Not scored" where it has a figure but nothing to
// rank it against, "Not enough data" otherwise. The rest of the note stays under it.
function nullReason(b) {
  const note = b.note || "";
  const m = /^((?:Not |Can't |No revenue)[^.]*)\.\s*([\s\S]*)$/.exec(note);
  if (m) return [m[1], m[2]];
  return [b.value && b.value !== "n/a" ? "Not scored" : "Not enough data", note];
}

// The insider bar's amount is written from the total the insider section states, cut like money() there, since the
// pipeline's own text rounds to the nearest $0.1M and can read larger ($1.0M for $999K).
function healthBar(b, r) {
  const none = b.score == null;
  const [lead, rest] = none ? nullReason(b) : [null, b.note];
  const value = b.key === "insiders" && r?.insiders?.total_value != null ? money(r.insiders.total_value) : b.value;
  return `<li class="hb${none ? " is-none" : ""}">
    <div class="hb-top"><span class="hb-l">${esc(b.label)}</span><span class="hb-v">${esc(value)}</span></div>
    ${meter(b.score, b.band, lead || "Not enough data", b.label)}
    ${rest ? `<p class="hb-n">${esc(rest)}</p>` : ""}
  </li>`;
}

function healthSection(r, home) {
  if (!r.health) {
    // Where the pipeline could tell why from the company's SEC filings (coverage), the fair value panel above says it.
    const why = r.coverage?.note && !r.valuation ? "Not scored, for the reason given above." : `We could not find a full year of revenue and profit in this company's SEC filings, so it cannot be scored.
      This is common for companies with no sales yet, funds, SPACs and foreign filers, foreign banks among them.`;
    return `<section class="sec" id="health" aria-labelledby="health-h"><div class="sec-head"><h2 id="health-h">Health check</h2></div><p class="sec-lede">${why}</p></section>`;
  }
  const s = r.health_score;
  const band = healthBand(s);
  const word = { strong: ["Healthy", "tag-up"], fair: ["Mixed", "tag-mid"], weak: ["Weak", "tag-down"] }[band];
  const context = r.health.filter((g) => g.bars.length && g.bars.every((b) => b.context));
  const ctxNames = context.map((g) => g.name);
  const scored = r.health.flatMap((g) => g.bars).filter((b) => !b.context && b.score != null).length;
  const leftOut = ctxNames.length ? `, leaving out ${andList(ctxNames.map((n) => n.toLowerCase()))}` : "";
  const higher = ctxNames.length
    ? `In every group but ${andList(ctxNames.map((n) => n.toLowerCase()))}, a higher score is healthier. ${andList(ctxNames)} ${ctxNames.length === 1 ? "is" : "are"} shown for context and not counted, and each of ${ctxNames.length === 1 ? "its" : "their"} bars says what its score means.`
    : "A higher score is healthier.";
  // The group by the profit bars holds the one-time items note when it is shown here.
  const noteGroup = home === "health" ? (r.health.find((g) => g.bars.some((b) => ["net_margin", "ffo_margin", "roa"].includes(b.key))) || r.health[0]) : null;
  return `<section class="sec" id="health" aria-labelledby="health-h">
    <div class="sec-head"><h2 id="health-h">Health check</h2></div>
    <div class="h-sum">
      <p class="h-score"><b>${s ?? "n/a"}</b><span class="h-of">of 100</span>${word ? `<span class="tag ${word[1]}">${word[0]}</span>` : ""}</p>
      <p class="h-intro">${s != null ? `The average of ${scored} scored measure${scored === 1 ? "" : "s"}${leftOut}. ` : ""}Each measure is scored from 1 to 100 where its figures allow. ${higher} Raw amounts like cash or share count only mean something next to price or history, so each is scored through the ratio that gives it meaning.</p>
    </div>
    <div class="frame h-grid">${r.health.map((g, i) => {
      const ctx = context.includes(g);
      return `<section class="hg${ctx ? " is-context" : ""}" aria-labelledby="hg-${i}">
        <div class="hg-head"><h3 id="hg-${i}">${esc(g.name)}</h3>${ctx ? `<span class="tag tag-line">Not counted</span>` : ""}</div>
        <p class="hg-hint">${esc(g.hint)}</p>
        ${g === noteGroup ? noteBox(r.fundamentals.one_time_note) : ""}
        <ul class="hb-list">${g.bars.map((b) => healthBar(b, r)).join("")}</ul>
      </section>`;
    }).join("")}</div>
  </section>`;
}

// ---- Similar companies ----

// A valuation multiple, or n/a where it doesn't apply (the note under the table says why). One under 0.1x gets two
// decimals, and one under 0.01x says so, so a cheap company never reads 0.0x.
const mult = (x) => (x == null ? "n/a" : x > 0 && x < 0.01 ? "under 0.01x"
  : `${x >= 100 ? Math.round(x).toLocaleString("en-US") : x.toFixed(x > 0 && x < 0.1 ? 2 : 1)}x`);

// Reports written before peers were chosen by their numbers carry only peer_group and peer_count, which the
// notes at the bottom still describe.
function peersSection(r) {
  if (!r.peer_basis) return "";
  const list = r.peers || [];
  const pm = r.peer_multiples;
  const head = `<div class="sec-head"><h2 id="peers-h">Similar companies</h2>${list.length ? `<span class="count">${list.length} companies</span>` : ""}</div>
    <p class="sec-lede">${esc(r.peer_basis)}</p>`;
  if (!list.length || !pm) return `<section class="sec" id="peers" aria-labelledby="peers-h">${head}</section>`;
  // Non-financial companies also get price to sales, the measure their health check ranks. Banks get price to tangible
  // book (ptbv) in place of a sales multiple and have no sales_label, and so do companies valued as lenders on price to
  // book (pb) or on earnings alone; every other report has sales_label.
  const cols = [pm.ps_label && ["ps", pm.ps_label], pm.ptbv_label && ["ptbv", pm.ptbv_label],
    pm.pb_label && ["pb", pm.pb_label], pm.sales_label && ["sales", pm.sales_label], ["pe", pm.pe_label]].filter(Boolean);
  // Price to book sits near 1x for lenders, so it gets two decimals.
  const cell = (k, v) => `<td${v == null ? ` class="faint"` : ""}>${k === "pb" && v != null && v < 10 ? `${v.toFixed(2)}x` : mult(v)}</td>`;
  const figures = (x) => cols.map(([k]) => cell(k, x?.[k])).join("");
  return `<section class="sec" id="peers" aria-labelledby="peers-h">${head}
    <div class="table-wrap scrolls"><table class="data r-table peers">
      <caption class="sr-only">${esc(r.symbol)} beside its ${list.length} similar companies, with the median of each multiple</caption>
      <thead><tr><th scope="col">Company</th>${cols.map(([, label]) => `<th scope="col">${esc(label)}</th>`).join("")}</tr></thead>
      <tbody>
        <tr class="self"><th scope="row"><span class="co"><span class="sym">${esc(r.symbol)}</span> <span class="nm">This company</span></span></th>${figures(pm.company)}</tr>
        ${list.map((p) => `<tr><th scope="row"><a class="co peer-link" href="?t=${encodeURIComponent(p.symbol)}" data-sym="${esc(p.symbol)}"><span class="sym">${esc(p.symbol)}</span> <span class="nm">${esc(displayName(p.name))}</span></a></th>${figures(p)}</tr>`).join("")}
      </tbody>
      <tfoot><tr><th scope="row"><span class="co"><span class="sym-plain">Median</span><span class="nm">of the ${list.length} companies above</span></span></th>${figures(pm.median)}</tr></tfoot>
    </table></div>
    ${pm.note ? `<p class="r-fine">${esc(pm.note)}</p>` : ""}
  </section>`;
}

// ---- Insider buying ----

// Roles as the insiders page shortens them.
function role(roles) {
  if (!roles?.length) return "Insider";
  return roles.map((x) => x.replace(/Chief Executive Officer/i, "CEO").replace(/Chief Financial Officer/i, "CFO")
    .replace(/Chief Operating Officer/i, "COO")).join(", ");
}
// A share count in a few figures: 5.36M, 12.4K, 950.
function shares(n) {
  if (n == null) return "";
  const a = Math.abs(n);
  const cut = (v, d) => (Math.floor(v * 10 ** d + 1e-9) / 10 ** d).toFixed(d);
  return a >= 1e9 ? `${cut(a / 1e9, 2)}B` : a >= 1e6 ? `${cut(a / 1e6, 2)}M` : a >= 1e4 ? `${cut(a / 1e3, 1)}K` : Math.round(a).toLocaleString("en-US");
}

// Two share counts as a pair: shortened, or exact where shortening would make them read the same ("2.82M to 2.82M").
const sharePair = (a, b) => (shares(a) === shares(b) ? [a, b].map((n) => Math.round(Math.abs(n)).toLocaleString("en-US")) : [shares(a), shares(b)]);

// Whether the shares bought account for the whole change from the holding before to the one the latest filing reports.
// They need not: that filing can also count shares gained or given up otherwise (converted at a listing, granted,
// sold), which the stake change, the shares bought over the holding before, leaves out.
const adds = (b) => b.owned_before != null && b.owned_after != null && b.shares != null
  && Math.abs(b.owned_after - b.owned_before - b.shares) <= Math.max(1, 0.01 * b.shares);

// Under the change, the holding before and after where the purchases add up to the difference, else the two side by side.
function stakeCell(b) {
  const held = b.new_position || !b.owned_before ? (b.owned_after != null ? `<small>${shares(b.owned_after)} shares</small>` : "")
    : adds(b) ? `<small>${sharePair(b.owned_before, b.owned_after).join(" to ")} shares</small>`
    : b.owned_after == null ? `<small>${shares(b.owned_before)} shares before</small>`
    : `<small>${shares(b.owned_before)} shares before, ${shares(b.owned_after)} in latest filing</small>`;
  const td = `<td data-label="Stake change">`;
  if (b.new_position) return `${td}New position${held}</td>`;
  if (b.stake_increase == null) return `${td}<span class="faint">n/a</span>${held}</td>`;
  return `${td}${pct(b.stake_increase, b.stake_increase < 0.1 ? 1 : 0, true)}${held}</td>`;
}

const TIERS = {
  1: ["Tier 1", "tag-solid", (d) => `Tier 1 means $10 million or more bought on the open market in the last ${d} days.`],
  2: ["Tier 2", "", () => "Tier 2 means $1 million or more, or a single buyer growing their stake by 20% or more."],
  3: ["Smaller", "tag-line", () => "Smaller means less than Tier 2: under $1 million, with no single buyer growing their stake by 20% or more."],
};

function insiderSection(r) {
  const c = r.insiders;
  if (!c) return "";
  const days = r.insider_window || 30;
  const tier = TIERS[c.tier] || TIERS[3];
  // As the insiders page works it out (vsPaid): where the data gives no comparison, the sentence goes.
  const vs = vsPaid(r.price, c.avg_price, c.vs_paid);
  const summary = c.total_value != null
    ? ` Insiders bought ${money(c.total_value)} of its stock${c.avg_price ? `, paying ${price(c.avg_price)} a share on average` : ""}.${vs != null
      ? ` Today's price of ${price(r.price)} is ${Math.abs(vs) < 0.0005 ? "about the same as that" : `<span class="${signClass(vs)}">${pct(Math.abs(vs), Math.abs(vs) < 0.1 ? 1 : 0)} ${vs < 0 ? "below" : "above"}</span> that`}.` : ""}`
    : "";
  const bought = (b) => (b.first && b.last && b.first !== b.last ? `${day(b.first)} to ${day(b.last)}` : day(b.last || b.first));
  return `<section class="sec" id="insiders" aria-labelledby="insiders-h">
    <div class="sec-head"><h2 id="insiders-h">Insider buying</h2><span class="tag ${tier[1]}">${tier[0]}</span></div>
    <p class="sec-lede">Open-market purchases filed with the SEC in the last ${days} days.${summary}</p>
    <div class="table-wrap scrolls"><table class="data r-table buyers">
      <caption class="sr-only">Insiders who bought ${esc(r.symbol)} stock on the open market in the last ${days} days</caption>
      <thead><tr><th scope="col">Buyer</th><th scope="col">Bought</th><th scope="col">Amount</th><th scope="col">Price paid</th><th scope="col">Stake change</th><th scope="col"><span class="sr-only">SEC filing</span></th></tr></thead>
      <tbody>${c.buyers.map((b) => `<tr>
        <th scope="row"><span class="who"><b>${esc(b.name)}</b><span>${esc(role(b.roles))}</span></span></th>
        <td data-label="Bought">${esc(bought(b))}</td>
        <td data-label="Amount">${money(b.value)}${b.shares != null ? `<small>${shares(b.shares)} shares</small>` : ""}</td>
        <td data-label="Price paid">${price(b.avg_price)}</td>
        ${stakeCell(b)}
        <td class="c-doc">${b.filing ? `<a class="doc" href="${esc(b.filing)}" target="_blank" rel="noopener">Filing${icon("arrow-up-right")}<span class="sr-only"> by ${esc(b.name)} at the SEC (opens in a new tab)</span></a>` : ""}</td>
      </tr>`).join("")}</tbody>
    </table></div>
    <p class="r-fine">${esc(tier[2](days))} Stake change is the shares bought as a percentage of what the buyer held before, counting every account they reported. <a href="insiders.html">See all insider buying</a></p>
  </section>`;
}

// ---- News ----

function newsSection(r) {
  const n = r.news || { items: [], overall: { label: "none", text: "No recent headlines." } };
  const o = n.overall || { label: "none", text: "" };
  const items = n.items || [];
  const toneTag = (label) => (label ? `<span class="tag${NEWS_TAG[label] ? ` ${NEWS_TAG[label]}` : ""}">${esc(cap(label))}</span>` : "<span></span>");
  // How headlines are labeled, only where there are some.
  const lede = [o.text, items.length ? "Each headline is labeled by the words it uses, with the reason shown. Read the story before acting on a label." : ""]
    .filter(Boolean).map(esc).join(" ");
  return `<section class="sec" id="news" aria-labelledby="news-h">
    <div class="sec-head"><h2 id="news-h">Recent news</h2><span class="tag${NEWS_TAG[o.label] ? ` ${NEWS_TAG[o.label]}` : ""}">${o.label === "none" ? "No news" : `Overall: ${esc(cap(o.label))}`}</span></div>
    ${lede ? `<p class="sec-lede">${lede}</p>` : ""}
    ${items.length ? `<ul class="frame news">${items.map((i) => {
      const press = /^press release/i.test(i.source || "");
      const src = press ? (i.source || "").replace(/^press release\s*\(?([^)]*)\)?\s*$/i, (m, s) => cap(s || "")) : i.source;
      return `<li class="ni">${toneTag(i.label)}
        <div class="ni-body">
          <a class="ni-t" href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}<span class="sr-only"> (opens in a new tab)</span></a>
          ${i.reason ? `<p class="ni-why">${esc(i.reason)}</p>` : ""}
          <p class="ni-src">${press ? `<span class="tag tag-line">Press release</span>` : ""}${[src, day(i.date)].filter(Boolean).map(esc).join(", ")}</p>
        </div></li>`;
    }).join("")}</ul>` : ""}
  </section>`;
}

// ---- Notes, sources and the page around them ----

// Where revenue was read for a company whose revenue the SEC's data gives under no label the site reads: its own income
// statements (fundamentals.revenue_source; older data has none).
function revenueSource(f) {
  const s = f.revenue_source;
  const filed = s.filed ? ` filed ${esc(fullDate(s.filed))}` : "";
  const quarters = (f.quarters || []).some((q) => q.revenue != null) ? " Quarterly revenue comes from the same line of its quarterly reports." : "";
  // The breakdown it is shown under, where there is one (APA's "Oil and gas"); a line shown for a company and its
  // predecessor apart gives only one of them for the year.
  const part = s.sections && s.sections.length === 1 ? ` (${esc(s.sections[0])})` : "";
  return `Revenue is read from the company's own income statements: the "${esc(s.label)}" line${part} of its Form ${esc(s.form)}${filed} for fiscal year ${esc(String(f.fiscal_year))}.${quarters} The SEC's data doesn't give this company's revenue under the labels this site reads for other companies.`;
}

function notesSection(r, meta, home) {
  const f = r.fundamentals;
  const items = [
    f?.partial ? `Quarterly revenue from the company's quarterly reports (Form 10-Q); the SEC's data holds no full fiscal year of its results${r.coverage?.kind === "revenue_since" ? " with sales" : ""} yet. Source: SEC filings.`
      : f ? `Financials from fiscal year ${esc(String(f.fiscal_year))}${f.fiscal_year_end ? ` (ended ${esc(fullDate(f.fiscal_year_end))})` : ""}.${f.balance_as_of ? ` Balance sheet as of ${esc(fullDate(f.balance_as_of))}.` : ""} Source: SEC filings.` : "",
    f?.revenue_source ? revenueSource(f) : "",
    r.peer_group && !r.peer_basis ? `Compared with ${esc(String(r.peer_count))} other US-listed companies in ${esc(r.peer_group)}.` : "",
    meta?.market_date ? `${esc(closeWords(meta))}; data as of ${esc(fullDate(meta.market_date))}.` : "",
    "Educational estimates only. Not investment advice.",
  ].filter(Boolean);
  return `<section class="sec r-notes" aria-labelledby="notes-h">
    <h2 id="notes-h">Sources and notes</h2>
    ${home === "notes" ? noteBox(f.one_time_note) : ""}
    <ul>${items.map((t) => `<li>${t}</li>`).join("")}</ul>
  </section>`;
}

function header(r, meta) {
  const where = [r.sector, r.industry].filter(Boolean).map(esc).join(", ");
  const href = `screener.html${r.sector ? `?sector=${encodeURIComponent(r.sector)}` : ""}`;
  return `<header class="r-head">
    <div class="r-id">
      <h1 class="r-name" tabindex="-1"><span class="r-tk">${esc(r.symbol)}</span> <span class="r-nm">${esc(displayName(r.name))}</span></h1>
      <p class="r-where">${r.exchange ? `<span>${esc(r.exchange === "OTC" ? "Over the counter (OTC), not on an exchange" : r.exchange)}</span>` : ""}${where ? `<span>${where}</span>` : ""}${r.screener === true ? `<a class="tag tag-up r-pass" href="${href}">${icon("check")}Passes the screener</a>` : ""}</p>
    </div>
    <dl class="proof-line r-proof" aria-label="Price and size">
      <div class="r-px"><dt>${r.price_day && meta?.price_date && r.price_day !== meta.price_date ? `share price, last traded ${esc(longDate(r.price_day))}` : "share price"}</dt><dd>${price(r.price)}</dd></div>
      ${r.day_change != null && meta?.price_date ? `<div><dt>on ${esc(longDate(meta.price_date))}</dt><dd class="${signClass(Number((r.day_change * 100).toFixed(2)))}">${pct(r.day_change, 2, true)}</dd></div>` : ""}
      ${r.volume != null && meta?.price_date ? `<div><dt>shares traded</dt><dd>${volumeShort(r.volume)}</dd></div>` : ""}
      <div><dt>market value</dt><dd>${money(r.mcap)}</dd></div>
    </dl>
    <p class="source-line r-source" data-source-line></p>
  </header>`;
}

// Shares traded in a day, shortened.
function volumeShort(n) {
  return n >= 1e9 ? `${(n / 1e9).toFixed(2)}B` : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n);
}

function render(r, sc, meta) {
  shown = r;
  document.title = `${r.symbol}: ${displayName(r.name)} | ${TITLE}`;
  const home = oneTimeHome(r);
  const parts = [
    ["score", "Screener score", scoreSection(r, sc, meta, home)],
    ["quarters", "Revenue by quarter", quarterSection(r)],
    ["history", "Financials", historySection(r, home)],
    ["health", "Health check", healthSection(r, home)],
    ["peers", "Similar companies", peersSection(r)],
    ["insiders", "Insider buying", insiderSection(r)],
    ["news", "Recent news", newsSection(r)],
  ].filter(([, , html]) => html);
  host.innerHTML = `${header(r, meta)}
    <div class="r-grid${r.valuation && VERDICT[r.valuation.verdict] ? "" : " is-none"}">${verdictPanel(r, home)}${pricePanel(r)}</div>
    <nav class="r-rail" aria-label="Sections of this report"><span class="r-rail-l">On this page</span>${parts.map(([id, label]) =>
      `<a href="#${id}" data-jump="${id}">${label}</a>`).join("")}</nav>
    ${parts.map(([, , html]) => html).join("")}
    ${notesSection(r, meta, home)}`;
  if (meta) paintDataStatus(meta);
  fitRanges();
}

// ---- States: the start page, loading, a missing report and a failed load ----

// Bumped on every navigation, so a slow response for an earlier pick never paints over a later one.
let seq = 0;

function settle(y, focus) {
  host.setAttribute("aria-busy", "false");
  scrollTo(0, y);
  if (focus) host.querySelector("h1")?.focus({ preventScroll: true });
  requestAnimationFrame(() => requestAnimationFrame(() => { restoring = false; }));
}

function setMode(start) {
  startHead.hidden = !start;
  searchHost.classList.toggle("is-start", start);
  searchInput.placeholder = start ? "Ticker or company, e.g. AAPL" : "Another ticker or company";
}

async function start({ y = 0, focus = false, initial = false } = {}) {
  const id = ++seq;
  current = null;
  restoring = true;
  document.title = `Ticker report | ${TITLE}`;
  setMode(true);
  host.innerHTML = "";
  settle(y, false);
  // The keyboard would cover a phone's screen, so the search box takes focus only with a mouse or trackpad.
  if (initial && matchMedia("(hover: hover) and (pointer: fine)").matches) searchInput.focus({ preventScroll: true });
  else if (focus) startHead.querySelector("h1")?.focus({ preventScroll: true });
  let popular = "";
  try {
    const u = await load("universe.json");
    popular = [...u].sort((a, b) => (b[3] || 0) - (a[3] || 0)).slice(0, 8)
      .map((r) => `<li><a class="chip" href="?t=${encodeURIComponent(r[0])}" data-sym="${esc(r[0])}"><span class="sym">${esc(r[0])}</span> <span class="nm">${esc(displayName(r[1]))}</span></a></li>`).join("");
  } catch {}
  if (id !== seq || !popular) return;
  host.innerHTML = `<section class="r-pop" aria-labelledby="pop-h"><h2 id="pop-h">Largest by market value</h2><ul class="r-chips">${popular}</ul></section>`;
}

const SKELETON = `<div class="r-skel" aria-hidden="true">
  <div class="r-skel-head"><div><div class="skeleton sk-h1"></div><div class="skeleton sk-line"></div></div><div class="skeleton sk-px"></div></div>
  <div class="r-grid"><div class="skeleton sk-panel"></div><div class="skeleton sk-panel sk-short"></div></div></div>`;

// A ticker is letters, digits, dots and dashes (BRK.B); anything else can't name a report file.
const VALID = /^[A-Z0-9][A-Z0-9.\-]{0,11}$/;

async function show(sym, { y = 0, focus = false } = {}) {
  const id = ++seq;
  const S = String(sym).trim().toUpperCase();
  current = S;
  restoring = true;
  setMode(false);
  document.title = `${S} | ${TITLE}`;
  host.setAttribute("aria-busy", "true");
  host.innerHTML = SKELETON;
  try {
    if (!VALID.test(S)) throw Object.assign(new Error("not a ticker"), { status: 404 });
    const r = await load(`t/${S}.json`);
    // The screener's ledger and the data date come with the report, so the page is drawn once, at its full height.
    const [sc, meta] = await Promise.all([
      r.screener === true ? load("screener.json").catch(() => null) : null,
      load("meta.json").catch(() => null),
    ]);
    if (id !== seq) return;
    render(r, sc, meta);
    settle(y, focus);
    if (!y && location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({ block: "start" });
  } catch (e) {
    if (id !== seq) return;
    if (e.status === 404) {
      document.title = `No report for ${S} | ${TITLE}`;
      host.innerHTML = `<section class="frame r-empty"><h1 tabindex="-1">No report for "${esc(S)}"</h1>
        <p>Reports cover common stocks listed on the NYSE, Nasdaq and NYSE American that file with the SEC. Check the ticker, or search by company name above.</p></section>`;
    } else {
      document.title = `Could not load ${S} | ${TITLE}`;
      host.innerHTML = `<section class="frame r-empty"><h1 tabindex="-1">Could not load the ${esc(S)} report</h1>
        <p>The connection may have dropped. Try again in a moment.</p>
        <button class="btn btn-ghost btn-sm" type="button" data-retry>Try again</button></section>`;
      host.querySelector("[data-retry]").addEventListener("click", () => show(S, { focus: true }));
    }
    settle(0, focus);
  }
}

function route(opts = {}) {
  const t = new URLSearchParams(location.search).get("t");
  t ? show(t, opts) : start(opts);
}
route({ y: initialY, initial: true });
