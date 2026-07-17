import os, json, re, httpx
from html import unescape
from joki.display import _Spinner
from joki.config import _CONFIG_PATH

try:
    import html2text
    _HAS_HTML2TEXT = True
except ImportError:
    _HAS_HTML2TEXT = False


def _get_config_key(key_name):
    val = os.environ.get(key_name.upper()) or os.environ.get(f"JOKI_{key_name.upper()}")
    if val:
        return val
    try:
        data = json.loads(_CONFIG_PATH.read_text())
        return data.get(key_name, "") or ""
    except Exception:
        return ""


def _get_tinyfish_key():
    return _get_config_key("tinyfish_api_key")


def _get_brave_key():
    val = os.environ.get("BRAVE_API_KEY") or os.environ.get("JOKI_BRAVE_KEY")
    if val:
        return val
    try:
        data = json.loads(_CONFIG_PATH.read_text())
        return data.get("brave_api_key", "") or ""
    except Exception:
        return ""


def _fetch_httpx(url, timeout=30, params=None):
    return httpx.get(
        url, params=params, timeout=timeout, follow_redirects=True, verify=False,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
    )


def _fetch_via_tinyfish(url):
    key = _get_tinyfish_key()
    if not key:
        return None
    try:
        r = httpx.post(
            "https://api.fetch.tinyfish.ai",
            json={"urls": [url], "format": "markdown"},
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            verify=False,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or data
        if isinstance(results, list) and results:
            md = results[0].get("markdown", "") or results[0].get("content", "")
            if md and md.strip():
                return md.strip()
        return None
    except Exception:
        return None


def handle_web_fetch(args):
    url = args.get("url", "")
    force_text = args.get("format", "markdown") == "text"
    if not url:
        return "Error: URL wajib diisi. Contoh: web_fetch(url=\"https://example.com\")"

    with _Spinner("Mengambil konten web"):
        if not force_text and _HAS_HTML2TEXT:
            md = _fetch_via_tinyfish(url)
            if md:
                return md

        try:
            r = _fetch_httpx(url)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    return json.dumps(r.json(), indent=2)
                except Exception:
                    pass
            if force_text or not _HAS_HTML2TEXT or not r.text.strip():
                return r.text or "(konten kosong)"
            h = html2text.HTML2Text()
            h.body_width = 0
            h.ignore_links = False
            h.ignore_images = False
            h.ignore_emphasis = False
            h.skip_internal_links = True
            h.protect_links = True
            md = h.handle(r.text)
            return md.strip() or "(konten kosong)"
        except httpx.TimeoutException:
            return f"Error: Timeout mengambil {url} (30 detik)"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} untuk {url}"
        except httpx.InvalidURL:
            return f"Error: URL tidak valid: {url}"
        except Exception as e:
            return f"Error: Gagal mengambil {url}: {e}"


def _search_tavily(query, max_results):
    key = _get_config_key("tavily_api_key")
    if not key:
        return None
    try:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"query": query, "search_depth": "basic", "max_results": min(max_results, 20), "include_answer": False},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            verify=False, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            content = unescape(res.get("content", ""))
            lines.append(f"- [{title}]({url})")
            if content:
                lines.append(f"  {content}")
        return "\n".join(lines)
    except Exception:
        return None


def _search_tinyfish(query, max_results):
    key = _get_tinyfish_key()
    if not key:
        return None
    try:
        r = httpx.get(
            "https://api.search.tinyfish.ai",
            params={"query": query},
            headers={"X-API-Key": key},
            verify=False,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            snippet = unescape(res.get("snippet", ""))
            lines.append(f"- [{title}]({url})")
            if snippet:
                lines.append(f"  {snippet}")
        return "\n".join(lines)
    except Exception:
        return None


def _search_ddg(query, max_results):
    try:
        r = _fetch_httpx("https://html.duckduckgo.com/html/", params={"q": query})
    except Exception:
        return None
    if not r.text.strip():
        return None
    results = []
    for block in re.split(r'<div class="result[^"]*"[^>]*>', r.text)[1:]:
        if len(results) >= max_results:
            break
        title_m = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet_m = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', block, re.DOTALL)
        url_m = re.search(r'href="(https?://[^"]+)"', block)
        if title_m and url_m:
            title = unescape(re.sub(r'<[^>]+>', '', title_m.group(1))).strip()
            url = unescape(url_m.group(1))
            snippet = ""
            if snippet_m:
                snippet = unescape(re.sub(r'<[^>]+>', '', snippet_m.group(1))).strip()
            results.append(f"- [{title}]({url})")
            if snippet:
                results.append(f"  {snippet}")
    return "\n".join(results) if results else None


def _search_google(query, max_results):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        r = httpx.get(
            "https://www.google.com/search",
            params={"q": query, "num": min(max_results, 10), "hl": "en"},
            headers=headers, verify=False, timeout=15, follow_redirects=True,
        )
        r.raise_for_status()
    except Exception:
        return None
    html = r.text
    if "enablejs" in html[:2000].lower():
        return None
    results = []
    for block in re.split(r'<h3[^>]*>', html)[1:]:
        if len(results) >= max_results:
            break
        title_end = block.find("</h3>")
        if title_end == -1:
            continue
        title = unescape(re.sub(r"<[^>]+>", "", block[:title_end]).strip())
        url_m = re.search(r'href="(/url\?q=([^"&]+))"', block)
        if url_m:
            result_url = unescape(url_m.group(2))
        else:
            direct = re.search(r'href="(https?://[^"&]+)"', block)
            result_url = unescape(direct.group(1)) if direct else ""
        if not result_url:
            continue
        snippet = ""
        snip_m = re.search(r'<div[^>]*style="-webkit-line-clamp[^>]*>(.*?)</div>', block, re.DOTALL)
        if not snip_m:
            snip_m = re.search(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
        if snip_m:
            snippet = unescape(re.sub(r"<[^>]+>", "", snip_m.group(1)).strip())[:200]
        results.append(f"- [{title}]({result_url})")
        if snippet:
            results.append(f"  {snippet}")
    return "\n".join(results) if results else None


def _search_brave(query, max_results, api_key):
    if not api_key:
        return None
    try:
        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max_results, 20)},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key},
            verify=False, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return None
        lines = []
        for res in results[:max_results]:
            title = unescape(res.get("title", ""))
            url = res.get("url", "")
            desc = unescape(res.get("description", ""))
            lines.append(f"- [{title}]({url})")
            if desc:
                lines.append(f"  {desc}")
        return "\n".join(lines)
    except Exception:
        return None


def handle_web_search(args):
    query = args.get("query", "")
    source = args.get("source", "auto")
    max_results = min(args.get("max_results", 5), 20)

    if not query:
        return "Error: Query wajib diisi."

    with _Spinner(f"Mencari: {query}"):
        if source == "tinyfish":
            result = _search_tinyfish(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via TinyFish)"

        if source == "tavily":
            result = _search_tavily(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Tavily)"

        if source == "brave":
            key = _get_brave_key()
            if not key:
                return "Error: Brave API key tidak ditemukan"
            result = _search_brave(query, max_results, key)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Brave)"

        if source == "google":
            result = _search_google(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via Google)"

        if source == "duckduckgo":
            result = _search_ddg(query, max_results)
            if result:
                return result
            return f"(tidak ada hasil untuk '{query}' via DuckDuckGo)"

        result = _search_tinyfish(query, max_results)
        if result:
            return result
        result = _search_tavily(query, max_results)
        if result:
            return result
        key = _get_brave_key()
        if key:
            result = _search_brave(query, max_results, key)
            if result:
                return result
        result = _search_google(query, max_results)
        if result:
            return result
        result = _search_ddg(query, max_results)
        if result:
            return result
        return f"(tidak ada hasil untuk '{query}')"
