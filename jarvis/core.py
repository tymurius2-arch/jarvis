"""Ядро: слышит фразу -> протокол или нейросеть -> действие -> ответ голосом."""
import queue
import random
import re
import threading
import time
import traceback

from . import system, tts
from .brain import Brain
from .config import Settings
from .listener import Listener
from .protocols import Protocols, run_action
from .textutil import normalize
from .tools import Toolbox

ACK = ["Да, {t}?", "Слушаю, {t}.", "К вашим услугам, {t}.", "Я здесь, {t}.", "Да?"]
YES = ("да", "подтверждаю", "подтвердить", "давай", "конечно", "ага", "так точно", "выполняй", "yes", "так")
NO = ("нет", "отмена", "отменить", "не надо", "стоп", "no", "ні")


class Core:
    def __init__(self):
        self.settings = Settings()
        self.protocols = Protocols()
        self.tools = Toolbox(self)
        self.brain = Brain(self)
        self.listener = Listener(self)
        self.listeners: list = []  # функции, которые получают события (интерфейс)
        self.active_until = 0.0
        self.pending = None  # (описание, функция, срок)
        self.cmd_q: queue.Queue = queue.Queue()
        self.speech_lock = threading.Lock()
        self.history: list[dict] = []  # для интерфейса
        self.current_command = ""  # последняя фраза пользователя (для защиты звука)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------- события для интерфейса ----------
    def emit(self, event: dict):
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:
                pass

    def log(self, text: str, kind: str = "info"):
        try:
            print(f"[{kind}] {text}")
        except Exception:
            pass  # в .exe без консоли или в консоли без UTF-8
        self.emit({"type": "log", "kind": kind, "text": text, "time": time.strftime("%H:%M:%S")})

    def chat(self, role: str, text: str):
        item = {"role": role, "text": text, "time": time.strftime("%H:%M")}
        self.history.append(item)
        self.history = self.history[-200:]
        self.emit({"type": "chat", **item})

    # ---------- голос ----------
    def say(self, text: str, wait: bool = True):
        text = (text or "").strip()
        if not text:
            return
        self.chat("jarvis", text)
        self.emit({"type": "state", "state": "voicing"})
        cfg = self.settings["tts"]
        with self.speech_lock:
            try:
                x, sr = tts.speak_audio(text, cfg, log=lambda m: self.log(m, "error"))
                if cfg.get("fx", True):
                    x = tts.jarvis_fx(x, sr)
            except Exception as e:
                self.log(f"Голос недоступен: {e}", "error")
                self.emit({"type": "toast", "text": "🔇 Голос недоступен — подробности в «Журнале»"})
                self.speech_done()
                return
            secs = len(x) / sr
            self.listener.mute_for(secs + 0.7)  # чтобы Джарвис не слышал сам себя
            self.emit({"type": "speak", "text": text, "duration": secs, "envelope": tts.envelope(x, sr)})
            try:
                tts.play(x, sr, cfg.get("volume", 100))
            except Exception as e:
                self.log(f"Не удалось воспроизвести звук: {e}", "error")
            time.sleep(secs + 0.15)  # следующая фраза — после этой
        self.speech_done()

    def speech_done(self):
        self.listener.unmute()
        # после ответа можно продолжить разговор без слова «Джарвис»
        self.active_until = max(self.active_until, time.time() + self.settings["follow_up_seconds"])
        self.emit({"type": "state", "state": "idle"})

    # ---------- входящая речь/текст ----------
    def on_heard(self, text: str, forced: bool = False):
        norm = normalize(text)
        wake = None
        for w in sorted(self.settings["wake_words"], key=len, reverse=True):
            m = re.search(rf"(^|\s){re.escape(normalize(w))}\w*", norm)
            if m:
                wake = m
                break
        if wake:
            command = norm[wake.end():].strip()
            before = norm[:wake.start()].strip()
            if not command and len(before.split()) >= 2:
                command = before  # «запусти бимку, Джарвис»
        elif forced or time.time() < self.active_until or not self.settings["require_wake_word"] or self.pending:
            command = norm
        else:
            self.log(f"(фон) {text}", "muted")
            self.emit({"type": "state", "state": "idle"})
            return
        self.chat("user", text)
        if wake and not command:
            self.active_until = time.time() + self.settings["follow_up_seconds"]
            self.say(random.choice(ACK).format(t=self.settings["user_title"]))
            return
        self.cmd_q.put(command)

    def submit_text(self, text: str):
        """Текст из окна программы — без слова-активатора."""
        text = text.strip()
        if text:
            self.chat("user", text)
            self.cmd_q.put(normalize(text) if self.protocols.match(text) else text)

    def _worker(self):
        while True:
            command = self.cmd_q.get()
            try:
                self.handle_command(command)
            except Exception as e:
                traceback.print_exc()
                self.log(f"Ошибка: {e}", "error")
                self.say(f"Простите, {self.settings['user_title']}, произошла ошибка.")
            finally:
                self.emit({"type": "state", "state": "idle"})

    def handle_command(self, command: str):
        self.current_command = command
        norm = normalize(command)
        # 1. Ждём подтверждения?
        if self.pending:
            desc, fn, deadline = self.pending
            if time.time() < deadline:
                if any(re.search(rf"(^|\s){w}(\s|$)", norm) for w in YES):
                    self.pending = None
                    self.say("Выполняю.", wait=True)
                    fn()
                    return
                if any(re.search(rf"(^|\s){w}(\s|$)", norm) for w in NO):
                    self.pending = None
                    self.say(f"Отменено, {self.settings['user_title']}.")
                    return
            self.pending = None
        # 2. Протокол?
        proto = self.protocols.match(norm)
        if proto:
            self.run_protocol(proto)
            return
        # 3. Нейросеть
        self.handle_ai(command)

    def handle_ai(self, command: str):
        if not self.brain.ready():
            if not self._offline(command):
                self.say(f"{self.settings['user_title'].capitalize()}, подключите нейросеть в настройках — "
                         f"нужен бесплатный ключ Groq. Протоколы работают и без неё.")
            return
        self.emit({"type": "state", "state": "thinking"})
        t0 = time.time()
        try:
            answer = self.brain.ask(command)
        except Exception as e:
            self.brain.reset()
            msg = str(e)
            self.log(f"Нейросеть: {msg}", "error")
            if "401" in msg or "invalid" in msg.lower() and "key" in msg.lower():
                self.say("Ключ нейросети не подходит, сэр. Проверьте его в настройках.")
            elif "429" in msg:
                self.say("Нейросеть просит передышку — слишком много запросов. Попробуйте через минуту.")
            elif not self._offline(command):
                self.say("Не могу связаться с нейросетью, сэр. Проверьте интернет.")
            return
        self.log(f"Ответ за {time.time() - t0:.1f} с", "info")
        if self.pending and self.pending[2] > t0:
            return  # Джарвис уже спросил подтверждение голосом
        self.say(answer or f"Готово, {self.settings['user_title']}.")

    def run_protocol(self, proto: dict, from_ai: bool = False):
        if proto.get("confirm") and not from_ai:
            self.ask_confirm(proto["name"], lambda: self._run_actions(proto))
            return
        self._run_actions(proto)

    def _run_actions(self, proto):
        self.emit({"type": "protocol", "name": proto["name"], "icon": proto.get("icon", "⚡")})
        self.log(f"Протокол «{proto['name']}»", "protocol")
        for action in proto.get("actions", []):
            try:
                run_action(self, action)
            except Exception as e:
                self.log(f"Действие {action.get('type')} не удалось: {e}", "error")
        if not any(a.get("type") == "say" for a in proto.get("actions", [])):
            self.say(proto.get("reply") or f"Протокол «{proto['name']}» выполнен.")

    def ask_confirm(self, desc: str, fn):
        self.pending = (desc, fn, time.time() + 20)
        self.active_until = time.time() + 20
        self.say(f"{desc}. Подтверждаете, {self.settings['user_title']}?")
        self.emit({"type": "confirm", "text": desc})

    def confirm(self, yes: bool):
        self.cmd_q.put("да" if yes else "нет")

    # ---------- простые команды без нейросети ----------
    def _offline(self, command: str) -> bool:
        c = normalize(command)
        t = self.settings["user_title"]
        m = re.match(r"(?:открой|запусти|включи|замути|врубай|вруби|запускай)\s+(.+)", c)
        if m:
            name = m.group(1)
            res = self.tools.call("open_app", {"name": name})
            if res.startswith("Не нашёл"):
                res = self.tools.call("launch_steam_game", {"name": name})
                if "не найдена" in res:
                    self.say(f"Не нашёл «{name}», {t}.")
                    return True
            self.say(f"Запускаю, {t}.")
            return True
        m = re.match(r"(?:закрой|выключи|вырубай|выруби)\s+(.+)", c)
        if m and not re.search(r"компьютер|звук", c):
            self.say(self.tools.call("close_app", {"name": m.group(1)}))
            return True
        m = re.search(r"громкость\D*(\d+)", c)
        if m:
            system.set_volume(int(m.group(1)))
            self.say(f"Громкость {m.group(1)} процентов.")
            return True
        if re.search(r"(выключи|убери) звук|без звука", c):
            system.mute(True)
            return True
        if re.search(r"включи звук", c):
            system.mute(False)
            self.say("Звук включён.")
            return True
        if re.search(r"(сколько|который) (сейчас )?(времени|час)", c):
            self.say(f"Сейчас {system.now().split(',')[0]}, {t}.")
            return True
        if re.search(r"пауз|стоп музык|продолжи", c):
            system.media("play_pause")
            return True
        if re.search(r"следующ", c):
            system.media("next")
            return True
        if re.search(r"сверни вс", c):
            system.hotkey("win+d")
            return True
        if re.search(r"скриншот", c):
            system.screenshot()
            self.say("Скриншот сохранён.")
            return True
        m = re.search(r"(?:найди|загугли|поищи)\s+(.+)", c)
        if m:
            system.web_search(m.group(1))
            self.say("Открываю поиск.")
            return True
        return False

    # ---------- запуск ----------
    def start(self):
        self.listener.start()
        greet = time.localtime().tm_hour
        part = "Доброе утро" if 5 <= greet < 12 else "Добрый день" if greet < 18 else "Добрый вечер" if greet < 23 else "Доброй ночи"
        threading.Timer(1.5, lambda: self.say(f"{part}, {self.settings['user_title']}. Все системы в норме.")).start()
