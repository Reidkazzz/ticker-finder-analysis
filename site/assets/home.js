import { attachSearch, chrome, date, displayName, esc, icon, load, longDate, meter, money, pct, price, searchMarkup, signClass, tape, times } from "./common.js";
import { debtTag, scoreLedger } from "./ledger.js";

// The page has its own search box in the first screen, so the header leaves its own out.
chrome({ search: false });

const searchHost = document.getElementById("hero-search");
searchHost.innerHTML = searchMarkup("hero-q");
attachSearch(searchHost.querySelector("form"));

const meta = load("meta.json").catch(() => null);

// A buyer's role in a few words: chief executive first, then chief financial officer, then whatever they filed as.
const roleShort = (roles) => {
  if (!roles?.length) return "Insider";
  const r = roles.find((x) => /chief executive|ceo/i.test(x)) || roles.find((x) => /chief financial|cfo/i.test(x)) || roles[0];
  return r.replace(/Chief Executive Officer/i, "CEO").replace(/Chief Financial Officer/i, "CFO");
};
const noData = (text) => `<p class="proof-empty">${esc(text)}</p>`;

// The first screen's proof: the screener's top five, each with its score tape and what it is cheapest on.
async function topOfScreener() {
  const host = document.querySelector("[data-top-screen]");
  try {
    const s = await load("screener.json");
    const checked = s.sectors.reduce((a, x) => a + (x.checked || 0), 0);
    document.querySelector("[data-top-meta]").textContent = `${s.results.length} of ${checked.toLocaleString("en-US")} US companies pass`;
    document.querySelector("[data-top-all]").innerHTML = `See all ${s.results.length} companies${icon("arrow-right")}`;
    // The profit margin rule the data was built with (screener.RULES), as the screener page states it.
    const margin = s.rules?.min_net_margin;
    if (margin != null) {
      const n = Math.round(margin * 10000) / 100;
      document.querySelectorAll("[data-margin-cents]").forEach((el) => { el.textContent = `${n} cent${n === 1 ? "" : "s"}`; });
    }
    if (!s.results.length) {
      host.outerHTML = noData("No company passes every check today. The rules are strict on purpose.");
      return s;
    }
    host.innerHTML = s.results.slice(0, 5).map((r, i) => `<li>
      <a class="top-row" href="report.html?t=${encodeURIComponent(r.symbol)}">
        <span class="rank">${i + 1}</span>
        <span class="co"><span class="sym">${esc(r.symbol)}</span><span class="nm">${esc(displayName(r.name))}</span></span>
        <span class="why">${esc(times(r.ps))} sales${r.debt_status === "none" ? ", no debt" : r.debt_status === "net_cash" ? ", net cash" : ""}</span>
        <span class="score" role="img" aria-label="Score ${r.score} out of 100"><b>${r.score}</b>${tape(r.score_parts)}</span>
      </a></li>`).join("");
    return s;
  } catch {
    host.outerHTML = noData("Screener results appear after the first nightly update.");
    return null;
  }
}

// The screener's claim, proved by its top company's ledger: each part of the score beside its own figures.
async function ledgerProof(s) {
  const host = document.querySelector("[data-ledger-proof]");
  const r = s?.results?.[0];
  if (!r) { host.innerHTML = noData("The ranked list appears after the first nightly update."); return; }
  const m = await meta;
  host.innerHTML = `<div class="proof-head"><span class="proof-title">Why ${esc(r.symbol)} ranks first</span><span>${esc(displayName(r.name))}</span></div>
    <div class="ledger ledger-solo">${scoreLedger(r, { rules: s.rules, industries: s.industries, insiderDays: m?.insider_days })}</div>
    <div class="proof-strip"><span>Debt</span>${debtTag(r)}<span>Net margin</span><b>${esc(pct(r.net_margin))}</b><span>Cash yield</span><b>${esc(pct(r.fcf_yield))}</b></div>
    <a class="proof-foot" href="screener.html">Open it in the screener${icon("arrow-right")}</a>`;
}

// The insider claim, proved by the latest Tier 1 and 2 purchases. The feed (about 350 KB) loads when the section is
// near the screen rather than with the page.
async function latestBuying() {
  const list = document.querySelector("[data-latest]");
  try {
    const [ins, m] = await Promise.all([load("insiders.json"), meta]);
    const t = ins.tiers || {};
    if (t.tier1) document.querySelector("[data-tier1]").textContent = money(t.tier1).replace(/\.0+(?=[MB])/, "").replace(/M$/, " million").replace(/B$/, " billion");
    if (t.tier2) document.querySelector("[data-tier2]").textContent = money(t.tier2).replace(/\.0+(?=[MB])/, "").replace(/M$/, " million").replace(/B$/, " billion");
    if (t.tier2_stake) document.querySelector("[data-tier2-stake]").textContent = pct(t.tier2_stake, 0);
    const t1 = ins.companies.filter((c) => c.tier === 1).length;
    const t2 = ins.companies.filter((c) => c.tier === 2).length;
    const days = ins.window_days ?? m?.insider_days;
    document.querySelector("[data-insider-summary]").innerHTML = `Executives, directors and big owners must report their trades to the SEC within two business days. TFA reads every filing: ${days ? `in the last ${days} days` : "recently"}, insiders bought stock on the open market in <b>${t1}</b> Tier 1 and <b>${t2}</b> Tier 2 companies.`;
    const rows = ins.companies.filter((c) => c.tier <= 2).slice(0, 5);
    document.querySelector("[data-latest-date]").textContent = rows.length && m?.last_filing_day ? `Filed through ${longDate(m.last_filing_day)}` : "";
    if (!rows.length) {
      list.outerHTML = noData(`No Tier 1 or 2 purchases were filed ${m?.insider_pending?.length ? "in the days scanned so far" : `in the last ${days} days`}.`);
      return;
    }
    list.innerHTML = rows.map((c) => {
      const b = c.buyers[0];
      const more = c.buyers.length > 1 ? `, and ${c.buyers.length - 1} more` : "";
      return `<li><a class="buy-row" href="${c.symbol ? `report.html?t=${encodeURIComponent(c.symbol)}` : "insiders.html"}">
        <span class="tier tag ${c.tier === 1 ? "tag-solid" : "tag-line"}">Tier ${c.tier}</span>
        <span class="co"><span class="sym">${esc(c.symbol || "n/a")}</span><span class="nm">${esc(b.name)}, ${esc(roleShort(b.roles))}${esc(more)}</span></span>
        <span class="amt"><b>${money(c.total_value)}</b><span class="${signClass(c.vs_paid)}">${c.vs_paid == null ? esc(date(c.filed)) : `${pct(c.vs_paid, 1, true)} vs. paid`}</span></span>
      </a></li>`;
    }).join("");
  } catch {
    list.outerHTML = noData("Insider data is not available yet. It appears after the first nightly update.");
  }
}

// The report's claim, proved by a live report: the first of the top companies that has a fair value, its range against
// the price and three of its health measures.
async function exampleReport(s) {
  const host = document.querySelector("[data-example]");
  const link = document.querySelector("[data-example-link]");
  const syms = (s?.results || []).slice(0, 4).map((r) => r.symbol);
  for (const sym of syms) {
    let r;
    try { r = await load(`t/${sym}.json`); } catch { continue; }
    const v = r.valuation;
    if (!v?.verdict || v.low == null || v.high == null) continue;
    const bars = (r.health || []).flatMap((g) => g.bars).filter((b) => !b.context && b.score != null);
    const pick = ["net_margin", "net_cash", "ps"].map((k) => bars.find((b) => b.key === k)).filter(Boolean);
    const word = { undervalued: ["Undervalued", "tag-up"], fair: ["Fair", "tag-mid"], overvalued: ["Overvalued", "tag-down"] }[v.verdict];
    if (!word) continue;
    // The range bar's scale spans the range and the price with room either side, as the report's own does.
    const lo = Math.min(v.low, r.price) * 0.85, hi = Math.max(v.high, r.price) * 1.15;
    const at = (x) => `${(((x - lo) / (hi - lo)) * 100).toFixed(1)}%`;
    host.innerHTML = `<div class="proof-head"><span class="proof-title"><span class="sym">${esc(r.symbol)}</span> ${esc(displayName(r.name))}</span><span class="tag ${word[1]}">${word[0]}</span></div>
      <div class="ex-range" role="img" aria-label="Estimated fair value ${esc(price(v.low))} to ${esc(price(v.high))}, price ${esc(price(r.price))}">
        <div class="ex-track"><span class="ex-band" style="left:${at(v.low)};width:calc(${at(v.high)} - ${at(v.low)})"></span><span class="ex-mark" style="left:${at(r.price)}"></span></div>
        <div class="ex-labels"><span>Fair value <b>${esc(price(v.low))} to ${esc(price(v.high))}</b></span><span>Price <b>${esc(price(r.price))}</b></span></div>
      </div>
      <dl class="ex-bars">${pick.map((b) => `<div><dt>${esc(b.label)}</dt><dd><span class="ex-val">${esc(b.value ?? "")}</span>${meter(b.score, b.band, "Not enough data", b.label)}</dd></div>`).join("")}</dl>
      <p class="proof-note">Health score ${r.health_score ?? "n/a"} of 100. The fair value is an estimate from public filings, not a price target.</p>`;
    link.href = `report.html?t=${encodeURIComponent(sym)}`;
    link.innerHTML = `Open the ${esc(sym)} report${icon("arrow-right")}`;
    return;
  }
  host.innerHTML = noData("Search any US ticker above to open its report.");
}

// Below the first screen, the insider feed waits until its section comes near.
const insiderSection = document.querySelector("[data-latest]")?.closest("section");
if ("IntersectionObserver" in window && insiderSection) {
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { io.disconnect(); latestBuying(); }
  }, { rootMargin: "600px 0px" });
  io.observe(insiderSection);
} else latestBuying();

topOfScreener().then((s) => { ledgerProof(s); exampleReport(s); });
