"""Настройки Джарвиса: хранятся в %APPDATA%\\Jarvis\\settings.json."""
import copy
import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "Jarvis"


def resource_dir() -> Path:
    """Папка с файлами программы (работает и в .exe, и из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "jarvis"
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


# Готовые провайдеры нейросетей. Все, кроме Claude, говорят на "OpenAI-совместимом" языке.
PROVIDERS = {
    "auto": {
        "hint": "Джарвис сам переключается между всеми нейросетями, к которым есть ключ: кончился лимит у одной — "
                "отвечает следующая. Ключи бесплатные, получите хотя бы 2–3. Последней всегда отвечает Ollama.",
        "title": "⚡ Авто — много нейросетей по очереди (рекомендую)",
        "base_url": "",
        "models": [],
        "key_url": "",
    },
    "groq": {
        "hint": "Бесплатно: войдите через Google на console.groq.com → «Create API Key» → вставьте сюда. Этот же ключ даёт Джарвису отличный слух (Whisper).",
        "title": "Groq — бесплатно и очень быстро (рекомендую)",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
        "key_url": "https://console.groq.com/keys",
    },
    "gemini": {
        "hint": "Бесплатно: aistudio.google.com/apikey → «Create API key» → вставьте сюда. Если модель перегружена, Джарвис сам возьмёт запасную.",
        "title": "Google Gemini — есть бесплатный лимит",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest"],
        "key_url": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "hint": "Платно: console.anthropic.com → Settings → API Keys → «Create Key».",
        "title": "Claude (Anthropic) — самый умный, платный",
        "base_url": "",
        "models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openrouter": {
        "hint": "openrouter.ai/keys → «Create Key». Модели с «:free» — бесплатные, но с маленьким лимитом.",
        "title": "OpenRouter — сотни моделей по одному ключу",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["google/gemini-3.8-flash", "openai/gpt-oss-120b", "anthropic/claude-haiku-5.5",
                   "deepseek/deepseek-v4.1-flash", "anthropic/claude-sonnet-5.5",
                   "nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"],
        "dynamic": True,
        "key_url": "https://openrouter.ai/keys",
    },
    "ollama": {
        "hint": "Ключ не нужен. Установите Ollama, в командной строке: ollama pull qwen3:8b. Работает без лимитов, но медленнее облачных.",
        "title": "Ollama — нейросеть прямо на твоём компьютере, без интернета",
        "base_url": "http://localhost:11434/v1",
        "models": ["qwen3:8b", "llama3.1:8b", "gpt-oss:20b"],
        "key_url": "https://ollama.com/download",
    },
    "cerebras": {
        "hint": "Бесплатно: cloud.cerebras.ai → API Keys. Очень быстрые ответы.",
        "title": "Cerebras — бесплатно, очень быстро",
        "base_url": "https://api.cerebras.ai/v1",
        "models": [],
        "dynamic": True,
        "key_url": "https://cloud.cerebras.ai/platform",
    },
    "mistral": {
        "hint": "Бесплатно: console.mistral.ai → API Keys (бесплатный тариф «Experiment»).",
        "title": "Mistral — бесплатный тариф",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-medium-latest", "mistral-small-latest", "mistral-large-latest"],
        "key_url": "https://console.mistral.ai/api-keys",
    },
    "github": {
        "hint": "Бесплатно для аккаунта GitHub: Settings → Personal access tokens → Fine-grained → разрешение «Models: Read».",
        "title": "GitHub Models — бесплатно с аккаунтом GitHub",
        "base_url": "https://models.github.ai/inference",
        "models": ["openai/gpt-4.1-mini", "openai/gpt-4.1"],
        "dynamic": True,
        "key_url": "https://github.com/settings/personal-access-tokens/new",
    },
    "sambanova": {
        "hint": "Бесплатно: cloud.sambanova.ai → APIs → Create API key.",
        "title": "SambaNova — бесплатно",
        "base_url": "https://api.sambanova.ai/v1",
        "models": [],
        "dynamic": True,
        "key_url": "https://cloud.sambanova.ai/apis",
    },
    "nvidia": {
        "hint": "Бесплатно: build.nvidia.com → войти → API Keys → Generate.",
        "title": "NVIDIA — бесплатные пробные запросы",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "models": [],
        "dynamic": True,
        "key_url": "https://build.nvidia.com/settings/api-keys",
    },
    "huggingface": {
        "hint": "Бесплатно: huggingface.co → Settings → Access Tokens → Create (тип Read).",
        "title": "Hugging Face — бесплатный лимит каждый месяц",
        "base_url": "https://router.huggingface.co/v1",
        "models": [],
        "dynamic": True,
        "key_url": "https://huggingface.co/settings/tokens",
    },
    "custom": {
        "hint": "Адрес и ключ любого OpenAI-совместимого сервера.",
        "title": "Другой (OpenAI-совместимый адрес)",
        "base_url": "",
        "models": [],
        "key_url": "",
    },
}

VOICES = [
    {"id": "ru-RU-DmitryNeural", "title": "Дмитрий — русский, мужской (по умолчанию)"},
    {"id": "en-US-AndrewMultilingualNeural", "title": "Andrew — по-русски с английским акцентом"},
    {"id": "en-US-BrianMultilingualNeural", "title": "Brian — по-русски с английским акцентом, ниже"},
    {"id": "de-DE-FlorianMultilingualNeural", "title": "Florian — по-русски с акцентом"},
    {"id": "uk-UA-OstapNeural", "title": "Остап — украинский, мужской"},
    {"id": "en-GB-RyanNeural", "title": "Ryan — британский (ТОЛЬКО английский текст)"},
    {"id": "en-GB-ThomasNeural", "title": "Thomas — британский (ТОЛЬКО английский текст)"},
]

DEFAULTS = {
    "user_title": "сэр",
    "language": "ru",
    "wake_words": ["джарвис", "jarvis", "джервис", "жарвис", "джарвиз", "джарвес", "дарвис", "джарвіс"],
    "require_wake_word": True,
    "follow_up_seconds": 8,
    "llm": {"provider": "auto", "api_key": "", "model": "", "base_url": ""},
    "keys": {},  # ключи всех сервисов для режима «Авто»
    "stt": {"provider": "auto", "device": None, "sensitivity": 60},
    "tts": {
        "provider": "edge",
        "voice": "ru-RU-DmitryNeural",
        "rate": "+8%",
        "pitch": "-4Hz",
        "fx": True,
        "volume": 100,
        "elevenlabs_key": "",
        "elevenlabs_voice": "",
    },
    "listening": True,
    "allow_shell": True,
    "confirm_power": True,
    "autostart": False,
    "minimize_to_tray": True,
    "first_run": True,
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self):
        self.path = data_dir() / "settings.json"
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                self.data = _merge(DEFAULTS, json.loads(self.path.read_text("utf-8")))
            except Exception:
                pass
        # Ключ текущего сервиса и ключ слуха Groq — тоже в общий список для «Авто»
        keys = self.data.setdefault("keys", {})
        llm = self.data["llm"]
        if llm.get("api_key") and llm.get("provider") not in ("auto", "custom", "ollama"):
            keys.setdefault(llm["provider"], llm["api_key"])
        if self.data["stt"].get("groq_key"):
            keys.setdefault("groq", self.data["stt"]["groq_key"])
        # Старые неверные названия моделей OpenRouter из первых версий
        if self.data["llm"].get("model") in ("google/gemini-flash-latest", "anthropic/claude-haiku-5-5"):
            self.data["llm"]["model"] = "google/gemini-3.8-flash"
        # Ключ можно задать и через переменную окружения.
        if not self.data["llm"]["api_key"]:
            env = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                   "openrouter": "OPENROUTER_API_KEY"}.get(self.data["llm"]["provider"])
            if env and os.environ.get(env):
                self.data["llm"]["api_key"] = os.environ[env]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def update(self, patch: dict):
        with self._lock:
            self.data = _merge(self.data, patch)
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    def groq_key(self) -> str:
        """Ключ Groq нужен ещё и для распознавания речи через Whisper."""
        if self.data["llm"]["provider"] == "groq" and self.data["llm"]["api_key"]:
            return self.data["llm"]["api_key"]
        return (self.data["stt"].get("groq_key") or self.data.get("keys", {}).get("groq")
                or os.environ.get("GROQ_API_KEY", ""))
