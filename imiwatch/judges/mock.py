from __future__ import annotations

import base64
import hashlib
import math
import random

from .base import Judge, STATE_TEXT


class MockJudge(Judge):
    """API を使わない動作確認用。画像の中身から決まる、それらしい確率を返す。"""

    name = "Mock"

    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        seed_src = (image_data_url or "")[-4000:]
        brightness = _approx_brightness(image_data_url)
        answers = {}
        for qid, q in questions.items():
            h = int(hashlib.md5((seed_src + qid).encode()).hexdigest()[:8], 16)
            rnd = random.Random(h)
            base = 0.5 * brightness + 0.5 * rnd.random()
            t = q["type"]
            if t == "noul":
                answers[qid] = {"type": "noul", "noul": round(base, 3)}
            elif t == "score":
                n = len(q["criteria"])
                center = base * (n - 1)
                w = [math.exp(-((i - center) ** 2)) for i in range(n)]
                s = sum(w)
                probs = {str(i): round(x / s, 3) for i, x in enumerate(w)}
                score = sum(i * p for i, p in enumerate(x / s for x in w))
                answers[qid] = {
                    "type": "score",
                    "score": round(score, 3),
                    "probabilities": probs,
                    "confidence": round(max(w) / s, 3),
                    "legend": {str(i): c for i, c in enumerate(q["criteria"])},
                }
            elif t == "choice":
                keys = list(q["criteria"].keys())
                w = [rnd.random() + (base if i == 0 else 0) for i in range(len(keys))]
                s = sum(w)
                probs = {k: round(x / s, 3) for k, x in zip(keys, w)}
                best = max(probs, key=probs.get)
                answers[qid] = {"type": "choice", "choice": best, "probabilities": probs, "confidence": probs[best]}
        return answers


def _approx_brightness(data_url: str | None) -> float:
    if not data_url or "," not in data_url:
        return 0.5
    try:
        import io

        from PIL import Image, ImageStat

        raw = base64.b64decode(data_url.split(",", 1)[1])
        img = Image.open(io.BytesIO(raw)).convert("L")
        return ImageStat.Stat(img).mean[0] / 255.0
    except Exception:  # noqa: BLE001 - 動作確認用なので失敗しても続行
        return 0.5
