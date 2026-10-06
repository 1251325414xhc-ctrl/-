# -*- coding: utf-8 -*-
"""UI + 场地映射自检：构建真实 QtBookingApp，验证界面保持原样且映射逻辑正确。"""
import datetime
import importlib.util
import sys
import traceback

V2 = r'C:\Users\12513\Documents\New project 3\venue_booking_tool_v2.py'

from PySide6.QtWidgets import QApplication, QPushButton

spec = importlib.util.spec_from_file_location("vbt_v2", V2)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), extra))


def areas_from(names):
    """把显示名列表包装成接口形状（code 从 1 开始）。"""
    return [{"raw": "%d@@%s" % (i, n), "code": str(i), "name": n}
            for i, n in enumerate(names, 1)]


def main():
    app = QApplication([])
    win = mod.QtBookingApp()

    # ---- 界面必须保持原样：原来的时段和场地清单，且没有"同步"按钮 ----
    check("界面时段仍是 7 个", len(win.time_vars) == 7, str(len(win.time_vars)))
    check("界面场地仍是 20 片", len(win.court_vars) == 20, str(len(win.court_vars)))
    check("场地键是原来的硬编码名",
          "一楼塑胶1" in win.court_vars and "二楼塑胶12" in win.court_vars,
          str(list(win.court_vars)[:3]))
    buttons = [b.text() for b in win.findChildren(QPushButton)]
    check("界面上没有「同步场地列表」按钮",
          not any("同步" in t for t in buttons), str(buttons))
    check("仍然有「开始智能监控」按钮", any("开始" in t for t in buttons), str(buttons))
    check("极速模式默认开", win.fast_box.isChecked() is True)
    check("探测间隔默认 0.4", win.interval.text() == "0.4", win.interval.text())

    # ---- 接口层与映射层都已绑定到 Qt 类 ----
    for attr in ("_api", "_api_list", "_api_detail", "_api_submit", "_envelope",
                 "_verify_login", "_sleep_until",
                 "normalize_detail", "cell_state", "_split_label", "_best_match",
                 "_floor_key", "_strip_floor", "_venue_order", "_resolve_area",
                 "_build_targets", "_pick_targets", "_lock_targets",
                 "_slot_count", "_interleave_slots",
                 "_sync_metadata", "_fast_submit", "_click_submit",
                 "_dom_click_cell", "_dom_cell_class", "_dom_select_schedule",
                 "_dom_trigger_refresh"):
        check("QtBookingApp.%s 已绑定" % attr, callable(getattr(win, attr, None)))
    check("已删除 sync_venues", not hasattr(win, "sync_venues"))

    # ---- classmethod/staticmethod 描述符在重绑后仍然有效 ----
    check("normalize_detail 可用",
          win.normalize_detail({"areas": ["1@@A"], "times": ["t"]})["areas"][0]["code"] == "1")
    check("_split_label 可用", win._split_label("9@@9号场地") == ("9", "9号场地"))
    check("cell_state 可用",
          win.cell_state({"id": 1, "ticket_num": 2, "subscribed_num": 0},
                         datetime.datetime(2026, 1, 1))[0] is True)
    check("_floor_key 可用", win._floor_key("二楼塑胶5") == "二楼")
    check("_strip_floor 可用", win._strip_floor("一楼塑胶1") == "塑胶1")

    # ---- 界面清单的顺序定义 ----
    order = win._venue_order()
    check("清单共 20 项", len(order) == 20, str(len(order)))
    check("一楼塑胶1 = 第 0 片", order[0] == ("一楼塑胶1", 0, 0), str(order[0]))
    check("一楼木质8 = 第 7 片", order[7] == ("一楼木质8", 7, 7), str(order[7]))
    check("二楼塑胶1 = 整表第 9 片", order[8] == ("二楼塑胶1", 0, 8), str(order[8]))
    check("二楼塑胶5 = 整表第 13 片", order[12] == ("二楼塑胶5", 4, 12), str(order[12]))

    # ---- 场景 A：接口名字与界面完全一致 ----
    same = areas_from([n for _h, _d, names in mod.VENUE_GROUPS for n in names])
    hit, why = win._resolve_area(same, order, "二楼塑胶5")
    check("A 同名直接命中", hit and hit["name"] == "二楼塑胶5" and not why,
          "%s / %s" % (hit and hit["name"], why))

    # ---- 场景 B：接口只回 "N号场地"，无楼层信息 -> 按整表序号 ----
    flat = areas_from(["%d号场地" % i for i in range(1, 21)])
    hit, why = win._resolve_area(flat, order, "一楼塑胶1")
    check("B 一楼塑胶1 -> 1号场地", hit and hit["name"] == "1号场地", str(hit and hit["name"]))
    hit, why = win._resolve_area(flat, order, "二楼塑胶1")
    check("B 二楼塑胶1 -> 9号场地（不是 1 号）", hit and hit["name"] == "9号场地",
          str(hit and hit["name"]))
    hit, why = win._resolve_area(flat, order, "二楼塑胶5")
    check("B 二楼塑胶5 -> 13号场地", hit and hit["name"] == "13号场地", str(hit and hit["name"]))
    check("B 推定有说明可回显", bool(why), why)

    # ---- 场景 C：接口名字带楼层（"一楼1号场地"…）-> 楼层内序号 ----
    floored = areas_from(
        ["一楼%d号场地" % i for i in range(1, 9)] +
        ["二楼%d号场地" % i for i in range(1, 13)])
    hit, why = win._resolve_area(floored, order, "二楼塑胶5")
    check("C 二楼塑胶5 -> 二楼5号场地", hit and hit["name"] == "二楼5号场地",
          str(hit and hit["name"]))
    hit, why = win._resolve_area(floored, order, "一楼木质4")
    check("C 一楼木质4 -> 一楼4号场地", hit and hit["name"] == "一楼4号场地",
          str(hit and hit["name"]))

    # ---- 场景 D：接口名字没有中文楼层词（"2F-3"）-> 回落到整表序号 ----
    spaced = areas_from(["1F-%d" % i for i in range(1, 9)] + ["2F-%d" % i for i in range(1, 13)])
    hit, why = win._resolve_area(spaced, order, "二楼塑胶3")
    check("D 接口名无楼层词时按整表序号", hit and hit["name"] == "2F-3", str(hit and hit["name"]))

    # ---- 回归：跨楼层同名不能再撞到同一片场地 ----
    candidates, notes, limit = win._build_targets(
        {"areas": flat, "times": ["18:00-19:00"], "max_n": 0},
        ["18:00-19:00"], ["一楼塑胶1", "二楼塑胶1"])
    check("回归 一楼1/二楼1 映射到两片不同场地",
          candidates == [("18:00-19:00", "1"), ("18:00-19:00", "9")], str(candidates))

    # ---- 正常选择：多时段多场地，全部进备选池 ----
    candidates, notes, limit = win._build_targets(
        {"areas": flat, "times": ["18:00-19:00", "19:00-20:00"], "max_n": 0},
        ["18:00-19:00", "19:00-20:00"], ["二楼塑胶5", "二楼塑胶6"])
    check("多时段多场地展开为 4 个备选", len(candidates) == 4, str(candidates))
    check("超出单次上限也不裁剪", limit == 2, str(limit))

    # ---- 接口自己报的上限更小 -> 单次提交数跟着缩小，池子依旧全量保留 ----
    candidates, notes, limit = win._build_targets(
        {"areas": flat, "times": ["18:00-19:00", "19:00-20:00"], "max_n": 1},
        ["18:00-19:00", "19:00-20:00"], ["二楼塑胶5", "二楼塑胶6"])
    check("接口上限 1 -> limit=1 且池子不裁剪",
          len(candidates) == 4 and limit == 1, "%d / %s" % (len(candidates), limit))

    # ---- _pick_targets 也必须在 Qt 类上可用（开抢时真正挑 2 个的地方） ----
    cells = {("18:00-19:00", "9"): {"id": 9001, "ticket_num": 4, "subscribed_num": 0},
             ("18:00-19:00", "10"): {"id": 9002, "ticket_num": 4, "subscribed_num": 4}}
    ready, available, blocked, quota = win._pick_targets(
        [("18:00-19:00", "9"), ("18:00-19:00", "10")], cells,
        datetime.datetime(2026, 1, 1), limit=2)
    check("_pick_targets 在 Qt 类上可用", [i for _k, i in ready] == [9001], str(ready))
    check("_pick_targets 分清缺口", quota == 2 and len(available) == 1,
          "%s / %s" % (quota, available))

    # ---- 跨时段：在 Qt 类上也要真的「一个时段取一个」 ----
    cells2 = {("18:00-19:00", "9"): {"id": 9101, "ticket_num": 4, "subscribed_num": 0},
              ("18:00-19:00", "10"): {"id": 9102, "ticket_num": 4, "subscribed_num": 0},
              ("19:00-20:00", "11"): {"id": 9103, "ticket_num": 4, "subscribed_num": 0}}
    ready, available, blocked, quota = win._pick_targets(
        [("18:00-19:00", "9"), ("18:00-19:00", "10"), ("19:00-20:00", "11")],
        cells2, datetime.datetime(2026, 1, 1), limit=2)
    check("跨时段轮转在 Qt 类上生效",
          [i for _k, i in ready] == [9101, 9103], str(ready))
    check("_slot_count 在 Qt 类上可用", win._slot_count([("A", "1"), ("B", "2")]) == 2)
    check("跨时段开关默认打开", mod.CROSS_SLOT_PICK is True)

    # ---- 时段不在场次里要跳过并给出提示 ----
    candidates, notes, limit = win._build_targets(
        {"areas": flat, "times": ["18:00-19:00"], "max_n": 0},
        ["15:00-16:00"], ["二楼塑胶5"])
    check("不存在的时段被跳过并有说明",
          candidates == [] and any("15:00-16:00" in n for n in notes), str(notes))

    # ---- 接口没有场地时要有明确提示 ----
    candidates, notes, limit = win._build_targets(
        {"areas": [], "times": ["18:00-19:00"], "max_n": 0}, ["18:00-19:00"], ["二楼塑胶5"])
    check("空场地表给出说明",
          candidates == [] and any("没有返回任何场地" in n for n in notes), str(notes))

    win.close()
    del win
    app.quit()

    passed = sum(1 for _n, c, _e in RESULTS if c)
    print('=' * 70)
    for name, cond, extra in RESULTS:
        print('%-46s %s %s' % (name, 'PASS' if cond else 'FAIL', '' if cond else extra))
    print('=' * 70)
    print('%d/%d passed' % (passed, len(RESULTS)))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
