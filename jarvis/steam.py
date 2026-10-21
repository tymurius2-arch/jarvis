"""Поиск установленных игр Steam и их запуск."""
import difflib
import os
import re
from pathlib import Path

from .textutil import best_match, normalize

# Народные названия игр -> часть настоящего названия или appid.
GAME_ALIASES = {
    "бимка": 284160, "бимку": 284160, "бимнг": 284160, "бим нг": 284160, "бименджи": 284160,
    "beamng": 284160, "бимэнджи": 284160,
    "кс": 730, "кска": 730, "контра": 730, "контру": 730, "каэс": 730, "cs": 730, "counter strike": 730,
    "дота": 570, "доту": 570, "дотка": 570, "dota": 570,
    "гта": 271590, "гташка": 271590, "гта 5": 271590, "gta": 271590,
    "раст": 252490, "rust": 252490,
    "пубг": 578080, "пабг": 578080, "pubg": 578080,
    "форза": 1551360, "forza": 1551360,
    "тф2": 440, "тим фортресс": 440,
    "апекс": 1172470, "apex": 1172470,
    "эльден ринг": 1245620, "элден ринг": 1245620,
    "киберпанк": 1091500, "cyberpunk": 1091500,
    "ведьмак": 292030, "witcher": 292030,
    "террария": 105600, "terraria": 105600,
    "гаррис мод": 4000, "гмод": 4000, "garry": 4000,
    "рдр": 1174180, "рдр 2": 1174180, "red dead": 1174180,
    "фазмофобия": 739630, "фазму": 739630, "фазма": 739630,
    "валхейм": 892970,
    "euro truck": 227300, "етс": 227300, "евро трак": 227300, "дальнобойщики": 227300,
    "сатисфактори": 526870, "майнкрафт": None,
    "геометри": 322170, "геометрия даш": 322170, "геометри даш": 322170, "геометри дэш": 322170,
    "джеометри": 322170, "geometry": 322170, "гд": 322170, "геометрию": 322170,
    "дбд": 381210, "дед бай дейлайт": 381210,
    "вар тандер": 236390, "вартандер": 236390, "war thunder": 236390,
    "танки": 1407200, "мир танков": 1407200,
}


def steam_path() -> Path | None:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except Exception:
        pass
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam", os.path.expanduser("~/.steam/steam")):
        if Path(guess).exists():
            return Path(guess)
    return None


def _libraries(root: Path) -> list[Path]:
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text("utf-8", errors="ignore")
        for p in re.findall(r'"path"\s+"([^"]+)"', text):
            p = Path(p.replace("\\\\", "\\"))
            if p not in libs:
                libs.append(p)
    return libs


_cache: list[dict] | None = None


def installed_games(refresh: bool = False) -> list[dict]:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    games = []
    root = steam_path()
    if root:
        for lib in _libraries(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    text = acf.read_text("utf-8", errors="ignore")
                    appid = re.search(r'"appid"\s+"(\d+)"', text).group(1)
                    name = re.search(r'"name"\s+"([^"]+)"', text).group(1)
                except Exception:
                    continue
                if any(w in name.lower() for w in ("redistributable", "steamworks", "proton", "runtime")):
                    continue
                games.append({"appid": int(appid), "name": name})
    games.sort(key=lambda g: g["name"].lower())
    _cache = games
    return games


def find_game(query: str) -> dict | None:
    q = normalize(query)
    games = installed_games()
    # 1. Народное название
    for alias, appid in sorted(GAME_ALIASES.items(), key=lambda kv: -len(kv[0])):
        end = "" if len(alias) >= 4 else r"(\s|$)"
        if appid and re.search(rf"(^|\s){re.escape(alias)}{end}", q):
            for g in games:
                if g["appid"] == appid:
                    return g
            return {"appid": appid, "name": alias}
    if q.isdigit():
        return {"appid": int(q), "name": q}
    # 2. Совпадение по названию
    names = {normalize(g["name"]): g for g in games}
    for n, g in names.items():
        if q and (q in n or n in q):
            return g
    match = difflib.get_close_matches(q, list(names), n=1, cutoff=0.55)
    if match:
        return names[match[0]]
    return None


def find_installed(query: str) -> dict | None:
    """Только установленные игры и только уверенное совпадение — для мгновенного запуска без нейросети."""
    games = installed_games()
    if not games:
        return None
    q = normalize(query)
    ids = {g["appid"]: g for g in games}
    for alias, appid in sorted(GAME_ALIASES.items(), key=lambda kv: -len(kv[0])):
        end = "" if len(alias) >= 4 else r"(\s|$)"
        if appid in ids and re.search(rf"(^|\s){re.escape(alias)}{end}", q):
            return ids[appid]
    name = best_match(q, [g["name"] for g in games])
    return next((g for g in games if g["name"] == name), None) if name else None


def launch(appid: int) -> None:
    url = f"steam://rungameid/{int(appid)}"
    if hasattr(os, "startfile"):
        os.startfile(url)
    else:
        import webbrowser

        webbrowser.open(url)
