import { chrome, displayName, esc, icon, load, longDate, money, pct, tape, times } from "./common.js";
import { debtTag, flagDefs as sharedFlagDefs, flagIcons, flagTile, ledger, under } from "./ledger.js";

chrome();

const params = new URLSearchParams(location.search);
const INSIDER_FILTERS = ["any", "buying", "below"];
// Each sort's order is fixed: a score from best to worst, a ratio from cheapest, a margin from highest.
const SORT_NAMES = ["score", "ps", "pb", "net_margin", "fcf_yield", "drawdown", "mcap"];
const state = {
  sector: params.get("sector") || "All",
  insider: INSIDER_FILTERS.includes(params.get("insider")) ? params.get("insider") : "any",
  sort: SORT_NAMES.includes(params.get("sort")) ? params.get("sort") : "score",
  q: (params.get("q") || "").trim(), // the panel's filter by ticker or company name
  open: null,
};
const host = document.querySelector("[data-results]");
const filterEl = document.querySelector("[data-filter]");
const countEl = document.querySelector("[data-count]");
const sortEl = document.querySelector("[data-sort]");
const resetEl = document.querySelector("[data-reset]");
let data;
let insiderDays = null; // the insider scan's window (meta.json), for "in the last 30 days"
let insiderPending = 0; // the insider scan's days not yet read (meta.json), which the filter's empty state names

// Missing values sort last whichever way a column sorts. Ties keep the score's order (the sort is stable).
const low = (v) => v ?? Number.MAX_VALUE;
const high = (v) => v ?? -Number.MAX_VALUE;
const SORTS = {
  score: (a, b) => b.score - a.score,
  ps: (a, b) => a.ps - b.ps,
  pb: (a, b) => low(a.pb) - low(b.pb),
  net_margin: (a, b) => b.net_margin - a.net_margin,
  fcf_yield: (a, b) => high(b.fcf_yield) - high(a.fcf_yield),
  drawdown: (a, b) => high(b.drawdown) - high(a.drawdown),
  mcap: (a, b) => a.mcap - b.mcap,
};
const SORT_DIR = { score: "descending", ps: "ascending", pb: "ascending", net_margin: "descending", fcf_yield: "descending", drawdown: "descending", mcap: "ascending" };

// Older data (a screener.json from before a feature) lacks its keys, and the page hides what it can't show.
const hasPb = () => data.results.some((r) => "pb" in r);
const hasInsiders = () => data.insiders_checked === true;
const ofDrawdown = () => pct(data.rules?.out_of_favor_drawdown ?? 0.25, 0);
const window_ = () => (insiderDays ? `in the last ${insiderDays} days` : "recently");

// The ledger an opened row unfolds, shared with the ticker report (ledger.js).
const ctx = () => ({ rules: data.rules, industries: data.industries, insiderDays });
const flagDefs = () => sharedFlagDefs(ctx());
const flags = (r) => flagIcons(r, ctx());

// The flags any company on the list carries, each icon beside its name: the same whichever sector or filter is shown,
// so the table doesn't move when they change.
function flagLegend() {
  const used = flagDefs().filter(([k]) => data.results.some((r) => r.flags.includes(k)));
  return used.length ? `<ul class="flag-legend" aria-label="What the flag icons mean">${used.map((d) => `<li>${flagTile(d)}<span>${esc(d[3])}</span></li>`).join("")}</ul>` : "";
}

const detailId = (sym) => `ledger-${sym.replace(/[^A-Za-z0-9]/g, "-")}`;
function detailRow(r, cols) {
  return `<tr class="detail" id="${detailId(r.symbol)}"><td colspan="${cols}">${ledger(r, ctx())}</td></tr>`;
}

// ---- Filters, sort and the table ----

// The insider filter above the list (Tier 1 or 2 buying, and a price under what insiders paid).
const insiderOk = (r) => state.insider === "any"
  || (state.insider === "buying" ? r.flags.includes("insiders_buying") : r.flags.includes("below_insider_price"));
// What the filter asks, after "and" in the count (for one company, then for several). Only Tier 1 or 2 buying counts:
// a company with smaller purchases (Tier 3) is on neither side of the filter's claim.
const INSIDER_WORDS = {
  buying: ["has Tier 1 or 2 insider buying", "have Tier 1 or 2 insider buying"],
  below: ["trades below what Tier 1 or 2 insiders paid", "trade below what Tier 1 or 2 insiders paid"],
};

// The panel's filter: a ticker that starts with what is typed, or a company name that contains it.
const queryOk = (r) => {
  if (!state.q) return true;
  const q = state.q.toLowerCase();
  return r.symbol.toLowerCase().startsWith(q) || displayName(r.name).toLowerCase().includes(q) || String(r.name).toLowerCase().includes(q);
};

function setParam(key, value) {
  const u = new URL(location);
  value == null ? u.searchParams.delete(key) : u.searchParams.set(key, value);
  history.replaceState(null, "", u);
}

function paintReset() {
  resetEl.hidden = state.sector === "All" && state.insider === "any" && state.sort === "score" && !state.q;
}

function renderSectors() {
  const el = document.querySelector("[data-sectors]");
  // With the insider filter on, each sector counts the companies the list would show.
  const count = (name) => (state.insider === "any"
    ? (name === "All" ? data.results.length : data.sectors.find((s) => s.name === name)?.passed ?? 0)
    : data.results.filter((r) => insiderOk(r) && (name === "All" || r.sector === name)).length);
  const list = [{ name: "All" }, ...data.sectors.filter((s) => s.checked > 0 || s.excluded)];
  el.innerHTML = list.map((s) => {
    const n = s.excluded ? null : count(s.name);
    const label = s.name === "All" ? "All sectors" : s.name;
    return `<button class="pill${n ? "" : " pill-empty"}" type="button" data-sector="${esc(s.name)}" aria-pressed="${s.name === state.sector}">
    ${esc(label)}<span class="count"><span class="sr-only">, </span>${n == null ? "<span class=\"sr-only\">not screened</span><span aria-hidden=\"true\">n/a</span>" : `${n}<span class="sr-only"> passed</span>`}</span></button>`;
  }).join("");
  el.onclick = (e) => {
    const b = e.target.closest("[data-sector]");
    if (!b) return;
    state.sector = b.dataset.sector;
    state.open = null;
    setParam("sector", state.sector === "All" ? null : state.sector);
    renderSectors();
    paintQuick();
    renderTable();
    // The pills are drawn again, so keyboard focus goes back to the one chosen rather than to the page.
    el.querySelector(`[data-sector="${CSS.escape(state.sector)}"]`)?.focus();
  };
  // On a phone the sectors scroll sideways: bring the chosen one into view without moving the page.
  const on = el.querySelector('[aria-pressed="true"]');
  if (on && el.scrollWidth > el.clientWidth) el.scrollLeft = Math.max(0, on.offsetLeft - el.offsetLeft - 16);
  paintReset();
}

// Each quick filter's count: the companies it would show in the sector chosen, before the panel's text filter.
function paintQuick() {
  if (!hasInsiders()) return;
  const inSector = data.results.filter((r) => state.sector === "All" || r.sector === state.sector);
  const n = { any: inSector.length, buying: inSector.filter((r) => r.flags.includes("insiders_buying")).length,
    below: inSector.filter((r) => r.flags.includes("below_insider_price")).length };
  document.querySelectorAll("[data-insider-filter] [data-insider]").forEach((b) => {
    const k = b.dataset.insider;
    b.classList.toggle("pill-empty", !n[k]);
    b.querySelector("[data-quick-count]").innerHTML = `<span class="sr-only">, </span>${n[k]}<span class="sr-only"> ${n[k] === 1 ? "company" : "companies"}</span>`;
  });
}

function renderInsiderFilter() {
  const row = document.querySelector("[data-insider-only]");
  if (!hasInsiders()) {
    state.insider = "any";
    setParam("insider", null);
    return;
  }
  row.hidden = false;
  const el = row.querySelector("[data-insider-filter]");
  const paint = () => el.querySelectorAll("[data-insider]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.insider === state.insider));
  paint();
  paintQuick();
  el.onclick = (e) => {
    const b = e.target.closest("[data-insider]");
    if (!b) return;
    state.insider = b.dataset.insider;
    state.open = null;
    setParam("insider", state.insider === "any" ? null : state.insider);
    paint();
    renderSectors();
    renderTable();
  };
}

function setSort(key) {
  if (!SORTS[key] || (key === "pb" && !hasPb())) return;
  state.sort = key;
  state.open = null;
  sortEl.value = key;
  setParam("sort", key === "score" ? null : key);
  paintReset();
  renderTable();
}

// Companies that passed every other check but were left out because a check could not be confirmed on this run:
// their US registration ("us") or their debt ("debt"), each listed in "unconfirmed" with its reason in "reason".
// Older data lists only registration problems, as symbols or as objects without "unconfirmed".
const its = (n) => (n === 1 ? "its" : "their");
const isLeftOut = (n) => (n === 1 ? "it is left out" : "they are left out");
const UNCONFIRMED = {
  us: (n) => `passed every financial check, but ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
  debt: (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings, so the low-debt rule could not be confirmed and ${isLeftOut(n)}`,
  "debt us": (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings and ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
};
const andList = (a) => (a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
const symLink = (s, title) => `<a class="mono" href="report.html?t=${encodeURIComponent(s)}"${title ? ` title="${esc(title)}"` : ""}>${esc(s)}</a>`;

function unverifiedNote() {
  const list = (data.unverified || []).map((u) => (typeof u === "string" ? { symbol: u } : u))
    .filter((u) => u && u.symbol && (state.sector === "All" || !u.sector || u.sector === state.sector));
  if (!list.length || state.insider !== "any") return "";
  const groups = new Map();
  for (const u of list) {
    const key = Array.isArray(u.unconfirmed) && u.unconfirmed.length ? [...u.unconfirmed].sort().join(" ") : "us";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(u);
  }
  const order = ["us", "debt", "debt us"];
  const keys = [...groups.keys()].sort((a, b) => (order.indexOf(a) + 1 || 9) - (order.indexOf(b) + 1 || 9));
  const sentences = keys.map((key) => {
    const g = groups.get(key);
    const n = g.length;
    const links = andList(g.map((u) => symLink(u.symbol, [u.name && u.name.replace(/\.$/, ""), u.reason].filter(Boolean).join(". "))));
    const why = UNCONFIRMED[key] ? UNCONFIRMED[key](n)
      : `passed every other check, but one check could not be confirmed today, so ${isLeftOut(n)}`;
    // A registration lookup that failed today may work tomorrow. A debt reading changes only with new filings.
    const again = key.split(" ").includes("us") ? " The next update checks again." : "";
    return `${n} ${n === 1 ? "company" : "companies"} ${why}: ${links}.${again}`;
  });
  return `<div class="notice unverified">${icon("info")}
    <div>${sentences.map((s) => `<p>${s}</p>`).join("")}</div></div>`;
}

// The ranked table's columns: [key, heading, the sort it sets or null, its label on a phone].
const COLUMNS = () => [
  ["rank", `<span class="sr-only">Rank</span>#`, null],
  ["co", "Company", null],
  ["score", "Score", "score"],
  ["flags", "Flags", null],
  ["mcap", "Market value", "mcap", "Value"],
  ["ps", "Price / sales", "ps", "P/S"],
  ...(hasPb() ? [["pb", "Price / book", "pb", "P/B"]] : []),
  ["margin", "Net margin", "net_margin", "Margin"],
  ["debt", "Debt", null, "Debt"],
  ["fcf", "Cash yield", "fcf_yield", "Cash yield"],
  ["dd", "Below high", "drawdown", "Below high"],
  ["an", "Analysts", null, "Analysts"],
  ["go", `<span class="sr-only">Report</span>`, null],
];

function head() {
  return `<thead><tr>${COLUMNS().map(([k, h, s]) => {
    const cls = ["co", "score", "flags"].includes(k) ? ` class="txt c-${k}"` : ` class="c-${k}"`;
    if (!s) return `<th scope="col"${cls}${k === "debt" ? ` title="No debt, more cash than debt (net cash), or long-term debt as a multiple of equity"` : ""}>${h}</th>`;
    const on = state.sort === s;
    return `<th scope="col"${cls}${on ? ` aria-sort="${SORT_DIR[s]}"` : ""}><button class="sort" type="button" data-sort-key="${s}">${h}${icon(on ? (SORT_DIR[s] === "descending" ? "sort-down" : "sort-up") : "sort")}<span class="sr-only">${on ? ", sorted" : ", sort"}</span></button></th>`;
  }).join("")}</tr></thead>`;
}

function row(r, i) {
  const sym = esc(r.symbol);
  const labelled = (k, label, v, extra = "") => `<td class="c-${k}" data-label="${label}"${extra}>${v}</td>`;
  return `<tr class="row" data-sym="${sym}">
    <td class="c-rank">${i + 1}</td>
    <td class="txt c-co"><button class="row-toggle" type="button" aria-expanded="${state.open === r.symbol}" aria-controls="${detailId(r.symbol)}">
      ${icon("caret-right", "caret")}<span class="co"><span class="sym">${sym}</span> <span class="nm" title="${esc(r.name)}">${esc(displayName(r.name))}</span></span></button></td>
    <td class="txt c-score"><div class="score" role="img" aria-label="Score ${esc(String(r.score))} out of 100"><b>${esc(String(r.score))}</b>${tape(r.score_parts)}</div></td>
    <td class="txt c-flags">${flags(r)}</td>
    ${labelled("mcap", "Value", money(r.mcap))}
    ${labelled("ps", "P/S", times(r.ps))}
    ${hasPb() ? labelled("pb", "P/B", under(r.pb)) : ""}
    ${labelled("margin", "Margin", pct(r.net_margin), r.one_time_note && r.net_margin_reported != null ? ` title="Leaves out one-time items. Reported margin: ${pct(r.net_margin_reported)}"` : "")}
    ${labelled("debt", "Debt", debtTag(r))}
    ${labelled("fcf", "Cash yield", pct(r.fcf_yield))}
    ${labelled("dd", "Below high", r.drawdown == null ? "n/a" : pct(r.drawdown, 0))}
    ${labelled("an", "Analysts", r.analysts ?? "n/a")}
    <td class="c-go"><a class="go" href="report.html?t=${encodeURIComponent(r.symbol)}" aria-label="Open the ${sym} report">${icon("arrow-up-right")}</a></td>
  </tr>`;
}

function renderTable() {
  host.setAttribute("aria-busy", "false");
  // The list the sector and the quick filter leave, then the rows the panel's text filter keeps of it.
  const listed = data.results.filter((r) => (state.sector === "All" || r.sector === state.sector) && insiderOk(r)).sort(SORTS[state.sort]);
  const rows = listed.filter(queryOk);
  const sec = data.sectors.find((s) => s.name === state.sector);
  const which = state.insider === "any" ? "" : ` and ${INSIDER_WORDS[state.insider][listed.length === 1 ? 0 : 1]}`;
  countEl.innerHTML = sec?.excluded ? `${esc(state.sector)} is not screened`
    : state.q ? `<b>${rows.length}</b> of the ${listed.length} ${state.sector === "All" ? (listed.length === 1 ? "company" : "companies")
      : `${esc(state.sector)} ${listed.length === 1 ? "stock" : "stocks"}`} that passed every check${which} ${rows.length === 1 ? "matches" : "match"} "${esc(state.q)}"`
    : state.sector === "All" ? `<b>${rows.length}</b> ${rows.length === 1 ? "company" : "companies"} passed every check${which}`
    : `<b>${rows.length}</b> of ${sec?.checked ?? 0} ${esc(state.sector)} stocks passed every check${which}`;
  // The flag icons' legend sits on the count's line, beside the table it explains.
  document.querySelector("[data-result-meta]").innerHTML = rows.length && !sec?.excluded ? flagLegend() : "";

  if (sec?.excluded) {
    const see = Array.isArray(data.financials_list) ? ` <a href="#financials">See the banks and insurers under book value below.</a>` : "";
    host.innerHTML = `<div class="panel empty"><h2>${esc(state.sector)} companies are not screened</h2><p>${esc(sec.excluded)}${see}</p></div>`;
    return;
  }

  if (listed.length && !rows.length) {
    // Only the text filter empties the list: say so, and point to the report, which covers every listed stock.
    const ticker = /^[A-Za-z][A-Za-z.]{0,5}$/.test(state.q) ? state.q.toUpperCase() : null;
    host.innerHTML = `<div class="panel empty"><h2>No company on this list matches "${esc(state.q)}"</h2>
      <p>The filter looks at the tickers and company names of the ${listed.length} ${listed.length === 1 ? "company" : "companies"} on it.${ticker
        ? ` To see how ${esc(ticker)} looks against everything else, <a href="report.html?t=${encodeURIComponent(ticker)}">open its report</a>.` : ""}</p>
      <p class="empty-act"><button class="btn btn-ghost btn-sm" type="button" data-clear-filter>Clear the filter</button></p></div>`;
    return;
  }
  if (!rows.length) {
    const passing = state.sector === "All" ? "the companies that pass" : `the ${esc(state.sector)} companies that pass`;
    const notYet = insiderPending ? ` (${insiderPending} filing day${insiderPending === 1 ? " is" : "s are"} not scanned yet)` : "";
    host.innerHTML = state.insider !== "any"
      ? `<div class="panel empty"><h2>No ${state.sector === "All" ? "" : `${esc(state.sector)} `}company on the list ${INSIDER_WORDS[state.insider][0]}</h2>
        <p>None of ${passing} today had Tier 1 or 2 insider buying ${esc(window_())}${state.insider === "below" ? " at an average price above today's price" : ""}${notYet}.
        Try "Any company"${state.sector === "All" ? "" : " or another sector"}.</p></div>`
      : `<div class="panel empty"><h2>No ${esc(state.sector)} company passes today</h2>
      <p>That is normal. The checks are strict on purpose, and some sectors rarely have cheap, debt-light, profitable small caps.
      Try another sector or come back after the next update.</p></div>${unverifiedNote()}`;
    return;
  }
  const cols = COLUMNS().length;
  host.innerHTML = `<div class="table-wrap"><table class="data screen-table">
    <caption class="sr-only">Companies that passed every check, ${esc(sortEl.selectedOptions[0]?.textContent || "best score")} first. Open a company to see its score and rules.</caption>
    ${head()}
    <tbody>${rows.map((r, i) => row(r, i) + (state.open === r.symbol ? detailRow(r, cols) : "")).join("")}</tbody></table></div>
    <p class="table-note">Open a row to see how its score adds up and its figure for each rule. Cash yield is free cash flow as a share of market value.${hasPb() ? " Price to book is market value over book value: shareholders' equity less preferred stock." : ""}</p>
    ${unverifiedNote()}`;
  watch(host.querySelector(".table-wrap"));
}

// Opens or closes one row's ledger in place, leaving the rest of the table (and its scroll position) as it is.
function toggle(sym) {
  const tbody = host.querySelector("tbody");
  if (!tbody) return;
  const prev = state.open;
  tbody.querySelector("tr.detail")?.remove();
  tbody.querySelectorAll("tr.row .row-toggle[aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  state.open = prev === sym ? null : sym;
  if (!state.open) return;
  const tr = tbody.querySelector(`tr.row[data-sym="${CSS.escape(sym)}"]`);
  const r = data.results.find((x) => x.symbol === sym);
  if (!tr || !r) return;
  tr.querySelector(".row-toggle").setAttribute("aria-expanded", "true");
  tr.insertAdjacentHTML("afterend", detailRow(r, COLUMNS().length));
  fitLedger();
}

// A table wider than its box scrolls sideways, its right edge fading while there is more to see. An opened row's ledger
// keeps to the box's width rather than running the table's whole width. One observer serves every redraw.
let observed = null;
const ro = window.ResizeObserver ? new ResizeObserver(() => observed && edge(observed)) : null;
function edge(wrap) {
  const scrolls = wrap.scrollWidth > wrap.clientWidth + 1 || wrap.querySelector("table").offsetWidth > wrap.clientWidth + 1;
  wrap.classList.toggle("scrolls", scrolls);
  wrap.classList.toggle("more-right", scrolls && wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 1);
  fitLedger();
}
function fitLedger() {
  const wrap = host.querySelector(".table-wrap");
  const inner = wrap?.querySelector(".ledger");
  if (inner) inner.style.width = wrap.classList.contains("scrolls") ? `${wrap.clientWidth - 2}px` : "";
}
function watch(wrap) {
  observed = wrap;
  wrap.addEventListener("scroll", () => edge(wrap), { passive: true });
  ro?.observe(wrap);
  edge(wrap);
}

host.addEventListener("click", (e) => {
  const sortBtn = e.target.closest("[data-sort-key]");
  if (sortBtn) { setSort(sortBtn.dataset.sortKey); host.querySelector(`[data-sort-key="${sortBtn.dataset.sortKey}"]`)?.focus(); return; }
  if (e.target.closest("a, .ledger")) return;
  const tr = e.target.closest("tr.row");
  if (tr) toggle(tr.dataset.sym);
});

// ---- The lists under the ranked one ----

// Each is hidden in the page and shown only when the data has it, so a screener.json from before these lists leaves
// them out. A long list shows its first SHOWN rows and a button for the rest.
const SHOWN = 15;
const coCell = (r) => `<td class="txt co-cell"><div class="co"><a class="sym" href="report.html?t=${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a> <span class="nm" title="${esc(r.name)}">${esc(displayName(r.name))}</span></div></td>`;
const asOf = (iso) => (iso ? longDate(iso) : "n/a");

// A list wider than its box scrolls sideways too, its right edge fading while there is more to see. Each list keeps its
// own frame, so the lists share this observer rather than the ranked table's one. It also watches the table, which
// "Show all" can widen.
const listRo = window.ResizeObserver ? new ResizeObserver((es) => es.forEach((e) => fade(e.target.closest(".list-wrap")))) : null;
function fade(wrap) {
  wrap.classList.toggle("more-right", wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 1);
}

function section(key, body, count) {
  const el = document.querySelector(`[data-section="${key}"]`);
  el.hidden = false;
  el.querySelector("[data-body]").innerHTML = body;
  const wrap = el.querySelector(".list-wrap");
  if (wrap) {
    wrap.addEventListener("scroll", () => fade(wrap), { passive: true });
    listRo?.observe(wrap);
    listRo?.observe(wrap.querySelector("table"));
    fade(wrap);
  }
  const c = el.querySelector("[data-sec-count]");
  if (c && count != null) c.textContent = typeof count === "number" ? `${count} ${count === 1 ? "company" : "companies"}` : `${count} industries`;
  const more = el.querySelector("[data-more]");
  if (more) {
    more.onclick = () => {
      const extra = el.querySelectorAll("tr[data-extra]");
      const opening = more.getAttribute("aria-expanded") !== "true";
      extra.forEach((tr) => { tr.hidden = !opening; });
      more.setAttribute("aria-expanded", String(opening));
      more.textContent = opening ? "Show fewer" : more.dataset.label;
      if (opening) {
        // Keep keyboard focus in the list, at the first row it revealed: on its link, or on the row itself where it has
        // none (the industries), since the button has moved far down the page.
        const first = extra[0], link = first?.querySelector("a");
        if (link) link.focus();
        else if (first) { first.tabIndex = -1; first.focus(); }
      } else {
        more.scrollIntoView({ block: "nearest" }); // a long list closing above the button would leave it far up the page
      }
    };
  }
  index[key] = count;
}

// head: the column names. The first column (the company or industry) and those numbered in `text` hold words, aligned
// left (class "txt"); the rest hold figures, aligned right.
function listTable(caption, head, rows, cell, note = "", text = []) {
  const extra = rows.length - SHOWN;
  return `<div class="table-wrap list-wrap"><table class="data list">
    <caption class="sr-only">${esc(caption)}</caption>
    <thead><tr>${head.map((h, i) => `<th scope="col"${i === 0 || text.includes(i) ? ` class="txt"` : ""}>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr${i >= SHOWN ? " data-extra hidden" : ""}>${cell(r)}</tr>`).join("")}</tbody></table></div>
    ${extra > 0 ? `<button class="btn btn-ghost btn-sm list-more" type="button" data-more aria-expanded="false" data-label="Show all ${rows.length}">Show all ${rows.length}</button>` : ""}${note}`;
}

function unconfirmedNote(syms, what) {
  if (!syms?.length) return "";
  return `<div class="notice unverified">${icon("info")}<p>${syms.length} more ${syms.length === 1 ? "company meets" : "companies meet"} ${what}, but where ${syms.length === 1 ? "it is" : "they are"} incorporated or based could not be confirmed with the SEC today: ${andList(syms.map((s) => symLink(s)))}. The next update checks again.</p></div>`;
}

function renderIndustries() {
  if (!Array.isArray(data.industries)) return;
  const out = data.industries.filter((x) => x.out_of_favor);
  const market = data.market_median_ps;
  // The marking is named only where a table shows it. Each count goes as a string, which section() counts in industries.
  const lead = (marked) => `<p class="list-note">${data.industries.length} industries measured${market != null ? `. The whole market's median price to sales is ${times(market)}${marked ? `; an industry whose median is under it is marked "under market"` : ""}` : ""}.</p>`;
  if (!data.industries.length) {
    section("industries", `<div class="panel empty"><h3>Industries could not be measured today</h3><p>Too few companies had a year of prices loaded to measure any industry.</p></div>`, "0");
    return;
  }
  if (!out.length) {
    section("industries", `<div class="panel empty"><h3>No industry is out of favor today</h3><p>In every industry measured, the median company trades less than ${ofDrawdown()} below its 52-week high.</p></div>${lead(false)}`, `0 of ${data.industries.length}`);
    return;
  }
  // An industry cheaper than the whole market says so in words, not only in green.
  const cheap = (x) => x.median_ps != null && market != null && x.median_ps < market;
  section("industries", `${listTable("Industries out of favor", ["Industry", "Companies", "Median below high", "Median price / sales"], out, (x) => `
    <td class="txt ind-cell"><b>${esc(x.industry)}</b><span>${esc(x.sector)}</span></td><td>${x.count}</td><td>${pct(x.median_drawdown, 0)}</td>
    <td${cheap(x) ? ` class="up"` : ""}>${cheap(x) ? `<span class="cell-sub">under market</span>` : ""}${times(x.median_ps)}</td>`)}${lead(true)}`, `${out.length} of ${data.industries.length}`);
}

function runway(r) {
  if (r.runway_years != null) {
    const n = Math.max(1, Math.round(r.runway_years * 12));
    return r.runway_years < 1 ? `${n} month${n === 1 ? "" : "s"}` : `${r.runway_years.toFixed(1)} years`;
  }
  return r.runway_note === "not_burning" ? `<span class="faint">Not burning cash</span>`
    : r.runway_note === "stale" ? `<span class="faint" title="Its latest fiscal year of figures (${esc(String(r.fiscal_year ?? ""))}) ended more than 15 months before its balance sheet, so its cash burn is out of date">Out of date</span>`
    : `<span class="faint" title="Its free cash flow could not be read from its filings">Unknown</span>`;
}

function renderNetCash() {
  if (!Array.isArray(data.net_cash_list)) return;
  const rows = data.net_cash_list;
  const note = unconfirmedNote(data.net_cash_unconfirmed, "the other tests");
  if (!rows.length) {
    section("net_cash", `<div class="panel empty"><h3>No company is worth less than its net cash today</h3><p>None of the US companies checked has a market value under its cash and short-term investments minus all debt, preferred stock and outside stakes in its subsidiaries.</p></div>${note}`, 0);
    return;
  }
  // Each list's ratio is worked out from its figures, which are stored to the dollar. The stored ratio is rounded to 4
  // places, which can lift one just under 1 to 1.00 (a price to book of 0.99996), so under() couldn't cut it to 0.99x.
  section("net_cash", listTable("Companies worth less than their net cash", ["Company", "Market value", "Net cash", "Value / net cash", "Free cash flow", "Cash runway", "Balance sheet"], rows, (r) => `
    ${coCell(r)}<td>${money(r.mcap)}</td><td>${money(r.net_cash)}</td><td>${under(r.mcap / r.net_cash)}</td>
    <td class="${r.fcf != null && r.fcf < 0 ? "down" : ""}">${money(r.fcf)}</td><td>${runway(r)}</td><td>${esc(asOf(r.balance_as_of))}</td>`,
  `<p class="list-note">Cheapest against net cash first. Free cash flow is for the latest fiscal year; cash and debt are from the balance sheet dated on the right. "Not burning cash" means free cash flow was zero or more in its latest fiscal year.</p>${note}`), rows.length);
}

function renderFinancials() {
  if (!Array.isArray(data.financials_list)) return;
  const rows = data.financials_list;
  const note = unconfirmedNote(data.financials_unconfirmed, "the other tests");
  if (!rows.length) {
    section("financials", `<div class="panel empty"><h3>No profitable bank or insurer trades under book value today</h3></div>${note}`, 0);
    return;
  }
  // Price to book from its figures, as for net cash above.
  section("financials", listTable("Profitable banks and insurers under book value", ["Company", "Kind", "Market value", "Book used", "Price / book", "Profit last year", "Return on equity"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.kind === "bank" ? "Bank" : r.industry || "Financial")}</td><td>${money(r.mcap)}</td>
    <td>${r.book_basis === "tangible" ? "Tangible" : "Book"}, ${money(r.book)}</td>
    <td>${under(r.mcap / r.book)}</td><td>${money(r.net_income)}</td><td>${pct(r.roe)}</td>`,
  `<p class="list-note">Lowest price to book first. A bank is measured on tangible book value (equity less preferred stock, goodwill and other intangible assets), any other on book value (equity less preferred stock). Profit and return on equity leave out one-time items and are what is left to common shareholders, after preferred dividends.</p>${note}`, [1]), rows.length);
}

function renderGiants() {
  if (!Array.isArray(data.giants)) return;
  const rows = data.giants;
  if (!rows.length) {
    section("giants", `<div class="panel empty"><h3>No company worth over $90 billion trades above 10 times its sales today</h3></div>`, 0);
    return;
  }
  // Older data has no sales_basis: its sales were the latest fiscal year's.
  const period = (r) => (r.sales_basis === "ttm" ? `the 12 months to ${asOf(r.sales_end)}` : `fiscal ${r.fiscal_year ?? "year"}`);
  section("giants", listTable("Companies worth over $90 billion at more than 10 times sales", ["Company", "Sector", "Market value", "Sales", "Price / sales"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.sector)}</td><td>${money(r.mcap)}</td><td><span class="cell-sub">${esc(period(r))}</span>${money(r.revenue)}</td><td class="down">${times(r.ps)}</td>`,
  `<p class="list-note">Highest price to sales first, on each company's sales over the last 12 months where its quarterly reports give them, otherwise its latest fiscal year's.</p>`, [1]), rows.length);
}

// The rail of links to the lists under the ranked one, each with its count.
const index = {};
function renderIndex() {
  const el = document.querySelector("[data-index]");
  const items = [["how", "How the list is made", null], ["industries", "Industries out of favor", index.industries],
    ["net-cash", "Under 1x cash", index.net_cash], ["financials", "Banks and insurers under book value", index.financials], ["giants", "Overpriced giants", index.giants]]
    .filter(([id]) => !document.getElementById(id)?.hidden);
  el.innerHTML = `<span class="index-label">On this page</span>${items.map(([id, label, n]) => `<a href="#${id}">${esc(label)}${n != null ? `<span class="count">${n}</span>` : ""}</a>`).join("")}`;
  el.hidden = false;
}

// ---- Explanations wired to the data the page was built with ----

// The rules from the data (8% margin in data from before September 2026), the newer flags and the score's weights only
// where the data has them.
function explainRules() {
  const R = data.rules || {};
  const margin = R.min_net_margin;
  if (margin != null) {
    const n = Math.round(margin * 10000) / 100;
    document.querySelectorAll("[data-margin-pct]").forEach((el) => { el.textContent = `${n}%`; });
    document.querySelectorAll("[data-margin-cents]").forEach((el) => { el.textContent = `${n} cent${n === 1 ? "" : "s"}`; });
    document.querySelectorAll("[data-thin-margin]").forEach((el) => { el.hidden = margin > 0.05; });
  }
  const words = {
    max_ps: (v) => String(v),
    max_mcap: (v) => money(v).replace(/\.00B$/, " billion").replace(/B$/, " billion"),
    bonus_mcap: (v) => money(v).replace(/\.00B$/, " billion").replace(/B$/, " billion"),
    max_lt_de: (v) => (v === 0.5 ? "half" : `${times(v)}`),
    min_profitable_years: (v) => String(v),
    min_revenue_growth: (v) => pct(Math.abs(v), 0),
    out_of_favor_drawdown: (v) => pct(v, 0),
  };
  document.querySelectorAll("[data-rule]").forEach((el) => {
    const k = el.dataset.rule;
    if (R[k] != null && words[k]) el.textContent = words[k](R[k]);
  });
  const fresh = Array.isArray(data.industries);
  document.querySelectorAll("[data-new-flag]").forEach((el) => { el.hidden = !fresh; });
  const w = data.score_weights || {};
  const stated = Object.keys(STATED_WEIGHTS).every((k) => w[k] === STATED_WEIGHTS[k]) && Object.keys(w).length === Object.keys(STATED_WEIGHTS).length;
  const help = document.querySelector("[data-score-help]");
  help.hidden = !stated;
  // The note on which flags also earn points names the newer flags and the stated parts, so it needs both.
  document.querySelector("[data-flag-points]").hidden = !stated || !fresh;
  if (stated) {
    help.querySelector("[data-tape-key]").innerHTML = tape(Object.entries(STATED_WEIGHTS).map(([key, max]) => ({ key, max, points: max })), "tape-lg");
  }
}
// The weights "Ranked by 100 points" states (screener.SCORE_WEIGHTS), in the pipeline's order. Data scored with other
// weights hides it, so the page never explains a score by weights it wasn't built with; each row's own parts still show.
const STATED_WEIGHTS = { ps: 15, debt: 20, margin: 15, fcf: 10, growth: 10, small: 5, out_of_favor: 5, undiscovered: 5, under_book: 10, insiders_buying: 5 };

function renderProof(meta) {
  // The claim carries its own proof: how many of the stocks checked passed all eight rules. The pipeline counts one per
  // listing, so a company with two share classes is checked twice: "stocks", not "companies".
  const checked = data.sectors.reduce((a, s) => a + (s.checked || 0), 0);
  const n = data.results.length;
  if (checked) document.querySelector("[data-claim]").textContent = `${n.toLocaleString("en-US")} of ${checked.toLocaleString("en-US")} stocks pass all eight quality checks`;
  document.querySelector("[data-proof-date]").textContent = meta?.market_date ? longDate(meta.market_date) : "n/a";
  // The screen at a glance, from the data: its market value limit (screener.RULES) and today's top score.
  const cap = data.rules?.max_mcap;
  if (cap) document.querySelector('[data-stat="mcap"]').textContent = `Under ${cap % 1e9 === 0 ? `$${cap / 1e9}B` : money(cap)}`;
  const top = Math.max(...data.results.map((r) => r.score));
  document.querySelector('[data-stat="top"]').textContent = n ? String(top) : "None";
  const src = document.querySelector("[data-sources-date]");
  if (src && meta?.market_date) {
    src.textContent = `The latest update read SEC filings through ${longDate(meta.last_filing_day || meta.market_date)}, with prices from ${longDate(meta.market_date)}.`;
  }
}

// The panel's text filter narrows the table as it is typed, and stays in the page's address (?q=) like the rest.
filterEl.value = state.q;
let typing;
filterEl.addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(() => {
    state.q = filterEl.value.trim();
    state.open = null;
    setParam("q", state.q || null);
    paintReset();
    if (data) renderTable();
  }, 120);
});
filterEl.addEventListener("keydown", (e) => { if (e.key === "Escape" && filterEl.value) { e.preventDefault(); clearFilter(); } });
function clearFilter() {
  filterEl.value = "";
  state.q = "";
  setParam("q", null);
  paintReset();
  if (data) renderTable();
}
host.addEventListener("click", (e) => { if (e.target.closest("[data-clear-filter]")) { clearFilter(); filterEl.focus(); } });

// A link into "How the list is made" (screener.html#scoring, or to anything inside a section) opens its section.
function openTarget() {
  const el = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  for (let d = el?.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
}
window.addEventListener("hashchange", openTarget);

sortEl.addEventListener("change", (e) => setSort(e.target.value));
resetEl.addEventListener("click", () => {
  state.sector = "All"; state.insider = "any"; state.open = null; state.q = ""; filterEl.value = "";
  ["sector", "insider", "sort", "q"].forEach((k) => setParam(k, null));
  document.querySelectorAll("[data-insider]").forEach((b) => b.setAttribute("aria-pressed", b.dataset.insider === "any"));
  renderSectors();
  paintQuick();
  setSort("score");
  document.querySelector("[data-sectors] [aria-pressed='true']")?.focus();
});

Promise.all([load("screener.json"), load("meta.json").catch(() => null)]).then(([d, meta]) => {
  data = d;
  insiderDays = meta?.insider_days ?? null;
  insiderPending = meta?.insider_pending?.length ?? 0;
  if (insiderDays) document.querySelectorAll("[data-window]").forEach((el) => { el.textContent = insiderDays; });
  if (!hasPb()) {
    document.querySelector("[data-needs-pb]")?.remove();
    if (state.sort === "pb") state.sort = "score";
  }
  if (state.sector !== "All" && !d.sectors.some((s) => s.name === state.sector && (s.checked > 0 || s.excluded))) {
    state.sector = "All";
    setParam("sector", null);
  }
  sortEl.value = state.sort;
  renderProof(meta);
  explainRules();
  renderInsiderFilter();
  renderSectors();
  renderTable();
  renderIndustries();
  renderNetCash();
  renderFinancials();
  renderGiants();
  renderIndex();
  // A link to a list further down (screener.html#giants) lands on it once the lists are drawn, and a link into the
  // explanation opens its section first.
  openTarget();
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({ block: "start", behavior: "instant" });
}).catch(() => {
  host.setAttribute("aria-busy", "false");
  host.innerHTML = `<div class="panel empty"><h2>Screener data is not available yet</h2><p>Results appear after the first nightly update runs. If you expected them, reload the page in a minute.</p></div>`;
});
