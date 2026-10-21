"""Диагностика голоса Microsoft: python tests/voice_diag.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis import tts  # noqa: E402


def attempt(label, text, cfg, pause=0.0):
    time.sleep(pause)
    t = time.time()
    try:
        n = len(tts.edge(text, cfg))
        print(f"OK   {label}: {n} байт за {time.time() - t:.1f} с")
    except Exception as e:
        print(f"FAIL {label}: {type(e).__name__} за {time.time() - t:.1f} с")


base = {"voice": "ru-RU-DmitryNeural", "rate": "+8%", "pitch": "-4Hz"}
plain = {"voice": "ru-RU-DmitryNeural", "rate": "+0%", "pitch": "+0Hz"}
for i in range(5):
    attempt(f"подряд #{i}", "Запускаю, сэр.", base)
for i in range(3):
    attempt(f"пауза 2с #{i}", "Запускаю, сэр.", base, pause=2)
for i in range(3):
    attempt(f"без высоты/скорости #{i}", "Запускаю, сэр.", plain)
for txt in ["Да?", "Готово.", "Добрый вечер, сэр. Все системы в норме.", "Сэр, я готов помочь. Чем могу служить?"]:
    attempt(f"текст «{txt}»", txt, base)
