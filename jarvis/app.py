"""Окно программы Джарвис (pywebview) и мост между интерфейсом и ядром."""
import json
import os
import sys
import threading
import time

from . import listener, steam
from .config import PROVIDERS, VOICES, data_dir, resource_dir
from .core import Core
from .protocols import ACTION_TYPES


class Api:
    """Эти методы вызываются из JavaScript: window.pywebview.api.<метод>()."""

    def __init__(self, core: Core):
        self._core = core
        self._window = None

    def get_state(self):
        c = self._core
        return {
            "settings": c.settings.data,
            "providers": PROVIDERS,
            "voices": VOICES,
            "action_types": ACTION_TYPES,
            "protocols": c.protocols.items,
            "history": c.history[-80:],
            "devices": listener.list_devices(),
            "ai_ready": c.brain.ready(),
            "mic_error": c.listener.error,
            "data_dir": str(data_dir()),
        }

    def send_text(self, text):
        self._core.submit_text(text)
        return True

    def listen_now(self):
        """Кнопка микрофона: следующая фраза — команда без слова «Джарвис»."""
        self._core.listener.force_command = True
        self._core.listener.unmute()
        self._core.active_until = time.time() + 10
        self._core.emit({"type": "state", "state": "listening"})
        return True

    def speech_done(self):
        self._core.speech_done()

    def stop_speaking(self):
        self._core.listener.unmute()

    def set_listening(self, on):
        self._core.settings.update({"listening": bool(on)})
        return bool(on)

    def save_settings(self, patch):
        old_dev = self._core.settings["stt"].get("device")
        llm = self._core.settings["llm"]
        new_provider = (patch.get("llm") or {}).get("provider")
        if (new_provider and new_provider != llm["provider"] and llm["provider"] == "groq" and llm.get("api_key")
                and not self._core.settings["stt"].get("groq_key")):
            # Ушли с Groq — ключ остаётся для слуха (Whisper)
            patch.setdefault("stt", {})["groq_key"] = llm["api_key"]
        self._core.settings.update(patch)
        if self._core.settings["stt"].get("device") != old_dev:
            self._core.listener.restart()
        if "autostart" in patch:
            set_autostart(bool(patch["autostart"]))
        self._core.brain.reset()
        return self.get_state()

    def test_voice(self, text=None):
        threading.Thread(target=self._core.say, args=(text or "Добрый вечер, сэр. Все системы работают в штатном режиме.",),
                         daemon=True).start()

    def test_ai(self):
        try:
            self._core.brain.reset()
            ans = self._core.brain.ask("Скажи одной короткой фразой, что ты на связи.")
            self._core.brain.reset()
            return {"ok": True, "text": ans}
        except Exception as e:
            self._core.brain.reset()
            return {"ok": False, "text": str(e)}

    # ---- протоколы ----
    def save_protocol(self, proto):
        self._core.protocols.upsert(proto)
        return self._core.protocols.items

    def delete_protocol(self, pid):
        self._core.protocols.delete(pid)
        return self._core.protocols.items

    def reset_protocols(self):
        self._core.protocols.reset()
        return self._core.protocols.items

    def run_protocol(self, pid):
        for p in self._core.protocols.items:
            if p["id"] == pid:
                threading.Thread(target=self._core._run_actions, args=(p,), daemon=True).start()
        return True

    def test_phrase(self, phrase):
        p = self._core.protocols.match(phrase)
        return p["name"] if p else None

    def export_protocols(self):
        path = os.path.join(os.path.expanduser("~"), "Desktop", "jarvis_protocols.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._core.protocols.items, f, ensure_ascii=False, indent=2)
        return path

    def import_protocols(self):
        import webview

        res = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=("JSON (*.json)",))
        if not res:
            return self._core.protocols.items
        with open(res[0], encoding="utf-8") as f:
            for p in json.load(f):
                p.pop("id", None)
                self._core.protocols.upsert(p)
        return self._core.protocols.items

    def pick_file(self):
        import webview

        res = self._window.create_file_dialog(webview.OPEN_DIALOG)
        return res[0] if res else None

    # ---- игры и программы ----
    def steam_games(self):
        return steam.installed_games(refresh=True)

    def launch_game(self, appid):
        steam.launch(int(appid))
        return True

    def app_names(self):
        return self._core.tools.apps.names()

    def clear_chat(self):
        self._core.history.clear()
        self._core.brain.reset()

    def open_link(self, url):
        import webbrowser

        webbrowser.open(url)

    def open_data_dir(self):
        if hasattr(os, "startfile"):
            os.startfile(str(data_dir()))

    def quit(self):
        os._exit(0)


def set_autostart(on: bool):
    if os.name != "nt":
        return
    import winreg

    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --minimized'
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = f'"{pyw}" "{os.path.abspath(sys.argv[0])}" --minimized'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, "Jarvis", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "Jarvis")
            except FileNotFoundError:
                pass


def _tray(window, core):
    """Значок у часов: Джарвис продолжает слушать, когда окно закрыто."""
    try:
        import pystray
        from PIL import Image

        img = Image.open(resource_dir() / "ui" / "icon.png")

        def show(icon=None, item=None):
            window.show()
            window.restore()

        def toggle(icon, item):
            core.settings.update({"listening": not core.settings["listening"]})
            core.emit({"type": "settings", "settings": core.settings.data})

        menu = pystray.Menu(
            pystray.MenuItem("Открыть Джарвиса", show, default=True),
            pystray.MenuItem("Слушать микрофон", toggle, checked=lambda i: core.settings["listening"]),
            pystray.MenuItem("Выход", lambda i, it: (i.stop(), os._exit(0))),
        )
        icon = pystray.Icon("Jarvis", img, "Джарвис", menu)
        icon.run_detached()
        return icon
    except Exception as e:
        print("tray:", e)
        return None


def main():
    import webview

    core = Core()
    api = Api(core)
    minimized = "--minimized" in sys.argv
    window = webview.create_window(
        "J.A.R.V.I.S.", url=str(resource_dir() / "ui" / "index.html"), js_api=api,
        width=1280, height=820, min_size=(980, 640), background_color="#05080d", hidden=minimized)
    api._window = window

    ready = threading.Event()
    import queue

    events: queue.Queue = queue.Queue()
    last_level = {"v": None}

    def push(event):
        # Уровень микрофона приходит 10 раз в секунду — храним только последний
        if event.get("type") == "level":
            last_level["v"] = event
            events.put(None)
        else:
            events.put(event)

    def sender():
        """Одна очередь: события приходят в окно по порядку и не теряются."""
        backlog = []
        while True:
            item = events.get()
            if item is None:
                item, last_level["v"] = last_level["v"], None
                if item is None:
                    continue
            if not ready.is_set():
                if item.get("type") != "level":
                    backlog.append(item)
                continue
            for ev in backlog + [item]:
                try:
                    window.evaluate_js(f"window.jarvisEvent && window.jarvisEvent({json.dumps(ev, ensure_ascii=False)})")
                except Exception:
                    pass
            backlog = []

    threading.Thread(target=sender, daemon=True).start()

    core.listeners.append(push)

    tray = {"icon": None}

    def on_loaded():
        ready.set()
        events.put({"type": "state", "state": "idle"})

    def on_closing():
        if core.settings["minimize_to_tray"] and tray["icon"] is not None:
            window.hide()
            return False  # не закрываем, прячем в трей
        os._exit(0)

    window.events.loaded += on_loaded
    window.events.closing += on_closing

    def started():
        tray["icon"] = _tray(window, core)
        core.start()

    webview.start(started, debug="--debug" in sys.argv, private_mode=False,
                  storage_path=str(data_dir() / "webview"))
    os._exit(0)
