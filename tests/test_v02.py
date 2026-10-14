"""v0.2 で足した部分のテスト: OpenAI 互換 LLM、型別の問い提案、画面向け API、ログの移行。"""
from __future__ import annotations

import csv
import json
import time

import pytest
from PIL import Image

from imiwatch import llm_client, planner, store
from imiwatch.plan import validate_plan

PLAN = {
    "title": "在庫",
    "questions": {
        "in_stock": {"type": "noul", "instructions": "在庫ありと表示されているか"},
        "level": {"type": "score", "instructions": "表示の強さ", "criteria": ["切れ", "わずか", "あり"]},
        "btn": {"type": "choice", "instructions": "ボタン", "criteria": {"on": "押せる", "off": "押せない", "other": "なし"}, "target": "on"},
    },
    "metric": {"name": "在庫ありらしさ", "weights": {"in_stock": 2, "level": 1, "btn": 1}},
    "notify": {"op": ">=", "threshold": 0.0, "consecutive": 1, "cooldown_minutes": 0},
}


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IMIWATCH_HOME", str(tmp_path))
    for env in ("LLM_API_KEY", "OPENROUTER_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
                "PERPLEXITY_API_KEY", "TYPESAFE_API_KEY", "KEV_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def chat_reply(obj):
    return Resp(200, {"choices": [{"message": {"content": json.dumps(obj, ensure_ascii=False)}}]})


# ------------------------------------------------------------------ 設定の移行
def test_settings_migrate_openrouter_key():
    store.settings_path().write_text(json.dumps({"openrouter_api_key": "sk-or-old"}), encoding="utf-8")
    s = store.load_settings()
    assert s["llm_api_key"] == "sk-or-old"
    assert s["llm_base_url"].startswith("https://openrouter.ai")
    store.update_settings({"planner_model": "x"})
    raw = json.loads(store.settings_path().read_text(encoding="utf-8"))
    assert "openrouter_api_key" not in raw and raw["llm_api_key"] == "sk-or-old"


def test_env_key_not_persisted(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    assert store.load_settings()["llm_api_key"] == "from-env"
    store.update_settings({"planner_model": "m"})
    raw = json.loads(store.settings_path().read_text(encoding="utf-8"))
    assert "llm_api_key" not in raw  # 環境変数の値はファイルに書かない


# ------------------------------------------------------------------ OpenAI 互換クライアント
def test_endpoint_and_config():
    assert llm_client.endpoint("http://localhost:1234/v1/") == "http://localhost:1234/v1/chat/completions"
    assert llm_client.endpoint("https://x/v1/chat/completions") == "https://x/v1/chat/completions"
    ok, why = llm_client.is_configured({"llm_base_url": "http://localhost:11434/v1", "planner_model": ""})
    assert not ok and "モデル名" in why
    ok, _ = llm_client.is_configured({"llm_base_url": "http://localhost:11434/v1", "planner_model": "qwen"})
    assert ok  # ローカルならキーなしで良い
    ok, why = llm_client.is_configured({"llm_base_url": "https://openrouter.ai/api/v1", "planner_model": ""})
    assert not ok and "キー" in why


def test_chat_json_retries_without_response_format(monkeypatch):
    sent = []

    def fake_post(url, headers, json, timeout, **kw):
        sent.append((url, dict(headers), dict(json)))
        if "response_format" in json:
            return Resp(400, '{"error": "response_format is not supported"}')
        return Resp(200, {"choices": [{"message": {"content": "<think>考え中</think>```json\n{\"ok\": true}\n```"}}]})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    s = {"llm_base_url": "http://localhost:1234/v1", "llm_api_key": "", "planner_model": "local-model"}
    assert llm_client.chat_json(s, "sys", "user") == {"ok": True}
    assert len(sent) == 2 and "response_format" not in sent[1][2]
    assert sent[0][0] == "http://localhost:1234/v1/chat/completions"
    assert "Authorization" not in sent[0][1]  # キーなしなら送らない
    assert sent[0][2]["model"] == "local-model"


def test_chat_json_content_parts(monkeypatch):
    monkeypatch.setattr(llm_client.requests, "post", lambda url, headers, json, timeout, **kw: Resp(
        200, {"choices": [{"message": {"content": [{"type": "text", "text": '{"a": 1}'}]}}]}))
    s = {"llm_base_url": "https://api.openai.com/v1", "llm_api_key": "sk", "planner_model": "m"}
    assert llm_client.chat_json(s, "s", "u") == {"a": 1}


# ------------------------------------------------------------------ 型別の提案
@pytest.mark.parametrize("qtype,question", [
    ("noul", {"type": "noul", "instructions": "赤いバッジがあるか", "criteria": {"true": "ある", "false": "ない"}}),
    ("choice", {"type": "score", "instructions": "表示はどれか", "criteria": {"a": "在庫あり", "b": "在庫切れ", "other": "その他"}, "target": "a"}),
    ("score", {"type": "noul", "instructions": "表示の強さ", "criteria": ["弱い", "中", "強い"]}),
])
def test_suggest_question_forces_type(monkeypatch, qtype, question):
    seen = {}

    def fake_post(url, headers, json, timeout, **kw):
        seen["system"] = json["messages"][0]["content"]
        return chat_reply({"id": "in-stock!", "question": question, "weight": 1.5, "reason": "別の観点"})

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    s = {**store.load_settings(), "llm_api_key": "k"}
    r = planner.suggest_question(s, "在庫ありになったら", qtype, {"in_stock": {"type": "noul", "instructions": "x"}})
    assert r["question"]["type"] == qtype  # 型は指定どおりに直す
    assert r["id"] == "in_stock_2"  # 使えない文字を直し、重複を避ける
    assert r["weight"] == 1.5 and r["reason"] == "別の観点"
    assert f"type は {qtype}" in seen["system"]


def test_suggest_requires_llm():
    with pytest.raises(planner.LLMError):
        planner.suggest_question(store.load_settings(), "x", "noul")


# ------------------------------------------------------------------ ログの移行と画像
def test_log_header_migration():
    mid = "abc"
    p = store.log_path(mid)
    old = ["timestamp", "status", "metric", "values", "provider", "latency_ms", "notified", "error"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({"timestamp": "2026-10-01T10:00:00", "status": "ok", "metric": "0.5", "values": "{}",
                    "provider": "mock", "latency_ms": 1, "notified": 0, "error": ""})
    store.append_log(mid, {"timestamp": "2026-10-01T10:05:00", "status": "ok", "metric": "0.6", "values": {},
                           "provider": "mock", "latency_ms": 1, "notified": 0, "error": "", "shot": "20261001T100500.jpg"})
    rows = store.read_log(mid)
    assert [r["metric"] for r in rows] == [0.5, 0.6]
    assert rows[0]["shot"] == "" and rows[1]["shot"] == "20261001T100500.jpg"


def test_prune_shots():
    d = store.shots_dir("m1")
    for i in range(12):
        (d / f"20261001T1000{i:02d}.jpg").write_bytes(b"x")
    assert store.prune_shots("m1", 5) == 7
    assert sorted(p.name for p in d.iterdir())[0] == "20261001T100007.jpg"


# ------------------------------------------------------------------ 画面向け API
@pytest.fixture
def api(monkeypatch):
    from imiwatch import api as api_mod

    frames = {"n": 0}

    def fake_grab(region):
        frames["n"] += 1
        shade = 40 + (frames["n"] * 37) % 200
        return Image.new("RGB", (int(region["width"]), int(region["height"])), (shade, shade, 200))

    monkeypatch.setattr(api_mod.capture, "grab", fake_grab)
    monkeypatch.setattr("imiwatch.engine.capture.grab", fake_grab)
    store.update_settings({"default_provider": "mock"})
    a = api_mod.Api()
    yield a
    a._shutdown()


DRAFT = {"name": "在庫", "request": "在庫ありになったら", "region": {"left": 0, "top": 0, "width": 320, "height": 200},
         "plan": PLAN, "interval_seconds": 30, "provider": "mock"}


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("時間内に条件を満たしませんでした")


def test_api_create_run_poll_series_shot(api):
    b = api.bootstrap()
    assert b["ok"] and b["monitors"] == [] and any(p["key"] == "jev-local" for p in b["providers"])
    r = api.create(DRAFT, False)
    assert r["ok"], r
    mid = r["id"]
    assert api.run_now(mid)["ok"]
    ev = wait_for(lambda: [e for e in api.poll(0)["events"] if e["type"] == "result"])
    row = ev[0]["row"]
    assert row["s"] == "ok" and row["shot"].endswith(".jpg") and ev[0]["summary"]["metric"] is not None
    assert any(e["type"] == "notify" for e in api.poll(0)["events"])  # 閾値 0 なので通知される
    series = api.get_series(mid)
    assert series["ok"] and len(series["rows"]) == 1 and set(series["answers"]) == {"in_stock", "level", "btn"}
    img = api.shot(mid, row["shot"])
    assert img["ok"] and img["image"].startswith("data:image/jpeg;base64,")
    assert api.shot(mid, "../settings.json")["ok"] is False  # パスの細工は拒否


def test_api_update_validates_and_resets(api):
    mid = api.create(DRAFT, False)["id"]
    bad = api.update(mid, {"plan": {"questions": {}}})
    assert bad["ok"] is False and "questions" in bad["error"]
    new_plan = json.loads(json.dumps(PLAN))
    new_plan["notify"]["threshold"] = 0.9
    r = api.update(mid, {"plan": new_plan, "name": "  新しい名前 ", "interval_seconds": 3, "provider": ""})
    assert r["ok"] and r["monitor"]["name"] == "新しい名前" and r["monitor"]["interval_seconds"] == 10
    assert r["summary"]["threshold"] == 0.9
    assert api.update(mid, {"provider": "nope"})["ok"] is False


def test_api_trial_and_check(api):
    t = api.trial(DRAFT)
    assert t["ok"] and t["status"] == "ok" and t["image"].startswith("data:image") and set(t["values"]) == set(PLAN["questions"])
    assert api.trial({**DRAFT, "region": None})["ok"] is False
    c = api.check_plan({"questions": {"a": {"type": "choice", "instructions": "x", "criteria": {"p": 1}}}})
    assert c["ok"] is False


def test_api_settings_secrets(api):
    assert api.save_settings({"llm_api_key": "sk-1234567890", "llm_base_url": "http://localhost:1234/v1",
                              "max_shots": "500", "unknown_key": 1})["ok"]
    g = api.get_settings()
    assert "llm_api_key" not in g["settings"] and g["secrets"]["llm_api_key"]["hint"] == "…7890"
    assert g["settings"]["max_shots"] == 500 and "unknown_key" not in g["settings"]
    api.save_settings({"llm_api_key": ""})  # 空欄は変更しない
    assert store.load_settings()["llm_api_key"] == "sk-1234567890"
    api.save_settings({"llm_api_key": "__clear__"})
    assert store.load_settings()["llm_api_key"] == ""
    assert api.save_settings({"default_provider": "nope"})["ok"] is False


def test_api_design_without_llm_falls_back(api):
    r = api.design("在庫ありになったら", DRAFT["region"], "jev-local")
    assert r["ok"] and "簡易設計" in r["note"]
    assert r["plan"]["questions"]["main"]["instructions"].startswith("Does the image")  # jev-local 向けは英語
    assert api.design("", None, "")["ok"] is False


def test_api_delete(api):
    mid = api.create(DRAFT, False)["id"]
    assert api.delete(mid)["ok"]
    assert api.get_monitor(mid)["ok"] is False
    assert not (store.data_dir() / "monitors" / mid).exists()


def test_server_requires_token(api):
    import urllib.request

    from imiwatch.server import serve_in_thread

    httpd, url = serve_in_thread(api)
    base = url.split("/index.html")[0]
    token = url.split("token=")[1]
    try:
        html = urllib.request.urlopen(url).read().decode()
        assert "imiwatch" in html
        req = urllib.request.Request(base + "/api/bootstrap", data=b'{"args": []}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
        req.add_header("X-Imiwatch-Token", token)
        body = json.loads(urllib.request.urlopen(req).read())
        assert body["ok"] and body["version"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/../imiwatch/api.py")
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ Anthropic 形式（/v1/messages）
def test_anthropic_endpoint_and_headers():
    assert llm_client.endpoint("https://llm.example.local", "anthropic") == "https://llm.example.local/v1/messages"
    assert llm_client.endpoint("https://llm.example.local/v1/", "anthropic") == "https://llm.example.local/v1/messages"
    assert llm_client.endpoint("https://gw/api/v1/messages", "anthropic") == "https://gw/api/v1/messages"
    s = {"llm_api_format": "anthropic", "llm_auth_header": "auto"}
    assert llm_client.auth_headers(s, "k") == {"x-api-key": "k"}
    assert llm_client.auth_headers({**s, "llm_auth_header": "bearer"}, "k") == {"Authorization": "Bearer k"}
    assert llm_client.auth_headers({**s, "llm_auth_header": "both"}, "k") == {"x-api-key": "k", "Authorization": "Bearer k"}
    assert llm_client.auth_headers({"llm_api_format": "openai"}, "k") == {"Authorization": "Bearer k"}
    assert llm_client.auth_headers(s, "") == {}


def test_anthropic_request_shape():
    s = {"llm_api_format": "anthropic", "llm_base_url": "https://gw.internal", "llm_api_key": "k", "planner_model": "claude-x"}
    url, headers, payload = llm_client.build_request(s, "SYS", "USER", "data:image/png;base64,QUJD", "claude-x")
    assert url == "https://gw.internal/v1/messages"
    assert headers["anthropic-version"] == "2023-06-01" and headers["x-api-key"] == "k"
    assert payload["system"] == "SYS" and payload["max_tokens"] > 0 and payload["model"] == "claude-x"
    assert "temperature" not in payload and "response_format" not in payload
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assert content[1] == {"type": "text", "text": "USER"}
    assert [m["role"] for m in payload["messages"]] == ["user"]  # system は messages に入れない


def test_anthropic_design_over_http():
    """社内ゲートウェイを模したサーバーに、実際の HTTP で設計と提案を依頼する。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "key": self.headers.get("x-api-key"),
                         "ver": self.headers.get("anthropic-version"), "body": body})
            if "1つだけ" in body["system"]:
                reply = {"id": "badge", "question": {"type": "score", "instructions": "バッジの目立ち方",
                                                     "criteria": ["ない", "小さい", "大きく赤い"]}, "weight": 1}
            else:
                reply = PLAN
            text = "設計しました。\n```json\n" + json.dumps(reply, ensure_ascii=False) + "\n```"
            out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                              "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = {**store.load_settings(), "llm_api_format": "anthropic",
             "llm_base_url": f"http://127.0.0.1:{srv.server_address[1]}", "llm_api_key": "secret",
             "planner_model": "internal-claude"}
        plan, note = planner.make_plan(s, "在庫ありになったら", "data:image/jpeg;base64,QUJD")
        assert plan["metric"]["name"] == "在庫ありらしさ" and "internal-claude" in note
        sug = planner.suggest_question(s, "在庫ありになったら", "score", plan["questions"])
        assert sug["question"]["type"] == "score" and sug["id"] == "badge"
        assert seen[0]["path"] == "/v1/messages" and seen[0]["key"] == "secret" and seen[0]["ver"] == "2023-06-01"
        assert seen[0]["body"]["messages"][0]["content"][0]["type"] == "image"
    finally:
        srv.shutdown()


def test_anthropic_bad_reply_mentions_format(monkeypatch):
    monkeypatch.setattr(llm_client.requests, "post", lambda url, headers, json, timeout, **kw: chat_reply({"x": 1}))
    s = {"llm_api_format": "anthropic", "llm_base_url": "https://gw", "llm_api_key": "k", "planner_model": "m"}
    with pytest.raises(llm_client.LLMError, match="形式"):
        llm_client.chat_json(s, "s", "u")  # OpenAI 形式の応答が返ってきた → 形式の設定を見直すよう案内


def test_api_rejects_bad_llm_format(api):
    assert api.save_settings({"llm_api_format": "gemini"})["ok"] is False
    assert api.save_settings({"llm_api_format": "anthropic", "llm_auth_header": "x-api-key"})["ok"]
    assert store.load_settings()["llm_api_format"] == "anthropic"


# ------------------------------------------------------------------ 通信（待ち時間・プロキシ・理由の説明）
def test_net_uses_short_connect_timeout(monkeypatch):
    from imiwatch import net

    seen = {}
    monkeypatch.setattr(net.requests, "post", lambda url, headers, json, timeout, **kw: seen.setdefault("t", timeout) and Resp(200, {}))
    net.post("https://api.example.com/x", {}, {}, read_timeout=60)
    assert seen["t"] == (net.CONNECT_TIMEOUT, 60)


def test_net_loopback_ignores_proxy(monkeypatch):
    from imiwatch import net

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    assert net.route("http://127.0.0.1:8008") == "直接（このPC）"
    assert "プロキシ http://127.0.0.1:9" in net.route("http://example.com/")


@pytest.mark.parametrize("exc,kind", [
    ("ProxyError", "proxy"), ("SSLError", "ssl"), ("ConnectTimeout", "connect_timeout"),
    ("ReadTimeout", "read_timeout"),
])
def test_net_explains_errors(exc, kind):
    import requests

    from imiwatch import net

    e = getattr(requests.exceptions, exc)("boom")
    err = net.explain(e, "https://api.example.com/v1", (5, 60))
    assert err.kind == kind and "api.example.com" in str(err)


def test_net_refused_is_fast_and_explained():
    from imiwatch import net

    t0 = time.time()
    with pytest.raises(net.NetError) as e:
        net.post("http://127.0.0.1:1/", {}, {}, read_timeout=30)
    assert e.value.kind == "refused" and time.time() - t0 < 3


def test_connection_test_has_deadline(api, monkeypatch):
    from imiwatch import api as api_mod
    from imiwatch.judges.mock import MockJudge

    monkeypatch.setattr(api_mod, "TEST_DEADLINE", 0.5)
    monkeypatch.setattr(MockJudge, "judge", lambda self, *a, **k: time.sleep(3))
    t0 = time.time()
    r = api.test_judge("mock")
    assert r["ok"] is False and "打ち切りました" in r["error"] and time.time() - t0 < 2
