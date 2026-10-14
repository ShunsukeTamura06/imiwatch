"""ユーザーの一言を、判定モデル用の「設計図」に分解する役（LLM）。"""
from __future__ import annotations

import json
import re

from .llm_client import LLMError, chat_json, is_configured, llm_config
from .plan import PlanError, fallback_plan, validate_plan

SYSTEM = r"""あなたは「判定モデル（decision model）」の問いを設計する専門家です。
判定モデルは文章を生成せず、画像と問いを受け取り、あらかじめ決めた選択肢ごとの確率だけを返します。
ユーザーは PC 画面の一部（スクリーンショットの範囲）を選び、「何が起きたら知りたいか」「何を測りたいか」を一言で書きます。
あなたはそれを、判定モデルが答えやすい複数の観点（問い）に分解し、下の形式の JSON だけを返します。

# 問いの型
- noul: はい/いいえ。答えは「はい」の確率。instructions は「はい」が高い値になる向きで書く。criteria は {"true": "...", "false": "..."}（任意）
- score: 段階評価。criteria は低い順の配列（2〜10段階）。各段階は「程度」ではなく「目に見える状況」で書く
  （悪い例:「少し多い」「かなり多い」 / 良い例:「机の上に物がない」「机の半分ほどが物で埋まっている」）
- choice: 選択肢から1つ。criteria は {"選択肢ID": "説明"}。当てはまらない場合の "other" を必ず入れる。
  指標に使う場合は "target" に指標として測りたい選択肢IDを入れる

# 判定モデルの苦手なこと（問いに含めない）
- 数を数える、計算、日付や数値の大小比較、細かい文字や小さすぎる物、色コードの比較
- 1つの問いに複数の判断を詰め込むこと（1問1判断にする）
- 二重否定や「〜でないことはない」のような回りくどい言い方
数値の比較が必要な依頼は、「画面に表示された金額は 10,000 円未満に見えるか」のように、見た目で判断できる問いへ言い換え、caveats に精度の注意を書く。

# 出力形式（JSON のみ。説明文は不要）
{
  "title": "短い名前（15文字以内）",
  "summary": "この見張りが何をどう測るかを1〜2文で",
  "questions": {
    "<英数字のID>": {"type": "noul" | "score" | "choice", "instructions": "...", "criteria": ..., "target": "（choiceで指標に使う場合のみ）"}
  },
  "metric": {"name": "グラフに表示する指標名", "weights": {"<問いID>": 重み, ...}},
  "notify": {"op": ">=" または "<=", "threshold": 0〜1, "consecutive": 連続回数, "cooldown_minutes": 分, "message": "通知の文面"},
  "suggested_interval_seconds": 推奨の取得間隔（秒）,
  "caveats": ["この依頼で特に起きやすい誤判定や限界を、具体的に"]
}

# 方針
- 問いは 3〜6 個。指標（metric）は各問いの値（0〜1に正規化される）の重み付き平均。値が大きいほど「ユーザーの知りたい状態に近い」向きにそろえる
- 記録だけしたい問いは weights に入れなくてよい
- 通知が不要な「測るだけ」の依頼でも notify は書き、閾値は高め（0.8 前後）にする
- 取得間隔は、変化の速さに合わせる（ゆっくり変わるものは 300〜1800 秒、速いものは 30〜120 秒）
- {language_rule}"""

LANGUAGE_RULES = {
    "ja": "日本語で書く（問いID だけは英数字）",
    # imajev など英語中心で学習された判定モデル向け。人が読む title / summary / message / caveats は日本語のまま
    "en": "questions の instructions と criteria（選択肢・段階の説明）は英語で書く。"
          "title・summary・metric.name・notify.message・caveats は日本語で書く。問いID は英数字",
}


def question_language(provider: str) -> str:
    """判定モデルに合わせた、問いを書く言語。"""
    return "en" if provider == "jev-local" else "ja"


TYPE_RULES = {
    "noul": "type は noul（はい/いいえ）。instructions は「はい」が高い値になる向きの問いにする。"
            "criteria は {\"true\": \"はいに当たる見た目\", \"false\": \"いいえに当たる見た目\"}",
    "choice": "type は choice（選択肢から1つ）。criteria は {\"選択肢ID（英数字）\": \"説明\"} で 3〜6 個、"
              "当てはまらない場合の \"other\" を必ず入れる。指標として測りたい選択肢IDを target に入れる",
    "score": "type は score（段階評価）。criteria は低い順の配列で 3〜5 段階。"
             "各段階は「程度」ではなく「目に見える状況」で書く",
}

SUGGEST_SYSTEM = r"""あなたは「判定モデル（decision model）」の問いを設計する専門家です。
判定モデルは文章を生成せず、画像と問いを受け取り、あらかじめ決めた選択肢ごとの確率だけを返します。
ユーザーの依頼と、すでにある問いの一覧を見て、指定された型の問いを「1つだけ」新しく作ります。
すでにある問いと同じ観点は避け、依頼の判定に役立つ別の観点を選んでください。
判定モデルは、数を数える・計算・日付の比較・細かい文字が苦手です。1問1判断にしてください。

# 型の指定
{type_rule}

# 出力形式（JSON のみ）
{"id": "英数字の短いID", "question": {"type": "...", "instructions": "...", "criteria": ..., "target": "（choiceのみ）"},
 "weight": 指標に使う重み（0〜2。記録だけなら 0）, "reason": "この観点を選んだ理由（日本語で1文）"}

# 言語
{language_rule}"""


def _timeout(settings: dict) -> float:
    return float(settings.get("request_timeout", 60)) + 30


def make_plan(
    settings: dict, request: str, image_data_url: str | None = None, language: str = "ja"
) -> tuple[dict, str]:
    """設計図と、どう作ったかのメモを返す。LLM が使えなければ簡易設計に切り替える。"""
    system = SYSTEM.replace("{language_rule}", LANGUAGE_RULES.get(language, LANGUAGE_RULES["ja"]))
    ok, why = is_configured(settings)
    if not ok:
        return fallback_plan(request, language), f"{why}。簡易設計を使いました（設定で設計役 LLM を指定できます）"
    _, _, model = llm_config(settings)
    user = f"ユーザーの依頼: {request}\n\n添付画像は、ユーザーが選んだ画面範囲の現在の様子です（あれば）。"
    last_err = None
    # 画像付き → 画像なし の順に試す（画像を読めないモデルもあるため）
    for img in ([image_data_url, None] if image_data_url else [None]):
        try:
            raw = chat_json(settings, system, user, img, timeout=_timeout(settings))
            plan = validate_plan(raw)
            note = f"{model} で設計しました" + ("（画面の画像も参考にしました）" if img else "")
            if language == "en":
                note += "。判定モデルに合わせて、問いは英語で書いています"
            return plan, note
        except (LLMError, PlanError, TypeError, ValueError) as e:
            last_err = e
            continue
    return fallback_plan(request, language), f"LLM による設計に失敗したため簡易設計を使いました: {last_err}"


def suggest_question(
    settings: dict,
    request: str,
    qtype: str,
    existing: dict | None = None,
    image_data_url: str | None = None,
    language: str = "ja",
) -> dict:
    """指定した型の問いを1つ提案する。{"id", "question", "weight", "reason"} を返す。"""
    if qtype not in TYPE_RULES:
        raise PlanError(f"型は noul / choice / score のいずれかです: {qtype}")
    ok, why = is_configured(settings)
    if not ok:
        raise LLMError(why)
    system = (SUGGEST_SYSTEM.replace("{type_rule}", TYPE_RULES[qtype])
              .replace("{language_rule}", LANGUAGE_RULES.get(language, LANGUAGE_RULES["ja"])))
    existing = existing or {}
    user = (f"ユーザーの依頼: {request or '（未記入）'}\n\nすでにある問い:\n"
            + (json.dumps(existing, ensure_ascii=False, indent=1) if existing else "（まだありません）"))
    last_err: Exception | None = None
    for img in ([image_data_url, None] if image_data_url else [None]):
        try:
            raw = chat_json(settings, system, user, img, timeout=_timeout(settings))
            q = dict(raw.get("question") or {})
            q["type"] = qtype  # 型は必ず指定どおりにする
            qid = _unique_id(str(raw.get("id") or qtype), existing)
            # 1問だけの設計図として検証し、足りない項目を補う
            checked = validate_plan({"questions": {qid: q}, "metric": {"weights": {qid: 1}}})
            weight = raw.get("weight", 1)
            try:
                weight = max(0.0, float(weight))
            except (TypeError, ValueError):
                weight = 1.0
            return {"id": qid, "question": checked["questions"][qid], "weight": weight,
                    "reason": str(raw.get("reason") or "")}
        except (LLMError, PlanError, TypeError, ValueError, AttributeError) as e:
            last_err = e
            continue
    raise LLMError(f"問いの提案に失敗しました: {last_err}")


def _unique_id(base: str, existing: dict) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_")[:40] or "q"
    if not base[0].isalpha():
        base = "q_" + base
    qid, n = base, 2
    while qid in existing:
        qid, n = f"{base}_{n}", n + 1
    return qid


def plan_to_json(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)
