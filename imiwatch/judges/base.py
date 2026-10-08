from __future__ import annotations

import time
from typing import Any

import requests


class JudgeError(RuntimeError):
    pass


STATE_TEXT = "画像は、ユーザーが画面上で選んだ範囲のスクリーンショットです。問いには画像の内容だけで答えてください。"


class Judge:
    """判定モデルの共通インターフェース。"""

    name = "base"
    supports_images = True

    def __init__(self, settings: dict):
        self.settings = settings
        self.timeout = float(settings.get("request_timeout", 60))

    def judge(self, image_data_url: str | None, questions: dict, state_text: str = STATE_TEXT) -> dict:
        """SystemOne 形式の answers（問いID → 答え）を返す。"""
        raise NotImplementedError

    def judge_timed(self, image_data_url, questions, state_text=STATE_TEXT) -> tuple[dict, int]:
        t0 = time.perf_counter()
        answers = self.judge(image_data_url, questions, state_text)
        return answers, int((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------
    def _post(self, url: str, headers: dict, payload: dict) -> Any:
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise JudgeError(f"{self.name}: 通信に失敗しました: {e}") from e
        if r.status_code >= 400:
            body = r.text[:500]
            raise JudgeError(f"{self.name}: HTTP {r.status_code}: {body}")
        try:
            return r.json()
        except ValueError as e:
            raise JudgeError(f"{self.name}: 応答が JSON ではありません: {r.text[:200]}") from e

    @staticmethod
    def _require(value: str, label: str) -> str:
        if not value:
            raise JudgeError(f"{label} が設定されていません（設定画面で入力してください）")
        return value

    @staticmethod
    def _extract_answers(data: Any) -> dict:
        """各社の応答から answers を取り出す（Cloudflare は result に包まれている）。"""
        if isinstance(data, dict):
            if isinstance(data.get("answers"), dict):
                return data["answers"]
            res = data.get("result")
            if isinstance(res, dict) and isinstance(res.get("answers"), dict):
                return res["answers"]
            if data.get("success") is False:
                raise JudgeError(f"API がエラーを返しました: {data.get('errors')}")
        raise JudgeError(f"応答に answers がありません: {str(data)[:300]}")
