"""Инструменты, которыми нейросеть управляет компьютером."""
import json
import re
import threading
import traceback
from pathlib import Path

from . import apps, steam, system


def _fn(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []}}


S = {"type": "string"}
I = {"type": "integer"}

TOOL_SPECS = [
    _fn("open_app", "Запустить программу, приложение или сайт по названию (Telegram, Discord, Chrome, блокнот, настройки...).",
        {"name": S}, ["name"]),
    _fn("close_app", "Закрыть программу или игру (завершить процесс).", {"name": S}, ["name"]),
    _fn("launch_steam_game", "Запустить игру из Steam. Понимает сленг: бимка=BeamNG.drive, кс=CS2, дота, гта и т.д.",
        {"name": {"type": "string", "description": "название игры или appid"}}, ["name"]),
    _fn("list_steam_games", "Список установленных игр Steam."),
    _fn("run_protocol", "Выполнить протокол (сценарий) пользователя по названию.", {"name": S}, ["name"]),
    _fn("list_protocols", "Список протоколов пользователя."),
    _fn("open_url", "Открыть сайт в браузере.", {"url": S}, ["url"]),
    _fn("web_search", "Открыть поиск Google в браузере.", {"query": S}, ["query"]),
    _fn("youtube", "Найти и включить видео/музыку на YouTube.", {"query": S, "play": {"type": "boolean"}}, ["query"]),
    _fn("set_volume", "Установить громкость системы 0-100.", {"level": I}, ["level"]),
    _fn("change_volume", "Изменить громкость на delta (например +10 или -20).", {"delta": I}, ["delta"]),
    _fn("mute", "Выключить/включить звук. state: true=выключить звук, false=включить, не указан=переключить.",
        {"state": {"type": "boolean"}}),
    _fn("media_control", "Управление музыкой/видео.",
        {"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"]),
    _fn("press_keys", "Нажать клавишу или сочетание, например 'ctrl+shift+esc', 'win+d', 'alt+f4', 'f11', 'enter'.",
        {"keys": S}, ["keys"]),
    _fn("type_text", "Напечатать текст в активное окно.", {"text": S}, ["text"]),
    _fn("mouse", "Мышь: move/click/double_click/right_click по координатам, scroll (amount<0 вниз), или position.",
        {"action": {"type": "string", "enum": ["move", "click", "double_click", "right_click", "scroll", "position"]},
         "x": I, "y": I, "amount": I}, ["action"]),
    _fn("window_control", "Окна: list (список), focus/minimize/maximize/close по части заголовка, minimize_all.",
        {"action": {"type": "string", "enum": ["list", "focus", "minimize", "maximize", "close", "minimize_all"]},
         "title": S}, ["action"]),
    _fn("power", "Питание: shutdown, restart, sleep, hibernate, lock, logout, monitor_off, cancel (отменить выключение).",
        {"action": {"type": "string",
                    "enum": ["shutdown", "restart", "sleep", "hibernate", "lock", "logout", "monitor_off", "cancel"]}},
        ["action"]),
    _fn("system_info", "Загрузка процессора, память, диски, батарея, время работы."),
    _fn("get_time", "Текущие время и дата."),
    _fn("get_weather", "Погода и прогноз на 3 дня.", {"city": S}, ["city"]),
    _fn("set_timer", "Таймер/напоминание: через seconds секунд Джарвис скажет message.",
        {"seconds": I, "message": S}, ["seconds", "message"]),
    _fn("screenshot", "Сделать скриншот экрана (сохраняется в Изображения/Jarvis)."),
    _fn("open_folder", "Открыть папку: загрузки, документы, рабочий стол, изображения, музыка, видео или путь.",
        {"path": S}, ["path"]),
    _fn("clipboard", "Прочитать буфер обмена (text не указан) или записать в него text.", {"text": S}),
    _fn("write_note", "Записать заметку в файл Документы/Jarvis заметки.txt.", {"text": S}, ["text"]),
    _fn("run_shell", "Выполнить команду PowerShell и вернуть результат. Для всего, что не покрыто другими инструментами.",
        {"command": S}, ["command"]),
]


class Toolbox:
    def __init__(self, core):
        self.core = core
        self.apps = apps.AppIndex()

    @property
    def specs(self):
        specs = TOOL_SPECS
        if not self.core.settings["allow_shell"]:
            specs = [s for s in specs if s["name"] != "run_shell"]
        return specs

    def call(self, name: str, args: dict | str | None) -> str:
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except Exception:
                args = {}
        args = args or {}
        self.core.log(f"⚙ {name} {json.dumps(args, ensure_ascii=False)}", "tool")
        try:
            fn = getattr(self, "t_" + name, None)
            if not fn:
                return f"Нет такого инструмента: {name}"
            return str(fn(**args))
        except TypeError as e:
            return f"Неверные параметры: {e}"
        except Exception as e:
            traceback.print_exc()
            return f"Ошибка: {e}"

    # ---- Программы и игры ----
    def t_open_app(self, name):
        target = self.apps.find(name)
        if not target:
            game = steam.find_game(name)
            if game and game["appid"] in {g["appid"] for g in steam.installed_games()}:
                steam.launch(game["appid"])
                return f"Запускаю игру {game['name']} через Steam"
            return f"Не нашёл программу «{name}». Можно попробовать run_shell или open_url."
        apps.start(target)
        return f"Запущено: {name}"

    def t_close_app(self, name):
        closed = apps.close_process(name)
        return f"Закрыл: {', '.join(closed)}" if closed else f"Не нашёл запущенную программу «{name}»"

    def t_launch_steam_game(self, name):
        game = steam.find_game(str(name))
        if not game:
            names = ", ".join(g["name"] for g in steam.installed_games()[:60])
            return f"Игра «{name}» не найдена. Установлены: {names or 'Steam не найден'}"
        steam.launch(game["appid"])
        return f"Запускаю {game['name']} (appid {game['appid']}) в Steam"

    def t_list_steam_games(self):
        games = steam.installed_games(refresh=True)
        return ", ".join(f"{g['name']} ({g['appid']})" for g in games) or "Steam или игры не найдены"

    def t_run_protocol(self, name):
        proto = self.core.protocols.find_by_name(name)
        if not proto:
            return "Протокол не найден. Есть: " + ", ".join(p["name"] for p in self.core.protocols.items)
        threading.Thread(target=self.core.run_protocol, args=(proto, True), daemon=True).start()
        return f"Протокол «{proto['name']}» запущен (он сам всё озвучит, просто кратко подтверди)"

    def t_list_protocols(self):
        return "; ".join(f"{p['name']}: фразы {', '.join(p['triggers'])}" for p in self.core.protocols.items)

    # ---- Интернет ----
    def t_open_url(self, url):
        system.open_url(url)
        return "Открыл"

    def t_web_search(self, query):
        system.web_search(query)
        return "Открыл поиск"

    def t_youtube(self, query, play=True):
        return system.youtube(query, play)

    # ---- Звук ----
    SOUND_WORDS = re.compile(r"звук|громк|тише|громче|тихо|потиш|погромч|заглуш|беззвуч|без звука|mute|volume|"
                             r"sound|louder|quieter|гучн|тихіше|голосн", re.I)

    def _sound_allowed(self) -> bool:
        """Менять звук можно только если пользователь сам об этом попросил —
        иначе Джарвис может случайно заглушить собственный голос."""
        return bool(self.SOUND_WORDS.search(self.core.current_command or ""))

    DENY_SOUND = ("Отказано: громкость и звук меняются только по прямой просьбе пользователя. "
                  "Не трогай звук и просто ответь.")

    def t_set_volume(self, level):
        if not self._sound_allowed():
            return self.DENY_SOUND
        level = int(level)
        if level < 5 and not re.search(r"\b0\b|ноль|нул|выключ|mute|без звука", self.core.current_command or "", re.I):
            level = 5  # чтобы голос Джарвиса не пропал совсем
        return f"Громкость {system.set_volume(level)}%"

    def t_change_volume(self, delta):
        if not self._sound_allowed():
            return self.DENY_SOUND
        v = system.change_volume(delta)
        return f"Громкость {v}%" if v >= 0 else "Громкость изменена"

    def t_mute(self, state=None):
        if not self._sound_allowed():
            return self.DENY_SOUND
        return "Звук выключен" if system.mute(state) else "Звук включён"

    def t_media_control(self, action):
        system.media(action)
        return "Готово"

    # ---- Ввод ----
    def t_press_keys(self, keys):
        system.hotkey(keys)
        return f"Нажал {keys}"

    def t_type_text(self, text):
        system.type_text(text)
        return "Напечатал"

    def t_mouse(self, action, x=None, y=None, amount=0):
        return system.mouse(action, x, y, amount)

    def t_window_control(self, action, title=""):
        return system.window_control(action, title)

    # ---- Система ----
    def t_power(self, action):
        if action in ("shutdown", "restart", "logout", "hibernate") and self.core.settings["confirm_power"]:
            names = {"shutdown": "Выключение компьютера", "restart": "Перезагрузка компьютера",
                     "logout": "Выход из учётной записи", "hibernate": "Гибернация"}
            self.core.ask_confirm(names[action], lambda: system.power(action))
            return "Подтверждение уже запрошено голосом. Ничего не добавляй."
        return system.power(action)

    def t_system_info(self):
        return system.system_info()

    def t_get_time(self):
        return system.now()

    def t_get_weather(self, city):
        return system.weather(city)

    def t_set_timer(self, seconds, message):
        def fire():
            self.core.say(f"{self.core.settings['user_title'].capitalize()}, напоминаю: {message}")
            self.core.emit({"type": "toast", "text": f"⏰ {message}"})
        t = threading.Timer(int(seconds), fire)
        t.daemon = True
        t.start()
        return f"Таймер на {seconds} с поставлен"

    def t_screenshot(self):
        return "Скриншот сохранён: " + system.screenshot()

    def t_open_folder(self, path):
        p = system.known_folder(path)
        apps.start(str(p))
        return f"Открыл {p}"

    def t_clipboard(self, text=None):
        import pyperclip

        if text is None:
            return "В буфере: " + (pyperclip.paste() or "пусто")[:3000]
        pyperclip.copy(text)
        return "Скопировал в буфер"

    def t_write_note(self, text):
        folder = system.known_folder("documents") / "Jarvis"
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "заметки.txt"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"[{system.now()}] {text}\n")
        return f"Записал в {f}"

    def t_run_shell(self, command):
        if not self.core.settings["allow_shell"]:
            return "Выполнение команд отключено в настройках"
        if system.DANGEROUS.search(command):
            return "Отказано: команда может уничтожить данные. Пусть пользователь сделает это сам."
        return system.run_shell(command)


def default_protocols_path() -> Path:
    from .config import resource_dir

    return resource_dir() / "default_protocols.json"
