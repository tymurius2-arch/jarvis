"""Запуск Джарвиса: python jarvis.py"""
import os
import sys

# Разрешаем звук без клика по окну (для встроенного браузера Windows)
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")

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
    from jarvis.app import main

    main()
