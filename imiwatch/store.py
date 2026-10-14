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
    # jev-local（imajev-4b を自前の GPU サーバーで動かす。SSH トンネル越しの 127.0.0.1:8008 を想定）
    "jevlocal_base_url": "http://127.0.0.1:8008",
    "jevlocal_api_key": "",
    # 設計役の LLM と、LLM による判定の代用（OpenAI 互換の Chat Completions か、Anthropic 形式の Messages）
    # OpenRouter・OpenAI・Azure 以外の互換サーバー・LM Studio・Ollama・vLLM などを base URL で指定する
    "llm_base_url": "https://openrouter.ai/api/v1",
    "llm_api_format": "openai",   # openai（/chat/completions）か anthropic（/v1/messages）
    "llm_auth_header": "auto",    # auto / bearer / x-api-key / both
    "llm_api_key": "",
    "planner_model": "openrouter/auto",
    "llm_judge_model": "openrouter/auto",
    # 画像と差分
    "max_image_side": 768,
    "jpeg_quality": 85,
    "diff_threshold": 2.0,  # 前回と比べた平均画素差（0-255）。これ未満なら判定を省略
    "request_timeout": 60,
    # グラフで振り返るための、判定ごとの縮小画像
    "shot_side": 480,
    "max_shots": 3000,  # 見張りごとに残す枚数（古いものから消す）
}

# 画面に値をそのまま返さない項目
SECRET_KEYS = ("cloudflare_api_token", "perplexity_api_key", "systemone_api_key", "jevlocal_api_key", "llm_api_key")

_ENV_KEYS = {
    "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
    "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "systemone_api_key": "TYPESAFE_API_KEY",
    "jevlocal_api_key": "KEV_API_KEY",
}
# 設計役 LLM のキーは、先に見つかった環境変数を使う
_LLM_KEY_ENVS = ("LLM_API_KEY", "OPENROUTER_API_KEY")


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_file_settings() -> dict[str, Any]:
    """settings.json に保存されている値だけ（環境変数は混ぜない）。古い項目名は移行する。"""
    with _lock:
        p = settings_path()
        raw: dict[str, Any] = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        # v0.1 の OpenRouter 専用設定 → OpenAI 互換設定
        old_key = raw.pop("openrouter_api_key", None)
        if old_key and not raw.get("llm_api_key"):
            raw["llm_api_key"] = old_key
        return raw


def load_settings() -> dict[str, Any]:
    """実際に使う設定（既定値 ← settings.json ← 空欄だけ環境変数で補う）。"""
    with _lock:
        s = dict(DEFAULT_SETTINGS)
        s.update(load_file_settings())
        for key, env in _ENV_KEYS.items():
            if not s.get(key) and os.environ.get(env):
                s[key] = os.environ[env]
        if not s.get("llm_api_key"):
            for env in _LLM_KEY_ENVS:
                if os.environ.get(env):
                    s["llm_api_key"] = os.environ[env]
                    break
        return s


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """settings.json に patch を重ねて保存し、実際に使う設定を返す。"""
    with _lock:
        raw = load_file_settings()
        raw.update(patch)
        save_settings(raw)
        return load_settings()


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
    "shot",  # その判定の縮小画像のファイル名（shots/ の中）
]


def monitor_dir(monitor_id: str) -> Path:
    d = data_dir() / "monitors" / monitor_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(monitor_id: str) -> Path:
    return monitor_dir(monitor_id) / "log.csv"


def _migrate_log_header(p: Path) -> None:
    """古い列構成のログを、今の列構成に書き直す（足りない列は空欄）。"""
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == LOG_FIELDS:
            return
        rows = list(reader)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) or "" for k in LOG_FIELDS})
    tmp.replace(p)


def append_log(monitor_id: str, row: dict) -> None:
    with _lock:
        p = log_path(monitor_id)
        new = not p.exists()
        if not new:
            _migrate_log_header(p)
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


def shots_dir(monitor_id: str) -> Path:
    d = monitor_dir(monitor_id) / "shots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prune_shots(monitor_id: str, keep: int) -> int:
    """縮小画像を新しい順に keep 枚だけ残す。消した枚数を返す。"""
    files = sorted(shots_dir(monitor_id).glob("*.jpg"))
    old = files[: max(0, len(files) - max(1, keep))]
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    return len(old)


def delete_monitor_data(monitor_id: str) -> None:
    import shutil

    with _lock:
        shutil.rmtree(data_dir() / "monitors" / monitor_id, ignore_errors=True)
