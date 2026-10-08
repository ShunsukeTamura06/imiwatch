from __future__ import annotations

from .base import Judge, STATE_TEXT


class ClefJudge(Judge):
    """Cloudflare Workers AI の Clef / Clef-flash。

    画像は Clef 独自の `images` 欄に data URL で入れる（最大4枚・各4MiB）。
    """

    def __init__(self, settings: dict, model: str = "clef-flash"):
        super().__init__(settings)
        self.model = model
        self.name = f"Cloudflare {model}"

    def build_payload(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        payload = {"model": self.model, "state": state_text, "questions": questions}
        if image_data_url:
            payload["images"] = [image_data_url]
        return payload

    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        account = self._require(self.settings.get("cloudflare_account_id", ""), "Cloudflare のアカウントID")
        token = self._require(self.settings.get("cloudflare_api_token", ""), "Cloudflare の API トークン")
        url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/{self.model}"
        data = self._post(
            url,
            {"Authorization": f"Bearer {token}"},
            self.build_payload(image_data_url, questions, state_text),
        )
        return self._extract_answers(data)
