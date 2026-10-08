"""ユーザーの一言を、判定モデル用の「設計図」に分解する役（LLM）。"""
from __future__ import annotations

import json

from .llm_client import LLMError, chat_json
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
- 日本語で書く（問いID だけは英数字）"""


def make_plan(settings: dict, request: str, image_data_url: str | None = None) -> tuple[dict, str]:
    """設計図と、どう作ったかのメモを返す。LLM が使えなければ簡易設計に切り替える。"""
    key = settings.get("openrouter_api_key", "")
    if not key:
        return fallback_plan(request), "OpenRouter のキーがないため、簡易設計を使いました。"
    model = settings.get("planner_model") or "openrouter/auto"
    user = f"ユーザーの依頼: {request}\n\n添付画像は、ユーザーが選んだ画面範囲の現在の様子です（あれば）。"
    last_err = None
    # 画像付き → 画像なし の順に試す（画像を読めないモデルもあるため）
    for img in ([image_data_url, None] if image_data_url else [None]):
        try:
            raw = chat_json(key, model, SYSTEM, user, img, timeout=float(settings.get("request_timeout", 60)) + 30)
            plan = validate_plan(raw)
            note = f"{model} で設計しました" + ("（画面の画像も参考にしました）" if img else "")
            return plan, note
        except (LLMError, PlanError, TypeError, ValueError) as e:
            last_err = e
            continue
    return fallback_plan(request), f"LLM による設計に失敗したため簡易設計を使いました: {last_err}"


def plan_to_json(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)
