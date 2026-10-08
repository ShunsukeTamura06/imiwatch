from __future__ import annotations

import json
import uuid

from .base import Judge, JudgeError, STATE_TEXT

# imajev の公開 API の上限（jev-local docs/decision-api.md）
MAX_FIELDS_PER_REQUEST = 8
STATE_NOTE_EN = "Screenshot of a region the user selected on their PC screen. Answer from the image only."


def _text(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


class JevLocalJudge(Judge):
    """自前サーバーで動かすオープンウェイトの判定モデル（jev-local の imajev-4b）。

    jev-local の `POST /v1/decisions`（imajev 本来の契約）を使う。
      noul   → boolean
      choice → choice（options）
      score  → ordinal（levels は 0..n-1）
    画像は `images` に data URL で1枚送る。1リクエスト8項目までなので、超える分は分割して送る。

    imajev は答えを保留できる（status: abstained）。保留した問いは数値を持たない答えとして返し、
    指標の計算からは外れる（imiwatch.plan.normalize_answers が値のない答えを無視する）。
    """

    name = "jev-local (imajev)"

    def __init__(self, settings: dict):
        super().__init__(settings)
        self.timeout = max(self.timeout, 120.0)  # GPU 1枚の自前サーバーなので長めに待つ

    # -------------------------------------------------------------- 変換
    def build_requests(self, image_data_url, questions: dict) -> list[tuple[dict, dict]]:
        """(リクエスト本文, フィールドID → 元の問いID) のリストを返す。"""
        items = list(questions.items())
        out = []
        for start in range(0, len(items), MAX_FIELDS_PER_REQUEST):
            fields, idmap = [], {}
            for i, (qid, q) in enumerate(items[start : start + MAX_FIELDS_PER_REQUEST], start=start):
                fid = f"f{i}"  # imajev の ID 規則（英字始まり・英数字と _ のみ）に合わせる
                idmap[fid] = qid
                fields.append(self._field(fid, q))
            body = {
                "request_id": uuid.uuid4().hex,
                "state": {"note": STATE_NOTE_EN},
                "images": [image_data_url] if image_data_url else [],
                "fields": fields,
            }
            out.append((body, idmap))
        return out

    @staticmethod
    def _field(fid: str, q: dict) -> dict:
        t = q["type"]
        question = _text(q["instructions"])
        crit = q.get("criteria")
        if t == "noul":
            f = {"id": fid, "type": "boolean", "question": question}
            if isinstance(crit, dict):
                if crit.get("true") is not None:
                    f["yes_description"] = _text(crit["true"])
                if crit.get("false") is not None:
                    f["no_description"] = _text(crit["false"])
            return f
        if t == "choice":
            options = []
            for k, desc in crit.items():
                opt = {"value": str(k)}
                if desc not in (None, ""):
                    opt["description"] = _text(desc)
                options.append(opt)
            return {"id": fid, "type": "choice", "question": question, "options": options}
        if t == "score":
            levels = [{"value": i, "description": _text(c) or f"level {i}"} for i, c in enumerate(crit)]
            return {"id": fid, "type": "ordinal", "question": question, "levels": levels}
        raise JudgeError(f"未対応の問いの型です: {t}")

    @staticmethod
    def to_answer(q: dict, r: dict) -> dict:
        """imajev の結果1件を SystemOne 形式の答えにする。"""
        t = q["type"]
        scores = {str(k): float(v) for k, v in (r.get("scores") or {}).items()}
        unknown = scores.pop("__unknown__", 0.0)
        base = {"type": t, "status": r.get("status"), "unknown": round(unknown, 4),
                "calibrated": r.get("score_semantics") == "calibrated_normalized_scores"}
        if r.get("status") != "answered":
            # 判断保留: 値を持たせない（指標・通知に使わない）
            return {**base, "abstained": True, "probabilities": scores}
        if t == "noul":
            # 「不明」の確率は「はい」に混ぜない（jev-local の推奨どおり、再正規化しない）
            return {**base, "noul": scores.get("true", 0.0), "probabilities": scores}
        if t == "choice":
            return {**base, "choice": str(r.get("value")), "probabilities": scores,
                    "confidence": max(scores.values()) if scores else 0.0}
        # score: 既知の段階の中での期待値（グラフを滑らかにするため。imajev の value は選ばれた段階）
        known = {int(k): v for k, v in scores.items()}
        total = sum(known.values())
        expected = sum(k * v for k, v in known.items()) / total if total > 0 else float(r.get("value") or 0)
        return {**base, "score": expected, "level": r.get("value"), "probabilities": scores,
                "confidence": max(known.values()) if known else 0.0}

    # -------------------------------------------------------------- 実行
    def judge(self, image_data_url, questions, state_text=STATE_TEXT) -> dict:
        base_url = self._require(self.settings.get("jevlocal_base_url", ""), "jev-local の URL").rstrip("/")
        url = base_url if base_url.endswith("/v1/decisions") else base_url + "/v1/decisions"
        headers = {}
        if self.settings.get("jevlocal_api_key"):
            headers["Authorization"] = f"Bearer {self.settings['jevlocal_api_key']}"
        answers = {}
        for body, idmap in self.build_requests(image_data_url, questions):
            try:
                data = self._post(url, headers, body)
            except JudgeError as e:
                if "通信に失敗" in str(e):
                    raise JudgeError(
                        f"jev-local（{base_url}）に接続できません。EC2 で ./scripts/start.sh が動いているか、"
                        "SSH トンネル（ssh -N -L 8008:127.0.0.1:8008 ec2-user@<EC2>）が張られているかを確認してください。"
                    ) from e
                raise
            results = data.get("results") if isinstance(data, dict) else None
            if not isinstance(results, dict):
                raise JudgeError(f"jev-local の応答に results がありません: {str(data)[:300]}")
            for fid, qid in idmap.items():
                if fid in results:
                    answers[qid] = self.to_answer(questions[qid], results[fid])
        return answers
