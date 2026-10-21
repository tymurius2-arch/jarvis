"""Управление Windows: звук, клавиши, мышь, окна, питание, информация о системе."""
import datetime
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

IS_WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _gui():
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.03
    return pyautogui


# ---------- Звук ----------
def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities

    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    speakers = AudioUtilities.GetSpeakers()
    ev = getattr(speakers, "EndpointVolume", None)
    if ev is not None:
        return ev
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    try:
        return round(_endpoint().GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return -1


def set_volume(level: int) -> int:
    level = max(0, min(100, int(level)))
    try:
        ev = _endpoint()
        ev.SetMute(0, None)
        ev.SetMasterVolumeLevelScalar(level / 100, None)
    except Exception:
        g = _gui()  # запасной вариант: клавиши громкости (шаг 2%)
        g.press("volumedown", presses=50)
        g.press("volumeup", presses=level // 2)
    return level


def change_volume(delta: int) -> int:
    cur = get_volume()
    if cur < 0:
        key = "volumeup" if delta > 0 else "volumedown"
        _gui().press(key, presses=max(1, abs(int(delta)) // 2))
        return -1
    return set_volume(cur + int(delta))


def mute(state: bool | None = None) -> bool:
    try:
        ev = _endpoint()
        new = (not ev.GetMute()) if state is None else bool(state)
        ev.SetMute(int(new), None)
        return new
    except Exception:
        _gui().press("volumemute")
        return True


def media(action: str) -> None:
    keys = {"play_pause": "playpause", "pause": "playpause", "play": "playpause", "next": "nexttrack",
            "previous": "prevtrack", "prev": "prevtrack", "stop": "stop"}
    _gui().press(keys.get(action, "playpause"))


# ---------- Клавиатура и мышь ----------
KEY_NAMES = {"win": "winleft", "windows": "winleft", "ctrl": "ctrl", "control": "ctrl", "alt": "alt",
             "shift": "shift", "enter": "enter", "esc": "esc", "escape": "esc", "space": "space",
             "tab": "tab", "del": "delete", "backspace": "backspace", "printscreen": "printscreen"}


def hotkey(keys: str) -> None:
    parts = [KEY_NAMES.get(k.strip().lower(), k.strip().lower()) for k in re.split(r"[+\s]+", keys) if k.strip()]
    g = _gui()
    if len(parts) == 1:
        g.press(parts[0])
    else:
        g.hotkey(*parts)


def type_text(text: str) -> None:
    import pyperclip

    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)  # через буфер — так работает и русский текст
    time.sleep(0.05)
    _gui().hotkey("ctrl", "v")
    time.sleep(0.2)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def mouse(action: str, x: int | None = None, y: int | None = None, amount: int = 0) -> str:
    g = _gui()
    if x is not None and y is not None and action in ("move", "click", "double_click", "right_click"):
        g.moveTo(int(x), int(y), duration=0.15)
    if action == "click":
        g.click()
    elif action == "double_click":
        g.doubleClick()
    elif action == "right_click":
        g.rightClick()
    elif action == "scroll":
        g.scroll(int(amount or -500))
    w, h = g.size()
    px, py = g.position()
    return f"Экран {w}x{h}, курсор в ({px}, {py})"


# ---------- Окна ----------
def window_control(action: str, title: str = "") -> str:
    if action == "minimize_all":
        hotkey("win+d")
        return "Все окна свёрнуты"
    import pygetwindow as gw

    if action == "list":
        titles = [w.title for w in gw.getAllWindows() if w.title.strip() and w.visible]
        return "Открытые окна: " + "; ".join(titles[:40])
    wins = [w for w in gw.getAllWindows() if title.lower() in w.title.lower() and w.title.strip()]
    if not wins:
        return f"Окно «{title}» не найдено"
    w = wins[0]
    if action == "focus":
        try:
            if w.isMinimized:
                w.restore()
            w.activate()
        except Exception:
            _gui().press("alt")
            w.activate()
    elif action == "minimize":
        w.minimize()
    elif action == "maximize":
        w.maximize()
    elif action == "close":
        w.close()
    return f"Готово: {action} «{w.title}»"


# ---------- Питание ----------
def power(action: str) -> str:
    cmds = {
        "shutdown": "shutdown /s /t 5",
        "restart": "shutdown /r /t 5",
        "cancel": "shutdown /a",
        "logout": "shutdown /l",
        "lock": "rundll32.exe user32.dll,LockWorkStation",
        "sleep": "rundll32.exe powrprof.dll,SetSuspendState 0,1,0",
        "hibernate": "shutdown /h",
        "monitor_off": 'powershell -NoProfile -Command "(Add-Type \'[DllImport(\\"user32.dll\\")]public static extern int SendMessage(int hWnd,int hMsg,int wParam,int lParam);\' -Name a -Pas)::SendMessage(-1,0x0112,0xF170,2)"',
    }
    if action not in cmds:
        return "Неизвестное действие"
    subprocess.Popen(cmds[action], shell=True, creationflags=NO_WINDOW)
    return f"Выполняю: {action}"


# ---------- Информация ----------
def system_info() -> str:
    import psutil

    parts = [f"Процессор загружен на {psutil.cpu_percent(interval=0.4):.0f}%"]
    vm = psutil.virtual_memory()
    parts.append(f"оперативная память: занято {vm.used / 2**30:.1f} из {vm.total / 2**30:.1f} ГБ ({vm.percent:.0f}%)")
    try:
        b = psutil.sensors_battery()
        if b:
            parts.append(f"батарея {b.percent:.0f}%{' (заряжается)' if b.power_plugged else ''}")
    except Exception:
        pass
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            parts.append(f"диск {part.device} свободно {u.free / 2**30:.0f} ГБ из {u.total / 2**30:.0f}")
        except Exception:
            continue
    up = datetime.timedelta(seconds=int(time.time() - psutil.boot_time()))
    parts.append(f"компьютер работает {up}")
    try:
        procs = sorted(psutil.process_iter(["name", "memory_info"]),
                       key=lambda p: p.info["memory_info"].rss if p.info.get("memory_info") else 0, reverse=True)
        parts.append("больше всего памяти едят: " + ", ".join(p.info["name"] for p in procs[:5]))
    except Exception:
        pass
    return "; ".join(parts)


def now() -> str:
    d = datetime.datetime.now()
    days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return d.strftime(f"%H:%M, %d.%m.%Y, {days[d.weekday()]}")


def weather(city: str) -> str:
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": city, "count": 1, "language": "ru"}, timeout=8).json()
    if not geo.get("results"):
        return f"Город «{city}» не найден"
    g = geo["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "forecast_days": 3, "wind_speed_unit": "ms"}, timeout=8).json()
    c = w["current"]
    d = w["daily"]
    days = "; ".join(f"{d['time'][i]}: {d['temperature_2m_min'][i]:.0f}..{d['temperature_2m_max'][i]:.0f}°C, "
                     f"{_wcode(d['weather_code'][i])}, осадки {d['precipitation_probability_max'][i]}%"
                     for i in range(len(d["time"])))
    return (f"{g['name']}: сейчас {c['temperature_2m']:.0f}°C (ощущается {c['apparent_temperature']:.0f}), "
            f"{_wcode(c['weather_code'])}, ветер {c['wind_speed_10m']:.0f} м/с, влажность {c['relative_humidity_2m']}%. "
            f"Прогноз: {days}")


def _wcode(code: int) -> str:
    table = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "туман",
             51: "морось", 53: "морось", 55: "морось", 61: "небольшой дождь", 63: "дождь", 65: "ливень",
             71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снег", 80: "ливень", 81: "ливень",
             82: "сильный ливень", 85: "снегопад", 86: "снегопад", 95: "гроза", 96: "гроза с градом", 99: "гроза с градом"}
    return table.get(int(code), "осадки")


# ---------- Интернет ----------
def open_url(url: str) -> None:
    if not re.match(r"^[a-z][a-z0-9+.-]*:", url):
        url = "https://" + url
    import webbrowser

    webbrowser.open(url)


def web_search(query: str) -> None:
    open_url("https://www.google.com/search?q=" + urllib.parse.quote(query))


def youtube(query: str, play: bool = True) -> str:
    if play:
        try:
            html = requests.get("https://www.youtube.com/results", params={"search_query": query},
                                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru"}, timeout=8).text
            vid = re.search(r'"videoId":"([\w-]{11})"', html)
            if vid:
                open_url(f"https://www.youtube.com/watch?v={vid.group(1)}")
                return "Включаю видео на YouTube"
        except Exception:
            pass
    open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
    return "Открыл поиск на YouTube"


# ---------- Файлы ----------
def known_folder(name: str) -> Path:
    home = Path.home()
    table = {"загрузки": "Downloads", "downloads": "Downloads", "документы": "Documents", "documents": "Documents",
             "рабочий стол": "Desktop", "desktop": "Desktop", "изображения": "Pictures", "картинки": "Pictures",
             "pictures": "Pictures", "музыка": "Music", "music": "Music", "видео": "Videos", "videos": "Videos"}
    key = name.strip().lower()
    if key in table:
        return home / table[key]
    return Path(os.path.expandvars(os.path.expanduser(name)))


def screenshot() -> str:
    folder = known_folder("pictures") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / datetime.datetime.now().strftime("screen_%Y-%m-%d_%H-%M-%S.png")
    _gui().screenshot(str(path))
    return str(path)


def run_shell(command: str, timeout: int = 60) -> str:
    if IS_WIN:
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
    else:
        args = ["bash", "-lc", command]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
        out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
        return (out or f"Готово (код {r.returncode})")[:4000]
    except subprocess.TimeoutExpired:
        return "Команда выполнялась слишком долго и была остановлена"


DANGEROUS = re.compile(
    r"(format\s+[a-z]:|remove-item\s+.*-recurse.*[a-z]:\\\s*$|rm\s+-rf\s+/|del\s+/[sq].*[a-z]:\\\s*$|"
    r"diskpart|bcdedit|cipher\s+/w|vssadmin\s+delete|reg\s+delete\s+hk(lm|ey_local_machine)|"
    r"set-executionpolicy\s+unrestricted\s+-scope\s+localmachine)", re.I)
