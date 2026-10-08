"""OpenRouter（OpenAI 互換の Chat Completions）を呼ぶ小さなクライアント。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    pass


def chat_json(
    api_key: str,
    model: str,
    system: str,
    user_text: str,
    image_data_url: str | None = None,
    timeout: float = 90,
) -> Any:
    """LLM に JSON で答えさせ、パース済みのオブジェクトを返す。"""
    if not api_key:
        raise LLMError("OpenRouter の API キーが設定されていません")
    content: Any
    if image_data_url:
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    else:
        content = user_text
    payload = {
        "model": model or "openrouter/auto",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Title": "imiwatch",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise LLMError(f"OpenRouter への通信に失敗しました: {e}") from e
    if r.status_code >= 400:
        raise LLMError(f"OpenRouter: HTTP {r.status_code}: {r.text[:500]}")
    try:
        text = r.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"OpenRouter の応答を読めませんでした: {r.text[:300]}") from e
    return parse_json_loose(text)


def parse_json_loose(text: str) -> Any:
    """コードブロックや前置きが混ざった応答から JSON を取り出す。"""
    if isinstance(text, (dict, list)):
        return text
    s = (text or "").strip()
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
