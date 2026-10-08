from __future__ import annotations

import json
import math

from ..llm_client import LLMError, chat_json
from .base import Judge, JudgeError, STATE_TEXT

SYSTEM = """あなたは画像の判定器です。与えられた問いそれぞれについて、各選択肢の確率を推定し、JSON だけを返します。
出力形式:
{"answers": {
  "<問いID>": {"p_yes": 0.0〜1.0}                       ← type が noul の問い
  "<問いID>": {"probabilities": {"<選択肢ID>": 確率, ...}} ← type が choice の問い（合計1）
  "<問いID>": {"probabilities": {"0": 確率, "1": 確率, ...}} ← type が score の問い（段階の番号、合計1）
}}
すべての問いに答えてください。説明文は書かないでください。"""


class LLMJudge(Judge):
    """判定モデルの代わりに、画像を読める LLM（OpenRouter 経由）で判定する。

    判定モデルを契約していなくても試せるようにするための代用品。
    遅く、確率は較正されていないので、比較の基準として使う。
    """

    name = "LLM（OpenRouter）"

    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        user = state_text + "\n\n問い:\n" + json.dumps(questions, ensure_ascii=False, indent=1)
        try:
            data = chat_json(
                self.settings.get("openrouter_api_key", ""),
                self.settings.get("llm_judge_model") or "openrouter/auto",
                SYSTEM,
                user,
                image_data_url,
                timeout=self.timeout,
            )
        except LLMError as e:
            raise JudgeError(str(e)) from e
        return to_systemone_answers(questions, data)


def to_systemone_answers(questions: dict, data: dict) -> dict:
    """LLM の出力を SystemOne 形式の answers にそろえる。"""
    raw = data.get("answers", data) if isinstance(data, dict) else {}
    out = {}
    for qid, q in questions.items():
        a = raw.get(qid) if isinstance(raw, dict) else None
        if not isinstance(a, dict):
            continue
        t = q["type"]
        if t == "noul":
            p = a.get("p_yes", a.get("noul", a.get("probability")))
            if p is None:
                continue
            out[qid] = {"type": "noul", "noul": _clip(float(p))}
            continue
        keys = list(q["criteria"].keys()) if t == "choice" else [str(i) for i in range(len(q["criteria"]))]
        given = a.get("probabilities") or {}
        probs = {}
        for k in keys:
            try:
                probs[k] = max(0.0, float(given.get(k, 0.0)))
            except (TypeError, ValueError):
                probs[k] = 0.0
        total = sum(probs.values())
        probs = {k: v / total for k, v in probs.items()} if total > 0 else {k: 1 / len(keys) for k in keys}
        conf = _confidence(list(probs.values()))
        if t == "choice":
            best = max(probs, key=probs.get)
            out[qid] = {"type": "choice", "choice": best, "probabilities": probs, "confidence": conf}
        else:
            score = sum(int(k) * v for k, v in probs.items())
            out[qid] = {"type": "score", "score": score, "probabilities": probs, "confidence": conf}
    if not out:
        raise JudgeError(f"LLM の応答から答えを読み取れませんでした: {str(data)[:300]}")
    return out


def _clip(p: float) -> float:
    return max(0.0, min(1.0, p))


def _confidence(probs: list[float]) -> float:
    """1 - 正規化エントロピー（1つに集中しているほど 1 に近い）。"""
    n = len(probs)
    if n <= 1:
        return 1.0
    h = -sum(p * math.log(p) for p in probs if p > 0)
    return max(0.0, 1.0 - h / math.log(n))
