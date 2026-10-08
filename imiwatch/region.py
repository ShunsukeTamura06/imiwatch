"""画面全体に半透明の幕をかけ、ドラッグで見張る範囲を選ぶ。"""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import capture

MIN_SIZE = 16


def select_region(root: tk.Misc, on_done: Callable[[dict | None], None]) -> None:
    """範囲を選ばせ、{"left","top","width","height"}（物理ピクセル）か None を on_done に渡す。"""
    vs = capture.virtual_screen()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.35)
    win.geometry(f"{vs['width']}x{vs['height']}+{vs['left']}+{vs['top']}")
    win.configure(bg="black", cursor="crosshair")

    canvas = tk.Canvas(win, bg="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        vs["width"] // 2,
        40,
        text="ドラッグして見張る範囲を選んでください（Esc で中止）",
        fill="white",
        font=("Yu Gothic UI", 18, "bold"),
    )

    state: dict = {"start": None, "rect": None}

    def to_canvas(x_root: int, y_root: int) -> tuple[int, int]:
        return x_root - vs["left"], y_root - vs["top"]

    def on_press(e):
        state["start"] = (e.x_root, e.y_root)
        cx, cy = to_canvas(e.x_root, e.y_root)
        if state["rect"]:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(cx, cy, cx, cy, outline="#ff4d4d", width=3, fill="white")

    def on_drag(e):
        if not state["start"]:
            return
        x0, y0 = to_canvas(*state["start"])
        x1, y1 = to_canvas(e.x_root, e.y_root)
        canvas.coords(state["rect"], x0, y0, x1, y1)

    def finish(result):
        win.destroy()
        root.after(50, lambda: on_done(result))

    def on_release(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        x1, y1 = e.x_root, e.y_root
        left, top = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < MIN_SIZE or h < MIN_SIZE:
            state["start"] = None
            return  # 小さすぎるのでやり直し
        finish({"left": int(left), "top": int(top), "width": int(w), "height": int(h)})

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Escape>", lambda e: finish(None))
    win.focus_force()
