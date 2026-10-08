"""設定・見張りの定義・判定ログの保存先。

保存先は Windows なら %APPDATA%\\imiwatch、それ以外は ~/.imiwatch。
環境変数 IMIWATCH_HOME で上書きできる（テスト用）。
"""
from __future__ import annotations

import csv
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_lock = threading.RLock()


def data_dir() -> Path:
    env = os.environ.get("IMIWATCH_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt" and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / "imiwatch"
    else:
        base = Path.home() / ".imiwatch"
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------- 設定

DEFAULT_SETTINGS: dict[str, Any] = {
    # 既定の判定モデル（見張りごとに上書きできる）
    "default_provider": "clef-flash",
    # Cloudflare Workers AI（Clef / Clef-flash）
    "cloudflare_account_id": "",
    "cloudflare_api_token": "",
    # Perplexity Decisions API
    "perplexity_api_key": "",
    "perplexity_model": "pplx-decider-v1.1-27b",
    # SystemOne 互換の任意エンドポイント（TypeSafe Jev など）
    "systemone_base_url": "https://api.typesafe.ai/v1/systemone",
    "systemone_api_key": "",
    "systemone_model": "jev-latest",
    "systemone_image_mode": "none",  # none / clef / perplexity
    # OpenRouter（判定の設計役 LLM と、LLM による判定の代用）
    "openrouter_api_key": "",
    "planner_model": "openrouter/auto",
    "llm_judge_model": "openrouter/auto",
    # 画像と差分
    "max_image_side": 768,
    "jpeg_quality": 85,
    "diff_threshold": 2.0,  # 前回と比べた平均画素差（0-255）。これ未満なら判定を省略
    "request_timeout": 60,
}

_ENV_KEYS = {
    "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
    "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "systemone_api_key": "TYPESAFE_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
}


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    with _lock:
        s = dict(DEFAULT_SETTINGS)
        p = settings_path()
        if p.exists():
            try:
                s.update(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        # 空欄のキーは環境変数から補う
        for key, env in _ENV_KEYS.items():
            if not s.get(key) and os.environ.get(env):
                s[key] = os.environ[env]
        return s


def save_settings(s: dict[str, Any]) -> None:
    with _lock:
        settings_path().write_text(
            json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ---------------------------------------------------------------- 見張り


@dataclass
class Monitor:
    name: str
    request: str  # ユーザーが書いた「何を知りたいか」
    region: dict  # {"left", "top", "width", "height"}（物理ピクセル）
    plan: dict
    interval_seconds: int = 300
    provider: str = ""  # 空なら既定の判定モデル
    enabled: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Monitor":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def monitors_path() -> Path:
    return data_dir() / "monitors.json"


def load_monitors() -> list[Monitor]:
    with _lock:
        p = monitors_path()
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [Monitor.from_dict(d) for d in raw]


def save_monitors(monitors: list[Monitor]) -> None:
    with _lock:
        monitors_path().write_text(
            json.dumps([m.to_dict() for m in monitors], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------- 判定ログ

LOG_FIELDS = [
    "timestamp",
    "status",  # ok / reused / error
    "metric",
    "values",  # 問いごとの 0-1 値（JSON）
    "provider",
    "latency_ms",
    "notified",
    "error",
]


def monitor_dir(monitor_id: str) -> Path:
    d = data_dir() / "monitors" / monitor_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(monitor_id: str) -> Path:
    return monitor_dir(monitor_id) / "log.csv"


def append_log(monitor_id: str, row: dict) -> None:
    with _lock:
        p = log_path(monitor_id)
        new = not p.exists()
        with p.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            out = {k: row.get(k, "") for k in LOG_FIELDS}
            if isinstance(out["values"], dict):
                out["values"] = json.dumps(out["values"], ensure_ascii=False)
            w.writerow(out)


def read_log(monitor_id: str, limit: int | None = None) -> list[dict]:
    with _lock:
        p = log_path(monitor_id)
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    if limit:
        rows = rows[-limit:]
    for r in rows:
        try:
            r["values"] = json.loads(r["values"]) if r.get("values") else {}
        except json.JSONDecodeError:
            r["values"] = {}
        try:
            r["metric"] = float(r["metric"]) if r.get("metric") not in ("", None) else None
        except ValueError:
            r["metric"] = None
    return rows


def delete_monitor_data(monitor_id: str) -> None:
    import shutil

    with _lock:
        shutil.rmtree(data_dir() / "monitors" / monitor_id, ignore_errors=True)
