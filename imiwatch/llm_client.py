"""OpenAI 互換の Chat Completions API を呼ぶ小さなクライアント。

OpenRouter・OpenAI・LM Studio・Ollama・vLLM など、`POST {base_url}/chat/completions` を
話すサーバーならどれでも使える。
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(RuntimeError):
    pass


def endpoint(base_url: str) -> str:
    """base URL から Chat Completions の URL を作る（末尾まで書かれていればそのまま使う）。"""
    url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or DEFAULT_BASE_URL)


def llm_config(settings: dict, model_key: str = "planner_model") -> tuple[str, str, str]:
    """(base_url, api_key, model) を設定から取り出す。"""
    base = settings.get("llm_base_url") or DEFAULT_BASE_URL
    key = settings.get("llm_api_key", "")
    model = (settings.get(model_key) or "").strip()
    if not model and is_openrouter(base):
        model = "openrouter/auto"
    return base, key, model


def is_configured(settings: dict, model_key: str = "planner_model") -> tuple[bool, str]:
    """使える状態か、使えない理由。"""
    base, key, model = llm_config(settings, model_key)
    if is_openrouter(base) and not key:
        return False, "設計役 LLM の API キーが設定されていません"
    if not model:
        return False, "設計役 LLM のモデル名が設定されていません"
    return True, ""


def chat_json(
    settings: dict,
    system: str,
    user_text: str,
    image_data_url: str | None = None,
    timeout: float = 90,
    model_key: str = "planner_model",
) -> Any:
    """LLM に JSON で答えさせ、パース済みのオブジェクトを返す。"""
    ok, why = is_configured(settings, model_key)
    if not ok:
        raise LLMError(why)
    base, key, model = llm_config(settings, model_key)
    content: Any
    if image_data_url:
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        content = user_text
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if is_openrouter(base):
        headers["X-Title"] = "imiwatch"

    url = endpoint(base)
    r = _post(url, headers, payload, timeout)
    # JSON モード（response_format）に対応していないサーバーもあるので、外して1回だけやり直す
    if r.status_code in (400, 422) and "response_format" in r.text.lower():
        payload.pop("response_format")
        r = _post(url, headers, payload, timeout)
    if r.status_code >= 400:
        raise LLMError(f"LLM（{url}）: HTTP {r.status_code}: {r.text[:500]}")
    try:
        msg = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"LLM の応答を読めませんでした: {r.text[:300]}") from e
    if isinstance(msg, list):  # 一部のサーバーは content を部品の配列で返す
        msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
    return parse_json_loose(msg)


def _post(url: str, headers: dict, payload: dict, timeout: float) -> requests.Response:
    try:
        return requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise LLMError(f"LLM（{url}）に接続できませんでした: {e}") from e


def parse_json_loose(text: str) -> Any:
    """コードブロックや前置き、思考タグが混ざった応答から JSON を取り出す。"""
    if isinstance(text, (dict, list)):
        return text
    s = (text or "").strip()
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S).strip()  # 推論モデルの思考部分
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM の応答が JSON として読めません: {s[:300]}") from e
    raise LLMError(f"LLM の応答に JSON がありません: {s[:300]}")
