"""HTTP の送信をひとまとめにする。

- 接続の待ち時間（connect）は短く、応答の待ち時間（read）は設定どおりにする。
  接続先に複数のアドレスがあると、接続の待ち時間はアドレスの数だけ積み重なるため。
- 自分の PC（127.0.0.1 / localhost）宛ては、プロキシを通さない。
- つながらなかった理由を、日本語の文章で返す。
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import requests

CONNECT_TIMEOUT = 5.0
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
NO_PROXIES = {"http": None, "https": None, "all": None}  # None を指定すると、環境のプロキシ設定が外れる


class NetError(RuntimeError):
    """つながらなかった理由の種類（kind）と、利用者向けの説明を持つ。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOOPBACK


def route(url: str) -> str:
    """この URL へ、直接つなぐか、どのプロキシを通るか。"""
    if is_loopback(url):
        return "直接（このPC）"
    try:
        proxies = requests.utils.get_environ_proxies(url)
    except Exception:  # noqa: BLE001
        proxies = {}
    proxy = proxies.get(urlparse(url).scheme) or proxies.get("all")
    return f"プロキシ {proxy} 経由" if proxy else "直接"


def post(url: str, headers: dict, payload: dict, read_timeout: float,
         connect_timeout: float = CONNECT_TIMEOUT) -> requests.Response:
    timeout = (min(connect_timeout, read_timeout), read_timeout)
    try:
        if is_loopback(url):
            # 自分の PC 宛ては、環境変数や Windows のプロキシ設定があっても直接つなぐ
            return requests.post(url, headers=headers, json=payload, timeout=timeout, proxies=NO_PROXIES)
        return requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise explain(e, url, timeout) from e


def explain(e: Exception, url: str, timeout: tuple[float, float]) -> NetError:
    host = urlparse(url).hostname or url
    via = route(url)
    text = str(e)
    if isinstance(e, requests.exceptions.ProxyError):
        return NetError("proxy", f"プロキシが {host} への接続を通しませんでした（{via}）。"
                                 "社内のプロキシで止められている可能性があります。")
    if isinstance(e, requests.exceptions.SSLError):
        return NetError("ssl", f"{host} の証明書を確認できませんでした（{via}）。"
                               "社内の証明書を使うプロキシの場合は、Windows に証明書が登録されているか確認してください。")
    if isinstance(e, requests.exceptions.ConnectTimeout):
        return NetError("connect_timeout", f"{host} に {timeout[0]:g} 秒以内につながりませんでした（{via}）。"
                                           "ファイアウォールやプロキシで止められているか、URL が違う可能性があります。")
    if isinstance(e, requests.exceptions.ReadTimeout):
        return NetError("read_timeout", f"{host} にはつながりましたが、{timeout[1]:g} 秒以内に応答がありませんでした。")
    if isinstance(e, requests.exceptions.ConnectionError):
        if _is_dns_error(e):
            return NetError("dns", f"{host} の名前を解決できませんでした（{via}）。URL のつづりか、社内の DNS を確認してください。")
        if "refused" in text.lower() or "10061" in text:
            return NetError("refused", f"{host} に接続を拒否されました。サーバーが動いていないか、ポート番号が違う可能性があります。")
        return NetError("connection", f"{host} につながりませんでした（{via}）: {text[:200]}")
    return NetError("other", f"{host} との通信に失敗しました: {text[:200]}")


def _is_dns_error(e: Exception) -> bool:
    cur: BaseException | None = e
    for _ in range(8):
        if cur is None:
            return False
        if isinstance(cur, socket.gaierror) or "getaddrinfo failed" in str(cur) or "Name or service not known" in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__ or (cur.args[0] if cur.args and isinstance(cur.args[0], BaseException) else None)
    return False
