"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let api = null;
let S = null; // состояние из Python
let state = "idle";
let speakEnv = null, speakStart = 0, speakDur = 0;
let micLevel = 0;

const STATES = {
  idle: ["В РЕЖИМЕ ОЖИДАНИЯ", "Скажите «Джарвис» или нажмите на ядро"],
  listening: ["СЛУШАЮ", "Говорите команду…"],
  hearing: ["СЛЫШУ ВАС", ""],
  recognizing: ["РАСПОЗНАЮ", ""],
  thinking: ["АНАЛИЗИРУЮ", "Нейросеть обрабатывает запрос"],
  speaking: ["ОТВЕЧАЮ", ""],
  off: ["МИКРОФОН ВЫКЛЮЧЕН", "Включите «Слушать» слева или пишите текстом"],
};

// ---------- запуск ----------
window.addEventListener("pywebviewready", () => { api = window.pywebview.api; init(); });
setTimeout(() => { if (!api) { api = mockApi(); init(); } }, 1500); // просмотр в обычном браузере

async function init() {
  if (init.done) return; init.done = true;
  S = await api.get_state();
  renderAll();
  S.history.forEach(addMsg);
  if (!S.history.length) showEmpty();
  setState(S.settings.listening ? "idle" : "off");
  if (!S.ai_ready) setTimeout(() => toast("Подключите нейросеть: Настройки → ключ Groq (бесплатно)"), 2500);
}

function renderAll() {
  renderStatus(); renderProtocols(); fillSettings(); renderQuick();
}

// ---------- события из Python ----------
window.jarvisEvent = (e) => {
  switch (e.type) {
    case "chat": $(".empty-chat")?.remove(); addMsg(e); if (e.role === "user") hideConfirm(); break;
    case "state": if (state !== "speaking" || e.state !== "idle" || performance.now() - speakStart > speakDur * 1000) setState(e.state); break;
    case "speak": speakEnv = e.envelope; speakDur = e.duration; speakStart = performance.now(); setState("speaking");
      setTimeout(() => { if (state === "speaking") setState(S?.settings.listening ? "idle" : "off"); }, e.duration * 1000 + 200); break;
    case "level": micLevel = e.value; $("#mic-meter").style.width = Math.min(100, e.value * 260) + "%"; break;
    case "log": addLog(e); break;
    case "protocol": toast(`${e.icon} Протокол «${e.name}»`); break;
    case "confirm": showConfirm(e.text); break;
    case "toast": toast(e.text); break;
    case "mic": S && (S.mic_error = e.ok ? "" : e.error); renderStatus(); break;
    case "settings": S.settings = e.settings; fillSettings(); renderStatus(); break;
  }
};

function setState(s) {
  if (s === "idle" && S && !S.settings.listening) s = "off";
  state = s;
  const [label, hint] = STATES[s] || STATES.idle;
  $("#state-label").textContent = label;
  if (hint || s !== "speaking") $("#state-hint").textContent = hint;
  $("#mic-btn").classList.toggle("on", s === "listening" || s === "hearing");
}

// ---------- навигация ----------
$$(".nav").forEach((b) => b.onclick = () => {
  $$(".nav").forEach((x) => x.classList.toggle("active", x === b));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === "page-" + b.dataset.page));
  if (b.dataset.page === "games") loadGames();
});

// ---------- статус ----------
function renderStatus() {
  if (!S) return;
  const p = S.providers[S.settings.llm.provider];
  const ai = $("#st-ai");
  ai.textContent = S.ai_ready ? (S.settings.llm.model || p?.title).split("/").pop() : "нет ключа";
  ai.className = S.ai_ready ? "ok" : "bad";
  const mic = $("#st-mic");
  mic.textContent = S.mic_error ? "ошибка" : (S.settings.listening ? "слушает" : "выключен");
  mic.className = S.mic_error ? "bad" : S.settings.listening ? "ok" : "";
  mic.title = S.mic_error || "";
  $("#st-voice").textContent = S.settings.tts.provider === "elevenlabs" ? "ElevenLabs" : S.settings.tts.voice.split("-")[2].replace("Neural", "").replace("Multilingual", "");
  $("#listen-toggle").checked = S.settings.listening;
}
$("#listen-toggle").onchange = async (e) => {
  S.settings.listening = await api.set_listening(e.target.checked);
  renderStatus(); setState(S.settings.listening ? "idle" : "off");
};

// ---------- диалог ----------
function addMsg(m) {
  const d = document.createElement("div");
  d.className = "msg " + (m.role === "user" ? "user" : "jarvis");
  d.innerHTML = `<small>${m.role === "user" ? "ВЫ" : "ДЖАРВИС"} · ${m.time || ""}</small>`;
  d.appendChild(document.createTextNode(m.text));
  $("#chat").appendChild(d);
  $("#chat").scrollTop = 1e9;
}
function showEmpty() {
  $("#chat").innerHTML = `<div class="empty-chat">Скажите <b>«Джарвис, …»</b> или напишите ниже.<br>
    Например: <b>«Джарвис, замути бимку»</b>, <b>«какая погода в Киеве»</b>,<br><b>«сделай громкость 40»</b>, <b>«открой ютуб»</b>.</div>`;
}
$("#input-form").onsubmit = (e) => {
  e.preventDefault();
  const v = $("#input").value.trim();
  if (!v) return;
  $("#input").value = "";
  api.send_text(v);
};
$("#mic-btn").onclick = () => api.listen_now();
$("#reactor").onclick = () => api.listen_now();
$("#clear-chat").onclick = () => { api.clear_chat(); $("#chat").innerHTML = ""; showEmpty(); };
function renderQuick() {
  const items = ["Который час?", "Какая погода в Киеве?", "Как дела у компьютера?", "Включи lo-fi на ютубе", "Сверни все окна"];
  S.protocols.filter((p) => p.enabled !== false).slice(0, 3).reverse().forEach((p) => items.unshift(p.name));
  $("#quick").innerHTML = "";
  items.slice(0, 7).forEach((t) => { const c = el("button", "chip", t); c.onclick = () => api.send_text(t); $("#quick").appendChild(c); });
}
function showConfirm(text) { $("#confirm-text").textContent = text + " — подтверждаете?"; $("#confirm-bar").classList.remove("hidden"); }
function hideConfirm() { $("#confirm-bar").classList.add("hidden"); }
$("#confirm-yes").onclick = () => { api.send_text("да"); hideConfirm(); };
$("#confirm-no").onclick = () => { api.send_text("нет"); hideConfirm(); };

// ---------- журнал ----------
function addLog(e) {
  const d = document.createElement("div");
  d.className = e.kind;
  d.innerHTML = `<span class="t">${e.time}</span>`;
  d.appendChild(document.createTextNode(e.text));
  const log = $("#log");
  log.prepend(d);
  while (log.children.length > 400) log.lastChild.remove();
}

// ---------- протоколы ----------
const ACTION_HINT = {
  say: "Текст, который скажет Джарвис", steam: "appid игры (284160 = BeamNG) или название", open_app: "Название программы: Discord, Telegram…",
  open_url: "https://…", run: "Путь к файлу .exe", close_app: "Название программы", hotkey: "Например win+d, alt+tab, ctrl+shift+esc",
  type: "Текст для печати", volume: "0–100", media: "play_pause / next / previous", wait: "Секунды", shell: "Команда PowerShell",
  power: "sleep / lock / shutdown / restart / monitor_off", ai: "Вопрос нейросети, ответ будет озвучен",
};
function renderProtocols() {
  const box = $("#proto-list");
  box.innerHTML = "";
  S.protocols.forEach((p) => {
    const c = el("div", "card proto" + (p.enabled === false ? " off" : ""));
    c.innerHTML = `<div class="proto-top"><div class="proto-icon">${esc(p.icon || "⚡")}</div>
      <div><div class="proto-name">${esc(p.name)}</div><div class="proto-meta">${p.actions.length} действий${p.confirm ? " · с подтверждением" : ""}</div></div></div>
      <div class="tags">${p.triggers.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      <div class="steps">${p.actions.map((a, i) => `${i + 1}. ${esc(S.action_types[a.type] || a.type)}${a.value ? ": " + esc(String(a.value)).slice(0, 48) : ""}`).join("<br>")}</div>`;
    const run = el("button", "ghost small run", "▶");
    run.title = "Выполнить";
    run.onclick = (e) => { e.stopPropagation(); api.run_protocol(p.id); };
    c.appendChild(run);
    c.onclick = () => openEditor(p);
    box.appendChild(c);
  });
  renderQuick();
}
$("#proto-new").onclick = () => openEditor({ name: "", icon: "⚡", triggers: [], enabled: true, confirm: false, actions: [{ type: "say", value: "Выполняю, сэр." }] });
$("#proto-export").onclick = async () => toast("Сохранено: " + await api.export_protocols());
$("#proto-import").onclick = async () => { S.protocols = await api.import_protocols(); renderProtocols(); };
let testTimer;
$("#phrase-test").oninput = (e) => {
  clearTimeout(testTimer);
  testTimer = setTimeout(async () => {
    const v = e.target.value.trim();
    const r = v ? await api.test_phrase(v) : null;
    $("#phrase-result").textContent = v ? (r ? "→ " + r : "→ нейросеть") : "";
    $("#phrase-result").style.color = r ? "var(--green)" : "var(--muted)";
  }, 200);
};

let editing = null;
function openEditor(p) {
  editing = JSON.parse(JSON.stringify(p));
  $("#ed-title").textContent = p.id ? "РЕДАКТИРОВАНИЕ ПРОТОКОЛА" : "НОВЫЙ ПРОТОКОЛ";
  $("#ed-name").value = editing.name;
  $("#ed-icon").value = editing.icon || "";
  $("#ed-triggers").value = editing.triggers.join(", ");
  $("#ed-enabled").checked = editing.enabled !== false;
  $("#ed-confirm").checked = !!editing.confirm;
  $("#ed-delete").classList.toggle("hidden", !p.id);
  const sel = $("#ed-add-type");
  sel.innerHTML = Object.entries(S.action_types).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  renderActions();
  $("#editor").classList.remove("hidden");
}
function renderActions() {
  const box = $("#ed-actions");
  box.innerHTML = "";
  editing.actions.forEach((a, i) => {
    const row = el("div", "action");
    row.innerHTML = `<div class="num">${i + 1}</div>`;
    const type = document.createElement("select");
    type.innerHTML = Object.entries(S.action_types).map(([k, v]) => `<option value="${k}" ${k === a.type ? "selected" : ""}>${v}</option>`).join("");
    type.onchange = () => { a.type = type.value; renderActions(); };
    const val = el("div", "val");
    const inp = document.createElement("input");
    inp.value = a.value ?? "";
    inp.placeholder = ACTION_HINT[a.type] || "";
    inp.oninput = () => a.value = inp.value;
    val.appendChild(inp);
    if (a.type === "run") {
      const b = el("button", "ghost small", "…");
      b.type = "button";
      b.onclick = async () => { const f = await api.pick_file(); if (f) { a.value = f; inp.value = f; } };
      val.appendChild(b);
    }
    if (a.type === "steam") {
      const b = el("button", "ghost small", "Выбрать");
      b.type = "button";
      b.onclick = () => pickGame((g) => { a.value = String(g.appid); inp.value = a.value; });
      val.appendChild(b);
    }
    const btns = el("div", "btns");
    [["↑", -1], ["↓", 1]].forEach(([t, d]) => {
      const b = el("button", "", t); b.type = "button";
      b.onclick = () => { const j = i + d; if (j < 0 || j >= editing.actions.length) return; [editing.actions[i], editing.actions[j]] = [editing.actions[j], editing.actions[i]]; renderActions(); };
      btns.appendChild(b);
    });
    const del = el("button", "", "✕"); del.type = "button";
    del.onclick = () => { editing.actions.splice(i, 1); renderActions(); };
    btns.appendChild(del);
    row.append(type, val, btns);
    box.appendChild(row);
  });
  if (!editing.actions.length) box.innerHTML = `<p class="hint">Добавьте действия кнопкой «+ Добавить».</p>`;
}
function collectEditor() {
  editing.name = $("#ed-name").value.trim() || "Без названия";
  editing.icon = $("#ed-icon").value.trim() || "⚡";
  editing.triggers = $("#ed-triggers").value.split(",").map((s) => s.trim()).filter(Boolean);
  editing.enabled = $("#ed-enabled").checked;
  editing.confirm = $("#ed-confirm").checked;
  return editing;
}
$("#ed-add").onclick = () => { editing.actions.push({ type: $("#ed-add-type").value, value: "" }); renderActions(); };
$("#ed-close").onclick = () => $("#editor").classList.add("hidden");
$("#ed-save").onclick = async () => {
  const p = collectEditor();
  if (!p.triggers.length) return toast("Добавьте хотя бы одну фразу-активатор");
  S.protocols = await api.save_protocol(p);
  renderProtocols();
  $("#editor").classList.add("hidden");
  toast("Протокол сохранён");
};
$("#ed-delete").onclick = async () => {
  if (!confirm("Удалить протокол «" + editing.name + "»?")) return;
  S.protocols = await api.delete_protocol(editing.id);
  renderProtocols();
  $("#editor").classList.add("hidden");
};
$("#ed-run").onclick = async () => {
  const p = collectEditor();
  if (!p.id) { S.protocols = await api.save_protocol(p); renderProtocols(); p.id = S.protocols[S.protocols.length - 1].id; editing.id = p.id; }
  else { S.protocols = await api.save_protocol(p); renderProtocols(); }
  api.run_protocol(p.id);
};

// ---------- игры ----------
let games = [];
async function loadGames() {
  $("#games-list").innerHTML = `<p class="muted">Ищу игры…</p>`;
  games = await api.steam_games();
  renderGames();
}
function renderGames() {
  const q = $("#game-filter").value.toLowerCase();
  const box = $("#games-list");
  box.innerHTML = "";
  const list = games.filter((g) => g.name.toLowerCase().includes(q));
  if (!list.length) box.innerHTML = `<p class="muted">${games.length ? "Ничего не найдено" : "Steam или игры не найдены. Установите Steam и игры — они появятся здесь."}</p>`;
  list.forEach((g) => {
    const c = el("div", "card");
    c.innerHTML = `<img class="game-img" loading="lazy" src="https://cdn.cloudflare.steamstatic.com/steam/apps/${g.appid}/header.jpg" onerror="this.style.visibility='hidden'">
      <div class="game-name">${esc(g.name)}</div><div class="row"></div>`;
    const r = c.querySelector(".row");
    const play = el("button", "primary small", "▶ Запустить"); play.onclick = () => api.launch_game(g.appid);
    const mk = el("button", "ghost small", "+ Протокол");
    mk.onclick = () => openEditor({ name: g.name, icon: "🎮", triggers: [g.name.toLowerCase()], enabled: true, confirm: false,
      actions: [{ type: "say", value: `Запускаю ${g.name}, сэр.` }, { type: "steam", value: String(g.appid) }] });
    r.append(play, mk);
    box.appendChild(c);
  });
}
$("#game-filter").oninput = renderGames;
$("#games-refresh").onclick = loadGames;
async function pickGame(cb) {
  if (!games.length) games = await api.steam_games();
  if (!games.length) return toast("Игры Steam не найдены");
  const name = prompt("Название игры (часть):\n" + games.slice(0, 30).map((g) => g.name).join("\n"));
  if (!name) return;
  const g = games.find((x) => x.name.toLowerCase().includes(name.toLowerCase()));
  g ? cb(g) : toast("Не найдено");
}

// ---------- настройки ----------
function fillSettings() {
  const st = S.settings;
  $("#s-provider").innerHTML = Object.entries(S.providers).map(([k, v]) => `<option value="${k}">${v.title}</option>`).join("");
  $("#s-provider").value = st.llm.provider;
  $("#s-key").value = st.llm.api_key || "";
  $("#s-model").value = st.llm.model || "";
  $("#s-base").value = st.llm.base_url || "";
  fillModels();
  $("#s-voice").innerHTML = S.voices.map((v) => `<option value="${v.id}">${v.title}</option>`).join("");
  if (!S.voices.find((v) => v.id === st.tts.voice)) $("#s-voice").insertAdjacentHTML("beforeend", `<option value="${st.tts.voice}">${st.tts.voice}</option>`);
  $("#s-voice").value = st.tts.voice;
  $("#s-rate").value = st.tts.rate; $("#s-pitch").value = st.tts.pitch;
  $("#s-vol").value = st.tts.volume; $("#s-vol-v").textContent = st.tts.volume + "%";
  $("#s-fx").checked = st.tts.fx;
  $("#s-el-key").value = st.tts.elevenlabs_key || ""; $("#s-el-voice").value = st.tts.elevenlabs_voice || "";
  $("#s-el-on").checked = st.tts.provider === "elevenlabs";
  $("#s-device").innerHTML = `<option value="">Микрофон по умолчанию</option>` + S.devices.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("");
  $("#s-device").value = st.stt.device ?? "";
  $("#s-sens").value = st.stt.sensitivity; $("#s-sens-v").textContent = st.stt.sensitivity;
  $("#s-stt").value = st.stt.provider; $("#s-lang").value = st.language;
  $("#s-wake").value = st.wake_words.join(", ");
  $("#s-require").checked = st.require_wake_word;
  $("#s-title").value = st.user_title;
  $("#s-shell").checked = st.allow_shell; $("#s-confirm").checked = st.confirm_power;
  $("#s-autostart").checked = st.autostart; $("#s-tray").checked = st.minimize_to_tray;
}
function fillModels() {
  const p = S.providers[$("#s-provider").value];
  $("#s-models").innerHTML = (p.models || []).map((m) => `<option value="${m}">`).join("");
  $("#s-base-wrap").classList.toggle("hidden", !["custom", "ollama"].includes($("#s-provider").value));
  $("#s-key-get").classList.toggle("hidden", !p.key_url);
}
let saveTimer;
function save(patch, now) {
  clearTimeout(saveTimer);
  const go = async () => { S = Object.assign(S, await api.save_settings(patch)); renderStatus(); };
  now ? go() : (saveTimer = setTimeout(go, 400));
}
const bind = (id, ev, fn) => $(id).addEventListener(ev, fn);
bind("#s-provider", "change", () => {
  const p = S.providers[$("#s-provider").value];
  fillModels();
  $("#s-model").value = p.models[0] || "";
  if ($("#s-provider").value !== S.settings.llm.provider) $("#s-key").value = "";
  save({ llm: { provider: $("#s-provider").value, model: $("#s-model").value, api_key: $("#s-key").value, base_url: p.base_url && $("#s-provider").value !== "ollama" ? "" : $("#s-base").value || p.base_url } }, true);
});
bind("#s-key", "input", () => save({ llm: { api_key: $("#s-key").value.trim() } }));
bind("#s-model", "change", () => save({ llm: { model: $("#s-model").value.trim() } }));
bind("#s-base", "change", () => save({ llm: { base_url: $("#s-base").value.trim() } }));
bind("#s-key-get", "click", () => api.open_link(S.providers[$("#s-provider").value].key_url));
bind("#s-test-ai", "click", async () => {
  $("#s-ai-result").textContent = "Проверяю…";
  clearTimeout(saveTimer);
  await api.save_settings({ llm: { api_key: $("#s-key").value.trim(), model: $("#s-model").value.trim() } });
  const r = await api.test_ai();
  $("#s-ai-result").textContent = (r.ok ? "✅ " : "❌ ") + r.text.slice(0, 160);
  S = Object.assign(S, await api.get_state()); renderStatus();
});
bind("#s-voice", "change", () => save({ tts: { voice: $("#s-voice").value } }, true));
bind("#s-rate", "change", () => save({ tts: { rate: fmt($("#s-rate").value, "%") } }));
bind("#s-pitch", "change", () => save({ tts: { pitch: fmt($("#s-pitch").value, "Hz") } }));
bind("#s-vol", "input", () => { $("#s-vol-v").textContent = $("#s-vol").value + "%"; save({ tts: { volume: +$("#s-vol").value } }); });
bind("#s-fx", "change", () => save({ tts: { fx: $("#s-fx").checked } }, true));
bind("#s-el-key", "change", () => save({ tts: { elevenlabs_key: $("#s-el-key").value.trim() } }));
bind("#s-el-voice", "change", () => save({ tts: { elevenlabs_voice: $("#s-el-voice").value.trim() } }));
bind("#s-el-on", "change", () => save({ tts: { provider: $("#s-el-on").checked ? "elevenlabs" : "edge" } }, true));
bind("#s-test-voice", "click", async () => { clearTimeout(saveTimer); await api.save_settings({}); api.test_voice(); });
bind("#s-device", "change", () => save({ stt: { device: $("#s-device").value === "" ? null : +$("#s-device").value } }, true));
bind("#s-sens", "input", () => { $("#s-sens-v").textContent = $("#s-sens").value; save({ stt: { sensitivity: +$("#s-sens").value } }); });
bind("#s-stt", "change", () => save({ stt: { provider: $("#s-stt").value } }, true));
bind("#s-lang", "change", () => save({ language: $("#s-lang").value }, true));
bind("#s-wake", "change", () => save({ wake_words: $("#s-wake").value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean) }, true));
bind("#s-require", "change", () => save({ require_wake_word: $("#s-require").checked }, true));
bind("#s-title", "change", () => save({ user_title: $("#s-title").value.trim() || "сэр" }, true));
bind("#s-shell", "change", () => save({ allow_shell: $("#s-shell").checked }, true));
bind("#s-confirm", "change", () => save({ confirm_power: $("#s-confirm").checked }, true));
bind("#s-autostart", "change", () => save({ autostart: $("#s-autostart").checked }, true));
bind("#s-tray", "change", () => save({ minimize_to_tray: $("#s-tray").checked }, true));
bind("#s-open-dir", "click", () => api.open_data_dir());
bind("#s-quit", "click", () => { if (confirm("Выключить Джарвиса полностью?")) api.quit(); });
function fmt(v, unit) { v = v.trim().replace(unit, ""); if (!v) v = "0"; if (!/^[+-]/.test(v)) v = "+" + v; return v + unit; }

// ---------- ядро (анимация) ----------
const cv = $("#reactor"), cx = cv.getContext("2d");
let t0 = performance.now(), smooth = 0, spin = 0;
function colorFor(s) {
  return { hearing: [255, 181, 71], listening: [255, 120, 110], recognizing: [255, 181, 71], thinking: [160, 140, 255], off: [90, 110, 125] }[s] || [63, 212, 255];
}
let col = [63, 212, 255];
function frame(now) {
  const W = cv.width, H = cv.height, C = W / 2, t = (now - t0) / 1000;
  let amp = 0;
  if (state === "speaking" && speakEnv) {
    const i = Math.floor((now - speakStart) / 50);
    amp = speakEnv[i] ?? 0;
  } else if (state === "hearing" || state === "listening") amp = Math.min(1, micLevel * 4);
  smooth += (amp - smooth) * 0.25;
  const target = colorFor(state);
  col = col.map((c, i) => c + (target[i] - c) * 0.08);
  const rgb = (a) => `rgba(${col[0] | 0},${col[1] | 0},${col[2] | 0},${a})`;
  spin += state === "thinking" || state === "recognizing" ? 0.05 : 0.008;

  cx.clearRect(0, 0, W, H);
  // свечение
  const g = cx.createRadialGradient(C, C, 10, C, C, 230);
  g.addColorStop(0, rgb(0.35 + smooth * 0.4)); g.addColorStop(0.35, rgb(0.08 + smooth * 0.1)); g.addColorStop(1, rgb(0));
  cx.fillStyle = g; cx.fillRect(0, 0, W, H);

  cx.lineCap = "round";
  // внешние дуги
  const arcs = [[226, 2, 0.6, 4], [206, 1, -0.9, 6], [186, 3, 1.4, 3], [150, 1.5, -2.1, 10]];
  arcs.forEach(([r, w, sp, n], k) => {
    cx.strokeStyle = rgb(0.35 + 0.15 * k / arcs.length + smooth * 0.3);
    cx.lineWidth = w;
    for (let i = 0; i < n; i++) {
      const a = spin * sp + (i * Math.PI * 2) / n;
      cx.beginPath(); cx.arc(C, C, r + smooth * 6, a, a + (Math.PI * 2) / n * 0.62); cx.stroke();
    }
  });
  // деления
  cx.strokeStyle = rgb(0.5);
  cx.lineWidth = 1.2;
  for (let i = 0; i < 90; i++) {
    const a = (i / 90) * Math.PI * 2 - spin * 0.3, r1 = 166, r2 = i % 5 ? 172 : 180;
    cx.beginPath(); cx.moveTo(C + Math.cos(a) * r1, C + Math.sin(a) * r1); cx.lineTo(C + Math.cos(a) * r2, C + Math.sin(a) * r2); cx.stroke();
  }
  // волна голоса
  cx.beginPath();
  for (let i = 0; i <= 180; i++) {
    const a = (i / 180) * Math.PI * 2;
    const r = 118 + Math.sin(a * 6 + t * 3) * 3 + Math.sin(a * 13 - t * 5) * smooth * 16 + smooth * 10;
    const x = C + Math.cos(a) * r, y = C + Math.sin(a) * r;
    i ? cx.lineTo(x, y) : cx.moveTo(x, y);
  }
  cx.strokeStyle = rgb(0.85); cx.lineWidth = 2; cx.shadowBlur = 18; cx.shadowColor = rgb(1); cx.stroke();
  // треугольник реактора
  cx.save(); cx.translate(C, C); cx.rotate(-spin * 0.5);
  cx.beginPath();
  for (let i = 0; i < 3; i++) { const a = (i / 3) * Math.PI * 2 - Math.PI / 2; cx.lineTo(Math.cos(a) * 78, Math.sin(a) * 78); }
  cx.closePath(); cx.strokeStyle = rgb(0.55); cx.lineWidth = 3; cx.stroke(); cx.restore();
  // ядро
  const core = cx.createRadialGradient(C, C, 0, C, C, 60 + smooth * 14);
  core.addColorStop(0, "rgba(255,255,255,0.95)"); core.addColorStop(0.3, rgb(0.9)); core.addColorStop(1, rgb(0));
  cx.fillStyle = core; cx.beginPath(); cx.arc(C, C, 60 + smooth * 14, 0, Math.PI * 2); cx.fill();
  cx.shadowBlur = 0;
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ---------- часы ----------
function tick() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  $("#date").textContent = d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
}
tick(); setInterval(tick, 10000);

// ---------- мелочи ----------
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
let toastTimer;
function toast(text) { const t = $("#toast"); t.textContent = text; t.classList.remove("hidden"); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 3500); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#editor").classList.add("hidden"); });

// ---------- заглушка для просмотра в браузере ----------
function mockApi() {
  const st = {
    settings: { user_title: "сэр", language: "ru", wake_words: ["джарвис", "jarvis"], require_wake_word: true, listening: true,
      llm: { provider: "groq", api_key: "", model: "openai/gpt-oss-120b", base_url: "" },
      stt: { provider: "auto", device: null, sensitivity: 60 },
      tts: { provider: "edge", voice: "ru-RU-DmitryNeural", rate: "+8%", pitch: "-4Hz", fx: true, volume: 100 },
      allow_shell: true, confirm_power: true, autostart: false, minimize_to_tray: true },
    providers: { groq: { title: "Groq — бесплатно и очень быстро", models: ["openai/gpt-oss-120b"], key_url: "x" } },
    voices: [{ id: "ru-RU-DmitryNeural", title: "Дмитрий — русский" }],
    action_types: { say: "Сказать фразу", steam: "Запустить игру Steam", wait: "Пауза" },
    protocols: [{ id: "a", name: "Бимка", icon: "🚗", triggers: ["бимк", "beamng"], enabled: true, actions: [{ type: "say", value: "Запускаю BeamNG" }, { type: "steam", value: "284160" }] }],
    history: [], devices: [], ai_ready: false, mic_error: "",
  };
  const ok = async () => true;
  return new Proxy({ get_state: async () => st, steam_games: async () => [{ appid: 284160, name: "BeamNG.drive" }, { appid: 730, name: "Counter-Strike 2" }],
    test_phrase: async () => "Бимка", save_settings: async () => st,
    send_text: async (t) => { jarvisEvent({ type: "chat", role: "user", text: t, time: "12:00" }); setTimeout(() => jarvisEvent({ type: "chat", role: "jarvis", text: "Будет сделано, сэр.", time: "12:00" }), 400); } },
    { get: (o, k) => o[k] || ok });
}
