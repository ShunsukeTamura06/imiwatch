"""見た目の定義（色・文字・ttk のスタイル）。

方針: 机の端に置いておく「計器」。普段は静かで、知らせる瞬間だけ目立つ。
色を使うのは「測っている値（藍）」と「知らせる瞬間（琥珀）」の2つだけ。
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- 色
BG = "#F4F5F7"        # 窓の地
SURFACE = "#FFFFFF"   # 面（一覧・グラフ・入力欄）
INK = "#1C2230"       # 本文
MUTED = "#6B7280"     # 補足
LINE = "#E1E4EA"      # 罫線
HOVER = "#ECEEF2"     # ボタンのホバー
INDIGO = "#3A46C8"    # 指標・主ボタン
INDIGO_DARK = "#2E389F"
INDIGO_SOFT = "#E7E9FA"
AMBER = "#E8A33D"     # 通知・閾値
AMBER_SOFT = "#FDF3E3"
GREEN = "#2E9E6A"     # 動作中
RED = "#C8443A"       # エラー

SERIES = ["#8F96B3", "#B9A27A", "#7FA9A0", "#B48DA8", "#9AA88A", "#A39184"]  # 観点ごとの細い線（控えめ）


def _family(candidates: list[str], fallback: str) -> str:
    available = set(tkfont.families())
    for c in candidates:
        if c in available:
            return c
    return fallback


class Fonts:
    def __init__(self, root: tk.Misc):
        ui = _family(["Yu Gothic UI", "Meiryo UI", "Noto Sans CJK JP", "Noto Sans JP"], "TkDefaultFont")
        num = _family(["Segoe UI Variable Display", "Segoe UI", "Noto Sans", "DejaVu Sans"], ui)
        mono = _family(["Cascadia Mono", "Consolas", "DejaVu Sans Mono"], "TkFixedFont")
        self.ui = ui
        self.body = (ui, 10)
        self.small = (ui, 9)
        self.strong = (ui, 10, "bold")
        self.title = (ui, 15, "bold")
        self.heading = (ui, 12, "bold")
        self.brand = (num, 15, "bold")
        self.kpi = (num, 20)
        self.kpi_label = (ui, 9)
        self.mono = (mono, 9)


def dpi_scale(root: tk.Misc) -> float:
    """96dpi を 1.0 とした拡大率（Windows の 125% なら 1.25）。"""
    try:
        return max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        return 1.0


def apply(root: tk.Tk) -> Fonts:
    f = Fonts(root)
    root.configure(bg=BG)
    # 既定の名前付きフォントだけを差し替える（"*Font" を使うと ttk のスタイルの文字指定より優先されてしまう）
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
        try:
            tkfont.nametofont(name).configure(family=f.ui, size=10)
        except tk.TclError:
            pass
    root.option_add("*TCombobox*Listbox.font", f.body)
    root.option_add("*TCombobox*Listbox.selectBackground", INDIGO)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")

    s = ttk.Style(root)
    s.theme_use("clam")
    pad = (12, 6)

    s.configure(".", background=BG, foreground=INK, bordercolor=LINE, lightcolor=BG, darkcolor=BG,
                troughcolor=BG, focuscolor=INDIGO, font=f.body, selectbackground=INDIGO, selectforeground="white")
    s.configure("TFrame", background=BG)
    s.configure("Surface.TFrame", background=SURFACE)
    s.configure("TLabel", background=BG, foreground=INK)
    s.configure("Surface.TLabel", background=SURFACE)
    s.configure("Muted.TLabel", foreground=MUTED, font=f.small)
    s.configure("SurfaceMuted.TLabel", background=SURFACE, foreground=MUTED, font=f.small)
    s.configure("Title.TLabel", font=f.title)
    s.configure("Brand.TLabel", font=f.brand, foreground=INK)
    s.configure("Heading.TLabel", font=f.heading)
    s.configure("SurfaceHeading.TLabel", background=SURFACE, font=f.heading)
    s.configure("KpiValue.TLabel", background=SURFACE, font=f.kpi, foreground=INK)
    s.configure("KpiAccent.TLabel", background=SURFACE, font=f.kpi, foreground=INDIGO)
    s.configure("KpiAmber.TLabel", background=SURFACE, font=f.kpi, foreground=AMBER)
    s.configure("KpiLabel.TLabel", background=SURFACE, font=f.kpi_label, foreground=MUTED)
    s.configure("Pill.TLabel", background=HOVER, foreground=MUTED, font=f.small, padding=(8, 2))
    s.configure("PillOn.TLabel", background="#E2F3EA", foreground=GREEN, font=f.small, padding=(8, 2))
    s.configure("PillErr.TLabel", background="#F8E4E2", foreground=RED, font=f.small, padding=(8, 2))
    s.configure("Status.TLabel", background=BG, foreground=MUTED, font=f.small, padding=(14, 4))

    # ボタン: 平らで、押せる場所だけ形がある
    s.configure("TButton", background=SURFACE, foreground=INK, bordercolor=LINE, lightcolor=SURFACE,
                darkcolor=SURFACE, relief="flat", padding=pad, focusthickness=0)
    s.map("TButton", background=[("pressed", LINE), ("active", HOVER), ("disabled", BG)],
          foreground=[("disabled", MUTED)], bordercolor=[("focus", INDIGO)])
    s.configure("Accent.TButton", background=INDIGO, foreground="white", bordercolor=INDIGO,
                lightcolor=INDIGO, darkcolor=INDIGO, font=f.strong)
    s.map("Accent.TButton", background=[("pressed", INDIGO_DARK), ("active", INDIGO_DARK), ("disabled", "#A9AEDF")],
          bordercolor=[("active", INDIGO_DARK)], foreground=[("disabled", "white")])
    s.configure("Ghost.TButton", background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG, foreground=MUTED, padding=(8, 6))
    s.map("Ghost.TButton", background=[("active", HOVER)], foreground=[("active", INK)])
    s.configure("Danger.TButton", background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG, foreground=RED, padding=(8, 6))
    s.map("Danger.TButton", background=[("active", "#F8E4E2")])
    s.configure("Chip.TButton", background=SURFACE, foreground=INK, bordercolor=LINE, padding=(10, 4), font=f.small)
    s.map("Chip.TButton", background=[("active", INDIGO_SOFT)], bordercolor=[("active", INDIGO)])

    # 入力欄
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=SURFACE, background=SURFACE, foreground=INK, bordercolor=LINE,
                    lightcolor=SURFACE, darkcolor=SURFACE, arrowcolor=MUTED, padding=(8, 5), insertcolor=INK)
        s.map(w, bordercolor=[("focus", INDIGO)], lightcolor=[("focus", INDIGO)],
              fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)],
              selectforeground=[("readonly", INK)])
    k = dpi_scale(root)
    s.configure("TCheckbutton", background=BG, foreground=INK, indicatorbackground=SURFACE,
                indicatorforeground=INDIGO, bordercolor=LINE, indicatorsize=int(14 * k),
                indicatormargin=(0, 0, int(6 * k), 0))
    s.map("TCheckbutton", indicatorbackground=[("selected", INDIGO)], indicatorforeground=[("selected", "white")],
          background=[("active", BG)])
    s.configure("Surface.TCheckbutton", background=SURFACE)
    s.map("Surface.TCheckbutton", background=[("active", SURFACE)])

    # 一覧
    row_h = int(34 * dpi_scale(root))
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=INK, bordercolor=LINE,
                lightcolor=SURFACE, darkcolor=SURFACE, rowheight=row_h, font=f.body, relief="flat")
    s.map("Treeview", background=[("selected", INDIGO_SOFT)], foreground=[("selected", INK)])
    s.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, font=f.small, relief="flat",
                bordercolor=LINE, lightcolor=SURFACE, darkcolor=SURFACE, padding=(8, 6))
    s.map("Treeview.Heading", background=[("active", SURFACE)])
    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # 外枠を消す

    # タブ: 下線だけで今のタブを示す
    s.configure("TNotebook", background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=BG, foreground=MUTED, bordercolor=BG, lightcolor=BG, darkcolor=BG,
                padding=(14, 7), font=f.body)
    s.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", HOVER)],
          foreground=[("selected", INK)], lightcolor=[("selected", INDIGO)], bordercolor=[("selected", LINE)])

    s.configure("TPanedwindow", background=BG)
    s.configure("Sash", sashthickness=8, gripcount=0, background=BG)
    s.configure("TSeparator", background=LINE)
    s.configure("Vertical.TScrollbar", background=BG, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
                lightcolor=BG, darkcolor=BG, gripcount=0)
    s.configure("TLabelframe", background=BG, bordercolor=LINE)
    s.configure("TLabelframe.Label", background=BG, foreground=INK, font=f.strong)
    return f


def text_widget_style(root: tk.Misc, fonts: Fonts, mono: bool = False) -> dict:
    """tk.Text を周りに馴染ませる設定。"""
    return dict(bg=SURFACE, fg=INK, relief="flat", highlightthickness=1, highlightbackground=LINE,
                highlightcolor=INDIGO, insertbackground=INK, selectbackground=INDIGO_SOFT,
                selectforeground=INK, padx=10, pady=8, font=fonts.mono if mono else fonts.body,
                borderwidth=0)


def matplotlib_style(fonts: Fonts) -> dict:
    return {
        "font.family": [fonts.ui, "Yu Gothic", "Meiryo", "Noto Sans CJK JP", "sans-serif"],
        "font.size": 9,
        "axes.edgecolor": LINE,
        "axes.labelcolor": MUTED,
        "axes.facecolor": SURFACE,
        "figure.facecolor": SURFACE,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": LINE,
        "grid.linewidth": 0.8,
        "legend.frameon": False,
        "axes.unicode_minus": False,
    }


IS_WINDOWS = sys.platform == "win32"
