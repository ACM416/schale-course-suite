# -*- coding: utf-8 -*-
import html
import json
import os
import re
import threading
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

import requests

from core.paths import get_data_dir

DEFAULT_AI_CONFIG: Dict[str, Any] = {
    "provider": "deepseek",
    "api_base": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "enable_search": True,
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_config_lock = threading.Lock()


def _config_path() -> str:
    return str(get_data_dir() / "shared_ai_config.json")


def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}****{key[-4:]}"


def load_ai_config() -> Dict[str, Any]:
    with _config_lock:
        cfg = dict(DEFAULT_AI_CONFIG)
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k in DEFAULT_AI_CONFIG:
                        if k in data and data[k] is not None:
                            cfg[k] = data[k]
            except Exception:
                pass
        return cfg


def get_public_ai_config() -> Dict[str, Any]:
    cfg = load_ai_config()
    raw_key = str(cfg.get("api_key") or "").strip()
    return {
        "provider": str(cfg.get("provider") or "deepseek"),
        "api_base": str(cfg.get("api_base") or DEFAULT_AI_CONFIG["api_base"]),
        "model": str(cfg.get("model") or DEFAULT_AI_CONFIG["model"]),
        "enable_search": bool(cfg.get("enable_search", True)),
        "has_key": bool(raw_key),
        "api_key_masked": _mask_key(raw_key),
    }


def save_ai_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    with _config_lock:
        current = dict(DEFAULT_AI_CONFIG)
        path = _config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    current.update({k: data[k] for k in DEFAULT_AI_CONFIG if k in data})
            except Exception:
                pass

        if "provider" in payload and payload["provider"] is not None:
            current["provider"] = str(payload["provider"]).strip() or "custom"
        if "api_base" in payload and payload["api_base"] is not None:
            base = str(payload["api_base"]).strip().rstrip("/")
            if base:
                current["api_base"] = base
        if "model" in payload and payload["model"] is not None:
            model = str(payload["model"]).strip()
            if model:
                current["model"] = model
        if "enable_search" in payload:
            current["enable_search"] = bool(payload["enable_search"])

        if payload.get("clear_key"):
            current["api_key"] = ""
        else:
            new_key = payload.get("api_key")
            if isinstance(new_key, str) and new_key.strip():
                current["api_key"] = new_key.strip()

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    return get_public_ai_config()


def _query_baike(lemma_key: str) -> List[Dict[str, str]]:
    clean_key = lemma_key.strip()
    if not clean_key or len(clean_key) > 24:
        return []
    try:
        r = requests.get(
            "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi",
            params={"scope": 103, "format": "json", "appid": 379020, "bk_key": clean_key},
            headers={"User-Agent": USER_AGENT},
            timeout=5,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, dict) or not data.get("title"):
            return []
        abstract = _clean_text(str(data.get("abstract") or ""))
        desc = _clean_text(str(data.get("desc") or ""))
        snippet = abstract or desc
        if not snippet:
            return []
        if desc and abstract and desc not in abstract:
            snippet = f"[{desc}] {abstract}"
        url = str(data.get("url") or data.get("wapUrl") or "").strip()
        if not url and data.get("id"):
            url = f"https://baike.baidu.com/item/{requests.utils.quote(str(data['title']))}/{data['id']}"
        return [
            {
                "title": f"{data['title']}（百度百科）",
                "url": url or f"https://baike.baidu.com/item/{requests.utils.quote(clean_key)}",
                "snippet": snippet,
                "source": "百度百科",
            }
        ]
    except Exception:
        return []


def web_search(query: str, max_results: int = 6, baike_key: str = "") -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen_urls = set()

    short_key = (baike_key or query).strip()
    if len(short_key) <= 24:
        for item in _query_baike(short_key):
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                results.append(item)

    try:
        r = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "format": "rss"},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
            timeout=10,
        )
        if r.status_code == 200 and r.content:
            root = ET.fromstring(r.content)
            for item in root.findall("./channel/item"):
                title = _clean_text(item.findtext("title") or "")
                link = (item.findtext("link") or "").strip()
                desc = _clean_text(item.findtext("description") or "")
                if not title or not link or link in seen_urls:
                    continue
                seen_urls.add(link)
                results.append(
                    {
                        "title": title,
                        "url": link,
                        "snippet": desc or "暂无摘要",
                        "source": "必应搜索",
                    }
                )
                if len(results) >= max_results:
                    break
    except Exception:
        pass

    return results[:max_results]


def _build_search_query(query: str, course_name: str = "") -> str:
    lines = [ln.strip() for ln in (query or "").splitlines() if ln.strip()]
    stem = lines[0] if lines else ""
    if len(stem) < 18 and len(lines) > 1:
        stem = " ".join(lines[:2])
    stem = re.sub(r"^\s*(?:\d+[.、)）]|[（(]\d+[）)])\s*", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip()

    clean_course = re.sub(r"[（(][^）)]*[）)]", "", course_name or "").strip()
    if len(stem) < 8 and clean_course and clean_course not in stem:
        combined = f"{stem} {clean_course}"
    else:
        combined = stem
    return combined[:100].strip()


def _format_search_context(results: List[Dict[str, str]]) -> str:
    if not results:
        return "（本次未检索到外部网页摘要，请结合学科基础知识作答。）"
    blocks = []
    for idx, item in enumerate(results, 1):
        blocks.append(
            f"[{idx}] 标题: {item.get('title', '')}\n"
            f"来源: {item.get('source', '网络')}\n"
            f"摘要: {item.get('snippet', '')}\n"
            f"链接: {item.get('url', '')}"
        )
    return "\n\n".join(blocks)


def _call_openai_compatible(
    cfg: Dict[str, Any],
    query: str,
    mode: str,
    course_name: str,
    search_results: List[Dict[str, str]],
) -> str:
    api_base = str(cfg.get("api_base") or "").strip().rstrip("/")
    api_key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("model") or "deepseek-chat").strip()

    if not api_base:
        raise ValueError("AI 接口地址 (api_base) 不能为空")
    if not api_key:
        raise ValueError("尚未配置 AI API Key")

    endpoint = api_base if api_base.endswith("/chat/completions") else f"{api_base}/chat/completions"

    if mode == "discussion":
        system_prompt = (
            "你是一位耐心的大学课程学习与讨论导师。请结合联网检索到的参考资料，"
            "针对用户提供的课程讨论题目，提供：\n"
            "1. **核心议题与背景知识点**（简明扼要）；\n"
            "2. **2~3 个不同角度的思考观点与论据/实例**；\n"
            "3. **发言结构提纲**（方便学生用自己的语言整理回复）。\n"
            "注意区分确定事实与推测内容，如果联网资料包含相关事实可直接引用说明。"
        )
    else:
        system_prompt = (
            "你是一位严谨的大学课程学习辅导老师。请结合联网检索到的参考资料，"
            "针对用户发来的练习/测验题目进行深入讲解：\n"
            "1. **明确核心考点**（涉及的定义、原理或规律）；\n"
            "2. **逐项/分步解析**（若有选项，请逐一分析对错原因；若是简答/计算，给出完整推导逻辑）；\n"
            "3. **结论与易错点提示**（提醒常见混淆概念，帮助学生真正理解）。\n"
            "请以清晰的 Markdown 结构化排版回答。"
        )

    search_context = _format_search_context(search_results)
    course_line = f"所属课程：{course_name}\n" if course_name else ""
    user_prompt = (
        f"{course_line}"
        f"学习模式：{'讨论思路整理' if mode == 'discussion' else '练习题目解析'}\n\n"
        f"题目/讨论内容：\n{query.strip()}\n\n"
        f"联网检索参考资料：\n{search_context}"
    )

    resp = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.4,
        },
        timeout=45,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"AI 接口返回 HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"AI 接口未返回有效内容: {json.dumps(data, ensure_ascii=False)[:200]}")

    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("AI 返回内容为空")
    return content


def ask_assistant(
    query: str,
    mode: str = "exercise",
    course_name: str = "",
    use_search: bool = True,
) -> Dict[str, Any]:
    clean_query = (query or "").strip()
    if not clean_query:
        return {
            "ok": False,
            "ai_enabled": False,
            "model": "",
            "analysis": "",
            "search_results": [],
            "search_query": "",
            "warning": "请输入题目或讨论话题内容",
        }

    norm_mode = "discussion" if mode == "discussion" else "exercise"
    search_query = _build_search_query(clean_query, course_name=course_name)
    search_results = web_search(search_query, max_results=6, baike_key=clean_query) if use_search else []

    cfg = load_ai_config()
    api_key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("model") or "")
    analysis = ""
    warning = ""
    ai_enabled = bool(api_key)

    if ai_enabled:
        try:
            analysis = _call_openai_compatible(cfg, clean_query, norm_mode, course_name, search_results)
        except Exception as e:
            warning = f"AI 解析调用失败（已保留联网检索结果）：{e}"
    else:
        warning = "当前未配置 AI API Key，已展示免费联网检索结果；如需深度解析与讨论提纲，请在上方设置任意兼容接口 Key。"

    return {
        "ok": True,
        "ai_enabled": ai_enabled and bool(analysis),
        "model": model if ai_enabled else "",
        "analysis": analysis,
        "search_results": search_results,
        "search_query": search_query,
        "warning": warning,
    }
