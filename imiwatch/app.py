"""imiwatch の画面（tkinter）。"""
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
from matplotlib.dates import DateFormatter  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PIL import ImageTk  # noqa: E402

from . import capture, notify, planner, store  # noqa: E402
from .engine import MonitorRuntime, Result, Scheduler  # noqa: E402
from .judges import PROVIDERS, provider_label  # noqa: E402
from .plan import PlanError, describe_plan, validate_plan  # noqa: E402
from .region import select_region  # noqa: E402

rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "IPAexGothic", "sans-serif"]
rcParams["axes.unicode_minus"] = False

UI_FONT = ("Yu Gothic UI", 10)
EXAMPLES = [
    "この在庫表示が「在庫あり」に変わったら教えて",
    "チャット欄に、自分宛てで急ぎの依頼が来たら教えて",
    "このグラフが急に下がったら教えて",
    "このページの散らかり具合（未読・赤いバッジの多さ）を測りたい",
]
PROVIDER_KEYS = list(PROVIDERS.keys())


def _fmt_metric(v) -> str:
    return "—" if v is None else f"{v:.2f}"


# =====================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("imiwatch — 意味で見張る画面ウォッチャー")
        self.geometry("1180x720")
        self.minsize(960, 600)
        self.option_add("*Font", UI_FONT)

        self.settings = store.load_settings()
        self.monitors: list[store.Monitor] = store.load_monitors()
        self.scheduler = Scheduler(lambda: self.settings)
        # 別スレッドから画面を触らないよう、画面の更新はこのキュー経由で本スレッドに戻す
        self.ui_calls: "queue.Queue" = queue.Queue()
        self._thumb = None  # 画像の参照を保持（GC 対策）

        self._build()
        self._refresh_list()
        # 前回動いていた見張りを再開
        for m in self.monitors:
            if m.enabled:
                self.scheduler.start(m)
        self.after(300, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self.monitors:
            self.after(400, lambda: self._status("右上の「＋ 新しい見張り」から始めましょう。"
                                                 "APIキーは「設定」で入力します。"))

    # ------------------------------------------------------------ 画面の組み立て
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="imiwatch", font=("Yu Gothic UI", 15, "bold")).pack(side="left")
        ttk.Label(top, text="  画面の一部を、あなたの言葉で見張ります", foreground="#666").pack(side="left")
        ttk.Button(top, text="設定", command=self._open_settings).pack(side="right")
        ttk.Button(top, text="＋ 新しい見張り", command=self._new_monitor).pack(side="right", padx=6)

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # 左: 見張りの一覧
        left = ttk.Frame(body)
        body.add(left, weight=1)
        cols = ("name", "state", "metric", "last")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse", height=18)
        for c, label, w in [("name", "見張り", 150), ("state", "状態", 60), ("metric", "最新値", 60), ("last", "最終判定", 80)]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_detail())
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="開始", command=self._start_selected).pack(side="left")
        ttk.Button(btns, text="停止", command=self._stop_selected).pack(side="left", padx=4)
        ttk.Button(btns, text="今すぐ判定", command=self._run_now).pack(side="left")
        ttk.Button(btns, text="削除", command=self._delete_selected).pack(side="right")

        # 右: 詳細
        right = ttk.Frame(body)
        body.add(right, weight=3)
        head = ttk.Frame(right)
        head.pack(fill="x")
        self.detail_title = ttk.Label(head, text="（見張りを選んでください）", font=("Yu Gothic UI", 13, "bold"))
        self.detail_title.pack(side="left")

        ctl = ttk.Frame(right)
        ctl.pack(fill="x", pady=(4, 6))
        ttk.Label(ctl, text="判定モデル").pack(side="left")
        self.provider_var = tk.StringVar()
        self.provider_box = ttk.Combobox(
            ctl, textvariable=self.provider_var, state="readonly", width=42,
            values=["（既定の設定に従う）"] + [provider_label(k) for k in PROVIDER_KEYS],
        )
        self.provider_box.pack(side="left", padx=(4, 12))
        self.provider_box.bind("<<ComboboxSelected>>", lambda e: self._change_provider())
        ttk.Label(ctl, text="取得間隔（秒）").pack(side="left")
        self.interval_var = tk.IntVar(value=300)
        sp = ttk.Spinbox(ctl, from_=10, to=86400, increment=10, textvariable=self.interval_var, width=8,
                         command=self._change_interval)
        sp.pack(side="left", padx=4)
        sp.bind("<FocusOut>", lambda e: self._change_interval())
        sp.bind("<Return>", lambda e: self._change_interval())
        self.notify_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctl, text="通知する", variable=self.notify_var, command=self._toggle_notify).pack(side="left", padx=10)

        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)

        # タブ1: グラフ＋最新画像
        tab_graph = ttk.Frame(nb)
        nb.add(tab_graph, text="グラフ")
        self.fig = Figure(figsize=(6, 3.6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=tab_graph)
        self.canvas.get_tk_widget().pack(side="left", fill="both", expand=True)
        side = ttk.Frame(tab_graph, width=260)
        side.pack(side="right", fill="y", padx=(8, 0))
        ttk.Label(side, text="最新の画像").pack(anchor="w")
        self.thumb_label = ttk.Label(side)
        self.thumb_label.pack(anchor="w", pady=4)
        self.latest_text = tk.Text(side, width=34, height=14, wrap="word", relief="flat", bg=self.cget("bg"))
        self.latest_text.pack(fill="both", expand=True)
        self.show_parts = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="観点ごとの線も表示", variable=self.show_parts,
                        command=self._draw_chart).pack(anchor="w")

        # タブ2: 設計図
        tab_plan = ttk.Frame(nb)
        nb.add(tab_plan, text="判定の設計図")
        self.plan_text = tk.Text(tab_plan, wrap="word")
        self.plan_text.pack(fill="both", expand=True)

        # タブ3: ログ
        tab_log = ttk.Frame(nb)
        nb.add(tab_log, text="ログ")
        self.log_text = tk.Text(tab_log, wrap="none", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar()
        ttk.Label(self, textvariable=self.status_var, anchor="w", foreground="#555", padding=(10, 2)).pack(fill="x")

    # ------------------------------------------------------------ 一覧と詳細
    def _selected(self) -> store.Monitor | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return next((m for m in self.monitors if m.id == sel[0]), None)

    def _row_values(self, m: store.Monitor) -> tuple:
        rt = self.scheduler.runtimes.get(m.id)
        last = rt.last_result if rt else None
        if last is None:
            rows = store.read_log(m.id, limit=1)
            metric = rows[-1]["metric"] if rows else None
            when = rows[-1]["timestamp"][11:16] if rows else ""
        else:
            metric, when = last.metric, last.timestamp[11:16]
        state = "動作中" if self.scheduler.is_running(m.id) else "停止"
        return (m.name, state, _fmt_metric(metric), when)

    def _refresh_list(self, keep: str | None = None):
        # 一覧の顔ぶれが同じなら、値だけ書き換える（選択や入力中の欄を崩さない）
        if keep is None and list(self.tree.get_children()) == [m.id for m in self.monitors]:
            for m in self.monitors:
                self.tree.item(m.id, values=self._row_values(m))
            return
        keep = keep or (self.tree.selection()[0] if self.tree.selection() else None)
        self.tree.delete(*self.tree.get_children())
        for m in self.monitors:
            self.tree.insert("", "end", iid=m.id, values=self._row_values(m))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        elif self.monitors:
            self.tree.selection_set(self.monitors[0].id)

    def _show_detail(self):
        m = self._selected()
        if not m:
            return
        self.detail_title.config(text=m.name)
        self.provider_var.set(provider_label(m.provider) if m.provider else "（既定の設定に従う）")
        self.interval_var.set(m.interval_seconds)
        self.notify_var.set(m.plan["notify"].get("enabled", True))
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("end", f"あなたの依頼: {m.request}\n\n{describe_plan(m.plan)}\n\n"
                                     f"範囲: {m.region}\n\n--- JSON ---\n{json.dumps(m.plan, ensure_ascii=False, indent=2)}")
        self._draw_chart()
        self._show_latest()
        self._show_log()

    def _draw_chart(self):
        m = self._selected()
        self.ax.clear()
        if not m:
            self.canvas.draw_idle()
            return
        rows = [r for r in store.read_log(m.id, limit=1000) if r["status"] in ("ok", "reused") and r["metric"] is not None]
        if not rows:
            self.ax.text(0.5, 0.5, "まだデータがありません", ha="center", va="center", transform=self.ax.transAxes, color="#888")
        else:
            xs = [datetime.fromisoformat(r["timestamp"]) for r in rows]
            if self.show_parts.get():
                for qid in m.plan["metric"]["weights"]:
                    ys = [r["values"].get(qid) for r in rows]
                    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
                    if pts:
                        self.ax.plot([p[0] for p in pts], [p[1] for p in pts], lw=1, alpha=0.45, label=qid)
            self.ax.plot(xs, [r["metric"] for r in rows], lw=2.4, color="#1f5fbf", label=m.plan["metric"]["name"])
            hits = [(x, r["metric"]) for x, r in zip(xs, rows) if r.get("notified") == "1"]
            if hits:
                self.ax.scatter([h[0] for h in hits], [h[1] for h in hits], s=60, color="#d33", zorder=5, label="通知")
            n = m.plan["notify"]
            if n.get("enabled", True):
                self.ax.axhline(n["threshold"], ls="--", lw=1, color="#d33", alpha=0.7)
            self.ax.xaxis.set_major_formatter(DateFormatter("%m/%d %H:%M"))
            self.ax.legend(loc="upper left", fontsize=8, ncol=2)
        self.ax.set_ylim(-0.03, 1.03)
        self.ax.set_ylabel(m.plan["metric"]["name"])
        self.ax.grid(alpha=0.25)
        self.fig.autofmt_xdate()
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _show_latest(self, result: Result | None = None):
        m = self._selected()
        if not m:
            return
        img = None
        if result is not None and result.image is not None:
            img = result.image
        else:
            p = store.monitor_dir(m.id) / "latest.jpg"
            if p.exists():
                from PIL import Image
                try:
                    img = Image.open(p)
                except OSError:
                    img = None
        if img is not None:
            t = img.copy()
            t.thumbnail((250, 180))
            self._thumb = ImageTk.PhotoImage(t)
            self.thumb_label.config(image=self._thumb)
        else:
            self.thumb_label.config(image="")
        self.latest_text.delete("1.0", "end")
        rt = self.scheduler.runtimes.get(m.id)
        r = result or (rt.last_result if rt else None)
        if r is None:
            rows = store.read_log(m.id, limit=1)
            if rows:
                last = rows[-1]
                self.latest_text.insert("end", f"{last['timestamp']}\n{m.plan['metric']['name']}: {_fmt_metric(last['metric'])}\n")
                for k, v in last["values"].items():
                    self.latest_text.insert("end", f"  {k}: {v:.2f}\n")
            return
        self.latest_text.insert("end", f"{r.timestamp}（{r.status}）\n")
        if r.error:
            self.latest_text.insert("end", f"エラー: {r.error}\n")
            return
        self.latest_text.insert("end", f"{m.plan['metric']['name']}: {_fmt_metric(r.metric)}\n")
        for qid, q in m.plan["questions"].items():
            a = r.answers.get(qid, {})
            if q["type"] == "noul":
                txt = f"{a.get('noul', 0):.2f}"
            elif q["type"] == "score":
                txt = f"{a.get('score', 0):.2f} / {len(q['criteria']) - 1}"
            else:
                txt = f"{a.get('choice', '-')}（確信度 {a.get('confidence', 0):.2f}）"
            self.latest_text.insert("end", f"・{qid}: {txt}\n")
        if r.latency_ms:
            self.latest_text.insert("end", f"\n判定 {r.latency_ms} ms / {r.provider}\n")

    def _show_log(self):
        m = self._selected()
        self.log_text.delete("1.0", "end")
        if not m:
            return
        for r in reversed(store.read_log(m.id, limit=300)):
            line = f"{r['timestamp']}  {r['status']:<6} {_fmt_metric(r['metric']):>5}  {r['provider']:<11} {r['latency_ms']:>6}ms"
            if r.get("notified") == "1":
                line += "  ★通知"
            if r.get("error"):
                line += f"  {r['error'][:120]}"
            self.log_text.insert("end", line + "\n")

    # ------------------------------------------------------------ 操作
    def _save(self):
        store.save_monitors(self.monitors)

    def _start_selected(self):
        m = self._selected()
        if not m:
            return
        m.enabled = True
        self._save()
        self.scheduler.start(m)
        self._status(f"「{m.name}」を開始しました（{m.interval_seconds} 秒ごと）")
        self._refresh_list()

    def _stop_selected(self):
        m = self._selected()
        if not m:
            return
        m.enabled = False
        self._save()
        self.scheduler.stop(m.id)
        self._status(f"「{m.name}」を停止しました")
        self._refresh_list()

    def _run_now(self):
        m = self._selected()
        if m:
            self.scheduler.run_now(m)
            self._status(f"「{m.name}」を判定中…")

    def _delete_selected(self):
        m = self._selected()
        if not m or not messagebox.askyesno("削除", f"「{m.name}」とその記録を削除しますか？"):
            return
        self.scheduler.stop(m.id)
        self.monitors = [x for x in self.monitors if x.id != m.id]
        self._save()
        store.delete_monitor_data(m.id)
        self._refresh_list()
        self._show_detail()

    def _change_provider(self):
        m = self._selected()
        if not m:
            return
        idx = self.provider_box.current()
        m.provider = "" if idx <= 0 else PROVIDER_KEYS[idx - 1]
        self._save()
        used = m.provider or self.settings.get("default_provider")
        self._status(f"「{m.name}」の判定モデルを {provider_label(used)} にしました（次の判定から）")

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
            self._status(f"取得間隔を {v} 秒にしました（次の待機から反映）")

    def _toggle_notify(self):
        m = self._selected()
        if m:
            m.plan["notify"]["enabled"] = bool(self.notify_var.get())
            self._save()

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
                    self._status(f"「{m.name}」でエラー: {r.error[:150]}")
                elif r.status == "reused":
                    self._status(f"「{m.name}」画面に変化がないため判定を省略（{r.timestamp[11:19]}）")
                else:
                    self._status(f"「{m.name}」{m.plan['metric']['name']} = {_fmt_metric(r.metric)}（{r.latency_ms} ms）")
                if r.notified:
                    self._notify(m, r)
                sel = self._selected()
                if sel and sel.id == m.id:
                    self._draw_chart()
                    self._show_latest(r)
                    self._show_log()
        except queue.Empty:
            pass
        if changed:
            self._refresh_list()
        self.after(200, self._poll)

    def _notify(self, m: store.Monitor, r: Result):
        msg = f"{m.plan['notify'].get('message', '')}（{m.plan['metric']['name']} {_fmt_metric(r.metric)}）"
        img = str(store.monitor_dir(m.id) / "latest.jpg")
        if not notify.send_toast(f"imiwatch: {m.name}", msg, img):
            ToastWindow(self, f"{m.name}", msg)

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

    def _on_close(self):
        self.scheduler.stop_all()
        self.destroy()


# =====================================================================
class ToastWindow(tk.Toplevel):
    """winotify が使えないときの、画面右下に出る簡易通知。"""

    def __init__(self, master, title: str, message: str):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#222")
        tk.Label(self, text=title, fg="white", bg="#222", font=("Yu Gothic UI", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        tk.Label(self, text=message, fg="#ddd", bg="#222", wraplength=320, justify="left").pack(anchor="w", padx=12, pady=(2, 10))
        self.update_idletasks()
        w, h = 360, self.winfo_reqheight()
        x = self.winfo_screenwidth() - w - 24
        y = self.winfo_screenheight() - h - 72
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.bind("<Button-1>", lambda e: self.destroy())
        self.after(8000, self.destroy)


# =====================================================================
class NewMonitorDialog(tk.Toplevel):
    """範囲 → 依頼 → 設計 → 試し判定 → 保存、の順で見張りを作る。"""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("新しい見張り")
        self.geometry("880x700")
        self.transient(app)
        self.region: dict | None = None
        self.preview_img = None
        self.plan: dict | None = None

        pad = {"padx": 12, "pady": 4}
        # 1. 範囲
        f1 = ttk.LabelFrame(self, text="① 見張る範囲", padding=8)
        f1.pack(fill="x", **pad)
        ttk.Button(f1, text="画面上でドラッグして選ぶ", command=self._pick_region).pack(side="left")
        self.region_label = ttk.Label(f1, text="未選択", foreground="#666")
        self.region_label.pack(side="left", padx=10)
        self.preview_label = ttk.Label(f1)
        self.preview_label.pack(side="right")

        # 2. 依頼
        f2 = ttk.LabelFrame(self, text="② 何が起きたら知りたいですか？ 何を測りたいですか？（自由に書いてください）", padding=8)
        f2.pack(fill="x", **pad)
        self.request = tk.Text(f2, height=3, wrap="word")
        self.request.pack(fill="x")
        ex = ttk.Frame(f2)
        ex.pack(fill="x", pady=(4, 0))
        ttk.Label(ex, text="例:", foreground="#666").pack(side="left")
        for e in EXAMPLES:
            b = ttk.Button(ex, text=e[:16] + "…", command=lambda t=e: self._set_request(t))
            b.pack(side="left", padx=2)

        # 3. 設計
        f3 = ttk.LabelFrame(self, text="③ 判定の設計図（AIが観点に分解します。JSON は直接編集できます）", padding=8)
        f3.pack(fill="both", expand=True, **pad)
        row = ttk.Frame(f3)
        row.pack(fill="x")
        self.plan_btn = ttk.Button(row, text="判定を設計する", command=self._make_plan)
        self.plan_btn.pack(side="left")
        ttk.Button(row, text="JSON の編集を反映", command=self._apply_json).pack(side="left", padx=6)
        self.plan_note = ttk.Label(row, text="", foreground="#666")
        self.plan_note.pack(side="left", padx=8)
        panes = ttk.PanedWindow(f3, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(6, 0))
        self.plan_view = tk.Text(panes, wrap="word", height=12)
        self.plan_json = tk.Text(panes, wrap="none", height=12, font=("Consolas", 9))
        panes.add(self.plan_view, weight=1)
        panes.add(self.plan_json, weight=1)

        # 4. 試し判定と保存
        f4 = ttk.LabelFrame(self, text="④ 試して保存", padding=8)
        f4.pack(fill="x", **pad)
        ttk.Label(f4, text="判定モデル").grid(row=0, column=0, sticky="w")
        self.provider_box = ttk.Combobox(
            f4, state="readonly", width=40,
            values=["（既定の設定に従う）"] + [provider_label(k) for k in PROVIDER_KEYS],
        )
        self.provider_box.current(0)
        self.provider_box.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Button(f4, text="今の画面で試し判定", command=self._trial).grid(row=0, column=2, padx=8)
        ttk.Label(f4, text="名前").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(f4, textvariable=self.name_var, width=30).grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(f4, text="取得間隔（秒）").grid(row=1, column=2, sticky="e", pady=(6, 0))
        self.interval_var = tk.IntVar(value=300)
        ttk.Spinbox(f4, from_=10, to=86400, increment=10, textvariable=self.interval_var, width=8).grid(
            row=1, column=3, sticky="w", padx=4, pady=(6, 0))
        self.trial_label = ttk.Label(f4, text="", foreground="#1f5fbf", wraplength=820, justify="left")
        self.trial_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))

        bottom = ttk.Frame(self, padding=(12, 6))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="保存して開始", command=lambda: self._save(True)).pack(side="right")
        ttk.Button(bottom, text="保存のみ", command=lambda: self._save(False)).pack(side="right", padx=6)
        ttk.Button(bottom, text="キャンセル", command=self.destroy).pack(side="left")

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
            if region:
                self.region = region
                self.region_label.config(text=f"{region['width']}×{region['height']} px（左上 {region['left']}, {region['top']}）",
                                         foreground="black")
                try:
                    img = capture.grab(region)
                    self.preview_img = img
                    t = img.copy()
                    t.thumbnail((240, 110))
                    self._ph = ImageTk.PhotoImage(t)
                    self.preview_label.config(image=self._ph)
                except Exception as e:  # noqa: BLE001
                    messagebox.showerror("撮影エラー", str(e), parent=self)

        # ウィンドウが隠れきるのを待ってから幕を出す
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

    def _make_plan(self):
        req = self._request_text()
        if not req:
            messagebox.showinfo("入力してください", "② に、知りたいことを書いてください。", parent=self)
            return
        self.plan_btn.config(state="disabled")
        self.plan_note.config(text="設計中…（数秒〜数十秒）")
        img = self._current_image_url()
        settings = dict(self.app.settings)

        def work():
            plan, note = planner.make_plan(settings, req, img)
            self.app.call_soon(lambda: self._set_plan(plan, note))

        threading.Thread(target=work, daemon=True).start()

    def _set_plan(self, plan: dict, note: str):
        self.plan_btn.config(state="normal")
        self.plan = plan
        self.plan_note.config(text=note)
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
            messagebox.showerror("設計図のエラー", str(e), parent=self)
            return False
        self.plan = plan
        self.plan_view.delete("1.0", "end")
        self.plan_view.insert("end", describe_plan(plan))
        return True

    def _provider(self) -> str:
        idx = self.provider_box.current()
        return "" if idx <= 0 else PROVIDER_KEYS[idx - 1]

    def _draft(self) -> store.Monitor | None:
        if not self.region:
            messagebox.showinfo("範囲", "① で見張る範囲を選んでください。", parent=self)
            return None
        if self.plan is None:
            messagebox.showinfo("設計図", "③ で判定を設計してください。", parent=self)
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
        self.trial_label.config(text="判定中…")
        rt = MonitorRuntime(m)
        settings = dict(self.app.settings)

        def work():
            r = rt.run_once(settings, force=True, record=False)
            self.app.call_soon(lambda: self._show_trial(m, r))

        threading.Thread(target=work, daemon=True).start()

    def _show_trial(self, m: store.Monitor, r: Result):
        if r.status == "error":
            self.trial_label.config(text=f"エラー: {r.error}", foreground="#c00")
            return
        parts = ", ".join(f"{k} {v:.2f}" for k, v in r.values.items())
        n = m.plan["notify"]
        hit = (r.metric is not None) and (r.metric >= n["threshold"] if n["op"] == ">=" else r.metric <= n["threshold"])
        self.trial_label.config(
            foreground="#1f5fbf",
            text=f"今の画面: {m.plan['metric']['name']} = {_fmt_metric(r.metric)}"
                 f"（通知条件は{'成立' if hit else '不成立'}） / 内訳: {parts} / {r.latency_ms} ms・{r.provider}",
        )

    def _save(self, start: bool):
        m = self._draft()
        if not m:
            return
        self.app.add_monitor(m, start)
        self.destroy()


# =====================================================================
class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("Cloudflare（Clef / Clef-flash）", None, None),
        ("アカウントID", "cloudflare_account_id", False),
        ("APIトークン", "cloudflare_api_token", True),
        ("Perplexity", None, None),
        ("APIキー", "perplexity_api_key", True),
        ("モデル", "perplexity_model", False),
        ("SystemOne 互換（Jev など）", None, None),
        ("エンドポイントURL", "systemone_base_url", False),
        ("APIキー", "systemone_api_key", True),
        ("モデル", "systemone_model", False),
        ("OpenRouter（設計役のLLM・LLMでの代用判定）", None, None),
        ("APIキー", "openrouter_api_key", True),
        ("設計に使うモデル", "planner_model", False),
        ("代用判定に使うモデル", "llm_judge_model", False),
        ("画像と差分", None, None),
        ("画像の長辺（px）", "max_image_side", False),
        ("差分のしきい値（0-255）", "diff_threshold", False),
    ]

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("設定")
        self.transient(app)
        self.resizable(True, True)
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        s = app.settings
        ttk.Label(frm, text="既定の判定モデル", font=("Yu Gothic UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.default_box = ttk.Combobox(frm, state="readonly", width=46, values=[provider_label(k) for k in PROVIDER_KEYS])
        self.default_box.current(PROVIDER_KEYS.index(s.get("default_provider", "clef-flash"))
                                 if s.get("default_provider") in PROVIDER_KEYS else 0)
        self.default_box.grid(row=0, column=1, sticky="w", pady=4)

        self.vars: dict[str, tk.StringVar] = {}
        r = 1
        for label, key, secret in self.FIELDS:
            if key is None:
                ttk.Label(frm, text=label, font=("Yu Gothic UI", 10, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
            else:
                ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", padx=(12, 0))
                v = tk.StringVar(value=str(s.get(key, "")))
                ttk.Entry(frm, textvariable=v, width=48, show="•" if secret else "").grid(row=r, column=1, sticky="w", pady=2)
                self.vars[key] = v
            r += 1
        ttk.Label(frm, text="SystemOne の画像の送り方").grid(row=r, column=0, sticky="w", padx=(12, 0))
        self.image_mode = ttk.Combobox(frm, state="readonly", width=20, values=["none", "clef", "perplexity"])
        self.image_mode.set(s.get("systemone_image_mode", "none"))
        self.image_mode.grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(frm, text=f"保存先: {store.settings_path()}\n※ APIキーは暗号化せずに保存されます。共有PCでは環境変数での指定もできます。",
                  foreground="#777", wraplength=600, justify="left").grid(row=r, column=0, columnspan=2, sticky="w", pady=(14, 0))
        r += 1
        b = ttk.Frame(frm)
        b.grid(row=r, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(b, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(b, text="保存", command=self._save).pack(side="right", padx=6)

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
