# -*- coding: utf-8 -*-
"""Deterministic end-to-end test of the API channel against a local mock server.

Proves, with the real _api / _envelope / normalize_detail / cell_state code:
  1. no token   -> reported as "not logged in"
  2. with token -> Authorization: GYMMOBILE <token> is actually sent
  3. the documented payload shape parses into usable cells
"""
import asyncio
import importlib.util
import json
import os
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHROME = r'D:\预约系统\ms-playwright\chromium-1228\chrome-win64\chrome.exe'
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', r'D:\预约系统\ms-playwright')

from playwright.async_api import async_playwright

V2 = r'C:\Users\12513\Documents\New project 3\venue_booking_tool_v2.py'
spec = importlib.util.spec_from_file_location("vbt_v2", V2)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
App = mod.BookingApp

_tomorrow = datetime.now() + timedelta(days=1)
TOMORROW = _tomorrow.strftime('%Y-%m-%d')                       # 界面用的 ISO 格式
TOMORROW_CN = '%02d月%02d日' % (_tomorrow.month, _tomorrow.day)  # 接口返回的中文格式
NOW = datetime.now()

DETAIL = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00", "19:00-20:00"],
    "maxSubscribeNum": 4,
    "sessionObj": {
        "18:00-19:00@@1": {"id": 101, "session_date": TOMORROW, "disabled_time": "17:00:00",
                           "ticket_num": 4, "subscribed_num": 1, "price": 10,
                           "area_name": "1号场地", "session_time": "18:00-19:00"},
        "18:00-19:00@@2": {"id": 102, "session_date": TOMORROW, "disabled_time": "17:00:00",
                           "ticket_num": 4, "subscribed_num": 4, "price": 10,
                           "area_name": "2号场地", "session_time": "18:00-19:00"},
        "19:00-20:00@@3": {"id": 203, "session_date": TOMORROW, "disabled_time": "23:00:00",
                           "ticket_num": 4, "subscribed_num": 0, "price": 10,
                           "area_name": "3号场地", "session_time": "19:00-20:00"},
    },
}
LIST = {"allowTimeRange": ["07:00", "22:00"], "isBan": False,
        # 站点真实返回的是中文日期「09月21日」，不是 ISO —— 2026-09-21 那次 12:00 没开抢
        # 就是因为两边直接比字符串。这里必须照抄真实格式，端到端才守得住这个 bug。
        "scheduleList": [{"id": 77, "schedule_date": TOMORROW_CN, "status": "正常"}]}


def _cell(sid, subscribed):
    """按 mock 的形状造一个场次：ticket_num=4，subscribed_num 决定可约与否。"""
    return {"id": sid, "session_date": TOMORROW, "disabled_time": "23:00:00",
            "ticket_num": 4, "subscribed_num": subscribed, "price": 10}


# 8. 备选顺延：勾 4 片场地，1 号已满、4 号已满 -> 系统只让选 2 个，
#    所以要按顺序顺延到 2 号 + 3 号，而不是死等 1 号。
DETAIL_MULTI = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地", "4@@4号场地"],
    "times": ["18:00-19:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(401, 4),
        "18:00-19:00@@2": _cell(402, 0),
        "18:00-19:00@@3": _cell(403, 1),
        "18:00-19:00@@4": _cell(404, 4),
    },
}

# 9. 只有 1 个可约 -> 到点后按「有几个抢先几个」提交，不空手。
DETAIL_ONLY_ONE = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(501, 0),
        "18:00-19:00@@2": _cell(502, 4),
        "18:00-19:00@@3": _cell(503, 4),
    },
}

# 10. FILL_GRACE_SECONDS = 0 的契约：第 1 轮只有 1 个可约就该立刻下单，
#     哪怕 0.2 秒后第 2 个也会放出来 —— 绝不为凑满 2 个而把到手的等丢。
DETAIL_STEP_ONE = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(601, 0),
        "18:00-19:00@@2": _cell(602, 4),
        "18:00-19:00@@3": _cell(603, 4),
    },
}
DETAIL_STEP_TWO = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(601, 0),
        "18:00-19:00@@2": _cell(602, 0),
        "18:00-19:00@@3": _cell(603, 4),
    },
}

# 11. 12:00 定点筛选：全程订满，用来验证"放号时刻到点一定会有一次明确标注的筛选"
#     而且满场时这一轮也不会乱下单。
DETAIL_ALL_FULL = {
    "areas": ["1@@1号场地", "2@@2号场地"],
    "times": ["18:00-19:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(701, 4),
        "18:00-19:00@@2": _cell(702, 4),
    },
}

# 12. 跨时段：勾 2 个时段 × 3 片场地，18:00 有两片可约、19:00 有一片可约。
#     CHANG 明确要求「保证有两个不同时间段的场地」，所以必须交
#     18:00 的 1 片 + 19:00 的 1 片 —— 而不是把 18:00 那两片一起交上去。
DETAIL_CROSS = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00", "19:00-20:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(801, 0),
        "18:00-19:00@@2": _cell(802, 0),
        "19:00-20:00@@3": _cell(803, 0),
    },
}

# 13. 跨时段的降级：池子覆盖两个时段，但 19:00 一片都没有（压根没放出来）。
#     没得跨就别硬等着只交 1 个，退回同时段 2 个 —— 有几个抢先几个。
DETAIL_CROSS_ONE_SIDE = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00", "19:00-20:00"],
    "maxSubscribeNum": 2,
    "sessionObj": {
        "18:00-19:00@@1": _cell(901, 0),
        "18:00-19:00@@2": _cell(902, 0),
    },
}

RECEIVED = []
SUBMITTED = []
PAGE = b'<html><head><title>mock</title></head><body>mock venue site</body></html>'
TOKEN = 'TESTTOKEN123'
# 让测试能切换"服务器是否强制要 Authorization"，用来验证客户端不再抢答"未登录"
STATE = {'require_auth': True, 'detail': DETAIL, 'detail_seq': None, 'detail_idx': 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        auth = self.headers.get('Authorization')
        RECEIVED.append((self.path, auth))
        if not self.path.startswith('/api/'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
            return
        if STATE['require_auth'] and not auth:
            self._send({"code": 401, "message": "失败",
                        "detail": {"detail": "身份认证信息未提供。"}}, 401)
            return
        if 'schedule/detail' in self.path:
            seq = STATE.get('detail_seq')
            if seq:
                # 按请求次数依次出牌，用来模拟"余量逐轮变化"（同一批放号陆续出现）。
                index = STATE.get('detail_idx', 0)
                body = seq[min(index, len(seq) - 1)]
                STATE['detail_idx'] = index + 1
            else:
                body = STATE.get('detail') or DETAIL
            self._send({"code": 200, "message": "成功", "detail": body})
            return
        if 'schedule/list' in self.path:
            self._send({"code": 200, "message": "成功", "detail": LIST})
            return
        self._send({"code": 404, "message": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length).decode('utf-8', 'replace')
        auth = self.headers.get('Authorization')
        RECEIVED.append((self.path, auth))
        if STATE['require_auth'] and not auth:
            self._send({"code": 401, "message": "失败",
                        "detail": {"detail": "身份认证信息未提供。"}}, 401)
            return
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        SUBMITTED.append(body)
        ids = body.get('sessionIds') or []
        self._send({"code": 200, "message": "成功",
                    "detail": [{"order_id": "ORD%s" % i, "session_id": i} for i in ids]})


RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), extra))


async def main():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = 'http://127.0.0.1:%d/' % port

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, executable_path=CHROME,
                                           args=['--headless=new', '--no-sandbox'])
        page = await browser.new_page()
        await page.goto(base, wait_until='domcontentloaded')
        check("mock page loaded", (await page.title()) == 'mock')

        inst = App.__new__(App)
        inst.page = page

        # ---- 1. no token -> reported as not logged in --------------------
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("no token: token flag False", res.get("token") is False, str(res.get('token')))
        check("no token: not ok", not ok)
        check("no token: code 401", code == 401, str(code))
        check("no token: friendly note", "登录" in note, note)

        # ---- 2a. wrapped token (uni-app 对象形态) -------------------------
        await page.evaluate(
            "(t) => localStorage.setItem('App-Token', JSON.stringify({type:'string', data:t}))",
            TOKEN)
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("2a wrapped token: ok", ok, note)
        check("2a wrapped token: detail returned",
              isinstance(detail, dict) and 'scheduleList' in detail, str(detail)[:80])
        auths = [a for p, a in RECEIVED if a]
        check("2a wrapped token: Authorization header sent",
              auths and auths[-1] == 'GYMMOBILE ' + TOKEN, str(auths[-1:]))
        check("2a correct path+query",
              any('/api/v1/mobile/schedule/list/' in p and 'item=2' in p for p, _a in RECEIVED),
              str([p for p, _a in RECEIVED][-3:]))
        check("2a latency measured", isinstance(res.get("ms"), int) and res["ms"] >= 0, str(res.get('ms')))

        # ---- 2b. RAW string token —— 这就是线上"明明登录了却说未登录"的根因
        # uni-app 的 setStorage 对字符串是**原样存**的，不包 {type,data}。
        # 旧代码对它做 JSON.parse 会抛异常 -> 兜底返回空串 -> 误判未登录。
        JWTISH = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc-DEF_123'
        await page.evaluate("(t) => localStorage.setItem('App-Token', t)", JWTISH)
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("2b raw token: token flag True", res.get("token") is True, str(res.get('token')))
        check("2b raw token: ok", ok, note)
        check("2b raw token: exact header value",
              [a for p, a in RECEIVED if a][-1] == 'GYMMOBILE ' + JWTISH,
              str([a for p, a in RECEIVED if a][-1:]))
        check("2b raw token: key reported", res.get("tokenKey") == 'App-Token', str(res.get('tokenKey')))

        # ---- 2c. 换了键名也要能找到（兜底扫描 token/auth 键） -------------
        await page.evaluate("""() => {
          localStorage.removeItem('App-Token');
          localStorage.setItem('venue_token', '%s');
        }""" % TOKEN)
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("2c alternate key: found", res.get("token") is True, str(res.get('token')))
        check("2c alternate key: reported", res.get("tokenKey") == 'venue_token', str(res.get('tokenKey')))
        check("2c alternate key: header sent",
              [a for p, a in RECEIVED if a][-1] == 'GYMMOBILE ' + TOKEN,
              str([a for p, a in RECEIVED if a][-1:]))

        # ---- 2d. 没有令牌但服务器接受（cookie 会话）-> 不该抢答"未登录" ----
        await page.evaluate("() => { localStorage.clear(); }")
        STATE['require_auth'] = False
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("2d anonymous accepted by server: ok", ok, note)
        check("2d anonymous accepted: token flag False", res.get("token") is False)
        STATE['require_auth'] = True

        # ---- 2e. 没有令牌且服务器 401 -> 明确报未登录，并给出诊断信息 ----
        res = await inst._api(mod.API_LIST, params={"item": mod.API_ITEM_ID})
        ok, code, detail, note = App._envelope(res)
        check("2e anonymous rejected: not ok", not ok)
        check("2e anonymous rejected: code 401", code == 401, str(code))
        check("2e anonymous rejected: mentions 尚未登录", "尚未登录" in note, note)
        check("2e anonymous rejected: carries nested reason", "身份认证" in note, note)

        # ---- 2f. _verify_login 在 2c 的键名下应判定为已登录 ------------
        await page.evaluate("""() => { localStorage.clear();
          localStorage.setItem('App-Token', '%s'); }""" % JWTISH)
        ready, why = await inst._verify_login()
        check("2f login check passes", ready is True, why)
        check("2f login check names the key", 'App-Token' in why, why)
        await page.evaluate("() => { localStorage.clear(); }")
        ready, why = await inst._verify_login()
        check("2f login check fails when server 401s", ready is False, why)

        # 后面几节需要带着登录态跑，把令牌放回去。
        await page.evaluate("(t) => localStorage.setItem('App-Token', t)", TOKEN)

        # ---- 3. detail payload parses into usable cells -----------------
        res = await inst._api(mod.API_DETAIL, params={"item": mod.API_ITEM_ID, "schedule": "77"})
        ok, code, detail, note = App._envelope(res)
        check("detail: ok", ok, note)
        meta = App.normalize_detail(detail)
        check("detail: 3 areas", len(meta["areas"]) == 3, str(len(meta["areas"])))
        check("detail: 2 times", meta["times"] == ["18:00-19:00", "19:00-20:00"], str(meta["times"]))
        check("detail: max_n = 4", meta["max_n"] == 4, str(meta["max_n"]))
        check("detail: available cell", App.cell_state(meta["cells"][("18:00-19:00", "1")], NOW)[0] is True)
        check("detail: full cell blocked", App.cell_state(meta["cells"][("18:00-19:00", "2")], NOW)[1] == "已订满")
        check("detail: absent cell blocked", App.cell_state(meta["cells"].get(("19:00-20:00", "1")), NOW)[0] is False)

        # ---- 4. targets resolve against this payload --------------------
        # 备选池按「时段优先」展开，且**不再**受 maxSubscribeNum 裁剪：
        # 江大系统单次最多选 2 个，多出来的留在池子里顺延备用。
        candidates, notes, limit = App._build_targets(
            inst, meta, ["18:00-19:00", "19:00-20:00"], ["1号场地", "3号场地"])
        check("备选池按「时段优先」展开",
              candidates == [("18:00-19:00", "1"), ("18:00-19:00", "3"),
                             ("19:00-20:00", "1"), ("19:00-20:00", "3")], str(candidates))
        check("单次提交上限 = 2（江大系统上限）", limit == 2 == mod.MAX_PER_ORDER, str(limit))
        ready, available, blocked, quota = App._pick_targets(
            candidates, meta["cells"], NOW, limit)
        check("4 个备选里只有 2 个真可约", len(available) == 2, str(available))
        check("只提交顺序最靠前的 2 个", [i for _k, i in ready] == [101, 203], str(ready))

        # ---- 5. 定点开抢：等待函数真的睡到目标时刻 ----------------------
        LOG = []
        inst.write = lambda msg: LOG.append(msg)
        target = datetime.now() + timedelta(seconds=1.0)
        await inst._sleep_until(target)
        drift = (datetime.now() - target).total_seconds()
        check("sleep_until 不早于目标时刻", drift >= 0, 'drift=%.3fs' % drift)
        check("sleep_until 到点误差 < 0.30s", drift < 0.30, 'drift=%.3fs' % drift)
        check("sleep_until 播报了最后一分钟",
              any('最后 1 分钟' in m for m in LOG), str(LOG[:3]))
        # 已过去的时刻应立即返回
        t0 = time.monotonic()
        await inst._sleep_until(datetime.now() - timedelta(seconds=5))
        check("sleep_until 对已过时刻立即返回", time.monotonic() - t0 < 0.05,
              '%.3fs' % (time.monotonic() - t0))

        # ---- 6. 目标日期不在可约场次里时，绝不静默换天 -------------------
        LOG2 = []
        inst.write = lambda msg: LOG2.append(msg)
        sid, meta = await inst._sync_metadata('2099-01-01')
        check("6 目标日期缺失时不换天", sid is None and meta is None, str((sid, meta)))
        check("6 目标日期缺失时给出说明",
              any('2099-01-01' in m for m in LOG2), str(LOG2))
        check("6 说明里列出当前可约日期（按站点原始格式）",
              any(TOMORROW_CN in m for m in LOG2), str(LOG2))
        sid, meta = await inst._sync_metadata(TOMORROW)
        check("6 存在的日期照常同步", sid == 77 and meta is not None, str(sid))
        LOG2.clear()
        sid, meta = await inst._sync_metadata('2099-01-01', quiet=True)
        check("6 quiet 模式完全不写日志", LOG2 == [] and meta is None, str(LOG2))
        LOG2.clear()
        sid, meta = await inst._sync_metadata(TOMORROW, quiet=True)
        check("6 quiet 模式成功时只报一条汇总",
              len(LOG2) == 1 and '已同步' in LOG2[0] and sid == 77, str(LOG2))

        # ---- 7. 监控主循环端到端：等待 -> 登录自检 -> 同步 -> 命中 -> 下单 ----
        # 把开抢时刻临时挪到 1.2 秒后，这样能把 _monitor 的完整链路跑一遍。
        SUBMITTED.clear()
        LOG3 = []
        inst.write = lambda msg: LOG3.append(msg)
        inst.fast_submit = True
        inst._opening_for = lambda date_text, now=None: datetime.now() + timedelta(seconds=1.2)
        t0 = time.monotonic()
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00'], ['1号场地'], 0.2), timeout=60)
        elapsed = time.monotonic() - t0
        text = '\n'.join(LOG3)

        check("7 真的走到下单接口", len(SUBMITTED) == 1, str(SUBMITTED))
        check("7 提交的是目标场次 id",
              SUBMITTED and SUBMITTED[0].get('sessionIds') == [101], str(SUBMITTED))
        check("7 提交时带了令牌",
              any(a == 'GYMMOBILE ' + TOKEN for p, a in RECEIVED if 'submit' in p),
              str([a for p, a in RECEIVED if 'submit' in p]))
        check("7 等到开抢时刻才动", elapsed >= 1.0, '%.2fs' % elapsed)
        check("7 播报了等待", '还需等待' in text)
        check("7 等待期间做了登录自检", '登录自检通过' in text)
        check("7 到点后开始筛选", '开始筛选可用场地' in text)
        check("7 同步了目标日期（中文日期格式）", '已同步 ' + TOMORROW_CN in text, text[-300:])
        check("7 解析出订单号", 'ORD101' in text, text[-200:])
        check("7 提示去付款", '不会代付' in text)

        # ---- 8. 备选顺延：勾了 4 片，实际只有第 2、3 片可约 ---------------
        # 系统只让选 2 个，所以绝不该死等第 1 片；要顺着备选池往下拿。
        SUBMITTED.clear()
        LOG8 = []
        inst.write = lambda msg: LOG8.append(msg)
        inst.fast_submit = True
        inst._opening_for = lambda date_text, now=None: datetime.now()
        STATE['detail'] = DETAIL_MULTI
        STATE['detail_seq'] = None
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00'],
                          ['1号场地', '2号场地', '3号场地', '4号场地'], 0.2), timeout=30)
        text8 = '\n'.join(LOG8)
        check("8 只提交一次", len(SUBMITTED) == 1, str(SUBMITTED))
        check("8 提交的正好是 2 个（系统上限）",
              SUBMITTED and len(SUBMITTED[0].get('sessionIds') or []) == 2, str(SUBMITTED))
        check("8 满的场地被跳过，顺延到可约的备选",
              SUBMITTED and SUBMITTED[0].get('sessionIds') == [402, 403], str(SUBMITTED))
        check("8 备选池是全量 4 个", '备选池共 4 个单元' in text8, text8[:400])
        check("8 播报已凑齐 2 个", '已凑齐 2 个可约单元' in text8, text8[-400:])
        check("8 下单日志写明提交几个", '本次提交 2 个单元' in text8, text8[-400:])

        # ---- 9. 只有 1 个可约 -> 有几个抢先几个，绝不空手 ---------------
        SUBMITTED.clear()
        LOG9 = []
        inst.write = lambda msg: LOG9.append(msg)
        STATE['detail'] = DETAIL_ONLY_ONE
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00'],
                          ['1号场地', '2号场地', '3号场地'], 0.2), timeout=30)
        text9 = '\n'.join(LOG9)
        check("9 不足 2 个也照样提交", SUBMITTED and SUBMITTED[0].get('sessionIds') == [501],
              str(SUBMITTED))
        check("9 日志说明是按有几个抢先几个", '有几个抢先几个' in text9, text9[-500:])

        # ---- 10. 宽限为 0：见 1 个就下单，不等 0.2 秒后的第 2 个 ----------
        # 第 1 轮只看得到 601，第 2 个 602 稍后放出。既然 CHANG 定了"见可约就抢"，
        # 这里必须提交 [601] 一个，而不是等齐 601+602。
        SUBMITTED.clear()
        LOG10 = []
        inst.write = lambda msg: LOG10.append(msg)
        STATE['detail_seq'] = [DETAIL_STEP_ONE, DETAIL_STEP_ONE, DETAIL_STEP_TWO]
        STATE['detail_idx'] = 0
        check("10 宽限参数确实是 0", mod.FILL_GRACE_SECONDS == 0, str(mod.FILL_GRACE_SECONDS))
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00'],
                          ['1号场地', '2号场地', '3号场地'], 0.2), timeout=30)
        text10 = '\n'.join(LOG10)
        check("10 只提交一次", len(SUBMITTED) == 1, str(SUBMITTED))
        check("10 第一眼看到的那个立刻下单，不等第 2 个",
              SUBMITTED and SUBMITTED[0].get('sessionIds') == [601], str(SUBMITTED))
        check("10 没有为了凑数而等待", '再等' not in text10, text10[:500])
        check("10 日志说明按数量直接提交", '有几个抢先几个' in text10, text10[-400:])
        STATE['detail_seq'] = None
        STATE['detail_idx'] = 0

        # ---- 11. 开跑即到点（12:00 整才开始）：第一轮就是定点筛选 ----------
        # 全程订满（不会下单），把开抢时刻设为"此刻"，于是进主循环第一轮就是定点轮。
        SUBMITTED.clear()
        LOG11 = []
        inst.write = lambda msg: LOG11.append(msg)
        STATE['detail'] = DETAIL_ALL_FULL
        STATE['detail_seq'] = None
        inst._opening_for = lambda d, now=None: datetime.now()
        inst._release_for = lambda d, now=None: datetime.now()
        try:
            await asyncio.wait_for(
                inst._monitor(TOMORROW, ['18:00-19:00'], ['1号场地', '2号场地'], 0.2),
                timeout=6)
        except asyncio.TimeoutError:
            pass          # 全程满场，_monitor 本来就不会自己结束
        text11 = '\n'.join(LOG11)
        check("11 开跑即宣布已到点并给出高频节奏",
              '已到点，立即开始筛选' in text11 and '探测节奏' in text11, text11[:400])
        check("11 第一轮就执行定点筛选", '执行定点筛选' in text11, text11[:800])
        check("11 轮次日志标出了定点筛选", '【12:00 定点筛选】' in text11, text11[-600:])
        check("11 满场时定点筛选不会乱下单", SUBMITTED == [], str(SUBMITTED))
        check("11 满场时轮到第二轮及以后不再标定点",
              text11.count('执行定点筛选') == 1, str(text11.count('执行定点筛选')))

        # ---- 12. 跨时段：两个时段都有货 -> 必须 1+1，不能 2+0 -----------------
        # CHANG 2026-10-06：「场地筛选不对同一时间段的场地进行选择，
        # 而是要保证有两个不同时间段的场地」。
        SUBMITTED.clear()
        LOG12 = []
        inst.write = lambda msg: LOG12.append(msg)
        inst.fast_submit = True
        STATE['detail'] = DETAIL_CROSS
        STATE['detail_seq'] = None
        STATE['detail_idx'] = 0
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00', '19:00-20:00'],
                          ['1号场地', '2号场地', '3号场地'], 0.2), timeout=30)
        text12 = '\n'.join(LOG12)
        check("12 只提交一次", len(SUBMITTED) == 1, str(SUBMITTED))
        check("12 提交的 2 个来自不同时段（18:00 一片 + 19:00 一片）",
              SUBMITTED and SUBMITTED[0].get('sessionIds') == [801, 803], str(SUBMITTED))
        check("12 不会把同一时段的两片一起交上去",
              SUBMITTED and SUBMITTED[0].get('sessionIds') != [801, 802], str(SUBMITTED))
        check("12 轮次日志写明跨时段取", '跨时段取' in text12, text12)
        check("12 开抢前说明了跨时段规则", '从不同时段轮流取' in text12, text12[:600])

        # ---- 13. 只有一个时段有货 -> 没得跨，退回同时段 2 个，不空手 ----------
        SUBMITTED.clear()
        LOG13 = []
        inst.write = lambda msg: LOG13.append(msg)
        STATE['detail'] = DETAIL_CROSS_ONE_SIDE
        STATE['detail_idx'] = 0
        await asyncio.wait_for(
            inst._monitor(TOMORROW, ['18:00-19:00', '19:00-20:00'],
                          ['1号场地', '2号场地', '3号场地'], 0.2), timeout=30)
        text13 = '\n'.join(LOG13)
        check("13 另一个时段没货时退回同时段 2 个",
              SUBMITTED and SUBMITTED[0].get('sessionIds') == [901, 902], str(SUBMITTED))
        check("13 这种降级不打「跨时段取」标记", '跨时段取' not in text13, text13[-600:])

        await browser.close()
    srv.shutdown()


asyncio.run(main())

passed = sum(1 for _n, c, _e in RESULTS if c)
print('=' * 74)
for name, cond, extra in RESULTS:
    print('%-48s %s %s' % (name, 'PASS' if cond else 'FAIL', '' if cond else extra))
print('=' * 74)
print('%d/%d passed' % (passed, len(RESULTS)))
print()
print('requests the mock server received:')
for p, a in RECEIVED:
    print('   %-70s auth=%s' % (p[:70], a))
