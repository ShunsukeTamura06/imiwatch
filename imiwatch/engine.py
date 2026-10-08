"""見張りの実行部分（撮影 → 差分確認 → 判定 → 指標 → 通知判定 → 記録）。GUI には依存しない。"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from PIL import Image

from . import capture, store
from .judges import JudgeError, make_judge
from .plan import NotifyState, api_questions, compute_metric, normalize_answers


@dataclass
class Result:
    monitor_id: str
    timestamp: str
    status: str  # ok / reused / error
    metric: float | None = None
    values: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    provider: str = ""
    latency_ms: int = 0
    notified: bool = False
    error: str = ""
    image: Image.Image | None = None


class MonitorRuntime:
    """1つの見張りの、実行中の状態（前回の画像・通知の状態など）。"""

    def __init__(self, monitor: store.Monitor):
        self.monitor = monitor
        self.prev_fp: Image.Image | None = None
        self.last_result: Result | None = None
        self.notify_state = NotifyState()
        self._lock = threading.Lock()

    def run_once(
        self,
        settings: dict,
        grab_fn: Callable[[dict], Image.Image] = capture.grab,
        force: bool = False,
        record: bool = True,
    ) -> Result:
        # 定期実行と「今すぐ判定」が重ならないようにする
        with self._lock:
            return self._run_once(settings, grab_fn, force, record)

    def _run_once(self, settings, grab_fn, force, record) -> Result:
        m = self.monitor
        provider = m.provider or settings.get("default_provider", "clef-flash")
        ts = datetime.now().isoformat(timespec="seconds")
        try:
            img = grab_fn(m.region)
        except Exception as e:  # noqa: BLE001
            return self._finish(Result(m.id, ts, "error", provider=provider, error=f"撮影に失敗: {e}"), record)

        small = capture.shrink(img, int(settings.get("max_image_side", 768)))
        fp = capture.fingerprint(small)
        diff = capture.diff_score(self.prev_fp, fp)
        threshold = float(settings.get("diff_threshold", 2.0))

        # 画面がほとんど変わっていなければ、判定を省略して前回の値を使い回す
        if not force and self.last_result and self.last_result.status in ("ok", "reused") and diff < threshold:
            prev = self.last_result
            r = Result(
                m.id, ts, "reused", metric=prev.metric, values=dict(prev.values),
                answers=prev.answers, provider=provider, image=small,
            )
            return self._finish(r, record)

        try:
            judge = make_judge(provider, settings)
            answers, latency = judge.judge_timed(
                capture.to_data_url(small, int(settings.get("jpeg_quality", 85))),
                api_questions(m.plan),
            )
        except JudgeError as e:
            return self._finish(Result(m.id, ts, "error", provider=provider, error=str(e), image=small), record)
        except Exception as e:  # noqa: BLE001
            return self._finish(Result(m.id, ts, "error", provider=provider, error=f"想定外のエラー: {e}", image=small), record)

        self.prev_fp = fp
        values = normalize_answers(m.plan, answers)
        metric = compute_metric(m.plan, values)
        r = Result(m.id, ts, "ok", metric=metric, values=values, answers=answers,
                   provider=provider, latency_ms=latency, image=small)
        # 通知は実際に判定したときだけ数える（使い回しの値で連続回数を稼がない）
        if record:
            r.notified = self.notify_state.update(m.plan, metric)
        return self._finish(r, record)

    def _finish(self, r: Result, record: bool) -> Result:
        if r.status != "error":
            self.last_result = r
        if record:
            store.append_log(r.monitor_id, {
                "timestamp": r.timestamp, "status": r.status,
                "metric": "" if r.metric is None else f"{r.metric:.4f}",
                "values": {k: round(v, 4) for k, v in r.values.items()},
                "provider": r.provider, "latency_ms": r.latency_ms,
                "notified": int(r.notified), "error": r.error,
            })
            if r.image is not None:
                d = store.monitor_dir(r.monitor_id)
                try:
                    r.image.save(d / "latest.jpg", quality=80)
                    if r.notified:
                        r.image.save(d / f"notified_{r.timestamp.replace(':', '')}.jpg", quality=85)
                except OSError:
                    pass
        return r


class Scheduler:
    """見張りごとにスレッドを1本立て、決めた間隔で run_once を呼ぶ。結果は queue に流す。"""

    def __init__(self, get_settings: Callable[[], dict]):
        self.get_settings = get_settings
        self.results: "queue.Queue[Result]" = queue.Queue()
        self._threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self.runtimes: dict[str, MonitorRuntime] = {}

    def runtime(self, monitor: store.Monitor) -> MonitorRuntime:
        rt = self.runtimes.get(monitor.id)
        if rt is None:
            rt = self.runtimes[monitor.id] = MonitorRuntime(monitor)
        rt.monitor = monitor
        return rt

    def is_running(self, monitor_id: str) -> bool:
        t = self._threads.get(monitor_id)
        return bool(t and t[0].is_alive())

    def start(self, monitor: store.Monitor) -> None:
        if self.is_running(monitor.id):
            return
        stop = threading.Event()
        rt = self.runtime(monitor)

        def loop():
            while not stop.is_set():
                started = time.monotonic()
                self.results.put(rt.run_once(self.get_settings()))
                wait = max(1.0, rt.monitor.interval_seconds - (time.monotonic() - started))
                if stop.wait(wait):
                    break

        th = threading.Thread(target=loop, name=f"monitor-{monitor.id}", daemon=True)
        self._threads[monitor.id] = (th, stop)
        th.start()

    def stop(self, monitor_id: str) -> None:
        t = self._threads.pop(monitor_id, None)
        if t:
            t[1].set()

    def stop_all(self) -> None:
        for mid in list(self._threads):
            self.stop(mid)

    def run_now(self, monitor: store.Monitor) -> None:
        """すぐに1回判定する（記録もする）。"""
        rt = self.runtime(monitor)
        threading.Thread(
            target=lambda: self.results.put(rt.run_once(self.get_settings(), force=True)),
            daemon=True,
        ).start()
