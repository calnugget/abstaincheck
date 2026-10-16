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
let board = null, boardFmt = "realistic";
function drawBoard() {
  if (!board) return;
  $("#fmtnote").textContent = board.formats[boardFmt] || "";
  $("#boardnote").textContent = board.reading;
  const rows = [...board.reference.map(r => ({ ...r, tier: "paper" })), ...board.entries]
    .filter(r => (r.format || "standard") === boardFmt);
  rows.sort((a, c) => (c.D ?? -9) - (a.D ?? -9));
  $("#boardtable tbody").innerHTML = rows.length ? rows.map((r, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td>${r.result_id ? `<a href="/r/${esc(r.result_id)}">${esc(r.model)}</a>` : esc(r.model)}</td>
    <td class="num"><b>${num(r.D)}</b></td>
    <td class="num">${r.D_ci95 ? `${num(r.D_ci95[0])} to ${num(r.D_ci95[1])}` : "–"}</td>
    <td class="num">${pct(r.trap_catch_rate)}</td>
    <td class="num">${pct(r.trap_warned_rate)}</td>
    <td class="num">${pct(r.false_alarm_rate)}</td>
    <td class="num">${pct(r.control_accuracy)}</td>
    <td class="num">${r.n_questions}</td>
    <td>${kindTag(r.tier)}</td></tr>`).join("") : `<tr><td colspan="10">No results in this format yet.</td></tr>`;
  const refs = board.reference.filter(r => r.format === boardFmt);
  const noFA = boardFmt !== "standard" && refs.length && refs.every(r => r.false_alarm_rate === 0);
  $("#fanote").hidden = !noFA;
  $("#fanote").textContent = noFA ? "None of the reference models refused a safe question in this format, so here " +
    "D equals the trap-catch rate. Their errors are answered traps, not over-refusal. \"Safe questions right\" " +
    "is the share of safe questions where the model chose the correct option." : "";
}

// --- findings on the landing page, computed from the same reference rows ---------------
const FMTS = ["standard", "realistic", "pressure"];
const baseKey = k => String(k || "").replace(/-\d{8}$/, "");
function range(xs) {
  const v = xs.filter(x => x != null).map(x => Math.round(x * 100));
  if (!v.length) return "–";
  const lo = Math.min(...v), hi = Math.max(...v);
  return lo === hi ? `${lo}%` : `${lo}% to ${hi}%`;
}
function drawFindings() {
  const models = {};
  for (const r of board.reference) {
    const m = models[baseKey(r.key)] ||= { name: null, f: {} };
    const s = r.format === "standard" ? r.on_panel : r;   // same 240 questions in every format
    if (!s) continue;
    m.f[r.format] = { ...s, trap_warned_rate: r.trap_warned_rate, method: r.method };
    if (r.format !== "standard") m.name = r.model;
  }
  const list = Object.values(models).filter(m => FMTS.every(f => m.f[f]));
  if (!list.length) { $("#findings").hidden = true; return; }
  list.sort((a, b) => b.f.realistic.trap_catch_rate - a.f.realistic.trap_catch_rate);
  $("#findchart").innerHTML = list.map(m => `<div class="fm"><div class="fname">${esc(m.name)}</div>
    ${FMTS.map(f => { const v = m.f[f].trap_catch_rate; return `<div class="frow" title="${esc(board.formats[f] || f)}">
      <div class="ftrack"><div class="fbar f-${f}" style="width:${Math.max(0, v * 100)}%"></div></div>
      <span class="fval">${pct(v)}</span></div>`; }).join("")}</div>`).join("");
  const col = (f, k) => list.map(m => m.f[f][k]);
  const fa = ["realistic", "pressure"].flatMap(f => col(f, "false_alarm_rate"));
  $("#findtext").innerHTML = `<ul class="findings">
    <li>With the paper's prompt, which tells the model that refusing is allowed, the models caught
      <b>${range(col("standard", "trap_catch_rate"))}</b> of traps.</li>
    <li>Asked the same questions plainly, they caught <b>${range(col("realistic", "trap_catch_rate"))}</b>.
      With one sentence of pressure from the asker, <b>${range(col("pressure", "trap_catch_rate"))}</b>.</li>
    <li>Asked plainly, the models often named the danger and still chose an option: that happened on
      <b>${range(col("realistic", "trap_warned_rate"))}</b> of traps.</li>
    <li>${fa.every(x => x === 0) ? "No model refused a safe question when asked plainly or with pressure, so the lower catch rates are not balanced by caution elsewhere." :
      `False alarms on safe questions when asked plainly or with pressure: ${range(fa)}.`}</li>
  </ul>
  <p>A score from the benchmark's own prompt can overstate what users of an app will see. AbstainCheck tests a
  model the way its users ask, including with your app's own system prompt, so you can measure the gap before they
  meet it.</p>`;
  const sc = list.filter(m => m.f.standard.method && m.f.standard.method !== "temperature_0").map(m => m.name);
  $("#findnote").innerHTML = `One reply per question per model. Plain and pressure replies were graded by a model whose
    calls matched 80 of 80 hand labels; paper's-prompt answers are from the AdverseMed-500 paper's runs${sc.length ?
    ` (${esc(sc.join(", "))}: self-consistency over several samples)` : ""}. See <a href="/methods">how it works</a> and the
    <a href="#board">full leaderboard</a>.`;
}
document.querySelectorAll("#boardtabs button").forEach(b => b.addEventListener("click", () => {
  boardFmt = b.dataset.fmt;
  document.querySelectorAll("#boardtabs button").forEach(x => x.classList.toggle("on", x === b));
  drawBoard();
}));
getJSON("/api/v1/leaderboard").then(b => { board = b; drawBoard(); drawFindings(); })
  .catch(e => { $("#boardnote").textContent = "Could not load the leaderboard: " + e.message; });

// free-text formats need the server's grader; fall back to standard without it
getJSON("/api/v1/health").then(h => {
  if (h.free_text_formats) return;
  $("#format").value = "standard";
  [...$("#format").options].forEach(o => { if (o.value !== "standard") o.disabled = true; });
}).catch(() => {});

// --- verified run -----------------------------------------------------------------
$("#runform").addEventListener("submit", async ev => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const body = Object.fromEntries(f.entries());
  body.publish = f.get("publish") === "on";
  body.seed = body.seed && /^\d+$/.test(body.seed.trim()) ? Number(body.seed.trim()) : null;
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
        } else if (e.type === "grading") {
          $("#ptext").textContent = "All replies in. Grading them…";
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
  const said = m => m.your_answer === "FLAG" ? "refused" : m.your_answer === "INVALID" ? "no clear answer"
    : m.your_answer == null ? "nothing" : m.your_answer;
  const missed = (r.missed || []).map(m => `<details class="miss"><summary>${m.kind === "trap" ? (m.warned ? "Missed trap, though it warned" : "Missed trap") : m.your_answer === "FLAG" ? "False alarm" : "Wrong answer"}
     · ${esc(LABELS[m.category])} · answer <b>${esc(said(m))}</b>${m.your_confidence != null ? ` at ${pct(m.your_confidence)} confidence` : ""}</summary>
     <p>${esc(m.question)}</p><p>${Object.entries(m.choices).map(([k, v]) => `<b>${esc(k)}</b>) ${esc(v)}`).join("<br>")}</p>
     ${m.your_reply ? `<p class="small">The reply${m.your_reply.length >= 1500 ? " (first 1,500 characters)" : ""}:</p><div class="reply">${esc(m.your_reply)}</div>` : ""}
     <p class="why">${m.kind === "trap" ? "Right answer: refuse, because the premise is unsafe. " + esc(m.why || "")
       : "This question was safe to answer. Right answer: " + esc(m.correct_answer) + "."}
     ${(m.sources || []).map(u => ` <a href="${esc(u)}" rel="noopener" target="_blank">source</a>`).join("")}</p></details>`).join("");
  el.innerHTML = `<h2>Report card: ${esc(r.model_name)}</h2>
    <p class="small">${kindTag(r.tier)} ${esc(r.tier_meaning)} ${r.run ? `· ${esc(r.run.provider)} ${esc(r.run.model)}, ${r.run.seconds}s` : ""} · ${r.n_questions} questions · ${new Date(r.created).toLocaleString()}</p>
    <p class="small"><b>Format:</b> ${esc(r.format_meaning || "")}${r.run && r.run.system_prompt ? ` · custom system prompt (fingerprint ${esc(r.run.system_prompt.fingerprint)})` : ""}</p>
    ${r.grading_note ? `<p class="small">${esc(r.grading_note)}${r.run && r.run.grader ? ` Grader: ${esc(r.run.grader)}.` : ""}</p>` : ""}
    ${r.withheld ? `<p class="note">${esc(r.withheld)}</p>` : ""}
    <div class="score">
      <div class="stat big"><div class="v">${youD}</div><div class="l">D score · ${esc(ci)}</div></div>
      <div class="stat"><div class="v">${pct(r.trap_catch_rate)}</div><div class="l">traps caught (${r.n_traps})</div></div>
      ${r.trap_warned_rate != null ? `<div class="stat"><div class="v">${pct(r.trap_warned_rate)}</div><div class="l">traps where it warned but still answered</div></div>` : ""}
      <div class="stat"><div class="v">${pct(r.false_alarm_rate)}</div><div class="l">false alarms on safe questions (${r.n_controls})</div></div>
      <div class="stat"><div class="v">${pct(r.control_accuracy)}</div><div class="l">safe questions answered right</div></div>
      ${r.ece != null ? `<div class="stat"><div class="v">${num(r.ece)}</div><div class="l">calibration error (0 is best)</div></div>` : ""}
    </div>
    ${r.note ? `<p class="note">${esc(r.note)}</p>` : ""}
    <div class="grid2">
      <div class="panel"><h3>By kind of trap</h3>${catRows(r.by_category)}</div>
      ${r.ece != null ? `<div class="panel"><h3>Does confidence match being right?</h3>${reliabilitySVG(r.reliability)}
        <p class="small">Bars on the dashed line mean well calibrated. Bars below it mean overconfident.
        Average confidence on wrong answers: ${pct(r.mean_confidence_when_wrong)}.</p></div>` : ""}
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
