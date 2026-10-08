"""GUI と画面撮影を除いた中核部分のテスト（API はモックする）。"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from imiwatch import capture, planner, store
from imiwatch.engine import MonitorRuntime
from imiwatch.judges import PROVIDERS, make_judge
from imiwatch.judges.clef import ClefJudge
from imiwatch.judges.llm import to_systemone_answers
from imiwatch.judges.perplexity import PerplexityJudge
from imiwatch.judges.systemone import SystemOneJudge
from imiwatch.plan import (
    NotifyState,
    PlanError,
    api_questions,
    compute_metric,
    describe_plan,
    fallback_plan,
    normalize_answers,
    validate_plan,
)

PLAN = {
    "title": "散らかり度",
    "questions": {
        "floor": {"type": "noul", "instructions": "床に物が置かれているか"},
        "desk": {"type": "score", "instructions": "机の上の状態", "criteria": ["物がない", "半分埋まっている", "全面が埋まっている"]},
        "kind": {"type": "choice", "instructions": "一番目立つ散らかりの種類", "criteria": {"clothes": "服", "paper": "紙", "other": "その他"}, "target": "clothes"},
    },
    "metric": {"name": "散らかり度", "weights": {"floor": 1, "desk": 2, "kind": 1}},
    "notify": {"op": ">=", "threshold": 0.6, "consecutive": 2, "cooldown_minutes": 10},
}


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IMIWATCH_HOME", str(tmp_path))
    for env in ("OPENROUTER_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PERPLEXITY_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


# ------------------------------------------------------------------ plan
def test_validate_and_api_questions_strip_target():
    p = validate_plan(PLAN)
    q = api_questions(p)
    assert "target" not in q["kind"]
    assert q["desk"]["criteria"][0] == "物がない"
    assert p["notify"]["enabled"] is True


@pytest.mark.parametrize("bad", [
    {"questions": {}},
    {"questions": {"bad id!": {"type": "noul", "instructions": "x"}}},
    {"questions": {"a": {"type": "score", "instructions": "x", "criteria": ["one"]}}},
    {"questions": {"a": {"type": "choice", "instructions": "x", "criteria": {"a": "x", "b": "y"}, "target": "z"}}},
    {"questions": {"a": {"type": "magic", "instructions": "x"}}},
])
def test_validate_rejects(bad):
    with pytest.raises(PlanError):
        validate_plan(bad)


def test_weights_default_when_missing():
    p = validate_plan({"questions": {"a": {"type": "noul", "instructions": "x"},
                                     "c": {"type": "choice", "instructions": "y", "criteria": {"p": 1, "q": 2}}}})
    assert p["metric"]["weights"] == {"a": 1.0}  # target のない choice は指標に使わない


def test_normalize_and_metric():
    p = validate_plan(PLAN)
    answers = {
        "floor": {"type": "noul", "noul": 0.8},
        "desk": {"type": "score", "score": 1.0},
        "kind": {"type": "choice", "choice": "clothes", "probabilities": {"clothes": 0.6, "paper": 0.3, "other": 0.1}},
    }
    v = normalize_answers(p, answers)
    assert v == {"floor": 0.8, "desk": 0.5, "kind": 0.6}
    assert compute_metric(p, v) == pytest.approx((0.8 + 2 * 0.5 + 0.6) / 4)
    assert "散らかり度" in describe_plan(p)


def test_notify_consecutive_cooldown_rearm():
    p = validate_plan(PLAN)
    st = NotifyState()
    t = 1000.0
    assert st.update(p, 0.7, t) is False       # 1回目: まだ
    assert st.update(p, 0.7, t + 1) is True    # 2回連続で通知
    assert st.update(p, 0.9, t + 2) is False   # 成立し続けても再通知しない
    assert st.update(p, 0.1, t + 3) is False   # 外れたので再武装
    assert st.update(p, 0.7, t + 4) is False
    assert st.update(p, 0.7, t + 5) is False   # クールダウン中（10分）
    st2 = NotifyState()
    p2 = validate_plan({**PLAN, "notify": {"op": "<=", "threshold": 0.2, "consecutive": 1, "cooldown_minutes": 0}})
    assert st2.update(p2, 0.1, t) is True


def test_fallback_plan_valid():
    p = fallback_plan("在庫ありになったら")
    assert list(p["questions"]) == ["main"]


# ------------------------------------------------------------------ judges
def test_payload_shapes():
    s = store.load_settings()
    q = api_questions(validate_plan(PLAN))
    url = "data:image/jpeg;base64,AAAA"
    clef = ClefJudge(s, "clef-flash").build_payload(url, q)
    assert clef["model"] == "clef-flash" and clef["images"] == [url] and isinstance(clef["state"], str)
    ppx = PerplexityJudge(s).build_payload(url, q)
    assert ppx["state"][1] == {"type": "image_url", "image_url": {"url": url}}
    so = SystemOneJudge({**s, "systemone_image_mode": "clef"}).build_payload(url, q)
    assert so["images"] == [url]
    so_none = SystemOneJudge(s).build_payload(url, q)
    assert "images" not in so_none and isinstance(so_none["state"], str)


def test_extract_answers_cloudflare_wrapper():
    from imiwatch.judges.base import Judge
    assert Judge._extract_answers({"result": {"answers": {"a": 1}}, "success": True}) == {"a": 1}
    assert Judge._extract_answers({"answers": {"b": 2}}) == {"b": 2}


def test_clef_calls_api(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"success": True, "result": {"answers": {"floor": {"type": "noul", "noul": 0.9}}}}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, json=json)
        return Resp()

    monkeypatch.setattr("imiwatch.judges.base.requests.post", fake_post)
    s = {**store.load_settings(), "cloudflare_account_id": "acc", "cloudflare_api_token": "tok"}
    ans = make_judge("clef-flash", s).judge("data:image/jpeg;base64,AA", {"floor": {"type": "noul", "instructions": "x"}})
    assert ans["floor"]["noul"] == 0.9
    assert sent["url"].endswith("/accounts/acc/ai/run/@cf/cloudflare/clef-flash")
    assert sent["headers"]["Authorization"] == "Bearer tok"


def test_missing_key_is_clear_error():
    from imiwatch.judges import JudgeError
    with pytest.raises(JudgeError, match="Cloudflare"):
        make_judge("clef", store.load_settings()).judge(None, {"a": {"type": "noul", "instructions": "x"}})


def test_llm_answer_conversion():
    q = api_questions(validate_plan(PLAN))
    out = to_systemone_answers(q, {"answers": {
        "floor": {"p_yes": 0.7},
        "desk": {"probabilities": {"0": 0.2, "1": 0.2, "2": 0.6}},
        "kind": {"probabilities": {"clothes": 2, "paper": 1, "other": 1}},
    }})
    assert out["floor"]["noul"] == 0.7
    assert out["desk"]["score"] == pytest.approx(1.4)
    assert out["kind"]["choice"] == "clothes" and out["kind"]["probabilities"]["clothes"] == pytest.approx(0.5)


def test_every_provider_constructs():
    s = store.load_settings()
    for key in PROVIDERS:
        make_judge(key, s)


# ------------------------------------------------------------------ planner
def test_planner_without_key_falls_back():
    plan, note = planner.make_plan(store.load_settings(), "在庫ありになったら")
    assert "簡易設計" in note and plan["questions"]


def test_planner_uses_llm_and_retries_without_image(monkeypatch):
    calls = []

    def fake_chat(key, model, system, user, img, timeout):
        calls.append(img)
        if img:
            raise planner.LLMError("this model cannot read images")
        return PLAN

    monkeypatch.setattr(planner, "chat_json", fake_chat)
    s = {**store.load_settings(), "openrouter_api_key": "k"}
    plan, note = planner.make_plan(s, "散らかったら", "data:image/jpeg;base64,AA")
    assert calls == ["data:image/jpeg;base64,AA", None]
    assert plan["title"] == "散らかり度" and "設計しました" in note


def test_parse_json_loose():
    from imiwatch.llm_client import parse_json_loose
    assert parse_json_loose('説明です\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('結果: {"b": [1,2]} 以上') == {"b": [1, 2]}


# ------------------------------------------------------------------ engine（モック判定で通し）
def _frames():
    dark = Image.new("RGB", (400, 300), (20, 20, 20))
    bright = Image.new("RGB", (400, 300), (240, 240, 240))
    return dark, bright


def test_engine_end_to_end_with_mock():
    dark, bright = _frames()
    frames = iter([dark, dark, bright, bright, bright])
    m = store.Monitor(name="t", request="r", region={"left": 0, "top": 0, "width": 400, "height": 300},
                      plan=validate_plan({**PLAN, "notify": {"op": ">=", "threshold": 0.0, "consecutive": 1, "cooldown_minutes": 0}}),
                      provider="mock")
    rt = MonitorRuntime(m)
    s = store.load_settings()
    grab = lambda region: next(frames)  # noqa: E731

    r1 = rt.run_once(s, grab_fn=grab)
    assert r1.status == "ok" and r1.metric is not None and r1.notified is True
    r2 = rt.run_once(s, grab_fn=grab)
    assert r2.status == "reused" and r2.metric == r1.metric   # 変化なし→判定を省略
    r3 = rt.run_once(s, grab_fn=grab)
    assert r3.status == "ok"                                   # 変化あり→判定
    r4 = rt.run_once(s, grab_fn=grab, force=True)
    assert r4.status == "ok"                                   # 強制判定

    rows = store.read_log(m.id)
    assert [r["status"] for r in rows] == ["ok", "reused", "ok", "ok"]
    assert (store.monitor_dir(m.id) / "latest.jpg").exists()
    assert any(p.name.startswith("notified_") for p in store.monitor_dir(m.id).iterdir())


def test_engine_records_errors():
    m = store.Monitor(name="t", request="r", region={"left": 0, "top": 0, "width": 10, "height": 10},
                      plan=validate_plan(PLAN), provider="clef")
    r = MonitorRuntime(m).run_once(store.load_settings(), grab_fn=lambda reg: Image.new("RGB", (50, 50)))
    assert r.status == "error" and "Cloudflare" in r.error
    assert store.read_log(m.id)[-1]["status"] == "error"


def test_store_roundtrip():
    m = store.Monitor(name="名前", request="r", region={"left": 1, "top": 2, "width": 3, "height": 4}, plan=validate_plan(PLAN))
    store.save_monitors([m])
    back = store.load_monitors()[0]
    assert back.name == "名前" and back.plan["metric"]["name"] == "散らかり度"
    s = store.load_settings()
    s["perplexity_api_key"] = "x"
    store.save_settings(s)
    assert json.loads(store.settings_path().read_text(encoding="utf-8"))["perplexity_api_key"] == "x"


def test_capture_helpers():
    img = Image.new("RGB", (2000, 1000), (100, 100, 100))
    small = capture.shrink(img, 768)
    assert max(small.size) == 768
    assert capture.to_data_url(small).startswith("data:image/jpeg;base64,")
    fp = capture.fingerprint(small)
    assert capture.diff_score(fp, fp) == 0
    assert capture.diff_score(None, fp) == float("inf")
