"""Запуск Джарвиса: python jarvis.py"""
import os
import sys

# Разрешаем звук без клика по окну (для встроенного браузера Windows)
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")


def _unblock(folder):
    """Снимает с файлов метку «скачано из интернета», иначе .NET не грузит окно программы."""
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                os.remove(os.path.join(root, name) + ":Zone.Identifier")
            except OSError:
                pass


def _selftest():
    """Проверка сборки: окно, звук, ядро загружаются. Код выхода 0 = всё хорошо."""
    import traceback

    log = os.path.join(os.path.dirname(sys.executable), "selftest.log")
    try:
        import webview  # noqa: F401

        if sys.platform == "win32":
            import clr  # noqa: F401
            from webview.platforms import winforms  # noqa: F401
        import miniaudio  # noqa: F401
        import sounddevice  # noqa: F401
        from jarvis.core import Core  # noqa: F401

        open(log, "w", encoding="utf-8").write("OK")
        os._exit(0)
    except BaseException:
        open(log, "w", encoding="utf-8").write(traceback.format_exc())
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
        if getattr(sys, "frozen", False):
            _unblock(os.path.dirname(sys.executable))
    if "--selftest" in sys.argv:
        _selftest()
    from jarvis.app import main

    main()
