"""imiwatch の起動口。

通常は pywebview のウィンドウで画面（imiwatch/web/）を開く。
pywebview が使えないとき、または --browser を付けたときは、ローカルサーバーを立ててブラウザで開く。
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from . import store
from .api import Api

WEB_DIR = Path(__file__).resolve().parent / "web"


def _enable_dpi_awareness() -> None:
    """Windows の拡大表示でも、画面座標と撮影座標（物理ピクセル）をそろえる。"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def use_system_certificates() -> bool:
    """HTTPS の証明書確認に、OS の証明書ストアを使う。

    社内のプロキシや社内 API が社内の認証局の証明書を使っていても、
    Windows に登録済みなら「certificate verify failed」にならない。
    """
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except Exception:  # noqa: BLE001 - 入っていなければ、これまでどおり certifi を使う
        return False


def run_window(api: Api, debug: bool = False) -> None:
    import webview

    win = webview.create_window(
        "imiwatch", url=str(WEB_DIR / "index.html"), js_api=api,
        width=1320, height=860, min_size=(980, 640),
        text_select=True, background_color="#F2F4F7",
    )
    api._window = win
    win.events.closed += api._shutdown
    webview.start(http_server=True, debug=debug, private_mode=False,
                  storage_path=str(store.data_dir() / "webview"))


def run_browser(api: Api, port: int = 0) -> None:
    from .server import make_server

    httpd, url = make_server(api, port=port)
    done = threading.Event()

    def stop():
        api._shutdown()
        httpd.shutdown()
        done.set()

    api._quit_cb = stop
    print(f"imiwatch をブラウザで開きます: {url}\n終了するには画面の「終了」か Ctrl+C を押してください。", flush=True)
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        stop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="imiwatch")
    ap.add_argument("--browser", action="store_true", help="専用ウィンドウではなくブラウザで開く")
    ap.add_argument("--port", type=int, default=0, help="--browser のときの待ち受けポート（既定は空いている番号）")
    ap.add_argument("--debug", action="store_true", help="画面の開発者ツールを有効にする")
    args = ap.parse_args(argv)

    _enable_dpi_awareness()
    use_system_certificates()
    api = Api()
    if not args.browser:
        try:
            run_window(api, debug=args.debug)
            return
        except Exception as e:  # noqa: BLE001 - WebView2 が無いなどの場合はブラウザで続ける
            print(f"専用ウィンドウを開けませんでした（{e}）。ブラウザで開きます。", file=sys.stderr, flush=True)
            api._window = None
    run_browser(api, args.port)
