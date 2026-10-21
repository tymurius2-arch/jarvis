"""Протоколы — сценарии: фраза -> список действий."""
import copy
import difflib
import json
import threading
import time
import uuid

from . import apps, steam, system
from .config import data_dir, resource_dir
from .textutil import normalize

ACTION_TYPES = {
    "say": "Сказать фразу",
    "steam": "Запустить игру Steam",
    "open_app": "Запустить программу",
    "open_url": "Открыть сайт",
    "run": "Запустить файл",
    "close_app": "Закрыть программу",
    "hotkey": "Нажать клавиши",
    "type": "Напечатать текст",
    "volume": "Громкость",
    "media": "Музыка (пауза/дальше)",
    "wait": "Пауза (секунды)",
    "shell": "Команда PowerShell",
    "power": "Питание (сон/выкл/блок)",
    "ai": "Спросить нейросеть",
}


class Protocols:
    def __init__(self):
        self.path = data_dir() / "protocols.json"
        self.lock = threading.Lock()
        self.items: list[dict] = []
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text("utf-8"))
                return
            except Exception:
                pass
        self.items = json.loads((resource_dir() / "default_protocols.json").read_text("utf-8"))
        for p in self.items:
            p.setdefault("id", uuid.uuid4().hex[:8])
        self.save()

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)

    def upsert(self, proto: dict) -> dict:
        proto = copy.deepcopy(proto)
        proto.setdefault("id", uuid.uuid4().hex[:8])
        proto["triggers"] = [t.strip() for t in proto.get("triggers", []) if t and t.strip()]
        proto.setdefault("enabled", True)
        for i, p in enumerate(self.items):
            if p["id"] == proto["id"]:
                self.items[i] = proto
                break
        else:
            self.items.append(proto)
        self.save()
        return proto

    def delete(self, pid: str):
        self.items = [p for p in self.items if p["id"] != pid]
        self.save()

    def reset(self):
        self.path.unlink(missing_ok=True)
        self.load()

    def find_by_name(self, name: str):
        q = normalize(name)
        for p in self.items:
            if normalize(p["name"]) == q or q in normalize(p["name"]):
                return p
        names = {normalize(p["name"]): p for p in self.items}
        m = difflib.get_close_matches(q, list(names), n=1, cutoff=0.5)
        return names[m[0]] if m else self.match(name)

    def match(self, text: str):
        """Ищет протокол, фраза которого есть в тексте. Длинные фразы важнее коротких."""
        t = normalize(text)
        if not t:
            return None
        words = t.split()
        best, best_len = None, 0
        for p in self.items:
            if not p.get("enabled", True):
                continue
            for trig in p.get("triggers", []):
                trig_n = normalize(trig)
                if not trig_n:
                    continue
                hit = f" {trig_n}" in f" {t}"  # начало слова: «бимк» ловит «бимку»
                if not hit and " " not in trig_n and len(trig_n) >= 5:
                    hit = any(difflib.SequenceMatcher(None, trig_n, w[:len(trig_n) + 1]).ratio() >= 0.84 for w in words)
                if hit and len(trig_n) > best_len:
                    best, best_len = p, len(trig_n)
        return best


def run_action(core, action: dict) -> None:
    t = action.get("type")
    v = action.get("value", "")
    if t == "say":
        core.say(str(v), wait=True)
    elif t == "steam":
        appid = action.get("appid") or v
        if not str(appid).isdigit():
            game = steam.find_game(str(appid))
            appid = game["appid"] if game else None
        if appid:
            steam.launch(int(appid))
    elif t == "open_app":
        target = core.tools.apps.find(str(v))
        if target:
            apps.start(target)
        else:
            core.log(f"Программа «{v}» не найдена", "error")
    elif t == "open_url":
        system.open_url(str(v))
    elif t == "run":
        apps.start(str(v), action.get("args", ""))
    elif t == "close_app":
        apps.close_process(str(v))
    elif t == "hotkey":
        system.hotkey(str(v))
    elif t == "type":
        system.type_text(str(v))
    elif t == "volume":
        system.set_volume(int(v or 0))
    elif t == "media":
        system.media(str(v or "play_pause"))
    elif t == "wait":
        time.sleep(min(600.0, float(v or 1)))
    elif t == "shell":
        if core.settings["allow_shell"]:
            core.log(system.run_shell(str(v)), "tool")
    elif t == "power":
        system.power(str(v))
    elif t == "ai":
        core.handle_ai(str(v))
