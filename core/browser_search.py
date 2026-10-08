"""Открывает поисковую выдачу по прямой просьбе, без модели."""

from urllib.parse import urlencode

from core import launcher


def open_query(query: str) -> dict:
    query = " ".join(str(query or "").split()).strip()
    if not query:
        return {"ok": False, "text": "Не сказано, что искать."}
    url = "https://www.google.com/search?" + urlencode({"q": query})
    try:
        ok, why = launcher.open_web(url)
    except Exception as exc:
        ok, why = False, str(exc)
    return {"ok": bool(ok), "query": query, "url": url,
            "text": "Открыла поиск в браузере." if ok else "Не получилось открыть поиск в браузере.",
            "error": "" if ok else why}
