"""Быстрая проверка без микрофона и окна: python tests/smoke_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APPDATA"] = tempfile.mkdtemp()  # не трогаем настоящие настройки

from jarvis import steam, tts  # noqa: E402
from jarvis.core import Core  # noqa: E402

core = Core()
events = []
core.listeners.append(events.append)

cases = {
    "замути бимку": "Бимка",
    "Джарвис, запусти BeamNG": "Бимка",
    "врубай игровой режим": "Игровой режим",
    "протокол вечеринка": "Протокол «Вечеринка»",
    "спокойной ночи": "Спокойной ночи",
    "какая погода в киеве": None,
    "кстати как дела": None,
}
for phrase, expected in cases.items():
    got = core.protocols.match(phrase)
    got = got["name"] if got else None
    assert got == expected, f"{phrase!r}: ждали {expected}, получили {got}"
print("протоколы: ок")

assert steam.find_game("бимку")["appid"] == 284160
assert steam.find_game("кс")["appid"] == 730
assert steam.find_game("кстати") is None or steam.find_game("кстати")["appid"] != 730
print("игры: ок")

# Слово-активатор
core.cmd_q.put = lambda c: events.append({"type": "cmd", "c": c})
core.say = lambda *a, **k: events.append({"type": "say"})
core.on_heard("Джарвис, замути бимку")
assert events[-1] == {"type": "cmd", "c": "замути бимку"}, events[-1]
core.on_heard("просто разговор на фоне")
assert events[-1].get("c") != "просто разговор на фоне"
print("слово-активатор: ок")

# Быстрые команды без нейросети
from jarvis import steam as _steam, system as _system  # noqa: E402
_steam._cache = [{"appid": 322170, "name": "Geometry Dash"}, {"appid": 284160, "name": "BeamNG.drive"},
                 {"appid": 730, "name": "Counter-Strike 2"}, {"appid": 252490, "name": "Rust"}]
launched, said = [], []
_steam.launch = launched.append
core.say = lambda text, *a, **k: said.append(text)
_system.set_volume = lambda v: launched.append(("vol", v))
for phrase, appid in [("запусти geometry dash", 322170), ("запусти геометри дэш", 322170),
                      ("врубай геометрию даш", 322170), ("открой кс", 730), ("запусти раст пожалуйста", 252490),
                      ("запусти counter strike", 730)]:
    launched.clear()
    assert core.fast_command(phrase), phrase
    assert launched == [appid], (phrase, launched)
for phrase in ["включи музыку", "кстати как дела", "запусти ракету в космос", "какая погода в киеве",
               "открой мне что-нибудь интересное"]:
    launched.clear()
    ok = core.fast_command(phrase)
    assert not launched, (phrase, launched)
assert core.fast_command("громкость 40") and ("vol", 40) in launched
assert core.fast_command("который час")
print("быстрые команды: ок")

# Похожее на «Джарвис» слово тоже будит
events.clear()
core.cmd_q.put = lambda c: events.append({"type": "cmd", "c": c})
core.on_heard("Джарвиз, открой блокнот")
assert events and events[-1].get("c") == "открой блокнот", events
print("нечёткое слово-активатор: ок")

# Режим «Авто»: переключение между нейросетями
from types import SimpleNamespace as NS  # noqa: E402
from jarvis.router import Router, classify, prepare  # noqa: E402


class Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.status_code = code


calls_log = []


def fake_client(prov):
    def create(model, messages, **kw):
        calls_log.append((prov, model))
        assert all("_prov" not in m for m in messages), "служебное поле ушло в нейросеть"
        if prov == "gemini":
            raise Err(429, "Rate limit reached. Please try again in 20s")
        if prov == "groq" and model == "m1":
            raise Err(503, "This model is currently experiencing high demand")
        if prov == "cerebras":
            raise Err(401, "Invalid API key")
        return NS(choices=[NS(message=NS(content="Ок", tool_calls=None))])
    return NS(chat=NS(completions=NS(create=create)))


r = Router(core)
r.slots = lambda: [("gemini", "g1"), ("cerebras", "c1"), ("cerebras", "c2"), ("groq", "m1"), ("groq", "m2"),
                   ("ollama", "q")]
r.models = lambda prov, key: {"cerebras": ["c1", "c2"]}.get(prov, [])
r.client = fake_client
resp, used = r.complete("sys", [{"role": "user", "content": "привет"}], [])
assert used == "groq" and calls_log[-1] == ("groq", "m2"), calls_log
calls_log.clear()
resp, used = r.complete("sys", [{"role": "user", "content": "ещё"}], [])
assert calls_log == [("groq", "m2")], calls_log  # отдыхающие и мёртвые слоты пропущены
assert classify(Err(429, "try again in 7s"))[0] == "busy" and classify(Err(401, "bad key"))[0] == "auth"
hist = [{"role": "user", "content": "погода"},
        {"role": "assistant", "content": "", "_prov": "gemini", "tool_calls": [
            {"id": "a1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"},
             "extra_content": {"google": {"thought_signature": "x"}}}]},
        {"role": "tool", "tool_call_id": "a1", "content": "+5"}]
flat = prepare(hist, "groq")
assert flat[1]["role"] == "assistant" and "get_weather" in flat[1]["content"] and flat[2]["role"] == "user", flat
same = prepare(hist, "gemini")
assert same[1]["tool_calls"][0]["extra_content"] and "_prov" not in same[1]
print("режим «Авто»: ок")

# Джарвис не может сам выключить звук
core.current_command = "расскажи анекдот"
assert core.tools.call("mute", {"state": True}).startswith("Отказано")
assert core.tools.call("set_volume", {"level": 0}).startswith("Отказано")
assert core.tools.call("change_volume", {"delta": -50}).startswith("Отказано")
print("защита звука: ок")

assert len(core.tools.specs) > 20
print("инструменты:", len(core.tools.specs))
print(core.tools.call("get_time", {}))

if "--no-net" not in sys.argv:
    # Голос Microsoft должен работать сам, без запасного
    mp3 = tts.edge("Добрый вечер, сэр.", core.settings["tts"])
    assert len(mp3) > 2000, "голос Microsoft вернул пустой звук"
    x, sr = tts.decode(mp3)
    # «Проблемная» для Дмитрия фраза всё равно озвучивается (запасным голосом Microsoft)
    assert len(tts.edge("Проверка номер 3, сэр.", core.settings["tts"])) > 2000
    # Английский голос + русский текст не должен ломать озвучку
    mp3 = tts.edge("Запускаю, сэр.", {"voice": "en-GB-RyanNeural", "rate": "+8%", "pitch": "-4Hz"})
    assert len(mp3) > 2000
    for v in ("en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural", "de-DE-FlorianMultilingualNeural"):
        assert len(tts.edge("Добрый вечер, сэр.", {"voice": v})) > 2000, v
    x = tts.jarvis_fx(x, sr)
    assert len(x) / sr > 0.5
    print(f"голос: ок ({len(x) / sr:.1f} с)")
if sys.platform == "win32":
    print("голоса Windows:", tts.onecore_voices())
    x, sr = tts.decode(tts.onecore_voice("Good evening, sir."))
    assert len(x) / sr > 0.5
    print("современный голос Windows: ок")
    x, sr = tts.decode(tts.windows_voice("Good evening, sir."))
    assert len(x) / sr > 0.5
    print("запасной голос Windows: ок")
print("ВСЁ ОК")
