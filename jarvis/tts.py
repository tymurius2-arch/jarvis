"""Голос Джарвиса. По умолчанию — бесплатные нейроголоса Microsoft (edge-tts)."""
import asyncio
import re

import requests


def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "ссылка", text)
    text = re.sub(r"[*_#`>|\[\]]", "", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


async def _edge(text, voice, rate, pitch) -> bytes:
    import edge_tts

    # receive_timeout: сдаёмся, только если сервис замолчал на 10 секунд (длинная фраза — не проблема)
    com = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch, connect_timeout=8, receive_timeout=10)
    buf = bytearray()
    async for chunk in com.stream():
        if chunk.get("type") == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def split_sentences(text: str, limit: int = 220) -> list[str]:
    """Режем ответ на куски по предложениям: первый кусок озвучивается, пока готовятся остальные."""
    parts = re.split(r"(?<=[.!?…;:])\s+", text.strip())
    out, cur = [], ""
    for p in parts:
        if not p:
            continue
        if cur and (len(cur) + len(p) > limit or not out):  # первый кусок — покороче, чтобы начать сразу
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def elevenlabs(text: str, cfg: dict) -> bytes:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['elevenlabs_voice']}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": cfg["elevenlabs_key"]},
        json={"text": text, "model_id": "eleven_flash_v2_5"}, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", {})
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"ElevenLabs {r.status_code}: {msg}")
    return r.content


def edge(text: str, cfg: dict) -> bytes:
    voice = cfg.get("voice") or "ru-RU-DmitryNeural"
    rate, pitch = str(cfg.get("rate", "+0%")).strip(), str(cfg.get("pitch", "+0Hz")).strip()
    # Неверная скорость/высота — частая причина «No audio received»
    if not re.fullmatch(r"[+-]\d{1,3}%", rate):
        rate = "+0%"
    if not re.fullmatch(r"[+-]\d{1,3}Hz", pitch):
        pitch = "+0Hz"
    # Английский голос не умеет читать русский текст — тогда говорит Дмитрий
    if re.search(r"[а-яёіїєґ]", text, re.I) and not re.match(r"(ru|uk)-", voice) and "Multilingual" not in voice:
        voice = "uk-UA-OstapNeural" if voice.startswith("uk") else "ru-RU-DmitryNeural"
    return asyncio.run(_edge(text, voice, rate, pitch))


def synthesize(text: str, cfg: dict) -> bytes:
    """Основной голос (mp3): ElevenLabs, если включён, иначе голос Microsoft."""
    text = clean_for_speech(text)
    if not text:
        return b""
    if use_elevenlabs(cfg):
        return elevenlabs(text, cfg)
    return edge(text, cfg)


def use_elevenlabs(cfg: dict) -> bool:
    return cfg.get("provider") == "elevenlabs" and bool(cfg.get("elevenlabs_key")) and bool(cfg.get("elevenlabs_voice"))


def windows_voice(text: str) -> bytes:
    """Запасной голос — встроенный в Windows (работает без интернета). Возвращает WAV."""
    import os
    import subprocess
    import tempfile

    path = os.path.join(tempfile.gettempdir(), "jarvis_voice.wav")
    safe = text.replace("'", "''")
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$all = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'ru*' }; "
              "$v = $all | Where-Object { $_.VoiceInfo.Gender -eq 'Male' } | Select-Object -First 1; "
              "if (-not $v) { $v = $all | Select-Object -First 1 }; "
              "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
              f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{safe}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], timeout=30, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with open(path, "rb") as f:
        return f.read()


_down_until: dict = {}  # голос -> до какого времени не пробовать (после сбоя)
last_error = ""


def speak_audio(text: str, cfg: dict, log=print):
    """Готовит звук фразы по цепочке: ElevenLabs (если включён) → голос Microsoft → голос Windows."""
    global last_error
    import time

    text = clean_for_speech(text)
    chain = []
    if use_elevenlabs(cfg):
        chain.append(("ElevenLabs", elevenlabs))
    chain.append(("Microsoft", edge))
    errors = []
    for name, fn in chain:
        if time.time() < _down_until.get(name, 0):
            continue
        for _attempt in range(2 if name == "Microsoft" else 1):
            try:
                audio = fn(text, cfg)
                if audio:
                    _down_until.pop(name, None)
                    return decode(audio)
            except Exception as e:
                err = f"{type(e).__name__}: {e}".strip()
                errors.append(f"{name} — {err}")
        _down_until[name] = time.time() + 60  # минуту не мучаем этот голос, потом пробуем снова
    if errors:
        last_error = "; ".join(errors)[:300]
        log("Основной голос не ответил (" + last_error + ") — говорю голосом Windows")
    return decode(windows_voice(text))


def decode(mp3: bytes):
    """mp3 -> (моно float32 массив, частота)."""
    import miniaudio
    import numpy as np

    d = miniaudio.decode(mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1)
    return np.frombuffer(d.samples, dtype=np.int16).astype(np.float32) / 32768.0, d.sample_rate


def jarvis_fx(x, sr):
    """Лёгкий «компьютерный» оттенок: убираем бубнение, добавляем тонкое удвоение и отблеск."""
    import numpy as np

    # «предыскажение»: убирает бубнение, делает голос чётче и «цифровее»
    y = x.copy()
    y[1:] -= 0.7 * x[:-1]
    out = y.copy()
    for delay_ms, gain in ((11, 0.22), (23, 0.12), (61, 0.06)):
        d = int(sr * delay_ms / 1000)
        out[d:] += y[:-d] * gain
    peak = float(np.max(np.abs(out)) or 1.0)
    return (out / peak * 0.92).astype(np.float32)


def envelope(x, sr, step=0.05):
    import numpy as np

    n = max(1, int(sr * step))
    frames = len(x) // n
    if not frames:
        return []
    env = np.sqrt(np.mean(x[: frames * n].reshape(frames, n) ** 2, axis=1))
    m = float(env.max() or 1.0)
    return [round(float(v / m), 2) for v in env]


def play(x, sr, volume=100):
    import sounddevice as sd

    import numpy as np

    sd.play(np.clip(x * (max(0, min(150, volume)) / 100), -1, 1), sr, blocking=False)


def stop():
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass
