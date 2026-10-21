"""Запуск Джарвиса: python jarvis.py"""
import os
import sys

# Разрешаем звук без клика по окну (для встроенного браузера Windows)
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")


HERE = os.path.dirname(os.path.abspath(__file__))


def _unblock(folder):
    """Снимает с файлов метку «скачано из интернета», иначе .NET не грузит окно программы.
    Делается один раз после распаковки."""
    marker = os.path.join(folder, ".unblocked")
    if os.path.exists(marker):
        return
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                os.remove(os.path.join(root, name) + ":Zone.Identifier")
            except OSError:
                pass
    try:
        open(marker, "w").close()
    except OSError:
        pass


def _desktop_shortcut():
    """Первый запуск из папки с встроенным Python: ярлык «Джарвис» на рабочем столе."""
    pyw = os.path.join(HERE, "python", "pythonw.exe")
    if not os.path.exists(pyw):
        return
    marker = os.path.join(HERE, ".shortcut")
    if os.path.exists(marker):
        return
    import subprocess

    lnk = os.path.join(os.path.expanduser("~"), "Desktop", "Джарвис.lnk")
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath = $env:PYW; "
          "$s.Arguments = '\"' + $env:SCRIPT + '\"'; $s.WorkingDirectory = $env:HERE; "
          "$s.IconLocation = $env:ICON; $s.Description = 'J.A.R.V.I.S.'; $s.Save()")
    env = dict(os.environ, LNK=lnk, PYW=pyw, SCRIPT=os.path.join(HERE, "jarvis.py"), HERE=HERE,
               ICON=os.path.join(HERE, "assets", "jarvis.ico"))
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        open(marker, "w").close()
    except Exception:
        pass


def _selftest():
    """Проверка сборки: окно, звук, ядро загружаются. Код выхода 0 = всё хорошо."""
    import importlib
    import traceback

    log = open(os.path.join(HERE, "selftest.log"), "w", encoding="utf-8")
    steps = ["webview", "clr", "webview.platforms.winforms", "miniaudio", "sounddevice", "jarvis.core"]
    if sys.platform != "win32":
        steps = [s for s in steps if s not in ("clr", "webview.platforms.winforms")]
    try:
        for name in steps:
            log.write(f"import {name}... ")
            log.flush()
            importlib.import_module(name)
            log.write("ok\n")
            log.flush()
        log.write("OK\n")
        log.close()
        os._exit(0)
    except BaseException:
        log.write("\n" + traceback.format_exc())
        log.close()
        os._exit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:  # чёткий интерфейс на мониторах с масштабированием
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        # Один Джарвис на компьютер
        import ctypes

        ctypes.windll.kernel32.CreateMutexW(None, False, "JarvisAssistantSingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(0, "Джарвис уже запущен — ищите значок возле часов.", "J.A.R.V.I.S.", 0x40)
            sys.exit(0)
        _unblock(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE)
    if "--selftest" in sys.argv:
        _selftest()
    if sys.platform == "win32":
        _desktop_shortcut()
    from jarvis.app import main

    main()
