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
        self._slot_cache = {}
        threading.Thread(target=self._run_loop, daemon=True).start()
        self._ui()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _ui(self):
        shell = ttk.Frame(self.root); shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, highlightthickness=0, background="#ffffff")
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        frm = ttk.Frame(canvas, padding=18)
        canvas_window = canvas.create_window((0, 0), window=frm, anchor="nw")
        frm.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(canvas_window, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
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
        time_box = ttk.LabelFrame(row, text="选择一个或多个预约时段", padding=6); time_box.pack(side="left", padx=8, fill="x", expand=True)
        for n, x in enumerate(time_options):
            var = tk.BooleanVar(value=False); self.time_vars[x] = var
            ttk.Checkbutton(time_box, text=x, variable=var).grid(row=n // 4, column=n % 4, sticky="w", padx=3)
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="场地优先级（可多选）", width=12).pack(side="left", anchor="n")
        court_options = ["一楼塑胶1", "一楼塑胶2", "一楼塑胶3", "一楼木质4", "一楼塑胶5", "一楼塑胶6", "一楼塑胶7", "一楼木质8"] + [f"二楼塑胶{i}" for i in range(1, 13)]
        self.court_vars = {}
        court_box = ttk.Frame(row); court_box.pack(side="left", padx=8, fill="x", expand=True)
        floor1 = ttk.LabelFrame(court_box, text="一楼（8个场地）", padding=5); floor1.pack(fill="x", pady=(0, 5))
        floor2 = ttk.LabelFrame(court_box, text="二楼（12个场地）", padding=5); floor2.pack(fill="x")
        for n, x in enumerate(court_options):
            var = tk.BooleanVar(value=False); self.court_vars[x] = var
            parent = floor1 if n < 8 else floor2
            j = n if n < 8 else n - 8
            ttk.Checkbutton(parent, text=x, variable=var).grid(row=j // 3, column=j % 3, sticky="w", padx=3)
        row = ttk.Frame(booking); row.pack(fill="x", pady=4)
        ttk.Label(row, text="刷新间隔（秒）").pack(side="left")
        self.interval = ttk.Entry(row, width=8); self.interval.insert(0, "1"); self.interval.pack(side="left", padx=8)
        self.start_btn = ttk.Button(frm, text="开始监控并自动选择", command=self.start, style="Accent.TButton")
        self.start_btn.pack(fill="x", pady=(2, 12))
        ttk.Button(frm, text="已在浏览器选好场地，继续进入付款", command=self.continue_payment).pack(fill="x", pady=(0, 10))
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
        self.write(f"开始监控：{date} / {times} / 场地 {courts}")
        now = datetime.now()
        opening = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now < opening:
            wait_seconds = (opening - now).total_seconds()
            self.write(f"预约每天 12:00 开放，正在等待 {int(wait_seconds // 60)} 分钟后开始监控。")
            await asyncio.sleep(wait_seconds)
            self.write("已到 12:00，开始监控可用场地。")
        first_round = True
        round_no = 0
        while True:
            try:
                round_no += 1
                if first_round:
                    await self.page.reload(wait_until="domcontentloaded")
                    first_round = False
                else:
                    # 页面提供“刷新”文本时只刷新预约表，避免完整 reload 破坏日历状态。
                    await self._click_text_variants(["刷新", "重新加载"], 120)
                # Vue/小程序页面通常把可选项渲染为按钮或文本；按可见文本优先匹配。
                await self.page.wait_for_timeout(220)
                if round_no % 5 == 1:
                    self.write(f"第 {round_no} 轮检查可用场地……")
                body_text = await self.page.locator("body").inner_text()
                if "登录状态已过期" in body_text or "请先登录" in body_text:
                    self.write("登录状态已过期，请在浏览器弹窗中点击“确定”并重新登录，然后再开始监控。")
                    return
                if date:
                    await self._select_calendar_date(date)
                await self._refresh_label_cache(times, courts)
                for t in times:
                    t_start = t.split("-")[0]
                    if await self._click_text_variants([t, t_start], 120):
                        self.write(f"已选择时间段 {t}")
                        for c in courts:
                            if await self._click_court_slot(c, t):
                                self.write(f"已点击场地 {c}，正在查找“请选择场地并提交”按钮……")
                                await self._submit_selected_court()
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

    async def _click_court_slot(self, court_label, time_label):
        """按截图中的表格布局，点击场地行与时间列的交叉单元格。"""
        try:
            key = (court_label, time_label)
            row_xy = self._slot_cache.get(("__label__", court_label))
            col_xy = self._slot_cache.get(("__label__", time_label))
            if row_xy and col_xy:
                await self.page.mouse.click(col_xy[0], row_xy[1])
                return True
            if key in self._slot_cache:
                x, y = self._slot_cache[key]
                await self.page.mouse.click(x, y)
                return True
            row = self.page.get_by_text(court_label, exact=True).first
            col = self.page.get_by_text(time_label, exact=True).first
            if not await row.count() or not await col.count():
                return await self._click_text_variants([court_label], 120)
            rb, cb = await row.bounding_box(), await col.bounding_box()
            if rb and cb:
                x = cb["x"] + cb["width"] / 2
                y = rb["y"] + rb["height"] / 2
                self._slot_cache[key] = (x, y)
                await self.page.mouse.click(x, y)
                return True
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
if __name__ == "__main__":
    root = tk.Tk(); BookingApp(root); root.mainloop()
