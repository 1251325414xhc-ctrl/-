import asyncio
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import ttk, messagebox
from pathlib import Path

from playwright.async_api import async_playwright

BASE_URL = "https://tybyy.ujs.edu.cn/"
ROUTE = "/pages/subscribe/index?itemId=2&title=%E7%BE%BD%E6%AF%9B%E7%90%83"
PROFILE = Path(__file__).with_name("browser_profile")


class BookingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("江苏大学羽毛球自动预约场地助手")
        self.root.geometry("760x680")
        self.root.minsize(700, 620)
        style = ttk.Style()
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"), foreground="#173f5f")
        style.configure("Hint.TLabel", font=("Microsoft YaHei UI", 9), foreground="#9a5b00")
        style.configure("Section.TLabelframe", padding=10)
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"), foreground="#20639b")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), foreground="white", background="#20639b", padding=8)
        self.loop = asyncio.new_event_loop()
        self.browser = self.page = None
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._ui()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _ui(self):
        frm = ttk.Frame(self.root, padding=18); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="江苏大学羽毛球自动预约场地助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frm, text="手动登录 · 多时段选择 · 分楼层场地优先级", foreground="#557080").pack(anchor="w", pady=(2, 12))
        login = ttk.LabelFrame(frm, text="第一步：登录预约系统", style="Section.TLabelframe"); login.pack(fill="x", pady=(0, 10))
        ttk.Label(login, text="点击后将在浏览器打开预约页面，请完成登录。", foreground="#557080").pack(side="left")
        ttk.Button(login, text="打开预约页面", command=self.open_page, style="Accent.TButton").pack(side="right")
        ttk.Label(frm, text="提示：场地每天 12:00 开放预约，提前启动会自动等待。", style="Hint.TLabel").pack(anchor="w", pady=(0, 8))
        booking = ttk.LabelFrame(frm, text="第二步：选择预约条件", style="Section.TLabelframe"); booking.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="预约日期", width=12).pack(side="left")
        dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(31)]
        self.date = ttk.Combobox(row, values=dates, state="readonly", width=16)
        self.date.current(1); self.date.pack(side="left", padx=8)
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="时间段（可多选）", width=12).pack(side="left", anchor="n")
        time_options = [f"{h:02d}:00-{h+1:02d}:00" for h in range(14, 21)]
        self.time_vars = {}
        time_box = ttk.Frame(row); time_box.pack(side="left", padx=8, fill="x", expand=True)
        for n, x in enumerate(time_options):
            var = tk.BooleanVar(value=False); self.time_vars[x] = var
            ttk.Checkbutton(time_box, text=x, variable=var).grid(row=n // 4, column=n % 4, sticky="w", padx=3)
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="场地优先级（可多选）", width=12).pack(side="left", anchor="n")
        court_options = [f"一楼 {i}号场" for i in range(1, 9)] + [f"二楼 {i}号场" for i in range(1, 13)]
        self.court_vars = {}
        court_box = ttk.Frame(row); court_box.pack(side="left", padx=8, fill="x", expand=True)
        for n, x in enumerate(court_options):
            var = tk.BooleanVar(value=False); self.court_vars[x] = var
            ttk.Checkbutton(court_box, text=x, variable=var).grid(row=n // 3, column=n % 3, sticky="w", padx=3)
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="刷新间隔（秒）").pack(side="left")
        self.interval = ttk.Entry(row, width=8); self.interval.insert(0, "3"); self.interval.pack(side="left", padx=8)
        self.start_btn = ttk.Button(frm, text="开始监控并自动选择", command=self.start, style="Accent.TButton")
        self.start_btn.pack(fill="x", pady=(2, 12))
        log_frame = ttk.LabelFrame(frm, text="运行日志", style="Section.TLabelframe"); log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=10, state="disabled", bg="#f7fafc", fg="#263238", relief="flat", font=("Consolas", 9)); self.log.pack(fill="both", expand=True)
        self.log.tag_configure("system", foreground="#455a64")
        self.log.tag_configure("wait", foreground="#9a6700")
        self.log.tag_configure("success", foreground="#16803c")
        self.log.tag_configure("error", foreground="#c62828")
        self.log.tag_configure("action", foreground="#1565c0")

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

    async def _open(self):
        if not self.browser:
            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch_persistent_context(str(PROFILE), headless=False)
            self.page = await self.browser.new_page()
        # 先打开域名首页，再设置 hash 路由；部分 SPA 对直接 goto 带 hash 的地址处理不完整。
        await self.page.goto(BASE_URL, wait_until="domcontentloaded")
        await self.page.evaluate("route => { window.location.hash = route; }", ROUTE)
        await self.page.wait_for_timeout(1200)
        self.write("页面已打开，请在浏览器中完成登录；登录状态会保存在 browser_profile。")

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
        future = self.submit(self._monitor(self.date.get().strip(), times, courts, float(self.interval.get() or 3)))
        future.add_done_callback(self._monitor_done)

    def _monitor_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self.write(f"监控异常：{exc}")
            self.root.after(0, lambda: self.start_btn.configure(state="normal", text="开始监控并自动选择"))

    async def _monitor(self, date, times, courts, interval):
        self.write(f"开始监控：{date} / {times} / 场地 {courts}")
        now = datetime.now()
        opening = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now < opening:
            wait_seconds = (opening - now).total_seconds()
            self.write(f"预约每天 12:00 开放，正在等待 {int(wait_seconds // 60)} 分钟后开始监控。")
            await asyncio.sleep(wait_seconds)
            self.write("已到 12:00，开始监控可用场地。")
        while True:
            try:
                await self.page.reload(wait_until="domcontentloaded")
                # Vue/小程序页面通常把可选项渲染为按钮或文本；按可见文本优先匹配。
                await self.page.wait_for_timeout(800)
                if date:
                    await self._click_text_variants([date, date.replace("-", "/")], 800)
                for t in times:
                    t_start = t.split("-")[0]
                    if await self._click_text_variants([t, t_start], 800):
                        self.write(f"已选择时间段 {t}")
                        for c in courts:
                            floor, number = c.split()
                            variants = [c, c.replace(" ", ""), f"{floor}{number}", number, number.replace("号场", "")]
                            if await self._click_text_variants(variants, 800):
                                self.write(f"已选择场地 {c}，请在浏览器中检查并完成验证码/最终提交。")
                                await self._go_payment_page()
                                return
                if self.page.url and await self.page.locator("body").count():
                    body = (await self.page.locator("body").inner_text())[:180].replace("\n", " | ")
                    self.write(f"页面未匹配到场地，当前页面：{body}")
                await asyncio.sleep(interval)
            except Exception as e:
                self.write(f"本轮未找到可用场地：{e}")
                await asyncio.sleep(interval)

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

    async def _go_payment_page(self):
        """尝试推进到订单/付款页面，但不执行支付。"""
        for label in ["提交订单", "立即预约", "预约", "下一步", "确认提交"]:
            try:
                button = self.page.get_by_text(label, exact=True).first
                if await button.count() and await button.is_visible():
                    await button.click()
                    await self.page.wait_for_timeout(1000)
                    self.write("已尝试进入订单/付款页面，请手动选择付款方式并完成支付。")
                    return
            except Exception:
                continue
        self.write("已选中场地，但未找到自动进入付款页面的按钮，请在浏览器中点击预约或提交订单。")
if __name__ == "__main__":
    root = tk.Tk(); BookingApp(root); root.mainloop()
