import difflib
import re


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
       "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
       "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "ye"}


def translit(text: str) -> str:
    return "".join(_TR.get(c, c) for c in text)


def variants(query: str) -> list[str]:
    """«геометри дэш» → ["геометри дэш", "geometri desh", "geometri dash", ...] — для поиска английских названий."""
    q = normalize(query)
    t = translit(q)
    out = [q, t, t.replace("ks", "x"), t.replace("dzh", "j"), t.replace("dzh", "g"), t.replace("e", "a"),
           t.replace("i", "y"), t.replace(" ", "")]
    seen, res = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


def best_match(query: str, names: list[str], full: float = 0.74, prefix: float = 0.84) -> str | None:
    """Строгий нечёткий поиск названия: лучше не найти, чем запустить не то."""
    if not query or not names:
        return None
    vs = variants(query)
    best, score = None, 0.0
    for n in names:
        nn = normalize(n)
        flat = nn.replace(" ", "")
        for v in vs:
            if len(v) >= 4 and (v == nn or v == flat or (len(v) >= 5 and (nn.startswith(v) or flat.startswith(v)))):
                return n
            r_full = max(difflib.SequenceMatcher(None, v, nn).ratio(), difflib.SequenceMatcher(None, v, flat).ratio())
            r_pre = difflib.SequenceMatcher(None, v, nn[:len(v) + 1]).ratio() if len(v) >= 5 else 0
            s = max(r_full / full, r_pre / prefix)
            if s > score:
                best, score = n, s
    return best if score >= 1.0 else None
