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

assert len(core.tools.specs) > 20
print("инструменты:", len(core.tools.specs))
print(core.tools.call("get_time", {}))

if "--no-net" not in sys.argv:
    audio = tts.synthesize("Добрый вечер, сэр.", core.settings["tts"])
    x, sr = tts.decode(audio)
    x = tts.jarvis_fx(x, sr)
    assert len(x) / sr > 0.5
    print(f"голос: ок ({len(x) / sr:.1f} с)")
print("ВСЁ ОК")
