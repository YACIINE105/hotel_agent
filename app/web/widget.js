/* Hotel Agent guest widget.
 * Embed: <script src="https://HOST/static/widget.js" data-hotel="atlas-bay" defer></script>
 * Optional: data-api="https://HOST" data-lang="fr" data-open="true"
 * Renders in a Shadow DOM so hotel site CSS cannot break it (and vice versa).
 */
(() => {
  const script = document.currentScript;
  const HOTEL = script.dataset.hotel;
  const API = (script.dataset.api || new URL(script.src).origin).replace(/\/$/, "");
  const STORE_KEY = `hotel-agent:${HOTEL}`;

  const T = {
    en: { placeholder: "Ask about rooms, services, or your booking…", send: "Send", choose: "Choose",
      confirm: "Confirm booking", confirming: "Confirming…", total: "Total", payAtHotel: "Payable at the hotel",
      refundable: "Free cancellation", nonRefundable: "Non-refundable", nights: "nights", left: "left",
      confirmed: "Booking confirmed", reference: "Reference", listening: "Listening…", thinking: "Thinking…",
      voiceOn: "Voice on", voiceOff: "Voice", staff: "Hotel team", handoff: "You're connected to our team.",
      priceChanged: "The price changed. Please review the updated summary.", failed: "Booking not completed",
      review: "Staff will verify your booking", chose: "I'd like the {room} ({rate}).", guest: "Guest",
      micDenied: "Microphone unavailable. Use localhost or HTTPS and allow microphone access.", speaking: "Speaking…" },
    fr: { placeholder: "Chambres, services ou votre réservation…", send: "Envoyer", choose: "Choisir",
      confirm: "Confirmer la réservation", confirming: "Confirmation…", total: "Total", payAtHotel: "À payer à l'hôtel",
      refundable: "Annulation gratuite", nonRefundable: "Non remboursable", nights: "nuits", left: "restantes",
      confirmed: "Réservation confirmée", reference: "Référence", listening: "J'écoute…", thinking: "Réflexion…",
      voiceOn: "Voix activée", voiceOff: "Voix", staff: "Équipe de l'hôtel", handoff: "Vous êtes en contact avec notre équipe.",
      priceChanged: "Le prix a changé. Vérifiez le nouveau récapitulatif.", failed: "Réservation non effectuée",
      review: "Notre équipe va vérifier votre réservation", chose: "Je souhaite la {room} ({rate}).", guest: "Client",
      micDenied: "Micro indisponible. Utilisez localhost ou HTTPS et autorisez le micro.", speaking: "Réponse…" },
    ar: { placeholder: "اسأل عن الغرف أو الخدمات أو حجزك…", send: "إرسال", choose: "اختيار",
      confirm: "تأكيد الحجز", confirming: "جارٍ التأكيد…", total: "المجموع", payAtHotel: "يدفع في الفندق",
      refundable: "إلغاء مجاني", nonRefundable: "غير قابل للاسترداد", nights: "ليالٍ", left: "متبقية",
      confirmed: "تم تأكيد الحجز", reference: "رقم الحجز", listening: "أستمع…", thinking: "أفكر…",
      voiceOn: "الصوت مفعل", voiceOff: "صوت", staff: "فريق الفندق", handoff: "أنت الآن على تواصل مع فريقنا.",
      priceChanged: "تغير السعر. يرجى مراجعة الملخص الجديد.", failed: "لم يكتمل الحجز",
      review: "سيتحقق فريقنا من حجزك", chose: "أرغب في {room} ({rate}).", guest: "الضيف",
      micDenied: "الميكروفون غير متاح. استخدم localhost أو HTTPS واسمح بالوصول.", speaking: "يتحدث…" },
  };

  const state = { conv: null, token: null, lang: script.dataset.lang || null, languages: ["en"], name: "",
    currency: "EUR", voiceLangs: [], busy: false, lastId: 0, paused: false, poll: null };
  let t = T.en;

  // ---------- DOM ----------
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483000;bottom:20px;right:20px;";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
  <style>
    :host { all: initial; }
    * { box-sizing: border-box; font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans Arabic", sans-serif; }
    .launcher { width: 60px; height: 60px; border-radius: 50%; border: 0; background: #0f4c5c; color: #fff; font-size: 26px;
      cursor: pointer; box-shadow: 0 6px 20px rgba(0,0,0,.25); float: right; }
    .panel { display: none; width: min(400px, calc(100vw - 32px)); height: min(640px, calc(100vh - 110px)); background: #fff;
      border-radius: 16px; box-shadow: 0 12px 40px rgba(0,0,0,.25); overflow: hidden; flex-direction: column; margin-bottom: 12px; color: #1b1f23; }
    .panel.open { display: flex; }
    header { background: #0f4c5c; color: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 8px; }
    header .title { flex: 1; font-weight: 600; font-size: 15px; }
    header select, header button { background: rgba(255,255,255,.15); color: #fff; border: 1px solid rgba(255,255,255,.3);
      border-radius: 8px; padding: 4px 8px; font-size: 13px; cursor: pointer; }
    header select option { color: #000; }
    header button.on { background: #e36414; border-color: #e36414; }
    .log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; background: #f6f7f9; }
    .msg { max-width: 85%; padding: 9px 12px; border-radius: 14px; font-size: 14px; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
    .guest { align-self: flex-end; background: #0f4c5c; color: #fff; border-bottom-right-radius: 4px; }
    .ai { align-self: flex-start; background: #fff; border: 1px solid #e3e6ea; border-bottom-left-radius: 4px; }
    .staff { align-self: flex-start; background: #fff4e5; border: 1px solid #f3c98b; }
    .staff::before { content: attr(data-label); display: block; font-size: 11px; font-weight: 600; color: #a05a00; margin-bottom: 2px; }
    .system { align-self: center; font-size: 12px; color: #5f6b76; text-align: center; }
    .status { align-self: flex-start; font-size: 12px; color: #5f6b76; display: flex; gap: 6px; align-items: center; }
    .dot { width: 6px; height: 6px; border-radius: 50%; background: #0f4c5c; animation: pulse 1s infinite; }
    @keyframes pulse { 50% { opacity: .2; } }
    .cards { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; align-self: stretch; }
    .card { min-width: 210px; background: #fff; border: 1px solid #dfe3e8; border-radius: 12px; padding: 10px; font-size: 13px; }
    .card h4 { margin: 0 0 4px; font-size: 14px; }
    .card .price { font-size: 17px; font-weight: 700; margin: 6px 0 2px; }
    .muted { color: #5f6b76; font-size: 12px; }
    .tag { display: inline-block; font-size: 11px; padding: 2px 6px; border-radius: 6px; background: #e6f4ea; color: #1e7a3c; margin-top: 4px; }
    .tag.nr { background: #fdecea; color: #b3261e; }
    .card button, .quote button { margin-top: 8px; width: 100%; border: 0; border-radius: 8px; padding: 8px; background: #0f4c5c; color: #fff; cursor: pointer; font-size: 13px; }
    .card button:disabled, .quote button:disabled { opacity: .5; cursor: default; }
    .quote { align-self: stretch; background: #fff; border: 2px solid #0f4c5c; border-radius: 12px; padding: 12px; font-size: 13px; }
    .quote dl { display: grid; grid-template-columns: auto 1fr; gap: 3px 10px; margin: 6px 0; }
    .quote dt { color: #5f6b76; } .quote dd { margin: 0; }
    .result { align-self: stretch; border-radius: 12px; padding: 10px 12px; font-size: 13px; background: #e6f4ea; border: 1px solid #9bd3ae; }
    .result.bad { background: #fdecea; border-color: #f1a9a3; }
    form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e3e6ea; background: #fff; }
    input { flex: 1; border: 1px solid #cfd5dc; border-radius: 10px; padding: 10px; font-size: 14px; outline: none; }
    input:focus { border-color: #0f4c5c; }
    form button { border: 0; border-radius: 10px; padding: 0 14px; background: #0f4c5c; color: #fff; cursor: pointer; }
    .meter { height: 3px; background: #e36414; width: 0; transition: width .08s; }
    [dir=rtl] .guest { align-self: flex-start; } [dir=rtl] .ai, [dir=rtl] .staff, [dir=rtl] .status { align-self: flex-end; }
  </style>
  <div class="panel" part="panel">
    <header><span class="title"></span><select class="lang" aria-label="Language"></select>
      <button class="voice" type="button" aria-pressed="false">🎙</button></header>
    <div class="meter"></div>
    <div class="log" role="log" aria-live="polite"></div>
    <form><input autocomplete="off" maxlength="2000"><button type="submit">➤</button></form>
  </div>
  <button class="launcher" aria-label="Chat with the hotel">💬</button>`;

  const $ = (s) => root.querySelector(s);
  const panel = $(".panel"), log = $(".log"), form = $("form"), input = $("input"),
    langSel = $(".lang"), voiceBtn = $(".voice"), meter = $(".meter");

  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  const add = (node) => { log.appendChild(node); scroll(); return node; };
  const money = (v, c) => { try { return new Intl.NumberFormat(state.lang || "en", { style: "currency", currency: c }).format(Number(v)); } catch { return `${v} ${c}`; } };
  const date = (d) => { try { return new Date(d + "T12:00:00").toLocaleDateString(state.lang || "en", { weekday: "short", day: "numeric", month: "short", year: "numeric" }); } catch { return d; } };
  const store = { get() { try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "null"); } catch { return null; } },
    set(v) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch {} } };

  function applyLang(lang) {
    state.lang = lang; t = T[lang] || T.en;
    panel.dir = lang === "ar" ? "rtl" : "ltr";
    input.placeholder = t.placeholder;
    voiceBtn.title = t.voiceOff;
  }

  // ---------- rendering ----------
  let aiBubble = null, statusNode = null;
  function setStatus(text) {
    if (!statusNode) { statusNode = el("div", "status"); statusNode.append(el("span", "dot"), el("span")); }
    statusNode.lastChild.textContent = text;
    add(statusNode);
  }
  function clearStatus() { statusNode?.remove(); statusNode = null; }

  function offerCards(offers) {
    const row = el("div", "cards");
    for (const o of offers) {
      const c = el("div", "card");
      c.append(el("h4", null, o.room_name), el("div", "muted", `${o.rate_plan} · ${o.meal_plan}`),
        el("div", "price", money(o.total, o.currency)), el("div", "muted", `${o.nights} ${t.nights}` +
          (Number(o.pay_at_property_fees) ? ` · +${money(o.pay_at_property_fees, o.currency)} ${t.payAtHotel}` : "")));
      c.append(el("span", o.refundable ? "tag" : "tag nr", o.refundable ? t.refundable : t.nonRefundable));
      if (o.rooms_left && o.rooms_left <= 2) c.append(el("div", "muted", `${o.rooms_left} ${t.left}`));
      const b = el("button", null, t.choose);
      b.onclick = () => { row.querySelectorAll("button").forEach((x) => (x.disabled = true));
        send(t.chose.replace("{room}", o.room_name).replace("{rate}", o.rate_plan) + ` [${o.offer_id}]`); };
      c.append(b); row.append(c);
    }
    add(row);
  }

  function quoteCard(q) {
    const box = el("div", "quote");
    box.append(el("strong", null, q.room_name + " · " + q.rate_plan));
    const dl = el("dl");
    const row = (k, v) => dl.append(el("dt", null, k), el("dd", null, v));
    row("📅", `${date(q.check_in)} → ${date(q.check_out)}`);
    row("👥", `${q.adults}` + (q.children_ages.length ? ` + ${q.children_ages.join(", ")}` : ""));
    row("🍳", q.meal_plan);
    row(t.total, money(q.total, q.currency));
    if (Number(q.pay_at_property_fees)) row(t.payAtHotel, money(q.pay_at_property_fees, q.currency));
    row("💳", q.payment_timing);
    row("↩", q.cancellation.description);
    row(t.guest, `${q.guest.first_name} ${q.guest.last_name} · ${q.guest.email}`);
    box.append(dl);
    const b = el("button", null, t.confirm);
    b.onclick = async () => {
      b.disabled = true; b.textContent = t.confirming;
      try {
        const r = await fetch(`${API}/v1/conversations/${state.conv}/bookings/confirm`, { method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
          body: JSON.stringify({ quote_id: q.quote_id }) });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "Error");
        bookingResult(data);
        b.remove();
      } catch (e) { b.disabled = false; b.textContent = t.confirm; add(el("div", "system", e.message)); }
    };
    box.append(b);
    add(box);
  }

  function bookingResult(r) {
    if (r.state === "CONFIRMED") {
      const n = add(el("div", "result")); n.append(el("strong", null, "✅ " + t.confirmed), el("div", null, `${t.reference}: ${r.reference}`));
    } else if (r.state === "PRICE_CHANGED") {
      add(el("div", "result bad", t.priceChanged)); if (r.new_quote) quoteCard(r.new_quote);
    } else if (r.state === "STAFF_REVIEW" || r.state === "UNKNOWN") {
      add(el("div", "result bad", `⏳ ${t.review}. ${r.detail || ""}`));
    } else {
      add(el("div", "result bad", `${t.failed}. ${r.detail || ""}`));
    }
  }

  function handleEvent(e) {
    switch (e.type) {
      case "status": setStatus(e.text); break;
      case "delta":
        clearStatus();
        if (!aiBubble) { aiBubble = add(el("div", "msg ai")); aiBubble.dir = "auto"; }
        aiBubble.textContent += e.text; scroll(); break;
      case "offers": clearStatus(); aiBubble = null; offerCards(e.offers); break;
      case "quote": clearStatus(); aiBubble = null; quoteCard(e.quote); break;
      case "reservation": {
        const r = e.reservation; aiBubble = null;
        add(el("div", "result", `${r.reference} · ${r.room_name} · ${date(r.check_in)} → ${date(r.check_out)} · ${money(r.total, r.currency)} · ${r.status}`));
        break; }
      case "handoff": state.paused = true; add(el("div", "system", t.handoff)); startPolling(); break;
      case "paused": clearStatus(); state.paused = true; add(el("div", "msg ai", e.text)); startPolling(); break;
      case "error": clearStatus(); add(el("div", "msg ai", e.detail)); break;
      case "transcript": clearStatus(); if (e.text) add(el("div", "msg guest", e.text)).dir = "auto"; setStatus(t.thinking); break;
      case "done":
        clearStatus(); aiBubble = null; state.busy = false;
        if (e.message_id) state.lastId = Math.max(state.lastId, e.message_id);
        if (e.language && e.language !== state.lang && T[e.language]) { applyLang(e.language); langSel.value = e.language; }
        break;
      case "audio": voice.play(e); break;
      case "interrupted": clearStatus(); aiBubble = null; break;
      case "turn_end": state.busy = false; clearStatus(); break;
    }
  }

  // ---------- session & text chat ----------
  async function ensureSession() {
    if (state.conv) return;
    const saved = store.get();
    if (saved) Object.assign(state, saved);
    const cfg = await (await fetch(`${API}/v1/properties/${HOTEL}/widget-config`)).json();
    state.name = cfg.name; state.languages = cfg.languages; state.currency = cfg.currency;
    $(".title").textContent = cfg.name;
    langSel.innerHTML = "";
    for (const l of cfg.languages) langSel.append(new Option(l.toUpperCase(), l));
    if (!state.lang) state.lang = cfg.languages.includes(navigator.language.slice(0, 2)) ? navigator.language.slice(0, 2) : cfg.languages[0];
    applyLang(state.lang); langSel.value = state.lang;
    if (saved?.conv) { await catchUp(true); return; }
    const r = await fetch(`${API}/v1/properties/${HOTEL}/sessions`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: state.lang }) });
    const s = await r.json();
    Object.assign(state, { conv: s.conversation_id, token: s.token, voiceLangs: s.voice_languages });
    store.set({ conv: state.conv, token: state.token, lang: state.lang, voiceLangs: state.voiceLangs });
    add(el("div", "msg ai", s.greeting)).dir = "auto";
  }

  async function catchUp(renderAll) {
    const r = await fetch(`${API}/v1/conversations/${state.conv}/messages?after=${renderAll ? 0 : state.lastId}`,
      { headers: { Authorization: `Bearer ${state.token}` } });
    if (r.status === 403) { store.set(null); Object.assign(state, { conv: null, token: null }); return ensureSession(); }
    const data = await r.json();
    state.paused = data.ai_paused;
    for (const m of data.messages) {
      state.lastId = Math.max(state.lastId, m.id);
      if (!renderAll && m.sender !== "staff") continue;
      if (m.sender === "system") continue;
      const n = add(el("div", `msg ${m.sender === "guest" ? "guest" : m.sender}`, m.content));
      n.dir = "auto"; if (m.sender === "staff") n.dataset.label = t.staff;
    }
    if (state.paused) startPolling();
  }

  function startPolling() {
    if (state.poll) return;
    state.poll = setInterval(() => catchUp(false).catch(() => {}), 4000);
  }

  async function send(text) {
    text = text.trim();
    if (!text || state.busy) return;
    await ensureSession();
    add(el("div", "msg guest", text.replace(/\s*\[[A-Z]+:[^\]]+\]$/, ""))).dir = "auto";  // local echo at 0 ms
    state.busy = true; aiBubble = null;
    setStatus(t.thinking);
    if (voice.active) { voice.sendText(text); return; }
    try {
      const r = await fetch(`${API}/v1/conversations/${state.conv}/messages`, { method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${state.token}` },
        body: JSON.stringify({ text, request_id: crypto.randomUUID(), language: state.lang }) });
      if (!r.ok) throw new Error((await r.json()).detail || "Error");
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = block.split("\n").find((l) => l.startsWith("data: "));
          if (line) handleEvent(JSON.parse(line.slice(6)));
        }
      }
    } catch (e) { clearStatus(); add(el("div", "system", e.message)); }
    state.busy = false;
  }

  // ---------- voice: VAD capture -> WAV -> WS; in-order gapless playback; barge-in ----------
  const voice = {
    active: false, ws: null, ctx: null, playCtx: null, stream: null, proc: null, sources: [], nextAt: 0,
    speaking: false, frames: [], preroll: [], voiced: 0, silence: 0, rate: 16000,

    async start() {
      await ensureSession();
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch { add(el("div", "system", t.micDenied)); return; }
      this.ctx = new AudioContext({ sampleRate: 16000 });
      this.rate = this.ctx.sampleRate;
      this.playCtx = new AudioContext();
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.proc = this.ctx.createScriptProcessor(1024, 1, 1);
      this.proc.onaudioprocess = (ev) => this.onFrame(new Float32Array(ev.inputBuffer.getChannelData(0)));
      src.connect(this.proc); this.proc.connect(this.ctx.destination);
      const wsUrl = API.replace(/^http/, "ws") + `/v1/conversations/${state.conv}/voice?token=${encodeURIComponent(state.token)}`;
      this.ws = new WebSocket(wsUrl);
      this.ws.onmessage = (m) => handleEvent(JSON.parse(m.data));
      this.ws.onclose = () => { if (this.active) this.stop(); };
      this.active = true; voiceBtn.classList.add("on"); voiceBtn.setAttribute("aria-pressed", "true");
      setStatus(t.listening);
    },

    stop() {
      this.active = false; voiceBtn.classList.remove("on"); voiceBtn.setAttribute("aria-pressed", "false");
      this.stopPlayback();
      try { this.ws?.close(); } catch {}
      this.stream?.getTracks().forEach((tr) => tr.stop());
      this.proc?.disconnect(); this.ctx?.close(); this.playCtx?.close();
      Object.assign(this, { ws: null, ctx: null, playCtx: null, stream: null, proc: null, frames: [], preroll: [] });
      meter.style.width = "0"; clearStatus();
    },

    onFrame(f) {
      let sum = 0; for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
      const rms = Math.sqrt(sum / f.length);
      meter.style.width = Math.min(100, rms * 600) + "%";
      const playing = this.sources.length > 0;
      const threshold = playing ? 0.06 : 0.018;  // higher while the agent talks, to ignore echo
      const frameMs = (f.length / this.rate) * 1000;
      if (rms > threshold) { this.voiced += frameMs; this.silence = 0; } else { this.silence += frameMs; if (!this.frames.length) this.voiced = 0; }
      if (!this.frames.length) {
        this.preroll.push(f); if (this.preroll.length > 12) this.preroll.shift();
        if (this.voiced >= 120) {
          this.frames = this.preroll.splice(0);
          if (playing || state.busy) { this.stopPlayback(); this.ws?.send(JSON.stringify({ type: "interrupt" })); }  // barge-in
          setStatus(t.listening);
        }
        return;
      }
      this.frames.push(f);
      const total = this.frames.length * frameMs;
      if (this.silence > 650 || total > 20000) this.flush();
    },

    flush() {
      const frames = this.frames; this.frames = []; this.voiced = 0; this.silence = 0;
      if (frames.length * (1024 / this.rate) < 0.35) return;  // too short: noise
      const wav = encodeWav(frames, this.rate);
      if (this.ws?.readyState === 1) {
        this.ws.send(wav);
        this.ws.send(JSON.stringify({ type: "utterance_end", mime: "audio/wav" }));
        state.busy = true; setStatus(t.thinking);
      }
    },

    sendText(text) { this.ws?.send(JSON.stringify({ type: "text", text })); },

    async play(e) {
      if (!this.playCtx) return;
      const bytes = Uint8Array.from(atob(e.data), (c) => c.charCodeAt(0));
      let buffer;
      try { buffer = await this.playCtx.decodeAudioData(bytes.buffer); } catch { return; }
      if (!this.active) return;
      const node = this.playCtx.createBufferSource();
      node.buffer = buffer; node.connect(this.playCtx.destination);
      const at = Math.max(this.playCtx.currentTime + 0.02, this.nextAt);  // queue gaplessly, in order
      node.start(at); this.nextAt = at + buffer.duration;
      this.sources.push(node);
      node.onended = () => { this.sources = this.sources.filter((s) => s !== node); };
    },

    stopPlayback() {
      for (const s of this.sources) { try { s.stop(); } catch {} }
      this.sources = []; this.nextAt = 0;
    },
  };

  function encodeWav(frames, rate) {
    const n = frames.reduce((a, f) => a + f.length, 0);
    const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    w(36, "data"); v.setUint32(40, n * 2, true);
    let o = 44;
    for (const f of frames) for (let i = 0; i < f.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, f[i])) * 0x7fff, true);
    return buf;
  }

  // ---------- wiring ----------
  $(".launcher").onclick = async () => { panel.classList.toggle("open"); if (panel.classList.contains("open")) { await ensureSession(); input.focus(); } };
  form.onsubmit = (e) => { e.preventDefault(); const v = input.value; input.value = ""; send(v); };
  langSel.onchange = () => { applyLang(langSel.value); store.set({ conv: state.conv, token: state.token, lang: state.lang, voiceLangs: state.voiceLangs }); };
  voiceBtn.onclick = () => (voice.active ? voice.stop() : voice.start());
  if (script.dataset.open === "true") $(".launcher").click();
  window.HotelAgent = { open: () => panel.classList.add("open"), send };
})();
