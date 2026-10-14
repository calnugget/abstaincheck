"use strict";
const ORIGIN = location.origin;
const $ = (s, el = document) => el.querySelector(s);
const pct = v => v == null ? "–" : Math.round(v * 100) + "%";
const num = (v, d = 2) => v == null ? "–" : v.toFixed(d);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LABELS = {
  contraindication: "Contraindicated drug", drug_drug_interaction: "Dangerous drug interaction",
  impossible_timing: "Treatment window has passed", physiological_impossibility: "Plan the body cannot support",
};

// fill in this server's address in the instructions, and make code blocks copyable
document.querySelectorAll("code").forEach(c => { c.textContent = c.textContent.replaceAll("ORIGIN", ORIGIN); });
document.querySelectorAll("pre.copy").forEach(p => p.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(p.innerText); p.classList.add("copied"); setTimeout(() => p.classList.remove("copied"), 1500); } catch (e) { /* clipboard blocked */ }
}));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// --- providers --------------------------------------------------------------------
getJSON("/api/v1/providers").then(ps => {
  $("#provider").innerHTML = ps.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("");
}).catch(() => {});

// --- leaderboard ------------------------------------------------------------------
function kindTag(tier) {
  const t = { paper: "paper", verified: "verified", self_reported: "self-reported" }[tier] || tier;
  return `<span class="tag ${esc(tier)}">${esc(t)}</span>`;
}
async function loadBoard() {
  const b = await getJSON("/api/v1/leaderboard");
  $("#boardnote").textContent = b.reading + " Paper rows are the full benchmark; others are the test size shown.";
  const rows = [...b.reference.map(r => ({ ...r, tier: "paper" })), ...b.entries];
  rows.sort((a, c) => (c.D ?? -9) - (a.D ?? -9));
  $("#boardtable tbody").innerHTML = rows.map((r, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td>${r.result_id ? `<a href="/r/${esc(r.result_id)}">${esc(r.model)}</a>` : esc(r.model)}</td>
    <td class="num"><b>${num(r.D)}</b></td>
    <td class="num">${r.D_ci95 ? `${num(r.D_ci95[0])} to ${num(r.D_ci95[1])}` : "–"}</td>
    <td class="num">${pct(r.trap_catch_rate)}</td>
    <td class="num">${pct(r.false_alarm_rate)}</td>
    <td class="num">${r.n_questions}</td>
    <td>${kindTag(r.tier)}</td></tr>`).join("");
}
loadBoard().catch(e => { $("#boardnote").textContent = "Could not load the leaderboard: " + e.message; });

// --- verified run -----------------------------------------------------------------
$("#runform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = Object.fromEntries(f.entries());
  body.publish = f.get("publish") === "on";
  const btn = $("button", ev.target);
  btn.disabled = true; $("#runerr").hidden = true; $("#progress").hidden = false;
  $("#barfill").style.width = "0"; $("#ptext").textContent = "Checking your key and model…";
  try {
    const r = await fetch("/api/v1/runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 1);
        if (!line.trim()) continue;
        const e = JSON.parse(line);
        if (e.type === "progress") {
          $("#barfill").style.width = (100 * e.done / e.total) + "%";
          $("#ptext").textContent = `${e.done} of ${e.total} questions answered`;
        } else if (e.type === "error") {
          throw new Error(e.message);
        } else if (e.type === "done") {
          ev.target.querySelector("[name=api_key]").value = "";
          history.pushState({}, "", "/r/" + e.result_id);
          route();
        }
      }
    }
  } catch (e) {
    $("#runerr").textContent = e.message; $("#runerr").hidden = false; $("#progress").hidden = true;
  } finally { btn.disabled = false; }
});

// --- report card ------------------------------------------------------------------
function reliabilitySVG(bins) {
  const W = 260, H = 200, P = 28, s = v => P + v * (W - P - 8), t = v => H - P - v * (H - P - 8);
  let g = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Reliability diagram">`;
  g += `<line x1="${s(0)}" y1="${t(0)}" x2="${s(1)}" y2="${t(1)}" stroke="currentColor" stroke-opacity=".3" stroke-dasharray="4 3"/>`;
  g += `<line x1="${s(0)}" y1="${t(0)}" x2="${s(1)}" y2="${t(0)}" stroke="currentColor" stroke-opacity=".4"/>`;
  g += `<line x1="${s(0)}" y1="${t(0)}" x2="${s(0)}" y2="${t(1)}" stroke="currentColor" stroke-opacity=".4"/>`;
  const max = Math.max(1, ...bins.map(b => b.count));
  bins.forEach(b => {
    if (!b.count) return;
    const x = s(b.low) + 2, w = s(b.high) - s(b.low) - 4;
    g += `<rect x="${x}" y="${t(b.accuracy)}" width="${w}" height="${t(0) - t(b.accuracy)}" fill="var(--accent)" fill-opacity="${0.35 + 0.65 * b.count / max}"><title>${b.count} answers with confidence ${b.low.toFixed(1)}–${b.high.toFixed(1)}: ${pct(b.accuracy)} right</title></rect>`;
  });
  g += `<text x="${s(.5)}" y="${H - 6}" text-anchor="middle">stated confidence</text>`;
  g += `<text x="10" y="${t(.5)}" text-anchor="middle" transform="rotate(-90 10 ${t(.5)})">share right</text>`;
  return g + "</svg>";
}
function catRows(by) {
  return Object.entries(by).map(([k, c]) => {
    const r = c.catch_rate, cls = r == null ? "" : r >= .9 ? "" : r >= .7 ? "mid" : "low";
    return `<div class="cat"><div class="row"><span>${esc(LABELS[k] || k)}</span><span>${c.caught}/${c.traps} caught · ${c.false_alarms}/${c.controls} false alarms</span></div>
      <div class="track ${cls}"><div style="width:${r == null ? 0 : 100 * r}%"></div></div></div>`;
  }).join("");
}
function renderReport(r) {
  const el = $("#report");
  const youD = r.D == null ? "–" : num(r.D);
  const ci = r.D_ci95 ? `95% interval ${num(r.D_ci95[0])} to ${num(r.D_ci95[1])}` : "";
  const cmp = r.comparison.rows.map(x => `<tr class="${x.is_you ? "you" : ""}"><td class="num">${x.rank}</td>
     <td>${x.is_you ? esc(r.model_name) + " (this test)" : esc(x.model)}</td><td class="num">${num(x.D)}</td>
     <td class="num">${pct(x.trap_catch_rate)}</td><td class="num">${pct(x.false_alarm_rate)}</td></tr>`).join("");
  const missed = (r.missed || []).map(m => `<details class="miss"><summary>${m.kind === "trap" ? "Missed trap" : m.your_answer === "FLAG" ? "False alarm" : "Wrong answer"}
     · ${esc(LABELS[m.category])} · you said <b>${esc(m.your_answer ?? "nothing")}</b>${m.your_confidence != null ? ` at ${pct(m.your_confidence)} confidence` : ""}</summary>
     <p>${esc(m.question)}</p><p>${Object.entries(m.choices).map(([k, v]) => `<b>${esc(k)}</b>) ${esc(v)}`).join("<br>")}</p>
     <p class="why">${m.kind === "trap" ? "Right answer: FLAG. " + esc(m.why || "") : "Right answer: " + esc(m.correct_answer) + "."}
     ${(m.sources || []).map(u => ` <a href="${esc(u)}" rel="noopener" target="_blank">source</a>`).join("")}</p></details>`).join("");
  el.innerHTML = `<h2>Report card: ${esc(r.model_name)}</h2>
    <p class="small">${kindTag(r.tier)} ${esc(r.tier_meaning)} ${r.run ? `· ${esc(r.run.provider)} ${esc(r.run.model)}, ${r.run.seconds}s` : ""} · ${r.n_questions} questions · ${new Date(r.created).toLocaleString()}</p>
    ${r.withheld ? `<p class="note">${esc(r.withheld)}</p>` : ""}
    <div class="score">
      <div class="stat big"><div class="v">${youD}</div><div class="l">D score · ${esc(ci)}</div></div>
      <div class="stat"><div class="v">${pct(r.trap_catch_rate)}</div><div class="l">traps caught (${r.n_traps})</div></div>
      <div class="stat"><div class="v">${pct(r.false_alarm_rate)}</div><div class="l">false alarms on safe questions (${r.n_controls})</div></div>
      <div class="stat"><div class="v">${pct(r.control_accuracy)}</div><div class="l">safe questions answered right</div></div>
      <div class="stat"><div class="v">${num(r.ece)}</div><div class="l">calibration error (0 is best)</div></div>
    </div>
    ${r.note ? `<p class="note">${esc(r.note)}</p>` : ""}
    <div class="grid2">
      <div class="panel"><h3>By kind of trap</h3>${catRows(r.by_category)}</div>
      <div class="panel"><h3>Does confidence match being right?</h3>${reliabilitySVG(r.reliability)}
        <p class="small">Bars on the dashed line mean well calibrated. Bars below it mean overconfident.
        Average confidence on wrong answers: ${pct(r.mean_confidence_when_wrong)}.</p></div>
    </div>
    <h3>Compared with the paper's models on the same questions</h3>
    <div class="scroll"><table><thead><tr><th>#</th><th>Model</th><th>D</th><th>Traps caught</th><th>False alarms</th></tr></thead><tbody>${cmp}</tbody></table></div>
    <h3 style="margin-top:22px">Questions missed (${(r.missed || []).length})</h3>${missed || "<p>None.</p>"}`;
  el.hidden = false;
}

async function route() {
  const m = location.pathname.match(/^\/r\/([\w-]+)/);
  if (!m) { $("#report").hidden = true; return; }
  document.querySelectorAll("main > section:not(#report)").forEach(s => s.hidden = true);
  $("#report").innerHTML = "<p>Loading…</p>"; $("#report").hidden = false;
  try { renderReport(await getJSON("/api/v1/results/" + m[1])); }
  catch (e) { $("#report").innerHTML = `<p class="err">Could not load this report: ${esc(e.message)}</p>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("popstate", () => location.reload());
route();
