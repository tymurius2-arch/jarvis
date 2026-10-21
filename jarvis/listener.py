"""Микрофон: ловит фразы (по громкости) и превращает их в текст."""
import io
import queue
import threading
import time
import wave

import numpy as np
import requests

RATE = 16000
BLOCK = 480  # 30 мс

# Whisper иногда «слышит» это в тишине
HALLUCINATIONS = ("субтитры", "продолжение следует", "спасибо за просмотр", "редактор субтитров",
                  "thanks for watching", "подписывайтесь", "dimatorzok", "amara.org")


def list_devices():
    try:
        import sounddevice as sd

        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["hostapi"] == 0:
                out.append({"id": i, "name": d["name"]})
        return out
    except Exception:
        return []


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class Listener:
    def __init__(self, core):
        self.core = core
        self.audio_q: queue.Queue = queue.Queue(maxsize=400)
        self.utter_q: queue.Queue = queue.Queue(maxsize=8)
        self.mute_until = 0.0
        self.force_command = False  # кнопка «Говорить» — без слова-активатора
        self.running = False
        self.gen = 0
        self.stream = None
        self.level = 0.0
        self.error = ""

    # ---------- запуск ----------
    def start(self):
        if self.running:
            return
        self.running = True
        self.gen += 1
        threading.Thread(target=self._capture_loop, args=(self.gen,), daemon=True).start()
        threading.Thread(target=self._recognize_loop, args=(self.gen,), daemon=True).start()

    def alive(self, gen):
        return self.running and gen == self.gen

    def restart(self):
        """Переоткрыть микрофон (например, после смены устройства)."""
        self.running = False
        time.sleep(0.2)
        self.start()

    def mute_for(self, seconds: float):
        self.mute_until = max(self.mute_until, time.time() + seconds)

    def unmute(self):
        self.mute_until = time.time() + 0.25

    def _open_stream(self):
        import sounddevice as sd

        dev = self.core.settings["stt"].get("device")

        def cb(indata, frames, t, status):
            try:
                self.audio_q.put_nowait(bytes(indata))
            except queue.Full:
                pass

        self.stream = sd.RawInputStream(samplerate=RATE, blocksize=BLOCK, channels=1, dtype="int16",
                                        device=dev if dev not in (None, "", -1) else None, callback=cb)
        self.stream.start()

    def _capture_loop(self, gen):
        while self.alive(gen):
            try:
                self._open_stream()
                self.error = ""
                self.core.emit({"type": "mic", "ok": True})
                self._vad_loop(gen)
            except Exception as e:
                self.error = str(e)
                self.core.emit({"type": "mic", "ok": False, "error": str(e)})
                self.core.log(f"Микрофон недоступен: {e}", "error")
                time.sleep(3)
            finally:
                try:
                    self.stream.stop()
                    self.stream.close()
                except Exception:
                    pass

    # ---------- поиск фраз по громкости ----------
    def _vad_loop(self, gen):
        noise = 300.0
        preroll: list[bytes] = []
        voiced: list[bytes] = []
        speaking = False
        loud = silent = 0
        last_level_emit = 0.0
        while self.alive(gen):
            try:
                block = self.audio_q.get(timeout=1)
            except queue.Empty:
                continue
            samples = np.frombuffer(block, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples)) + 1e-6)
            sens = int(self.core.settings["stt"].get("sensitivity", 60))
            ratio = 4.0 - sens / 100 * 2.6  # чувствительность 0..100 -> порог 4.0..1.4 × шум
            threshold = max(180.0, noise * ratio)
            now = time.time()
            self.level = min(1.0, rms / 6000)
            if now - last_level_emit > 0.1:
                self.core.emit({"type": "level", "value": round(self.level, 3)})
                last_level_emit = now

            if now < self.mute_until or not self.core.settings["listening"] and not self.force_command:
                preroll.clear()
                voiced.clear()
                speaking = False
                loud = 0
                continue

            if not speaking:
                noise = noise * 0.98 + min(rms, noise * 3) * 0.02  # медленно подстраиваемся под фон
                preroll.append(block)
                preroll = preroll[-12:]
                loud = loud + 1 if rms > threshold else 0
                if loud >= 3:
                    speaking = True
                    voiced = list(preroll)
                    silent = 0
                    self.core.emit({"type": "state", "state": "hearing"})
            else:
                voiced.append(block)
                silent = silent + 1 if rms < threshold * 0.8 else 0
                too_long = len(voiced) * BLOCK / RATE > 14
                if silent >= 27 or too_long:  # ~0.8 c тишины
                    speaking = False
                    pcm = b"".join(voiced[:-20] if not too_long else voiced)
                    voiced = []
                    preroll = []
                    dur = len(pcm) / 2 / RATE
                    if dur >= 0.45:
                        try:
                            self.utter_q.put_nowait(pcm)
                        except queue.Full:
                            pass
                    else:
                        self.core.emit({"type": "state", "state": "idle"})

    # ---------- распознавание ----------
    def _recognize_loop(self, gen):
        while self.alive(gen):
            try:
                pcm = self.utter_q.get(timeout=1)
            except queue.Empty:
                continue
            forced = self.force_command
            self.force_command = False
            self.core.emit({"type": "state", "state": "recognizing"})
            try:
                text = self.recognize(pcm)
            except Exception as e:
                self.core.log(f"Ошибка распознавания: {e}", "error")
                text = ""
            text = (text or "").strip()
            if not text or any(h in text.lower() for h in HALLUCINATIONS):
                self.core.emit({"type": "state", "state": "idle"})
                continue
            self.core.on_heard(text, forced=forced)

    def recognize(self, pcm: bytes) -> str:
        cfg = self.core.settings["stt"]
        provider = cfg.get("provider", "auto")
        lang = self.core.settings["language"]
        key = self.core.settings.groq_key()
        if provider in ("auto", "groq") and key:
            try:
                return self._groq(pcm, key, lang)
            except Exception as e:
                if provider == "groq":
                    raise
                self.core.log(f"Whisper недоступен ({e}), пробую Google", "error")
        return self._google(pcm, lang)

    def _groq(self, pcm, key, lang) -> str:
        r = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("speech.wav", to_wav(pcm), "audio/wav")},
            data={"model": "whisper-large-v3-turbo", "language": lang, "temperature": "0",
                  "prompt": "Джарвис, протокол, запусти, Стим, бимка, BeamNG, YouTube, громкость."},
            timeout=20)
        r.raise_for_status()
        return r.json().get("text", "")

    def _google(self, pcm, lang) -> str:
        import speech_recognition as sr

        rec = sr.Recognizer()
        code = {"ru": "ru-RU", "uk": "uk-UA", "en": "en-US"}.get(lang, lang)
        try:
            return rec.recognize_google(sr.AudioData(pcm, RATE, 2), language=code)
        except sr.UnknownValueError:
            return ""
