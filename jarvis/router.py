"""Режим «Авто»: много нейросетей по очереди.

Каждая пара «сервис + модель» — отдельный «слот» со своим бесплатным лимитом.
Кончился лимит или сервис перегружен — слот отдыхает, отвечает следующий.
Последней всегда пробуется Ollama на компьютере пользователя (без лимитов).
"""
import json
import re
import threading
import time

import requests

from .config import PROVIDERS

# Порядок: быстрые и умные — первыми
AUTO_ORDER = ["gemini", "groq", "cerebras", "openrouter", "mistral", "github", "sambanova", "nvidia", "huggingface"]
SHORT = {"gemini": "Gemini", "groq": "Groq", "cerebras": "Cerebras", "openrouter": "OpenRouter", "mistral": "Mistral",
         "github": "GitHub", "sambanova": "SambaNova", "nvidia": "NVIDIA", "huggingface": "Hugging Face",
         "ollama": "Ollama"}

# Какие модели брать из каталога сервиса (по убыванию желательности)
PREFER = ["gpt-oss-120b", "llama-4-maverick", "qwen3", "deepseek-v4", "deepseek-v3", "kimi", "llama-3.3-70b",
          "gemma-4", "mistral-large", "nemotron", "glm", "llama-4-scout", "gpt-oss-20b", "gpt-4.1", "gpt-5"]
SKIP = ("embed", "whisper", "tts", "audio", "guard", "rerank", "image", "ocr", "moderation", "vision-only", "reward",
        "safety", "speech", "transcribe", "parse", "retriever")
PER_PROVIDER = 5


class NoBrains(Exception):
    pass


def classify(err: Exception) -> tuple[str, float]:
    """→ (вид, сколько секунд не трогать слот). Виды: auth, dead, busy, bad."""
    text = str(err)
    low = text.lower()
    code = getattr(err, "status_code", None)
    m = re.search(r"(?:try again in|retry after|retry in)\s*([\d.]+)\s*(ms|s|m)?", low)
    wait = 60.0
    if m:
        wait = float(m.group(1)) * {"ms": 0.001, "m": 60}.get(m.group(2) or "s", 1)
        wait = max(5.0, min(wait + 1, 3600.0))
    if code in (401, 403) or "invalid api key" in low or "unauthorized" in low or "incorrect api key" in low:
        return "auth", 1e9
    if code == 402 or "credit" in low or "payment" in low or "insufficient" in low and "balance" in low:
        return "dead", 1e9
    if code == 404 or "no endpoints" in low or ("model" in low and ("not found" in low or "does not exist" in low
                                                                      or "decommission" in low)):
        return "dead", 1e9
    if "tool" in low and ("support" in low or "not enabled" in low):
        return "dead", 1e9
    if code == 429 or "rate limit" in low or "quota" in low or "429" in text:
        return "busy", wait
    if code in (500, 502, 503, 504) or any(w in low for w in ("overloaded", "high demand", "unavailable", "timed out",
                                                               "timeout", "connection", "temporarily")):
        return "busy", 30.0
    return "bad", 120.0


class Router:
    def __init__(self, core):
        self.core = core
        self.cool: dict = {}   # (сервис, модель) -> до какого времени отдыхает
        self.dead: set = set()
        self.catalog: dict = {}  # сервис -> (время, [модели])
        self.clients: dict = {}
        self.lock = threading.Lock()
        self.last_slot = None

    # ---------- какие слоты есть ----------
    def keys(self) -> dict:
        keys = dict(self.core.settings.data.get("keys") or {})
        if self.core.settings["stt"].get("groq_key"):
            keys.setdefault("groq", self.core.settings["stt"]["groq_key"])
        return {k: v.strip() for k, v in keys.items() if v and v.strip()}

    def models(self, prov: str, key: str) -> list[str]:
        static = list(PROVIDERS.get(prov, {}).get("models", []))
        if not PROVIDERS.get(prov, {}).get("dynamic"):
            return static
        cached = self.catalog.get(prov)
        if cached and time.time() - cached[0] < 6 * 3600:
            found = cached[1]
        else:
            try:
                found = self._discover(prov, key)
            except Exception:
                found = []
            self.catalog[prov] = (time.time(), found)
        out = []
        for m in static + found:
            if m not in out:
                out.append(m)
        return out

    def _discover(self, prov: str, key: str) -> list[str]:
        headers = {"Authorization": f"Bearer {key}"}
        if prov == "github":
            data = requests.get("https://models.github.ai/catalog/models", headers=headers, timeout=6).json()
            ids = [m["id"] for m in data if "tool-calling" in (m.get("capabilities") or [])]
            return self._pick(ids)
        if prov == "openrouter":
            data = requests.get("https://openrouter.ai/api/v1/models", params={"supported_parameters": "tools"},
                                timeout=6).json()["data"]
            return [m["id"] for m in data if m["id"].endswith(":free")][:16]
        base = PROVIDERS[prov]["base_url"].rstrip("/")
        data = requests.get(f"{base}/models", headers=headers, timeout=6).json()
        ids = [m.get("id") for m in data.get("data", data if isinstance(data, list) else []) if m.get("id")]
        return self._pick(ids)

    @staticmethod
    def _pick(ids: list[str]) -> list[str]:
        good = [i for i in ids if not any(w in i.lower() for w in SKIP)]

        def rank(i):
            low = i.lower()
            for n, p in enumerate(PREFER):
                if p in low:
                    return n
            return len(PREFER)
        good.sort(key=rank)
        return [i for i in good if rank(i) < len(PREFER)][:PER_PROVIDER]

    def ollama_models(self) -> list[str]:
        cached = self.catalog.get("ollama")
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        try:
            tags = requests.get("http://localhost:11434/api/tags", timeout=1.5).json().get("models", [])
            names = [t["name"] for t in tags if "embed" not in t["name"]]
        except Exception:
            names = []
        llm = self.core.settings["llm"]
        if llm.get("provider") == "ollama" and llm.get("model") in names:
            names.remove(llm["model"])
            names.insert(0, llm["model"])
        self.catalog["ollama"] = (time.time(), names[:2])
        return names[:2]

    def slots(self) -> list[tuple[str, str]]:
        keys = self.keys()
        out = []
        for prov in AUTO_ORDER:
            if prov in keys:
                out += [(prov, m) for m in self.models(prov, keys[prov])]
        out += [("ollama", m) for m in self.ollama_models()]
        return out

    def status(self) -> dict:
        per = {}
        for prov, _m in self.slots():
            per[SHORT.get(prov, prov)] = per.get(SHORT.get(prov, prov), 0) + 1
        return {"total": sum(per.values()), "providers": per}

    # ---------- запрос ----------
    def client(self, prov: str):
        from openai import OpenAI

        key = self.keys().get(prov, "ollama")
        sig = (prov, key)
        if sig not in self.clients:
            base = PROVIDERS[prov]["base_url"]
            self.clients[sig] = OpenAI(api_key=key, base_url=base, max_retries=0,
                                       timeout=180 if prov == "ollama" else 20,
                                       default_headers={"X-Title": "J.A.R.V.I.S.",
                                                        "HTTP-Referer": "https://github.com/tymurius2-arch/jarvis"})
        return self.clients[sig]

    def complete(self, system_text: str, history: list, tools: list):
        """Пробует слоты по очереди. Возвращает (ответ, сервис)."""
        deadline = time.time() + 60
        tried, first = 0, None
        slots = self.slots()
        if not slots:
            raise NoBrains("Нет ни одного ключа нейросети, и Ollama не запущена.")
        last_err = None
        for prov, model in slots:
            if (prov, model) in self.dead or time.time() < self.cool.get((prov, model), 0):
                continue
            if time.time() > deadline:
                break
            first = first or (prov, model)
            tried += 1
            kw = {}
            sys_text = system_text
            if "gpt-oss" in model and prov in ("groq", "openrouter", "cerebras"):
                kw["reasoning_effort"] = "low"
            if prov == "ollama":
                kw["extra_body"] = {"think": False}
                if "qwen3" in model:
                    sys_text += "\n/no_think"
            try:
                resp = self.client(prov).chat.completions.create(
                    model=model, messages=[{"role": "system", "content": sys_text}] + prepare(history, prov),
                    tools=tools, tool_choice="auto", temperature=0.6, max_tokens=1024, **kw)
                if not resp.choices:
                    raise RuntimeError("пустой ответ")
                if self.last_slot and self.last_slot != (prov, model):
                    self.core.notice(f"🔀 Отвечает {SHORT.get(prov, prov)} · {model}", every=30)
                self.last_slot = (prov, model)
                return resp, prov
            except Exception as e:
                last_err = e
                kind, wait = classify(e)
                if kind in ("auth", "dead"):
                    self.dead.add((prov, model))
                    if kind == "auth":
                        for m in self.models(prov, self.keys().get(prov, "")):
                            self.dead.add((prov, m))
                        self.core.notice(f"🔑 Ключ {SHORT.get(prov, prov)} не подходит — пропускаю этот сервис",
                                         every=600)
                else:
                    self.cool[(prov, model)] = time.time() + wait
                self.core.log(f"{SHORT.get(prov, prov)} · {model}: {kind} — {str(e)[:160]}", "error")
        if last_err is None:
            soonest = min((self.cool.get(s, 0) for s in slots), default=0) - time.time()
            raise NoBrains(f"Все нейросети отдыхают после лимита. Ближайшая освободится через {max(1, int(soonest))} с.")
        raise last_err


def prepare(history: list, prov: str) -> list:
    """История для конкретного сервиса. Вызовы инструментов, сделанные другим сервисом,
    превращаем в обычный текст — у разных сервисов разные требования к их формату."""
    out = []
    flattened_ids = set()
    for m in history:
        m = dict(m)
        owner = m.pop("_prov", None)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            if owner and owner != prov or prov == "mistral" and owner != "mistral":
                names = ", ".join(f"{c['function']['name']}({c['function'].get('arguments', '')})"
                                  for c in m["tool_calls"])
                flattened_ids.update(c["id"] for c in m["tool_calls"])
                out.append({"role": "assistant", "content": (m.get("content") or "") + f" [вызвал: {names}]"})
                continue
            if prov != "gemini":
                m["tool_calls"] = [{k: v for k, v in c.items() if k != "extra_content"} for c in m["tool_calls"]]
        if m.get("role") == "tool" and m.get("tool_call_id") in flattened_ids:
            out.append({"role": "user", "content": f"[результат инструмента]: {m.get('content', '')}"})
            continue
        out.append(m)
    return out


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
