from __future__ import annotations

import ctypes
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, scrolledtext, ttk

from web_content_agent.logging_setup import configure_logging
from web_content_agent.onepoint3acres.chrome_bridge import BridgeServer
from web_content_agent.onepoint3acres.diagnostics import LOG_RETENTION_DAYS, PersistentRunLog
from web_content_agent.onepoint3acres.journal import QuizJournal
from web_content_agent.onepoint3acres.model_selection import (
    installed_models,
    save_selection,
    selected_model,
)
from web_content_agent.onepoint3acres.scheduler import DailyScheduleLedger
from web_content_agent.onepoint3acres.supervision import (
    INTERNAL_SCHEDULE_ENABLED,
    SupervisedChromeBridge,
)
from web_content_agent.paths import default_workspace_root, project_root

UI_REFRESH_INTERVAL_MS = 1_000
SCHEDULE_CHECK_INTERVAL_MS = 10 * 60 * 1_000


def enable_windows_dpi_awareness() -> None:
    """Ask Windows to render Tk at the monitor's native DPI instead of bitmap scaling it."""
    if sys.platform != "win32":
        return
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return
    try:
        windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def log_style_for_message(message: str) -> str:
    if any(word in message for word in ("失败", "停止", "错误", "RuntimeError", "TimeoutError")):
        return "error"
    if any(word in message for word in ("成功", "已完成", "本次运行结束", "今日已经")):
        return "success"
    if any(word in message for word in ("开始", "等待", "已连接", "Chrome run id")):
        return "accent"
    return "normal"


def format_local_clock(now: datetime) -> str:
    offset = now.strftime("%z")
    utc_offset = f"UTC{offset[:3]}:{offset[3:]}" if len(offset) == 5 else now.strftime("%Z")
    return f"{now:%Y-%m-%d %H:%M:%S} · {utc_offset}"


class OnePointThreeAcresApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.colors = {
            "page": "#F3F6FA",
            "surface": "#FFFFFF",
            "surface_muted": "#EAF1F8",
            "navy": "#102A43",
            "text": "#16283A",
            "muted": "#61758A",
            "accent": "#167D8D",
            "accent_hover": "#126B79",
            "accent_soft": "#DDF2F3",
            "border": "#D8E2EC",
            "log": "#10202F",
            "log_text": "#E8F0F7",
            "log_muted": "#AFC0CF",
            "success": "#6EE7B7",
            "error": "#FDA4AF",
        }
        self._configure_window()
        self._configure_styles()
        self.running = False
        self.run_request_pending = False
        self._request_lock = threading.Lock()
        self.close_request_pending = False
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.diagnostic_log = PersistentRunLog(
            project_root() / ".local" / "state" / "onepoint3acres-app.log"
        )
        try:
            removed_logs = self.diagnostic_log.prune_older_than(LOG_RETENTION_DAYS)
        except OSError:
            removed_logs = 0
        self.ledger = DailyScheduleLedger(
            project_root() / ".local" / "state" / "onepoint3acres-app.json", hour=9
        )
        self.daily_review_ledger = DailyScheduleLedger(
            project_root() / ".local/state/onepoint3acres-codex-daily.json", hour=0, strict=True
        )
        self.bridge = SupervisedChromeBridge(
            self._output, journal=QuizJournal(default_workspace_root())
        )
        self.bridge_server = BridgeServer(
            self.bridge,
            self.diagnostic_log.tail,
            self._queue_background_run,
            request_simulation=self._queue_simulation,
            request_daily_run=lambda: self._queue_background_run(daily=True),
            request_close=self._queue_close,
        )
        threading.Thread(target=self.bridge_server.serve_forever, daemon=True).start()

        self.clock_text = tk.StringVar()
        self.next_run_text = tk.StringVar()
        self.status_text = tk.StringVar(value="空闲")
        self.last_run_text = tk.StringVar(
            value=self.daily_review_ledger.last_started_at or "尚未运行"
        )
        self._build_ui()
        self._refresh_models()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain_events)
        self.root.after(250, self._refresh_status)
        if INTERNAL_SCHEDULE_ENABLED:
            self.root.after(250, self._schedule_tick)
        self._log(
            "App 已启动：Codex 审核模式。App 内部定时已关闭；"
            "Codex 每 6 小时检查，签到与答题未全部完成就继续。"
        )
        self._log("本地模型只给建议；必须收到 Codex 明确批准才提交答案，也可以跳过。")
        self._log(
            "日志保留至少 7 天；题目、证据与模型结果另存 "
            "WebContent/runs/onepoint3acres，供复查，不自动学习。"
        )
        if removed_logs:
            self._log(f"启动清理：已删除 {removed_logs} 条超过 {LOG_RETENTION_DAYS} 天的历史日志。")

    def _configure_window(self) -> None:
        self.root.title("一亩三分地每日助手")
        self.root.configure(background=self.colors["page"])
        self.root.update_idletasks()
        scaling = max(1.333, float(self.root.winfo_fpixels("1i")) / 72.0)
        self.root.tk.call("tk", "scaling", scaling)

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1100, max(860, screen_width - 120))
        height = min(780, max(640, screen_height - 140))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(860, width), min(640, height))

    def _configure_styles(self) -> None:
        self.fonts = {
            "title": tkfont.Font(self.root, family="Microsoft YaHei UI", size=23, weight="bold"),
            "subtitle": tkfont.Font(self.root, family="Microsoft YaHei UI", size=11),
            "section": tkfont.Font(self.root, family="Microsoft YaHei UI", size=13, weight="bold"),
            "body": tkfont.Font(self.root, family="Microsoft YaHei UI", size=11),
            "body_bold": tkfont.Font(
                self.root, family="Microsoft YaHei UI", size=11, weight="bold"
            ),
            "metric": tkfont.Font(self.root, family="Microsoft YaHei UI", size=12, weight="bold"),
            "small": tkfont.Font(self.root, family="Microsoft YaHei UI", size=10),
            "log": tkfont.Font(self.root, family="Microsoft YaHei UI", size=11),
        }
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Page.TFrame", background=self.colors["page"])
        style.configure("Hero.TFrame", background=self.colors["navy"])
        style.configure("Card.TFrame", background=self.colors["surface"])
        style.configure("Metric.TFrame", background=self.colors["surface"])
        style.configure("Status.TFrame", background=self.colors["accent_soft"])
        style.configure(
            "HeroTitle.TLabel",
            background=self.colors["navy"],
            foreground="#FFFFFF",
            font=self.fonts["title"],
        )
        style.configure(
            "HeroSubtitle.TLabel",
            background=self.colors["navy"],
            foreground="#C9D7E5",
            font=self.fonts["subtitle"],
        )
        style.configure(
            "StatusDot.TLabel",
            background=self.colors["accent_soft"],
            foreground=self.colors["accent"],
            font=self.fonts["body_bold"],
        )
        style.configure(
            "StatusText.TLabel",
            background=self.colors["accent_soft"],
            foreground=self.colors["navy"],
            font=self.fonts["body_bold"],
        )
        style.configure(
            "Section.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["text"],
            font=self.fonts["section"],
        )
        style.configure(
            "SectionHint.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self.fonts["small"],
        )
        style.configure(
            "MetricLabel.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self.fonts["small"],
        )
        style.configure(
            "MetricValue.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["text"],
            font=self.fonts["metric"],
        )
        style.configure(
            "Primary.TButton",
            background=self.colors["accent"],
            foreground="#FFFFFF",
            borderwidth=0,
            focusthickness=2,
            focuscolor=self.colors["accent_soft"],
            font=self.fonts["body_bold"],
            padding=(20, 12),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#A6BEC4"),
                ("pressed", self.colors["accent_hover"]),
                ("active", self.colors["accent_hover"]),
            ],
            foreground=[("disabled", "#EAF1F3")],
        )
        style.configure(
            "Secondary.TButton",
            background=self.colors["surface_muted"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
            borderwidth=1,
            focusthickness=1,
            focuscolor=self.colors["border"],
            font=self.fonts["body"],
            padding=(16, 11),
        )
        style.map(
            "Secondary.TButton",
            background=[("pressed", "#DDE8F1"), ("active", "#E2ECF4")],
        )
        style.configure("Card.TSeparator", background=self.colors["border"])

    def _build_ui(self) -> None:
        hero = ttk.Frame(self.root, style="Hero.TFrame", padding=(28, 22))
        hero.pack(fill=tk.X)
        hero.columnconfigure(0, weight=1)
        ttk.Label(hero, text="一亩三分地每日助手", style="HeroTitle.TLabel").grid(
            row=0, column=0, sticky=tk.W
        )
        ttk.Label(
            hero,
            text="使用当前 Chrome 登录；本地模型给建议，Codex 审核后才提交答案",
            style="HeroSubtitle.TLabel",
        ).grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        status = ttk.Frame(hero, style="Status.TFrame", padding=(14, 8))
        status.grid(row=0, column=1, rowspan=2, sticky=tk.E)
        ttk.Label(status, text="●", style="StatusDot.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.status_text, style="StatusText.TLabel").pack(
            side=tk.LEFT, padx=(7, 0)
        )

        outer = ttk.Frame(self.root, style="Page.TFrame", padding=(24, 20, 24, 24))
        outer.pack(fill=tk.BOTH, expand=True)

        info = ttk.Frame(outer, style="Card.TFrame", padding=(20, 17))
        info.pack(fill=tk.X)
        ttk.Label(info, text="运行信息", style="Section.TLabel").pack(anchor=tk.W)
        ttk.Separator(info, style="Card.TSeparator").pack(fill=tk.X, pady=(12, 14))
        metrics = ttk.Frame(info, style="Card.TFrame")
        metrics.pack(fill=tk.X)
        values = (
            ("当前时间", self.clock_text),
            ("下次自动运行", self.next_run_text),
            ("上次运行启动", self.last_run_text),
            ("当前状态", self.status_text),
        )
        for index, (label, variable) in enumerate(values):
            row, column = divmod(index, 2)
            metrics.columnconfigure(column, weight=1, uniform="metric")
            metric = ttk.Frame(metrics, style="Metric.TFrame")
            metric.grid(
                row=row,
                column=column,
                sticky=tk.NSEW,
                padx=(0 if column == 0 else 28, 0),
                pady=(0 if row == 0 else 16, 0),
            )
            ttk.Label(metric, text=label, style="MetricLabel.TLabel").pack(anchor=tk.W)
            ttk.Label(
                metric,
                textvariable=variable,
                style="MetricValue.TLabel",
                wraplength=420,
            ).pack(anchor=tk.W, pady=(5, 0))

        model_row = ttk.Frame(info, style="Card.TFrame")
        model_row.pack(fill=tk.X, pady=(16, 0))
        ttk.Label(model_row, text="答题模型", style="MetricLabel.TLabel").pack(side=tk.LEFT)
        self.model_choices: dict[str, str | None] = {"WebContentAgent 默认模型": None}
        self.model_text = tk.StringVar(value="正在读取已下载模型……")
        self.model_combo = ttk.Combobox(
            model_row, textvariable=self.model_text, state=tk.DISABLED, font=self.fonts["small"]
        )
        self.model_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        self.model_combo.bind("<<ComboboxSelected>>", self._select_model)
        self.model_refresh = ttk.Button(
            model_row, text="刷新模型", command=self._refresh_models, style="Secondary.TButton"
        )
        self.model_refresh.pack(side=tk.RIGHT)
        ttk.Label(
            info,
            text="仅答题时加载；临时切换空闲模型，完成后恢复默认。不会在开机时加载。",
            style="SectionHint.TLabel",
        ).pack(anchor=tk.W, pady=(6, 0))

        actions = ttk.Frame(outer, style="Page.TFrame")
        actions.pack(fill=tk.X, pady=(16, 16))
        self.run_button = ttk.Button(
            actions,
            text="立即运行一次",
            command=self._manual_run,
            style="Primary.TButton",
        )
        self.run_button.pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="清空日志",
            command=self._clear_log,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(
            actions,
            text="App 定时已关闭 · 等待 Codex 触发与审核",
            style="SectionHint.TLabel",
        ).pack(side=tk.RIGHT)

        log_card = ttk.Frame(outer, style="Card.TFrame", padding=(20, 17, 20, 20))
        log_card.pack(fill=tk.BOTH, expand=True)
        log_header = ttk.Frame(log_card, style="Card.TFrame")
        log_header.pack(fill=tk.X)
        ttk.Label(log_header, text="运行日志", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            log_header,
            text="新消息会自动滚动到最下方",
            style="SectionHint.TLabel",
        ).pack(side=tk.RIGHT)
        ttk.Separator(log_card, style="Card.TSeparator").pack(fill=tk.X, pady=(12, 14))

        log_frame = tk.Frame(
            log_card,
            background=self.colors["log"],
            highlightthickness=1,
            highlightbackground="#233B50",
        )
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_view = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=self.fonts["log"],
            background=self.colors["log"],
            foreground=self.colors["log_text"],
            insertbackground=self.colors["log_text"],
            selectbackground=self.colors["accent"],
            borderwidth=0,
            relief=tk.FLAT,
            padx=14,
            pady=12,
            spacing1=2,
            spacing3=4,
        )
        self.log_view.pack(fill=tk.BOTH, expand=True)
        self.log_view.tag_configure("normal", foreground=self.colors["log_text"])
        self.log_view.tag_configure("accent", foreground="#7DD3FC")
        self.log_view.tag_configure("success", foreground=self.colors["success"])
        self.log_view.tag_configure("error", foreground=self.colors["error"])

    def _log(self, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(sep=" ", timespec="seconds")
        line = f"[{timestamp}] {message}"
        try:
            self.diagnostic_log.append(line)
        except OSError:
            pass
        self.events.put(("log", line))

    def _output(self, message: str) -> None:
        for line in message.splitlines() or [""]:
            self._log(line)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_view.configure(state=tk.NORMAL)
                    message = str(payload)
                    self.log_view.insert(
                        tk.END,
                        f"{message}\n",
                        (log_style_for_message(message),),
                    )
                    self.log_view.see(tk.END)
                    self.log_view.configure(state=tk.DISABLED)
                elif kind == "done":
                    self.running = False
                    self.run_button.configure(state=tk.NORMAL)
                    self.status_text.set("空闲")
                    self.model_combo.configure(state="readonly")
                    self.model_refresh.configure(state=tk.NORMAL)
                elif kind == "close_request":
                    self._close()
                    return
                elif kind == "models":
                    models, selected = payload
                    self.model_choices = {"WebContentAgent 默认模型": None}
                    self.model_choices.update({item["label"]: item["key"] for item in models})
                    label = next(
                        (name for name, key in self.model_choices.items() if key == selected), None
                    )
                    if label is None:
                        label = f"未安装：{selected}（请重新选择）"
                        self.model_choices[label] = selected
                    self.model_text.set(label)
                    self._log(f"答题模型已配置：{label}；只在需要答题时加载。")
                    self.model_combo.configure(
                        values=list(self.model_choices),
                        state=tk.DISABLED if self.running else "readonly",
                    )
                    self.model_refresh.configure(state=tk.DISABLED if self.running else tk.NORMAL)
                elif kind == "models_error":
                    self.model_text.set("模型列表读取失败；请刷新")
                    self.model_refresh.configure(state=tk.NORMAL)
                    self._log(f"模型列表读取失败：{payload}")
                elif kind == "run_request":
                    self.run_request_pending = False
                    if not self.running:
                        self._manual_run()
                elif kind == "simulate_request":
                    self.run_request_pending = False
                    if not self.running:
                        self._start_run(scheduled=False, simulation_payload=payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _refresh_models(self) -> None:
        if self.running:
            return
        self.model_combo.configure(state=tk.DISABLED)
        self.model_refresh.configure(state=tk.DISABLED)

        def discover() -> None:
            try:
                self.events.put(("models", (installed_models(), selected_model())))
            except Exception as exc:
                self.events.put(("models_error", str(exc)))

        threading.Thread(target=discover, daemon=True).start()

    def _select_model(self, _event: object = None) -> None:
        if self.running or self.run_request_pending:
            return
        try:
            save_selection(self.model_choices[self.model_text.get()])
            self._log(f"已保存答题模型：{self.model_text.get()}。只影响本助手，下次答题时生效。")
        except Exception as exc:
            self._log(f"保存模型失败：{exc}")
            self._refresh_models()

    def _refresh_status(self) -> None:
        now = datetime.now().astimezone()
        self.clock_text.set(format_local_clock(now))
        self.next_run_text.set("Codex 每 6 小时检查；签到与答题未全部完成就继续")
        if self.running:
            state = self.bridge.status().get("quiz_solve_status")
            if state == "awaiting_review":
                self.status_text.set("等待 Codex 审核（不会自动提交）")
            elif state == "pending":
                self.status_text.set("等待本地模型建议")
            elif state == "complete":
                self.status_text.set("Codex 已批准，等待 Chrome 执行")
        self.root.after(UI_REFRESH_INTERVAL_MS, self._refresh_status)

    def _schedule_tick(self) -> None:
        if not INTERNAL_SCHEDULE_ENABLED:
            return
        now = datetime.now().astimezone()
        if self.ledger.is_due(now) and not self.running and not self.run_request_pending:
            self.ledger.mark_started(now)
            self.last_run_text.set(self.ledger.last_started_at or "")
            self._start_run(scheduled=True)
        self.root.after(SCHEDULE_CHECK_INTERVAL_MS, self._schedule_tick)

    def _manual_run(self) -> None:
        if self.running or self.run_request_pending or self.close_request_pending:
            messagebox.showinfo("正在运行", "已有一次检查正在运行，请等待它结束。")
            return
        self._start_run(scheduled=False)

    def _queue_background_run(self, *, daily: bool = False) -> bool:
        with self._request_lock:
            if self.running or self.run_request_pending or self.close_request_pending:
                return False
            if daily:
                now = datetime.now().astimezone()
                if self.bridge.journal.daily_progress()["completed"]:
                    return False
                self.daily_review_ledger.mark_started(now)
            self.run_request_pending = True
            self.events.put(("run_request", None))
            return True

    def _queue_close(self) -> bool:
        with self._request_lock:
            if self.running or self.run_request_pending or self.close_request_pending:
                return False
            self.close_request_pending = True
            self.events.put(("close_request", None))
            return True

    def _queue_simulation(self, payload: dict) -> bool:
        with self._request_lock:
            if self.running or self.run_request_pending or self.close_request_pending:
                return False
            self.run_request_pending = True
            self.events.put(("simulate_request", payload))
            return True

    def _start_run(self, *, scheduled: bool, simulation_payload: dict | None = None) -> None:
        with self._request_lock:
            if self.running or self.close_request_pending:
                return
            self.running = True
            self.run_request_pending = False
        if simulation_payload is None:
            self.last_run_text.set(datetime.now().astimezone().isoformat(timespec="seconds"))
        self.run_button.configure(state=tk.DISABLED)
        self.model_combo.configure(state=tk.DISABLED)
        self.model_refresh.configure(state=tk.DISABLED)
        mode = "签到与答题审核"
        if simulation_payload is not None:
            mode = "模拟答题（不提交）"
        self.status_text.set(mode)
        threading.Thread(
            target=self._run_worker, args=(scheduled, simulation_payload), daemon=True
        ).start()

    def _run_worker(self, scheduled: bool, simulation_payload: dict | None = None) -> None:
        try:
            if simulation_payload is not None:
                result = self.bridge.simulate(simulation_payload)
                if not result["ok"]:
                    raise RuntimeError(result["error"])
            else:
                self._execute(scheduled)
        except Exception as exc:
            self._log(f"安全停止：{type(exc).__name__}: {exc}")
        else:
            self._log("本次运行结束。")
        finally:
            self.events.put(("done", None))

    def _execute(self, scheduled: bool) -> None:
        # This ledger records attempts only. Daily completion comes from explicit
        # check-in AND quiz outcomes in the journal, never from this timestamp.
        with self._request_lock:
            now = datetime.now().astimezone()
            if self.daily_review_ledger.last_scheduled_date != now.date().isoformat():
                self.daily_review_ledger.mark_started(now)
        run_id = self.bridge.start_run()
        mode = "签到与答题审核"
        self._log(f"[1/8] 已创建{mode}；run id：{run_id}。正在打开 Chrome。")
        self._launch_chrome()
        self._log("[1/8] 等待 Chrome 扩展下一次每分钟轮询并接管已登录会话……")
        result = self.bridge.wait(timeout_seconds=900)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error", "Chrome 自动运行失败")))

    def _launch_chrome(self) -> None:
        candidates = [
            shutil.which("chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        chrome_path = next((path for path in candidates if path and Path(path).is_file()), None)
        if chrome_path is None:
            raise RuntimeError("没有找到 Google Chrome")
        subprocess.Popen([chrome_path, "https://www.1point3acres.com/home"])

    def _clear_log(self) -> None:
        self.log_view.configure(state=tk.NORMAL)
        self.log_view.delete("1.0", tk.END)
        self.log_view.configure(state=tk.DISABLED)

    def _close(self) -> None:
        if self.running or self.run_request_pending:
            messagebox.showinfo("正在运行", "请先完成当前浏览器操作，等待状态恢复为空闲后再关闭。")
            return
        self.bridge_server.shutdown()
        self.bridge_server.server_close()
        self.root.destroy()


def main() -> None:
    configure_logging(False)
    enable_windows_dpi_awareness()
    root = tk.Tk()
    OnePointThreeAcresApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
