import asyncio
import ctypes
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Portable EXE builds keep the Playwright browser beside the executable.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)
    # Qt's DLL dependency resolution can vary with the process working
    # directory (for example when launched from a desktop shortcut).  Add
    # the bundled runtime directories explicitly so the app starts reliably
    # no matter where the shortcut is launched from.
    if sys.platform == "win32":
        _bundle_dir = Path(sys._MEIPASS)
        # Put bundled Qt libraries first.  Some machines have another Qt
        # installation on PATH; loading that version causes WinError 127.
        _dll_paths = [
            _bundle_dir,
            _bundle_dir / "PySide6",
            _bundle_dir / "shiboken6",
        ]
        os.environ["PATH"] = os.pathsep.join(
            [str(p) for p in _dll_paths if p.is_dir()] + [os.environ.get("PATH", "")]
        )
        for _dll_dir in (
            _bundle_dir,
            _bundle_dir / "PySide6",
            _bundle_dir / "shiboken6",
        ):
            try:
                if _dll_dir.is_dir():
                    os.add_dll_directory(str(_dll_dir))
            except (AttributeError, OSError):
                pass
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path(sys.executable).resolve().parent / "ms-playwright"),
    )

def _enable_windows_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

_enable_windows_dpi_awareness()

from playwright.async_api import async_playwright
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

BASE_URL = "https://tybyy.ujs.edu.cn/"
ROUTE = "/pages/subscribe/index?itemId=2&title=%E7%BE%BD%E6%AF%9B%E7%90%83"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
PROFILE = APP_DIR / "browser_profile"

# ---------------------------------------------------------------------------
# 预约系统接口（逆向自站点前端 bundle：assets/schedule.*.js + pages-subscribe-index.*.js）
#
#   GET  /api/v1/mobile/schedule/list/?item=2
#        -> detail.allowTimeRange / isBan / scheduleList[{id, schedule_date, status}]
#   GET  /api/v1/mobile/schedule/detail/?item=2&schedule=<id>
#        -> detail.sessionObj["18:00-19:00@@1"] = {id, session_date, disabled_time,
#                                                  ticket_num, subscribed_num, price, ...}
#           detail.areas  = ["1@@1号场地", ...]      <- "code@@显示名"
#           detail.times  = ["18:00-19:00", ...]
#           detail.maxSubscribeNum
#   POST /api/v1/mobile/session/submit/  {"sessionIds": [id, ...]}
#        -> detail[{order_id, ...}]
#
# 认证：请求头 Authorization: "GYMMOBILE " + localStorage["App-Token"].data
# 判定：已过期 -> session_date+disabled_time <= now；已订满 -> ticket_num <= subscribed_num
# ---------------------------------------------------------------------------
API_ITEM_ID = "2"                               # 羽毛球（对应 ROUTE 里的 itemId=2）
API_LIST = "/api/v1/mobile/schedule/list/"
API_DETAIL = "/api/v1/mobile/schedule/detail/"
API_SUBMIT = "/api/v1/mobile/session/submit/"
TOKEN_KEY = "App-Token"
AUTH_SCHEME = "GYMMOBILE"

# ---------------------------------------------------------------------------
# 界面场地清单（唯一的顺序定义）
#
# 界面上的勾选项保持原样，不需要用户同步。程序在匹配时把这套名字映射到接口
# 返回的 areas。映射顺序：名字完全一致 > 同楼层内名字匹配 > 按本清单的声明顺序
# 取第 N 片。所以**这里的顺序和分组就是最终依据**，改动它等于改动映射结果。
# ---------------------------------------------------------------------------
FLOOR_TOKENS = ("一楼", "二楼", "三楼", "四楼", "五楼", "地下", "负一", "负二")

VENUE_GROUPS = [
    ("一楼场地", "共 8 个场地，可多选备选。",
     ["一楼塑胶1", "一楼塑胶2", "一楼塑胶3", "一楼木质4",
      "一楼塑胶5", "一楼塑胶6", "一楼塑胶7", "一楼木质8"]),
    ("二楼场地", "共 12 个场地，可多选备选。",
     ["二楼塑胶%d" % i for i in range(1, 13)]),
]

# ---------------------------------------------------------------------------
# 单次预约上限（江大系统一次最多只能选 2 个场次）
#
# 界面上的「已选 N 个」到 MAX_PER_ORDER 就不再让点，所以程序也必须遵守：
# 界面上允许勾任意多个备选，开抢时只按顺序提交前 MAX_PER_ORDER 个可约单元，
# 剩下的留作备选 —— 前面抢不到时自动往后顺延，不需要用户改勾选。
# ---------------------------------------------------------------------------
MAX_PER_ORDER = 2

# 凑数宽限（秒）：第一次发现"可约数不够 MAX_PER_ORDER"之后，最多再等这么久，
# 看看同一批放号的其它场地是不是跟着出现。窗口一到就按实际可约数提交
# （哪怕只有 1 个）—— 到手的绝不为了凑满 2 个而丢掉。
# 设成 0 表示一刻不等：只要发现 ≥1 个可约就立刻提交。
# CHANG 定的是"有几个抢先几个"，所以这里就是 0，不做任何等待。
FILL_GRACE_SECONDS = 0.0

# 页面轻量刷新的节拍（秒）：点站点自带的刷新图标重拉数据，比整页 reload 快得多。
# 11:59 进入筛选状态后就开始按这个节拍刷新，保证浏览器里的界面和接口看到的是同一份数据，
# 同时让「页面点击」这条兜底通道一直是热的。设成 0 就完全不刷新页面（纯接口模式）。
DOM_REFRESH_EVERY = 5.0

# 放号时刻是预约日期当天 12:00。除了"轮询得快"，还要在这个点上**准时有一次确定的筛选**：
# 放号前最后 PRE_RELEASE_WINDOW 秒把探测间隔收窄到 PRE_RELEASE_INTERVAL，
# 跨过 12:00 的第一轮会被标成「定点筛选」并立即用最新数据重新匹配一遍。
PRE_RELEASE_WINDOW = 5.0
PRE_RELEASE_INTERVAL = 0.05


class BookingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("江苏大学羽毛球自动预约场地助手")
        self.root.geometry("1180x840")
        self.root.minsize(900, 700)
        self.root.configure(background="#f3f6fb")
        style = ttk.Style()
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#f3f6fb", foreground="#172033")
        style.configure("Page.TFrame", background="#f3f6fb")
        style.configure("Header.TFrame", background="#102a43")
        style.configure("HeaderTitle.TLabel", background="#102a43", foreground="#ffffff",
                        font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("HeaderSub.TLabel", background="#102a43", foreground="#b9d6ee",
                        font=("Microsoft YaHei UI", 9))
        style.configure("HeaderStatus.TLabel", background="#163d5c", foreground="#7ee2b8",
                        font=("Microsoft YaHei UI", 9, "bold"), padding=(12, 7))
        style.configure("Meta.TLabel", background="#f3f6fb", foreground="#6b7f92",
                        font=("Microsoft YaHei UI", 9))
        style.configure("Pill.TLabel", background="#e9f7f1", foreground="#18794e",
                        font=("Microsoft YaHei UI", 8, "bold"), padding=(8, 4))
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#dce5ef",
                        lightcolor="#dce5ef", darkcolor="#dce5ef", relief="solid", borderwidth=1, padding=14)
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#1f4e79",
                        font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Subcard.TLabelframe", background="#f8fafc", bordercolor="#e3eaf2", padding=8)
        style.configure("Subcard.TLabelframe.Label", background="#f8fafc", foreground="#52677a",
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Card.TLabel", background="#ffffff", foreground="#354a5f")
        style.configure("Field.TLabel", background="#ffffff", foreground="#23384d",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Hint.TLabel", background="#fff7e8", foreground="#9a5b00", padding=(10, 7))
        style.configure("TCheckbutton", background="#ffffff", foreground="#30475e", padding=3)
        style.map("TCheckbutton", background=[("active", "#ffffff")], foreground=[("active", "#0f6cbd")])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor="#cbd8e6", padding=6)
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor="#cbd8e6", padding=6)
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                        foreground="#ffffff", background="#0f6cbd", borderwidth=0, padding=(16, 10))
        style.map("Primary.TButton", background=[("active", "#0b5ca3"), ("disabled", "#9fb6ca")])
        style.configure("Secondary.TButton", foreground="#1f4e79", background="#eaf2f8",
                        bordercolor="#c9dbea", padding=(14, 9))
        style.map("Secondary.TButton", background=[("active", "#dcebf6")])
        self.loop = asyncio.new_event_loop()
        self.browser = self.page = None
        self._slot_cache = {}
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._ui_dashboard()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _sidebar_action(self, item):
        positions = {"总览 Dashboard": 0.0, "预约任务": 0.22, "场地资源": 0.52, "运行日志": 0.78}
        if getattr(self, "_dashboard_canvas", None):
            self._dashboard_canvas.yview_moveto(positions.get(item, 0.0))
        self.write(f"已切换到 {item}")

    def _ui_dashboard(self):
        """8pt-grid dashboard UI mapped from the web design."""
        bg, surface, border = "#e8edf1", "#f9fbfc", "#cbd5dc"
        navy, primary = "#20252b", "#f97316"
        muted, text = "#6b7280", "#111827"
        self.root.configure(bg=bg)

        style = ttk.Style()
        style.configure("Dash.TFrame", background=bg)
        style.configure("Surface.TFrame", background=surface)
        style.configure("Dash.TLabel", background=surface, foreground=text)
        style.configure("Muted.TLabel", background=surface, foreground=muted)
        style.configure("Field.TLabel", background=surface, foreground="#334155",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Dash.TCheckbutton", background=surface, foreground="#334155", padding=4)
        style.map("Dash.TCheckbutton", background=[("active", surface)], foreground=[("active", primary)])
        style.configure("Dash.TCombobox", padding=8, fieldbackground=surface)
        style.configure("Dash.TEntry", padding=8, fieldbackground=surface)
        style.configure("Primary40.TButton", background="#f97316", foreground="#ffffff",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 8), borderwidth=0)
        style.map("Primary40.TButton", background=[("active", "#ea580c")])
        style.configure("Secondary40.TButton", background=surface, foreground="#303630",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 8),
                        bordercolor="#d9e0e7", relief="solid", borderwidth=1)
        style.map("Secondary40.TButton", background=[("active", "#fff7ed")])

        shell = ttk.Frame(self.root, style="Dash.TFrame")
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg="#20252b", width=224)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="NOVA / BOOKING", bg="#20252b", fg="#ffffff",
                 font=("Microsoft YaHei UI", 12, "bold"), padx=24, pady=24).pack(anchor="w")
        tk.Label(sidebar, text="数据管理后台", bg="#20252b", fg="#9ca3af",
                 font=("Microsoft YaHei UI", 9), padx=24).pack(anchor="w", pady=(0, 24))
        for item in ["总览 Dashboard", "预约任务", "场地资源", "运行日志"]:
            active = item == "总览 Dashboard"
            tk.Button(sidebar, text=f"  {item}", anchor="w",
                      bg="#30363d" if active else "#20252b",
                      fg="#ffffff" if active else "#9ca3af",
                      activebackground="#f97316", activeforeground="#ffffff",
                      relief="flat", borderwidth=0, cursor="hand2",
                      font=("Microsoft YaHei UI", 9, "bold" if active else "normal"),
                      padx=16, pady=8,
                      command=lambda name=item: self._sidebar_action(name)).pack(
                          fill="x", padx=12, pady=4)
        tk.Label(sidebar, text="● 服务在线", bg="#20252b", fg="#fb923c",
                 font=("Microsoft YaHei UI", 9), padx=24, pady=24).pack(side="bottom", anchor="w")
        content = ttk.Frame(shell, style="Dash.TFrame")
        content.pack(side="left", fill="both", expand=True)
        canvas = tk.Canvas(content, bg=bg, highlightthickness=0)
        self._dashboard_canvas = canvas
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        page = ttk.Frame(canvas, style="Dash.TFrame", padding=24)
        window = canvas.create_window((0, 0), window=page, anchor="n")

        def resize_page(event):
            width = min(max(event.width - 48, 0), 1200)
            canvas.itemconfigure(window, width=width)
            canvas.coords(window, event.width / 2, 0)

        page.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", resize_page)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        def card(parent):
            outer = tk.Frame(parent, bg=border, padx=1, pady=1)
            inner = tk.Frame(outer, bg=surface, padx=24, pady=24,
                             highlightbackground="#ffffff", highlightthickness=1)
            inner.pack(fill="both", expand=True)
            return outer, inner

        def heading(parent, title, subtitle=""):
            tk.Label(parent, text=title, bg=surface, fg=text,
                     font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
            if subtitle:
                tk.Label(parent, text=subtitle, bg=surface, fg=muted,
                         font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 0))

        header = tk.Frame(page, bg=navy, padx=24, pady=24,
                          highlightbackground="#39483f", highlightthickness=1)
        header.pack(fill="x", pady=(0, 16))
        header_left = tk.Frame(header, bg=navy)
        header_left.pack(side="left", fill="x", expand=True)
        tk.Label(header_left, text="羽毛球场地预约助手", bg=navy, fg="#ffffff",
                 font=("Microsoft YaHei UI", 28, "bold")).pack(anchor="w")
        tk.Label(header_left, text="JIANGSU UNIVERSITY  ·  SMART BOOKING DASHBOARD",
                 bg=navy, fg="#ffffff", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(header, text="●  系统就绪", bg="#65776c", fg="#ffffff",
                 font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=8).pack(side="right", anchor="n")

        stats = ttk.Frame(page, style="Dash.TFrame")
        stats.pack(fill="x", pady=(0, 16))
        for col in range(4):
            stats.columnconfigure(col, weight=1, uniform="stat")
        stat_data = [
            ("预约日期", date.today().strftime("%m-%d"), "默认选择当天"),
            ("开始匹配", "11:59", "预约日期当天启动"),
            ("监控模式", "自动", "多场地优先匹配"),
            ("刷新间隔", "1 秒", "刷新后滚动到底部"),
        ]
        for index, (label, value, detail) in enumerate(stat_data):
            outer, inner = card(stats)
            outer.grid(row=0, column=index, sticky="nsew",
                       padx=(0 if index == 0 else 8, 0 if index == 3 else 8))
            tk.Label(inner, text=label, bg=surface, fg=muted,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            tk.Label(inner, text=value, bg=surface, fg=text,
                     font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w", pady=(8, 4))
            tk.Label(inner, text=detail, bg=surface, fg=muted,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w")

        main = ttk.Frame(page, style="Dash.TFrame")
        main.pack(fill="x", pady=(0, 16))
        main.columnconfigure(0, weight=2)
        main.columnconfigure(1, weight=1)
        booking_outer, booking = card(main)
        booking_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        control_outer, control = card(main)
        control_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        heading(booking, "预约条件", "设置日期、刷新频率和期望时间段。")

        fields = tk.Frame(booking, bg=surface)
        fields.pack(fill="x", pady=(24, 16))
        fields.columnconfigure(0, weight=1)
        fields.columnconfigure(1, weight=1)
        date_group = tk.Frame(fields, bg=surface)
        date_group.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(date_group, text="预约日期", style="Field.TLabel").pack(anchor="w", pady=(0, 8))
        full_dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(90)]
        dates = [full_date[5:] for full_date in full_dates]
        self._date_display_map = dict(zip(dates, full_dates))
        self.date = ttk.Combobox(date_group, values=dates, state="readonly", style="Dash.TCombobox")
        self.date.current(0)
        self.date.pack(fill="x")
        interval_group = tk.Frame(fields, bg=surface)
        interval_group.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(interval_group, text="刷新间隔（秒）", style="Field.TLabel").pack(anchor="w", pady=(0, 8))
        self.interval = ttk.Entry(interval_group, style="Dash.TEntry")
        self.interval.insert(0, "1")
        self.interval.pack(fill="x")

        ttk.Label(booking, text="预约时间段（可多选）", style="Field.TLabel").pack(anchor="w", pady=(0, 8))
        time_options = [f"{h:02d}:00-{h+1:02d}:00" for h in range(14, 21)]
        self.time_vars = {}
        time_box = tk.Frame(booking, bg=surface)
        time_box.pack(fill="x")
        for n, slot in enumerate(time_options):
            time_box.columnconfigure(n % 4, weight=1)
            var = tk.BooleanVar(value=False)
            self.time_vars[slot] = var
            ttk.Checkbutton(time_box, text=slot, variable=var, style="Dash.TCheckbutton").grid(
                row=n // 4, column=n % 4, sticky="w", padx=4, pady=4
            )

        heading(control, "运行控制", "付款和最终提交需要本人确认。")
        safety = tk.Frame(control, bg="#eef3f1", padx=16, pady=16,
                          highlightbackground="#ffffff", highlightthickness=1)
        safety.pack(fill="x", pady=(24, 16))
        tk.Label(safety, text="✓  安全模式已开启", bg="#eef3f1", fg="#4f5e55",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(safety, text="程序只负责匹配和选择场地。", bg="#eef3f1", fg="#65776c",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 0))
        ttk.Button(control, text="打开预约页面", command=self.open_page,
                   style="Secondary40.TButton").pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(control, text="开始智能监控", command=self.start,
                                    style="Primary40.TButton")
        self.start_btn.pack(fill="x", pady=(0, 8))
        ttk.Button(control, text="继续进入付款", command=self.continue_payment,
                   style="Secondary40.TButton").pack(fill="x")

        courts = ttk.Frame(page, style="Dash.TFrame")
        courts.pack(fill="x", pady=(0, 16))
        courts.columnconfigure(0, weight=1)
        courts.columnconfigure(1, weight=1)
        floor1_outer, floor1 = card(courts)
        floor1_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        floor2_outer, floor2 = card(courts)
        floor2_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        heading(floor1, "一楼场地", "共 8 个场地，按选择顺序匹配。")
        heading(floor2, "二楼场地", "共 12 个场地，可设置多个备选。")
        court_options = ["一楼塑胶1", "一楼塑胶2", "一楼塑胶3", "一楼木质4",
                         "一楼塑胶5", "一楼塑胶6", "一楼塑胶7", "一楼木质8"] + [
                            f"二楼塑胶{i}" for i in range(1, 13)
                         ]
        self.court_vars = {}
        floor1_grid = tk.Frame(floor1, bg=surface)
        floor1_grid.pack(fill="x", pady=(24, 0))
        floor2_grid = tk.Frame(floor2, bg=surface)
        floor2_grid.pack(fill="x", pady=(24, 0))
        for n, court_name in enumerate(court_options):
            var = tk.BooleanVar(value=False)
            self.court_vars[court_name] = var
            target = floor1_grid if n < 8 else floor2_grid
            pos = n if n < 8 else n - 8
            target.columnconfigure(pos % 3, weight=1)
            ttk.Checkbutton(target, text=court_name.replace("一楼", "").replace("二楼", ""),
                            variable=var, style="Dash.TCheckbutton").grid(
                                row=pos // 3, column=pos % 3, sticky="w", padx=4, pady=4
                            )

        log_outer, log_card = card(page)
        log_outer.pack(fill="both", expand=True)
        heading(log_card, "实时运行日志", "蓝色操作 · 绿色成功 · 黄色等待 · 红色异常")
        self.log = tk.Text(log_card, height=10, state="disabled", bg="#303a34", fg="#f5f2eb",
                           insertbackground="#ffffff", selectbackground="#65776c", relief="flat",
                           padx=24, pady=24, font=("Cascadia Mono", 9), spacing1=4, spacing3=4)
        self.log.pack(fill="both", expand=True, pady=(24, 0))
        self.log.tag_configure("system", foreground="#d9ddd6")
        self.log.tag_configure("wait", foreground="#c7c2ae")
        self.log.tag_configure("success", foreground="#b8cbbd")
        self.log.tag_configure("error", foreground="#d3b9ad")
        self.log.tag_configure("action", foreground="#c0d0c6")

        tk.Label(page, text="Jiangsu University Smart Booking  ·  验证码、付款和最终提交需本人完成",
                 bg=bg, fg=muted, font=("Microsoft YaHei UI", 9)).pack(pady=(16, 0))

    def _ui(self):
        shell = ttk.Frame(self.root, style="Page.TFrame"); shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0, background="#f3f6fb")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        frm = ttk.Frame(canvas, padding=22, style="Page.TFrame")
        canvas_window = canvas.create_window((0, 0), window=frm, anchor="nw")
        frm.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        header = ttk.Frame(frm, style="Header.TFrame", padding=(22, 18))
        header.pack(fill="x", pady=(0, 14))
        header_text = ttk.Frame(header, style="Header.TFrame")
        header_text.pack(side="left", fill="x", expand=True)
        ttk.Label(header_text, text="羽毛球场地预约助手", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(header_text, text="JIANGSU UNIVERSITY  ·  SMART BOOKING CONSOLE",
                  style="HeaderSub.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(header, text="●  系统就绪", style="HeaderStatus.TLabel").pack(side="right", anchor="n")
        login = ttk.LabelFrame(frm, text="01  登录预约系统", style="Card.TLabelframe")
        login.pack(fill="x", pady=(0, 12))
        ttk.Label(login, text="打开预约页面并完成登录，浏览器会安全保留本地登录状态。",
                  style="Card.TLabel").pack(side="left")
        ttk.Button(login, text="打开预约页面  →", command=self.open_page,
                   style="Primary.TButton").pack(side="right")
        ttk.Label(frm, text="  每天 11:59 开始匹配场地；提前启动后，程序会自动等待开始时间。",
                  style="Hint.TLabel").pack(fill="x", pady=(0, 12))
        overview = ttk.Frame(frm, style="Page.TFrame")
        overview.pack(fill="x", pady=(0, 12))
        ttk.Label(overview, text="自动监控  ·  多时段匹配  ·  场地优先级  ·  本地登录状态",
                  style="Meta.TLabel").pack(side="left")
        ttk.Label(overview, text="安全模式：付款需本人确认", style="Pill.TLabel").pack(side="right")
        booking = ttk.LabelFrame(frm, text="02  设置预约条件", style="Card.TLabelframe")
        booking.pack(fill="x", pady=(0, 12))
        row = ttk.Frame(booking, style="Card.TFrame"); row.pack(fill="x", pady=6)
        ttk.Label(row, text="预约日期", width=14, style="Field.TLabel").pack(side="left")
        dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(90)]
        self.date = ttk.Combobox(row, values=dates, state="readonly", width=16)
        self.date.current(0); self.date.pack(side="left", padx=8)
        ttk.Label(row, text="默认今日", style="Pill.TLabel").pack(side="left", padx=(2, 0))
        row = ttk.Frame(booking, style="Card.TFrame"); row.pack(fill="x", pady=6)
        ttk.Label(row, text="时间段（可多选）", width=14, style="Field.TLabel").pack(side="left", anchor="n")
        time_options = [f"{h:02d}:00-{h+1:02d}:00" for h in range(14, 21)]
        self.time_vars = {}
        time_box = ttk.LabelFrame(row, text="选择一个或多个预约时段", style="Subcard.TLabelframe")
        time_box.pack(side="left", padx=8, fill="x", expand=True)
        for n, x in enumerate(time_options):
            var = tk.BooleanVar(value=False); self.time_vars[x] = var
            ttk.Checkbutton(time_box, text=x, variable=var).grid(row=n // 4, column=n % 4, sticky="w", padx=3)
        row = ttk.Frame(booking, style="Card.TFrame"); row.pack(fill="x", pady=6)
        ttk.Label(row, text="场地优先级", width=14, style="Field.TLabel").pack(side="left", anchor="n")
        court_options = ["一楼塑胶1", "一楼塑胶2", "一楼塑胶3", "一楼木质4", "一楼塑胶5", "一楼塑胶6", "一楼塑胶7", "一楼木质8"] + [f"二楼塑胶{i}" for i in range(1, 13)]
        self.court_vars = {}
        court_box = ttk.Frame(row, style="Card.TFrame"); court_box.pack(side="left", padx=8, fill="x", expand=True)
        floor1 = ttk.LabelFrame(court_box, text="一楼 · 8 个场地", style="Subcard.TLabelframe")
        floor1.pack(fill="x", pady=(0, 7))
        floor2 = ttk.LabelFrame(court_box, text="二楼 · 12 个场地", style="Subcard.TLabelframe")
        floor2.pack(fill="x")
        for n, x in enumerate(court_options):
            var = tk.BooleanVar(value=False); self.court_vars[x] = var
            parent = floor1 if n < 8 else floor2
            j = n if n < 8 else n - 8
            ttk.Checkbutton(parent, text=x, variable=var).grid(row=j // 3, column=j % 3, sticky="w", padx=3)
        row = ttk.Frame(booking, style="Card.TFrame"); row.pack(fill="x", pady=6)
        ttk.Label(row, text="刷新间隔（秒）", width=14, style="Field.TLabel").pack(side="left")
        self.interval = ttk.Entry(row, width=8); self.interval.insert(0, "1"); self.interval.pack(side="left", padx=8)
        self.start_btn = ttk.Button(frm, text="开始智能监控并自动选择场地", command=self.start,
                                    style="Primary.TButton")
        self.start_btn.pack(fill="x", pady=(2, 9))
        ttk.Button(frm, text="已手动选好场地，继续进入付款", command=self.continue_payment,
                   style="Secondary.TButton").pack(fill="x", pady=(0, 12))
        log_frame = ttk.LabelFrame(frm, text="03  实时运行日志", style="Card.TLabelframe")
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="状态颜色：蓝色 操作  ·  绿色 成功  ·  黄色 等待  ·  红色 异常",
                  style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.log = tk.Text(log_frame, height=11, state="disabled", bg="#0f1f2e", fg="#d7e5f0",
                           insertbackground="#ffffff", selectbackground="#234e70", relief="flat",
                           padx=12, pady=10, font=("Cascadia Mono", 9), spacing1=2, spacing3=2)
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("system", foreground="#a9bfd1")
        self.log.tag_configure("wait", foreground="#f6c85f")
        self.log.tag_configure("success", foreground="#63d297")
        self.log.tag_configure("error", foreground="#ff7b7b")
        self.log.tag_configure("action", foreground="#68b5f8")
        ttk.Label(frm, text="Jiangsu University Smart Booking  ·  本工具不会自动完成付款",
                  style="Meta.TLabel").pack(anchor="center", pady=(12, 2))

    def write(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        if any(k in msg for k in ["异常", "失败", "错误"]): tag = "error"
        elif any(k in msg for k in ["已选择", "已到", "成功", "进入"]): tag = "success"
        elif any(k in msg for k in ["等待", "未找到"]): tag = "wait"
        elif any(k in msg for k in ["开始", "点击", "尝试", "刷新"]): tag = "action"
        else: tag = "system"
        def append():
            self.log.configure(state="normal")
            self.log.insert("end", f"[{stamp}] {msg}\n", tag)
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, append)

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def open_page(self):
        self.submit(self._open())

    async def _scroll_to_page_bottom(self, attempts=6):
        """等待异步内容渲染，并将窗口及页面主滚动容器移到底部。"""
        for _ in range(attempts):
            await self.page.evaluate("""() => {
              window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'auto'});
              for (const el of document.querySelectorAll('*')) {
                if (el.scrollHeight > el.clientHeight + 20) {
                  el.scrollTop = el.scrollHeight;
                }
              }
            }""")
            await self.page.wait_for_timeout(250)

    async def _open(self):
        if not self.browser:
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch_persistent_context(str(PROFILE), headless=False)
            self.page = await self.browser.new_page()
        # 先打开域名首页，再设置 hash 路由；部分 SPA 对直接 goto 带 hash 的地址处理不完整。
        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await self.page.evaluate("route => { window.location.hash = route; }", ROUTE)
        await self.page.wait_for_timeout(1200)
        await self._scroll_to_page_bottom()
        self.write("页面已打开，请在浏览器中完成登录；登录状态会保存在 browser_profile。")

    # =====================================================================
    # 一、接口通道
    # 在页面上下文里发 fetch，天然复用浏览器登录态（cookie + App-Token），
    # 不依赖 DOM，速度是"扫描整页文本"的几十倍，也不会被页面重绘干扰。
    # =====================================================================
    async def _api(self, path, params=None, data=None, method="GET", timeout=8000):
        """发一次接口请求。任何情况下都返回 dict，从不抛异常。"""
        if not self.page:
            return {"status": -1, "error": "页面尚未打开", "ms": 0, "token": False}
        js = r"""
        async ({path, params, data, method, timeout, tokenKey, scheme}) => {
          // uni-app 的存储封装有两种形态：字符串原样存，对象才包成 {type:"...", data:...}。
          // 所以 JSON.parse 失败时绝不能返回空串 —— token 本身往往就是一段裸串
          // （JWT 之类），旧写法正是这里把已登录判成了未登录。
          const unwrap = (raw) => {
            if (raw === null || raw === undefined) return '';
            let value = raw;
            try {
              const parsed = JSON.parse(raw);
              if (typeof parsed === 'string') value = parsed;
              else if (parsed && typeof parsed === 'object') {
                if ('data' in parsed) value = parsed.data;
                else if ('value' in parsed) value = parsed.value;
              }
            } catch (e) { value = raw; }
            if (value && typeof value === 'object' && 'data' in value) value = value.data;
            if (value === null || value === undefined) return '';
            return (typeof value === 'string' ? value : String(value))
              .replace(/^"|"$/g, '').trim();
          };
          const readAt = (key) => { try { return unwrap(localStorage.getItem(key)); } catch (e) { return ''; } };
          let names = [];
          try { names = Object.keys(localStorage); } catch (e) { names = []; }
          let token = readAt(tokenKey), usedKey = token ? tokenKey : '';
          if (!token) {
            // 兜底：万一站点换了键名，扫一遍名字里带 token/auth 的键。
            for (const key of names) {
              if (key === tokenKey || !/token|auth|ticket/i.test(key)) continue;
              const value = readAt(key);
              if (value) { token = value; usedKey = key; break; }
            }
          }
          const qs = params ? new URLSearchParams(params).toString() : '';
          const url = location.origin + path + (qs ? '?' + qs : '');
          const ctl = new AbortController();
          const timer = setTimeout(() => ctl.abort(), timeout);
          const t0 = performance.now();
          const meta = {token: !!token, tokenKey: usedKey, storageKeys: names.slice(0, 24)};
          try {
            const headers = {'Content-Type': 'application/json'};
            if (token) { headers['Authorization'] = scheme + ' ' + token; }
            const r = await fetch(url, {
              method, headers, credentials: 'include', signal: ctl.signal,
              body: data ? JSON.stringify(data) : undefined
            });
            const text = await r.text();
            let body = null, raw = '';
            try { body = JSON.parse(text); } catch (e) { raw = text.slice(0, 300); }
            return Object.assign({status: r.status, body, raw,
                                  ms: Math.round(performance.now() - t0)}, meta);
          } catch (e) {
            return Object.assign({status: -1, error: String(e),
                                  ms: Math.round(performance.now() - t0)}, meta);
          } finally { clearTimeout(timer); }
        }
        """
        try:
            return await self.page.evaluate(js, {
                "path": path, "params": params or {}, "data": data, "method": method,
                "timeout": timeout, "tokenKey": TOKEN_KEY, "scheme": AUTH_SCHEME,
            })
        except Exception as exc:
            return {"status": -1, "error": str(exc), "ms": 0, "token": False}

    @staticmethod
    def _envelope(res):
        """统一拆信封 -> (ok, code, detail, note)。

        注意：这里**不**因为"没读到 localStorage 令牌"就直接判未登录。请求照样发出去，
        以服务器的回答为准 —— 站点可能改用 cookie 会话，也可能换了令牌键名。
        """
        if not isinstance(res, dict):
            return False, -1, None, "无响应"
        if res.get("status") == -1:
            return False, -1, None, "请求异常：%s" % str(res.get("error"))[:70]
        status = res.get("status")
        body = res.get("body")
        if not isinstance(body, dict):
            return False, -1, None, "响应不是 JSON：%s" % str(res.get("raw"))[:70]
        code = body.get("code")
        detail = body.get("detail")
        if status == 200 and code in (200, None):
            return True, 200, detail, ""
        # 站点把具体原因放在嵌套的 detail.detail 里（例如「身份认证信息未提供。」），
        # 外层 message 往往只是笼统的「失败」，所以嵌套原因优先。
        nested = detail.get("detail") if isinstance(detail, dict) else None
        message = nested or body.get("message") or body.get("msg") or ""
        if code == 401 or status == 401:
            if res.get("token"):
                return False, 401, detail, "登录状态无效或已过期：%s" % (
                    str(message)[:70] or "请重新登录")
            hint = "浏览器 localStorage 里没有可用的登录令牌"
            seen = res.get("storageKeys") or []
            if seen:
                hint += "（现有键：%s）" % "、".join(str(k) for k in seen[:8])
            return False, 401, detail, "尚未登录：%s；服务器也拒绝了请求（%s）" % (
                hint, str(message)[:40] or "401")
        return (False, (code if code is not None else status), detail,
                "接口返回 %s：%s" % (code, str(message)[:70]))

    async def _api_list(self):
        return await self._api(API_LIST, params={"item": API_ITEM_ID})

    async def _api_detail(self, schedule_id):
        return await self._api(API_DETAIL, params={"item": API_ITEM_ID, "schedule": schedule_id})

    async def _api_submit(self, session_ids):
        return await self._api(API_SUBMIT, data={"sessionIds": list(session_ids)},
                               method="POST", timeout=12000)

    async def _verify_login(self):
        """开抢前的登录自检 -> (可用?, 说明)。

        请求失败（网络异常 / 页面没开）不算"未登录"，只提示但继续，免得误拦。
        """
        res = await self._api_list()
        ok, code, detail, note = self._envelope(res)
        if ok:
            key = res.get("tokenKey")
            if key:
                return True, "登录令牌来自 localStorage['%s']" % key
            return True, "浏览器用 cookie 会话认证（localStorage 里没有令牌）"
        if code == 401:
            return False, note
        return True, "登录自检未得到确认（%s），仍继续。" % note

    @staticmethod
    def _opening_for(date_text, now=None):
        """开抢时刻 = 预约日期当天的 11:59。

        系统在预约日期当天 12:00 放号，所以提前一分钟进入筛选状态，
        这样 12:00 那一瞬间已经在打接口，而不是刚启动。
        """
        now = now or datetime.now()
        try:
            day = datetime.strptime(str(date_text), "%Y-%m-%d")
        except (TypeError, ValueError):
            day = now
        return day.replace(hour=11, minute=59, second=0, microsecond=0)

    @staticmethod
    def _release_for(date_text, now=None):
        """放号时刻 = 预约日期当天的 12:00:00（比开抢时刻 _opening_for 晚一分钟）。"""
        now = now or datetime.now()
        try:
            day = datetime.strptime(str(date_text), "%Y-%m-%d")
        except (TypeError, ValueError):
            day = now
        return day.replace(hour=12, minute=0, second=0, microsecond=0)

    @classmethod
    def _parse_date_parts(cls, text):
        """从任意日期串里抽出 (年|None, 月, 日)。「09月21日」「2026-09-21」「9/21」都能吃。"""
        nums = re.findall(r"\d+", str(text or ""))
        if len(nums) >= 3 and len(nums[0]) == 4:
            return int(nums[0]), int(nums[1]), int(nums[2])
        if len(nums) >= 2:
            return None, int(nums[-2]), int(nums[-1])
        return None

    @classmethod
    def _date_matches(cls, server_text, want_text):
        """站点返回的日期串与界面选的日期是不是同一天。

        站点给的是「09月21日」这种中文格式，界面用的是「2026-09-21」，
        直接拿字符串比对永远不相等 —— 2026-09-21 那次就是这样空等到超时的。
        站点串通常不带年份，此时只比月日（可约列表只覆盖最近几天，不会跨年）。
        """
        a = str(server_text or "").strip()
        b = str(want_text or "").strip()
        if not a or not b:
            return False
        if a == b:
            return True
        pa, pb = cls._parse_date_parts(a), cls._parse_date_parts(b)
        if not pa or not pb:
            return False
        ya, ma, da = pa
        yb, mb, db = pb
        if ma != mb or da != db:
            return False
        return (ya == yb) if (ya and yb) else True

    async def _tick_dom_refresh(self, state, every=DOM_REFRESH_EVERY):
        """按节拍触发一次页面轻量刷新。state 是调用方持有的 {"last": float}。

        点的是站点自带的刷新图标（只重拉 list+detail，不是整页 reload），
        所以既不会丢登录态，也不会拖慢接口轮询。
        """
        if every <= 0 or self.page is None:
            return False
        now = time.monotonic()
        if now - state.get("last", 0.0) < every:
            return False
        state["last"] = now
        try:
            res = await self._dom_trigger_refresh()
        except Exception as exc:
            if not state.get("warned"):
                state["warned"] = True
                self.write("提示：页面刷新没成功（%s），改为纯接口轮询，不影响抢场地。"
                           % str(exc)[:60])
            return False
        if isinstance(res, dict) and not res.get("ok") and not state.get("warned"):
            state["warned"] = True
            self.write("提示：页面上没找到刷新图标（%s），改为纯接口轮询，不影响抢场地。"
                       % str(res.get("why") or "未知原因")[:60])
        return bool(isinstance(res, dict) and res.get("ok"))

    async def _sleep_until(self, moment, report_every=300.0):
        """精确睡到 moment。长等待期间定期汇报剩余时间，最后 60 秒收窄到毫秒级。"""
        announced = False
        while True:
            remain = (moment - datetime.now()).total_seconds()
            if remain <= 0:
                return
            if remain <= 60:
                if not announced:
                    announced = True
                    self.write("进入最后 1 分钟，准备开始筛选场地。")
                await asyncio.sleep(min(0.05, remain))
                continue
            announced = False
            await asyncio.sleep(min(report_every, remain - 60))
            left = (moment - datetime.now()).total_seconds()
            if left > 60:
                self.write("等待放号中：剩余 %d 小时 %d 分 %d 秒。" % (
                    int(left // 3600), int(left % 3600 // 60), int(left % 60)))

    # =====================================================================
    # 二、数据归一化与可用性判定
    # 关键：raw "1@@1号场地" 原样保留，只在读取时 split；
    #       任何下游都不许拿显示名反推 code，否则键空间错位、全部查不到。
    # =====================================================================
    @staticmethod
    def _split_label(raw):
        text = "" if raw is None else str(raw)
        if "@@" in text:
            code, _, name = text.partition("@@")
            return code.strip(), name.strip()
        return text.strip(), text.strip()

    @classmethod
    def normalize_detail(cls, detail):
        detail = detail if isinstance(detail, dict) else {}
        areas = []
        for raw in (detail.get("areas") or []):
            code, name = cls._split_label(raw)
            areas.append({"raw": raw, "code": code, "name": name})
        times = [str(t) for t in (detail.get("times") or [])]
        session_obj = detail.get("sessionObj") or {}
        cells = {}
        for area in areas:
            for label in times:
                cell = session_obj.get("%s@@%s" % (label, area["code"]))
                if cell is None:
                    cell = session_obj.get("%s@@%s" % (label, area["raw"]))
                if isinstance(cell, dict):
                    cells[(label, area["code"])] = cell
        try:
            max_n = int(detail.get("maxSubscribeNum") or 0)
        except (TypeError, ValueError):
            max_n = 0
        return {"areas": areas, "times": times, "cells": cells, "max_n": max_n}

    @staticmethod
    def cell_state(cell, now):
        """(可用?, 说明)：完全对应站点前端的两条官方规则。"""
        if not isinstance(cell, dict) or not cell.get("id"):
            return False, "无场次"
        expire = "%s %s" % (str(cell.get("session_date") or "").strip(),
                            str(cell.get("disabled_time") or "").strip())
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                if datetime.strptime(expire.strip(), fmt) <= now:
                    return False, "已过期"
                break
            except ValueError:
                continue
        try:
            left = int(cell.get("ticket_num") or 0) - int(cell.get("subscribed_num") or 0)
        except (TypeError, ValueError):
            left = 0
        if left <= 0:
            return False, "已订满"
        return True, "剩 %d 张" % left

    @classmethod
    def _floor_key(cls, text):
        """取名字里的楼层标识（'一楼'/'二楼'…），取不到返回空串。"""
        text = str(text or "")
        for token in FLOOR_TOKENS:
            if token in text:
                return token
        return ""

    @classmethod
    def _strip_floor(cls, text):
        """去掉开头的楼层前缀：'一楼塑胶1' -> '塑胶1'。"""
        text = str(text or "")
        for prefix in FLOOR_TOKENS:
            if text.startswith(prefix):
                return text[len(prefix):]
        return text

    @classmethod
    def _best_match(cls, candidates, wanted):
        """界面文字 -> 接口真实名字：精确 > 楼层内去前缀 > 楼层内包含 > 序号。

        楼层感知很关键：候选池里同时有一楼和二楼的场地时，「二楼塑胶5」必须
        只在二楼那批里找，否则会落到一楼的第 5 片上去。
        """
        if not candidates or not wanted:
            return None
        want = str(wanted).strip()
        for cand in candidates:
            if cand == want:
                return cand

        floor = cls._floor_key(want)
        scoped = [c for c in candidates if cls._floor_key(c) == floor] if floor else []
        short = cls._strip_floor(want)
        for pool in (scoped, candidates):
            if not pool:
                continue
            for cand in pool:
                if cls._strip_floor(cand) == short:
                    return cand
            for cand in pool:
                if short and (short in cand or cand in short):
                    return cand
        number = re.search(r"(\d+)", short)
        if number:
            index = int(number.group(1)) - 1
            if 0 <= index < len(scoped or candidates):
                return (scoped or candidates)[index]
        return None

    # =====================================================================
    # 三、DOM 精确定位（兜底通道）
    # 表格结构：每行 .data-column-row 依此对应 times，
    #           行内 .data-column-col 依此对应 areas，
    # 于是可以用下标直接命中单元格，不再用"行标签中心 × 列标签中心"的几何猜法。
    # =====================================================================
    async def _dom_cell_class(self, row_index, col_index):
        js = r"""
        ({row, col}) => {
          const rows = document.querySelectorAll('.data-column-row');
          const rowEl = rows[row];
          if (!rowEl) return '';
          const cell = rowEl.querySelectorAll('.data-column-col')[col];
          return cell ? String(cell.className || '') : '';
        }
        """
        try:
            return await self.page.evaluate(js, {"row": row_index, "col": col_index})
        except Exception:
            return ""

    async def _dom_click_cell(self, row_index, col_index):
        """只点一次（单元格是 toggle 语义，多点会取消选择）。"""
        js = r"""
        ({row, col}) => {
          const rows = document.querySelectorAll('.data-column-row');
          const rowEl = rows[row];
          if (!rowEl) return {ok: false, why: 'no-row', rows: rows.length};
          const cols = rowEl.querySelectorAll('.data-column-col');
          const cell = cols[col];
          if (!cell) return {ok: false, why: 'no-col', cols: cols.length};
          const cls = String(cell.className || '');
          if (/inner-select/.test(cls)) return {ok: true, already: true, cls: cls};
          if (/inner-disabled/.test(cls)) {
            return {ok: false, why: 'disabled', cls: cls,
                    text: (cell.innerText || '').replace(/\s+/g, '')};
          }
          try { cell.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
          cell.click();
          return {ok: true, clicked: true, cls: cls, text: (cell.innerText || '').replace(/\s+/g, '')};
        }
        """
        try:
            return await self.page.evaluate(js, {"row": row_index, "col": col_index})
        except Exception as exc:
            return {"ok": False, "why": str(exc)}

    async def _dom_select_schedule(self, schedule_id):
        """把左侧场次切到目标 schedule（刷新后页面会回到第一个）。"""
        js = r"""
        (sid) => {
          const el = document.getElementById('id-' + sid);
          if (!el) return {ok: false, why: 'no-item'};
          if (/\bselect\b/.test(String(el.className || ''))) return {ok: true, already: true};
          el.click();
          return {ok: true, clicked: true};
        }
        """
        try:
            return await self.page.evaluate(js, str(schedule_id))
        except Exception as exc:
            return {"ok": False, "why": str(exc)}

    async def _dom_trigger_refresh(self):
        """点站点自带的刷新图标：只重拉 list+detail，比整页 reload 快一个数量级。"""
        js = r"""
        () => {
          const icon = document.querySelector('.icon-refresh');
          if (!icon) return {ok: false, why: 'no-refresh-icon'};
          const target = icon.closest('uni-text') || icon.parentElement || icon;
          target.click();
          return {ok: true, tag: target.tagName};
        }
        """
        try:
            return await self.page.evaluate(js)
        except Exception as exc:
            return {"ok": False, "why": str(exc)}

    def start(self):
        if not self.page:
            messagebox.showwarning("提示", "请先打开预约页面并登录")
            return
        times = [x for x, var in self.time_vars.items() if var.get()]
        courts = [x for x, var in self.court_vars.items() if var.get()]
        if not times or not courts:
            messagebox.showwarning("提示", "请至少选择一个时间段和一个场地")
            return
        self.write("已点击开始监控，正在准备预约页面……")
        self.start_btn.configure(state="disabled", text="监控进行中…")
        display_date = self.date.get().strip()
        selected_date = getattr(self, "_date_display_map", {}).get(display_date, display_date)
        future = self.submit(self._monitor(selected_date, times, courts, float(self.interval.get() or 3)))
        future.add_done_callback(self._monitor_done)

    def continue_payment(self):
        if not self.page:
            messagebox.showwarning("提示", "请先打开预约页面并登录")
            return
        self.write("正在尝试从当前浏览器页面进入付款流程……")
        future = self.submit(self._go_payment_page())
        future.add_done_callback(self._monitor_done)

    def _monitor_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self.write(f"监控异常：{exc}")
            self.root.after(0, lambda: self.start_btn.configure(state="normal", text="开始监控并自动选择"))

    async def _monitor(self, date, times, courts, interval):
        """接口优先的抢场循环：高频读接口（毫秒级），命中后才动页面或直接下单。"""
        interval = max(0.2, float(interval or 0.4))
        fast_mode = bool(getattr(self, "fast_submit", True))
        self.write(f"开始监控：{date} / 时段 {times} / 场地 {courts}")
        self.write(
            f"探测间隔 {interval}s，命中后"
            + ("直接下单（极速模式）" if fast_mode else "在页面上点击选中（界面模式）")
        )

        now = datetime.now()
        # 抢场时序：预约日期当天 12:00 放号 -> 当天 11:59（提前一分钟）开始筛选场地，
        # 这样 12:00 放号的那一瞬间已经在打接口，而不是刚启动。
        opening = self._opening_for(date, now)
        target_day = opening
        if now < opening:
            wait_seconds = (opening - now).total_seconds()
            self.write(
                f"{target_day:%Y-%m-%d} 12:00 放号，将在当天 11:59 开始筛选场地；"
                f"现在 {now:%H:%M:%S}，还需等待 "
                f"{int(wait_seconds // 3600)} 小时 {int(wait_seconds % 3600 // 60)} 分。")
            # 先确认登录态再进长等待，免得干等几个小时才发现根本没登录。
            ready, why = await self._verify_login()
            if not ready:
                self.write("登录自检失败：" + why)
                self.write("已取消本次监控。请先在浏览器里完成登录，再重新点开始监控。")
                return
            self.write("登录自检通过：" + why)
            await self._sleep_until(opening)
            self.write(f"已到 {opening:%H:%M:%S}，开始筛选可用场地。")

        # ---------- 1. 同步元数据：真实场次 / 场地 / 时段 / 可约上限 ----------
        # 11:59 开始筛选时，目标日期往往还没出现在可约场次里（12:00 才放号），
        # 所以这里要重试等待它出现，而不是换一个日期。
        retry_deadline = opening + timedelta(minutes=15)
        wait_logged = False
        last_report = 0.0
        dom_state = {"last": 0.0}
        wait_round = 0
        while True:
            wait_round += 1
            # 从 11:59 起就开始刷新预约界面，放号前的每一轮都不闲着
            await self._tick_dom_refresh(dom_state)
            schedule_id, meta = await self._sync_metadata(date, quiet=wait_logged)
            if meta:
                break
            if datetime.now() >= retry_deadline:
                self.write(f"等待结束：{date} 没有出现在可约场次里，已停止。")
                return
            if not wait_logged:
                wait_logged = True
                last_report = time.monotonic()
                self.write(
                    f"{date} 还没出现在可约场次里；每 {max(0.5, interval):.1f}s 重试一次，"
                    "它一出现就开始筛选场地（最多等 15 分钟）。")
            elif time.monotonic() - last_report >= 5:
                last_report = time.monotonic()
                left = (retry_deadline - datetime.now()).total_seconds()
                self.write("第 %d 轮：仍在刷新等待 %s 放号（还能等 %d 分 %d 秒）。"
                           % (wait_round, date, int(left // 60), int(left % 60)))
            await asyncio.sleep(max(0.5, interval))
        areas = meta["areas"]

        # ---------- 2. 界面选择 -> 备选池 + 单次提交上限 ----------
        candidates, notes, limit = self._build_targets(meta, times, courts)
        for note in notes:
            self.write(note)
        if not candidates:
            self.write("没有可用的目标组合，已停止。请在界面上勾选要抢的时段和场地。")
            if areas:
                self.write("接口实际返回的场地：" + "、".join(a["name"] for a in areas))
            return
        self.write("备选池共 %d 个单元（%s）。系统单次最多选 %d 个，开抢时按上面的顺序"
                   "取前 %d 个可约的提交，其余顺延备用。" % (
                       len(candidates), "、".join("%s %s" % c for c in candidates),
                       MAX_PER_ORDER, min(limit, len(candidates))))

        # ---------- 3. 主循环：只读接口，凑够单次上限就动手 ----------
        # 光"轮询快"还不够：12:00 这个点上必须准时有一次确定的筛选，
        # 所以放号前最后几秒把间隔收窄，跨过 12:00 的第一轮会被标成定点筛选。
        release = self._release_for(date, now)
        release_pending = now < release
        if release_pending:
            self.write("放号时刻 %s：到点会立即做一次定点筛选"
                       "（最后 %d 秒把探测间隔收窄到 %.2f 秒）。"
                       % (release.strftime("%H:%M:%S"),
                          int(PRE_RELEASE_WINDOW), PRE_RELEASE_INTERVAL))

        def nap():
            if release_pending:
                left = (release - datetime.now()).total_seconds()
                if left <= PRE_RELEASE_WINDOW:
                    return max(0.02, min(interval, PRE_RELEASE_INTERVAL))
            return interval

        poll_no = 0
        fail_streak = 0
        last_available = -1
        grace_until = None
        grace_logged = False
        t_begin = time.monotonic()
        while True:
            poll_no += 1
            # 界面跟着一起刷，保证页面上看到的就是这一轮匹配用的数据
            await self._tick_dom_refresh(dom_state)
            # 跨过放号时刻的第一轮 = 12:00 定点筛选
            hit_release = False
            if release_pending and datetime.now() >= release:
                release_pending = False
                hit_release = True
                self.write("已到 %s 放号时刻，执行定点筛选。"
                           % release.strftime("%H:%M:%S"))
            try:
                res = await asyncio.wait_for(self._api_detail(schedule_id), timeout=10)
            except asyncio.TimeoutError:
                res = {"status": -1, "error": "探测超时", "token": True}
            ok, code, detail, note = self._envelope(res)
            if not ok:
                fail_streak += 1
                if code == 401:
                    self.write("登录状态已失效（接口返回 401）。请重新登录后再点开始监控。")
                    return
                if fail_streak <= 3 or fail_streak % 20 == 0:
                    self.write(f"探测失败 {fail_streak} 次：{note}")
                if fail_streak >= 60:
                    self.write("连续探测失败过多，已停止。请确认预约页面仍然开着。")
                    return
                await asyncio.sleep(nap())
                continue
            fail_streak = 0

            live = self.normalize_detail(detail)
            now_dt = datetime.now()
            # 按备选池顺序挑：池子头部的先抢，抢不到自动顺延到后面的备选。
            ready, available, blocked, quota = self._pick_targets(
                candidates, live["cells"], now_dt, limit)

            # 定点轮一定要打印，哪怕可约数没变 —— 12:00 这一下必须在日志里看得见
            if len(available) != last_available or poll_no % 25 == 1 or hit_release:
                last_available = len(available)
                detail_text = ""
                if available:
                    detail_text += "（" + "、".join(
                        "%s %s" % k for k, _sid in available) + "）"
                if blocked:
                    detail_text += "；未就绪：" + "、".join(
                        "%s %s %s" % (k[0], k[1], w) for k, w in blocked[:3])
                self.write("%s第 %d 轮：可约 %d/%d（本次要凑 %d 个）%s" % (
                    "【12:00 定点筛选】" if hit_release else "",
                    poll_no, len(available), len(candidates), quota, detail_text))

            if not available:
                grace_until, grace_logged = None, False
                await asyncio.sleep(nap())
                continue

            used = time.monotonic() - t_begin
            if len(available) >= quota:
                # 凑够单次上限（或池子本身就不够上限、已全部可约）：立刻锁定
                if await self._lock_targets(
                        schedule_id, live, ready,
                        "第 %d 轮已凑齐 %d 个可约单元（累计耗时 %.1fs，本轮接口 %sms 返回），开始锁定。"
                        % (poll_no, len(ready), used, res.get("ms", "?"))):
                    return
                grace_until = time.monotonic() + max(interval, 1.0)
                await asyncio.sleep(nap())
                continue

            # 有可约的但还没凑满上限：给一个很短的窗口，等同一批放号的其它场地出现。
            # 窗口一到，哪怕只有 1 个也照抢 —— 到手的绝不为了凑满而丢掉。
            if grace_until is None:
                grace_until = time.monotonic() + FILL_GRACE_SECONDS
            remain = grace_until - time.monotonic()
            if remain > 0:
                if not grace_logged:
                    grace_logged = True
                    self.write("已发现 %d 个可约（不足 %d 个），再等 %.1f 秒看同一批是否补齐；"
                               "到点仍不足就按现有数量直接提交。"
                               % (len(available), quota, remain))
                await asyncio.sleep(min(interval, remain))
                continue
            if await self._lock_targets(
                    schedule_id, live, ready,
                    "只抢到 %d 个（不足 %d 个），按「有几个抢先几个」立即提交。"
                    % (len(ready), quota)):
                return
            grace_until = time.monotonic() + max(interval, 1.0)
            await asyncio.sleep(interval)

    @classmethod
    def _venue_order(cls):
        """界面场地清单的声明顺序 -> [(全名, 楼层内序号, 全局序号)]。"""
        out, global_index = [], 0
        for _, _, names in VENUE_GROUPS:
            for local_index, name in enumerate(names):
                out.append((name, local_index, global_index))
                global_index += 1
        return out

    @classmethod
    def _resolve_area(cls, areas, order, court_label):
        """界面勾选的场地 -> 接口返回的场地项。返回 (area | None, 说明)。"""
        want = str(court_label).strip()
        if not areas:
            return None, "接口没有返回任何场地"

        # 1. 名字完全一致：接口也用这套名字时最省事
        for area in areas:
            if area["name"] == want:
                return area, ""

        floor = cls._floor_key(want)
        scoped = [a for a in areas if cls._floor_key(a["name"]) == floor] if floor else []
        short = cls._strip_floor(want)

        # 2. 名字匹配：先在同楼层里找，再退回整张表
        for pool in (scoped, areas):
            if not pool:
                continue
            for area in pool:
                if cls._strip_floor(area["name"]) == short:
                    return area, ""
            for area in pool:
                if short and (short in area["name"] or area["name"] in short):
                    return area, ""

        # 3. 序号兜底：用界面清单的顺序，不用名字里的数字
        #    （「二楼塑胶5」是整表第 13 片，不是第 5 片）
        info = next((x for x in order if x[0] == want), None)
        if info is None:
            return None, "不在界面场地清单里"
        _, local_index, global_index = info
        if scoped and 0 <= local_index < len(scoped):
            return scoped[local_index], "按%s第 %d 片推定" % (floor, local_index + 1)
        if global_index < len(areas):
            return areas[global_index], "按场地清单第 %d 片推定" % (global_index + 1)
        return None, "在接口返回的 %d 片场地里找不到对应的" % len(areas)

    def _build_targets(self, meta, times, courts):
        """界面勾选 -> 备选池 + 单次提交上限。

        返回 (candidates, notes, limit)：
          candidates —— **完整**备选池，不再裁剪。它的顺序就是"按顺序取"的依据：
                        时段优先（界面时段从上到下），每个时段内按场地清单顺序。
          limit      —— 本次最多提交几个（系统硬上限与接口上限取小）。
        notes 是需要回显到日志的说明。

        勾多了不会被丢掉：开抢时由 _pick_targets 从池子头部开始挑可约的，
        挑够 limit 个就提交；前面的抢不到会自动往后顺延到备选。
        """
        areas = meta.get("areas") or []
        max_n = meta.get("max_n") or 0
        order = self._venue_order()
        candidates, notes = [], []
        guessed = []
        for time_label in times:
            matched_time = self._best_match(meta.get("times") or [], time_label)
            if not matched_time:
                notes.append(f"时段「{time_label}」不在当前场次里，已跳过。")
                continue
            # 同一片场地出现在不同时段是正常需求，所以按时段分别去重
            taken = {}
            for court_label in courts:
                area, why = self._resolve_area(areas, order, court_label)
                if not area:
                    notes.append(f"场地「{court_label}」{why}，已跳过。")
                    continue
                if area["code"] in taken:
                    notes.append(
                        "场地「%s」和「%s」都映射到 %s，已跳过后者。"
                        % (court_label, taken[area["code"]], area["name"]))
                    continue
                taken[area["code"]] = court_label
                if why:
                    guessed.append("%s→%s（%s）" % (court_label, area["name"], why))
                if (matched_time, area["code"]) not in candidates:
                    candidates.append((matched_time, area["code"]))
        if guessed:
            notes.append("以下场地是按顺序推定的，请核对：" + "；".join(guessed))
        limit = MAX_PER_ORDER
        if 0 < max_n < limit:
            limit = max_n
            notes.append("接口显示本次最多只能选 %d 个，单次提交数已降到 %d。" % (max_n, max_n))
        return candidates, notes, limit

    @classmethod
    def _pick_targets(cls, candidates, cells, now, limit):
        """按池子顺序挑出本轮要提交的单元。

        返回 (ready, available, blocked, quota)：
          ready     —— 真正拿去下单的 [(key, session_id)]，池子顺序，最多 quota 个
          available —— 本轮全部可约单元，同样按池子顺序
          blocked   —— [(key, 原因)]，只用于日志
          quota     —— 本次想要凑到的个数（= min(limit, 池子大小)）

        ready 非空就表示"可以提交了"；调用方再用 len(available) >= quota
        区分"凑满"和"没凑满但按有几个抢先几个提交"。
        """
        available, blocked = [], []
        for key in candidates:
            cell = cells.get(key) if isinstance(cells, dict) else None
            usable, why = cls.cell_state(cell, now)
            if usable:
                available.append((key, cell.get("id")))
            else:
                blocked.append((key, why))
        quota = min(int(limit or 0), len(candidates)) if candidates else 0
        return available[:quota], available, blocked, quota

    async def _lock_targets(self, schedule_id, live, ready, note):
        """按挑好的单元去锁定：极速模式直接下单，失败再回落到页面点击。"""
        keys = [key for key, _sid in ready]
        ids = [sid for _key, sid in ready]
        self.write(note)
        self.write("本次提交 %d 个单元（系统上限 %d）：%s" % (
            len(ids), MAX_PER_ORDER, "、".join("%s %s" % k for k in keys)))
        fast_mode = bool(getattr(self, "fast_submit", True))
        if fast_mode and await self._fast_submit(ids):
            return True
        if fast_mode:
            self.write("极速模式未成功，改用页面点击方式重试。")
        if await self._click_submit(schedule_id, live, keys):
            return True
        self.write("本轮未锁定成功，继续探测。")
        return False

    async def _sync_metadata(self, date_text, quiet=False):
        """读取场次表 + 目标场次余量，返回 (schedule_id, meta)。失败返回 (None, None)。

        quiet=True 用于刚过开抢时刻的重试：少打日志，避免刷屏。
        """
        ok, code, detail, note = self._envelope(await self._api_list())
        if not ok:
            if not quiet:
                self.write(f"读取预约场次失败：{note}")
                if code == 401:
                    self.write("请先在浏览器里完成登录，再重新点开始监控。")
            return None, None
        detail = detail if isinstance(detail, dict) else {}
        if detail.get("isBan") and not quiet:
            self.write("警告：当前账号在预约黑名单中，接口很可能拒绝下单。")
        allow = detail.get("allowTimeRange")
        if isinstance(allow, (list, tuple)) and len(allow) == 2 and not quiet:
            self.write(f"系统开放预约时段：{allow[0]} - {allow[1]}")
        schedule_list = detail.get("scheduleList") or []
        if not schedule_list:
            if not quiet:
                self.write("系统没有返回任何可预约场次（当天可能未安排或已结束）。")
            return None, None
        picked = None
        for item in schedule_list:
            # 站点用「09月21日」，界面用「2026-09-21」——必须拆成月日再比，
            # 直接比字符串会永远匹配不上，然后一直空等到超时。
            if self._date_matches(item.get("schedule_date"), date_text):
                picked = item
                break
        if picked is None:
            # 绝不静默换成别的日期 —— 在 11:59 这条路径上，目标日期正是放号瞬间
            # 才会出现；此时随便挑一个场次等于"抢错天"。交给上层重试。
            if not quiet:
                dates = "、".join(str(i.get("schedule_date")) for i in schedule_list[:5])
                self.write(f"可约场次里暂时没有 {date_text}（当前可约：{dates}）。")
            return None, None
        if picked.get("status") == "闭馆":
            self.write(f"{picked.get('schedule_date')} 显示闭馆，没有可约场地。")
            return None, None
        schedule_id = picked.get("id")
        ok, code, detail, note = self._envelope(await self._api_detail(schedule_id))
        if not ok:
            if not quiet:
                self.write(f"读取场地余量失败：{note}")
            return None, None
        meta = self.normalize_detail(detail)
        self.write(
            f"已同步 {picked.get('schedule_date')}：{len(meta['areas'])} 片场地 × "
            f"{len(meta['times'])} 个时段，最多可约 {meta['max_n'] or '不限'} 个场次。")
        return schedule_id, meta

    async def _fast_submit(self, ids):
        """直接调提交接口下单：与页面点提交等价，但省掉整轮 DOM 交互。"""
        try:
            res = await asyncio.wait_for(self._api_submit(ids), timeout=15)
        except asyncio.TimeoutError:
            self.write("提交请求超时，下单结果未知，请在浏览器里核对订单。")
            return False
        ok, code, detail, note = self._envelope(res)
        if not ok:
            self.write(f"接口提交未成功：{note}")
            return False
        rows = detail if isinstance(detail, list) else []
        order_ids = [str(r.get("order_id")) for r in rows
                     if isinstance(r, dict) and r.get("order_id")]
        if order_ids:
            self.write("下单成功，订单号：" + "、".join(order_ids) + "。请在 5 分钟内完成支付。")
        else:
            self.write("接口返回成功，但没解析到订单号，请在浏览器里确认订单。")
        self.write("请到浏览器页面完成支付（本工具不会代付）。")
        return True

    async def _click_submit(self, schedule_id, live, targets):
        """兜底通道：轻量刷新 -> 切回目标场次 -> 按下标精确点击 -> 点提交。"""
        if not self.page:
            return False
        self.write("促发页面轻量刷新（只重拉数据，不做整页 reload）……")
        await self._dom_trigger_refresh()
        await self.page.wait_for_timeout(700)
        switched = await self._dom_select_schedule(schedule_id)
        if isinstance(switched, dict) and switched.get("clicked"):
            await self.page.wait_for_timeout(700)
        for _ in range(12):
            rows = await self.page.evaluate(
                "() => document.querySelectorAll('.data-column-row').length")
            if rows:
                break
            await self.page.wait_for_timeout(150)
        time_index = {t: i for i, t in enumerate(live["times"])}
        area_index = {a["code"]: i for i, a in enumerate(live["areas"])}
        picked = []
        for key in targets:
            row, col = time_index.get(key[0]), area_index.get(key[1])
            if row is None or col is None:
                self.write(f"页面表格里找不到位置：{key[0]} / {key[1]}")
                continue
            result = await self._dom_click_cell(row, col)
            if not result.get("ok"):
                self.write(f"点击 {key[0]} {key[1]} 失败：{result.get('why')}")
                continue
            for _ in range(10):
                await self.page.wait_for_timeout(80)
                if "inner-select" in await self._dom_cell_class(row, col):
                    picked.append(key)
                    break
            else:
                self.write(f"{key[0]} {key[1]} 点击后没有出现已选状态。")
        if len(picked) < len(targets):
            self.write(f"页面只选中 {len(picked)}/{len(targets)} 个，本轮放弃提交。")
            return False
        self.write("页面已选中全部目标，正在提交……")
        await self._submit_selected_court()
        return True

    async def _click_text_variants(self, variants, timeout=800):
        for text in variants:
            try:
                loc = self.page.get_by_text(text, exact=False).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=timeout)
                    return True
            except Exception:
                continue
        return False

    async def _selected_slot_count(self):
        """读取页面在场地真正选中后显示的场次数量。"""
        try:
            return await self.page.evaluate(r"""() => {
              let count = 0;
              for (const el of document.querySelectorAll('*')) {
                const text = (el.innerText || '').replace(/\s+/g, '');
                const match = text.match(/已选(?:定|择)?(\d+)个(?:场次|场地|时段)/)
                           || text.match(/已选(?:定|择)?[:：]?(\d+)/);
                if (match) count = Math.max(count, Number(match[1]));
              }
              return count;
            }""")
        except Exception:
            return 0

    async def _click_slot_at(self, x, y, previous_count):
        """点击候选单元格，仅在页面确认选中后返回成功。"""
        try:
            selectable = await self.page.evaluate(r"""({x, y}) => {
              const el = document.elementFromPoint(x, y);
              if (!el) return false;
              const text = (el.innerText || '').replace(/\s+/g, '');
              const cls = String(el.className || '').toLowerCase();
              return !/已约|不可预约|已满|售罄/.test(text) &&
                     !/(disabled|unavailable|occupied)/.test(cls);
            }""", {"x": x, "y": y})
            if not selectable:
                return False
            await self.page.mouse.click(x, y)
            for _ in range(8):
                await self.page.wait_for_timeout(150)
                if await self._selected_slot_count() > previous_count:
                    return True
                # 有些页面只改变单元格样式，不更新“已选数量”文本。
                # 通过 aria/class/style 识别这种已选状态，保证第二片场地或第二个时段也能继续点击。
                marked = await self.page.evaluate(r"""({x, y}) => {
                  const el = document.elementFromPoint(x, y);
                  if (!el) return false;
                  const chain = [el, el.parentElement, el.closest('button,[role=button]')].filter(Boolean);
                  return chain.some(node => {
                    const cls = String(node.className || '').toLowerCase();
                    const aria = String(node.getAttribute('aria-pressed') || '').toLowerCase();
                    const style = String(node.getAttribute('style') || '').toLowerCase();
                    return aria === 'true' || /(selected|select|checked|active|chosen|已选)/.test(cls + style);
                  });
                }""", {"x": x, "y": y})
                if marked:
                    return True
        except Exception as e:
            self.write(f"场地点击确认失败：{e}")
        return False

    async def _click_court_slot(self, court_label, time_label):
        """点击场地行与时间列的交叉单元格，并确认页面真的选中。"""
        try:
            previous_count = await self._selected_slot_count()
            key = (court_label, time_label)
            row_xy = self._slot_cache.get(("__label__", court_label))
            col_xy = self._slot_cache.get(("__label__", time_label))
            if row_xy and col_xy:
                if await self._click_slot_at(col_xy[0], row_xy[1], previous_count):
                    return True
                self._slot_cache.pop(("__label__", court_label), None)
                self._slot_cache.pop(("__label__", time_label), None)
                self._slot_cache.pop(key, None)
            if key in self._slot_cache:
                x, y = self._slot_cache[key]
                if await self._click_slot_at(x, y, previous_count):
                    return True
                self._slot_cache.pop(key, None)
            row = self.page.get_by_text(court_label, exact=True).first
            col = self.page.get_by_text(time_label, exact=True).first
            if not await row.count() or not await col.count():
                return False
            rb, cb = await row.bounding_box(), await col.bounding_box()
            if rb and cb:
                x = cb["x"] + cb["width"] / 2
                y = rb["y"] + rb["height"] / 2
                self._slot_cache[key] = (x, y)
                if await self._click_slot_at(x, y, previous_count):
                    return True
            self.write(f"场地 {court_label} / {time_label} 点击后未出现选定状态")
        except Exception as e:
            self.write(f"表格交叉点点击失败：{e}")
        return False

    async def _refresh_label_cache(self, times, courts):
        """一次性扫描页面文本节点，缓存时间列和场地行坐标。"""
        try:
            labels = list(dict.fromkeys(times + courts))
            found = {}
            for _ in range(6):
                found = await self.page.evaluate("""labels => {
              const out = {};
              for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText || '').trim();
                if (labels.includes(t)) { const r = el.getBoundingClientRect(); if (r.width && r.height) out[t] = {x:r.x+r.width/2,y:r.y+r.height/2}; }
              }
              return out;
                }""", labels)
                if len(found) >= 2:
                    break
                await asyncio.sleep(0.12)
            # 页面刷新或异步重绘后，旧坐标不再可信。
            self._slot_cache.clear()
            for k, v in found.items():
                self._slot_cache[("__label__", k)] = (v["x"], v["y"])
            if found:
                self.write(f"已加载预约表坐标：{len(found)} 个")
        except Exception:
            pass

    async def _select_calendar_date(self, date_text):
        """选择 uni-app 日历中的日期，例如 2026-07-15 -> 点击 15。"""
        try:
            target = datetime.strptime(date_text, "%Y-%m-%d")
            month_label = f"{target.year}年{target.month:02d}月"
            await self._click_text_variants([month_label, f"{target.year}年{target.month}月"], 500)
            day = str(target.day)
            # 日历日期通常是按钮/文本节点，优先使用精确文本，避免误点说明文字。
            candidates = self.page.get_by_text(day, exact=True)
            for i in range(await candidates.count()):
                item = candidates.nth(i)
                if await item.is_visible():
                    await item.click();
                    self.write(f"已选择日期 {date_text}")
                    await self.page.wait_for_timeout(500)
                    return True
        except Exception as e:
            self.write(f"日期选择失败：{e}")
        return False

    async def _go_payment_page(self):
        """尝试推进到订单/付款页面，但不执行支付。"""
        labels = ["请选择场地并提交", "场地并提交", "请提交", "提交订单", "立即预约", "立即预订", "预约", "预订", "下一步", "确认提交", "去支付", "支付"]
        # 先尝试常规文本定位，兼容 uni-app 中文字包在多层 view 的情况。
        for label in labels:
            try:
                button = self.page.get_by_text(label, exact=True).first
                if await button.count() and await button.is_visible():
                    await button.click()
                    await self.page.wait_for_timeout(1000)
                    self.write("已尝试进入订单/付款页面，请手动选择付款方式并完成支付。")
                    return
            except Exception:
                continue
        # 再遍历可点击元素，处理文本带空格、换行或图标的情况。
        try:
            for i in range(await self.page.locator("[class*=button], [class*=btn], [role=button]").count()):
                item = self.page.locator("[class*=button], [class*=btn], [role=button]").nth(i)
                if not await item.is_visible():
                    continue
                text = " ".join((await item.inner_text()).split())
                if any(label in text for label in labels):
                    await item.click()
                    await self.page.wait_for_timeout(1000)
                    self.write(f"已点击“{text[:20]}”，请检查是否已进入付款页面。")
                    return
        except Exception:
            pass
        self.write("已选中场地，但未找到自动进入付款页面的按钮，请在浏览器中点击预约或提交订单。")

    async def _submit_selected_court(self):
        """等待底部提交按钮更新后自动点击。"""
        for _ in range(12):
            # uni-app 底部固定按钮可能是 div/view，直接触发其最近可点击父节点。
            try:
                clicked = await self.page.evaluate("""() => {
                  const keys=['请选择场地并提交','场地并提交','请提交','提交订单','立即预约','确认提交'];
                  for (const el of document.querySelectorAll('*')) {
                    const t=(el.innerText||'').replace(/\\s+/g,'').trim();
                    if (keys.some(k=>t.includes(k)) && el.getBoundingClientRect().width>100) {
                      const target=el.closest('button,[role=button],[class*=button],[class*=btn]') || el;
                      target.click(); return t;
                    }
                  }
                  return '';
                }""")
                if clicked:
                    self.write(f"已自动触发提交控件“{clicked[:20]}”，正在进入付款页面……")
                    await self.page.wait_for_timeout(600)
                    self.write(f"当前页面：{self.page.url}")
                    await self._go_payment_page()
                    return
            except Exception:
                pass
            try:
                clicked = await self.page.evaluate("""() => {
                  const h=innerHeight;
                  for (const el of document.querySelectorAll('*')) {
                    const r=el.getBoundingClientRect(), t=(el.innerText||'').replace(/\\s+/g,'');
                    if (r.width>300 && r.height>35 && r.bottom>h-130 && r.bottom<=h+20 && (t.includes('提交')||t.includes('预约'))) { el.click(); return t; }
                  }
                  return '';
                }""")
                if clicked:
                    self.write(f"已触发底部提交按钮“{clicked[:20]}”，正在进入付款页面……")
                    await self.page.wait_for_timeout(700)
                    await self._go_payment_page()
                    return
            except Exception:
                pass
            for label in ["请选择场地并提交", "场地并提交", "请提交", "提交订单", "立即预约", "确认提交"]:
                try:
                    button = self.page.get_by_text(label, exact=False).first
                    if await button.count() and await button.is_visible():
                        await button.click()
                        self.write(f"已自动点击“{label}”，正在进入付款页面……")
                        await self.page.wait_for_timeout(800)
                        await self._go_payment_page()
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.25)
        self.write("场地已点击，但提交按钮未在规定时间内出现。")
class GlassCard(QFrame):
    """Semi-transparent card with a soft elevation shadow."""

    def __init__(self, parent=None, object_name="glassCard"):
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(31, 41, 55, 34))
        self.setGraphicsEffect(shadow)


class QtBookingApp(QMainWindow):
    """High-DPI PySide6 front end using the existing Playwright booking core."""

    log_signal = Signal(str, str, str)
    monitor_finished = Signal()

    def __init__(self):
        QMainWindow.__init__(self)
        self.setWindowTitle("江苏大学羽毛球自动预约场地助手")
        self.resize(1360, 900)
        self.setMinimumSize(1040, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.loop = asyncio.new_event_loop()
        self.browser = self.page = None
        self._slot_cache = {}
        self._nav_buttons = {}
        self._section_widgets = {}
        self._venue_boxes = []
        self.fast_submit = True
        threading.Thread(target=self._run_loop, daemon=True).start()

        self.log_signal.connect(self._append_log)
        self.monitor_finished.connect(self._reset_monitor_button)
        self._build_qt_ui()
        QTimer.singleShot(80, self._enable_acrylic_backdrop)

    def _enable_acrylic_backdrop(self):
        """Use the Windows compositor for a real blurred/translucent backdrop."""
        if sys.platform != "win32":
            return
        try:
            class AccentPolicy(ctypes.Structure):
                _fields_ = [
                    ("AccentState", ctypes.c_int),
                    ("AccentFlags", ctypes.c_int),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_int),
                ]

            class WindowCompositionAttributeData(ctypes.Structure):
                _fields_ = [
                    ("Attribute", ctypes.c_int),
                    ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t),
                ]

            accent = AccentPolicy(4, 2, 0xD8F2F0ED, 0)
            data = WindowCompositionAttributeData(
                19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent)
            )
            ctypes.windll.user32.SetWindowCompositionAttribute(int(self.winId()), ctypes.byref(data))
        except Exception:
            try:
                backdrop = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    int(self.winId()), 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
                )
            except Exception:
                pass

    def _build_qt_ui(self):
        self.setStyleSheet(self._stylesheet())
        shell = QWidget()
        shell.setObjectName("windowShell")
        self.setCentralWidget(shell)
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(16, 16, 16, 16)
        shell_layout.setSpacing(16)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(224)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 24)
        side.setSpacing(8)

        brand = QLabel("NOVA / BOOKING")
        brand.setObjectName("brand")
        side.addWidget(brand)
        product = QLabel("智能场地管理系统")
        product.setObjectName("sideMuted")
        side.addWidget(product)
        side.addSpacing(24)
        for key, label in [
            ("overview", "总览 Dashboard"),
            ("booking", "预约任务"),
            ("courts", "场地资源"),
            ("logs", "运行日志"),
        ]:
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setChecked(key == "overview")
            button.clicked.connect(lambda _checked=False, name=key: self._sidebar_action(name))
            side.addWidget(button)
            self._nav_buttons[key] = button
        side.addStretch(1)
        online = QLabel("●  服务在线")
        online.setObjectName("online")
        side.addWidget(online)
        version = QLabel("Qt Glass UI  ·  6.11")
        version.setObjectName("sideMuted")
        side.addWidget(version)
        shell_layout.addWidget(sidebar)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("scrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        viewport_host = QWidget()
        viewport_layout = QHBoxLayout(viewport_host)
        viewport_layout.setContentsMargins(24, 8, 24, 24)
        viewport_layout.addStretch(1)
        page = QWidget()
        page.setObjectName("dashboardPage")
        page.setMaximumWidth(1200)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.page_layout = QVBoxLayout(page)
        self.page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_layout.setSpacing(16)
        viewport_layout.addWidget(page, 1)
        viewport_layout.addStretch(1)
        self.scroll.setWidget(viewport_host)
        shell_layout.addWidget(self.scroll, 1)

        hero = GlassCard(object_name="heroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 24, 24, 24)
        hero_layout.setSpacing(16)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(4)
        title = QLabel("羽毛球场地预约助手")
        title.setObjectName("heroTitle")
        subtitle = QLabel("JIANGSU UNIVERSITY  ·  SMART BOOKING DASHBOARD")
        subtitle.setObjectName("heroSubtitle")
        hero_text.addWidget(title)
        hero_text.addWidget(subtitle)
        hero_layout.addLayout(hero_text, 1)
        status = QLabel("●  系统就绪")
        status.setObjectName("statusChip")
        hero_layout.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
        self.page_layout.addWidget(hero)
        self._section_widgets["overview"] = hero

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(16)
        metrics.setVerticalSpacing(16)
        metric_data = [
            ("预约日期", date.today().strftime("%m-%d"), "默认选择当天"),
            ("开始匹配", "11:59", "精确对时，误差 < 0.1 秒"),
            ("探测方式", "接口轮询", "不刷新页面，毫秒级"),
            ("命中之后", "直接下单", "极速模式，可切换为点击"),
        ]
        for index, (label, value, detail) in enumerate(metric_data):
            metric = GlassCard()
            layout = QVBoxLayout(metric)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(4)
            small = QLabel(label)
            small.setObjectName("mutedText")
            number = QLabel(value)
            number.setObjectName("metricValue")
            hint = QLabel(detail)
            hint.setObjectName("mutedText")
            layout.addWidget(small)
            layout.addWidget(number)
            layout.addWidget(hint)
            metrics.addWidget(metric, 0, index)
        self.page_layout.addLayout(metrics)

        booking_row = QHBoxLayout()
        booking_row.setSpacing(16)
        booking_card = GlassCard()
        booking_card.setMinimumWidth(560)
        booking_layout = QVBoxLayout(booking_card)
        booking_layout.setContentsMargins(24, 24, 24, 24)
        booking_layout.setSpacing(16)
        self._add_heading(booking_layout, "预约条件", "设置日期、刷新频率和期望时间段。")

        fields = QHBoxLayout()
        fields.setSpacing(16)
        date_box = QVBoxLayout()
        date_box.setSpacing(8)
        date_box.addWidget(self._field_label("预约日期"))
        self.date = QComboBox()
        for offset in range(90):
            full_date = (date.today() + timedelta(days=offset)).isoformat()
            self.date.addItem(full_date[5:], full_date)
        date_box.addWidget(self.date)
        fields.addLayout(date_box, 1)
        interval_box = QVBoxLayout()
        interval_box.setSpacing(8)
        interval_box.addWidget(self._field_label("探测间隔（秒）"))
        self.interval = QLineEdit("0.4")
        self.interval.setPlaceholderText("建议 0.3–1.0 秒")
        interval_box.addWidget(self.interval)
        fields.addLayout(interval_box, 1)
        booking_layout.addLayout(fields)

        booking_layout.addWidget(self._field_label("预约时间段（可多选）"))
        self.time_host = QWidget()
        self.time_grid = QGridLayout(self.time_host)
        self.time_grid.setContentsMargins(0, 0, 0, 0)
        self.time_grid.setHorizontalSpacing(8)
        self.time_grid.setVerticalSpacing(8)
        booking_layout.addWidget(self.time_host)
        self._build_time_checks([f"{hour:02d}:00-{hour + 1:02d}:00" for hour in range(14, 21)])
        booking_layout.addStretch(1)
        booking_row.addWidget(booking_card, 2)
        self._section_widgets["booking"] = booking_card

        control_card = GlassCard()
        control_layout = QVBoxLayout(control_card)
        control_layout.setContentsMargins(24, 24, 24, 24)
        control_layout.setSpacing(8)
        self._add_heading(control_layout, "运行控制", "付款与最终提交需要本人确认。")
        safe_box = QFrame()
        safe_box.setObjectName("safeBox")
        safe_layout = QVBoxLayout(safe_box)
        safe_layout.setContentsMargins(16, 16, 16, 16)
        safe_layout.setSpacing(4)
        safe_title = QLabel("✓  安全模式已开启")
        safe_title.setObjectName("safeTitle")
        safe_layout.addWidget(safe_title)
        safe_layout.addWidget(QLabel("程序只负责匹配和选择场地。"))
        control_layout.addWidget(safe_box)
        control_layout.addStretch(1)
        self.fast_box = QCheckBox("极速模式：命中后直接调用下单接口")
        self.fast_box.setChecked(True)
        self.fast_box.setToolTip(
            "开启：探测到全部目标可约时，直接调用下单接口（最快，通常 0.1–0.3 秒完成）。\n"
            "关闭：改用页面点击方式选中后再提交（较慢，但能看到界面上的选中过程）。\n"
            "两种方式下单结果等价；验证码与付款始终需要本人完成。")
        self.fast_box.stateChanged.connect(self._toggle_fast_mode)
        control_layout.addWidget(self.fast_box)
        open_button = QPushButton("打开预约页面")
        open_button.clicked.connect(self.open_page)
        control_layout.addWidget(open_button)
        self.start_btn = QPushButton("开始智能监控")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.clicked.connect(self.start)
        control_layout.addWidget(self.start_btn)
        payment_button = QPushButton("继续进入付款")
        payment_button.clicked.connect(self.continue_payment)
        control_layout.addWidget(payment_button)
        booking_row.addWidget(control_card, 1)
        self.page_layout.addLayout(booking_row)

        self.courts_host = QWidget()
        self.courts_row = QHBoxLayout(self.courts_host)
        self.courts_row.setContentsMargins(0, 0, 0, 0)
        self.courts_row.setSpacing(16)
        self.page_layout.addWidget(self.courts_host)
        self._build_court_cards(VENUE_GROUPS)

        log_card = GlassCard()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(24, 24, 24, 24)
        log_layout.setSpacing(16)
        self._add_heading(log_layout, "实时运行日志", "蓝色操作 · 绿色成功 · 黄色等待 · 红色异常")
        self.log = QTextEdit()
        self.log.setObjectName("logView")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        log_layout.addWidget(self.log)
        self.page_layout.addWidget(log_card)
        self._section_widgets["logs"] = log_card

        footer = QLabel("Jiangsu University Smart Booking  ·  验证码、付款和最终提交需本人完成")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_layout.addWidget(footer)

    # =====================================================================
    # 场地/时段选项：支持从接口同步后重建（避免硬编码名字与真实名字对不上）
    # =====================================================================
    def _build_time_checks(self, slots):
        self.time_vars = {}
        while self.time_grid.count():
            item = self.time_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, slot in enumerate(slots):
            box = QCheckBox(str(slot))
            self.time_vars[str(slot)] = box
            self.time_grid.addWidget(box, index // 4, index % 4)

    def _build_court_cards(self, groups):
        self.court_vars = {}
        while self.courts_row.count():
            item = self.courts_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for floor_index, (heading, detail, names) in enumerate(groups):
            floor_card = GlassCard()
            floor_layout = QVBoxLayout(floor_card)
            floor_layout.setContentsMargins(24, 24, 24, 24)
            floor_layout.setSpacing(16)
            self._add_heading(floor_layout, heading, detail)
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            for index, name in enumerate(names):
                label = str(name)
                for prefix in ("一楼", "二楼", "三楼", "四楼"):
                    if label.startswith(prefix):
                        label = label[len(prefix):]
                        break
                box = QCheckBox(label)
                box.setToolTip(str(name))
                self.court_vars[str(name)] = box
                grid.addWidget(box, index // 3, index % 3)
            floor_layout.addLayout(grid)
            self.courts_row.addWidget(floor_card, 1)
            if floor_index == 0:
                self._section_widgets["courts"] = floor_card

    def _toggle_fast_mode(self, state):
        self.fast_submit = bool(state)
        self.write("已切换为" + ("极速模式：命中后直接调接口下单。"
                                 if self.fast_submit else "界面模式：在页面上点击选中后提交。"))

    def _add_heading(self, layout, title, subtitle):
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        detail = QLabel(subtitle)
        detail.setObjectName("mutedText")
        layout.addWidget(heading)
        layout.addWidget(detail)

    def _field_label(self, text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _stylesheet(self):
        return r"""
        * { font-family: "Microsoft YaHei UI"; font-size: 14px; color: #1f2937; }
        QMainWindow, #windowShell { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(222, 230, 238, 158), stop:1 rgba(247, 237, 228, 142)); }
        #sidebar { background: rgba(30, 35, 40, 246); border: 1px solid rgba(255,255,255,30); border-radius: 24px; }
        #brand { color: #ffffff; font-size: 17px; font-weight: 700; letter-spacing: 1px; }
        #sideMuted { color: #9ca3af; font-size: 12px; }
        #online { color: #fb923c; font-size: 13px; font-weight: 600; }
        QPushButton#navButton { min-height: 40px; text-align: left; padding: 0 16px; color: #9ca3af; background: transparent; border: 0; border-radius: 12px; }
        QPushButton#navButton:hover { color: #ffffff; background: rgba(255,255,255,18); }
        QPushButton#navButton:checked { color: #ffffff; background: rgba(249,115,22,210); font-weight: 600; }
        #scrollArea, #dashboardPage, QScrollArea > QWidget > QWidget { background: transparent; border: 0; }
        #glassCard { background: rgba(255,255,255,148); border: 1px solid rgba(255,255,255,210); border-radius: 24px; }
        #heroCard { background: rgba(30,35,40,224); border: 1px solid rgba(255,255,255,46); border-radius: 24px; }
        #heroTitle { color: #ffffff; font-size: 32px; font-weight: 700; }
        #heroSubtitle { color: #cbd5e1; font-size: 12px; letter-spacing: 1px; }
        #statusChip { color: #ffffff; background: rgba(249,115,22,210); border: 1px solid rgba(255,255,255,70); border-radius: 12px; padding: 8px 12px; font-size: 13px; font-weight: 600; }
        #metricValue { color: #111827; font-size: 27px; font-weight: 700; padding: 4px 0; }
        #cardTitle { color: #111827; font-size: 19px; font-weight: 700; }
        #mutedText, #footer { color: #6b7280; font-size: 12px; }
        #fieldLabel { color: #374151; font-size: 13px; font-weight: 600; }
        QComboBox, QLineEdit { min-height: 40px; padding: 0 12px; background: rgba(255,255,255,175); border: 1px solid rgba(148,163,184,95); border-radius: 12px; selection-background-color: #f97316; }
        QComboBox:hover, QLineEdit:hover, QComboBox:focus, QLineEdit:focus { border: 1px solid rgba(249,115,22,190); background: rgba(255,255,255,225); }
        QComboBox::drop-down { width: 28px; border: 0; }
        QComboBox QAbstractItemView { background: #ffffff; border: 1px solid #d1d5db; selection-background-color: #ffedd5; selection-color: #9a3412; }
        QCheckBox { min-height: 32px; spacing: 8px; color: #374151; }
        QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #a8b0bb; border-radius: 5px; background: rgba(255,255,255,190); }
        QCheckBox::indicator:checked { background: #f97316; border: 4px solid #f97316; }
        QPushButton { min-height: 40px; padding: 0 16px; color: #30363d; background: rgba(255,255,255,176); border: 1px solid rgba(148,163,184,95); border-radius: 12px; font-weight: 600; }
        QPushButton:hover { background: rgba(255,247,237,235); border-color: rgba(249,115,22,175); }
        QPushButton:pressed { background: rgba(255,237,213,240); }
        QPushButton:disabled { color: #9ca3af; background: rgba(229,231,235,155); }
        QPushButton#primaryButton { color: #ffffff; background: #f97316; border: 1px solid #fb923c; }
        QPushButton#primaryButton:hover { background: #ea580c; }
        #safeBox { background: rgba(224,231,226,145); border: 1px solid rgba(255,255,255,180); border-radius: 16px; }
        #safeTitle { color: #4b6355; font-weight: 700; }
        #logView { background: rgba(28,34,31,235); color: #e5e7eb; border: 1px solid rgba(255,255,255,45); border-radius: 16px; padding: 16px; font-family: "Cascadia Mono"; font-size: 12px; }
        QScrollBar:vertical { width: 8px; background: transparent; margin: 8px 0; }
        QScrollBar::handle:vertical { background: rgba(107,114,128,100); min-height: 40px; border-radius: 4px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """

    def _sidebar_action(self, item):
        for key, button in self._nav_buttons.items():
            button.setChecked(key == item)
        target = self._section_widgets.get(item)
        if target:
            self.scroll.ensureWidgetVisible(target, 0, 24)
        labels = {"overview": "总览", "booking": "预约任务", "courts": "场地资源", "logs": "运行日志"}
        self.write(f"已切换到 {labels.get(item, item)}")

    def write(self, msg):
        stamp = datetime.now().strftime("%H:%M:%S")
        if any(key in msg for key in ["异常", "失败", "错误"]):
            category = "error"
        elif any(key in msg for key in ["已选择", "已到", "成功", "进入"]):
            category = "success"
        elif any(key in msg for key in ["等待", "未找到"]):
            category = "wait"
        elif any(key in msg for key in ["开始", "点击", "尝试", "刷新"]):
            category = "action"
        else:
            category = "system"
        self.log_signal.emit(stamp, msg, category)

    def _append_log(self, stamp, msg, category):
        colors = {
            "system": "#d9ddd6", "wait": "#f4d06f", "success": "#9ed3ae",
            "error": "#f09a8d", "action": "#8ec5ff",
        }
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colors.get(category, "#d9ddd6")))
        fmt.setFontFamilies(["Cascadia Mono", "Microsoft YaHei UI"])
        cursor.insertText(f"[{stamp}] {msg}\n", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def open_page(self):
        self.submit(self._open())

    def start(self):
        if not self.page:
            QMessageBox.warning(self, "提示", "请先打开预约页面并登录")
            return
        times = [name for name, checkbox in self.time_vars.items() if checkbox.isChecked()]
        courts = [name for name, checkbox in self.court_vars.items() if checkbox.isChecked()]
        if not times or not courts:
            QMessageBox.warning(self, "提示", "请至少选择一个时间段和一个场地")
            return
        try:
            interval = max(0.2, float(self.interval.text().strip() or "1"))
        except ValueError:
            QMessageBox.warning(self, "提示", "刷新间隔必须是数字")
            return
        selected_date = self.date.currentData() or date.today().isoformat()
        self.fast_submit = bool(self.fast_box.isChecked())
        units = len(times) * len(courts)
        if units > MAX_PER_ORDER:
            self.write("已勾选 %d 个时段 × %d 片场地 = %d 个备选单元；开抢时按顺序取前 %d 个"
                       "可约的提交，其余顺延备用。" % (
                           len(times), len(courts), units, MAX_PER_ORDER))
        self.write("已点击开始监控，正在准备预约页面……")
        self.start_btn.setEnabled(False)
        self.start_btn.setText("监控进行中…")
        future = self.submit(self._monitor(selected_date, times, courts, interval))
        future.add_done_callback(self._monitor_done)

    def continue_payment(self):
        if not self.page:
            QMessageBox.warning(self, "提示", "请先打开预约页面并登录")
            return
        self.write("正在尝试从当前浏览器页面进入付款流程……")
        future = self.submit(self._go_payment_page())
        future.add_done_callback(self._monitor_done)

    def _monitor_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self.write(f"监控异常：{exc}")
        finally:
            self.monitor_finished.emit()

    def _reset_monitor_button(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始智能监控")

    def closeEvent(self, event):
        try:
            if self.browser:
                self.submit(self.browser.close())
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass
        event.accept()


# Reuse only the UI-independent Playwright controller methods.  Keeping this
# list explicit prevents the legacy Tk interface from being initialized or
# collected as a runtime dependency.
for _controller_method in (
    "_run_loop",
    "submit",
    "_scroll_to_page_bottom",
    "_open",
    "_monitor",
    # --- 接口通道（本次新增） ---
    "_api",
    "_api_list",
    "_api_detail",
    "_api_submit",
    "_envelope",
    "_verify_login",
    "_sleep_until",
    "_tick_dom_refresh",
    "_opening_for",
    "_release_for",
    "_parse_date_parts",
    "_date_matches",
    "normalize_detail",
    "cell_state",
    "_split_label",
    "_best_match",
    "_floor_key",
    "_strip_floor",
    "_venue_order",
    "_resolve_area",
    "_build_targets",
    "_pick_targets",
    "_lock_targets",
    "_sync_metadata",
    "_fast_submit",
    "_click_submit",
    # --- DOM 精确定位（兜底通道） ---
    "_dom_cell_class",
    "_dom_click_cell",
    "_dom_select_schedule",
    "_dom_trigger_refresh",
    # --- 旧通道保留，供极端情况兜底 ---
    "_click_text_variants",
    "_selected_slot_count",
    "_click_slot_at",
    "_click_court_slot",
    "_refresh_label_cache",
    "_select_calendar_date",
    "_go_payment_page",
    "_submit_selected_court",
):
    # 用 __dict__ 取值以保住 staticmethod / classmethod 描述符，
    # 否则 self.normalize_detail(...) 会把 self 当成第一个业务参数。
    setattr(QtBookingApp, _controller_method, BookingApp.__dict__[_controller_method])


if __name__ == "__main__":
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("羽毛球场地预约助手")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = QtBookingApp()
    window.show()
    sys.exit(app.exec())
