from __future__ import annotations

from .base import Judge, STATE_TEXT


class PerplexityJudge(Judge):
    """Perplexity の Decisions API。

    画像は state を配列にして、テキストと image_url パーツを並べて渡す。
    """

    name = "Perplexity"
    URL = "https://api.perplexity.ai/v1/decisions"

    def build_payload(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        if image_data_url:
            state = [state_text, {"type": "image_url", "image_url": {"url": image_data_url}}]
        else:
            state = state_text
        return {
            "model": self.settings.get("perplexity_model") or "pplx-decider-v1.1-27b",
            "state": state,
            "questions": questions,
        }

    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        key = self._require(self.settings.get("perplexity_api_key", ""), "Perplexity の API キー")
        data = self._post(
            self.URL,
            {"Authorization": f"Bearer {key}"},
            self.build_payload(image_data_url, questions, state_text),
        )
        return self._extract_answers(data)
