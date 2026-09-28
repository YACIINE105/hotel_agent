/* Hotel Agent guest widget.
 * Embed: <script src="https://HOST/static/widget.js" data-hotel="steigenberger-aldau" defer></script>
 * Optional: data-api="https://HOST" data-lang="ar" data-open="true"
 * JS API: HotelAgent.open(), HotelAgent.openBooking(), HotelAgent.startVoice(), HotelAgent.send(text)
 * Renders in a Shadow DOM so hotel site CSS cannot break it (and vice versa).
 */
(() => {
  const script = document.currentScript;
  const HOTEL = script.dataset.hotel;
  const API = (script.dataset.api || new URL(script.src).origin).replace(/\/$/, "");
  const STORE_KEY = `hotel-agent:${HOTEL}`;
  const POLL_MS = 2000;

  // Fonts must be loaded by the host document to be usable inside the shadow root.
  if (!document.querySelector("link[data-hotel-agent-fonts]")) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.dataset.hotelAgentFonts = "";
    link.href = "https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700" +
      "&family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap";
    document.head.appendChild(link);
  }

  const T = {
    en: {
      online: "AI concierge · online", placeholder: "Ask about rooms, services, or your booking",
      book: "Book a room", checkin: "Check-in time", facilities: "Pools & spa", human: "Talk to staff",
      qCheckin: "What time is check-in and check-out?", qFacilities: "What pools and spa facilities do you have?",
      qHuman: "I would like to talk to a member of staff.",
      checkIn: "Check-in", checkOut: "Check-out", adults: "Adults", children: "Children", age: "Age",
      search: "Check availability", searching: "Checking live availability", choose: "Select",
      details: "Guest details", firstName: "First name", lastName: "Last name", email: "Email", phone: "Phone (optional)",
      requests: "Special requests (optional)", continue: "Review booking", back: "Back",
      summary: "Booking summary", confirm: "Confirm booking", confirming: "Confirming",
      total: "Total", payAtHotel: "Payable at the hotel", refundable: "Free cancellation", nonRefundable: "Non-refundable",
      nights: (n) => `${n} night${n === 1 ? "" : "s"}`, left: (n) => `Only ${n} left`, guests: "Guests", dates: "Dates",
      meals: "Meals", payment: "Payment", cancellation: "Cancellation", guest: "Guest",
      confirmed: "Booking confirmed", reference: "Reference", review: "Our team will verify your booking",
      failed: "Booking not completed", priceChanged: "The price changed. Please review the updated summary.",
      thinking: "Thinking", listening: "Listening", staffTyping: "Hotel team is typing", staff: "Hotel team",
      handoff: "You're now connected with our team. They will reply here.",
      voice: "Voice", voiceOn: "Voice on", close: "Close", send: "Send", open: "Chat with the hotel",
      micDenied: "Microphone unavailable. Use localhost or HTTPS and allow microphone access.",
      invalidDates: "Check-out must be after check-in.", required: "Please fill in the required fields.",
      simulated: "Demo · availability and prices are simulated", adultsN: (n) => `${n} adult${n === 1 ? "" : "s"}`,
      childrenN: (n) => `${n} child${n === 1 ? "" : "ren"}`,
    },
    ar: {
      online: "المساعد الذكي · متصل", placeholder: "اسأل عن الغرف أو الخدمات أو حجزك",
      book: "احجز غرفة", checkin: "موعد الوصول", facilities: "المسابح والسبا", human: "التحدث مع موظف",
      qCheckin: "ما هو موعد تسجيل الوصول والمغادرة؟", qFacilities: "ما هي المسابح ومرافق السبا لديكم؟",
      qHuman: "أرغب في التحدث مع أحد الموظفين.",
      checkIn: "تاريخ الوصول", checkOut: "تاريخ المغادرة", adults: "البالغون", children: "الأطفال", age: "العمر",
      search: "تحقق من التوفر", searching: "جارٍ التحقق من التوفر", choose: "اختيار",
      details: "بيانات الضيف", firstName: "الاسم الأول", lastName: "اسم العائلة", email: "البريد الإلكتروني",
      phone: "الهاتف (اختياري)", requests: "طلبات خاصة (اختياري)", continue: "مراجعة الحجز", back: "رجوع",
      summary: "ملخص الحجز", confirm: "تأكيد الحجز", confirming: "جارٍ التأكيد",
      total: "المجموع", payAtHotel: "يُدفع في الفندق", refundable: "إلغاء مجاني", nonRefundable: "غير قابل للاسترداد",
      nights: (n) => `${n} ${n === 1 ? "ليلة" : n === 2 ? "ليلتان" : "ليالٍ"}`, left: (n) => `متبقي ${n} فقط`,
      guests: "الضيوف", dates: "التواريخ", meals: "الوجبات", payment: "الدفع", cancellation: "الإلغاء", guest: "الضيف",
      confirmed: "تم تأكيد الحجز", reference: "رقم الحجز", review: "سيتحقق فريقنا من حجزك",
      failed: "لم يكتمل الحجز", priceChanged: "تغير السعر. يرجى مراجعة الملخص الجديد.",
      thinking: "يفكر", listening: "أستمع", staffTyping: "فريق الفندق يكتب", staff: "فريق الفندق",
      handoff: "أنت الآن على تواصل مع فريقنا، وسيردون عليك هنا.",
      voice: "صوت", voiceOn: "الصوت مفعل", close: "إغلاق", send: "إرسال", open: "تحدث مع الفندق",
      micDenied: "الميكروفون غير متاح. استخدم localhost أو HTTPS واسمح بالوصول.",
      invalidDates: "يجب أن يكون تاريخ المغادرة بعد تاريخ الوصول.", required: "يرجى تعبئة الحقول المطلوبة.",
      simulated: "نسخة تجريبية · التوفر والأسعار محاكاة", adultsN: (n) => `${n} بالغ`, childrenN: (n) => `${n} طفل`,
    },
    fr: {
      online: "Concierge IA · en ligne", placeholder: "Chambres, services ou réservation",
      book: "Réserver", checkin: "Heure d'arrivée", facilities: "Piscines et spa", human: "Parler à l'équipe",
      qCheckin: "À quelle heure sont l'arrivée et le départ ?", qFacilities: "Quelles piscines et quel spa proposez-vous ?",
      qHuman: "Je voudrais parler à un membre du personnel.",
      checkIn: "Arrivée", checkOut: "Départ", adults: "Adultes", children: "Enfants", age: "Âge",
      search: "Voir les disponibilités", searching: "Vérification des disponibilités", choose: "Choisir",
      details: "Coordonnées", firstName: "Prénom", lastName: "Nom", email: "E-mail", phone: "Téléphone (facultatif)",
      requests: "Demandes spéciales (facultatif)", continue: "Vérifier la réservation", back: "Retour",
      summary: "Récapitulatif", confirm: "Confirmer la réservation", confirming: "Confirmation",
      total: "Total", payAtHotel: "À payer à l'hôtel", refundable: "Annulation gratuite", nonRefundable: "Non remboursable",
      nights: (n) => `${n} nuit${n === 1 ? "" : "s"}`, left: (n) => `Plus que ${n}`, guests: "Voyageurs", dates: "Dates",
      meals: "Repas", payment: "Paiement", cancellation: "Annulation", guest: "Client",
      confirmed: "Réservation confirmée", reference: "Référence", review: "Notre équipe va vérifier votre réservation",
      failed: "Réservation non effectuée", priceChanged: "Le prix a changé. Vérifiez le nouveau récapitulatif.",
      thinking: "Réflexion", listening: "J'écoute", staffTyping: "L'équipe de l'hôtel écrit", staff: "Équipe de l'hôtel",
      handoff: "Vous êtes en contact avec notre équipe. Elle vous répondra ici.",
      voice: "Voix", voiceOn: "Voix activée", close: "Fermer", send: "Envoyer", open: "Discuter avec l'hôtel",
      micDenied: "Micro indisponible. Utilisez localhost ou HTTPS et autorisez le micro.",
      invalidDates: "Le départ doit être après l'arrivée.", required: "Veuillez remplir les champs obligatoires.",
      simulated: "Démo · disponibilités et prix simulés", adultsN: (n) => `${n} adulte${n === 1 ? "" : "s"}`,
      childrenN: (n) => `${n} enfant${n === 1 ? "" : "s"}`,
    },
  };

  const ICON = {
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
    users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 12h12"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 6v12M6 12h12"/></svg>',
    bed: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18V7M3 14h18v4M21 14v-2a3 3 0 0 0-3-3h-7v5"/><circle cx="7" cy="11" r="1.6"/></svg>',
  };

  const state = { conv: null, token: null, lang: script.dataset.lang || null, languages: ["en"], name: "",
    currency: "USD", busy: false, lastId: 0, paused: false, poll: null };
  let t = T.en;

  // ---------- DOM ----------
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483000;bottom:20px;right:20px;";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
  <style>
    :host { all: initial; }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    .w { --blue: #2663eb; --blue-d: #1d4ed8; --ink: #0f172a; --ink-2: #334155; --muted: #64748b; --line: #e2e8f0;
      --soft: #f6f9fe; --ok: #15803d; --ok-bg: #f0fdf4; --bad: #b91c1c; --bad-bg: #fef2f2;
      font-family: "DM Sans", "IBM Plex Sans Arabic", system-ui, -apple-system, "Segoe UI", sans-serif;
      color: var(--ink); font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
    .w[dir=rtl] { font-family: "IBM Plex Sans Arabic", "DM Sans", system-ui, sans-serif; }
    .mono { font-family: "Geist Mono", ui-monospace, monospace; letter-spacing: .04em; text-transform: uppercase; font-size: 10.5px; }
    /* Letter-spacing breaks the joined letters of Arabic script. */
    [dir=rtl] .mono { font-family: "IBM Plex Sans Arabic", system-ui, sans-serif; letter-spacing: 0; font-size: 11.5px; }
    svg { width: 18px; height: 18px; display: block; flex: none; }
    button { font: inherit; color: inherit; cursor: pointer; }
    .orb { width: 32px; height: 32px; border-radius: 50%; flex: none;
      background: radial-gradient(circle at 30% 28%, #fff 0 6%, #bfdbfe 16%, #60a5fa 42%, #2663eb 70%, #172554 100%);
      box-shadow: inset 0 -3px 6px rgba(23,37,84,.35), 0 2px 8px rgba(38,99,235,.35); }
    .orb.live { animation: breathe 1.6s ease-in-out infinite; }
    @keyframes breathe { 50% { transform: scale(1.12); box-shadow: 0 0 0 8px rgba(38,99,235,.12), inset 0 -3px 6px rgba(23,37,84,.35); } }

    .shell { display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }
    .launcher { display: flex; align-items: center; gap: 10px; padding: 8px 18px 8px 8px; border-radius: 999px;
      border: 1px solid var(--line); background: #fff; box-shadow: 0 10px 30px rgba(15,23,42,.14); font-weight: 600; }
    .launcher:hover { border-color: #bfdbfe; }
    .launcher .orb { width: 40px; height: 40px; }

    .panel { display: none; width: min(400px, calc(100vw - 24px)); height: min(680px, calc(100vh - 40px)); background: #fff;
      border: 1px solid var(--line); border-radius: 24px; box-shadow: 0 24px 60px rgba(15,23,42,.18); overflow: hidden;
      flex-direction: column; }
    .panel.open { display: flex; }
    /* Shadow-root !important beats the host element's inline position on small screens. */
    @media (max-width: 480px) {
      :host { right: 8px !important; bottom: 8px !important; }
      .panel { width: calc(100vw - 16px); height: calc(100dvh - 16px); border-radius: 20px; }
      .who span { white-space: nowrap; overflow: hidden; }
    }

    header { display: flex; align-items: center; gap: 10px; padding: 14px 14px 12px 16px; border-bottom: 1px solid var(--line); }
    .who { flex: 1; min-width: 0; }
    .who b { display: block; font-size: 15px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .who span { color: var(--muted); display: flex; align-items: center; gap: 6px; }
    .who span::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: #22c55e; }
    .seg { display: flex; padding: 3px; background: #f1f5f9; border-radius: 10px; }
    .seg button { border: 0; background: none; padding: 4px 9px; border-radius: 7px; font-size: 12px; font-weight: 600; color: var(--muted); }
    .seg button.on { background: #fff; color: var(--ink); box-shadow: 0 1px 3px rgba(15,23,42,.12); }
    .icon-btn { width: 34px; height: 34px; border-radius: 10px; border: 1px solid var(--line); background: #fff;
      display: grid; place-items: center; color: var(--ink-2); }
    .icon-btn:hover { background: var(--soft); }
    .icon-btn.on { background: var(--blue); border-color: var(--blue); color: #fff; }
    .meter { height: 2px; background: var(--blue); width: 0; transition: width .08s; }

    .log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px;
      background: linear-gradient(var(--soft), #fff 180px); }
    .msg { max-width: 86%; padding: 10px 14px; border-radius: 18px; white-space: pre-wrap; word-wrap: break-word; }
    .guest { align-self: flex-end; background: var(--blue); color: #fff; border-end-end-radius: 6px; }
    .ai { align-self: flex-start; background: #fff; border: 1px solid var(--line); border-end-start-radius: 6px; }
    .staff { align-self: flex-start; background: #fff; border: 1px solid #bfdbfe; border-end-start-radius: 6px; }
    .staff .label { display: block; color: var(--blue); margin-bottom: 2px; }
    .note { align-self: center; color: var(--muted); font-size: 12px; text-align: center; max-width: 90%; }
    .typing { align-self: flex-start; display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12.5px;
      padding: 8px 12px; background: #fff; border: 1px solid var(--line); border-radius: 14px; }
    .dots { display: flex; gap: 3px; }
    .dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--blue); animation: blink 1.2s infinite; }
    .dots i:nth-child(2) { animation-delay: .15s; } .dots i:nth-child(3) { animation-delay: .3s; }
    @keyframes blink { 0%, 60%, 100% { opacity: .25; } 30% { opacity: 1; } }

    .chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .chip { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: #fff;
      border-radius: 999px; padding: 6px 12px; font-size: 13px; font-weight: 500; color: var(--ink-2); }
    .chip:hover { border-color: #93c5fd; color: var(--blue); }
    .chip.primary { background: var(--blue); border-color: var(--blue); color: #fff; }
    .chip svg { width: 15px; height: 15px; }

    .card { align-self: stretch; background: #fff; border: 1px solid var(--line); border-radius: 18px; padding: 14px;
      box-shadow: 0 4px 12px rgba(15,23,42,.05); }
    .card h4 { margin: 0 0 10px; font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .card h4 svg { color: var(--blue); }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    label.f { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); font-weight: 500; }
    input, select, textarea { font: inherit; font-size: 14px; color: var(--ink); background: #fff; border: 1px solid var(--line);
      border-radius: 12px; padding: 9px 11px; outline: none; width: 100%; min-width: 0; }
    input:focus, select:focus, textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(38,99,235,.12); }
    .stepper { display: flex; align-items: center; justify-content: space-between; padding: 5px 6px;
      border: 1px solid var(--line); border-radius: 12px; }
    .stepper b { min-width: 20px; text-align: center; }
    .stepper button { width: 30px; height: 30px; border-radius: 50%; border: 1px solid var(--line); background: #fff;
      display: grid; place-items: center; }
    .stepper button:disabled { opacity: .35; cursor: default; }
    .stepper svg { width: 14px; height: 14px; }
    .row { display: flex; gap: 8px; margin-top: 12px; }
    .btn { flex: 1; border: 0; border-radius: 999px; padding: 10px 16px; background: var(--blue); color: #fff; font-weight: 600; }
    .btn:hover { background: var(--blue-d); }
    .btn.ghost { flex: 0 0 auto; background: #fff; border: 1px solid var(--line); color: var(--ink-2); }
    .btn:disabled { opacity: .55; cursor: default; }
    .err { color: var(--bad); font-size: 12.5px; margin-top: 8px; }
    .ages { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .ages select { width: auto; padding: 6px 8px; }

    .offers { display: flex; gap: 10px; overflow-x: auto; align-self: stretch; padding-bottom: 6px; scroll-snap-type: x mandatory; flex: none; }
    .offer { min-width: 236px; max-width: 236px; scroll-snap-align: start; display: flex; flex-direction: column; }
    .name { font-weight: 700; font-size: 14.5px; }
    .sub { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
    .price { font-size: 22px; font-weight: 700; letter-spacing: -.01em; margin-top: 10px; }
    .price small { font-size: 12px; color: var(--muted); font-weight: 500; margin-inline-start: 6px; }
    .tag { display: inline-flex; align-items: center; gap: 4px; margin-top: 8px; font-size: 11.5px; font-weight: 600;
      padding: 3px 8px; border-radius: 999px; background: var(--ok-bg); color: var(--ok); align-self: flex-start; }
    .tag svg { width: 12px; height: 12px; }
    .tag.nr { background: #f1f5f9; color: var(--ink-2); }
    .scarce { color: #c2410c; font-size: 12px; margin-top: 6px; }
    .spacer { flex: 1; min-height: 10px; }

    dl.sum { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; margin: 10px 0 0; font-size: 13px; }
    dl.sum dt { color: var(--muted); } dl.sum dd { margin: 0; }
    .total { display: flex; justify-content: space-between; align-items: baseline; border-top: 1px solid var(--line);
      margin-top: 12px; padding-top: 10px; font-weight: 700; font-size: 16px; }
    .result { align-self: stretch; display: flex; gap: 10px; align-items: flex-start; border-radius: 16px; padding: 12px 14px;
      background: var(--ok-bg); border: 1px solid #bbf7d0; color: #14532d; }
    .result.bad { background: var(--bad-bg); border-color: #fecaca; color: #7f1d1d; }
    .result .ic { width: 26px; height: 26px; border-radius: 50%; background: var(--ok); color: #fff; display: grid; place-items: center; flex: none; }
    .result.bad .ic { background: var(--bad); }
    .result .ic svg { width: 15px; height: 15px; }
    .result b { display: block; }

    form.compose { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--line); background: #fff; align-items: center; }
    form.compose input { border-radius: 999px; padding: 11px 16px; background: var(--soft); border-color: transparent; }
    form.compose input:focus { background: #fff; }
    .send { width: 40px; height: 40px; border-radius: 50%; border: 0; background: var(--blue); color: #fff; display: grid; place-items: center; flex: none; }
    [dir=rtl] .send svg { transform: scaleX(-1); }
    .foot { text-align: center; color: var(--muted); padding: 0 12px 8px; background: #fff; }
  </style>
  <div class="w shell">
    <section class="panel" role="dialog" aria-label="Hotel concierge">
      <header>
        <div class="orb"></div>
        <div class="who"><b class="hotel-name"></b><span class="mono status-line"></span></div>
        <div class="seg lang" role="group" aria-label="Language"></div>
        <button class="icon-btn voice" type="button" aria-pressed="false">${ICON.mic}</button>
        <button class="icon-btn close" type="button">${ICON.close}</button>
      </header>
      <div class="meter"></div>
      <div class="log" role="log" aria-live="polite"></div>
      <form class="compose"><input autocomplete="off" maxlength="2000"><button class="send" type="submit">${ICON.send}</button></form>
      <div class="foot mono simulated"></div>
    </section>
    <button class="launcher" type="button"><span class="orb"></span><span class="launch-label"></span></button>
  </div>`;

  const $ = (s) => root.querySelector(s);
  const W = $(".w"), panel = $(".panel"), log = $(".log"), form = $("form.compose"), input = $("form.compose input"),
    langSeg = $(".lang"), voiceBtn = $(".voice"), meter = $(".meter"), headerOrb = $("header .orb"), launcher = $(".launcher");

  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const html = (tag, cls, markup) => { const n = el(tag, cls); n.innerHTML = markup; return n; };
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  const add = (node) => { log.appendChild(node); scroll(); return node; };
  const bubble = (cls, text) => { const n = add(el("div", `msg ${cls}`, text)); n.dir = "auto"; return n; };
  const locale = () => (state.lang === "ar" ? "ar-EG" : state.lang || "en");
  const money = (v, c) => { try { return new Intl.NumberFormat(locale(), { style: "currency", currency: c }).format(Number(v)); } catch { return `${v} ${c}`; } };
  const fmtDate = (d) => { try { return new Date(d + "T12:00:00").toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short" }); } catch { return d; } };
  const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const store = { get() { try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || "null"); } catch { return null; } },
    set(v) { try { sessionStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch {} } };
  const auth = () => ({ "Content-Type": "application/json", Authorization: `Bearer ${state.token}` });
  const save = () => store.set({ conv: state.conv, token: state.token, lang: state.lang });
  const field = (label, control) => { const l = el("label", "f"); l.append(el("span", null, label), control); return l; };

  async function post(path, body) {
    const r = await fetch(`${API}${path}`, { method: "POST", headers: auth(), body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = data.detail;
      throw new Error(Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || r.statusText);
    }
    return data;
  }

  function applyLang(lang) {
    state.lang = T[lang] ? lang : "en"; t = T[state.lang];
    W.dir = state.lang === "ar" ? "rtl" : "ltr";
    input.placeholder = t.placeholder;
    $(".launch-label").textContent = t.open;
    $(".status-line").textContent = t.online;
    $(".simulated").textContent = t.simulated;
    voiceBtn.title = voiceBtn.ariaLabel = voice.active ? t.voiceOn : t.voice;
    $(".close").title = $(".close").ariaLabel = t.close;
    $(".send").ariaLabel = t.send;
    for (const b of langSeg.children) b.classList.toggle("on", b.dataset.lang === state.lang);
  }

  // ---------- indicators ----------
  let aiBubble = null, statusNode = null, staffNode = null;
  function typingNode(text) {
    const n = el("div", "typing");
    n.append(html("span", "dots", "<i></i><i></i><i></i>"), el("span", null, text));
    return n;
  }
  function setStatus(text) { statusNode?.remove(); statusNode = add(typingNode(text)); }
  function clearStatus() { statusNode?.remove(); statusNode = null; }
  function setStaffTyping(on) {
    if (on && !staffNode) staffNode = add(typingNode(t.staffTyping));
    else if (on) log.appendChild(staffNode);  // keep it last
    else if (staffNode) { staffNode.remove(); staffNode = null; }
  }

  // ---------- quick actions ----------
  function quickActions() {
    const row = el("div", "chips");
    const chip = (label, icon, fn, primary) => {
      const b = html("button", "chip" + (primary ? " primary" : ""), icon || "");
      b.append(el("span", null, label));
      b.type = "button"; b.onclick = fn; row.append(b);
    };
    chip(t.book, ICON.calendar, () => bookingForm(), true);
    chip(t.checkin, null, () => send(t.qCheckin));
    chip(t.facilities, null, () => send(t.qFacilities));
    chip(t.human, null, () => send(t.qHuman));
    add(row);
  }

  // ---------- booking form: dates & guests ----------
  function stepper(value, min, max, onChange) {
    const wrap = el("div", "stepper");
    const minus = html("button", null, ICON.minus), plus = html("button", null, ICON.plus), num = el("b");
    minus.type = plus.type = "button";
    const sync = () => { num.textContent = value; minus.disabled = value <= min; plus.disabled = value >= max; onChange(value); };
    minus.onclick = () => { if (value > min) { value--; sync(); } };
    plus.onclick = () => { if (value < max) { value++; sync(); } };
    wrap.append(minus, num, plus);
    sync();
    return wrap;
  }

  function bookingForm(prefill = {}) {
    const card = el("form", "card");
    card.noValidate = true;
    card.innerHTML = `<h4>${ICON.calendar}</h4>`;
    card.firstChild.append(el("span", null, t.book));
    const today = new Date(), tomorrow = new Date(Date.now() + 864e5), after = new Date(Date.now() + 3 * 864e5);
    const ci = Object.assign(el("input"), { type: "date", min: iso(today), value: prefill.check_in || iso(tomorrow), required: true });
    const co = Object.assign(el("input"), { type: "date", min: iso(tomorrow), value: prefill.check_out || iso(after), required: true });
    ci.onchange = () => {
      const next = new Date(ci.value + "T12:00:00"); next.setDate(next.getDate() + 1);
      co.min = iso(next); if (co.value <= ci.value) co.value = iso(next);
    };
    const dates = el("div", "grid2"); dates.append(field(t.checkIn, ci), field(t.checkOut, co));

    let adults = prefill.adults || 2;
    const ages = [];
    const agesBox = el("div", "ages");
    const renderAges = (n) => {
      while (ages.length < n) ages.push(5);
      ages.length = n;
      agesBox.replaceChildren(...ages.map((a, i) => {
        const s = el("select");
        s.ariaLabel = `${t.age} ${i + 1}`;
        for (let y = 0; y <= 17; y++) s.append(new Option(`${t.age} ${y}`, y, false, y === a));
        s.onchange = () => { ages[i] = Number(s.value); };
        return s;
      }));
    };
    const people = el("div", "grid2"); people.style.marginTop = "8px";
    people.append(field(t.adults, stepper(adults, 1, 6, (v) => { adults = v; })),
      field(t.children, stepper(0, 0, 4, renderAges)));
    const err = el("div", "err"); err.hidden = true;
    const actions = el("div", "row");
    const go = el("button", "btn", t.search); go.type = "submit";
    actions.append(go);
    card.append(dates, people, agesBox, err, actions);
    card.onsubmit = async (e) => {
      e.preventDefault();
      err.hidden = true;
      if (!ci.value || !co.value || co.value <= ci.value) { err.textContent = t.invalidDates; err.hidden = false; return; }
      go.disabled = true;
      await ensureSession();
      try {
        setStatus(t.searching);
        const data = await post(`/v1/conversations/${state.conv}/availability`,
          { check_in: ci.value, check_out: co.value, adults, children_ages: ages.slice() });
        clearStatus();
        card.remove();
        bubble("guest", `${fmtDate(ci.value)} – ${fmtDate(co.value)} · ${t.adultsN(adults)}` +
          (ages.length ? ` · ${t.childrenN(ages.length)}` : ""));
        bubble("ai", data.reply);
        if (data.offers.length) offerCards(data.offers); else bookingForm({ adults });
      } catch (e2) { clearStatus(); err.textContent = e2.message; err.hidden = false; go.disabled = false; }
    };
    add(card);
  }

  // ---------- offers ----------
  function offerCards(offers) {
    const row = el("div", "offers");
    for (const o of offers) {
      const c = el("div", "card offer");
      c.append(el("div", "name", o.room_name), el("div", "sub", [o.room_size, o.rate_plan].filter(Boolean).join(" · ")),
        el("div", "sub", o.meal_plan));
      const price = el("div", "price", money(o.total, o.currency));
      price.append(el("small", null, t.nights(o.nights)));
      c.append(price);
      if (Number(o.pay_at_property_fees)) c.append(el("div", "sub", `+ ${money(o.pay_at_property_fees, o.currency)} ${t.payAtHotel}`));
      const tag = html("span", o.refundable ? "tag" : "tag nr", o.refundable ? ICON.check : "");
      tag.append(el("span", null, o.refundable ? t.refundable : t.nonRefundable));
      c.append(tag);
      if (o.rooms_left && o.rooms_left <= 2) c.append(el("div", "scarce", t.left(o.rooms_left)));
      c.append(el("div", "spacer"));
      const b = el("button", "btn", t.choose); b.type = "button";
      b.onclick = () => { row.querySelectorAll(".btn").forEach((x) => (x.disabled = true)); detailsForm(o, row); };
      c.append(b);
      row.append(c);
    }
    add(row);
  }

  // ---------- guest details ----------
  function detailsForm(offer, offersRow) {
    const card = el("form", "card");
    card.noValidate = true;
    card.innerHTML = `<h4>${ICON.users}</h4>`;
    card.firstChild.append(el("span", null, `${t.details} · ${offer.room_name}`));
    const mk = (type, auto) => Object.assign(el("input"), { type, autocomplete: auto });
    const first = mk("text", "given-name"), last = mk("text", "family-name"), email = mk("email", "email"), phone = mk("tel", "tel");
    const notes = Object.assign(el("textarea"), { rows: 2, maxLength: 500 });
    const names = el("div", "grid2"); names.append(field(t.firstName, first), field(t.lastName, last));
    const more = el("div"); more.style.cssText = "display:grid;gap:8px;margin-top:8px";
    more.append(field(t.email, email), field(t.phone, phone), field(t.requests, notes));
    const err = el("div", "err"); err.hidden = true;
    const actions = el("div", "row");
    const back = el("button", "btn ghost", t.back); back.type = "button";
    back.onclick = () => { card.remove(); offersRow.querySelectorAll(".btn").forEach((x) => (x.disabled = false)); };
    const go = el("button", "btn", t.continue); go.type = "submit";
    actions.append(back, go);
    card.append(names, more, err, actions);
    card.onsubmit = async (e) => {
      e.preventDefault();
      err.hidden = true;
      if (!first.value.trim() || !last.value.trim() || !email.value.trim() || !email.checkValidity()) {
        err.textContent = t.required; err.hidden = false; return;
      }
      go.disabled = back.disabled = true;
      try {
        const body = { offer_id: offer.offer_id, first_name: first.value.trim(), last_name: last.value.trim(), email: email.value.trim() };
        if (phone.value.trim()) body.phone = phone.value.trim();
        if (notes.value.trim()) body.special_requests = notes.value.trim();
        const data = await post(`/v1/conversations/${state.conv}/quotes`, body);
        card.remove();
        bubble("guest", `${body.first_name} ${body.last_name} · ${body.email}`);
        bubble("ai", data.reply);
        quoteCard(data.quote);
      } catch (e2) { err.textContent = e2.message; err.hidden = false; go.disabled = back.disabled = false; }
    };
    add(card);
    first.focus({ preventScroll: true });
  }

  // ---------- quote & result ----------
  function quoteCard(q) {
    const card = el("div", "card quote");
    card.innerHTML = `<h4>${ICON.bed}</h4>`;
    card.firstChild.append(el("span", null, t.summary));
    card.append(el("div", "name", `${q.room_name} · ${q.rate_plan}`));
    const dl = el("dl", "sum");
    const row = (k, v) => dl.append(el("dt", null, k), el("dd", null, v));
    row(t.dates, `${fmtDate(q.check_in)} – ${fmtDate(q.check_out)}`);
    row(t.guests, t.adultsN(q.adults) + (q.children_ages.length ? ` · ${t.childrenN(q.children_ages.length)} (${q.children_ages.join(", ")})` : ""));
    row(t.meals, q.meal_plan);
    row(t.payment, q.payment_timing);
    row(t.cancellation, q.cancellation.description);
    row(t.guest, `${q.guest.first_name} ${q.guest.last_name} · ${q.guest.email}`);
    if (Number(q.pay_at_property_fees)) row(t.payAtHotel, money(q.pay_at_property_fees, q.currency));
    const total = el("div", "total"); total.append(el("span", null, t.total), el("span", null, money(q.total, q.currency)));
    const err = el("div", "err"); err.hidden = true;
    const b = el("button", "btn confirm", t.confirm); b.type = "button";
    const actions = el("div", "row"); actions.append(b);
    b.onclick = async () => {
      b.disabled = true; b.textContent = t.confirming; err.hidden = true;
      try {
        const data = await post(`/v1/conversations/${state.conv}/bookings/confirm`, { quote_id: q.quote_id });
        actions.remove();
        bookingResult(data);
      } catch (e) { b.disabled = false; b.textContent = t.confirm; err.textContent = e.message; err.hidden = false; }
    };
    card.append(dl, total, err, actions);
    add(card);
  }

  function result(ok, title, detail) {
    const n = el("div", ok ? "result" : "result bad");
    n.append(html("span", "ic", ok ? ICON.check : ICON.alert));
    const body = el("div"); body.append(el("b", null, title)); if (detail) body.append(el("span", null, detail));
    n.append(body);
    add(n);
  }

  function bookingResult(r) {
    if (r.state === "CONFIRMED") result(true, t.confirmed, `${t.reference}: ${r.reference}`);
    else if (r.state === "PRICE_CHANGED") { result(false, t.priceChanged); if (r.new_quote) quoteCard(r.new_quote); }
    else if (r.state === "STAFF_REVIEW" || r.state === "UNKNOWN") result(false, t.review, r.detail);
    else result(false, t.failed, r.detail);
  }

  // ---------- agent events (SSE and voice) ----------
  function handleEvent(e) {
    switch (e.type) {
      case "status": setStatus(e.text); break;
      case "delta":
        clearStatus();
        if (!aiBubble) aiBubble = bubble("ai", "");
        aiBubble.textContent += e.text; scroll(); break;
      case "rewrite": {  // server cleaned the reply (e.g. removed repeated sentences)
        const target = aiBubble || [...log.querySelectorAll(".msg.ai")].pop();
        if (target) target.textContent = e.text;
        break; }
      case "offers": clearStatus(); aiBubble = null; offerCards(e.offers); break;
      case "quote": clearStatus(); aiBubble = null; quoteCard(e.quote); break;
      case "booking_form": aiBubble = null; bookingForm(); break;
      case "reservation": {
        const r = e.reservation; aiBubble = null;
        result(true, `${r.reference} · ${r.room_name}`, `${fmtDate(r.check_in)} – ${fmtDate(r.check_out)} · ${money(r.total, r.currency)} · ${r.status}`);
        break; }
      case "handoff": state.paused = true; add(el("div", "note", t.handoff)); break;
      case "paused": clearStatus(); state.paused = true; break;  // staff are handling the chat: stay silent
      case "error": clearStatus(); bubble("ai", e.detail); break;
      case "transcript":
        clearStatus();
        if (e.rejected) { voice.duck(false); break; }  // noise: restore the agent's voice
        if (e.text) bubble("guest", e.text);
        setStatus(t.thinking); break;
      case "done":
        clearStatus(); aiBubble = null; state.busy = false;
        if (e.message_id) state.lastId = Math.max(state.lastId, e.message_id);
        if (e.language && e.language !== state.lang && T[e.language] && state.languages.includes(e.language)) { applyLang(e.language); save(); }
        break;
      case "audio": voice.play(e); break;
      case "interrupted": clearStatus(); aiBubble = null; voice.stopPlayback(); break;
      case "turn_end": state.busy = false; clearStatus(); drain(); break;
    }
  }

  // ---------- session, polling, text chat ----------
  let sessionPromise = null;
  function ensureSession() { return (sessionPromise ||= startSession()); }

  async function startSession() {
    const saved = store.get();
    const cfg = await (await fetch(`${API}/v1/properties/${HOTEL}/widget-config`)).json();
    state.name = cfg.name; state.languages = cfg.languages; state.currency = cfg.currency;
    $(".hotel-name").textContent = cfg.name;
    langSeg.replaceChildren(...cfg.languages.map((l) => {
      const b = el("button", null, l === "ar" ? "ع" : l.toUpperCase()); b.type = "button"; b.dataset.lang = l;
      b.onclick = () => { applyLang(l); save(); };
      return b;
    }));
    const browser = (navigator.language || "en").slice(0, 2);
    applyLang(saved?.lang || state.lang || (cfg.languages.includes(browser) ? browser : cfg.languages[0]));
    if (saved?.conv) {
      Object.assign(state, { conv: saved.conv, token: saved.token });
      if (await catchUp(true)) { startLive(); return; }
    }
    const s = await (await fetch(`${API}/v1/properties/${HOTEL}/sessions`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: state.lang }) })).json();
    Object.assign(state, { conv: s.conversation_id, token: s.token, lastId: 0 });
    save();
    bubble("ai", s.greeting);
    quickActions();
    startLive();
  }

  async function catchUp(renderAll) {
    const r = await fetch(`${API}/v1/conversations/${state.conv}/messages?after=${renderAll ? 0 : state.lastId}`, { headers: auth() });
    if (r.status === 403 || r.status === 404) { store.set(null); return false; }
    if (!r.ok) return true;
    const data = await r.json();
    state.paused = data.ai_paused;
    let staffArrived = false;
    for (const m of data.messages) {
      state.lastId = Math.max(state.lastId, m.id);
      if (!renderAll && m.sender !== "staff") continue;  // guest/AI messages are already on screen
      if (m.sender === "system") continue;
      if (m.sender === "staff") { if (!showStaff(m)) continue; staffArrived = true; continue; }
      add(el("div", `msg ${m.sender === "guest" ? "guest" : m.sender}`, m.content)).dir = "auto";
    }
    setStaffTyping(data.staff_typing && !staffArrived);
    if (renderAll && !data.messages.length) quickActions();
    return true;
  }

  const shownStaff = new Set();
  function showStaff(m) {  // render a staff message once, whichever channel delivered it
    if (shownStaff.has(m.id)) return false;
    shownStaff.add(m.id);
    state.lastStaffId = Math.max(state.lastStaffId || 0, m.id);
    const n = el("div", "msg staff");
    n.dir = "auto";
    n.append(el("span", "label mono", t.staff), document.createTextNode(m.content));
    add(n);
    return true;
  }

  function startPolling() {
    if (state.poll) return;
    // Fallback when push is unavailable: every 2 s, staff replies and the typing indicator.
    state.poll = setInterval(() => { if (!document.hidden) catchUp(false).catch(() => {}); }, POLL_MS);
  }

  // Push: the server streams staff replies, typing and pause state as they happen (no polling).
  function startLive() {
    if (!window.EventSource) return startPolling();
    let failures = 0;
    const connect = () => {
      const url = `${API}/v1/conversations/${state.conv}/events?token=${encodeURIComponent(state.token)}&after=${state.lastStaffId || 0}`;
      const es = new EventSource(url);
      state.live = es;
      es.onopen = () => { failures = 0; };
      es.addEventListener("message", (ev) => {
        const m = JSON.parse(ev.data);
        if (showStaff(m)) setStaffTyping(false);
      });
      es.addEventListener("state", (ev) => {
        const st = JSON.parse(ev.data);
        state.paused = st.ai_paused;
        setStaffTyping(st.staff_typing);
      });
      es.onerror = () => {
        es.close();
        if (++failures > 3) { startPolling(); return; }  // e.g. a proxy that blocks streaming
        setTimeout(connect, 2000 * failures);
      };
    };
    connect();
  }

  const queue = [];
  function drain() { if (!state.busy && queue.length) send(queue.shift()); }

  async function send(text) {
    text = text.trim();
    if (!text) return;
    if (state.busy) { queue.push(text); return; }  // sent when the current reply finishes
    await ensureSession();
    bubble("guest", text);  // local echo at 0 ms
    state.busy = true; aiBubble = null;
    setStatus(t.thinking);
    if (voice.active) { voice.sendText(text); return; }
    try {
      const r = await fetch(`${API}/v1/conversations/${state.conv}/messages`, { method: "POST", headers: auth(),
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
    } catch (e) { clearStatus(); add(el("div", "note", e.message)); }
    state.busy = false;
    drain();
  }

  // ---------- voice: VAD capture -> WAV -> WS; in-order gapless playback; barge-in ----------
  const voice = {
    active: false, ws: null, ctx: null, playCtx: null, stream: null, proc: null, sources: [], nextAt: 0,
    frames: [], preroll: [], voiced: 0, speech: 0, silence: 0, rate: 16000,

    async start() {
      await ensureSession();
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      } catch { add(el("div", "note", t.micDenied)); return; }
      this.ctx = new AudioContext({ sampleRate: 16000 });
      this.rate = this.ctx.sampleRate;
      this.playCtx = new AudioContext();
      this.gain = this.playCtx.createGain();
      this.gain.connect(this.playCtx.destination);
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.proc = this.ctx.createScriptProcessor(1024, 1, 1);
      this.proc.onaudioprocess = (ev) => this.onFrame(new Float32Array(ev.inputBuffer.getChannelData(0)));
      src.connect(this.proc); this.proc.connect(this.ctx.destination);
      const wsUrl = API.replace(/^http/, "ws") + `/v1/conversations/${state.conv}/voice?token=${encodeURIComponent(state.token)}`;
      this.ws = new WebSocket(wsUrl);
      this.ws.onmessage = (m) => handleEvent(JSON.parse(m.data));
      this.ws.onclose = () => { if (this.active) this.stop(); };
      this.active = true;
      voiceBtn.classList.add("on"); voiceBtn.setAttribute("aria-pressed", "true"); voiceBtn.title = t.voiceOn;
      headerOrb.classList.add("live");
      setStatus(t.listening);
    },

    stop() {
      this.active = false;
      voiceBtn.classList.remove("on"); voiceBtn.setAttribute("aria-pressed", "false"); voiceBtn.title = t.voice;
      headerOrb.classList.remove("live");
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
      const threshold = playing ? 0.08 : 0.025;  // higher while the agent talks, to ignore echo
      const frameMs = (f.length / this.rate) * 1000;
      if (rms > threshold) { this.voiced += frameMs; this.speech += frameMs; this.silence = 0; } else { this.silence += frameMs; if (!this.frames.length) this.voiced = 0; }
      if (!this.frames.length) {
        this.preroll.push(f); if (this.preroll.length > 12) this.preroll.shift();
        if (this.voiced >= 250) {
          this.frames = this.preroll.splice(0); this.speech = this.voiced;
          // Barge-in: duck the agent's voice now; the server stops the answer only if this turns
          // out to be real speech (then an "interrupted" event arrives), so noise can't cut answers.
          if (playing) this.duck(true);
          setStatus(t.listening);
        }
        return;
      }
      this.frames.push(f);
      if (this.silence > 650 || this.frames.length * frameMs > 20000) this.flush();
    },

    flush() {
      const frames = this.frames, speech = this.speech; this.frames = []; this.voiced = 0; this.silence = 0; this.speech = 0;
      if (speech < 400) { setStatus(t.listening); return; }  // under 0.4 s of actual speech: noise or a cough
      const wav = encodeWav(frames, this.rate);
      if (this.ws?.readyState === 1) {
        this.ws.send(wav);
        this.ws.send(JSON.stringify({ type: "utterance_end", mime: "audio/wav", language: state.lang }));
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
      node.buffer = buffer; node.connect(this.gain);
      const at = Math.max(this.playCtx.currentTime + 0.02, this.nextAt);  // queue gaplessly, in order
      node.start(at); this.nextAt = at + buffer.duration;
      this.sources.push(node);
      node.onended = () => { this.sources = this.sources.filter((s) => s !== node); };
    },

    stopPlayback() {
      for (const s of this.sources) { try { s.stop(); } catch {} }
      this.sources = []; this.nextAt = 0;
      this.duck(false);
    },

    duck(on) {
      if (this.gain && this.playCtx) this.gain.gain.setTargetAtTime(on ? 0.15 : 1, this.playCtx.currentTime, 0.05);
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
  async function open() { panel.classList.add("open"); launcher.hidden = true; await ensureSession(); }
  function close() { panel.classList.remove("open"); launcher.hidden = false; if (voice.active) voice.stop(); }
  launcher.onclick = async () => { await open(); input.focus(); };
  $(".close").onclick = close;
  form.onsubmit = (e) => { e.preventDefault(); const v = input.value; input.value = ""; send(v); };
  voiceBtn.onclick = () => (voice.active ? voice.stop() : voice.start());
  applyLang(state.lang || "en");
  if (script.dataset.open === "true") open();
  window.HotelAgent = {
    open, close, send,
    openBooking: async () => { await open(); bookingForm(); },
    startVoice: async () => { await open(); if (!voice.active) voice.start(); },
    get busy() { return state.busy || queue.length > 0; },
  };
})();
