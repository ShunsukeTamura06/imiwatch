"""範囲選択を別プロセスで行う入口。

画面（pywebview / ブラウザ）とは別のプロセスで tkinter の半透明の幕を出し、
選ばれた範囲を JSON 1行で標準出力に書いて終わる。

    python -m imiwatch.region_picker
    → {"left": 10, "top": 20, "width": 300, "height": 200}   または  {"cancelled": true}
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parents[1]


def pick_in_subprocess(timeout: float = 300) -> dict | None:
    """別プロセスで範囲を選ばせる。中止されたら None。"""
    exe = sys.executable
    # pythonw.exe でも tkinter は動くので、そのまま使う
    try:
        proc = subprocess.run(
            [exe, "-m", "imiwatch.region_picker"],
            capture_output=True, text=True, timeout=timeout, cwd=str(PACKAGE_PARENT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return None
    line = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        data = json.loads(line[0])
    except json.JSONDecodeError:
        raise RuntimeError(f"範囲選択を起動できませんでした: {(proc.stderr or proc.stdout)[-400:]}")
    if data.get("cancelled"):
        return None
    if "error" in data:
        raise RuntimeError(data["error"])
    return {k: int(data[k]) for k in ("left", "top", "width", "height")}


def _enable_dpi_awareness() -> None:
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


def main() -> None:
    _enable_dpi_awareness()
    try:
        import tkinter as tk

        from .region import select_region
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"tkinter を読み込めません（Python の再インストールで tcl/tk を含めてください）: {e}"}))
        return

    root = tk.Tk()
    root.withdraw()
    result: dict = {"cancelled": True}

    def done(region):
        if region:
            result.clear()
            result.update(region)
        root.after(10, root.destroy)

    root.after(50, lambda: select_region(root, done))
    root.mainloop()
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
