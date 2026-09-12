"""web_search 类 adapter：keyless 联网搜索 + 正文抓取 + 事实抽取。

通道分层（对上游绑定同构，输入 query → 输出带溯源的事实卡）：
- keyless（默认，零凭据）：百度（中文相关性最佳）→ 搜狗 → Bing RSS →
  DDG HTML 依次级联，单通道故障自动降级
- browser（可选兜底）：playwright 连本机 Edge（channel=msedge，不另下浏览器），
  仅当 keyless 抓正文被拒（403/验证码/空文本）时启用
- api（预留）：settings.web_search.endpoint 配置了兼容端点时优先走
  （POST {query, top_k} → {results:[{title,url,snippet,date}]}），方便日后插付费 API

产物：
- facts：LLM 从正文抽取的定量事实，沿用 rag 的"抽取即对账"——数字必须能在
  该页原文中找到，否则丢弃；source=页面标题+URL，as_of=页面发布日期（尽力
  识别，退化"检索时点"）
- collections["web_pages"]：每页一行摘要（标题/日期/URL/一句话要点），供
  大纲/分节引用定性信息

结果缓存：data/cache/web_search/<sha>.json（TTL 默认 6h）——同一查询当天
多次运行不重复打搜索与抓取。
"""

import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from datalayer.adapters.base import AdapterResult, SourceAdapter
from datalayer.settings import settings

_EXTRACT_SYSTEM = """你从网页正文中抽取定量事实与一句话要点。要求：
- facts：只抽取正文明确出现的数字，禁止计算、换算或补全；每个事实给出
  name（事实名，含主体与时间口径）、value（数值，保留原文精度）、
  unit（单位，原文没有则空串）
- digest：一句话（≤60字）概括本页与查询最相关的信息
正文中没有定量事实时 facts 输出空列表。只输出 JSON：
{"facts": [{"name": "...", "value": 0, "unit": "..."}], "digest": "..."}"""

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_DATE_RES = [re.compile(p) for p in (
    r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
    r"(20\d{2})[-/年](\d{1,2})(?:月)?",
)]


def _cache_path(key: str) -> Path:
    return settings.resolve(
        f"data/cache/web_search/{hashlib.sha256(key.encode()).hexdigest()[:16]}.json")


def _cache_get(key: str, ttl_h: float) -> dict | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < ttl_h * 3600:
            return data
    except Exception:  # noqa: BLE001 —— 缓存损坏当未命中
        return None
    return None


def _cache_put(key: str, payload: dict) -> None:
    p = _cache_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload["_ts"] = time.time()
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _api_search(query: str, top_k: int) -> list[dict[str, Any]] | None:
    """api 后端（预留）：settings.web_search.endpoint 存在则优先
    （POST {query, top_k} → {results:[{title,url,snippet,date}]}），日后插付费 API 不改上游。"""
    endpoint = (settings.web_search or {}).get("endpoint")
    if not endpoint:
        return None
    from datalayer.sources.base import post_json
    data = post_json(endpoint, {"query": query, "top_k": top_k})
    return [{"title": r.get("title", ""), "url": r.get("url", ""),
             "snippet": r.get("snippet", ""), "date": r.get("date", "")}
            for r in data.get("results", [])][:top_k]


def _bing_rss(query: str, count: int) -> list[dict[str, Any]]:
    from datalayer.sources.base import get_text
    xml = get_text("https://cn.bing.com/search",
                   params={"q": query, "format": "rss", "count": count},
                   referer="https://cn.bing.com/")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
        if title and link.startswith("http"):
            out.append({"title": title, "url": link, "snippet": desc[:300],
                        "date": ""})
    return out


def _baidu_html(query: str, count: int) -> list[dict[str, Any]]:
    """百度网页搜索（中文相关性最好的 keyless 通道）；返回的 link 是跳转链接，
    抓正文时随 302 解析到真实站点。"""
    from datalayer.sources.base import get_text
    html = get_text("https://www.baidu.com/s",
                    params={"wd": query, "rn": max(count, 10)},
                    referer="https://www.baidu.com/")
    if "百度安全验证" in html:
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for h3 in soup.select("h3")[:count * 2]:
        a = h3.find("a", href=True)
        if not a:
            continue
        url = a["href"]
        title = a.get_text(" ", strip=True)
        if title and url.startswith("http"):
            out.append({"title": title, "url": url, "snippet": "", "date": ""})
    return out


def _sogou_html(query: str, count: int) -> list[dict[str, Any]]:
    """搜狗网页搜索（中文第二通道）；link 为站内相对跳转，补全域名。"""
    from datalayer.sources.base import get_text
    html = get_text("https://www.sogou.com/web", params={"query": query},
                    referer="https://www.sogou.com/")
    if "验证码" in html or "antispider" in html.lower():
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for h3 in soup.select("h3")[:count * 2]:
        a = h3.find("a", href=True)
        if not a:
            continue
        url = a["href"]
        if url.startswith("/link?"):
            url = "https://www.sogou.com" + url
        title = a.get_text(" ", strip=True)
        if title and url.startswith("http"):
            out.append({"title": title, "url": url, "snippet": "", "date": ""})
    return out


def _ddg_html(query: str, count: int) -> list[dict[str, Any]]:
    """DDG HTML 兜底：国内网络常态超时——8s 打不通就放弃，不算失败。"""
    from datalayer.sources.base import get_text
    try:
        html = get_text("https://html.duckduckgo.com/html/",
                        params={"q": query}, referer="https://duckduckgo.com/",
                        timeout=8)
    except Exception:  # noqa: BLE001
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a.result__a")[:count]:
        href = a.get("href") or ""
        if href.startswith("//duckduckgo.com/l/?uddg="):
            from urllib.parse import unquote, urlparse, parse_qs
            try:
                href = unquote(parse_qs(urlparse("https:" + href).query)["uddg"][0])
            except Exception:  # noqa: BLE001
                continue
        if href.startswith("http"):
            out.append({"title": a.get_text(" ", strip=True), "url": href,
                        "snippet": "", "date": ""})
    return out


def _search(query: str, top_k: int) -> list[dict[str, Any]]:
    """通道级联：api（预留）→ 百度 → 搜狗 → Bing RSS → DDG。
    通道命中即停止叠加；全失败返回空（上层报错）。"""
    results = _api_search(query, top_k)
    if results is None:
        for chan in (_baidu_html, _sogou_html):
            try:
                results = chan(query, top_k * 2)
            except Exception:  # noqa: BLE001 —— 单通道故障降级
                results = []
            if len(results) >= 3:
                break
        if len(results) < 3:
            try:
                results = _bing_rss(query, max(top_k * 3, 12))
            except Exception:  # noqa: BLE001
                results = []
        if len(results) < 3:
            try:
                results = _ddg_html(query, top_k * 2)
            except Exception:  # noqa: BLE001
                results = []
    # 去重（URL 与标题各留一份最好的）+ 排除非 http
    seen, out = set(), []
    for r in results:
        key = r["url"].split("#")[0]
        if key in seen or not r["url"].startswith("http"):
            continue
        # 过滤搜索引擎自身的聚合页
        host = re.sub(r"^www\.", "", r["url"].split("/")[2])
        if host in ("cn.bing.com", "www.bing.com", "duckduckgo.com"):
            continue
        seen.add(key)
        out.append(r)
    return out[:top_k]


def _page_date(html: str) -> str:
    """尽力识别页面发布日期：HTML 里的日期串取最新合理的一个。"""
    now = datetime.now()
    best = ""
    for pat in _DATE_RES:
        for m in pat.finditer(html):
            y, mo = int(m.group(1)), int(m.group(2))
            d = int(m.group(3)) if m.lastindex and m.lastindex >= 3 else 1
            try:
                dt = datetime(y, mo, d)
            except ValueError:
                continue
            if dt <= now and dt >= now - timedelta(days=5 * 365):
                if not best or dt.strftime("%Y-%m-%d") > best:
                    best = dt.strftime("%Y-%m-%d")
    return best


def _fetch_text_http(url: str) -> tuple[str, str, str]:
    """requests + bs4 抽正文；返回 (正文文本, 识别到的日期, 跳转后最终URL)。"""
    import requests
    from bs4 import BeautifulSoup
    resp = requests.get(url, timeout=12, headers={
        "User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.baidu.com/"}, verify=False, allow_redirects=True)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    html = resp.text
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
    return text, _page_date(html), str(resp.url)


def _fetch_text_browser(url: str) -> str:
    """playwright 连本机 Edge（不另下浏览器）：JS 页/反爬页兜底。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page(user_agent=_UA)
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            return page.evaluate("document.body ? document.body.innerText : ''")
        finally:
            browser.close()


def _fetch_page(url: str, browser_fallback: bool) -> tuple[str, str, str, str]:
    """抓单页正文；返回 (文本, 日期, via, 最终URL)。文本过短/被拒时走浏览器。"""
    try:
        text, date, final_url = _fetch_text_http(url)
        if len(text) >= 200:
            return text, date, "http", final_url
        err = "text too short"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}"
    if browser_fallback:
        try:
            text = _fetch_text_browser(url)
            if len(text) >= 200:
                return text, "", "browser", url
        except Exception:  # noqa: BLE001
            pass
    raise RuntimeError(f"抓取失败（{err}）")


def _number_in_text(value: float, text: str) -> bool:
    from datalayer.adapters.rag import _number_in_text as _nit
    return _nit(value, text)


class WebSearchAdapter(SourceAdapter):
    key = "web_search"
    kind = "web"
    default_reliability = "retrieved"
    summary = ("联网搜索（免key）：Bing RSS 主通道 + Edge 兜底，LLM 抽数"
               "并回验原文，事实卡带 URL 与日期")
    param_schema = [
        {"k": "query", "label": "搜索词", "ph": "如：高纯石英矿 新矿种 2025",
         "hint": "建议带年份/机构名提高时效命中率", "required": True, "type": "str"},
        {"k": "top_k", "label": "结果条数", "ph": "6", "hint": "搜索结果保留数",
         "required": False, "type": "number"},
        {"k": "fetch_pages", "label": "抓取页数", "ph": "3",
         "hint": "实际抓正文的前几条", "required": False, "type": "number"},
        {"k": "browser_fallback", "label": "浏览器兜底", "ph": "true",
         "hint": "HTTP 抓取被拒时用本机 Edge 重试", "required": False,
         "type": "bool"},
        {"k": "cache_ttl_h", "label": "缓存(小时)", "ph": "6",
         "hint": "同查询结果复用时长", "required": False, "type": "number"},
    ]
    ctx_keys: list[str] = []

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        from pipeline.llm import chat_json, tier_for

        query = params["query"]
        top_k = int(params.get("top_k", 6))
        fetch_n = int(params.get("fetch_pages", 3))
        browser_fallback = str(params.get("browser_fallback", "true")).lower() \
            not in ("0", "false", "no")
        ttl = float(params.get("cache_ttl_h", 6))

        cache_key = f"{query}|{top_k}|{fetch_n}"
        cached = _cache_get(cache_key, ttl)
        if cached is not None:
            pages = cached["pages"]
        else:
            results = _search(query, top_k)
            if not results:
                raise RuntimeError(f"搜索无结果：{query}")

            def _handle(r: dict[str, Any]) -> dict[str, Any]:
                """单页：抓正文 → LLM 抽取（抽取即对账）。页面间独立，并发执行。"""
                try:
                    text, date, via, final_url = _fetch_page(r["url"],
                                                             browser_fallback)
                except Exception as e:  # noqa: BLE001 —— 单页失败跳过
                    return {"title": r["title"], "url": r["url"],
                            "date": r.get("date", ""), "via": "fail",
                            "error": str(e)[:120], "digest": "", "facts": []}
                out = chat_json(
                    _EXTRACT_SYSTEM,
                    f"【查询】{query}\n【网页】{r['title']}\n"
                    f"【正文】\n{text[:3500]}",
                    schema_hint="只输出一个合法 JSON 对象。",
                    tier=tier_for("extract"))
                kept, dropped = [], 0
                for f in out.get("facts") or []:
                    try:
                        value = float(f["value"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not _number_in_text(value, text):
                        dropped += 1      # 原文找不到该数字 → 抽取幻觉，丢弃
                        continue
                    kept.append(f)
                return {"title": r["title"], "url": final_url,
                        "date": r.get("date", "") or date
                        or (_page_date(text) if text else ""),
                        "via": via,
                        "digest": str(out.get("digest", ""))[:120],
                        "facts": kept,
                        "n_dropped": dropped}

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(fetch_n, 4)) as ex:
                pages = list(ex.map(_handle, results[:fetch_n]))
            _cache_put(cache_key, {"pages": pages})

        facts: list[dict[str, Any]] = []
        warnings: list[str] = []
        for pi, page in enumerate(pages):
            if page.get("via") == "fail":
                warnings.append(f"web_search 页面抓取失败：{page['title'][:40]}"
                                f"（{page.get('error', '')}）")
                continue
            if page.get("n_dropped"):
                warnings.append(f"web_search 抽取丢弃 {page['n_dropped']} 个"
                                f"原文无出处的数字（{page['title'][:30]}）")
            src = f"web:{page['title'][:40]} {page['url']}"
            for j, f in enumerate(page.get("facts") or []):
                try:
                    value = float(f["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                facts.append({
                    "id": f"web.{pi:02d}.{j:02d}",
                    "name": str(f.get("name", ""))[:80],
                    "value": value, "unit": str(f.get("unit", "")),
                    "source": src,
                    "as_of": page.get("date") or "检索时点",
                    "url": page["url"]})
        collections = {"web_pages": [
            {k: page.get(k, "") for k in ("title", "url", "date", "via",
                                          "digest", "error")}
            for page in pages]}
        return AdapterResult(facts=facts, warnings=warnings,
                             collections=collections)
