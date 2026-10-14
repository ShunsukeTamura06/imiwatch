"""画面（HTML）から呼ばれる API。

pywebview では `window.pywebview.api.<名前>(...)`、ブラウザでは `POST /api/<名前>` で呼ばれる。
どの関数も {"ok": true, ...} か {"ok": false, "error": "..."} を返す（例外は画面に投げない）。
名前が _ で始まる属性は画面から呼べない（pywebview の仕様）。
"""
from __future__ import annotations

import base64
import functools
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Callable

from PIL import Image, ImageDraw

from . import __version__, capture, net, notify, planner, store
from .engine import MonitorRuntime, Result, Scheduler
from .judges import PROVIDERS, JudgeError, make_judge, provider_label
from .llm_client import LLMError, chat_json, llm_config
from .plan import PlanError, describe_plan, validate_plan
from .region_picker import pick_in_subprocess

TEST_CONNECT_TIMEOUT = 4.0   # 接続テストでの、1つのアドレスあたりの接続の待ち時間
TEST_READ_TIMEOUT = 20.0     # 接続テストでの、応答の待ち時間
TEST_DEADLINE = 30.0         # 接続テスト全体の上限
SHOT_RE = re.compile(r"^[0-9T]{8,20}\.jpg$")
SERIES_CAP = 20000
SPARK_POINTS = 48
EDITABLE = ("name", "request", "region", "plan", "interval_seconds", "provider")
IMAGE_PROVIDERS = {"clef", "clef-flash", "perplexity", "jev-local", "llm", "mock"}


def _safe(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            out = fn(*args, **kwargs)
            return {"ok": True, **(out or {})}
        except (PlanError, LLMError, JudgeError, ValueError, KeyError, RuntimeError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - 画面には必ず文章で返す
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return wrapper


def _data_url(img: Image.Image, max_side: int, quality: int = 80) -> str:
    return capture.to_data_url(capture.shrink(img, max_side), quality)


def _file_data_url(path) -> str | None:
    try:
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def _row(r: dict) -> dict:
    """ログの1行を、画面向けの短い形にする。"""
    return {
        "t": r.get("timestamp", ""), "s": r.get("status", ""), "m": r.get("metric"),
        "v": r.get("values") or {}, "n": str(r.get("notified", "")) in ("1", "True", "true"),
        "e": r.get("error", ""), "shot": r.get("shot", ""), "p": r.get("provider", ""),
        "l": int(float(r.get("latency_ms") or 0)),
    }


def _result_row(r: Result) -> dict:
    return {"t": r.timestamp, "s": r.status, "m": r.metric, "v": {k: round(v, 4) for k, v in r.values.items()},
            "n": r.notified, "e": r.error, "shot": r.shot, "p": r.provider, "l": r.latency_ms}


def _public_answers(answers: dict) -> dict:
    out = {}
    for qid, a in (answers or {}).items():
        if isinstance(a, dict):
            out[qid] = {k: v for k, v in a.items() if k in
                        ("type", "noul", "score", "choice", "confidence", "probabilities", "abstained", "level", "unknown")}
    return out


class Api:
    def __init__(self):
        self._lock = threading.RLock()
        self._settings = store.load_settings()
        self._monitors: list[store.Monitor] = store.load_monitors()
        self._scheduler = Scheduler(lambda: self._settings)
        self._series: dict[str, list[dict]] = {}
        self._errors: dict[str, str] = {}
        self._events: deque = deque(maxlen=1000)
        self._seq = 0
        self._window = None  # pywebview のウィンドウ（範囲選択のときに最小化する）
        self._quit_cb: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._consumer = threading.Thread(target=self._consume, name="results", daemon=True)
        self._consumer.start()
        for m in self._monitors:
            if m.enabled:
                self._scheduler.start(m)

    # ================================================================ 内部
    def _find(self, mid: str) -> store.Monitor:
        with self._lock:
            for m in self._monitors:
                if m.id == mid:
                    return m
        raise KeyError(f"見張りが見つかりません: {mid}")

    def _save_monitors(self) -> None:
        with self._lock:
            store.save_monitors(self._monitors)

    def _rows(self, mid: str) -> list[dict]:
        with self._lock:
            if mid not in self._series:
                self._series[mid] = [_row(r) for r in store.read_log(mid, limit=SERIES_CAP)]
            return self._series[mid]

    def _emit(self, ev: dict) -> None:
        with self._lock:
            self._seq += 1
            ev["seq"] = self._seq
            self._events.append(ev)

    def _summary(self, m: store.Monitor, thumb: bool = False) -> dict:
        rows = self._rows(m.id)
        valid = [r for r in rows if r["s"] in ("ok", "reused") and r["m"] is not None]
        last = rows[-1] if rows else None
        n = m.plan.get("notify", {})
        out = {
            "id": m.id, "name": m.name, "request": m.request,
            "running": self._scheduler.is_running(m.id),
            "error": self._errors.get(m.id, ""),
            "metric": valid[-1]["m"] if valid else None,
            "last_time": last["t"] if last else "",
            "last_status": last["s"] if last else "",
            "metric_name": m.plan.get("metric", {}).get("name", "指標"),
            "notify_enabled": n.get("enabled", True), "op": n.get("op", ">="),
            "threshold": n.get("threshold", 0.7), "consecutive": n.get("consecutive", 1),
            "interval_seconds": m.interval_seconds,
            "provider": m.provider, "provider_used": m.provider or self._settings.get("default_provider", ""),
            "spark": [r["m"] for r in valid[-SPARK_POINTS:]],
            "count": len(rows),
        }
        if thumb:
            p = store.monitor_dir(m.id) / "latest.jpg"
            if p.exists():
                try:
                    out["thumb"] = _data_url(Image.open(p), 200, 70)
                except OSError:
                    pass
        return out

    def _consume(self) -> None:
        """判定結果を受け取り、記録の更新・通知・画面への知らせを行う。"""
        while not self._stop.is_set():
            try:
                r: Result = self._scheduler.results.get(timeout=0.5)
            except Exception:  # noqa: BLE001 - queue.Empty
                continue
            try:
                self._handle_result(r)
            except Exception:  # noqa: BLE001 - 1件の失敗で受け取りを止めない
                pass

    def _handle_result(self, r: Result) -> None:
        try:
            m = self._find(r.monitor_id)
        except KeyError:
            return
        row = _result_row(r)
        with self._lock:
            rows = self._rows(m.id)
            rows.append(row)
            if len(rows) > SERIES_CAP:
                del rows[: len(rows) - SERIES_CAP]
            if r.status == "error":
                self._errors[m.id] = r.error
            else:
                self._errors.pop(m.id, None)
        summary = self._summary(m)
        if r.image is not None:
            summary["thumb"] = _data_url(r.image, 200, 70)
        self._emit({"type": "result", "monitor_id": m.id, "row": row, "summary": summary,
                    "answers": _public_answers(r.answers)})
        if r.notified:
            metric_name = m.plan["metric"]["name"]
            msg = f"{m.plan['notify'].get('message', '')}（{metric_name} {r.metric:.2f}）"
            img = str(store.monitor_dir(m.id) / "latest.jpg")
            native = notify.send_toast(f"imiwatch: {m.name}", msg, img)
            self._emit({"type": "notify", "monitor_id": m.id, "title": m.name, "message": msg, "native": native})

    def _language(self, provider: str) -> str:
        return planner.question_language(provider or self._settings.get("default_provider", ""))

    def _region_image(self, region: dict | None) -> Image.Image | None:
        if not region:
            return None
        try:
            return capture.grab(region)
        except Exception:  # noqa: BLE001
            return None

    def _region_data_url(self, region: dict | None) -> str | None:
        img = self._region_image(region)
        if img is None:
            return None
        s = self._settings
        return _data_url(img, int(s.get("max_image_side", 768)), int(s.get("jpeg_quality", 85)))

    def _draft_monitor(self, draft: dict) -> store.Monitor:
        region = draft.get("region")
        if not region:
            raise ValueError("見張る範囲を選んでください")
        plan = validate_plan(draft.get("plan"))
        interval = max(10, int(draft.get("interval_seconds") or plan.get("suggested_interval_seconds") or 300))
        return store.Monitor(
            name=(draft.get("name") or plan["title"]).strip()[:60],
            request=(draft.get("request") or "").strip(),
            region={k: int(region[k]) for k in ("left", "top", "width", "height")},
            plan=plan, interval_seconds=interval,
            provider=draft.get("provider") or "",
        )

    def _shutdown(self, *args) -> None:
        self._stop.set()
        self._scheduler.stop_all()

    # ================================================================ 起動時
    @_safe
    def bootstrap(self):
        with self._lock:
            monitors = [self._summary(m, thumb=True) for m in self._monitors]
        return {
            "version": __version__,
            "providers": [{"key": k, "label": provider_label(k), "images": k in IMAGE_PROVIDERS or (
                k == "systemone" and self._settings.get("systemone_image_mode", "none") != "none")}
                for k in PROVIDERS],
            "default_provider": self._settings.get("default_provider", ""),
            "monitors": monitors,
            "seq": self._seq,
            "native_notify": sys.platform == "win32",
            "data_dir": str(store.data_dir()),
            "browser_mode": self._window is None,
        }

    @_safe
    def poll(self, since: int = 0):
        with self._lock:
            events = [e for e in self._events if e["seq"] > since]
            oldest = self._events[0]["seq"] if self._events else self._seq + 1
            reset = since and since < oldest - 1 and since < self._seq
        return {"seq": self._seq, "events": events, "reset": bool(reset)}

    # ================================================================ 見張り
    @_safe
    def list_monitors(self):
        with self._lock:
            return {"monitors": [self._summary(m, thumb=True) for m in self._monitors]}

    @_safe
    def get_monitor(self, mid: str):
        m = self._find(mid)
        return {"monitor": m.to_dict(), "summary": self._summary(m), "text": describe_plan(m.plan)}

    @_safe
    def get_series(self, mid: str):
        self._find(mid)
        rt = self._scheduler.runtimes.get(mid)
        last = rt.last_result if rt else None
        return {"rows": list(self._rows(mid)), "answers": _public_answers(last.answers) if last else {}}

    @_safe
    def shot(self, mid: str, name: str):
        if not SHOT_RE.match(name or ""):
            raise ValueError("画像の名前が正しくありません")
        p = store.shots_dir(mid) / name
        return {"image": _file_data_url(p)}

    @_safe
    def latest_image(self, mid: str):
        return {"image": _file_data_url(store.monitor_dir(mid) / "latest.jpg")}

    @_safe
    def start(self, mid: str):
        m = self._find(mid)
        m.enabled = True
        self._errors.pop(mid, None)
        self._save_monitors()
        self._scheduler.start(m)
        return {"summary": self._summary(m)}

    @_safe
    def stop(self, mid: str):
        m = self._find(mid)
        m.enabled = False
        self._save_monitors()
        self._scheduler.stop(mid)
        return {"summary": self._summary(m)}

    @_safe
    def run_now(self, mid: str):
        self._scheduler.run_now(self._find(mid))
        return {}

    @_safe
    def delete(self, mid: str):
        m = self._find(mid)
        self._scheduler.stop(mid)
        with self._lock:
            self._monitors = [x for x in self._monitors if x.id != mid]
            self._series.pop(mid, None)
        self._save_monitors()
        store.delete_monitor_data(mid)
        self._emit({"type": "deleted", "monitor_id": mid})
        return {"name": m.name}

    @_safe
    def create(self, draft: dict, start: bool = True):
        m = self._draft_monitor(draft)
        m.enabled = bool(start)
        with self._lock:
            self._monitors.append(m)
        self._save_monitors()
        if start:
            self._scheduler.start(m)
        return {"summary": self._summary(m), "id": m.id}

    @_safe
    def update(self, mid: str, patch: dict):
        m = self._find(mid)
        reset = False
        for key in EDITABLE:
            if key not in patch:
                continue
            val = patch[key]
            if key == "plan":
                val = validate_plan(val)
                reset = True
            elif key == "region":
                val = {k: int(val[k]) for k in ("left", "top", "width", "height")}
                reset = True
            elif key == "interval_seconds":
                val = max(10, int(val))
            elif key in ("name", "request"):
                val = str(val).strip()
                if key == "name" and not val:
                    continue
            elif key == "provider":
                val = val or ""
                if val and val not in PROVIDERS:
                    raise ValueError(f"不明な判定モデルです: {val}")
                self._errors.pop(mid, None)
            setattr(m, key, val)
        if "notify_enabled" in patch:
            m.plan["notify"]["enabled"] = bool(patch["notify_enabled"])
        self._save_monitors()
        rt = self._scheduler.runtimes.get(mid)
        if rt:
            rt.monitor = m
            if reset:
                rt.reset()
        return {"monitor": m.to_dict(), "summary": self._summary(m), "text": describe_plan(m.plan)}

    # ================================================================ 範囲と設計
    @_safe
    def pick_region(self):
        win = self._window
        if win is not None:
            try:
                win.minimize()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.35)  # 最小化しきるのを待つ
        try:
            region = pick_in_subprocess()
        finally:
            if win is not None:
                try:
                    win.restore()
                except Exception:  # noqa: BLE001
                    pass
        if not region:
            return {"cancelled": True}
        img = self._region_image(region)
        return {"region": region, "image": _data_url(img, 900, 82) if img else None}

    @_safe
    def region_image(self, region: dict):
        img = self._region_image(region)
        return {"image": _data_url(img, 900, 82) if img else None}

    @_safe
    def design(self, request: str, region: dict | None = None, provider: str = ""):
        if not (request or "").strip():
            raise ValueError("知りたいことを書いてください")
        plan, note = planner.make_plan(self._settings, request.strip(), self._region_data_url(region),
                                       self._language(provider))
        return {"plan": plan, "note": note, "text": describe_plan(plan)}

    @_safe
    def suggest(self, request: str, qtype: str, questions: dict | None = None,
                region: dict | None = None, provider: str = ""):
        return planner.suggest_question(self._settings, request or "", qtype, questions or {},
                                        self._region_data_url(region), self._language(provider))

    @_safe
    def check_plan(self, plan: dict):
        p = validate_plan(plan)
        return {"plan": p, "text": describe_plan(p)}

    @_safe
    def trial(self, draft: dict):
        m = self._draft_monitor(draft)
        r = MonitorRuntime(m).run_once(self._settings, force=True, record=False)
        n = m.plan["notify"]
        hit = r.metric is not None and (r.metric >= n["threshold"] if n["op"] == ">=" else r.metric <= n["threshold"])
        return {"status": r.status, "metric": r.metric, "values": r.values, "answers": _public_answers(r.answers),
                "latency_ms": r.latency_ms, "provider": r.provider, "error": r.error, "hit": bool(hit),
                "image": _data_url(r.image, 900, 82) if r.image is not None else None}

    # ================================================================ 設定
    @_safe
    def get_settings(self):
        s = dict(self._settings)
        secrets = {}
        for k in store.SECRET_KEYS:
            v = s.pop(k, "") or ""
            secrets[k] = {"set": bool(v), "hint": ("…" + v[-4:]) if len(v) >= 8 else ("設定済み" if v else "")}
        return {"settings": s, "secrets": secrets, "path": str(store.settings_path()),
                "providers": [{"key": k, "label": provider_label(k)} for k in PROVIDERS]}

    @_safe
    def save_settings(self, patch: dict):
        clean: dict[str, Any] = {}
        for k, v in (patch or {}).items():
            if k not in store.DEFAULT_SETTINGS:
                continue
            if k in store.SECRET_KEYS:
                if v is None or v == "":
                    continue  # 空欄は「変更しない」
                v = "" if v == "__clear__" else str(v).strip()
            elif k in ("max_image_side", "shot_side", "max_shots", "request_timeout"):
                v = max(1, int(float(v)))
            elif k == "diff_threshold":
                v = max(0.0, float(v))
            elif k == "connect_timeout":
                v = min(60.0, max(1.0, float(v)))
            elif k == "default_provider" and v not in PROVIDERS:
                raise ValueError(f"不明な判定モデルです: {v}")
            elif k == "llm_api_format" and v not in ("openai", "anthropic"):
                raise ValueError(f"LLM の形式は openai か anthropic です: {v}")
            elif k == "llm_auth_header" and v not in ("auto", "bearer", "x-api-key", "both"):
                raise ValueError(f"認証ヘッダーの指定が正しくありません: {v}")
            else:
                v = str(v).strip() if isinstance(v, str) else v
            clean[k] = v
        new = store.update_settings(clean)
        self._settings.clear()
        self._settings.update(new)
        return {}

    def _provider_url(self, provider: str) -> str:
        s = self._settings
        if provider in ("clef", "clef-flash"):
            return "https://api.cloudflare.com/"
        if provider == "perplexity":
            return "https://api.perplexity.ai/"
        if provider == "systemone":
            return s.get("systemone_base_url", "")
        if provider == "jev-local":
            return s.get("jevlocal_base_url", "")
        if provider == "llm":
            return s.get("llm_base_url", "")
        return ""

    def _test_settings(self) -> dict:
        """接続テスト用: 接続は短く、応答も長くは待たない。"""
        return {**self._settings, "connect_timeout": TEST_CONNECT_TIMEOUT,
                "request_timeout": min(float(self._settings.get("request_timeout", 60)), TEST_READ_TIMEOUT)}

    @staticmethod
    def _with_deadline(fn: Callable[[], Any], seconds: float, what: str) -> Any:
        """fn を別スレッドで動かし、seconds 秒で打ち切る（打ち切っても裏の通信は放っておく）。"""
        box: dict = {}

        def run():
            try:
                box["value"] = fn()
            except Exception as e:  # noqa: BLE001
                box["error"] = e

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(seconds)
        if th.is_alive():
            raise RuntimeError(f"{what}: {seconds:g} 秒たっても結果が返らないため、打ち切りました。")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    @_safe
    def test_judge(self, provider: str = ""):
        provider = provider or self._settings.get("default_provider", "")
        img = Image.new("RGB", (320, 200), (70, 120, 200))
        ImageDraw.Draw(img).rectangle((110, 60, 210, 140), fill=(250, 250, 250))
        q = {"square": {"type": "noul", "instructions": "Is there a white square in the image?"}}
        judge = make_judge(provider, self._test_settings())
        judge.timeout = min(judge.timeout, TEST_READ_TIMEOUT)  # jev-local は普段は長めに待つが、テストでは待たない
        url = self._provider_url(provider)
        via = net.route(url) if url else ""
        try:
            answers, ms = self._with_deadline(lambda: judge.judge_timed(capture.to_data_url(img), q),
                                              TEST_DEADLINE, "接続テスト")
        except (JudgeError, RuntimeError) as e:
            raise RuntimeError(f"{e}" + (f"（{via}）" if via and via not in str(e) else "")) from e
        a = answers.get("square", {})
        return {"provider": provider, "latency_ms": ms, "route": via,
                "answer": a.get("noul") if "noul" in a else ("判断保留" if a.get("abstained") else None)}

    @_safe
    def test_llm(self):
        settings = self._test_settings()
        base, _, model = llm_config(settings)
        t0 = time.perf_counter()
        data = self._with_deadline(
            lambda: chat_json(settings, 'JSON だけで {"ok": true} と答えてください。', "接続テスト",
                              timeout=settings["request_timeout"]),
            TEST_DEADLINE, "接続テスト")
        return {"latency_ms": int((time.perf_counter() - t0) * 1000), "model": model, "base_url": base,
                "route": net.route(base), "reply": data}

    @_safe
    def open_data_dir(self):
        path = str(store.data_dir())
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"path": path}

    @_safe
    def quit(self):
        self._shutdown()
        if self._window is not None:
            threading.Thread(target=self._window.destroy, daemon=True).start()
        elif self._quit_cb:
            threading.Thread(target=self._quit_cb, daemon=True).start()
        return {}
