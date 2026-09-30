import { chrome, date, esc, load, localToday, money, pct, price, relDay, signClass } from "./common.js";

chrome();

const params = new URLSearchParams(location.search);
const FITS = ["any", "pass", "near"];
const state = { sector: params.get("sector") || "All", tier: "12", fit: FITS.includes(params.get("fit")) ? params.get("fit") : "any" };
const feed = document.querySelector("[data-feed]");
const stakesEl = document.querySelector("[data-stakes]");
let data, meta;

const TIER_TAG = { 1: `<span class="tag tag-solid">Tier 1</span>`, 2: `<span class="tag">Tier 2</span>`, 3: `<span class="tag" style="background:transparent;border:1px solid var(--line-2)">Smaller</span>` };

function role(roles) {
  if (!roles?.length) return "Insider";
  return roles.map((r) => r.replace(/Chief Executive Officer/i, "CEO").replace(/Chief Financial Officer/i, "CFO")
    .replace(/Chief Operating Officer/i, "COO")).join(", ");
}

// "a", "a and b", "a, b and c" (or "or").
const words = (xs, and = "and") => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ${and} ${xs[xs.length - 1]}`);

// The screener filter, in the words the empty states use.
const FIT_WORDS = { pass: "passes the screener", near: "passes the screener or misses it by one rule" };

// Whether a company clears the screener filter chosen above (screener.fit in the pipeline). "Passes or misses by one"
// takes a company whose figures miss no rule and that has one rule that could not be checked, since it can only pass
// or miss that one.
function fitOk(c) {
  if (state.fit === "any") return true;
  const f = c.fit;
  return f?.status === "pass" || (state.fit === "near" && (f?.status === "near" || (f?.status === "unconfirmed" && f.unchecked.length === 1)));
}

// One line on how the company measures against the screener's rules, with the figures behind it and why a rule could
// not be checked in its tooltip.
function fitLine(f) {
  if (!f) return "";
  if (f.status === "not_screened") return `<span class="fit-note">Screener: not screened (${esc(f.why)})</span>`;
  if (f.status === "no_data") return `<span class="fit-note">Screener: no yearly figures to check</span>`;
  const figures = [f.ps == null ? null : `price to sales ${f.ps.toFixed(2)}`,
    f.net_margin == null ? null : `profit margin ${pct(f.net_margin, 1)} without one-time items`,
    f.lt_de == null ? null : `long-term debt to equity ${f.lt_de.toFixed(2)}`].filter(Boolean).join(", ");
  const tip = [figures && `${figures[0].toUpperCase()}${figures.slice(1)}.`, f.why].filter(Boolean).join(" ");
  const title = tip ? ` title="${esc(tip)}"` : "";
  const unchecked = f.unchecked.length ? `${words(f.unchecked)} could not be checked` : "";
  if (f.status === "pass") return `<span class="tag tag-up fit"${title}>Passes the screener</span>`;
  if (f.status === "near") return `<span class="tag tag-mid fit"${title}>Screener: misses one rule, ${esc(f.misses[0])}</span>`;
  if (f.status === "unconfirmed") return `<span class="tag tag-mid fit"${title}>Screener: left out, ${esc(unchecked)}</span>`;
  return `<span class="fit-note"${title}>Screener: misses ${f.missed === 1 ? "one rule" : `${f.missed} rules`} (${esc(f.misses.join("; "))})${unchecked ? `, and ${esc(unchecked)}` : ""}</span>`;
}

function stake(b) {
  if (b.new_position) return `<span class="up">New</span><small>position</small>`;
  if (b.stake_increase == null) return `<span class="faint">n/a</span>`;
  const big = b.stake_increase >= data.tiers.tier2_stake;
  return `<span class="${big ? "up" : ""}">${pct(b.stake_increase, b.stake_increase < 0.1 ? 1 : 0, true)}</span><small>stake</small>`;
}

// Every share the buyer reported: in their own name, trusts, retirement accounts, family.
function owned(b) {
  if (b.owned_before == null) return "";
  if (b.new_position) return "Owned no shares before buying";
  const n = (x) => `${Math.round(x).toLocaleString("en-US")} ${Math.round(x) === 1 ? "share" : "shares"}`;
  return `Owned ${n(b.owned_before)} before buying${b.owned_after == null ? "" : ` and ${n(b.owned_after)} after`}, counting every account reported`;
}

function card(c) {
  const link = c.symbol ? `report.html?t=${encodeURIComponent(c.symbol)}` : null;
  const vs = c.vs_paid;
  return `<article class="buy">
    <div class="buy-co">
      <div class="top">${TIER_TAG[c.tier]}
        ${link ? `<a class="sym" href="${link}">${esc(c.symbol)}</a>` : `<span class="sym">${esc(c.symbol || "Unlisted")}</span>`}
        <span class="nm">${esc(c.name)}</span></div>
      <span class="faint" style="font-size:13px">${esc(c.sector)}${c.mcap ? `, ${money(c.mcap)} market value` : ""}</span>
      ${c.fit ? `<div class="fit-line">${fitLine(c.fit)}</div>` : ""}
      <div class="buy-stats">
        <div>Bought<b>${money(c.total_value)}</b></div>
        <div>Avg. price paid<b>${price(c.avg_price)}</b></div>
        <div>Price now<b class="${signClass(vs)}">${price(c.price)}${vs == null ? "" : ` <span style="font-size:13px">${pct(vs, 1, true)}</span>`}</b></div>
      </div>
    </div>
    <div class="buyers">
      <div class="buyers-head"><span>Buyer</span><span>Amount</span><span>Price paid</span><span class="stake">Stake change</span><span></span></div>
      ${c.buyers.map((b) => `<div class="buyer">
        <div class="who"><b title="${esc(b.name)}">${esc(b.name)}</b><span>${esc(role(b.roles))}, bought ${esc(date(b.last))}</span></div>
        <div class="n">${money(b.value)}${b.shares == null ? "" : `<small>${Math.round(b.shares).toLocaleString("en-US")} sh</small>`}</div>
        <div class="n">${price(b.avg_price)}</div>
        <div class="n stake"${owned(b) ? ` title="${esc(owned(b))}"` : ""}>${stake(b)}</div>
        <a class="doc" href="${esc(b.filing)}" target="_blank" rel="noopener" title="Open the SEC filing"><i class="ph ph-arrow-up-right" aria-hidden="true"></i><span class="sr-only">SEC filing</span></a>
      </div>`).join("")}
    </div>
  </article>`;
}

function renderSectors() {
  const el = document.querySelector("[data-sectors]");
  const counts = {};
  for (const c of data.companies) if (state.tier.includes(String(c.tier)) && fitOk(c)) counts[c.sector] = (counts[c.sector] || 0) + 1;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const names = Object.keys(counts).sort();
  if (state.sector !== "All" && !names.includes(state.sector)) names.push(state.sector);
  el.innerHTML = [["All", total], ...names.map((n) => [n, counts[n] || 0])].map(([n, k]) =>
    `<button class="pill" type="button" data-sector="${esc(n)}" aria-pressed="${n === state.sector}">${esc(n === "All" ? "All sectors" : n)}<span class="count">${k}</span></button>`).join("");
}

function render() {
  renderSectors();
  const rows = data.companies.filter((c) => state.tier.includes(String(c.tier)) && fitOk(c) && (state.sector === "All" || c.sector === state.sector));
  const today = localToday();
  const latest = rows.filter((c) => c.filed === meta.market_date).length;
  const span = meta.insider_pending?.length ? "in the days scanned so far" : `in the last ${data.window_days} days`;
  document.querySelector("[data-count]").innerHTML =
    `<b>${rows.length}</b> ${rows.length === 1 ? "company" : "companies"} in the last ${data.window_days} days` +
    (latest ? `, <b>${latest}</b> filed on ${esc(date(meta.market_date))}` : "");

  if (!rows.length) {
    // Only suggest widening a filter that is narrowed.
    const tries = [state.tier !== "123" && `"All purchases"`, state.fit !== "any" && `"Any company"`, state.sector !== "All" && "another sector"].filter(Boolean);
    feed.innerHTML = `<div class="panel empty"><h3>No purchases match</h3><p>No ${state.sector === "All" ? "" : esc(state.sector) + " "}company had
      ${state.tier === "1" ? "Tier 1" : state.tier === "12" ? "Tier 1 or 2" : "any"} open-market insider buying ${span}${state.fit === "any" ? "" : state.fit === "pass" ? " and passed the screener" : " and passed the screener or missed it by one rule"}.
      ${tries.length ? `Try ${words(tries, "or")}.` : ""}</p></div>`;
  } else {
    const days = new Map();
    for (const c of rows) {
      if (!days.has(c.filed)) days.set(c.filed, []);
      days.get(c.filed).push(c);
    }
    feed.innerHTML = [...days.entries()].map(([d, list]) => `
      <section class="day-group">
        <div class="day-head"><h2>${esc(relDay(d, today))}</h2><span>${["Today", "Yesterday"].includes(relDay(d, today)) ? esc(date(d, { weekday: "long", month: "long", day: "numeric" })) + ". " : ""}${list.length} ${list.length === 1 ? "company" : "companies"}</span></div>
        <div class="buy-list">${list.map(card).join("")}</div>
      </section>`).join("");
  }

  const inSector = (data.stakes || []).filter((s) => state.sector === "All" || s.sector === state.sector);
  const stakes = inSector.filter(fitOk);
  const where = state.sector === "All" ? "" : "in " + esc(state.sector) + " ";
  // When the screener filter hides every stake, say so rather than that none were filed.
  const hidden = inSector.length === 1
    ? `The new 5%+ stake filed ${where}${span} is not in a company that ${FIT_WORDS[state.fit]}. Choose "Any company" to see it.`
    : `None of the ${inSector.length} new 5%+ stakes filed ${where}${span} is in a company that ${FIT_WORDS[state.fit]}. Choose "Any company" to see them.`;
  stakesEl.innerHTML = stakes.length ? `<div class="stakes">${stakes.slice(0, 60).map((s) => `
    <div class="stake-row">
      <div><div class="sym">${s.symbol ? `<a href="report.html?t=${encodeURIComponent(s.symbol)}">${esc(s.symbol)}</a>` : "Unlisted"}</div><div class="faint" style="font-size:12.5px">${esc(date(s.filed))}</div></div>
      <div><div>${esc(s.filer)}</div><div class="faint" style="font-size:13px">in ${esc(s.name)}</div>${s.fit ? `<div class="fit-line">${fitLine(s.fit)}</div>` : ""}</div>
      <div class="pct">${s.percent == null ? "n/a" : s.percent.toFixed(1) + "%"}</div>
      <div class="purpose">${esc(s.purpose || "No stated purpose in the filing.")}</div>
      <a class="doc" href="${esc(s.filing)}" target="_blank" rel="noopener" title="Open the SEC filing"><i class="ph ph-arrow-up-right" aria-hidden="true"></i><span class="sr-only">SEC filing</span></a>
    </div>`).join("")}</div>`
    : `<div class="panel empty"><p>${inSector.length ? hidden : `No new 5%+ stakes were filed ${where}${span}.`}</p></div>`;
}

document.querySelector("[data-sectors]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sector]");
  if (!b) return;
  state.sector = b.dataset.sector;
  const u = new URL(location);
  state.sector === "All" ? u.searchParams.delete("sector") : u.searchParams.set("sector", state.sector);
  history.replaceState(null, "", u);
  render();
});
document.querySelectorAll("[data-fit]").forEach((x) => x.setAttribute("aria-pressed", x.dataset.fit === state.fit));
document.querySelector("[data-fits]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-fit]");
  if (!b || !data) return;
  state.fit = b.dataset.fit;
  document.querySelectorAll("[data-fit]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  const u = new URL(location);
  state.fit === "any" ? u.searchParams.delete("fit") : u.searchParams.set("fit", state.fit);
  history.replaceState(null, "", u);
  render();
});
document.querySelector("[data-tiers]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-tier]");
  if (!b) return;
  state.tier = b.dataset.tier;
  document.querySelectorAll("[data-tier]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  render();
});

function pendingNotice() {
  const el = document.querySelector("[data-pending]");
  const p = [...(meta.insider_pending || [])].sort().reverse();
  if (!p.length) return;
  const oldest = p[p.length - 1], newest = p[0];
  const which = p.length === 1 ? `${date(newest)} has` : `${p.length} weekdays between ${date(oldest)} and ${date(newest)} have`;
  // Only claim the recent days are complete when the newest unscanned day is older than the latest one.
  const recent = newest < meta.market_date
    ? `Everything filed after ${date(newest)} has been checked.`
    : `${p.length === 1 ? "That is" : "That includes"} the latest day, so some recent purchases may not be listed yet.`;
  el.innerHTML = `<i class="ph ph-clock-counter-clockwise" aria-hidden="true"></i>
    <span>Still catching up: ${esc(which)} not been fully scanned yet.
    The SEC limits how fast filings can be downloaded, so each evening's update fills in more. ${esc(recent)}</span>`;
  el.hidden = false;
}

Promise.all([load("insiders.json"), load("meta.json")]).then(([d, m]) => {
  data = d;
  meta = m;
  document.querySelectorAll("[data-window]").forEach((el) => (el.textContent = d.window_days));
  // Data from before the screener check has no "fit" on its entries (build.py gives every entry one, null where it
  // wasn't checked), so the page then leaves the check out rather than filter on it.
  if ([...d.companies, ...(d.stakes || [])].some((c) => "fit" in c)) document.querySelectorAll("[data-fit-only]").forEach((el) => (el.hidden = false));
  else state.fit = "any";
  pendingNotice();
  render();
}).catch(() => {
  feed.innerHTML = `<div class="panel empty"><h3>Insider data is not available yet</h3><p>Filings appear after the first nightly update runs.</p></div>`;
});
