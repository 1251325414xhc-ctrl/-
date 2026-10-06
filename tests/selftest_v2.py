# -*- coding: utf-8 -*-
"""Offline self-test for the v2 booking core. No network, no browser, no Qt window."""
import importlib.util
import sys
import traceback
from datetime import datetime

V2 = r'C:\Users\12513\Documents\New project 3\venue_booking_tool_v2.py'

spec = importlib.util.spec_from_file_location("vbt_v2", V2)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
App = mod.BookingApp

NOW = datetime(2026, 9, 21, 18, 30, 0)

DETAIL = {
    "areas": ["1@@1号场地", "2@@2号场地", "3@@3号场地"],
    "times": ["18:00-19:00", "19:00-20:00", "20:00-21:00"],
    "maxSubscribeNum": 4,
    "sessionObj": {
        "18:00-19:00@@1": {"id": 101, "session_date": "2026-09-21", "disabled_time": "18:45:00",
                           "ticket_num": 4, "subscribed_num": 1, "price": 10,
                           "area_name": "1号场地", "session_time": "18:00-19:00"},
        "18:00-19:00@@2": {"id": 102, "session_date": "2026-09-21", "disabled_time": "18:45:00",
                           "ticket_num": 4, "subscribed_num": 4, "price": 10,
                           "area_name": "2号场地", "session_time": "18:00-19:00"},
        "18:00-19:00@@3": {"id": 103, "session_date": "2026-09-21", "disabled_time": "18:45:00",
                           "ticket_num": 4, "subscribed_num": 0, "price": 10,
                           "area_name": "3号场地", "session_time": "18:00-19:00"},
        "19:00-20:00@@1": {"id": 201, "session_date": "2026-09-21", "disabled_time": "17:00:00",
                           "ticket_num": 4, "subscribed_num": 0, "price": 10,
                           "area_name": "1号场地", "session_time": "19:00-20:00"},
        "20:00-21:00@@1": {"id": 301, "session_date": "2026-09-21", "disabled_time": "20:45:00",
                           "ticket_num": 4, "subscribed_num": 2, "price": 10,
                           "area_name": "1号场地", "session_time": "20:00-21:00"},
    },
}

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), extra))


def main():
    # ---------- _split_label ----------
    check("split raw code@@name", App._split_label("1@@1号场地") == ("1", "1号场地"))
    check("split plain", App._split_label("18:00-19:00") == ("18:00-19:00", "18:00-19:00"))
    check("split None is safe", App._split_label(None) == ("", ""))

    # ---------- normalize_detail ----------
    meta = App.normalize_detail(DETAIL)
    check("areas parsed", [a["code"] for a in meta["areas"]] == ["1", "2", "3"])
    check("area names kept", [a["name"] for a in meta["areas"]] == ["1号场地", "2号场地", "3号场地"])
    check("times NOT split on @@", meta["times"] == ["18:00-19:00", "19:00-20:00", "20:00-21:00"])
    check("max_n read", meta["max_n"] == 4)
    check("cell keyed by time+code", ("18:00-19:00", "1") in meta["cells"])
    check("cell id correct", meta["cells"][("18:00-19:00", "1")]["id"] == 101)
    check("no stale name-keyed cell", ("18:00-19:00", "1号场地") not in meta["cells"])
    check("normalize tolerates junk", App.normalize_detail({"areas": None, "sessionObj": 5})["cells"] == {})

    # ---------- cell_state ----------
    ok, why = App.cell_state(DETAIL["sessionObj"]["18:00-19:00@@1"], NOW)
    check("available cell", ok and "3" in why, why)
    ok, why = App.cell_state(DETAIL["sessionObj"]["18:00-19:00@@2"], NOW)
    check("full cell blocked", not ok and why == "已订满", why)
    ok, why = App.cell_state(DETAIL["sessionObj"]["19:00-20:00@@1"], NOW)
    check("expired cell blocked", not ok and why == "已过期", why)
    ok, why = App.cell_state(None, NOW)
    check("missing cell blocked", not ok, why)

    # ---------- _best_match ----------
    names = ["1号场地", "2号场地", "3号场地"]
    check("exact match", App._best_match(names, "2号场地") == "2号场地")
    check("floor prefix stripped", App._best_match(["一楼塑胶1", "一楼塑胶2"], "塑胶2") == "一楼塑胶2")
    check("legacy name falls back to ordinal", App._best_match(names, "一楼塑胶1") == "1号场地")
    check("second legacy name -> second slot", App._best_match(names, "一楼塑胶2") == "2号场地")
    check("unknown name -> None", App._best_match(names, "") is None)

    # ---------- _build_targets (instance method, bare instance) ----------
    inst = App.__new__(App)
    candidates, notes, limit = App._build_targets(inst, meta, ["18:00-19:00"], ["1号场地"])
    check("single candidate", candidates == [("18:00-19:00", "1")], str(candidates))
    check("no notes for clean input", notes == [])
    check("单次上限 = 2", limit == 2 == mod.MAX_PER_ORDER, str(limit))

    candidates, notes, limit = App._build_targets(
        inst, meta, ["18:00-19:00", "18:00-19:00"], ["1号场地"])
    check("duplicate combos deduped", candidates == [("18:00-19:00", "1")], str(candidates))

    # 备选池不再被裁掉：勾 3 个时段 × 2 片场地 -> 6 个全留，开抢时只取前 2 个
    candidates, notes, limit = App._build_targets(
        inst, meta, ["18:00-19:00", "19:00-20:00", "20:00-21:00"], ["1号场地", "2号场地"])
    check("备选池全量保留（不再裁剪）", len(candidates) == 6, str(len(candidates)))
    check("池子顺序 = 时段优先", candidates[:4] == [
        ("18:00-19:00", "1"), ("18:00-19:00", "2"),
        ("19:00-20:00", "1"), ("19:00-20:00", "2")], str(candidates))
    check("上限与池子大小无关", limit == 2, str(limit))

    # 接口自己报了更小的上限时以接口为准
    narrow = dict(meta, max_n=1)
    candidates, notes, limit = App._build_targets(inst, narrow, ["18:00-19:00"], ["1号场地"])
    check("接口上限更小则降为 1", limit == 1 and any("最多只能选 1 个" in n for n in notes),
          "%s / %s" % (limit, notes))

    candidates, notes, limit = App._build_targets(inst, meta, ["23:00-24:00"], ["1号场地"])
    check("unknown time skipped with note", candidates == [] and len(notes) == 1, str(notes))

    candidates, notes, limit = App._build_targets(inst, meta, ["18:00-19:00"], ["一楼塑胶3"])
    check("legacy court name still resolves", candidates == [("18:00-19:00", "3")], str(candidates))

    # ---------- _pick_targets：按池子顺序挑出要提交的单元 ----------
    cells = meta["cells"]
    pool = [("18:00-19:00", "1"), ("18:00-19:00", "3"),
            ("19:00-20:00", "1"), ("18:00-19:00", "2")]
    ready, available, blocked, quota = App._pick_targets(pool, cells, NOW, 2)
    check("quota = min(上限, 池子)", quota == 2, str(quota))
    check("只取可约的", [k for k, _i in available] == [
        ("18:00-19:00", "1"), ("18:00-19:00", "3")], str(available))
    check("取池子顺序靠前的 2 个", ready == available[:2], str(ready))
    check("带出 session id", [i for _k, i in ready] == [101, 103], str(ready))
    check("不可约的进 blocked", len(blocked) == 2, str(blocked))

    # 池子头部抢不到时自动顺延到后面的备选
    pool = [("18:00-19:00", "2"), ("18:00-19:00", "3")]
    ready, available, blocked, quota = App._pick_targets(pool, cells, NOW, 2)
    check("头部已满则顺延到下一个备选", [i for _k, i in ready] == [103], str(ready))
    check("没凑满时 quota 仍是 2", quota == 2, str(quota))

    # 池子本身就只有 1 个 -> 那个可约就提交
    ready, available, blocked, quota = App._pick_targets([("18:00-19:00", "1")], cells, NOW, 2)
    check("池子不足上限时 quota 跟着缩小", quota == 1, str(quota))
    check("单个池子照样能锁定", [i for _k, i in ready] == [101], str(ready))

    # 一个都没有
    ready, available, blocked, quota = App._pick_targets(
        [("18:00-19:00", "2"), ("19:00-20:00", "1")], cells, NOW, 2)
    check("全不可约时 ready 为空", ready == [] and available == [] and quota == 2, str(ready))

    # ---------- 跨时段轮转（CHANG 2026-10-06：保证两个不同时段） ----------
    check("跨时段开关默认打开", mod.CROSS_SLOT_PICK is True)

    # 池子覆盖 18:00 和 19:00 两个时段
    cross_pool = [("18:00-19:00", "1"), ("18:00-19:00", "3"), ("19:00-20:00", "1")]
    check("_slot_count 数时段", App._slot_count(cross_pool) == 2, str(App._slot_count(cross_pool)))
    check("_slot_count 空池子 = 0", App._slot_count([]) == 0)
    # 19:00 那片在这个 meta 里已过期 -> 没得跨，退回同时段 2 个（有几个抢先几个）
    ready, available, blocked, quota = App._pick_targets(cross_pool, cells, NOW, 2)
    check("另一时段没货时退回同时段 2 个",
          [k for k, _i in ready] == [("18:00-19:00", "1"), ("18:00-19:00", "3")],
          str(ready))

    # 两个时段都有货 -> 必须 1+1，绝不能交 18:00 的两片上去
    both = dict(cells)
    both[("19:00-20:00", "1")] = dict(cells[("18:00-19:00", "1")], id=201)
    ready, available, blocked, quota = App._pick_targets(cross_pool, both, NOW, 2)
    check("两个时段都有货时取 1+1（跨时段）",
          [k for k, _i in ready] == [("18:00-19:00", "1"), ("19:00-20:00", "1")],
          str(ready))
    check("跨时段后仍然凑满 2 个", len(ready) == 2 and quota == 2, str(ready))
    check("跨时段不影响 available 计数", len(available) == 3, str(available))

    # 三个时段都有货 -> 取最靠前的两个时段各 1 个
    cross3 = cross_pool + [("20:00-21:00", "1")]
    ready, available, blocked, quota = App._pick_targets(cross3, both, NOW, 2)
    check("三个时段时取最靠前的两个时段各 1 个",
          [k for k, _i in ready] == [("18:00-19:00", "1"), ("19:00-20:00", "1")],
          str(ready))

    # 只勾 1 个时段：没有别的时段可跨，照旧同时段取 2 个
    ready, available, blocked, quota = App._pick_targets(
        [("18:00-19:00", "1"), ("18:00-19:00", "3")], cells, NOW, 2)
    check("只勾 1 个时段时同时段取 2 个", len(ready) == 2, str(ready))

    # 轮转顺序本身：时段按第一次出现的先后，时段内保持池子顺序
    picked = App._interleave_slots(
        [(("A", "x1"), 1), (("A", "x2"), 2), (("B", "y1"), 3), (("A", "x3"), 4)], 3)
    check("轮转顺序 = A1 B1 A2", [i for _k, i in picked] == [1, 3, 2], str(picked))
    check("轮转遇上 quota=0 返回空", App._interleave_slots([(("A", "x1"), 1)], 0) == [])

    # _build_targets 要把跨时段规则写进日志说明
    candidates, notes, limit = App._build_targets(
        inst, meta, ["18:00-19:00", "19:00-20:00"], ["1号场地", "2号场地"])
    check("跨时段时给出日志说明",
          any("从不同时段轮流取" in n for n in notes), str(notes))
    candidates, notes, limit = App._build_targets(
        inst, meta, ["18:00-19:00"], ["1号场地", "2号场地"])
    check("单时段不给跨时段说明",
          not any("从不同时段轮流取" in n for n in notes), str(notes))

    # ---------- _envelope ----------
    ok, code, detail, note = App._envelope(
        {"status": 200, "body": {"code": 200, "detail": {"a": 1}}, "token": True})
    check("envelope ok", ok and code == 200 and detail == {"a": 1})
    ok, code, detail, note = App._envelope(
        {"status": 401, "body": {"code": 401, "message": "失败",
                                 "detail": {"detail": "身份认证信息未提供。"}}, "token": True})
    check("envelope 401", (not ok) and code == 401 and "身份认证" in note, note)
    # 关键回归：不再因为"localStorage 里读不到令牌"就抢答未登录，以服务器回答为准。
    ok, code, detail, note = App._envelope({"status": 200, "body": {"code": 200}, "token": False})
    check("no token but server ok -> ok", ok and code == 200, note)
    ok, code, detail, note = App._envelope(
        {"status": 401, "body": {"code": 401, "message": "失败",
                                 "detail": {"detail": "身份认证信息未提供。"}},
         "token": False, "storageKeys": ["App-Token", "__DC_STAT_UUID"]})
    check("no token + 401 -> 尚未登录",
          (not ok) and code == 401 and "尚未登录" in note, note)
    check("no token + 401 -> 附带现有键名", "App-Token" in note, note)
    ok, code, detail, note = App._envelope(
        {"status": 401, "body": {"code": 401, "message": "失败",
                                 "detail": {"detail": "身份认证信息未提供。"}}, "token": True})
    check("stale token + 401 -> 已过期", (not ok) and "已过期" in note, note)
    ok, code, detail, note = App._envelope({"status": -1, "error": "boom", "token": True})
    check("envelope network error", (not ok) and "boom" in note, note)
    ok, code, detail, note = App._envelope(
        {"status": 200, "body": {"code": 500, "message": "服务器内部错误"}, "token": True})
    check("envelope business error", (not ok) and code == 500 and "服务器" in note, note)

    # ---------- 开抢时刻：预约日期当天 12:00 整（CHANG 定：不再提前到 11:59） ----------
    check("开抢时刻 = 预约日期当天 12:00:00",
          App._opening_for("2026-09-21") == datetime(2026, 9, 21, 12, 0, 0),
          str(App._opening_for("2026-09-21")))
    check("开抢时刻不再是 11:59",
          App._opening_for("2026-09-21") != datetime(2026, 9, 21, 11, 59, 0))
    check("非法日期退化为当天 12:00",
          App._opening_for("", datetime(2026, 9, 21, 8, 0)) == datetime(2026, 9, 21, 12, 0, 0),
          str(App._opening_for("", datetime(2026, 9, 21, 8, 0))))
    check("开抢时刻与放号时刻完全重合",
          App._opening_for("2026-09-21") == App._release_for("2026-09-21"),
          "%s vs %s" % (App._opening_for("2026-09-21"), App._release_for("2026-09-21")))

    # ---------- 放号时刻：预约日期当天 12:00（12:00 定点筛选的依据） ----------
    check("放号时刻 = 预约日期当天 12:00",
          App._release_for("2026-09-21") == datetime(2026, 9, 21, 12, 0, 0),
          str(App._release_for("2026-09-21")))
    check("非法日期退化为当天 12:00",
          App._release_for("", datetime(2026, 9, 21, 8, 0)) == datetime(2026, 9, 21, 12, 0, 0),
          str(App._release_for("", datetime(2026, 9, 21, 8, 0))))
    check("开抢后 8 秒内保持高频节奏",
          mod.RELEASE_BURST_SECONDS == 8.0 and mod.PRE_RELEASE_INTERVAL == 0.05,
          "%s / %s" % (mod.RELEASE_BURST_SECONDS, mod.PRE_RELEASE_INTERVAL))

    # ---------- 日期匹配：站点「09月21日」 vs 界面「2026-09-21」 ----------
    # 2026-09-21 那次 12:00 没开抢，根因就是这两个串直接比字符串，永远不相等。
    check("站点中文日期能匹配上 ISO 日期",
          App._date_matches("09月21日", "2026-09-21") is True)
    check("不带前导零的中文日期也能匹配",
          App._date_matches("9月21日", "2026-09-21") is True)
    check("ISO 对 ISO 仍然匹配",
          App._date_matches("2026-09-21", "2026-09-21") is True)
    check("不同日期不匹配",
          App._date_matches("09月22日", "2026-09-21") is False)
    check("同月不同日不匹配",
          App._date_matches("09月21日", "2026-09-20") is False)
    check("跨月（9月21日 vs 10月21日）不匹配",
          App._date_matches("10月21日", "2026-09-21") is False)
    check("两边都带年份且年份不同 -> 不匹配",
          App._date_matches("2025-09-21", "2026-09-21") is False)
    check("空值不参与匹配",
          App._date_matches(None, "2026-09-21") is False
          and App._date_matches("09月21日", "") is False)
    check("解析中文日期得到 (None, 9, 21)",
          App._parse_date_parts("09月21日") == (None, 9, 21),
          str(App._parse_date_parts("09月21日")))
    check("解析 ISO 日期得到 (2026, 9, 21)",
          App._parse_date_parts("2026-09-21") == (2026, 9, 21),
          str(App._parse_date_parts("2026-09-21")))

    # ---------- report ----------
    passed = sum(1 for _n, c, _e in RESULTS if c)
    print('=' * 66)
    for name, cond, extra in RESULTS:
        print('%-42s %s %s' % (name, 'PASS' if cond else 'FAIL', '' if cond else extra))
    print('=' * 66)
    print('%d/%d passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
