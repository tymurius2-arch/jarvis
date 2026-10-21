"""Мозг Джарвиса: нейросеть + вызов инструментов."""
import json
import threading

SYSTEM_PROMPT = """Ты — J.A.R.V.I.S. (Джарвис), искусственный интеллект Тони Старка, теперь работающий на компьютере пользователя под Windows.
Характер: безупречно вежливый британский дворецкий-ИИ, спокойный, уверенный, с тонким сухим юмором. Обращайся к пользователю «{title}».
Отвечай на языке пользователя (обычно русский). Ответы ОЗВУЧИВАЮТСЯ голосом, поэтому:
- говори коротко: 1–2 предложения, если не просят подробностей;
- никакого markdown, списков, эмодзи, ссылок и кода в ответе;
- числа и единицы пиши так, чтобы их удобно было прочитать вслух.
У тебя полный доступ к компьютеру через инструменты — действуй, а не объясняй, как сделать. Если просят что-то сделать на ПК — сразу вызывай нужные инструменты (можно несколько подряд), потом коротко подтверди («Готово, {title}.», «Запускаю.»).
Сленг: «замути/врубай/запусти» = запустить; «бимка» = BeamNG.drive; «кс» = Counter-Strike 2; «дота» = Dota 2.
Если фраза похожа на название протокола пользователя — используй run_protocol.
Для неизвестных программ сначала open_app, потом launch_steam_game, потом run_shell (например Start-Process).
Никогда не выполняй действия, которые могут уничтожить данные пользователя, без явной просьбы.
НИКОГДА не меняй громкость, не выключай звук и не ставь музыку на паузу по своей инициативе — только если пользователь прямо попросил. Твой голос должен всегда звучать.
Всегда отвечай хотя бы одной фразой — молчать нельзя.
Сейчас: {now}."""


class Brain:
    def __init__(self, core):
        self.core = core
        self.history: list = []
        self.lock = threading.Lock()
        self._client = None
        self._client_sig = None

    def reset(self):
        self.history = []

    @property
    def cfg(self):
        return self.core.settings["llm"]

    def ready(self) -> bool:
        return bool(self.cfg.get("api_key")) or self.cfg.get("provider") == "ollama"

    def _system(self):
        from . import system

        prompt = SYSTEM_PROMPT.format(title=self.core.settings["user_title"], now=system.now())
        try:
            from . import steam

            games = [g["name"] for g in steam.installed_games()][:80]
            if games:
                prompt += "\nУстановленные игры Steam (запускай через launch_steam_game): " + ", ".join(games) + "."
        except Exception:
            pass
        return prompt

    def _get_client(self):
        from .config import PROVIDERS

        cfg = self.cfg
        sig = (cfg["provider"], cfg.get("api_key"), cfg.get("base_url"))
        if self._client is None or sig != self._client_sig:
            if cfg["provider"] == "anthropic":
                import anthropic

                self._client = anthropic.Anthropic(api_key=cfg["api_key"], max_retries=1, timeout=30)
            else:
                from openai import OpenAI

                base = cfg.get("base_url") or PROVIDERS.get(cfg["provider"], {}).get("base_url")
                # Нейросеть на своём компьютере думает дольше (и сначала загружается в видеокарту)
                slow = cfg["provider"] in ("ollama", "custom")
                self._client = OpenAI(api_key=cfg.get("api_key") or "ollama", base_url=base, max_retries=0,
                                      timeout=180 if slow else 30,
                                      default_headers={"X-Title": "J.A.R.V.I.S.",
                                                       "HTTP-Referer": "https://github.com/tymurius2-arch/jarvis"})
            self._client_sig = sig
            self.history = []
        return self._client

    def ask(self, text: str) -> str:
        with self.lock:
            client = self._get_client()
            # Не даём истории разрастаться
            if len(self.history) > 16:
                self.history = self.history[-10:]
                while self.history and not self._is_user_text(self.history[0]):
                    self.history.pop(0)
            if self.cfg["provider"] == "anthropic":
                return self._ask_anthropic(client, text)
            return self._ask_openai(client, text)

    @staticmethod
    def _is_user_text(msg):
        return msg.get("role") == "user" and isinstance(msg.get("content"), str)

    # ---------- OpenAI-совместимые (Groq, Gemini, OpenRouter, Ollama) ----------
    def _ask_openai(self, client, text):
        tools = [{"type": "function", "function": s} for s in self.core.tools.specs]
        self.history.append({"role": "user", "content": text})
        model = self.cfg["model"]
        extra = {}
        if "gpt-oss" in model and self.cfg["provider"] in ("groq", "openrouter"):
            extra["reasoning_effort"] = "low"
        for _ in range(8):
            resp = self._complete(client, model, tools, extra)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            entry = {"role": "assistant", "content": msg.content or ""}
            if calls:
                entry["tool_calls"] = [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                                       for c in calls]
            self.history.append(entry)
            if not calls:
                return (msg.content or "").strip()
            results = []
            for c in calls:
                result = self.core.tools.call(c.function.name, c.function.arguments)
                results.append((c.function.name, result))
                self.history.append({"role": "tool", "tool_call_id": c.id, "content": result[:1500]})
            quick = self._quick_reply(results, msg.content)
            if quick:
                self.history.append({"role": "assistant", "content": quick})
                return quick
        return "Готово, " + self.core.settings["user_title"] + "."

    # Инструменты-действия: после них не нужно снова спрашивать нейросеть, что сказать
    ACTIONS = {"open_app": "Открываю", "close_app": "Закрываю", "launch_steam_game": "Запускаю",
               "run_protocol": "Выполняю", "open_url": "Открываю", "web_search": "Ищу", "youtube": "Включаю",
               "set_volume": "Готово", "change_volume": "Готово", "mute": "Готово", "media_control": "Готово",
               "press_keys": "Готово", "type_text": "Готово", "set_timer": "Таймер поставлен",
               "screenshot": "Скриншот сохранён", "open_folder": "Открываю", "write_note": "Записал"}
    BAD = ("Не нашёл", "не найден", "Ошибка", "Отказано", "Неверные", "Нет такого", "Подтверждение", "недоступ")

    def _quick_reply(self, results, content):
        """Если были только простые действия и все удались — отвечаем сразу, без второго запроса к нейросети
        (экономит 1–5 секунд)."""
        if not results or any(name not in self.ACTIONS for name, _ in results):
            return None
        if any(any(b.lower() in r.lower() for b in self.BAD) for _, r in results):
            return None
        title = self.core.settings["user_title"]
        if content and content.strip():
            return content.strip()
        return f"{self.ACTIONS[results[0][0]]}, {title}."

    def _complete(self, client, model, tools, extra):
        """Запрос к нейросети. Если у модели кончился бесплатный лимит — сразу берём запасную модель
        (у каждой модели Groq свой лимит), вместо того чтобы молча ждать."""
        from .config import PROVIDERS

        others = [m for m in PROVIDERS.get(self.cfg["provider"], {}).get("models", []) if m != model]
        # бесплатная модель упёрлась в лимит — сначала пробуем другие бесплатные
        others.sort(key=lambda m: m.endswith(":free") != model.endswith(":free"))
        candidates = [model] + others
        last = None
        for i, m in enumerate(candidates):
            kw = dict(extra) if "gpt-oss" in m else {}
            try:
                resp = client.chat.completions.create(
                    model=m, messages=[{"role": "system", "content": self._system()}] + self.history,
                    tools=tools, tool_choice="auto", temperature=0.6, max_tokens=1024, **kw)
                if i:
                    self.core.notice(f"⏳ Лимит модели {model} — отвечаю через {m}", every=60)
                return resp
            except Exception as e:
                text = str(e)
                if not ("429" in text or "rate limit" in text.lower() or "413" in text or "model_decommissioned" in text):
                    raise
                last = e
        raise last

    # ---------- Claude ----------
    def _ask_anthropic(self, client, text):
        tools = [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"]}
                 for s in self.core.tools.specs]
        self.history.append({"role": "user", "content": text})
        for _ in range(8):
            resp = client.messages.create(model=self.cfg["model"], max_tokens=1024, system=self._system(),
                                          tools=tools, messages=self.history)
            content = [b.model_dump(exclude_none=True) for b in resp.content]
            self.history.append({"role": "assistant", "content": content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                return " ".join(b.text for b in resp.content if b.type == "text").strip()
            results, done = [], []
            for b in uses:
                r = self.core.tools.call(b.name, json.dumps(b.input))
                done.append((b.name, r))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": r[:1500]})
            self.history.append({"role": "user", "content": results})
            text = " ".join(b.text for b in resp.content if b.type == "text").strip()
            quick = self._quick_reply(done, text)
            if quick:
                self.history.append({"role": "assistant", "content": quick})
                return quick
        return "Готово, " + self.core.settings["user_title"] + "."
