// Insider buying: open-market purchases from SEC Form 4 filings, one entry per company under the day of its newest
// filing, then the new 5%+ stakes from Schedule 13D filings. Data: insiders.json, with meta.json for the window's dates
// and the days still being scanned (the page still works without meta.json).
import { chrome, date, displayName, esc, icon, load, localToday, longDate, money, pct, price, relDay, signClass, times } from "./common.js";
import { deRatio } from "./ledger.js";

chrome();

const PAGE = 25; // companies drawn at first, and added by each "Show more"
const BUYERS_SHOWN = 6; // a company's buyer rows before "Show all N buyers"
const STAKES_SHOWN = 15;
const PURPOSE_CUT = 280; // pipeline/insiders.parse_13d keeps a stake's stated purpose to its first 280 characters

const params = new URLSearchParams(location.search);
const FITS = ["any", "pass", "near"];
// Each tier choice and the tiers it shows. The URL names it in words: ?tier=1 for Tier 1 only, ?tier=all for every
// purchase; Tier 1 and 2, the default, leaves it out.
const TIERS = { 12: [1, 2], 1: [1], 123: [1, 2, 3] };
const TIER_FROM_URL = new Map([["1", "1"], ["12", "12"], ["all", "123"], ["123", "123"]]);
const tierToUrl = (t) => (t === "1" ? "1" : t === "123" ? "all" : null);
const state = {
  sector: params.get("sector") || "All",
  tier: TIER_FROM_URL.get(params.get("tier")) || "12",
  fit: FITS.includes(params.get("fit")) ? params.get("fit") : "any",
  shown: PAGE,
  open: new Set(), // companies showing every buyer
  allStakes: false,
};
const feed = document.querySelector("[data-feed]");
const stakesEl = document.querySelector("[data-stakes]");
const countEl = document.querySelector("[data-count]");
const resetEl = document.querySelector("[data-reset]");
let data = null;
let meta = null;
let sectors = []; // every sector with a purchase or a new stake in the data, A to Z
let fitChecked = false; // whether the data has the screener check (see start)
const hasFit = () => fitChecked;

// ---- Words and figures ----

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
// "a", "a and b", "a, b and c" (or "or").
const words = (xs, and = "and") => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ${and} ${xs[xs.length - 1]}`);
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
const reportHref = (sym) => `report.html?t=${encodeURIComponent(sym)}`;
const slug = (sym) => String(sym || "").replace(/[^A-Za-z0-9]/g, "-");
// Shares: exact under a million, then in millions to two places.
const shares = (n) => (n == null ? "n/a" : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : Math.round(n).toLocaleString("en-US"));
// A round threshold in words ("$10 million"), anything else in the site's money format.
const amount = (n) => (n >= 1e9 && n % 1e9 === 0 ? `$${n / 1e9} billion` : n >= 1e6 && n % 1e6 === 0 ? `$${n / 1e6} million` : money(n));
const tier2Stake = () => data.tiers?.tier2_stake ?? 0.2;
const windowDays = () => data.window_days ?? meta?.insider_days;

function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The filing window as pipeline/insiders.build counts it: filings from the update's date less window_days, through the
// latest day scanned (or a later filing already read). Without meta.json the page says "in the last N days".
function windowSpan() {
  if (!meta?.market_date || windowDays() == null) return null;
  const from = addDays(meta.market_date, -windowDays());
  const to = [meta.last_filing_day, ...data.companies.map((c) => c.filed), ...(data.stakes || []).map((s) => s.filed)]
    .filter(Boolean).sort().pop();
  return to && to >= from ? `${date(from)} to ${date(to)}` : null;
}
const lastDays = () => (windowDays() == null ? "recently" : `in the last ${windowDays()} days`);
const filedWords = () => (windowSpan() ? `filed ${windowSpan()}` : lastDays());
const inFilings = () => (windowSpan() ? `in filings from ${windowSpan()}` : lastDays());
const fromTo = () => (windowSpan() ? `from ${windowSpan()}` : lastDays());

// A buyer's roles as filed, in plain words: an officer's title first (chief officers by their usual initials, a title
// filed in capitals in normal case), then director, then an owner of 10% or more.
const CHIEFS = [[/chief executive officer/gi, "CEO"], [/chief financial officer/gi, "CFO"], [/chief operating officer/gi, "COO"]];
const SHORT_TITLES = { VP: "Vice president", SVP: "Senior vice president", EVP: "Executive vice president" };
const KEEP_CAPS = /^(CEO|CFO|COO|CTO|CIO|CAO|CMO|EVP|SVP|VP|GC|LLC|LP|II|III)$/;
function officerTitle(t) {
  let s = String(t).trim().replace(/\s{2,}/g, " ");
  if (SHORT_TITLES[s]) return SHORT_TITLES[s];
  s = CHIEFS.reduce((x, [re, to]) => x.replace(re, to), s);
  if (s === s.toUpperCase() && /[A-Z]{4,}/.test(s)) s = s.replace(/[A-Z]{2,}/g, (w) => (KEEP_CAPS.test(w) ? w : w[0] + w.slice(1).toLowerCase()));
  return s;
}
const roleRank = (r) => (/^director$/i.test(r) ? 1 : /^10% owner$/i.test(r) ? 2 : /^see remarks$/i.test(r) ? 3 : 0);
function roleWords(roles) {
  if (!roles?.length) return "Insider";
  return cap([...roles].sort((a, b) => roleRank(a) - roleRank(b)).map((r) =>
    /^director$/i.test(r) ? "director" : /^10% owner$/i.test(r) ? "owns 10% or more"
      : /^see remarks$/i.test(r) ? "title given in the filing's remarks" : officerTitle(r)).join(", "));
}

// ---- Filters ----

const tierOk = (c) => TIERS[state.tier].includes(c.tier);
// Whether a company clears the screener choice (screener.fit in the pipeline). "Passes or misses by one" takes a company
// whose figures miss no rule and that has one rule that could not be checked, since it can only pass or miss that one.
function fitOk(c) {
  if (state.fit === "any") return true;
  const f = c.fit;
  return f?.status === "pass" || (state.fit === "near" && (f?.status === "near" || (f?.status === "unconfirmed" && f.unchecked?.length === 1)));
}
const matching = () => data.companies.filter((c) => tierOk(c) && fitOk(c) && (state.sector === "All" || c.sector === state.sector));

const TIER_WORDS = { 12: "Tier 1 or 2 buying", 1: "Tier 1 buying", 123: "open-market insider buying" };
const FIT_WORDS = {
  pass: ["passes the screener", "pass the screener"],
  near: ["passes the screener or misses it by one rule", "pass the screener or miss it by one rule"],
};

function setParam(key, value) {
  const u = new URL(location);
  value == null ? u.searchParams.delete(key) : u.searchParams.set(key, value);
  history.replaceState(null, "", u);
}

// ---- The screener check ----

// The figures the check used, said in full where the old page kept them in a tooltip.
function fitFigures(f) {
  const parts = [f.ps == null ? null : `price to sales ${times(f.ps)}`,
    f.net_margin == null ? null : `net margin ${pct(f.net_margin)} without one-time items`,
    f.lt_de == null ? null : `long-term debt to equity ${deRatio(f.lt_de)}`].filter(Boolean);
  return parts.length ? `<span class="fit-figs">${esc(cap(parts.join(", ")))}.</span>` : "";
}

// One line on how the company measures against the screener's rules: the result, what it misses or could not be
// checked, then its figures. `compact` is the stakes table's Screener column: the result and what it misses only (the
// figures are in the company's report).
function fitLine(f, sector, { compact = false } = {}) {
  if (!f) return compact ? `<span class="faint">Not checked</span>` : "";
  const line = compact
    ? (tag, text) => `<div class="fit-c">${tag}${tag ? `<span class="sr-only">: </span>` : ""}<span class="fit-t">${text}</span></div>`
    : (tag, text, figs = "") => `<p class="fit"><span class="fit-k">Screener<span class="sr-only">:</span></span>${tag}${tag ? `<span class="sr-only">: </span>` : ""}<span class="fit-t">${text}</span>${figs ? ` ${figs}` : ""}</p>`;
  const unchecked = f.unchecked?.length ? `${words(f.unchecked)} could not be checked` : "";
  switch (f.status) {
    case "not_screened": return line("", `Not screened: ${esc(f.why || "outside the screener's scope")}.`);
    case "no_data": return line("", "No yearly figures to check.");
    case "pass": return line(`<span class="tag tag-up">Passes</span>`,
      `On the <a href="screener.html${sector ? `?sector=${encodeURIComponent(sector)}` : ""}">screener's list</a>.`, fitFigures(f));
    case "near": return line(`<span class="tag tag-mid">Misses one rule</span>`, `${esc(cap(f.misses?.[0] || ""))}.`, fitFigures(f));
    case "unconfirmed": return line(`<span class="tag tag-mid">Not confirmed</span>`,
      `Misses no rule, but ${esc(unchecked || "one rule could not be checked")}.${f.why ? ` ${esc(f.why)}` : ""}`, fitFigures(f));
    case "fail": {
      const n = f.missed || f.misses?.length || 0;
      return line(`<span class="tag">Misses ${n === 1 ? "one rule" : `${n} rules`}</span>`,
        `${esc(cap((f.misses || []).join("; ")))}${unchecked ? `; ${esc(unchecked)}` : ""}.`, fitFigures(f));
    }
    default: return "";
  }
}

// ---- A company and its buyers ----

const TIER_TAG = {
  1: `<span class="tag tag-solid">Tier 1</span>`,
  2: `<span class="tag">Tier 2</span>`,
  3: `<span class="tag tag-line">Smaller<span class="sr-only"> purchases</span></span>`,
};

function stakeCell(b) {
  const change = b.new_position ? `<b class="up">New position</b>`
    : b.stake_increase == null ? `<span class="faint">n/a</span>`
    : `<b class="${b.stake_increase >= tier2Stake() ? "up" : ""}">${pct(b.stake_increase, b.stake_increase < 0.1 ? 1 : 0, true)}</b>`;
  // Every share the buyer reported, in their own name, trusts, retirement accounts and family: before their first
  // purchase in the window, and what their latest filing reports.
  const held = b.owned_before == null ? "Holdings not available"
    : b.owned_after == null ? `Held ${shares(b.owned_before)} before`
    : `Held ${shares(b.owned_before)} to ${shares(b.owned_after)}`;
  return `${change}<span class="sub">${esc(held)}</span>`;
}

function buyerRow(b, extra, hidden) {
  const when = b.first && b.last && b.first !== b.last ? `${date(b.first)} to ${date(b.last)}` : date(b.last);
  return `<tr${extra ? " data-extra" : ""}${hidden ? " hidden" : ""}>
    <th scope="row" class="b-who">${esc(b.name)}</th>
    <td class="txt b-role">${esc(roleWords(b.roles))}<span class="b-when-inline">, bought ${esc(when)}</span></td>
    <td class="txt b-when" data-label="Bought">${esc(when)}</td>
    <td class="b-val" data-label="Value">${money(b.value)}${b.shares == null ? "" : `<span class="sub">${shares(b.shares)} shares</span>`}</td>
    <td class="b-paid" data-label="Price paid">${price(b.avg_price)}</td>
    <td class="b-stake" data-label="Stake change">${stakeCell(b)}</td>
    <td class="b-doc">${b.filing ? `<a class="doc" href="${esc(b.filing)}" target="_blank" rel="noopener">Form 4${icon("arrow-up-right")}<span class="sr-only">, ${esc(b.name)}'s latest filing at the SEC (opens in a new tab)</span></a>` : `<span class="faint">n/a</span>`}</td>
  </tr>`;
}

function card(c, fresh) {
  const key = slug(c.symbol || c.name);
  const name = esc(displayName(c.name));
  const title = c.symbol
    ? `<a href="${reportHref(c.symbol)}"><span class="sym">${esc(c.symbol)}</span><span class="nm">${name}</span></a>`
    : `<span class="sym">Unlisted</span><span class="nm">${name}</span>`;
  const vs = c.vs_paid == null ? null : Math.round(c.vs_paid * 1000) / 1000;
  const open = state.open.has(key);
  const n = c.buyers.length;
  return `<article class="buy${fresh ? " fresh" : ""}" id="co-${key}" aria-labelledby="h-${key}">
    <div class="buy-head">
      <div class="buy-id">
        <h3 id="h-${key}">${title}</h3>
        <p class="buy-meta">${TIER_TAG[c.tier] || ""}<span>${esc(c.sector || "Sector not given")}, ${c.mcap ? `${money(c.mcap)} market value` : "market value not available"}</span></p>
      </div>
      <dl class="buy-figs">
        <div><dt>Bought</dt><dd>${money(c.total_value)}</dd><dd class="sub">by ${plural(n, "buyer")}</dd></div>
        <div><dt>Average price paid</dt><dd>${price(c.avg_price)}</dd></div>
        <div><dt>Price now</dt><dd>${price(c.price)}</dd><dd class="sub ${signClass(vs)}">${vs == null ? "n/a" : pct(vs, 1, true)} vs. paid</dd></div>
      </dl>
    </div>
    ${fitLine(c.fit, c.sector)}
    <div class="buy-table"><table class="data buyers">
      <caption class="sr-only">Who bought ${esc(c.symbol || displayName(c.name))}, newest filing first</caption>
      <colgroup><col><col class="w-role"><col class="w-when"><col class="w-val"><col class="w-paid"><col class="w-stake"><col class="w-doc"></colgroup>
      <thead><tr><th scope="col" class="txt">Buyer</th><th scope="col" class="txt">Role</th><th scope="col" class="txt">Bought</th><th scope="col">Value</th><th scope="col">Price paid</th><th scope="col">Stake change</th><th scope="col"><span class="sr-only">Filing</span></th></tr></thead>
      <tbody id="buyers-${key}">${c.buyers.map((b, i) => buyerRow(b, i >= BUYERS_SHOWN, i >= BUYERS_SHOWN && !open)).join("")}</tbody>
    </table></div>
    ${n > BUYERS_SHOWN ? `<div class="buy-foot"><button class="btn btn-ghost btn-sm" type="button" data-buyers="${key}" data-n="${n}" aria-controls="buyers-${key}" aria-expanded="${open}">${open ? `Show the first ${BUYERS_SHOWN}` : `Show all ${n} buyers`}</button></div>` : ""}
  </article>`;
}

// ---- The feed ----

function renderCount(n) {
  const sector = state.sector === "All" ? "" : `${esc(state.sector)} `;
  const fit = state.fit === "any" ? "" : ` that ${FIT_WORDS[state.fit][n === 1 ? 0 : 1]}`;
  countEl.innerHTML = `<b>${n}</b> ${sector}${n === 1 ? "company" : "companies"} with ${TIER_WORDS[state.tier]}${fit}, ${esc(filedWords())}`;
}

function emptyFeed() {
  // Only suggest widening a filter that is narrowed.
  const tries = [state.tier !== "123" && `"All purchases"`, state.fit !== "any" && `"Any company"`, state.sector !== "All" && "another sector"].filter(Boolean);
  const which = { 1: "Tier 1", 12: "Tier 1 or 2", 123: "any" }[state.tier];
  const fit = state.fit === "pass" ? " and passed the screener" : state.fit === "near" ? " and passed the screener or missed it by one rule" : "";
  return `<div class="panel empty"><h3>No purchases match</h3><p>No ${state.sector === "All" ? "" : `${esc(state.sector)} `}company had
    ${which} open-market insider buying ${esc(inFilings())}${fit}.${tries.length ? ` Try ${words(tries, "or")}.` : ""}</p></div>`;
}

function renderFeed(fresh = new Set()) {
  const rows = matching();
  feed.setAttribute("aria-busy", "false");
  renderCount(rows.length);
  if (!rows.length) {
    feed.innerHTML = emptyFeed();
    return;
  }
  const perDay = new Map();
  for (const c of rows) perDay.set(c.filed, (perDay.get(c.filed) || 0) + 1);
  const days = new Map();
  for (const c of rows.slice(0, state.shown)) {
    if (!days.has(c.filed)) days.set(c.filed, []);
    days.get(c.filed).push(c);
  }
  const today = localToday();
  const left = rows.length - state.shown;
  feed.innerHTML = [...days].map(([d, list]) => {
    const rel = relDay(d, today);
    const all = perDay.get(d);
    return `<section class="day" id="filed-${esc(d)}" aria-labelledby="day-${esc(d)}">
      <div class="day-head"><h2 id="day-${esc(d)}">Filed ${esc(date(d, { weekday: "long", month: "short", day: "numeric" }))}${
        rel === "Today" || rel === "Yesterday" ? `<span class="rel"><span class="sr-only">, </span>${rel}</span>` : ""}</h2>
        <span class="day-n">${list.length < all ? `${list.length} of ${all} companies shown` : plural(all, "company", "companies")}</span></div>
      <div class="buy-list">${list.map((c) => card(c, fresh.has(c))).join("")}</div>
    </section>`;
  }).join("") + (left > 0
    ? `<div class="feed-more"><button class="btn btn-ghost" type="button" data-more-feed>Show ${Math.min(PAGE, left)} more${icon("arrow-down")}</button><span>${state.shown} of ${rows.length} companies shown</span></div>`
    : rows.length > PAGE ? `<p class="feed-more"><span>All ${rows.length} companies shown.</span></p>` : "");
}

feed.addEventListener("click", (e) => {
  if (!data) return;
  const more = e.target.closest("[data-more-feed]");
  if (more) {
    const rows = matching();
    const from = state.shown;
    state.shown += PAGE;
    renderFeed(new Set(rows.slice(from, state.shown)));
    // Keyboard focus goes to the first company the button revealed (its report link, or its first control).
    const first = feed.querySelector(".buy.fresh");
    (first?.querySelector("h3 a") || first?.querySelector("a, button"))?.focus();
    return;
  }
  const btn = e.target.closest("[data-buyers]");
  if (btn) {
    const key = btn.dataset.buyers;
    const opening = btn.getAttribute("aria-expanded") !== "true";
    opening ? state.open.add(key) : state.open.delete(key);
    document.getElementById(btn.getAttribute("aria-controls"))?.querySelectorAll("tr[data-extra]").forEach((tr) => { tr.hidden = !opening; });
    btn.setAttribute("aria-expanded", String(opening));
    btn.textContent = opening ? `Show the first ${BUYERS_SHOWN}` : `Show all ${btn.dataset.n} buyers`;
    // A long list closing above the button would leave it far down the page.
    if (!opening) btn.scrollIntoView({ block: "nearest" });
  }
});

// ---- New 5%+ stakes ----

// The stated purpose in full, or, when it was cut at PURPOSE_CUT characters, up to its last whole word, an ellipsis and
// a link to the filing that has the rest.
function purpose(s) {
  const raw = String(s.purpose || "");
  const p = raw.trim();
  if (!p) return `<span class="faint">No purpose was read from the filing.</span>`;
  // Measured before trimming: a statement cut just after a space is 280 characters with a trailing space.
  if (raw.length < PURPOSE_CUT) return esc(p);
  // A cut just after a space ended on a whole word; otherwise the last word may be partial and is left out.
  const space = p.lastIndexOf(" ");
  const kept = (/\s$/.test(raw) || space < PURPOSE_CUT / 2 ? p : p.slice(0, space)).replace(/[\s,;:(]+$/, "");
  const ends = /[.!?]$/.test(kept);
  return `${esc(kept)}${ends ? "" : "…"}${s.filing ? ` <a class="more-in" href="${esc(s.filing)}" target="_blank" rel="noopener">Full statement in the filing<span class="sr-only"> (opens in a new tab)</span></a>` : ""}`;
}

function stakeRow(s, extra) {
  const name = esc(displayName(s.name));
  const co = s.symbol ? `<a href="${reportHref(s.symbol)}"><span class="sym">${esc(s.symbol)}</span><span class="nm">${name}</span></a>`
    : `<span class="sym">Unlisted</span><span class="nm">${name}</span>`;
  return `<tr${extra ? ` data-extra${state.allStakes ? "" : " hidden"}` : ""}>
    <td class="txt s-filed" data-label="Filed">${esc(date(s.filed))}</td>
    <th scope="row" class="s-co"><div class="s-name">${co}</div></th>
    <td class="txt s-filer" data-label="Filer">${esc(s.filer || "Not given")}</td>
    <td class="s-pct" data-label="Stake">${s.percent == null ? "n/a" : `${s.percent.toFixed(1)}%`}</td>
    ${hasFit() ? `<td class="txt s-fit" data-label="Screener">${fitLine(s.fit, s.sector, { compact: true })}</td>` : ""}
    <td class="txt s-purpose" data-label="Stated purpose">${purpose(s)}</td>
    <td class="s-doc">${s.filing ? `<a class="doc" href="${esc(s.filing)}" target="_blank" rel="noopener">13D${icon("arrow-up-right")}<span class="sr-only">, ${esc(s.filer || "the")} filing at the SEC (opens in a new tab)</span></a>` : ""}</td>
  </tr>`;
}

function renderStakes() {
  const all = data.stakes || [];
  const inSector = all.filter((s) => state.sector === "All" || s.sector === state.sector);
  const rows = inSector.filter(fitOk);
  const where = state.sector === "All" ? "" : `in ${esc(state.sector)} `;
  document.querySelector("[data-stakes-count]").textContent = rows.length === all.length
    ? `${plural(all.length, "stake")}, ${filedWords()}` : `${rows.length} of ${all.length} stakes`;
  if (!rows.length) {
    // When the screener choice hides every stake, say so rather than that none were filed.
    const hidden = inSector.length === 1
      ? `The new 5%+ stake filed ${where}${esc(fromTo())} is not in a company that ${FIT_WORDS[state.fit][0]}. Choose "Any company" to see it.`
      : `None of the ${inSector.length} new 5%+ stakes filed ${where}${esc(fromTo())} is in a company that ${FIT_WORDS[state.fit][0]}. Choose "Any company" to see them.`;
    stakesEl.innerHTML = `<div class="panel empty"><p>${inSector.length ? hidden : `No new 5%+ stakes were filed ${where}${esc(fromTo())}.`}</p></div>`;
    return;
  }
  stakesEl.innerHTML = `<div class="table-wrap"><table class="data stakes">
    <caption class="sr-only">New 5%+ stakes from Schedule 13D filings, newest first</caption>
    <colgroup><col class="w-filed"><col class="w-co"><col class="w-filer"><col class="w-pct">${hasFit() ? `<col class="w-fit">` : ""}<col><col class="w-doc"></colgroup>
    <thead><tr><th scope="col" class="txt">Filed</th><th scope="col" class="txt">Company</th><th scope="col" class="txt">Filer</th><th scope="col">Stake</th>${hasFit() ? `<th scope="col" class="txt">Screener</th>` : ""}<th scope="col" class="txt">Stated purpose</th><th scope="col"><span class="sr-only">Filing</span></th></tr></thead>
    <tbody id="stake-rows">${rows.map((s, i) => stakeRow(s, i >= STAKES_SHOWN)).join("")}</tbody></table></div>
    ${rows.length > STAKES_SHOWN ? `<button class="btn btn-ghost btn-sm stakes-more" type="button" data-more-stakes data-n="${rows.length}" aria-controls="stake-rows" aria-expanded="${state.allStakes}">${state.allStakes ? "Show fewer" : `Show all ${rows.length} stakes`}</button>` : ""}`;
}

stakesEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-more-stakes]");
  if (!btn || !data) return;
  state.allStakes = btn.getAttribute("aria-expanded") !== "true";
  const extra = stakesEl.querySelectorAll("tr[data-extra]");
  extra.forEach((tr) => { tr.hidden = !state.allStakes; });
  btn.setAttribute("aria-expanded", String(state.allStakes));
  btn.textContent = state.allStakes ? "Show fewer" : `Show all ${btn.dataset.n} stakes`;
  if (state.allStakes) extra[0]?.querySelector("a")?.focus(); // keep keyboard focus in the list, at the first row it revealed
  else btn.scrollIntoView({ block: "nearest" });
});

// ---- Controls ----

function renderSectors() {
  const el = document.querySelector("[data-sectors]");
  // Each sector counts the companies the feed would show with the size and screener choices as they are.
  const counts = new Map();
  for (const c of data.companies) if (tierOk(c) && fitOk(c)) counts.set(c.sector, (counts.get(c.sector) || 0) + 1);
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  el.innerHTML = [["All", total], ...sectors.map((s) => [s, counts.get(s) || 0])].map(([s, n]) =>
    `<button class="pill${n ? "" : " pill-empty"}" type="button" data-sector="${esc(s)}" aria-pressed="${s === state.sector}">${esc(s === "All" ? "All sectors" : s)}<span class="count"><span class="sr-only">, </span>${n}<span class="sr-only"> ${n === 1 ? "company" : "companies"}</span></span></button>`).join("");
  // On a phone the sectors scroll sideways: bring the chosen one into view without moving the page.
  const on = el.querySelector('[aria-pressed="true"]');
  if (on && el.scrollWidth > el.clientWidth) el.scrollLeft = Math.max(0, on.offsetLeft - el.offsetLeft - 16);
}

function paintControls() {
  document.querySelectorAll("[data-tier]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.tier === state.tier)));
  document.querySelectorAll("[data-fit]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.fit === state.fit)));
  resetEl.hidden = state.sector === "All" && state.tier === "12" && state.fit === "any";
}

// A filter change: the feed starts again at its first page.
function refilter() {
  state.shown = PAGE;
  paintControls();
  renderSectors();
  renderFeed();
  renderStakes();
}

document.querySelector("[data-sectors]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sector]");
  if (!b || !data) return;
  state.sector = b.dataset.sector;
  setParam("sector", state.sector === "All" ? null : state.sector);
  refilter();
  // The pills are drawn again, so keyboard focus goes back to the one chosen rather than to the page.
  document.querySelector(`[data-sectors] [data-sector="${CSS.escape(state.sector)}"]`)?.focus();
});
document.querySelector("[data-tiers]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tier]");
  if (!b || !data || !TIERS[b.dataset.tier]) return;
  state.tier = b.dataset.tier;
  setParam("tier", tierToUrl(state.tier));
  refilter();
});
document.querySelector("[data-fits]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-fit]");
  if (!b || !data || !FITS.includes(b.dataset.fit)) return;
  state.fit = b.dataset.fit;
  setParam("fit", state.fit === "any" ? null : state.fit);
  refilter();
});
resetEl.addEventListener("click", () => {
  if (!data) return;
  state.sector = "All"; state.tier = "12"; state.fit = "any";
  ["sector", "tier", "fit"].forEach((k) => setParam(k, null));
  refilter();
  document.querySelector("[data-sectors] [aria-pressed='true']")?.focus();
});

// ---- The page head, the tier definitions and the catching-up notice ----

function renderProof() {
  const set = (k, html) => { const el = document.querySelector(`[data-proof="${k}"]`); if (el) el.innerHTML = html; };
  const n = (t) => data.companies.filter((c) => c.tier === t).length.toLocaleString("en-US");
  set("t1", n(1));
  set("t2", n(2));
  set("t3", n(3));
  set("stakes", `<a href="#stakes">${(data.stakes || []).length.toLocaleString("en-US")}</a>`);
  set("date", meta?.market_date ? esc(longDate(meta.market_date)) : "n/a");
}

// The thresholds from insiders.json "tiers"; data without them keeps the wording in the page.
function explainTiers() {
  const t = data.tiers || {};
  const set = (k, s) => document.querySelectorAll(`[data-tier-def="${k}"]`).forEach((el) => { el.textContent = s; });
  if (t.tier1 != null) {
    set("1", `${amount(t.tier1)} or more bought`);
    set("1-long", `${amount(t.tier1)} or more bought on the open market`);
  }
  if (t.tier2 != null && t.tier2_stake != null) {
    set("2", `${amount(t.tier2)} or more, or one buyer's stake up ${pct(t.tier2_stake, 0)} or more`);
    set("2-long", `${amount(t.tier2)} or more, or a single buyer growing their stake by ${pct(t.tier2_stake, 0)} or more`);
  }
}

function pendingNotice() {
  const el = document.querySelector("[data-pending]");
  const p = [...(meta?.insider_pending || [])].sort().reverse();
  if (!p.length || !meta?.market_date) return;
  const oldest = p[p.length - 1], newest = p[0];
  const which = p.length === 1 ? `${date(newest)} has` : `${p.length} weekdays between ${date(oldest)} and ${date(newest)} have`;
  // Only claim the recent days are complete when the newest unscanned day is older than the latest one.
  const recent = newest < meta.market_date
    ? `Everything filed after ${date(newest)} has been checked.`
    : `${p.length === 1 ? "That is" : "That includes"} the latest day, so some recent purchases may not be listed yet.`;
  el.innerHTML = `${icon("history")}<p>Still catching up: ${esc(which)} not been fully scanned yet.
    The SEC limits how fast filings can be downloaded, so each evening's update fills in more. ${esc(recent)}</p>`;
  el.hidden = false;
}

// A link to a company (insiders.html#co-SYM) or to the stakes lands on it once the feed is drawn, drawing further
// pages of the feed when the company is past the first.
function land() {
  const id = decodeURIComponent(location.hash.slice(1));
  if (!id) return;
  if (id.startsWith("co-") && !document.getElementById(id)) {
    const i = matching().findIndex((c) => `co-${slug(c.symbol || c.name)}` === id);
    if (i >= state.shown) {
      state.shown = (Math.floor(i / PAGE) + 1) * PAGE;
      renderFeed();
    }
  }
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: "start", behavior: "instant" });
  // The web font can arrive after this and change the height of everything above; land again unless the reader has
  // already scrolled.
  const y = scrollY;
  document.fonts?.ready.then(() => { if (Math.abs(scrollY - y) < 2) el.scrollIntoView({ block: "start", behavior: "instant" }); });
}

function start() {
  document.querySelectorAll("[data-window]").forEach((el) => { el.textContent = windowDays() ?? "30"; });
  explainTiers();
  sectors = [...new Set([...data.companies.map((c) => c.sector), ...(data.stakes || []).map((s) => s.sector)].filter(Boolean))].sort();
  // ?sector= names one of the sectors, in any case; anything else shows every sector and leaves the URL.
  if (state.sector !== "All") {
    const match = sectors.find((s) => s.toLowerCase() === state.sector.toLowerCase()) || null;
    state.sector = match || "All";
    setParam("sector", match);
  }
  if (params.has("tier")) setParam("tier", tierToUrl(state.tier));
  // Data from before the screener check has no "fit" on its entries (build.py gives every entry one, null where it
  // wasn't checked), so the page then leaves the check out rather than filter on it.
  fitChecked = [...data.companies, ...(data.stakes || [])].some((c) => "fit" in c);
  document.querySelectorAll("[data-fit-only]").forEach((el) => { el.hidden = !fitChecked; });
  if (!fitChecked) state.fit = "any";
  if (params.has("fit") && state.fit === "any") setParam("fit", null);
  renderProof();
  pendingNotice();
  paintControls();
  renderSectors();
  renderFeed();
  renderStakes();
  land();
}

function fail() {
  feed.setAttribute("aria-busy", "false");
  feed.innerHTML = `<div class="panel empty"><h3>Insider data is not available yet</h3><p>Filings appear after the first nightly update runs. If you expected them, reload the page in a minute.</p></div>`;
  countEl.textContent = "";
  stakesEl.innerHTML = `<div class="panel empty"><p>New 5%+ stakes appear with the insider data.</p></div>`;
  document.querySelectorAll("[data-proof]").forEach((el) => { el.textContent = "n/a"; });
  load("meta.json").then((m) => {
    const el = document.querySelector('[data-proof="date"]');
    if (el && m?.market_date) el.textContent = longDate(m.market_date);
  }).catch(() => {});
}

// meta.json only adds the window's dates and the catching-up notice, so the feed shows without it.
Promise.all([load("insiders.json"), load("meta.json").catch(() => null)]).then(([d, m]) => {
  data = d;
  meta = m;
  start();
}, fail);
