/* Thehro judge mode (P3). Plain JavaScript, no build step, nothing loaded from the internet.
 *
 * Talks to the bank server (bank/server.py, or bank/mock_server.py with MOCK=1):
 *   POST /judge/session, GET /judge/options, POST /judge/case, POST /judge/action,
 *   GET /judge/decisions/{id}, POST /judge/reset, GET /device/flags
 *
 * Rule: the phone pane renders only what the phone itself has (payment, masked payee ref, `phone` /
 * `phone_by_lang`, `phone_meta`, action notes, SMS, and its own call state). Nothing from `bank_view`
 * is ever drawn inside it: the score lines must stay away from scammers.
 *
 * Mock-server helpers (rehearsal only): ?mock=t2_pause forces a canned case screen;
 * ?mockpoll=released (or cancelled) makes the hold poll return that state.
 */
(function () {
"use strict";

// ---------------------------------------------------------------- constants and helpers
const TIERS = ["Allow", "Note", "Pause and explain", "Trusted check", "Hold for review"];
const TRUSTED_MIN = 50000;   // config.TRUSTED_CHECK_MIN_AMOUNT
const HOLD_MIN = 10000;      // config.HOLD_MIN_AMOUNT
const MEMORY_DAYS = 7;       // config.OVERRIDE_MEMORY_DAYS
const RETRY_HOURS = 24;      // config.RETRY_MEMORY_HOURS (policy v2)
const POLL_MS = 2000;
const POLL_STATES = ["held", "review_requested", "call_bank", "callback_pending"];
const CALLBACK_MIN = 15;     // config.CALLBACK_SLA_MINUTES
const FINAL_STATES = ["paid", "cancelled"];
const DEFAULT_CTX = { on_call: 1, call_ott: 1, call_min: 70, caller_unknown: 1, screen_share: 0, remote_app: 0, app_switches: 3 };
const EV_DEFAULTS = {
  new_payee_payment: { amount: 9000, hours_ago: 20 }, fd_break: { hours_ago: 3 },
  stranger_credit: { amount: 300, hours_ago: 4 }, sent_today: { amount: 5000, hours_ago: 2 },
};
const QS = new URLSearchParams(location.search);
const FORCE_MOCK = QS.get("mock");
const PHONE = QS.get("phone") === "1";       // a judge's own phone: follows one session, shows only the phone
const PHONE_SESSION = QS.get("s");
const MOCK_POLL = QS.get("mockpoll");

const UI = {
  en: { send: "Send money", bankApp: "Your bank app (demo)", pay: "Pay", payAnyway: "Pay anyway", cancel: "Cancel",
        checking: "Checking this payment…", pin: "Enter UPI PIN", paid: "Paid", cancelled: "Payment cancelled",
        noMoney: "No money left your account.", via: "via", oncall: "On call", unknown: "unknown number",
        internet: "internet call", sharing: "Screen shared", why: "Why we're asking",
        cont: s => s > 0 ? `Continue (${s} s)` : "Continue", thisWasMe: "This was me",
        thisWasMeWait: s => `This was me (${s} s)`, trustedSent: "Request sent to your trusted person",
        trustedSees: "They see the amount, the payee and the warning signs, and can approve or decline.",
        trustedPhone: "Trusted person's phone (demo)", approve: "Approve", decline: "Decline",
        callBank: "Call your bank", stillHeld: "This payment stays held until a bank fraud officer decides.",
        sms: "SMS from your bank", ready: "Ready for the next payment.", newPayment: "New payment",
        pickedOnPay: "Payee is picked by the bank when you press Pay", ref: "Ref", reviewAsked: "You asked for a review",
        callbackSent: `Your bank will call you within ${CALLBACK_MIN} minutes`,
        callbackSees: "On your registered number. The caller will never ask for your PIN or OTP. If they can't reach you, you can wait an hour and then pay.",
        waitHour: "Wait 1 hour instead" },
  hi: { send: "पैसे भेजें", bankApp: "आपका बैंक ऐप (डेमो)", pay: "भुगतान करें", payAnyway: "फिर भी भुगतान करें", cancel: "रद्द करें",
        checking: "इस भुगतान की जाँच हो रही है…", pin: "UPI PIN डालें", paid: "भुगतान हो गया", cancelled: "भुगतान रद्द",
        noMoney: "आपके खाते से कोई पैसा नहीं गया।", via: "माध्यम:", oncall: "कॉल पर", unknown: "अनजान नंबर",
        internet: "इंटरनेट कॉल", sharing: "स्क्रीन शेयर हो रही है", why: "हम क्यों पूछ रहे हैं",
        cont: s => s > 0 ? `जारी रखें (${s} सेकंड)` : "जारी रखें", thisWasMe: "यह भुगतान मैंने ही किया है",
        thisWasMeWait: s => `यह भुगतान मैंने ही किया है (${s} सेकंड)`, trustedSent: "आपके भरोसेमंद व्यक्ति को अनुरोध भेजा गया",
        trustedSees: "वे रकम, प्राप्तकर्ता और चेतावनी के संकेत देखकर मंज़ूरी दे सकते हैं या मना कर सकते हैं।",
        trustedPhone: "भरोसेमंद व्यक्ति का फ़ोन (डेमो)", approve: "मंज़ूर करें", decline: "मना करें",
        callBank: "अपने बैंक को कॉल करें", stillHeld: "जब तक बैंक का धोखाधड़ी अधिकारी फ़ैसला नहीं करता, यह भुगतान रुका रहेगा।",
        sms: "आपके बैंक का SMS", ready: "अगले भुगतान के लिए तैयार।", newPayment: "नया भुगतान",
        pickedOnPay: "Pay दबाने पर बैंक प्राप्तकर्ता चुनता है", ref: "संदर्भ", reviewAsked: "आपने जाँच का अनुरोध किया",
        callbackSent: `आपका बैंक ${CALLBACK_MIN} मिनट के अंदर आपको कॉल करेगा`,
        callbackSees: "आपके रजिस्टर्ड नंबर पर। कॉल करने वाला कभी आपका PIN या OTP नहीं माँगेगा। अगर आपसे बात नहीं हो पाई, तो आप एक घंटा रुककर भुगतान कर सकते हैं।",
        waitHour: "इसके बजाय 1 घंटा रुकें" },
};

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = x => "₹" + Math.round(Number(x) || 0).toLocaleString("en-IN");
const pct = (x, d = 0) => (x * 100).toFixed(d) + "%";
const n0 = x => (x == null || Number.isNaN(Number(x))) ? 0 : Number(x);
const f1 = x => x == null ? "–" : (x >= 10 ? Math.round(x).toString() : (Math.round(x * 10) / 10).toString());
const yn = x => x ? "yes" : "no";
const BAND = ["none", "medium", "high", "very high"];
const TV = i => `var(--t${i})`, TS = i => `var(--t${i}-soft)`;

const S = {
  opts: null, session: null, lang: "en",
  payeeSkip: 0, pending: [], sentHistory: [],
  entries: [], cur: -1, busy: false, checking: null,
  countdown: null, poll: null, devicePoll: null, device: null, officerChecked: false,
};
const ui = () => UI[S.lang];
const curEntry = () => S.entries[S.cur] || null;

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method, headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined, cache: "no-store",
    });
  } catch (e) {
    throw new Error("Can't reach the bank server. Is demo_up.sh running?");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    if (data && data.detail) msg = typeof data.detail === "string" ? data.detail
      : data.detail.map(d => d.msg || JSON.stringify(d)).join("; ");
    const err = new Error(msg); err.status = res.status; throw err;
  }
  return data;
}

function toast(msg, kind) {
  const t = document.createElement("div");
  t.className = "toast" + (kind === "err" ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "err" ? 6000 : 3500);
}

// ---------------------------------------------------------------- tabs and language
function showTab(name) {
  if (!["judge", "officer", "kit", "results"].includes(name)) name = "judge";
  $$("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== name; });
  $$(".tabs [data-tab]").forEach(a => a.setAttribute("aria-selected", String(a.dataset.tab === name)));
  if (name === "officer") loadOfficer();
  if (name === "kit") loadKit();
  if (name === "results") loadReport();
}
function bindTop() {
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1) || "judge");
  $$(".lang [data-lang]").forEach(b => b.addEventListener("click", () => {
    S.lang = b.dataset.lang;
    $$(".lang [data-lang]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    renderPhone(); renderBank();          // re-render from phone_by_lang / reasons: no new request
  }));
}

async function loadOfficer() {
  if (S.officerChecked) return;
  S.officerChecked = true;
  const body = $("#officer-body");
  let ok = false;
  try { ok = (await fetch("officer.html", { method: "HEAD", cache: "no-store" })).ok; } catch (e) { ok = false; }
  if (ok) {
    body.innerHTML = `<iframe class="officer-frame" src="officer.html" title="Officer review"></iframe>`;
  } else {
    S.officerChecked = false;              // check again next time: P4 may add it later
    body.innerHTML = `<p class="muted">The officer page (<span class="mono">bank/static/officer.html</span>, P4) isn't there yet.
      Until it is, act on a hold in <a href="/docs" target="_blank" rel="noopener">/docs</a> with
      <span class="mono">POST /officer/cases/{case_id}/action</span> and
      <span class="mono">{"action": "release_confirm" | "cancel_scam" | "no_answer"}</span>.
      The judge page picks up the result on its own within 2 seconds.</p>`;
  }
}

// ---------------------------------------------------------------- case builder
function renderBuilder() {
  const o = S.opts;
  const custDefault = (o.customers.find(c => c.id === "C23724") || o.customers[0] || {}).id;
  const payeeDefault = (o.payee_types.find(p => p.id === "mule_network") || o.payee_types[0] || {}).id;
  const fields = o.context_fields || [];
  const bools = fields.filter(f => f.type === "bool"), nums = fields.filter(f => f.type !== "bool");
  $("#builder").innerHTML = `
    <div class="panel-h"><h3>Case builder</h3><span class="label">Demo time: ${esc(o.now && o.now.label)}</span></div>
    <label class="field"><span>Customer</span>
      <select id="f-customer">${o.customers.map(c => `<option value="${esc(c.id)}" ${c.id === custDefault ? "selected" : ""}>${esc(c.id)} · ${esc(c.label)} · usual ${esc(c.usual_payment_text)}</option>`).join("")}</select>
    </label>
    <p class="hint" id="cust-hint"></p>
    <div class="field"><span>Payee type</span>
      <div class="row-in"><select id="f-payee">${o.payee_types.map(p => `<option value="${esc(p.id)}" ${p.id === payeeDefault ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select>
      <button type="button" class="icon" id="f-skip" title="Another account of this type" aria-label="Another account of this type">↻</button></div>
    </div>
    <p class="hint" id="payee-hint"></p>
    <label class="field"><span>Account</span>
      <select id="f-same"><option value="">A new account of this type</option></select>
    </label>
    <div class="grid2">
      <label class="field"><span>Amount (₹)</span><input id="f-amount" type="number" min="1" step="1" inputmode="numeric" value="15693"></label>
      <label class="field"><span>Channel</span><select id="f-channel">${(o.channels || []).map(c => `<option>${esc(c)}</option>`).join("")}</select></label>
    </div>
    <fieldset class="field"><legend>Earlier events (optional)</legend>
      <div class="events" id="ev-list"></div>
      <div class="row-in"><select id="ev-kind">${(o.history_kinds || []).map(k => `<option value="${esc(k.id)}">${esc(k.label)}</option>`).join("")}</select>
        <button type="button" class="icon" id="ev-add">+ add</button></div>
      <div class="sent" id="ev-sent"></div>
    </fieldset>
    <fieldset class="field"><legend>Phone context</legend>
      <div class="radios" role="radiogroup">
        <label><input type="radio" name="ctxsrc" value="manual" checked> Manual</label>
        <label><input type="radio" name="ctxsrc" value="device"> From the demo phone</label>
      </div>
      <div class="ctx" id="ctx-manual">
        ${bools.map(f => `<label class="${f.note ? "wide" : ""}"><input type="checkbox" id="cx-${esc(f.id)}" ${DEFAULT_CTX[f.id] ? "checked" : ""}>
          <span>${esc(f.label)}${f.note ? `<small>${esc(f.note)}</small>` : ""}</span></label>`).join("")}
        ${nums.map(f => `<label class="num"><span>${esc(f.label)}</span><input type="number" min="0" step="${f.type === "int" ? 1 : "any"}" id="cx-${esc(f.id)}" value="${esc(DEFAULT_CTX[f.id] ?? 0)}"></label>`).join("")}
      </div>
      <div id="ctx-device" hidden><p class="live-line" id="device-line">Waiting for the demo phone…</p></div>
      <label class="check"><input type="checkbox" id="f-consent"> Customer has opted in: share phone flags</label>
      <p class="hint" id="consent-hint"></p>
    </fieldset>
    <label class="field"><span>Minutes since the previous payment</span><input id="f-gap" type="number" min="0" step="1" value="5"></label>
    <fieldset class="field drills"><legend>Failure drills (this payment only)</legend>
      <label class="check"><input type="checkbox" id="d-feeds"> Outside payee feeds time out (I4C registry, DoT FRI)</label>
      <label class="check"><input type="checkbox" id="d-service"> Decision service doesn't answer</label>
    </fieldset>
    <div class="actions">
      <button type="submit" class="btn primary" id="f-pay">Pay</button>
      <button type="button" class="btn" id="f-reset">Reset</button>
    </div>`;

  $("#f-customer").addEventListener("change", onCustomer);
  $("#f-payee").addEventListener("change", () => { S.payeeSkip = 0; payeeHint(); });
  $("#f-same").addEventListener("change", payeeHint);
  $("#f-skip").addEventListener("click", () => { S.payeeSkip += 1; payeeHint(); toast(`Next payment uses another ${payeeLabel($("#f-payee").value).toLowerCase()} account (#${S.payeeSkip + 1})`); });
  $("#f-amount").addEventListener("input", () => { if (!curEntry() && !S.checking) renderPhone(); });
  $("#ev-add").addEventListener("click", addEvent);
  $$('input[name="ctxsrc"]').forEach(r => r.addEventListener("change", onCtxSource));
  $("#f-consent").addEventListener("change", consentHint);
  $$("#ctx-manual input").forEach(i => i.addEventListener("input", () => { if (!curEntry() && !S.checking) renderPhone(); }));
  $("#builder").addEventListener("submit", ev => { ev.preventDefault(); pay(); });
  $("#f-reset").addEventListener("click", reset);
  onCustomer(); payeeHint(); renderEvents();
}

function customer() { return S.opts.customers.find(c => c.id === $("#f-customer").value) || {}; }
function payeeLabel(id) { const p = S.opts.payee_types.find(x => x.id === id); return p ? p.label : id; }
function ctxSource() { const r = $('input[name="ctxsrc"]:checked'); return r ? r.value : "manual"; }

function onCustomer() {
  const c = customer();
  $("#f-consent").checked = !!c.consent;
  $("#cust-hint").textContent = `Usual payment ${c.usual_payment_text || "–"} · consent on file: ${c.consent ? "yes" : "no"} · trusted person: ${c.trusted_contact ? "yes" : "no"}`;
  consentHint();
}
function sameOptions() {
  // "pay that same account again": earlier scored payments of this session, newest first
  const sel = $("#f-same");
  if (!sel) return;
  const keep = sel.value;
  const prev = S.entries.map((e, i) => ({ e, i })).filter(({ e }) => !e.kit && e.res.payee && e.res.payee.ref
    && e.res.payee.type !== "shop" && !(e.res.bank_view && e.res.bank_view.fallback)).reverse();
  sel.innerHTML = `<option value="">A new account of this type</option>` + prev.map(({ e, i }) =>
    `<option value="${esc(e.res.decision_id)}">Same account as payment #${i + 1}: ${esc(e.res.payee.ref)} · ${esc(payeeLabel(e.res.payee.type))} · ${esc((e.res.payment && e.res.payment.amount_text) || "")}</option>`).join("");
  sel.value = prev.some(({ e }) => e.res.decision_id === keep) ? keep : "";
}
function payeeHint() {
  const p = S.opts.payee_types.find(x => x.id === $("#f-payee").value) || {};
  const bits = [];
  const same = $("#f-same") && $("#f-same").value;
  if (same) {
    $("#payee-hint").textContent = "Pays that same account again (its payee type is kept); the payee type above is ignored.";
    return;
  }
  if (p.scored === false) bits.push(p.note || "not scored");
  if (S.payeeSkip) bits.push(`account #${S.payeeSkip + 1} of this type`);
  $("#payee-hint").textContent = bits.join(" · ");
}
function consentHint() {
  const on = $("#f-consent").checked;
  $("#consent-hint").textContent = on ? "" : "Consent off: the phone's flags stay on the phone. Nothing about the call is sent.";
  $("#consent-hint").className = on ? "hint" : "hint warn";
  $("#ctx-manual").classList.toggle("off", !on);
}
function onCtxSource() {
  const dev = ctxSource() === "device";
  $("#ctx-manual").hidden = dev;
  $("#ctx-device").hidden = !dev;
  clearInterval(S.devicePoll); S.devicePoll = null;
  if (dev) { pollDevice(); S.devicePoll = setInterval(pollDevice, POLL_MS); }
  if (!curEntry() && !S.checking) renderPhone();
}
function contextFromInputs() {
  const ctx = {};
  for (const f of S.opts.context_fields || []) {
    const el = $("#cx-" + f.id);
    if (!el) continue;
    ctx[f.id] = f.type === "bool" ? (el.checked ? 1 : 0) : n0(el.value);
  }
  return ctx;
}
function localContext() {
  return ctxSource() === "device" ? Object.assign({}, (S.device && S.device.flags) || {}) : contextFromInputs();
}

async function pollDevice() {
  try {
    S.device = await api("GET", "/device/flags");
  } catch (e) {
    $("#device-line").textContent = e.message; $("#device-line").className = "live-line"; return;
  }
  const d = S.device, f = d.flags || {}, line = $("#device-line");
  if (!d.updated_at) {
    line.textContent = "No phone has posted yet. Set up MacroDroid (brief, Step 7), or use Manual.";
    line.className = "live-line";
  } else {
    const who = d.device_id === "phone-A" ? "Phone A" : (d.device_id || "Phone");
    const call = f.on_call ? `on a call${f.call_ott ? " (internet)" : ""}${f.call_min != null ? ` · ${f1(Number(f.call_min))} min` : ""}` : "not on a call";
    line.textContent = `${who}: ${call} · via ${d.source || "phone"} at ${d.updated_at}`;
    line.className = "live-line" + (f.on_call ? " on" : "");
  }
  if (!curEntry() && !S.checking) renderPhone();
}

// earlier events: added once, sent with the next payment, then part of the session's history
function addEvent() {
  const kind = $("#ev-kind").value;
  const k = (S.opts.history_kinds || []).find(x => x.id === kind);
  if (!k) return;
  const ev = { kind };
  for (const fld of k.fields || []) ev[fld] = (EV_DEFAULTS[kind] || {})[fld] ?? (fld === "hours_ago" ? 2 : 1000);
  S.pending.push(ev);
  renderEvents();
}
function evLabel(kind) { const k = (S.opts.history_kinds || []).find(x => x.id === kind); return k ? k.label : kind; }
function evText(ev) {
  return [evLabel(ev.kind), ev.amount != null ? inr(ev.amount) : null, ev.hours_ago != null ? `${ev.hours_ago} h before` : null].filter(Boolean).join(" · ");
}
function renderEvents() {
  const list = $("#ev-list");
  list.innerHTML = S.pending.map((ev, i) => {
    const k = (S.opts.history_kinds || []).find(x => x.id === ev.kind) || { fields: [] };
    return `<div class="ev"><div class="ev-h"><b>${esc(k.label || ev.kind)}</b><button type="button" data-rm="${i}" aria-label="Remove">×</button></div>
      <div class="grid2">${(k.fields || []).map(fld => `<label>${fld === "amount" ? "Amount (₹)" : "Hours before this payment"}
        <input type="number" min="0" step="${fld === "amount" ? 1 : "any"}" data-i="${i}" data-f="${fld}" value="${esc(ev[fld])}"></label>`).join("")}</div></div>`;
  }).join("") || `<p class="hint">None yet. They become real history and are sent once, with the next payment.</p>`;
  $$("[data-rm]", list).forEach(b => b.addEventListener("click", () => { S.pending.splice(Number(b.dataset.rm), 1); renderEvents(); }));
  $$("input[data-i]", list).forEach(inp => inp.addEventListener("input", () => { S.pending[Number(inp.dataset.i)][inp.dataset.f] = n0(inp.value); }));
  $("#ev-sent").innerHTML = S.sentHistory.length
    ? `<span class="label">Already in this session's history</span>${S.sentHistory.map(t => `<span>✓ ${esc(t)}</span>`).join("")}` : "";
}

// ---------------------------------------------------------------- pay, act, reset
async function pay() {
  if (S.busy) return;
  const amount = Number($("#f-amount").value);
  if (!(amount > 0)) { toast("Enter an amount above ₹0", "err"); return; }
  const consent = $("#f-consent").checked, src = ctxSource(), ctx = localContext();
  const body = {
    session_id: S.session, customer: $("#f-customer").value, payee_type: $("#f-payee").value,
    payee_skip: S.payeeSkip, amount, channel: $("#f-channel").value, consent, context_source: src,
    minutes_later: n0($("#f-gap").value || 5), lang: S.lang,
  };
  if (S.pending.length) {
    body.history = S.pending.map(ev => {
      const k = (S.opts.history_kinds || []).find(x => x.id === ev.kind) || { fields: [] };
      const h = { kind: ev.kind };
      for (const fld of k.fields || []) if (ev[fld] != null) h[fld] = Number(ev[fld]);
      return h;
    });
  }
  if (consent && src === "manual") body.context = ctx;      // consent off: the phone sends nothing
  const same = $("#f-same").value;
  if (same) body.same_payee_as = same;
  if ($("#d-feeds").checked) body.feeds_down = true;
  if ($("#d-service").checked) body.service_down = true;
  if (FORCE_MOCK) body.mock = FORCE_MOCK;
  S.busy = true;
  S.checking = { amount, channel: body.channel, ctx };
  $("#f-pay").disabled = true;
  renderPhone();
  try {
    const res = await api("POST", "/judge/case", body);
    S.sentHistory.push(...S.pending.map(evText));
    S.pending = [];
    S.entries.push({ res, state: res.state, note: null, notifications: [], ctx, decidedAt: Date.now(),
                     skip: false, utr: null, until: null, reviewCase: null, caseInfo: null });
    S.cur = S.entries.length - 1;
    if (same) { $("#f-same").value = ""; }
    $("#d-feeds").checked = false; $("#d-service").checked = false;      // drills are one payment at a time
    window.scrollTo({ top: 0, behavior: "smooth" });   // the decision (laptop) or the phone (narrow) is at the top
  } catch (e) {
    toast(e.message, "err");
  } finally {
    S.busy = false; S.checking = null; $("#f-pay").disabled = false;
  }
  renderEvents();
  renderAll();
  payeeHint();
}

async function act(action) {
  const e = curEntry();
  if (!e || S.busy) return;
  const body = { decision_id: e.res.decision_id, action };
  if (e.skip && (action === "continue" || action === "this_was_me")) body.skip_wait = true;
  S.busy = true;
  try {
    const r = await api("POST", "/judge/action", body);
    e.state = r.state; e.note = r.note || null;
    if (r.utr) e.utr = r.utr;
    if (r.until) e.until = r.until;
    if (r.case_id) e.reviewCase = r.case_id;
    if (r.time_to_get_past_s != null) e.past = { s: r.time_to_get_past_s, tier: r.alert_tier };
  } catch (err) {
    toast(err.message, "err");
  } finally {
    S.busy = false;
  }
  renderAll();
}

async function reset() {
  try {
    await api("POST", "/judge/reset", { session_id: S.session });
  } catch (e) { toast(e.message, "err"); return; }
  S.entries = []; S.cur = -1; S.pending = []; S.sentHistory = []; S.payeeSkip = 0;
  payeeHint(); renderEvents(); renderAll();
  toast("Reset: this session's history, memory and timeline are cleared");
}

// ---------------------------------------------------------------- hold polling
function needsPoll(e) {
  return !!((e.res.phone_meta && e.res.phone_meta.case_id) || e.reviewCase) && POLL_STATES.includes(e.state);
}
function following(e) {        // the current payment, until it is paid or cancelled (it may be acted on from a phone)
  return e === curEntry() && !FINAL_STATES.includes(e.state);
}
function ensurePoll() {
  const any = S.entries.some(e => needsPoll(e) || following(e));
  if (any && !S.poll) S.poll = setInterval(pollHolds, POLL_MS);
  if (!any && S.poll) { clearInterval(S.poll); S.poll = null; }
}
async function pollHolds() {
  let changedCur = false, changedAny = false;
  for (const e of S.entries.filter(e => needsPoll(e) || following(e))) {
    try {
      const q = MOCK_POLL ? `?mock=${encodeURIComponent(MOCK_POLL)}` : "";
      const d = await api("GET", `/judge/decisions/${encodeURIComponent(e.res.decision_id)}${q}`);
      const nots = d.notifications || [];
      const changed = d.state !== e.state || nots.length !== e.notifications.length
        || JSON.stringify(d.case) !== JSON.stringify(e.caseInfo);
      e.caseInfo = d.case || e.caseInfo;
      if (changed) {
        if (d.state !== e.state) e.note = null;
        e.state = d.state; e.notifications = nots;
        changedAny = true;
        if (e === curEntry()) changedCur = true;
      }
    } catch (err) { /* keep polling quietly; a toast every 2 s would drown the page */ }
  }
  if (changedCur) renderAll();
  else if (changedAny) { renderTimeline(); ensurePoll(); }
}

// ---------------------------------------------------------------- phone (phone data only)
function phoneMsg(e) {
  const r = e.res, by = r.phone_by_lang || {};
  return by[S.lang] || r.phone || {};
}
function msgTier(e) { const m = phoneMsg(e); return m.tier != null ? m.tier : (e.res.phone && e.res.phone.tier) || 0; }
function secondsLeft(e) {
  const wait = (e.res.phone_meta && e.res.phone_meta.continue_after_s) || 0;
  return e.skip ? 0 : Math.max(0, wait - Math.floor((Date.now() - e.decidedAt) / 1000));
}
function clockOf(timeLabel) { const t = String(timeLabel || ""); return t.includes("· ") ? t.split("· ")[1] : (t || "11:00"); }

function statusBar(ctx, timeLabel) {
  const u = ui(), c = ctx || {};
  let pill = "";
  if (n0(c.screen_share)) pill = `<span class="callpill share"><i></i>${u.sharing}</span>`;
  else if (n0(c.on_call)) {
    const bits = [u.oncall];
    if (n0(c.call_ott)) bits.push(u.internet); else if (n0(c.caller_unknown)) bits.push(u.unknown);
    if (c.call_min != null) bits.push(`${Math.round(n0(c.call_min))}m`);
    pill = `<span class="callpill"><i></i>${esc(bits.join(" · "))}</span>`;
  }
  return `<div class="status"><span>${esc(clockOf(timeLabel))}</span>${pill}<span>4G ▮▮▮</span></div>`;
}
function smsBanner(e) {
  const n = e && e.notifications && e.notifications[e.notifications.length - 1];
  return n ? `<div class="sms" role="status"><b>${ui().sms} · ${esc(n.at || "")}</b><span>${esc(n.sms)}</span></div>` : "";
}
function base(ctx, timeLabel, e) {
  const u = ui();
  return statusBar(ctx, timeLabel) + smsBanner(e) +
    `<div class="appbar"><div class="logo">B</div><b>${u.send}</b><small>${u.bankApp}</small></div>`;
}
function payeeBlock(ref, sub) {
  const r = String(ref || "");
  return `<div class="payee"><div class="avatar">${esc(r.slice(-4) || "?")}</div><div><b>${esc(r || ui().newPayment)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}</div></div>`;
}
function payView(head, amountText, channel, extra, btns, L) {
  return `${head}<div class="pay"${L}>${payeeBlockHTML(head)}${amountText ? `<div class="amount">${esc(amountText)}</div>` : ""}
    <div class="via">${ui().via} ${esc(channel || "")}</div><div class="spacer"></div>${extra || ""}<div class="btns">${btns || ""}</div></div>`;
}
let _payeeHTML = "";
function payeeBlockHTML() { return _payeeHTML; }

function renderPhone() {
  clearInterval(S.countdown); S.countdown = null;
  const s = $("#screen"), u = ui(), L = S.lang === "hi" ? ' lang="hi"' : "";
  const e = curEntry();

  if (S.checking) {
    const c = S.checking;
    _payeeHTML = payeeBlock("", u.pickedOnPay);
    s.innerHTML = payView(base(c.ctx, S.opts && S.opts.now && S.opts.now.label), inr(c.amount), c.channel,
      `<p class="muted" style="text-align:center">${u.checking}</p>`, `<button class="btn primary" disabled>${u.pay} ${inr(c.amount)}</button>`, L);
    return;
  }
  if (!e) {
    if (!S.opts) { s.innerHTML = ""; return; }
    const amt = n0($("#f-amount") && $("#f-amount").value);
    _payeeHTML = payeeBlock("", u.pickedOnPay);
    s.innerHTML = payView(base(localContext(), S.opts.now && S.opts.now.label), amt > 0 ? inr(amt) : "", $("#f-channel") && $("#f-channel").value,
      "", `<button class="btn primary" id="ph-pay" ${amt > 0 ? "" : "disabled"}>${u.pay}${amt > 0 ? " " + inr(amt) : ""}</button>`, L);
    $("#ph-pay").addEventListener("click", pay);
    return;
  }

  const r = e.res, pmt = r.payment || {}, amountText = pmt.amount_text || inr(pmt.amount);
  const head = base(e.ctx, pmt.time, e), ref = (r.payee && r.payee.ref) || "";
  const m = phoneMsg(e), k = msgTier(e);
  _payeeHTML = payeeBlock(ref, "");
  const sheet = (tier, body, btns) => `${head}<div class="sheet"${L}><div class="bar" style="background:${TV(tier)}"></div><div class="sheet-body">${body}</div><div class="btns">${btns}</div></div>`;
  const cancelBtn = (primary) => `<button class="btn ${primary ? "primary" : "ghost"}" data-act="cancel">${u.cancel}</button>`;
  const done = (ok, title, sub) => `${head}<div class="done"${L}><div class="tick" style="background:${ok ? "var(--t0)" : "var(--muted)"}">${ok ? "✓" : "×"}</div>
    <h3>${esc(title)}</h3>${sub}<div class="spacer"></div><p class="muted">${u.ready}</p></div>`;

  switch (e.state) {
    case "pin": {
      s.innerHTML = `${head}<div class="done"${L}><b>${u.pin}</b><div class="pin"><i class="f"></i><i class="f"></i><i class="f"></i><i class="f"></i></div>
        <span class="muted">${esc(amountText)} → ${esc(ref)}</span>${e.note ? `<p class="msg" style="font-size:12.5px">${esc(e.note)}</p>` : ""}
        <div class="spacer"></div><div class="btns" style="width:100%"><button class="btn primary" data-act="confirm_pin">${u.pay} ${esc(amountText)}</button>${cancelBtn(false)}</div></div>`;
      break;
    }
    case "awaiting_customer": {
      if (k === 1) {
        s.innerHTML = payView(head, amountText, pmt.channel, `<div class="note"><b>${esc(m.headline)}</b><span>${esc(m.note)}</span></div>`,
          `<button class="btn primary" data-act="continue">${u.payAnyway}</button>${cancelBtn(false)}`, L);
      } else {
        s.innerHTML = alertSheet(e, m, k, sheet, head);
      }
      break;
    }
    case "held":
      s.innerHTML = alertSheet(e, m, 4, sheet, head);
      break;
    case "review_requested":
      s.innerHTML = sheet(4, `<h3>${esc(m.headline || u.reviewAsked)}</h3>${e.note ? `<p class="msg">${esc(e.note)}</p>` : ""}${m.hold_note ? `<p class="hold">${esc(m.hold_note)}</p>` : ""}`,
        cancelBtn(true));
      break;
    case "call_bank":
      s.innerHTML = sheet(Math.max(k, 2), `<h3>${u.callBank}</h3><p class="msg">${esc(e.note || "")}</p>${needsPoll(e) ? `<p class="hold">${u.stillHeld}</p>` : ""}`,
        cancelBtn(true));
      break;
    case "trusted_pending":
      s.innerHTML = sheet(3, `<h3>${u.trustedSent}</h3><p class="advice">${u.trustedSees}</p>
        <div class="trusted-demo"><span class="label">${u.trustedPhone}</span>
        <div class="row-in"><button class="btn primary" data-act="trusted_ok">${u.approve}</button><button class="btn" data-act="trusted_declined">${u.decline}</button></div></div>`,
        cancelBtn(false));
      break;
    case "callback_pending":
      s.innerHTML = sheet(3, `<h3>${u.callbackSent}</h3><p class="advice">${u.callbackSees}</p>`,
        `<button class="btn" data-act="wait">${u.waitHour}</button>${cancelBtn(false)}`);
      break;
    case "waiting":
      s.innerHTML = sheet(3, `<h3>${esc(e.note || "")}</h3>${e.until ? `<p class="advice">${esc(e.until)}</p>` : ""}`, cancelBtn(false));
      break;
    case "paid":
      s.innerHTML = done(true, `${u.paid} ${amountText}`, `<span class="muted">${esc(ref)}</span>${e.utr ? `<span class="mono muted">UTR ${esc(e.utr)}</span>` : ""}`);
      break;
    case "cancelled":
      s.innerHTML = done(false, u.cancelled, `<p>${u.noMoney}</p>`);
      break;
    default:
      s.innerHTML = sheet(k, `<h3>${esc(e.state)}</h3><p class="msg">${esc(e.note || "")}</p>`, cancelBtn(false));
  }

  $$("[data-act]", s).forEach(b => b.addEventListener("click", () => act(b.dataset.act)));
  startCountdown(e);
}

function alertSheet(e, m, k, sheet, head) {
  const u = ui();
  const why = (m.why || []).length ? `<div><div class="label">${u.why}</div><ul>${m.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
  const body = `<h3>${esc(m.headline)}</h3>${k === 4 && m.hold_note ? `<p class="hold">${esc(m.hold_note)}</p>` : ""}
    ${m.pattern ? `<p class="pattern">${esc(m.pattern)}</p>` : ""}${m.advice ? `<p class="advice">${esc(m.advice)}</p>` : ""}${why}`;
  const left = secondsLeft(e);
  let btns = (m.actions || []).map((a, j) => {
    if (a.id === "continue") return `<button class="btn ghost" data-act="continue" data-wait ${left > 0 ? "disabled" : ""}>${u.cont(left)}</button>`;
    return `<button class="btn ${j === 0 ? "primary" : ""}" data-act="${esc(a.id)}">${esc(a.label)}</button>`;
  }).join("");
  if (k === 2) btns += `<button class="linkbtn" data-act="this_was_me" data-wait data-twm ${left > 0 ? "disabled" : ""}>${left > 0 ? u.thisWasMeWait(left) : u.thisWasMe}</button>`;
  if (k === 4) btns += `<button class="linkbtn" data-act="this_was_me">${u.thisWasMe}</button>`;
  return sheet(k, body, btns);
}

function startCountdown(e) {
  if (e.state !== "awaiting_customer" || msgTier(e) !== 2 || secondsLeft(e) <= 0) return;
  S.countdown = setInterval(() => {
    const left = secondsLeft(e), u = ui();
    $$("#screen [data-wait]").forEach(b => {
      b.disabled = left > 0;
      b.textContent = b.hasAttribute("data-twm") ? (left > 0 ? u.thisWasMeWait(left) : u.thisWasMe) : u.cont(left);
    });
    if (left <= 0) { clearInterval(S.countdown); S.countdown = null; renderBelow(); }
  }, 1000);
}

// below the phone: judge-only affordances, outside the phone frame
function phoneLink() {
  return S.lanBase && S.session ? `${S.lanBase}/?phone=1&s=${encodeURIComponent(S.session)}` : null;
}
function phonePanel() {
  const url = phoneLink();
  if (!url) return "";
  return `<details class="onphone"><summary>📱 Show this on a judge's own phone</summary>
    <img src="/judge/qr?data=${encodeURIComponent(url)}" alt="QR code for ${esc(url)}" width="168" height="168" onerror="this.remove()">
    <span class="mono">${esc(url)}</span>
    <span class="small">Same Wi-Fi or hotspot as this laptop. The phone shows each payment you make here; its buttons act on it.</span></details>`;
}
function renderBelow() {
  const e = curEntry(), el = $("#below");
  if (!e) { el.innerHTML = phonePanel(); return; }
  const bits = [];
  const caseId = e.res.phone_meta && e.res.phone_meta.case_id;
  if (caseId) {
    const st = e.caseInfo && e.caseInfo.status === "closed" ? ` · closed: ${esc(e.caseInfo.outcome || "")}` : "";
    bits.push(`<span>Sent to officer review · <a href="#officer">${esc(caseId)}</a>${st}</span>`);
  }
  if (e.reviewCase && e.reviewCase !== caseId) {
    const what = String(e.reviewCase).startsWith("B-") ? `Call-back requested (no trusted person; the bank calls within ${CALLBACK_MIN} min)` : "Customer asked for a review";
    bits.push(`<span>${what} · <a href="#officer">${esc(e.reviewCase)}</a></span>`);
  }
  if (needsPoll(e)) bits.push(`<span class="muted">Watching for the officer's decision (every 2 s)…</span>`);
  if (e.state === "paid" && e.past && e.past.tier >= 1)
    bits.push(`<span>Alert to paid: <b>${esc(durText(e.past.s))}</b> (${esc(TIERS[e.past.tier] || "tier " + e.past.tier)})</span>`);
  if (e.state === "awaiting_customer" && msgTier(e) === 2 && secondsLeft(e) > 0)
    bits.push(`<button type="button" class="rehearse" id="skipwait">Rehearsal only: skip the 30-second wait</button>`);
  el.innerHTML = bits.join("") + phonePanel();
  const sk = $("#skipwait");
  if (sk) sk.addEventListener("click", () => { e.skip = true; renderPhone(); renderBelow(); });
}

function tierShort(e) {
  const b = e.res.bank_view || {};
  if (b.fallback) return "Fallback";
  if (b.fast_path) return "Shop · not scored";
  return TIERS[b.tier] || `Tier ${b.tier}`;
}
function renderTimeline() {
  const glyph = { paid: " ✓", cancelled: " ×", held: " ⏸", review_requested: " ⏸" };
  $("#timeline").innerHTML = S.entries.map((e, i) => {
    const t = (e.res.bank_view && e.res.bank_view.tier) || 0;
    const amt = (e.res.payment && (e.res.payment.amount_text || inr(e.res.payment.amount))) || "";
    return `<button type="button" class="tl ${i === S.cur ? "cur" : ""}" data-i="${i}" title="${esc(e.res.decision_id)} · ${esc(e.state)}">
      <span class="dot" style="background:${TV(t)}"></span>${esc(amt)} · ${esc(tierShort(e))}${glyph[e.state] || ""}</button>`;
  }).join("");
  $$("#timeline [data-i]").forEach(b => b.addEventListener("click", () => { S.cur = Number(b.dataset.i); renderAll(); }));
}

// ---------------------------------------------------------------- bank view (bank only)
const LO = 1e-4;
function pos(v) { const x = Math.max(LO, Math.min(1, v)); return (Math.log10(x) - Math.log10(LO)) / (0 - Math.log10(LO)) * 100; }
function scale(v, ticks, color) {
  return `<div class="scale" aria-hidden="true"><div class="track"></div><div class="fill" style="width:${pos(v)}%;background:${color}"></div>
    ${ticks.filter(t => t[0] != null).map(t => `<div class="tick" style="left:${pos(t[0])}%"><span>${esc(t[1])}</span></div>`).join("")}
    <div class="val" style="left:${pos(v)}%;background:${color}"></div></div>`;
}
const FAM = {
  journey: ["Journey", "bank's own records of this customer"],
  payee: ["Payee", "NPCI / MuleHunter.AI risk, I4C registry, DoT FRI, live network flag"],
  context: ["Context", "on-phone flags, only with consent; no audio, no messages"],
};
function famFacts(f, fam, consent, callerId) {
  if (fam === "journey") {
    const recentSum = Math.pow(10, n0(f.recent_payee_sum_7d_log)) - 1;
    return [
      ["New payee", n0(f.n_prev_to_payee) >= 50 ? "no, saved" : (n0(f.is_new_payee) ? "yes" : `no, ${f1(n0(f.n_prev_to_payee))} before`), n0(f.is_new_payee) && n0(f.amt_vs_usual) >= 5],
      ["Amount vs usual", "×" + f1(f.amt_vs_usual), n0(f.amt_vs_usual) >= 5],
      ["Series to new payees (7 d)", n0(f.recent_payee_payments_7d) ? `${f1(n0(f.recent_payee_payments_7d))}, ${inr(recentSum)}` : "none", n0(f.recent_payee_payments_7d) >= 2],
      ["Sent today vs usual", "×" + f1(f.push_out_24h_vs_usual), n0(f.push_out_24h_vs_usual) >= 10],
      ["FD broken (48 h)", yn(n0(f.fd_break_48h)), !!n0(f.fd_break_48h)],
      ["Credits from strangers", n0(f.stranger_credits_24h) ? f1(n0(f.stranger_credits_24h)) : "none", n0(f.stranger_credits_24h) >= 1],
    ];
  }
  if (fam === "payee") {
    const inflow = Math.pow(10, n0(f.payee_inflow_24h_log)) - 1;
    return [
      ["Opened", f.payee_age_days == null ? "–" : `${Math.max(1, Math.round(f.payee_age_days))} days ago`, f.payee_age_days != null && f.payee_age_days < 30],
      ["Idle before today", n0(f.payee_idle_days) >= 1 ? `${Math.round(f.payee_idle_days)} days` : "active today", n0(f.payee_idle_days) >= 90],
      ["New payers (24 h)", f1(n0(f.payee_new_payers_24h)), n0(f.payee_new_payers_24h) >= 3],
      ["Money in (24 h)", inr(inflow), false],
      ["Passed on (24 h)", f.payee_passthrough_24h == null ? "–" : pct(Math.min(1, f.payee_passthrough_24h)), n0(f.payee_passthrough_24h) >= 0.8 && n0(f.payee_inflow_24h_log) >= 3],
      ["I4C suspect registry", yn(n0(f.payee_registry)), !!n0(f.payee_registry)],
      ["DoT fraud-risk (FRI)", BAND[n0(f.payee_fri_band)] || "none", n0(f.payee_fri_band) >= 2],
      ["Live mule network", n0(f.payee_in_campaign) ? `yes, ${f1(n0(f.payee_campaign_size))} accounts` : "no", !!n0(f.payee_in_campaign)],
    ];
  }
  if (!consent) return null;
  return [
    ["On a call", n0(f.on_call) ? (callerId && n0(f.caller_unknown) ? "yes, unknown number" : "yes") + (n0(f.call_ott) ? ", internet call" : "") : "no", n0(f.on_call) && (callerId ? n0(f.caller_unknown) : n0(f.call_min) >= 40)],
    ["Call length", n0(f.on_call) ? `${Math.round(n0(f.call_min))} min` : "–", n0(f.on_call) && n0(f.call_min) >= 40],
    callerId ? ["Caller's FRI band", n0(f.on_call) ? (BAND[n0(f.caller_fri_band)] || "none") : "–", n0(f.caller_fri_band) >= 2]
             : ["Who is calling", n0(f.on_call) ? "not readable by a bank app" : "–", false],
    ["Screen shared", yn(n0(f.screen_share)), !!n0(f.screen_share)],
    ["Remote-access app", yn(n0(f.remote_app)), !!n0(f.remote_app)],
    ["App switches", f1(n0(f.app_switches)), n0(f.app_switches) >= 4],
  ];
}
function weightChip(x) {
  if (x.weight == null) {      // not a model signal: memory of an earlier warning, or a plain fact about the payment
    const mem = x.family === "memory", rep = x.code === "BANK_REPORTED";
    const tip = mem ? "From this customer's earlier warnings, not from the model"
      : rep ? "This bank's own confirmed report, shared with every customer at once"
      : "A true fact about this payment, shown because no single signal is above its own line";
    return `<span class="lr fam-${esc(x.family)} nw" title="${esc(tip)} (${esc(x.code)})">${mem ? "memory" : rep ? "bank report" : "fact"}</span>`;
  }
  return `<span class="lr fam-${esc(x.family)}" title="How much more often this appears in reported scams than in other payments (${esc(x.code)})">×${f1(x.weight)}</span>`;
}
function agoText(min) {
  const m = n0(min);
  return m < 60 ? `${Math.max(1, Math.round(m))} min` : m < 2880 ? `${f1(m / 60)} h` : `${f1(m / 1440)} days`;
}
function durText(sec) {
  const v = Math.max(0, Math.round(n0(sec)));
  return v < 120 ? `${v} s` : `${Math.floor(v / 60)} min ${v % 60} s`;
}
function memoryRung(b) {
  if (!b.memory_note) return "";
  const k = b.memory && b.memory.kind;
  const ago = b.memory && b.memory.minutes_since != null ? ` (${agoText(b.memory.minutes_since)} ago)` : "";
  const text = {
    continued: `the customer went ahead past a warning in the last ${MEMORY_DAYS} days${ago}, so a note counts as a pause (a trusted check only for ₹50,000+ or a flagged payee; a hold if its evidence is there).`,
    retry: `the customer didn't go ahead with a warning in the last ${RETRY_HOURS} hours${ago}, so a payment to a new payee, or to the cancelled one, is at least a pause.`,
    confirmed: `a bank officer released a payment to this payee for this customer${ago} and no new outside flag has appeared since, so it is allowed.`,
  }[k] || `${b.memory_note}.`;
  return `<div class="rung mem"><span class="ok">${k === "confirmed" ? "✓" : "↑"}</span><span><b>Memory</b>: ${esc(text)}</span></div>`;
}
function truthText(t) {
  if (!t) return "";
  const what = t.mule ? `mule (${t.mule})` : `genuine${t.role ? " " + t.role : ""} account`;
  return `${what}${t.campaign != null ? `, campaign ${t.campaign}` : ""}`;
}
function timingText(b) {
  const t = Object.assign({}, b.timing_ms || {});
  if (b.decision_ms != null && t.decision == null) t.decision = b.decision_ms;
  const order = ["features", "decision", "total"];
  const keys = order.filter(k => t[k] != null).concat(Object.keys(t).filter(k => !order.includes(k) && !k.startsWith("_")));
  return keys.map(k => `${k.replace(/_/g, " ")} ${f1(Number(t[k]))} ms`).join(" · ");
}

function renderBank() {
  const el = $("#bank"), e = curEntry();
  if (!e) {
    el.innerHTML = `<div class="panel"><div class="panel-h"><h3>Bank view</h3><span class="bank-tag">bank only</span></div>
      <p class="muted">Build a payment on the left and press <b>Pay</b>. This side shows what Thehro saw and decided.
      None of it reaches the customer's phone.</p></div>`;
    return;
  }
  const r = e.res, b = r.bank_view || {}, tier = b.tier || 0, pmt = r.payment || {};
  const consent = !!(r.sent_from_phone && r.sent_from_phone.consent);
  const scores = b.scores || null, L = b.lines || {}, s = scores ? scores.s_fused : null;
  const special = !!(b.fallback || b.fast_path);

  const banners = [
    b.fallback ? `<div class="xbanner red"><b>Fallback</b><span>${esc(b.fallback)}</span></div>` : "",
    b.fast_path ? `<div class="xbanner grey"><b>Shop payment</b><span>${esc(b.fast_path)}</span></div>` : "",
    b.memory_note ? `<div class="xbanner amber"><b>Memory</b><span>${esc(b.memory_note)}</span></div>` : "",
    b.feeds_down ? `<div class="xbanner amber"><b>Feeds down</b><span>${esc(b.feeds_down)}</span></div>` : "",
    b.bank_reported ? `<div class="xbanner red"><b>Reported by this bank</b><span>A fraud officer confirmed this account as a scam ${esc(b.bank_reported.minutes_ago)} min ago (case ${esc(b.bank_reported.case_id)}); it is flagged for every customer.</span></div>` : "",
  ].join("");

  let ladder = "";
  if (scores && !special && s != null) {
    const amt = n0(pmt.amount);
    const rung = (ok, name, rule) => `<div class="rung ${ok ? "yes" : ""}"><span class="ok">${ok ? "✓" : "–"}</span><span><b>${name}</b>: ${rule}</span></div>`;
    ladder = `<div class="ladder">
      ${rung(s >= L.note, "Note", "risk above the note line (genuine payments cross it 1 in 1,000)")}
      ${rung(s >= L.pause || (b.payee_flag && n0((b.features || {}).is_new_payee)), "Pause and explain", "risk above the pause line (genuine payments cross it 1 in 10,000), or a first payment to a payee with an outside flag")}
      ${rung(s >= L.pause && b.gate && amt >= TRUSTED_MIN, "Trusted check", `pause, two families agree, and ${inr(TRUSTED_MIN)} or more`)}
      ${rung(b.gate && b.payee_flag && s >= L.hold && amt >= HOLD_MIN, "Hold for review", `two families agree, an outside flag on the payee, risk above the hold line, ${inr(HOLD_MIN)} or more`)}
      ${memoryRung(b)}
    </div>`;
  }
  const nfam = b.families ? Object.values(b.families).filter(Boolean).length : 0;
  const guess = b.workflow_guess ? `<p class="small">Model's guess (officers only, never shown to the customer): <b>${esc(b.workflow_guess)}</b>${b.workflow_confidence != null ? ` (${pct(b.workflow_confidence)})` : ""}</p>` : "";
  const decision = `<div class="panel"><div class="panel-h"><h3>Decision</h3><span class="bank-tag">bank only</span></div>
    ${banners}
    <div class="verdict" style="background:${TS(tier)}"><span class="dot" style="background:${TV(tier)};width:12px;height:12px"></span>
      <b style="color:${TV(tier)}">Tier ${tier}: ${esc(b.tier_name || TIERS[tier])}${b.fallback ? " (bank's own rules)" : ""}</b>
      <span class="mono">${esc(r.decision_id)} · ${esc(pmt.time || "")} · ${esc(pmt.amount_text || inr(pmt.amount))}</span></div>
    ${scores && s != null ? `${scale(s, [[L.note, "note"], [L.pause, "pause"]].concat(L.hold != null && L.hold !== L.pause ? [[L.hold, "hold"]] : []), TV(tier))}
      <p style="font-size:12.5px">Fused risk ${s.toFixed(3)} · ${nfam} of 3 families elevated · ${b.gate ? "two kinds of evidence agree" : "fewer than two families agree"}${b.payee_flag ? " · outside flag on the payee" : ""}</p>` : ""}
    ${ladder}${guess}</div>`;

  let fams = "";
  if (b.features && scores) {
    fams = `<div class="panel"><div class="panel-h"><h3>What Thehro saw</h3><span class="label">three families of evidence</span></div><div class="fams">${
      ["journey", "payee", "context"].map(k => {
        const facts = famFacts(b.features, k, consent, b.caller_id !== false), el_ = !!(b.families && b.families[k]);
        const sc = scores["s_" + k], line = b.family_lines ? b.family_lines[k] : null;
        const body = facts ? `${sc != null ? scale(sc, [[line, "elevated"]], el_ ? "var(--t2)" : "var(--muted)") : ""}
          <div class="facts">${facts.map(x => `<div class="fact ${x[2] ? "hot" : ""}"><span>${esc(x[0])}</span><span>${esc(x[1])}</span></div>`).join("")}</div>`
          : `<p class="muted" style="font-size:12px">Off: not shared, so this family doesn't vote. The other two families still protect the customer.</p>`;
        return `<div class="fam ${el_ ? "el" : ""}"><div class="fam-h"><b>${FAM[k][0]}</b><span class="chip ${el_ ? "el" : ""}">${facts ? (el_ ? "elevated" : "normal") : "off"}</span></div>
          <span class="src">${esc(FAM[k][1])}</span>${body}</div>`;
      }).join("")}</div></div>`;
  }

  const reasons = (b.reasons || []).length
    ? `<ul class="reasons">${b.reasons.map(x => `<li>${weightChip(x)}<span ${S.lang === "hi" ? 'lang="hi"' : ""}>${esc(x[S.lang] || x.en || x.code)}</span></li>`).join("")}</ul>`
    : `<p class="muted" style="font-size:12.5px">${special ? "Not scored." : "No warning signs above their lines."}</p>`;
  const payee = r.payee || {};
  const why = `<div class="two">
    <div class="panel"><div class="panel-h"><h3>Why</h3><span class="label">reasons × weight</span></div>${reasons}</div>
    <div class="panel"><div class="panel-h"><h3>Payee</h3><span class="mono">${esc(payee.ref || "")}</span></div>
      <p class="small">${esc(payeeLabel(payee.type))}</p>
      ${(payee.facts || []).length ? `<ul class="reasons">${payee.facts.map(x => `<li><span class="lr">•</span><span>${esc(x)}</span></li>`).join("")}</ul>` : ""}
      ${payee.truth ? `<p class="truth">Simulation truth (the model never sees this): ${esc(truthText(payee.truth))}</p>` : ""}</div></div>`;

  const caseId = r.phone_meta && r.phone_meta.case_id;
  const casePanel = caseId ? `<div class="panel case"><div class="panel-h"><h3>Hold case ${esc(caseId)}</h3><a href="#officer">Open in the Officer tab →</a></div>
    <dl class="kv"><dt>Status</dt><dd>${esc(e.caseInfo ? `${e.caseInfo.status}${e.caseInfo.outcome ? ": " + e.caseInfo.outcome : ""}` : "open")}</dd>
    <dt>Customer's screen</dt><dd>${esc(e.state)}</dd>
    <dt>Rule</dt><dd>Only an officer releases or cancels it. No answer within 30 minutes means cancelled, and the money stays put.</dd></dl></div>` : "";

  const sent = r.sent_from_phone || {}, dropped = r.dropped_from_phone || [];
  const left = `<div class="panel"><div class="panel-h"><h3>What left the phone</h3><span class="label">${sent.consent ? "consent on" : "consent off"}</span></div>
    <pre class="json">${esc(JSON.stringify(sent, null, 1))}</pre>
    ${!sent.context && !b.fast_path ? `<p class="small">No phone context in this request.</p>` : ""}
    ${dropped.length ? `<div class="chips">${dropped.map(d => `<span class="chip drop">dropped: ${esc(d)} (${d === "caller_unknown" && sent.consent ? "a bank app can't read the caller's number" : sent.consent ? "never leaves the phone" : "consent off"})</span>`).join("")}</div>` : ""}</div>`;
  const st = r.stored || {}, rec = st.record || {}, body = rec.body || {};
  const stored = `<div class="panel"><div class="panel-h"><h3>What we stored</h3><span class="label">${st.kind === "evidence" ? `evidence #${esc(st.seq)}` : "nothing extra"}</span></div>
    ${st.kind === "evidence" ? `<dl class="kv">
        <dt>Record</dt><dd>#${esc(rec.seq ?? st.seq)} · ${esc(rec.kind || "decision")} · ${esc(rec.at || "")}</dd>
        <dt>Hash</dt><dd class="mono">${esc(String(rec.hash || "").slice(0, 16))}…</dd>
        <dt>Previous</dt><dd class="mono">${esc(String(rec.prev_hash || "").slice(0, 16))}…</dd>
        <dt>Chain</dt><dd>${st.chain_ok === false ? `<span class="bad-chain">chain broken ✗</span>` : `<span class="ok-chain">chain verified ✓</span>`}</dd>
        <dt>Reason codes</dt><dd class="mono">${esc((body.reasons || []).join(", ") || "–")}</dd></dl>
        <details><summary>Full record</summary><pre class="json">${esc(JSON.stringify(rec, null, 1))}</pre></details>`
      : `<p style="font-size:12.5px">${esc(st.note || "Nothing stored beyond the bank's normal payment ledger.")}</p>`}
    ${(st.not_kept || []).length ? `<p class="small">Never kept: ${esc(st.not_kept.join(", "))}.</p>` : ""}</div>`;

  const timing = timingText(b);
  el.innerHTML = decision + fams + why + casePanel + `<div class="two">${left}${stored}</div>` +
    (timing ? `<p class="small" style="text-align:right">${esc(timing)}</p>` : "");
}

function renderAll() {
  if (PHONE) { renderPhone(); return; }
  renderPhone(); renderBelow(); renderTimeline(); renderBank(); ensurePoll(); sameOptions();
}

// ---------------------------------------------------------------- Kit tab: K2 random draws and K1 cases
const KIT = { cases: null, k1: {}, busy: false };
const tierDot = t => `<span class="tierchip"><span class="dot" style="background:${TV(t)}"></span>${t} ${esc(TIERS[t] || "")}</span>`;
const shareText = d => d && d.n ? `${pct(d.value, 1)} (95% CI ${pct(d.lo, 1)}–${pct(d.hi, 1)})` : "–";

function loadKit() {
  renderK2();
  if (KIT.cases) { renderK1(); return; }
  api("GET", "/kit/cases").then(d => { KIT.cases = d; renderK1(); })
    .catch(e => { $("#kit-k1").innerHTML = `<p class="hint warn">${esc(e.message)}</p>`; });
}

function renderK2() {
  const el = $("#kit-k2");
  if (el.dataset.ready) return;
  el.dataset.ready = "1";
  el.innerHTML = `<div class="panel-h"><h3>K2 · random labelled draws</h3><span class="label">you pick the seed</span></div>
    <p class="small">Fresh samples from the test days (days 42–59, never used for training or for setting the alert lines).
      Each one runs through the same decision service as the Judge tab, after replaying that customer's earlier test-day
      payments so memory is right, and is checked against the offline evaluation. Same draws as
      <span class="mono">scripts/spot_check.py --seed N</span>.</p>
    <div class="kitform">
      <label class="field"><span>Seed</span><input id="k2-seed" type="number" min="0" step="1" inputmode="numeric" value="${1000 + Math.floor(Math.random() * 9000)}"></label>
      <label class="field"><span>Draw</span><select id="k2-mode">
        <option value="">60 payments: 25 scam, 25 risky-looking genuine, 10 ordinary genuine</option>
        <option value="30">30 scam victims: right if any of their payments was warned</option></select></label>
      <button type="button" class="btn primary" id="k2-run">Draw and run</button>
    </div>
    <p class="live-line" id="k2-status" hidden></p>
    <div id="k2-out"></div>`;
  $("#k2-run").addEventListener("click", runK2);
}

async function runK2() {
  if (KIT.busy) return;
  const seed = Number($("#k2-seed").value), journeys = $("#k2-mode").value ? Number($("#k2-mode").value) : null;
  if (!Number.isInteger(seed) || seed < 0) { toast("The seed must be a whole number, 0 or more", "err"); return; }
  const st = $("#k2-status");
  const say = (text, warn) => { st.hidden = !text; st.textContent = text || ""; st.className = "live-line" + (warn ? " warnline" : ""); };
  KIT.busy = true; $("#k2-run").disabled = true;
  say(`Drawing with seed ${seed}…`);
  try {
    for (;;) {
      const r = await api("POST", "/kit/spot_check", journeys ? { seed, journeys } : { seed });
      if (r.status === "ran") { say(""); renderK2Result(r); break; }
      if (r.status === "loading") {
        say(`Loading the frozen Round 1 baseline, once per server (about a minute; ${r.baseline.seconds || 0} s so far). ` +
            "It first checks that it reproduces Round 1: 679 of 720 victims warned, 70 false pauses.");
        await new Promise(res => setTimeout(res, POLL_MS));
        continue;
      }
      say((r.baseline && r.baseline.error) || `Can't run the draw (${r.status}).`, true);
      break;
    }
  } catch (e) {
    say(e.message, true);
  } finally {
    KIT.busy = false; $("#k2-run").disabled = false;
  }
}

function renderK2Result(r) {
  const sm = r.summary, journey = r.level === "journey";
  const head = journey
    ? `<b>${sm.right} of ${sm.n} victims warned</b> · ${shareText(sm.right_share)}`
    : `<b>${sm.right} of ${sm.n} right</b> · scam payments interrupted ${sm.scam_right}/${sm.n_scam} · genuine left alone ${sm.genuine_right}/${sm.n_genuine}`;
  const rows = journey
    ? r.samples.map(x => `<tr class="${x.right ? "" : "miss"}"><td>${x.sample}</td><td>${esc(x.scam_type)}</td><td>${esc(x.amount_text)}</td>
        <td>${x.payment_tiers.map(t => `<span class="dot" title="tier ${t}" style="background:${TV(t)}"></span>`).join("")}</td>
        <td>${x.first_warned_payment ? `payment ${x.first_warned_payment} of ${x.n_payments}` : "–"}</td><td>${x.right ? "✓" : "✗"}</td>
        <td>${x.live_equals_evaluation ? "same" : "<b>differs</b>"}</td><td class="small">${esc(x.top_reason)}</td></tr>`).join("")
    : r.samples.map(x => `<tr class="${x.right ? "" : "miss"}"><td>${x.sample}</td><td>${esc(x.label)}</td>
        <td>${esc(x.scam_type || x.kind)}</td><td>${esc(x.amount_text)}</td><td>${esc(x.expected.replace("_", " "))}</td>
        <td>${tierDot(x.got.tier)}</td><td>${x.right ? "✓" : "✗"}</td><td>${x.live_equals_evaluation ? "same" : "<b>differs</b>"}</td>
        <td class="small">${esc(x.top_reason)}</td></tr>`).join("");
  const cols = journey
    ? ["#", "Scam type", "Total", "Tier of each payment", "First warned", "Right", "Live vs offline", "First reason"]
    : ["#", "Truth", "Kind", "Amount", "Expected", "Got", "Right", "Live vs offline", "First reason"];
  $("#k2-out").innerHTML = `<div class="k2sum">${head}<br><span class="small">Live decision = offline evaluation in
      ${sm.live_equals_evaluation} of ${sm.n} · seed ${r.seed} · synthetic, measured · misses stay in</span></div>
    <p class="small">${esc(r.note)}</p>
    <div class="tbl-wrap"><table class="kt"><thead><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderK1() {
  const d = KIT.cases, el = $("#kit-k1"), cases = d.cases || [];
  const waiting = cases.filter(c => !c.registered).length;
  el.innerHTML = `<div class="panel-h"><h3>K1 · hand-written cases</h3><span class="label">${cases.length} cases, expected outcome written first</span></div>
    <p class="small">Written by a teammate who didn't build the model, with the expected outcome, and frozen (SHA-256 list) before anything ran on them.
      Every miss stays in. Interrupt = tier 2 or higher.</p>
    ${waiting ? `<p class="hint warn">${esc(d.rule)} ${waiting} of ${cases.length} aren't on the frozen list (or changed since), so they can't run here.</p>` : ""}
    <div class="row-in"><button type="button" class="btn" id="k1-all" ${!waiting && cases.length ? "" : "disabled"}>Run all ${cases.length}</button>
      <span class="small" id="k1-score">${k1Score()}</span></div>
    <div class="tbl-wrap"><table class="kt"><thead><tr><th>Case</th><th>Expected</th><th>Got</th><th>Right</th><th></th></tr></thead><tbody>
    ${cases.map(c => { const r = KIT.k1[c.id];
      return `<tr class="${r && r.right === false ? "miss" : ""}"><td><b>${esc(c.id)}</b> · ${esc(c.title)}
          <small class="k1why">${esc(c.group)} · ${esc(c.truth)} · ${esc(c.why)}</small></td>
        <td>${esc(String(c.expected).replace("_", " "))}</td><td>${r ? tierDot(r.got.tier) : "–"}</td>
        <td>${r ? (r.right ? "✓" : "✗") : ""}</td>
        <td><button type="button" class="btn small" data-k1="${esc(c.id)}" ${c.registered ? "" : "disabled title=\"not on the frozen list\""}>Run</button>
          ${r ? `<button type="button" class="linkbtn" data-k1open="${esc(c.id)}">open</button>` : ""}</td></tr>`; }).join("")}
    </tbody></table></div>`;
  $$("[data-k1]", el).forEach(b => b.addEventListener("click", () => runK1([b.dataset.k1])));
  $$("[data-k1open]", el).forEach(b => b.addEventListener("click", () => openK1(b.dataset.k1open)));
  const all = $("#k1-all");
  if (all) all.addEventListener("click", () => runK1(cases.map(c => c.id)));
}
function k1Score() {
  const done = Object.values(KIT.k1);
  return done.length ? `${done.filter(r => r.right).length} of ${done.length} run so far are right` : "";
}
async function runK1(ids) {
  if (KIT.busy) return;
  KIT.busy = true;
  try {
    for (const id of ids) {
      try { KIT.k1[id] = await api("POST", "/kit/run", { case_id: id }); }
      catch (e) { toast(`${id}: ${e.message}`, "err"); break; }
    }
  } finally { KIT.busy = false; }
  renderK1();
}
function openK1(id) {
  // show the whole decision (phone, bank view, officer case) in the Judge tab
  const r = KIT.k1[id];
  if (!r) return;
  S.entries.push({ res: r.decision, state: r.decision.state, note: null, notifications: [], ctx: (r.decision.sent_from_phone || {}).context || {},
                   decidedAt: Date.now(), skip: false, utr: null, until: null, reviewCase: null, caseInfo: null, kit: id });
  S.cur = S.entries.length - 1;
  location.hash = "#judge";
  renderAll();
}

// ---------------------------------------------------------------- Results tab: test_kit/report.md
async function loadReport() {
  const el = $("#report");
  try {
    const d = await api("GET", "/kit/report");
    $("#report-updated").textContent = `test_kit/report.md · generated ${d.updated}`;
    el.innerHTML = markdown(d.markdown);
  } catch (e) {
    el.innerHTML = `<p class="hint warn">${esc(e.message)}</p>`;
  }
}
function inlineMd(t) {
  return esc(t).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>").replace(/(^|[\s(])_([^_\s][^_]*)_(?=[\s.,;:)]|$)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, txt, url) => /^https?:/.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${txt}</a>` : txt);
}
function markdown(src) {
  // the small subset the kit's report uses: headings, paragraphs, lists, tables, code blocks, bold, italics, code
  const lines = src.replace(/\r/g, "").split("\n"), out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const code = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++; out.push(`<pre class="json">${esc(code.join("\n"))}</pre>`); continue;
    }
    const h = /^(#{1,4})\s+(.*)/.exec(l);
    if (h) { const n = Math.min(4, h[1].length + 1); out.push(`<h${n}>${inlineMd(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      const cells = r => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const body = rows.filter((r, k) => !(k === 1 && /^[\s|:-]+$/.test(r)));
      out.push(`<div class="tbl-wrap"><table class="kt"><thead><tr>${cells(body[0]).map(c => `<th>${inlineMd(c)}</th>`).join("")}</tr></thead><tbody>${
        body.slice(1).map(r => `<tr>${cells(r).map(c => `<td>${inlineMd(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l), items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, "");
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+\.)\s+/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(`<li>${inlineMd(item)}</li>`);
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*\||\s*([-*]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inlineMd(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- start
async function initPhone() {
  document.body.classList.add("phone-mode");
  S.session = PHONE_SESSION;
  $$(".lang [data-lang]").forEach(b => b.addEventListener("click", () => {
    S.lang = b.dataset.lang;
    $$(".lang [data-lang]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    renderPhone();
  }));
  try { S.opts = await api("GET", "/judge/options"); } catch (e) { toast(e.message, "err"); }
  const follow = async () => {
    let d;
    try { d = await api("GET", `/judge/session/${encodeURIComponent(S.session)}/latest`); }
    catch (e) { $("#screen").innerHTML = `<div class="done"><p class="hint warn">${esc(e.message)}</p></div>`; return; }
    const cur = curEntry(), res = d.decision;
    if (!res) {
      $("#screen").innerHTML = `<div class="done"><h3>Waiting for the next payment…</h3><p class="muted">Make a payment on the judge page; it appears here.</p></div>`;
      return;
    }
    if (!cur || cur.res.decision_id !== res.decision_id) {
      S.entries = [{ res, state: res.state, note: null, notifications: d.notifications || [],
                     ctx: (res.sent_from_phone || {}).context || {}, decidedAt: Date.now() - 1000 * (d.age_s || 0),
                     skip: false, utr: null, until: null, reviewCase: null, caseInfo: d.case }];
      S.cur = 0;
      renderPhone();
    } else if (cur.state !== res.state || (d.notifications || []).length !== cur.notifications.length) {
      if (cur.state !== res.state) cur.note = null;
      cur.state = res.state; cur.notifications = d.notifications || []; cur.caseInfo = d.case;
      renderPhone();
    }
  };
  await follow();
  setInterval(follow, 1500);
}

async function init() {
  if (PHONE && PHONE_SESSION) return initPhone();
  bindTop();
  renderBank();
  try {
    const [sess, opts] = await Promise.all([api("POST", "/judge/session", {}), api("GET", "/judge/options")]);
    S.session = sess.session_id; S.opts = opts;
    api("GET", "/judge/lan").then(l => { S.lanBase = l.base; renderBelow(); }).catch(() => {});
  } catch (e) {
    $("#builder").innerHTML = `<p class="hint warn">${esc(e.message)}</p><button type="button" class="btn" onclick="location.reload()">Try again</button>`;
    toast(e.message, "err");
    return;
  }
  renderBuilder();
  renderAll();
}
init();
})();
