"""Поиск и запуск программ Windows, закрытие процессов."""
import difflib
import json
import os
import subprocess
import threading
from pathlib import Path

from .textutil import best_match, normalize

# Как люди называют программы -> что запускать
APP_ALIASES = {
    "блокнот": "notepad.exe", "notepad": "notepad.exe",
    "калькулятор": "calc.exe", "калькулятора": "calc.exe",
    "проводник": "explorer.exe", "мой компьютер": "explorer.exe", "этот компьютер": "explorer.exe",
    "диспетчер задач": "taskmgr.exe", "диспетчер": "taskmgr.exe",
    "панель управления": "control.exe",
    "настройки": "ms-settings:", "параметры": "ms-settings:",
    "командную строку": "cmd.exe", "командная строка": "cmd.exe", "консоль": "cmd.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe", "павершелл": "powershell.exe", "терминал": "wt.exe",
    "paint": "mspaint.exe", "пейнт": "mspaint.exe",
    "стим": "steam://open/main", "steam": "steam://open/main", "стима": "steam://open/main",
    "браузер": "https://www.google.com",
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com",
    "почту": "https://mail.google.com", "гугл": "https://www.google.com",
    "чат gpt": "https://chatgpt.com", "чатгпт": "https://chatgpt.com",
    "магазин": "ms-windows-store:", "microsoft store": "ms-windows-store:",
    "ножницы": "snippingtool.exe",
}

# Народные названия -> название процесса для закрытия
PROCESS_ALIASES = {
    "хром": "chrome", "гугл хром": "chrome", "браузер": "chrome", "chrome": "chrome",
    "эдж": "msedge", "edge": "msedge", "опера": "opera", "яндекс": "browser", "файрфокс": "firefox",
    "стим": "steam", "steam": "steam", "дискорд": "discord", "discord": "discord",
    "телеграм": "telegram", "телегу": "telegram", "telegram": "telegram",
    "спотифай": "spotify", "spotify": "spotify", "блокнот": "notepad", "калькулятор": "calculatorapp",
    "ворд": "winword", "word": "winword", "эксель": "excel", "excel": "excel",
    "бимку": "beamng", "бимка": "beamng", "beamng": "beamng", "кс": "cs2", "дота": "dota2", "доту": "dota2",
    "обс": "obs64", "obs": "obs64", "вс код": "code", "vs code": "code",
}


class AppIndex:
    def __init__(self):
        self.items: dict[str, str] = {}  # нормализованное имя -> путь/команда
        self._ready = threading.Event()
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self):
        items: dict[str, str] = {}
        dirs = []
        for env in ("PROGRAMDATA", "APPDATA"):
            if os.environ.get(env):
                dirs.append(Path(os.environ[env]) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        dirs.append(Path.home() / "Desktop")
        if os.environ.get("PUBLIC"):
            dirs.append(Path(os.environ["PUBLIC"]) / "Desktop")
        for d in dirs:
            if not d.exists():
                continue
            for f in list(d.rglob("*.lnk")) + list(d.rglob("*.url")):
                name = normalize(f.stem)
                if not name or "uninstall" in name or "удален" in name:
                    continue
                items.setdefault(name, str(f))
        # Приложения из Microsoft Store (Get-StartApps)
        if os.name == "nt":
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
                    capture_output=True, text=True, encoding="utf-8", timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                ).stdout
                for app in json.loads(out or "[]"):
                    name = normalize(app.get("Name", ""))
                    if name and "uninstall" not in name:
                        items.setdefault(name, "shell:AppsFolder\\" + app["AppID"])
            except Exception:
                pass
        self.items = items
        self._ready.set()

    def names(self) -> list[str]:
        return sorted(self.items)

    def find(self, query: str) -> str | None:
        q = normalize(query)
        if not q:
            return None
        for alias in sorted(APP_ALIASES, key=len, reverse=True):
            if q == alias:
                return APP_ALIASES[alias]
        self._ready.wait(timeout=10)
        translit = _translit(q)
        for cand in (q, translit):
            if cand in self.items:
                return self.items[cand]
        for cand in (q, translit):
            hits = [n for n in self.items if cand in n or (len(n) > 3 and n in cand)]
            if hits:
                return self.items[min(hits, key=len)]
        for alias in sorted(APP_ALIASES, key=len, reverse=True):
            if alias in q:
                return APP_ALIASES[alias]
        match = difflib.get_close_matches(translit, list(self.items), n=1, cutoff=0.6)
        if match:
            return self.items[match[0]]
        return None


    def find_strict(self, query: str) -> str | None:
        q = normalize(query)
        if q in APP_ALIASES:
            return APP_ALIASES[q]
        self._ready.wait(timeout=5)
        if q in self.items:
            return self.items[q]
        name = best_match(q, list(self.items))
        return self.items[name] if name else None


def start(target: str, args: str = "") -> None:
    if target.startswith("shell:AppsFolder"):
        subprocess.Popen(["explorer.exe", target])
    elif args:
        subprocess.Popen(f'"{target}" {args}', shell=True)
    elif hasattr(os, "startfile"):
        os.startfile(target)
    else:
        import webbrowser

        webbrowser.open(target)


def close_process(name: str) -> list[str]:
    import psutil

    q = normalize(name)
    target = PROCESS_ALIASES.get(q)
    if not target:
        for alias, proc in PROCESS_ALIASES.items():
            if alias in q:
                target = proc
                break
    target = (target or _translit(q).replace(" ", "")).lower()
    closed = []
    protected = {"explorer", "system", "svchost", "csrss", "winlogon", "lsass", "dwm", "jarvis", "python", "pythonw"}
    for p in psutil.process_iter(["name"]):
        pname = (p.info.get("name") or "").lower().removesuffix(".exe")
        if not pname or pname in protected:
            continue
        if pname == target or (len(target) >= 4 and target in pname):
            try:
                p.terminate()
                closed.append(p.info["name"])
            except Exception:
                pass
    return sorted(set(closed))


_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
       "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
       "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye"}


def _translit(text: str) -> str:
    return "".join(_TR.get(c, c) for c in text)
