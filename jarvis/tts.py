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

    com = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    buf = bytearray()
    stream = com.stream().__aiter__()
    while True:
        # Ждём каждый кусочек звука не дольше 8 секунд: длинная фраза — не повод сдаваться,
        # а вот если сервис замолчал — пробуем заново.
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=8)
        except StopAsyncIteration:
            break
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


def synthesize(text: str, cfg: dict) -> bytes:
    """Возвращает mp3."""
    text = clean_for_speech(text)
    if not text:
        return b""
    if cfg.get("provider") == "elevenlabs" and cfg.get("elevenlabs_key") and cfg.get("elevenlabs_voice"):
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['elevenlabs_voice']}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": cfg["elevenlabs_key"]},
            json={"text": text, "model_id": "eleven_flash_v2_5"}, timeout=30)
        r.raise_for_status()
        return r.content
    voice, rate, pitch = cfg.get("voice", "ru-RU-DmitryNeural"), cfg.get("rate", "+0%"), cfg.get("pitch", "+0Hz")
    return asyncio.run(_edge(text, voice, rate, pitch))


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


_edge_down_until = 0.0


def speak_audio(text: str, cfg: dict, log=print):
    """Готовит звук фразы. Если основной голос не отвечает — пробует ещё раз, потом голос Windows."""
    global _edge_down_until
    import time

    errors = []
    attempts = 0 if time.time() < _edge_down_until and cfg.get("provider") != "elevenlabs" else 2
    for attempt in range(attempts):
        try:
            audio = synthesize(text, cfg)
            if audio:
                return decode(audio)
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
    if errors:
        _edge_down_until = time.time() + 60  # минуту не мучаем сервис, потом пробуем снова
        log("Основной голос не ответил (" + "; ".join(errors)[:300] + ") — говорю голосом Windows")
    return decode(windows_voice(clean_for_speech(text)))


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
