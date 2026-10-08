"""imiwatch の画面（tkinter）。見た目の定義は style.py。"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib import rcParams  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.dates import AutoDateLocator, DateFormatter  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PIL import ImageTk  # noqa: E402

from . import capture, notify, planner, store, style  # noqa: E402
from .engine import MonitorRuntime, Result, Scheduler  # noqa: E402
from .judges import PROVIDERS, provider_label  # noqa: E402
from .plan import PlanError, describe_plan, validate_plan  # noqa: E402
from .region import select_region  # noqa: E402

EXAMPLES = [
    "在庫表示が「在庫あり」に変わったら教えて",
    "チャット欄に自分宛ての急ぎの依頼が来たら教えて",
    "このグラフが急に下がったら教えて",
    "未読や赤いバッジの多さを測りたい",
]
PROVIDER_KEYS = list(PROVIDERS.keys())
DEFAULT_CHOICE = "既定の設定に従う"


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def _provider_choices() -> list[str]:
    return [DEFAULT_CHOICE] + [provider_label(k) for k in PROVIDER_KEYS]


# =====================================================================
# 小さな部品


class ScrollFrame(ttk.Frame):
    """縦にスクロールできる枠。中身は .body に置く。"""

    def __init__(self, master, style_name: str = "TFrame", **kw):
        super().__init__(master, style=style_name, **kw)
        bg = style.SURFACE if style_name.startswith("Surface") else style.BG
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style=style_name)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")


class FlowFrame(ttk.Frame):
    """幅に合わせて折り返して並べる枠（例文のチップ用）。"""

    def __init__(self, master, gap: int = 6, **kw):
        super().__init__(master, **kw)
        self.gap = gap
        self.items: list[tk.Widget] = []
        self.bind("<Configure>", lambda e: self._reflow(e.width))

    def add(self, widget: tk.Widget):
        self.items.append(widget)
        self.after_idle(lambda: self._reflow(self.winfo_width()))

    def _reflow(self, width: int):
        if width <= 1:
            return
        x = y = row_h = 0
        for w in self.items:
            w.update_idletasks()
            ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
            if x and x + ww > width:
                x, y = 0, y + row_h + self.gap
                row_h = 0
            w.place(x=x, y=y)
            x += ww + self.gap
            row_h = max(row_h, wh)
        self.configure(height=y + row_h)


class Kpi(ttk.Frame):
    """数値帯の1マス。"""

    def __init__(self, master, label: str, value_style: str = "KpiValue.TLabel"):
        super().__init__(master, style="Surface.TFrame", padding=(16, 10))
        self.label = ttk.Label(self, text=label, style="KpiLabel.TLabel")
        self.label.pack(anchor="w")
        self.value = ttk.Label(self, text="—", style=value_style)
        self.value.pack(anchor="w")
        self.sub = ttk.Label(self, text="", style="SurfaceMuted.TLabel")
        self.sub.pack(anchor="w")

    def set(self, value: str, sub: str = "", label: str | None = None):
        self.value.configure(text=value)
        self.sub.configure(text=sub)
        if label is not None:
            self.label.configure(text=label)


# =====================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("imiwatch")
        self.fonts = style.apply(self)
        rcParams.update(style.matplotlib_style(self.fonts))
        self.k = style.dpi_scale(self)
        w = min(int(1240 * self.k), int(self.winfo_screenwidth() * 0.92))
        h = min(int(780 * self.k), int(self.winfo_screenheight() * 0.88))
        self.geometry(f"{w}x{h}")
        self.minsize(min(int(900 * self.k), w), min(int(560 * self.k), h))

        self.settings = store.load_settings()
        self.monitors: list[store.Monitor] = store.load_monitors()
        self.scheduler = Scheduler(lambda: self.settings)
        # 別スレッドから画面を触らないよう、画面の更新はこのキュー経由で本スレッドに戻す
        self.ui_calls: "queue.Queue" = queue.Queue()
        self._thumb = None
        self._last_error: dict[str, str] = {}

        self._build()
        self._refresh_list()
        for m in self.monitors:
            if m.enabled:
                self.scheduler.start(m)
        self._show_detail()
        self.after(300, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def px(self, n: float) -> int:
        return int(n * self.k)

    # ------------------------------------------------------------ 組み立て
    def _build(self):
        head = ttk.Frame(self, padding=(18, 14, 18, 10))
        head.pack(fill="x")
        ttk.Label(head, text="imiwatch", style="Brand.TLabel").pack(side="left")
        ttk.Label(head, text="画面の一部を、あなたの言葉で見張ります", style="Muted.TLabel").pack(side="left", padx=(12, 0), pady=(4, 0))
        ttk.Button(head, text="＋ 新しい見張り", style="Accent.TButton", command=self._new_monitor).pack(side="right")
        ttk.Button(head, text="設定", command=self._open_settings).pack(side="right", padx=(0, 8))

        # 下の状態表示は、窓が小さくても消えないよう先に下へ固定する
        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(side="bottom", fill="x", pady=(4, 2))

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=18)

        # 左: 見張りの一覧
        side = ttk.Frame(body)
        body.add(side, weight=0)
        ttk.Label(side, text="見張り", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.tree = ttk.Treeview(side, columns=("metric",), show="tree", selectmode="browse")
        self.tree.column("#0", width=self.px(200), stretch=True)
        self.tree.column("metric", width=self.px(64), anchor="e", stretch=False)
        self.tree.tag_configure("running", foreground=style.INK)
        self.tree.tag_configure("stopped", foreground=style.MUTED)
        self.tree.tag_configure("error", foreground=style.RED)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())
        self.side_empty = ttk.Label(side, text="まだ見張りがありません。\n右上の「＋ 新しい見張り」から\n作りましょう。",
                                    style="Muted.TLabel", justify="left")

        # 右: 詳細と空の状態
        self.right = ttk.Frame(body, padding=(16, 0, 0, 0))
        body.add(self.right, weight=1)
        self._build_empty(self.right)
        self._build_detail(self.right)

    def _build_empty(self, parent):
        f = self.empty = ttk.Frame(parent)
        inner = ttk.Frame(f)
        inner.place(relx=0.5, rely=0.42, anchor="center")
        ttk.Label(inner, text="何を見張りますか？", style="Title.TLabel").pack()
        ttk.Label(inner, text="画面の一部をドラッグで選び、知りたいことを一言で書くと、\n"
                              "その範囲を定期的に確かめて、条件を満たしたときに知らせます。",
                  style="Muted.TLabel", justify="center").pack(pady=(8, 16))
        ttk.Button(inner, text="＋ 新しい見張りを作る", style="Accent.TButton", command=self._new_monitor).pack()

    def _build_detail(self, parent):
        d = self.detail = ttk.Frame(parent)

        top = ttk.Frame(d)
        top.pack(fill="x")
        left = ttk.Frame(top)
        left.pack(side="left", fill="x", expand=True)
        row = ttk.Frame(left)
        row.pack(anchor="w")
        self.d_title = ttk.Label(row, text="", style="Title.TLabel")
        self.d_title.pack(side="left")
        self.d_pill = ttk.Label(row, text="", style="Pill.TLabel")
        self.d_pill.pack(side="left", padx=(10, 0), pady=(4, 0))
        self.d_request = ttk.Label(left, text="", style="Muted.TLabel", wraplength=self.px(560), justify="left")
        self.d_request.pack(anchor="w", pady=(2, 0))
        btns = ttk.Frame(top)
        btns.pack(side="right", anchor="n")
        self.run_btn = ttk.Button(btns, text="開始", style="Accent.TButton", command=self._toggle_run)
        self.run_btn.pack(side="right")
        ttk.Button(btns, text="今すぐ判定", command=self._run_now).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="削除", style="Danger.TButton", command=self._delete_selected).pack(side="right", padx=(0, 4))

        # 数値帯
        kp = ttk.Frame(d, style="Surface.TFrame")
        kp.pack(fill="x", pady=(14, 0))
        self.k_metric = Kpi(kp, "現在値", "KpiAccent.TLabel")
        self.k_last = Kpi(kp, "最終判定")
        self.k_rule = Kpi(kp, "通知の条件", "KpiAmber.TLabel")
        self.k_model = Kpi(kp, "判定モデル")
        for i, k in enumerate((self.k_metric, self.k_last, self.k_rule, self.k_model)):
            k.grid(row=0, column=i, sticky="nsew")
            kp.columnconfigure(i, weight=1, uniform="kpi")
        self.k_model.value.configure(font=(self.fonts.ui, 13, "bold"))

        # 操作の行
        ctl = ttk.Frame(d)
        ctl.pack(fill="x", pady=(12, 8))
        ttk.Label(ctl, text="判定モデル").pack(side="left")
        self.provider_box = ttk.Combobox(ctl, state="readonly", values=_provider_choices(), width=34)
        self.provider_box.pack(side="left", padx=(6, 18))
        self.provider_box.bind("<<ComboboxSelected>>", lambda e: self._change_provider())
        ttk.Label(ctl, text="取得間隔").pack(side="left")
        self.interval_var = tk.IntVar(value=300)
        sp = ttk.Spinbox(ctl, from_=10, to=86400, increment=10, textvariable=self.interval_var, width=7,
                         command=self._change_interval)
        sp.pack(side="left", padx=(6, 4))
        sp.bind("<FocusOut>", lambda e: self._change_interval())
        sp.bind("<Return>", lambda e: self._change_interval())
        ttk.Label(ctl, text="秒").pack(side="left", padx=(0, 18))
        self.notify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="条件を満たしたら通知する", variable=self.notify_var, command=self._toggle_notify).pack(side="left")

        nb = ttk.Notebook(d)
        nb.pack(fill="both", expand=True)

        # グラフ
        g = ttk.Frame(nb, style="Surface.TFrame", padding=(8, 8))
        nb.add(g, text="グラフ")
        side = ttk.Frame(g, style="Surface.TFrame", padding=(12, 4, 4, 4))
        side.pack(side="right", fill="y")
        self.fig = Figure(figsize=(6, 3.6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=g)
        self.canvas.get_tk_widget().configure(highlightthickness=0, bg=style.SURFACE)
        self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True)
        ttk.Label(side, text="最新の画像", style="SurfaceMuted.TLabel").pack(anchor="w")
        self.thumb_label = tk.Label(side, bg=style.BG, width=1, height=1)
        self.thumb_label.pack(anchor="w", pady=(4, 10))
        self.thumb_box = (self.px(240), self.px(150))
        ttk.Label(side, text="観点ごとの値", style="SurfaceMuted.TLabel").pack(anchor="w")
        self.show_parts = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="グラフに線を表示", variable=self.show_parts, style="Surface.TCheckbutton",
                        command=self._draw_chart).pack(anchor="w", pady=(2, 0))
        self.latest_text = tk.Text(side, width=30, height=6, wrap="word", **style.text_widget_style(self, self.fonts))
        self.latest_text.configure(highlightthickness=0, padx=0, font=self.fonts.small)
        self.latest_text.pack(fill="both", expand=True, pady=(4, 0))

        # 設計図
        p = ttk.Frame(nb, style="Surface.TFrame")
        nb.add(p, text="判定の設計図")
        self.plan_text = tk.Text(p, wrap="word", **style.text_widget_style(self, self.fonts))
        self.plan_text.configure(highlightthickness=0, padx=16, pady=12)
        self.plan_text.pack(fill="both", expand=True)

        # ログ
        lg = ttk.Frame(nb, style="Surface.TFrame")
        nb.add(lg, text="ログ")
        self.log_text = tk.Text(lg, wrap="none", **style.text_widget_style(self, self.fonts, mono=True))
        self.log_text.configure(highlightthickness=0, padx=16, pady=12)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("notified", foreground=style.AMBER)
        self.log_text.tag_configure("error", foreground=style.RED)

        d.bind("<Configure>", lambda e: self.d_request.configure(wraplength=max(200, e.width - self.px(320))))

    # ------------------------------------------------------------ 一覧と詳細
    def _selected(self) -> store.Monitor | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return next((m for m in self.monitors if m.id == sel[0]), None)

    def _last_metric(self, m: store.Monitor):
        rt = self.scheduler.runtimes.get(m.id)
        if rt and rt.last_result:
            return rt.last_result.metric, rt.last_result.timestamp
        rows = store.read_log(m.id, limit=1)
        return (rows[-1]["metric"], rows[-1]["timestamp"]) if rows else (None, "")

    def _row(self, m: store.Monitor):
        metric, _ = self._last_metric(m)
        running = self.scheduler.is_running(m.id)
        tag = "error" if m.id in self._last_error and running else ("running" if running else "stopped")
        dot = "●" if running else "○"
        return f"  {dot}  {m.name}", (_fmt(metric),), (tag,)

    def _refresh_list(self, keep: str | None = None):
        ids = [m.id for m in self.monitors]
        if keep is None and list(self.tree.get_children()) == ids:
            for m in self.monitors:
                text, values, tags = self._row(m)
                self.tree.item(m.id, text=text, values=values, tags=tags)
        else:
            keep = keep or (self.tree.selection()[0] if self.tree.selection() else None)
            self.tree.delete(*self.tree.get_children())
            for m in self.monitors:
                text, values, tags = self._row(m)
                self.tree.insert("", "end", iid=m.id, text=text, values=values, tags=tags)
            if keep and self.tree.exists(keep):
                self.tree.selection_set(keep)
            elif self.monitors:
                self.tree.selection_set(self.monitors[0].id)
        if self.monitors:
            self.side_empty.pack_forget()
            if not self.tree.winfo_ismapped():
                self.tree.pack(fill="both", expand=True)
        else:
            self.tree.pack_forget()
            self.side_empty.pack(anchor="w", pady=8)

    def _show_detail(self):
        m = self._selected()
        if not m:
            self.detail.pack_forget()
            self.empty.pack(fill="both", expand=True)
            return
        self.empty.pack_forget()
        self.detail.pack(fill="both", expand=True)
        self.d_title.configure(text=m.name)
        self.d_request.configure(text=f"「{m.request}」")
        self.provider_box.set(provider_label(m.provider) if m.provider else DEFAULT_CHOICE)
        self.interval_var.set(m.interval_seconds)
        self.notify_var.set(m.plan["notify"].get("enabled", True))
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("end", f"あなたの依頼\n{m.request}\n\n{describe_plan(m.plan)}\n\n"
                                     f"範囲: {m.region}\n\n— JSON —\n{json.dumps(m.plan, ensure_ascii=False, indent=2)}")
        self._update_header(m)
        self._draw_chart()
        self._show_latest()
        self._show_log()

    def _update_header(self, m: store.Monitor, r: Result | None = None):
        running = self.scheduler.is_running(m.id)
        err = self._last_error.get(m.id)
        if running and err:
            self.d_pill.configure(text="エラー", style="PillErr.TLabel")
        elif running:
            self.d_pill.configure(text=f"動作中・{m.interval_seconds} 秒ごと", style="PillOn.TLabel")
        else:
            self.d_pill.configure(text="停止中", style="Pill.TLabel")
        self.run_btn.configure(text="停止" if running else "開始", style="TButton" if running else "Accent.TButton")

        metric, ts = self._last_metric(m)
        self.k_metric.set(_fmt(metric), label=m.plan["metric"]["name"], sub="0〜1（高いほど条件に近い）")
        self.k_last.set(ts[11:16] if ts else "—", sub=ts[:10] if ts else "まだ判定していません")
        n = m.plan["notify"]
        if n.get("enabled", True):
            self.k_rule.set(f"{n['op']} {n['threshold']:.2f}", sub=f"{n['consecutive']} 回続いたら")
        else:
            self.k_rule.set("オフ", sub="記録のみ")
        used = m.provider or self.settings.get("default_provider", "")
        lat = ""
        if r is not None and r.latency_ms:
            lat = f"前回 {r.latency_ms} ms"
        elif err:
            lat = "接続・設定を確認"
        self.k_model.set(used, sub=lat)

    def _draw_chart(self):
        m = self._selected()
        ax = self.ax
        ax.clear()
        if not m:
            self.canvas.draw_idle()
            return
        rows = [r for r in store.read_log(m.id, limit=1000) if r["status"] in ("ok", "reused") and r["metric"] is not None]
        n = m.plan["notify"]
        if not rows:
            ax.text(0.5, 0.5, "最初の判定を待っています\n「今すぐ判定」で、すぐに1点目を記録できます",
                    ha="center", va="center", transform=ax.transAxes, color=style.MUTED, fontsize=10, linespacing=1.8)
            ax.set_xticks([])
        else:
            xs = [datetime.fromisoformat(r["timestamp"]) for r in rows]
            if self.show_parts.get():
                for i, qid in enumerate(m.plan["metric"]["weights"]):
                    pts = [(x, r["values"].get(qid)) for x, r in zip(xs, rows) if r["values"].get(qid) is not None]
                    if pts:
                        ax.plot([p[0] for p in pts], [p[1] for p in pts], lw=1, color=style.SERIES[i % len(style.SERIES)],
                                alpha=0.9, label=qid)
            ax.plot(xs, [r["metric"] for r in rows], lw=2.4, color=style.INDIGO, label=m.plan["metric"]["name"],
                    solid_capstyle="round", zorder=4)
            hits = [(x, r["metric"]) for x, r in zip(xs, rows) if r.get("notified") == "1"]
            if hits:
                ax.scatter([h[0] for h in hits], [h[1] for h in hits], s=70, color=style.AMBER, edgecolor="white",
                           linewidth=1.5, zorder=5, label="通知した点")
            loc = AutoDateLocator(minticks=3, maxticks=7)
            ax.xaxis.set_major_locator(loc)
            span = (xs[-1] - xs[0]).total_seconds()
            fmt = "%H:%M:%S" if span < 600 else ("%H:%M" if xs[-1].date() == xs[0].date() else "%m/%d %H:%M")
            ax.xaxis.set_major_formatter(DateFormatter(fmt))
            ax.legend(loc="upper left", fontsize=8, ncol=3, handlelength=1.6)
        if n.get("enabled", True):
            ax.axhline(n["threshold"], ls=(0, (4, 3)), lw=1.2, color=style.AMBER, zorder=3)
            ax.text(1.0, n["threshold"], f" 通知 {n['threshold']:.2f}", transform=ax.get_yaxis_transform(),
                    va="center", ha="left", color=style.AMBER, fontsize=8, clip_on=False)
        ax.set_ylim(-0.03, 1.05)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.grid(axis="y")
        ax.tick_params(length=0)
        self.fig.subplots_adjust(left=0.07, right=0.9, top=0.95, bottom=0.12)
        self.canvas.draw_idle()

    def _show_latest(self, result: Result | None = None):
        m = self._selected()
        if not m:
            return
        img = result.image if result is not None and result.image is not None else None
        if img is None:
            p = store.monitor_dir(m.id) / "latest.jpg"
            if p.exists():
                from PIL import Image
                try:
                    img = Image.open(p)
                except OSError:
                    img = None
        if img is not None:
            t = img.copy()
            t.thumbnail(self.thumb_box)
            self._thumb = ImageTk.PhotoImage(t)
            self.thumb_label.configure(image=self._thumb, width=t.width, height=t.height, bg=style.SURFACE)
        else:
            self._thumb = None
            self.thumb_label.configure(image="", text="まだ撮影していません", fg=style.MUTED, bg=style.SURFACE,
                                       width=24, height=4, font=self.fonts.small)

        t = self.latest_text
        t.delete("1.0", "end")
        rt = self.scheduler.runtimes.get(m.id)
        r = result or (rt.last_result if rt else None)
        if r is None:
            rows = store.read_log(m.id, limit=1)
            if rows:
                for k, v in rows[-1]["values"].items():
                    t.insert("end", f"{k}   {v:.2f}\n")
            return
        if r.status == "error":
            t.insert("end", f"エラー\n{r.error}")
            return
        for qid, q in m.plan["questions"].items():
            a = r.answers.get(qid, {})
            if a.get("abstained"):
                txt = "判断保留"
            elif q["type"] == "noul":
                txt = f"{a.get('noul', 0):.2f}"
            elif q["type"] == "score":
                txt = f"{a.get('score', 0):.2f} / {len(q['criteria']) - 1}"
            else:
                txt = f"{a.get('choice', '-')}"
            t.insert("end", f"{qid}   {txt}\n")
        if r.status == "reused":
            t.insert("end", "\n画面に変化がないため、前回の値を使っています")

    def _show_log(self):
        m = self._selected()
        t = self.log_text
        t.delete("1.0", "end")
        if not m:
            return
        rows = store.read_log(m.id, limit=300)
        if not rows:
            t.insert("end", "まだ記録がありません。")
            return
        t.insert("end", "時刻                 状態     値     モデル        所要\n")
        for r in reversed(rows):
            line = f"{r['timestamp']}  {r['status']:<7}{_fmt(r['metric']):>5}   {r['provider']:<12}{r['latency_ms']:>6}ms"
            tag = ""
            if r.get("notified") == "1":
                line += "   通知"
                tag = "notified"
            if r.get("error"):
                line += f"   {r['error'][:140]}"
                tag = tag or ("error" if r["status"] == "error" else "")
            t.insert("end", line + "\n", tag)

    # ------------------------------------------------------------ 操作
    def _save(self):
        store.save_monitors(self.monitors)

    def _toggle_run(self):
        m = self._selected()
        if not m:
            return
        if self.scheduler.is_running(m.id):
            m.enabled = False
            self.scheduler.stop(m.id)
            self._status(f"「{m.name}」を止めました")
        else:
            m.enabled = True
            self._last_error.pop(m.id, None)
            self.scheduler.start(m)
            self._status(f"「{m.name}」を開始しました（{m.interval_seconds} 秒ごと）")
        self._save()
        self._refresh_list()
        self._update_header(m)

    def _run_now(self):
        m = self._selected()
        if m:
            self.scheduler.run_now(m)
            self._status(f"「{m.name}」を判定しています…")

    def _delete_selected(self):
        m = self._selected()
        if not m or not messagebox.askyesno("見張りを削除", f"「{m.name}」と、その記録をすべて削除します。よろしいですか？", parent=self):
            return
        self.scheduler.stop(m.id)
        self.monitors = [x for x in self.monitors if x.id != m.id]
        self._save()
        store.delete_monitor_data(m.id)
        self._refresh_list(keep="")
        self._show_detail()
        self._status(f"「{m.name}」を削除しました")

    def _change_provider(self):
        m = self._selected()
        if not m:
            return
        idx = self.provider_box.current()
        m.provider = "" if idx <= 0 else PROVIDER_KEYS[idx - 1]
        self._save()
        self._last_error.pop(m.id, None)
        self._update_header(m)
        used = m.provider or self.settings.get("default_provider")
        self._status(f"判定モデルを {provider_label(used)} にしました（次の判定から）")

    def _change_interval(self):
        m = self._selected()
        if not m:
            return
        try:
            v = max(10, int(self.interval_var.get()))
        except (tk.TclError, ValueError):
            return
        if v != m.interval_seconds:
            m.interval_seconds = v
            self._save()
            self._update_header(m)
            self._status(f"取得間隔を {v} 秒にしました（次の待機から）")

    def _toggle_notify(self):
        m = self._selected()
        if m:
            m.plan["notify"]["enabled"] = bool(self.notify_var.get())
            self._save()
            self._update_header(m)
            self._draw_chart()

    def _status(self, text: str):
        self.status_var.set(text)

    # ------------------------------------------------------------ 結果の受け取り
    def call_soon(self, fn):
        """別スレッドから安全に、画面スレッドで fn を実行してもらう。"""
        self.ui_calls.put(fn)

    def _poll(self):
        while True:
            try:
                fn = self.ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except tk.TclError:
                pass  # ダイアログが閉じられた後など
        changed = False
        try:
            while True:
                r: Result = self.scheduler.results.get_nowait()
                changed = True
                m = next((x for x in self.monitors if x.id == r.monitor_id), None)
                if m is None:
                    continue
                if r.status == "error":
                    self._last_error[m.id] = r.error
                    self._status(f"「{m.name}」: {r.error[:160]}")
                else:
                    self._last_error.pop(m.id, None)
                    if r.status == "reused":
                        self._status(f"「{m.name}」画面に変化がないため、判定を省きました（{r.timestamp[11:19]}）")
                    else:
                        self._status(f"「{m.name}」{m.plan['metric']['name']} {_fmt(r.metric)}（{r.latency_ms} ms）")
                if r.notified:
                    self._notify(m, r)
                sel = self._selected()
                if sel and sel.id == m.id:
                    self._update_header(m, r)
                    self._draw_chart()
                    self._show_latest(r)
                    self._show_log()
        except queue.Empty:
            pass
        if changed:
            self._refresh_list()
        self.after(200, self._poll)

    def _notify(self, m: store.Monitor, r: Result):
        msg = f"{m.plan['notify'].get('message', '')}（{m.plan['metric']['name']} {_fmt(r.metric)}）"
        img = str(store.monitor_dir(m.id) / "latest.jpg")
        if not notify.send_toast(f"imiwatch: {m.name}", msg, img):
            ToastWindow(self, m.name, msg)

    # ------------------------------------------------------------ ダイアログ
    def _new_monitor(self):
        NewMonitorDialog(self)

    def _open_settings(self):
        SettingsDialog(self)

    def add_monitor(self, m: store.Monitor, start: bool):
        self.monitors.append(m)
        m.enabled = start
        self._save()
        if start:
            self.scheduler.start(m)
        self._refresh_list(keep=m.id)
        self._show_detail()
        self._status(f"「{m.name}」を{'開始しました' if start else '保存しました'}")

    def _on_close(self):
        self.scheduler.stop_all()
        self.destroy()


# =====================================================================
class ToastWindow(tk.Toplevel):
    """Windows の通知が使えないときの、画面右下の簡易通知。"""

    def __init__(self, app: App, title: str, message: str):
        super().__init__(app)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=style.AMBER)
        inner = tk.Frame(self, bg=style.SURFACE)
        inner.pack(fill="both", expand=True, padx=(4, 0))  # 左端の琥珀の帯
        tk.Label(inner, text=title, fg=style.INK, bg=style.SURFACE, font=app.fonts.strong).pack(anchor="w", padx=14, pady=(12, 0))
        tk.Label(inner, text=message, fg=style.MUTED, bg=style.SURFACE, wraplength=app.px(300), justify="left",
                 font=app.fonts.body).pack(anchor="w", padx=14, pady=(2, 12))
        self.update_idletasks()
        w, h = app.px(340), self.winfo_reqheight()
        self.geometry(f"{w}x{h}+{self.winfo_screenwidth() - w - app.px(20)}+{self.winfo_screenheight() - h - app.px(64)}")
        self.bind("<Button-1>", lambda e: self.destroy())
        self.after(8000, self.destroy)


# =====================================================================
class NewMonitorDialog(tk.Toplevel):
    """範囲 → 依頼 → 設計 → 試して保存、の順に作る。"""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("新しい見張り")
        self.configure(bg=style.BG)
        self.transient(app)
        w = min(app.px(920), int(self.winfo_screenwidth() * 0.9))
        h = min(app.px(820), int(self.winfo_screenheight() * 0.88))
        self.geometry(f"{w}x{h}")
        self.minsize(min(app.px(640), w), min(app.px(480), h))
        self.region: dict | None = None
        self.plan: dict | None = None

        # 下のボタンは常に見えるよう、スクロールの外に置く
        foot = ttk.Frame(self, padding=(20, 10, 20, 14))
        foot.pack(side="bottom", fill="x")
        ttk.Separator(self).pack(side="bottom", fill="x")
        ttk.Button(foot, text="キャンセル", style="Ghost.TButton", command=self.destroy).pack(side="left")
        ttk.Button(foot, text="保存して開始", style="Accent.TButton", command=lambda: self._save(True)).pack(side="right")
        ttk.Button(foot, text="保存のみ", command=lambda: self._save(False)).pack(side="right", padx=(0, 8))

        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        b = ttk.Frame(sf.body, padding=(20, 16, 20, 8))
        b.pack(fill="both", expand=True)

        # 1. 範囲
        self._step(b, "1", "見張る範囲", "画面の上で、見張りたい部分をドラッグします。")
        r1 = ttk.Frame(b)
        r1.pack(fill="x", pady=(0, 18))
        ttk.Button(r1, text="範囲を選ぶ", command=self._pick_region).pack(side="left")
        self.region_label = ttk.Label(r1, text="まだ選んでいません", style="Muted.TLabel")
        self.region_label.pack(side="left", padx=12)
        self.preview_label = tk.Label(r1, bg=style.BG)
        self.preview_label.pack(side="right")

        # 2. 依頼
        self._step(b, "2", "知りたいこと", "何が起きたら知らせてほしいか、何を測りたいかを、普段の言葉で書きます。")
        self.request = tk.Text(b, height=3, wrap="word", **style.text_widget_style(self, app.fonts))
        self.request.pack(fill="x")
        chips = FlowFrame(b, style="TFrame")
        chips.pack(fill="x", pady=(8, 18))
        for e in EXAMPLES:
            chips.add(ttk.Button(chips, text=e, style="Chip.TButton", command=lambda t=e: self._set_request(t)))

        # 3. 設計
        self._step(b, "3", "判定の設計", "AI が、知りたいことを判定しやすい観点に分けます。右の JSON は直接直せます。")
        r3 = ttk.Frame(b)
        r3.pack(fill="x")
        self.plan_btn = ttk.Button(r3, text="判定を設計する", style="Accent.TButton", command=self._make_plan)
        self.plan_btn.pack(side="left")
        ttk.Button(r3, text="JSON の変更を反映", command=self._apply_json).pack(side="left", padx=8)
        self.plan_note = ttk.Label(r3, text="", style="Muted.TLabel", wraplength=app.px(420), justify="left")
        self.plan_note.pack(side="left", padx=6)
        panes = ttk.Frame(b)
        panes.pack(fill="both", expand=True, pady=(8, 18))
        panes.columnconfigure(0, weight=1, uniform="p")
        panes.columnconfigure(1, weight=1, uniform="p")
        self.plan_view = tk.Text(panes, wrap="word", height=14, **style.text_widget_style(self, app.fonts))
        self.plan_json = tk.Text(panes, wrap="none", height=14, **style.text_widget_style(self, app.fonts, mono=True))
        self.plan_view.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.plan_json.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.plan_view.insert("end", "ここに、観点・重み・通知の条件が表示されます。")

        # 4. 試して保存
        self._step(b, "4", "試して保存", "今の画面で1回判定して、値の感じを確かめてから保存します。")
        grid = ttk.Frame(b)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="判定モデル").grid(row=0, column=0, sticky="w", pady=4)
        self.provider_box = ttk.Combobox(grid, state="readonly", values=_provider_choices(), width=40)
        self.provider_box.current(0)
        self.provider_box.grid(row=0, column=1, sticky="w", padx=10, pady=4)
        ttk.Button(grid, text="今の画面で試す", command=self._trial).grid(row=0, column=2, sticky="e", pady=4)
        ttk.Label(grid, text="名前").grid(row=1, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.name_var, width=32).grid(row=1, column=1, sticky="w", padx=10, pady=4)
        ttk.Label(grid, text="取得間隔").grid(row=2, column=0, sticky="w", pady=4)
        iv = ttk.Frame(grid)
        iv.grid(row=2, column=1, sticky="w", padx=10, pady=4)
        self.interval_var = tk.IntVar(value=300)
        ttk.Spinbox(iv, from_=10, to=86400, increment=10, textvariable=self.interval_var, width=7).pack(side="left")
        ttk.Label(iv, text="秒").pack(side="left", padx=4)
        self.trial_label = ttk.Label(b, text="", style="Heading.TLabel", wraplength=app.px(820), justify="left")
        self.trial_label.pack(anchor="w", pady=(12, 4))
        self.trial_detail = ttk.Label(b, text="", style="Muted.TLabel", wraplength=app.px(820), justify="left")
        self.trial_detail.pack(anchor="w")

    def _step(self, parent, num: str, title: str, hint: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        badge = tk.Label(row, text=num, bg=style.INDIGO, fg="white", font=self.app.fonts.strong, width=2)
        badge.pack(side="left")
        ttk.Label(row, text=title, style="Heading.TLabel").pack(side="left", padx=(10, 0))
        ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=self.app.px(820), justify="left").pack(anchor="w", pady=(0, 8))

    # ---------------------------------------------------------------
    def _set_request(self, text: str):
        self.request.delete("1.0", "end")
        self.request.insert("end", text)

    def _request_text(self) -> str:
        return self.request.get("1.0", "end").strip()

    def _pick_region(self):
        self.withdraw()
        self.app.iconify()

        def done(region):
            self.app.deiconify()
            self.deiconify()
            self.lift()
            if not region:
                return
            self.region = region
            self.region_label.configure(text=f"{region['width']} × {region['height']} px を選びました", style="TLabel")
            try:
                img = capture.grab(region)
                t = img.copy()
                t.thumbnail((self.app.px(220), self.app.px(110)))
                self._ph = ImageTk.PhotoImage(t)
                self.preview_label.configure(image=self._ph)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror("撮影できませんでした", str(e), parent=self)

        self.after(350, lambda: select_region(self.app, done))

    def _current_image_url(self) -> str | None:
        if not self.region:
            return None
        try:
            img = capture.grab(self.region)
        except Exception:  # noqa: BLE001
            return None
        s = self.app.settings
        return capture.to_data_url(capture.shrink(img, int(s.get("max_image_side", 768))), int(s.get("jpeg_quality", 85)))

    def _provider(self) -> str:
        idx = self.provider_box.current()
        return "" if idx <= 0 else PROVIDER_KEYS[idx - 1]

    def _make_plan(self):
        req = self._request_text()
        if not req:
            messagebox.showinfo("知りたいことを書いてください", "2 の欄に、知りたいことを書いてから設計します。", parent=self)
            return
        self.plan_btn.configure(state="disabled", text="設計しています…")
        self.plan_note.configure(text="数秒〜数十秒かかります")
        img = self._current_image_url()
        settings = dict(self.app.settings)
        language = planner.question_language(self._provider() or settings.get("default_provider", ""))

        def work():
            plan, note = planner.make_plan(settings, req, img, language)
            self.app.call_soon(lambda: self._set_plan(plan, note))

        threading.Thread(target=work, daemon=True).start()

    def _set_plan(self, plan: dict, note: str):
        self.plan_btn.configure(state="normal", text="判定を設計し直す")
        self.plan = plan
        self.plan_note.configure(text=note)
        self.plan_view.delete("1.0", "end")
        self.plan_view.insert("end", describe_plan(plan))
        self.plan_json.delete("1.0", "end")
        self.plan_json.insert("end", planner.plan_to_json(plan))
        if not self.name_var.get():
            self.name_var.set(plan.get("title", ""))
        self.interval_var.set(plan.get("suggested_interval_seconds", 300))

    def _apply_json(self) -> bool:
        try:
            plan = validate_plan(json.loads(self.plan_json.get("1.0", "end")))
        except (json.JSONDecodeError, PlanError, TypeError, ValueError) as e:
            messagebox.showerror("JSON を確認してください", str(e), parent=self)
            return False
        self.plan = plan
        self.plan_view.delete("1.0", "end")
        self.plan_view.insert("end", describe_plan(plan))
        return True

    def _draft(self) -> store.Monitor | None:
        if not self.region:
            messagebox.showinfo("範囲を選んでください", "1 で、見張る範囲を選んでください。", parent=self)
            return None
        if self.plan is None:
            messagebox.showinfo("判定を設計してください", "3 の「判定を設計する」を押してください。", parent=self)
            return None
        if not self._apply_json():
            return None
        try:
            interval = max(10, int(self.interval_var.get()))
        except (tk.TclError, ValueError):
            interval = 300
        return store.Monitor(
            name=self.name_var.get().strip() or self.plan["title"],
            request=self._request_text(),
            region=self.region,
            plan=self.plan,
            interval_seconds=interval,
            provider=self._provider(),
        )

    def _trial(self):
        m = self._draft()
        if not m:
            return
        self.trial_label.configure(text="判定しています…", foreground=style.MUTED)
        self.trial_detail.configure(text="")
        rt = MonitorRuntime(m)
        settings = dict(self.app.settings)

        def work():
            r = rt.run_once(settings, force=True, record=False)
            self.app.call_soon(lambda: self._show_trial(m, r))

        threading.Thread(target=work, daemon=True).start()

    def _show_trial(self, m: store.Monitor, r: Result):
        if r.status == "error":
            self.trial_label.configure(text="判定できませんでした", foreground=style.RED)
            self.trial_detail.configure(text=r.error)
            return
        n = m.plan["notify"]
        hit = (r.metric is not None) and (r.metric >= n["threshold"] if n["op"] == ">=" else r.metric <= n["threshold"])
        self.trial_label.configure(
            foreground=style.INDIGO,
            text=f"今の画面: {m.plan['metric']['name']} {_fmt(r.metric)}　—　通知の条件は{'満たしています' if hit else '満たしていません'}")
        parts = "、".join(f"{k} {v:.2f}" for k, v in r.values.items())
        extra = f"　{r.error}" if r.error else ""
        self.trial_detail.configure(text=f"内訳: {parts}　（{r.latency_ms} ms・{r.provider}）{extra}")

    def _save(self, start: bool):
        m = self._draft()
        if not m:
            return
        self.app.add_monitor(m, start)
        self.destroy()


# =====================================================================
class SettingsDialog(tk.Toplevel):
    TABS = [
        ("Cloudflare", "Clef / Clef-flash を Workers AI で使います。", [
            ("アカウントID", "cloudflare_account_id", False),
            ("APIトークン", "cloudflare_api_token", True),
        ]),
        ("Perplexity", "Perplexity の Decisions API を使います。", [
            ("APIキー", "perplexity_api_key", True),
            ("モデル", "perplexity_model", False),
        ]),
        ("jev-local", "自前の GPU サーバーで動かす imajev-4b。SSH トンネル越しなら URL はそのままで使えます。", [
            ("サーバーURL", "jevlocal_base_url", False),
            ("APIキー（KEV_API_KEY を設定した場合）", "jevlocal_api_key", True),
        ]),
        ("SystemOne", "SystemOne 形式を話す任意のサーバー（TypeSafe Jev など）。", [
            ("エンドポイントURL", "systemone_base_url", False),
            ("APIキー", "systemone_api_key", True),
            ("モデル", "systemone_model", False),
        ]),
        ("OpenRouter", "判定の設計役の LLM と、LLM による代用判定に使います。", [
            ("APIキー", "openrouter_api_key", True),
            ("設計に使うモデル", "planner_model", False),
            ("代用判定に使うモデル", "llm_judge_model", False),
        ]),
        ("画像", "送る画像の大きさと、判定を省く基準です。", [
            ("画像の長辺（px）", "max_image_side", False),
            ("差分のしきい値（0〜255）", "diff_threshold", False),
        ]),
    ]

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("設定")
        self.configure(bg=style.BG)
        self.transient(app)
        s = app.settings
        self.vars: dict[str, tk.StringVar] = {}

        foot = ttk.Frame(self, padding=(20, 10, 20, 14))
        foot.pack(side="bottom", fill="x")
        ttk.Label(foot, text=f"保存先: {store.settings_path()}（API キーは暗号化されません）", style="Muted.TLabel",
                  wraplength=app.px(420), justify="left").pack(side="left")
        ttk.Button(foot, text="保存", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(foot, text="キャンセル", style="Ghost.TButton", command=self.destroy).pack(side="right", padx=(0, 8))
        ttk.Separator(self).pack(side="bottom", fill="x")

        top = ttk.Frame(self, padding=(20, 16, 20, 8))
        top.pack(fill="x")
        ttk.Label(top, text="既定の判定モデル", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(top, text="見張りごとに変えることもできます。", style="Muted.TLabel").pack(anchor="w", pady=(2, 6))
        self.default_box = ttk.Combobox(top, state="readonly", width=48, values=[provider_label(k) for k in PROVIDER_KEYS])
        cur = s.get("default_provider", "clef-flash")
        self.default_box.current(PROVIDER_KEYS.index(cur) if cur in PROVIDER_KEYS else 0)
        self.default_box.pack(anchor="w")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=20, pady=(10, 4))
        for tab, hint, fields in self.TABS:
            f = ttk.Frame(nb, style="Surface.TFrame", padding=(16, 14))
            nb.add(f, text=tab)
            ttk.Label(f, text=hint, style="SurfaceMuted.TLabel", wraplength=app.px(520), justify="left").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
            for i, (label, key, secret) in enumerate(fields, start=1):
                ttk.Label(f, text=label, style="Surface.TLabel").grid(row=i, column=0, sticky="w", pady=5, padx=(0, 12))
                v = tk.StringVar(value=str(s.get(key, "")))
                ttk.Entry(f, textvariable=v, width=44, show="•" if secret else "").grid(row=i, column=1, sticky="ew", pady=5)
                self.vars[key] = v
            if tab == "SystemOne":
                r = len(fields) + 1
                ttk.Label(f, text="画像の送り方", style="Surface.TLabel").grid(row=r, column=0, sticky="w", pady=5)
                self.image_mode = ttk.Combobox(f, state="readonly", width=16, values=["none", "clef", "perplexity"])
                self.image_mode.set(s.get("systemone_image_mode", "none"))
                self.image_mode.grid(row=r, column=1, sticky="w", pady=5)
            f.columnconfigure(1, weight=1)

    def _save(self):
        s = dict(self.app.settings)
        s["default_provider"] = PROVIDER_KEYS[self.default_box.current()]
        for key, v in self.vars.items():
            val = v.get().strip()
            if key == "max_image_side":
                try:
                    val = max(128, int(val))
                except ValueError:
                    val = 768
            elif key == "diff_threshold":
                try:
                    val = max(0.0, float(val))
                except ValueError:
                    val = 2.0
            s[key] = val
        s["systemone_image_mode"] = self.image_mode.get()
        store.save_settings(s)
        self.app.settings.clear()
        self.app.settings.update(store.load_settings())
        m = self.app._selected()
        if m:
            self.app._update_header(m)
        self.app._status("設定を保存しました")
        self.destroy()


def main():
    _enable_dpi_awareness()
    App().mainloop()


def _enable_dpi_awareness():
    """Windows の拡大表示でも、画面座標と撮影座標（物理ピクセル）をそろえる。"""
    import sys

    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
