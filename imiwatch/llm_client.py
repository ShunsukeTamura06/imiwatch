"""設計役の LLM を呼ぶ小さなクライアント。2つの形式に対応する。

- openai    : `POST {base_url}/chat/completions`（OpenRouter・OpenAI・LM Studio・Ollama・vLLM など）
- anthropic : `POST {base_url}/v1/messages`（Anthropic の Messages API と、その形式を話す社内ゲートウェイなど）
"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMError(RuntimeError):
    pass


FORMATS = ("openai", "anthropic")
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 4096


def endpoint(base_url: str, fmt: str = "openai") -> str:
    """base URL から呼び出し先の URL を作る（末尾まで書かれていればそのまま使う）。"""
    url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if fmt == "anthropic":
        if url.endswith("/messages"):
            return url
        if url.endswith("/v1"):
            return url + "/messages"
        return url + "/v1/messages"
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or DEFAULT_BASE_URL)


def api_format(settings: dict) -> str:
    fmt = (settings.get("llm_api_format") or "openai").strip()
    return fmt if fmt in FORMATS else "openai"


def llm_config(settings: dict, model_key: str = "planner_model") -> tuple[str, str, str]:
    """(base_url, api_key, model) を設定から取り出す。"""
    base = settings.get("llm_base_url") or DEFAULT_BASE_URL
    key = settings.get("llm_api_key", "")
    model = (settings.get(model_key) or "").strip()
    if not model and is_openrouter(base) and api_format(settings) == "openai":
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


def auth_headers(settings: dict, key: str) -> dict:
    """認証ヘッダー。auto なら形式に合わせる（OpenAI 互換は Bearer、Anthropic 形式は x-api-key）。"""
    if not key:
        return {}
    mode = (settings.get("llm_auth_header") or "auto").strip()
    if mode == "auto":
        mode = "x-api-key" if api_format(settings) == "anthropic" else "bearer"
    if mode == "x-api-key":
        return {"x-api-key": key}
    if mode == "both":
        return {"x-api-key": key, "Authorization": f"Bearer {key}"}
    return {"Authorization": f"Bearer {key}"}


def _split_data_url(data_url: str) -> tuple[str, str]:
    """data:image/jpeg;base64,xxxx → ("image/jpeg", "xxxx")"""
    head, _, data = data_url.partition(",")
    media = head[5:].split(";")[0] if head.startswith("data:") else "image/jpeg"
    return media or "image/jpeg", data


def build_request(settings: dict, system: str, user_text: str, image_data_url: str | None,
                  model: str) -> tuple[str, dict, dict]:
    """(url, headers, payload) を形式に合わせて組み立てる。"""
    fmt = api_format(settings)
    base, key, _ = llm_config(settings)
    headers = {"Content-Type": "application/json", **auth_headers(settings, key)}
    if fmt == "anthropic":
        content: list[dict] = []
        if image_data_url:
            media, data = _split_data_url(image_data_url)
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
        content.append({"type": "text", "text": user_text})
        headers["anthropic-version"] = ANTHROPIC_VERSION
        payload = {
            "model": model,
            "max_tokens": int(settings.get("llm_max_tokens") or ANTHROPIC_MAX_TOKENS),
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        return endpoint(base, "anthropic"), headers, payload

    oa_content: Any
    if image_data_url:
        oa_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        oa_content = user_text
    if is_openrouter(base):
        headers["X-Title"] = "imiwatch"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": oa_content},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    return endpoint(base, "openai"), headers, payload


def read_text(fmt: str, body: Any) -> str:
    """応答本文から、モデルが書いた文章を取り出す。"""
    if fmt == "anthropic":
        blocks = body.get("content") if isinstance(body, dict) else None
        if not isinstance(blocks, list):
            raise KeyError("content")
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    msg = body["choices"][0]["message"]["content"]
    if isinstance(msg, list):  # 一部のサーバーは content を部品の配列で返す
        msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
    return msg


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
    _, _, model = llm_config(settings, model_key)
    fmt = api_format(settings)
    url, headers, payload = build_request(settings, system, user_text, image_data_url, model)
    r = _post(url, headers, payload, timeout)
    # JSON モード（response_format）に対応していないサーバーもあるので、外して1回だけやり直す
    if fmt == "openai" and r.status_code in (400, 422) and "response_format" in r.text.lower():
        payload.pop("response_format")
        r = _post(url, headers, payload, timeout)
    if r.status_code >= 400:
        raise LLMError(f"LLM（{url}）: HTTP {r.status_code}: {r.text[:500]}")
    try:
        text = read_text(fmt, r.json())
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"LLM の応答を読めませんでした（形式の設定が合っているか確認してください）: {r.text[:300]}") from e
    return parse_json_loose(text)


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
