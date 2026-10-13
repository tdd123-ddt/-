# -*- coding: utf-8 -*-
"""
网页搜索（免费、无需 key）：用必应 cn.bing.com 抓取医院/医生的公开评价。
- 用 ensearch=1 保证结果相关性；
- 解码 Bing 跳转链接得到真实 URL；
- 相关性过滤：结果标题/摘要须含查询关键词，否则丢弃（避免返回无关垃圾）。
返回 [{title, url, snippet}]；失败/无相关结果时返回空列表（页面降级提示，不崩）。
"""
import re
import html
import base64
import requests

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _decode_bing_url(url: str) -> str:
    """把 Bing 的 /ck/a?...&u=a1<base64url> 跳转链接解码成真实 URL。"""
    url = html.unescape(url)              # &amp; -> &
    m = re.search(r"[?&]u=a1([^&]+)", url)
    if not m:
        return url
    try:
        b = m.group(1)
        pad = "=" * (-len(b) % 4)
        return base64.urlsafe_b64decode(b + pad).decode("utf-8", "ignore")
    except Exception:
        return url


def _query_terms(query: str):
    terms = [t for t in re.split(r"[\s,，、]+", query) if t]
    return terms or [query]


def web_search(query: str, max_results: int = 5, timeout: int = 12) -> list:
    try:
        r = requests.get("https://cn.bing.com/search",
                         params={"q": query, "ensearch": "1", "mkt": "zh-CN"},
                         headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
    except Exception:
        return []

    terms = _query_terms(query)
    results = []
    for item in re.findall(r'<li class="b_algo".*?</li>', r.text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not m:
            continue
        title = _strip(m.group(2))
        url = _decode_bing_url(m.group(1))
        sn = re.search(r'<p[^>]*>(.*?)</p>', item, re.S)
        snippet = _strip(sn.group(1)) if sn else ""

        # 相关性过滤：标题+摘要里至少要命中一个查询关键词
        hay = (title + snippet).lower()
        if not any(t.lower() in hay for t in terms if len(t) >= 2):
            continue

        results.append({"title": title, "url": url, "snippet": snippet[:200]})
        if len(results) >= max_results:
            break
    return results


def llm_evaluate(hospital: str, disease: str) -> str:
    """用 LLM（DeepSeek）基于公开知识评价某医院在治疗某病种方面的水平/口碑/专家。

    网页抓取对不知名医院常返回垃圾，故用模型知识兜底。返回文本；无 key 或失败返回空串。
    """
    try:
        from modules.a.config import api_config
        import anthropic
        cfg = api_config()
        if not cfg.get("api_key"):
            return ""
        client = anthropic.Anthropic(api_key=cfg["api_key"], base_url=cfg["base_url"] or None)
        prompt = (f"请客观、简洁地评价「{hospital}」在治疗「{disease}」方面的综合水平、"
                  f"口碑和知名专家（3~5 句话）。基于你的公开知识、可能过时；不要编造具体数字或排名。")
        resp = client.messages.create(
            model=cfg["model"], max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"temperature": 0.2},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""
    except Exception:
        return ""
