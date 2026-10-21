"""Ядро: слышит фразу -> протокол или нейросеть -> действие -> ответ голосом."""
import difflib
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
        self.current_command = ""
        self._notices: dict = {}  # последняя фраза пользователя (для защиты звука)
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

    def notice(self, text: str, every: float = 0):
        """Служебное сообщение прямо в чате (лимиты, ошибки), не чаще чем раз в every секунд."""
        now = time.time()
        if every and now - self._notices.get(text[:40], 0) < every:
            return
        self._notices[text[:40]] = now
        self.chat("sys", text)

    # ---------- голос ----------
    def say(self, text: str, wait: bool = True):
        text = (text or "").strip()
        if not text:
            return
        self.chat("jarvis", text)
        self.emit({"type": "state", "state": "voicing"})
        cfg = self.settings["tts"]
        def log(m):
            self.log(m, "error")
            if "голосом Windows" in m:
                self.notice("🔈 Основной голос не работает, говорю запасным. Причина: " + tts.last_error[:180], every=120)
        with self.speech_lock:
            chunks = tts.split_sentences(text)
            ready: queue.Queue = queue.Queue()

            def produce():
                for part in chunks:
                    try:
                        x, sr = tts.speak_audio(part, cfg, log=log)
                        if cfg.get("fx", True):
                            x = tts.jarvis_fx(x, sr)
                        ready.put((part, x, sr))
                    except Exception as e:
                        ready.put((part, e, None))
                ready.put(None)

            threading.Thread(target=produce, daemon=True).start()
            while True:
                item = ready.get()
                if item is None:
                    break
                part, x, sr = item
                if sr is None:
                    self.log(f"Голос недоступен: {x}", "error")
                    self.emit({"type": "toast", "text": "🔇 Голос недоступен — подробности в «Журнале»"})
                    continue
                secs = len(x) / sr
                self.listener.mute_for(secs + 0.7)  # чтобы Джарвис не слышал сам себя
                self.emit({"type": "speak", "text": part, "duration": secs, "envelope": tts.envelope(x, sr)})
                try:
                    tts.play(x, sr, cfg.get("volume", 100))
                except Exception as e:
                    self.log(f"Не удалось воспроизвести звук: {e}", "error")
                time.sleep(secs + 0.08)
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
        if not wake:
            # Похожие слова: «джарвиз», «чарвис», «jarvis»… в первых двух словах
            words = norm.split()
            for wd in words[:2]:
                if len(wd) >= 5 and any(difflib.SequenceMatcher(None, wd, normalize(w)).ratio() >= 0.78
                                        for w in self.settings["wake_words"]):
                    wake = re.search(rf"(^|\s){re.escape(wd)}", norm)
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
            if len(norm.split()) >= 2:
                self.notice(f"🙉 Услышал «{text[:60]}», но без слова «Джарвис» — пропускаю", every=15)
            self.emit({"type": "state", "state": "idle"})
            return
        self.chat("user", text)
        if wake and not command:
            self.active_until = time.time() + self.settings["follow_up_seconds"]
            # Отвечаем в отдельном потоке: микрофон продолжает слушать
            threading.Thread(target=self.say, args=(random.choice(ACK).format(t=self.settings["user_title"]),),
                             daemon=True).start()
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
        # 3. Простые команды — мгновенно, без нейросети
        t0 = time.time()
        if self.fast_command(command):
            self.log(f"Быстрая команда за {time.time() - t0:.2f} с", "info")
            return
        # 4. Нейросеть
        self.handle_ai(command)

    LAUNCH_RE = re.compile(r"^(?:ну\s+|а\s+)?(запусти|запускай|открой|открывай|замути|замутим|врубай|вруби|включи|"
                           r"поставь|давай|го|launch|open|start)\s+(?:мне\s+|нам\s+|пожалуйста\s+|игру\s+|"
                           r"программу\s+|приложение\s+|игрушку\s+)*(.+?)(?:\s+пожалуйста)?$")

    def fast_command(self, command: str) -> bool:
        """Частые команды без нейросети: запуск игр/программ, громкость, музыка, время."""
        from . import apps, steam

        c = normalize(command)
        t = self.settings["user_title"]
        m = self.LAUNCH_RE.match(c)
        if m:
            verb, name = m.group(1), m.group(2).strip()
            game = steam.find_installed(name)
            if game:
                steam.launch(game["appid"])
                self.say(f"Запускаю {game['name']}, {t}.")
                return True
            if verb not in ("включи", "поставь", "давай", "го"):  # «включи музыку» — это не программа
                target = self.tools.apps.find_strict(name)
                if target:
                    apps.start(target)
                    self.say(f"Открываю, {t}.")
                    return True
            return False
        m = re.search(r"громкость\D{0,12}(\d{1,3})", c)
        if m:
            system.set_volume(int(m.group(1)))
            self.say(f"Громкость {m.group(1)} процентов.")
            return True
        if re.fullmatch(r"(сделай\s+)?(по)?громче( пожалуйста)?", c):
            system.change_volume(+15)
            return True
        if re.fullmatch(r"(сделай\s+)?(по)?тише( пожалуйста)?", c):
            system.change_volume(-15)
            return True
        if re.fullmatch(r"(выключи|убери|отключи) звук|без звука|заглуши( звук)?", c):
            system.mute(True)
            return True
        if re.fullmatch(r"(включи|верни) звук", c):
            system.mute(False)
            self.say("Звук включён.")
            return True
        if re.fullmatch(r"(поставь на )?пауз[ау]|стоп|продолжи|продолжай|играй", c):
            system.media("play_pause")
            return True
        if re.fullmatch(r"(включи |давай )?(следующ\w+|некст)( трек| песн\w+| видео)?", c):
            system.media("next")
            return True
        if re.fullmatch(r"(включи |давай )?(предыдущ\w+)( трек| песн\w+| видео)?", c):
            system.media("previous")
            return True
        if re.search(r"(сколько|который)\s+(сейчас\s+)?(времени|час)", c):
            self.say(f"Сейчас {system.now().split(',')[0]}, {t}.")
            return True
        if re.search(r"(какое|какой)\s+сегодня\s+(число|день|дата)|какая сегодня дата", c):
            parts = system.now().split(", ")
            self.say(f"Сегодня {parts[2]}, {parts[1]}.")
            return True
        return False

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
            self.notice("⚠ Нейросеть: " + msg[:160])
            if "401" in msg or "invalid" in msg.lower() and "key" in msg.lower():
                self.say("Ключ нейросети не подходит, сэр. Проверьте его в настройках.")
            elif "429" in msg or "rate limit" in msg.lower():
                m = re.search(r"try again in ([\d.]+)(m?s)", msg)
                wait = ""
                if m:
                    secs = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
                    wait = f" Подождите {max(1, round(secs))} секунд."
                self.notice("⏳ Бесплатный лимит Groq исчерпан на минуту." + wait)
                self.say("Нейросеть просит передышку, сэр." + wait)
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
        if self.settings["llm"]["provider"] == "ollama":
            self.notice("🐢 Нейросеть Ollama работает на вашем компьютере — она медленнее и глупее облачных. "
                        "Для скорости выберите в настройках Google Gemini или OpenRouter (gemini-3.8-flash).")
        greet = time.localtime().tm_hour
        part = "Доброе утро" if 5 <= greet < 12 else "Добрый день" if greet < 18 else "Добрый вечер" if greet < 23 else "Доброй ночи"
        threading.Timer(1.5, lambda: self.say(f"{part}, {self.settings['user_title']}. Все системы в норме.")).start()
