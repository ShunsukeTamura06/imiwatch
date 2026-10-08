from __future__ import annotations

from .base import Judge, JudgeError, STATE_TEXT


class SystemOneJudge(Judge):
    """SystemOne 形式（state + questions → answers）を話す任意のエンドポイント。

    既定は TypeSafe Jev。Jev は画像を読めないので image_mode=none のままでは
    画面の内容を判定できない点に注意（将来、文字認識の結果を state に入れる想定）。
    image_mode:
      none        画像を送らない
      clef        Clef 互換の `images` 欄で送る
      perplexity  state 配列の image_url パーツで送る
    """

    name = "SystemOne"

    def build_payload(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        mode = self.settings.get("systemone_image_mode", "none")
        payload = {
            "model": self.settings.get("systemone_model") or "jev-latest",
            "state": state_text,
            "questions": questions,
        }
        if image_data_url and mode == "clef":
            payload["images"] = [image_data_url]
        elif image_data_url and mode == "perplexity":
            payload["state"] = [state_text, {"type": "image_url", "image_url": {"url": image_data_url}}]
        return payload

    @property
    def supports_images(self) -> bool:  # type: ignore[override]
        return self.settings.get("systemone_image_mode", "none") != "none"

    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        url = self._require(self.settings.get("systemone_base_url", ""), "SystemOne の URL")
        key = self._require(self.settings.get("systemone_api_key", ""), "SystemOne の API キー")
        if image_data_url and not self.supports_images:
            raise JudgeError(
                "この SystemOne エンドポイントは画像を送らない設定です（Jev は画像を読めません）。"
                "設定で画像の送り方を変えるか、別の判定モデルを選んでください。"
            )
        data = self._post(url, {"Authorization": f"Bearer {key}"}, self.build_payload(image_data_url, questions, state_text))
        return self._extract_answers(data)
