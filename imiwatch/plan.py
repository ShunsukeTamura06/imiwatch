"""判定の設計図（plan）の検証と、判定結果から指標・通知を計算する部分。

設計図の形:
{
  "title": "部屋の散らかり度",
  "summary": "床・机・服の3観点で散らかり具合を測ります",
  "questions": {
    "floor": {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}},
    "level": {"type": "score", "instructions": "...", "criteria": ["...", "...", "..."]},
    "kind":  {"type": "choice", "instructions": "...", "criteria": {"a": "...", "other": "..."},
              "target": "a"}      # 指標に使う選択肢（choice のみ・任意）
  },
  "metric": {"name": "散らかり度", "weights": {"floor": 0.3, "level": 0.7}},
  "notify": {"op": ">=", "threshold": 0.7, "consecutive": 2,
             "cooldown_minutes": 60, "message": "部屋が散らかってきました"},
  "suggested_interval_seconds": 300,
  "caveats": ["暗い部屋では判定が不安定になります"]
}

問いの値はすべて 0〜1 に正規化する:
  noul   → yes の確率
  score  → score / (段階数 - 1)
  choice → target に指定した選択肢の確率（target がなければ指標には使わない）
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

QUESTION_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")
MAX_QUESTIONS = 64


class PlanError(ValueError):
    pass


def validate_plan(plan: Any) -> dict:
    """設計図を検証し、足りない項目を補った新しい dict を返す。"""
    if not isinstance(plan, dict):
        raise PlanError("設計図が JSON オブジェクトではありません")
    qs = plan.get("questions")
    if not isinstance(qs, dict) or not qs:
        raise PlanError("questions が空です")
    if len(qs) > MAX_QUESTIONS:
        raise PlanError(f"問いは {MAX_QUESTIONS} 個までです")

    clean_qs: dict[str, dict] = {}
    for qid, q in qs.items():
        if not QUESTION_ID_RE.match(str(qid)):
            raise PlanError(f"問いのID '{qid}' に使えない文字があります（英数字と _ . - のみ）")
        if not isinstance(q, dict):
            raise PlanError(f"問い '{qid}' の形式が不正です")
        qtype = q.get("type")
        instr = q.get("instructions")
        if not instr:
            raise PlanError(f"問い '{qid}' に instructions がありません")
        crit = q.get("criteria")
        if qtype == "noul":
            if crit is not None and not isinstance(crit, dict):
                raise PlanError(f"問い '{qid}'（noul）の criteria は {{true, false}} の形にしてください")
        elif qtype == "choice":
            if not isinstance(crit, dict) or len(crit) < 2:
                raise PlanError(f"問い '{qid}'（choice）には選択肢が2つ以上必要です")
            target = q.get("target")
            if target is not None and target not in crit:
                raise PlanError(f"問い '{qid}' の target '{target}' が選択肢にありません")
        elif qtype == "score":
            if not isinstance(crit, list) or not (2 <= len(crit) <= 10):
                raise PlanError(f"問い '{qid}'（score）の段階は2〜10個にしてください")
        else:
            raise PlanError(f"問い '{qid}' の type '{qtype}' は noul/choice/score のいずれかにしてください")
        clean_qs[str(qid)] = dict(q)

    metric = dict(plan.get("metric") or {})
    weights = metric.get("weights") or {}
    weights = {k: float(v) for k, v in weights.items() if k in clean_qs and _metric_capable(clean_qs[k])}
    if not weights or sum(abs(w) for w in weights.values()) == 0:
        # 重みがなければ、指標に使える問いを均等に使う
        weights = {k: 1.0 for k, q in clean_qs.items() if _metric_capable(q)}
    if not weights:
        raise PlanError("指標に使える問いがありません（choice には target を指定してください）")
    metric["weights"] = weights
    metric.setdefault("name", plan.get("title") or "指標")

    notify = dict(plan.get("notify") or {})
    notify.setdefault("op", ">=")
    if notify["op"] not in (">=", "<="):
        raise PlanError("notify.op は >= か <= にしてください")
    notify["threshold"] = float(notify.get("threshold", 0.7))
    notify["consecutive"] = max(1, int(notify.get("consecutive", 2)))
    notify["cooldown_minutes"] = max(0.0, float(notify.get("cooldown_minutes", 60)))
    notify.setdefault("message", f"{metric['name']} が条件を満たしました")
    notify.setdefault("enabled", True)

    out = dict(plan)
    out["questions"] = clean_qs
    out["metric"] = metric
    out["notify"] = notify
    out["title"] = plan.get("title") or metric["name"]
    out.setdefault("summary", "")
    out["suggested_interval_seconds"] = int(plan.get("suggested_interval_seconds") or 300)
    caveats = plan.get("caveats") or []
    out["caveats"] = [str(c) for c in caveats] if isinstance(caveats, list) else [str(caveats)]
    return out


def _metric_capable(q: dict) -> bool:
    return q.get("type") in ("noul", "score") or (q.get("type") == "choice" and q.get("target"))


def api_questions(plan: dict) -> dict:
    """API に送る問い（アプリ独自のキー target などを除いたもの）。"""
    out = {}
    for qid, q in plan["questions"].items():
        item = {"type": q["type"], "instructions": q["instructions"]}
        if q.get("criteria") is not None:
            item["criteria"] = q["criteria"]
        out[qid] = item
    return out


def normalize_answers(plan: dict, answers: dict) -> dict[str, float]:
    """判定結果を、問いごとの 0〜1 の値にそろえる。"""
    values: dict[str, float] = {}
    for qid, q in plan["questions"].items():
        a = answers.get(qid)
        if not isinstance(a, dict) or a.get("abstained"):
            continue  # 答えがない・判断保留の問いは指標に使わない
        t = q["type"]
        try:
            if t == "noul":
                values[qid] = float(a["noul"])
            elif t == "score":
                levels = len(q["criteria"])
                values[qid] = float(a["score"]) / (levels - 1)
            elif t == "choice":
                probs = a.get("probabilities") or {}
                target = q.get("target")
                if target is not None:
                    values[qid] = float(probs.get(target, 1.0 if a.get("choice") == target else 0.0))
        except (KeyError, TypeError, ValueError):
            continue
    return {k: min(1.0, max(0.0, v)) for k, v in values.items()}


def compute_metric(plan: dict, values: dict[str, float]) -> float | None:
    weights = plan["metric"]["weights"]
    num = den = 0.0
    for qid, w in weights.items():
        if qid in values:
            num += w * values[qid]
            den += abs(w)
    if den == 0:
        return None
    return max(0.0, min(1.0, num / den))


@dataclass
class NotifyState:
    """連続成立回数・クールダウン・再武装を管理する。"""

    streak: int = 0
    armed: bool = True
    last_fired: float = 0.0
    history: list = field(default_factory=list)

    def update(self, plan: dict, metric: float | None, now: float | None = None) -> bool:
        """新しい判定結果を受け取り、通知すべきなら True を返す。"""
        rule = plan["notify"]
        if not rule.get("enabled", True) or metric is None:
            return False
        now = time.time() if now is None else now
        hit = metric >= rule["threshold"] if rule["op"] == ">=" else metric <= rule["threshold"]
        if not hit:
            # 条件が外れたら、次に成立したとき再び通知できるようにする
            self.streak = 0
            self.armed = True
            return False
        self.streak += 1
        if not self.armed or self.streak < rule["consecutive"]:
            return False
        if now - self.last_fired < rule["cooldown_minutes"] * 60:
            return False
        self.armed = False
        self.last_fired = now
        return True


def describe_plan(plan: dict) -> str:
    """設計図を人が読める文章にする。"""
    lines = [f"■ {plan['title']}"]
    if plan.get("summary"):
        lines.append(plan["summary"])
    lines.append("")
    lines.append("【観点】")
    weights = plan["metric"]["weights"]
    for qid, q in plan["questions"].items():
        w = weights.get(qid)
        wtxt = f"（重み {w:g}）" if w is not None else "（記録のみ）"
        lines.append(f"・[{q['type']}] {q['instructions']} {wtxt}")
        crit = q.get("criteria")
        if q["type"] == "score" and isinstance(crit, list):
            for i, c in enumerate(crit):
                lines.append(f"    {i}: {c if isinstance(c, str) else c}")
        elif q["type"] == "choice" and isinstance(crit, dict):
            for k, c in crit.items():
                mark = " ←指標に使う" if q.get("target") == k else ""
                lines.append(f"    {k}: {c}{mark}")
    n = plan["notify"]
    lines.append("")
    lines.append(
        f"【通知】{plan['metric']['name']} {n['op']} {n['threshold']:.2f} が "
        f"{n['consecutive']} 回連続で成立したら通知（同じ通知は {n['cooldown_minutes']:g} 分あける）"
    )
    if plan.get("caveats"):
        lines.append("")
        lines.append("【注意】")
        lines.extend(f"・{c}" for c in plan["caveats"])
    return "\n".join(lines)


def fallback_plan(request: str, language: str = "ja") -> dict:
    """設計役の LLM が使えないときの、最小の設計図。"""
    instr = (f"Does the image satisfy this condition? {request}" if language == "en"
             else f"画像の状態は次の条件を満たしているか: {request}")
    return validate_plan(
        {
            "title": request[:30] or "見張り",
            "summary": "LLM を使わない簡易設計です（あなたの文章をそのまま1つの問いにしています）。",
            "questions": {
                "main": {
                    "type": "noul",
                    "instructions": instr,
                }
            },
            "metric": {"name": "条件の成立度", "weights": {"main": 1.0}},
            "notify": {"op": ">=", "threshold": 0.7, "consecutive": 2, "cooldown_minutes": 60},
            "suggested_interval_seconds": 300,
            "caveats": ["簡易設計のため精度は低めです。設定で OpenRouter のキーを入れると、観点を自動で分解します。"],
        }
    )
