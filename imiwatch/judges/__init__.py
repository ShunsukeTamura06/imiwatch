"""判定モデルの切り替え口。

どの判定モデルも「画像（data URL）＋問いの束 → SystemOne 形式の answers」を返す。
新しいモデルを足すときは Judge を継承したクラスを作り、PROVIDERS に登録する。
"""
from __future__ import annotations

from .base import Judge, JudgeError
from .clef import ClefJudge
from .jevlocal import JevLocalJudge
from .llm import LLMJudge
from .mock import MockJudge
from .perplexity import PerplexityJudge
from .systemone import SystemOneJudge

# 画面に表示する名前 → 生成関数
PROVIDERS = {
    "clef-flash": ("Cloudflare Clef-flash（速い・画像可）", lambda s: ClefJudge(s, "clef-flash")),
    "clef": ("Cloudflare Clef（精度重視・画像可）", lambda s: ClefJudge(s, "clef")),
    "perplexity": ("Perplexity Decisions API（画像可）", lambda s: PerplexityJudge(s)),
    "jev-local": ("jev-local・imajev-4b（自前サーバー・オープンウェイト・画像可）", lambda s: JevLocalJudge(s)),
    "systemone": ("SystemOne 互換（TypeSafe Jev など）", lambda s: SystemOneJudge(s)),
    "llm": ("LLM で代用（OpenRouter・遅い・確率は非較正）", lambda s: LLMJudge(s)),
    "mock": ("モック（APIを使わない動作確認用）", lambda s: MockJudge(s)),
}


def provider_label(key: str) -> str:
    return PROVIDERS.get(key, (key,))[0]


def make_judge(provider: str, settings: dict) -> Judge:
    if provider not in PROVIDERS:
        raise JudgeError(f"不明な判定モデルです: {provider}")
    return PROVIDERS[provider][1](settings)


__all__ = ["Judge", "JudgeError", "PROVIDERS", "make_judge", "provider_label"]
