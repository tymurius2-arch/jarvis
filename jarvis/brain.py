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

        return SYSTEM_PROMPT.format(title=self.core.settings["user_title"], now=system.now())

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
                self._client = OpenAI(api_key=cfg.get("api_key") or "ollama", base_url=base, max_retries=0, timeout=30)
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
            for c in calls:
                result = self.core.tools.call(c.function.name, c.function.arguments)
                self.history.append({"role": "tool", "tool_call_id": c.id, "content": result[:1500]})
        return "Готово, " + self.core.settings["user_title"] + "."

    def _complete(self, client, model, tools, extra):
        """Запрос к нейросети. Если у модели кончился бесплатный лимит — сразу берём запасную модель
        (у каждой модели Groq свой лимит), вместо того чтобы молча ждать."""
        from .config import PROVIDERS

        candidates = [model] + [m for m in PROVIDERS.get(self.cfg["provider"], {}).get("models", []) if m != model]
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
            results = []
            for b in uses:
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": self.core.tools.call(b.name, json.dumps(b.input))[:1500]})
            self.history.append({"role": "user", "content": results})
        return "Готово, " + self.core.settings["user_title"] + "."
