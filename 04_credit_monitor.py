#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04 · 积分监控 · 04_credit_monitor.py（只读）
════════════════════════════════════════════════════════════
查询每个账号的积分「总额 / 已用 / 剩余」，推送到企业微信 / PushPlus。
与签到脚本共用 trae_core.py 的账号配置、token 缓存、设备号解析，零第三方依赖。

☑ 只读保证
────────────────────────────────────────────────────────────
  本脚本**只调查询接口，绝不发起 claim**，不会消耗积分、不会影响当天签到，
  也不写当日状态文件（.trae_state.json），所以和 03_checkin.py 随便怎么交叉跑都行。
  唯一的写操作是 token 快过期时续期并落缓存 —— 和签到脚本共用同一份缓存，
  两边看到的永远是同一个 token。

用法
────────────────────────────────────────────────────────────
  python 04_credit_monitor.py            查全部账号并推送
  python 04_credit_monitor.py --local    只看终端输出，不推送

环境变量
────────────────────────────────────────────────────────────
  账号来源：accounts.json（脚本同目录）/ TRAE_ACCOUNTS（与签到脚本完全一致）
  TRAE_ONLY        只看指定账号（序号 / uid / 名字）
  QYWX_TOKEN       企业微信机器人 key（也认 WECHAT_WEBHOOK）
  PLUSPLUS_TOKEN   PushPlus token
  TRAE_WEBHOOK     自定义 webhook
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import trae_core as T
except ImportError:
    print("❌ 找不到 trae_core.py，请确认本脚本与它放在同一目录。")
    sys.exit(1)


def credits_detail(acc, device_id):
    """查询积分明细。返回 (总额, 已用, 剩余)，查不到返回 None。

    优先读 usage_summary（总额 - 已用），这是官方客户端口径；
    拿不到再退回按权益包逐项求和。
    """
    headers = T.ug_headers(acc["accessToken"], device_id, acc.get("region"))
    body = '{"require_usage": true, "full_data": true}'
    status, d, _ = T.http_post(T.EP_ENTITLE, headers, body, 20)
    if isinstance(d, dict) and status < 400:
        root = d.get("data") if isinstance(d.get("data"), dict) else d
        us = T.pick(root, "usage_summary", "usageSummary")
        if isinstance(us, dict):
            total = us.get("total_amount")
            used = us.get("consumed_amount")
            if total is not None and used is not None:
                try:
                    total, used = float(total), float(used)
                    return total, used, round(total - used, 2)
                except (TypeError, ValueError):
                    pass
    remain = T.api_usage(acc, device_id)
    if remain is not None:
        return None, None, remain
    return None


def main(argv=()):
    low = [a.lower() for a in argv]
    if any(a in ("-h", "--help", "help") for a in low):
        print(__doc__)
        return 0
    T.setup_log()
    accounts = T.load_accounts()
    if not accounts:
        print("❌ 没有可用账号。")
        print("   本机（装了 Trae 客户端）：先运行 python trae_get_token.py")
        print("   NAS / 无客户端          ：先运行 python trae_login.py")
        return 1

    print()
    print("=" * 64)
    print("  📊 Trae 积分监控（只读）%s" % T.bj())
    print("  账号数量: %d" % len(accounts))
    print("=" * 64)

    rows = []
    for idx, acc in enumerate(accounts):
        name = acc.get("name") or acc.get("uid") or T._acct_key(acc)
        ok, msg = T.ensure_token(acc)
        if not ok:
            print("  ❌ %s：凭证失效（%s）" % (name, msg))
            rows.append({"name": name, "icon": "❌", "detail": "凭证失效：%s" % msg})
            continue

        device_id, _src = T.resolve_device_id(acc)
        acc["deviceId"] = device_id

        detail = credits_detail(acc, device_id)
        if detail is None:
            # 可能是 token 刚被别处轮换，强刷一次再试
            ok2, _ = T.ensure_token(acc, force=True)
            if ok2:
                device_id, _src = T.resolve_device_id(acc)
                acc["deviceId"] = device_id
                detail = credits_detail(acc, device_id)

        if detail is None:
            print("  ❌ %s：查询失败" % name)
            rows.append({"name": name, "icon": "❌", "detail": "积分查询失败"})
        else:
            total, used, remain = detail
            if total is None:
                line = "剩余 %s" % T._fmt(remain)
            else:
                line = "总额 %s · 已用 %s · 剩余 %s" % (T._fmt(total), T._fmt(used), T._fmt(remain))
            print("  💰 %s：%s" % (name, line))
            rows.append({"name": name, "icon": "💰", "detail": line})

        if idx < len(accounts) - 1:
            time.sleep(3)

    print("=" * 64)

    if "--local" not in [a.lower() for a in argv]:
        out = ["📊 Trae 积分监控", "🕒 %s" % T.bj()]
        for r in rows:
            out.append("%s %s：%s" % (r["icon"], r["name"], r["detail"]))
        T.push("📊 Trae 积分监控", "\n".join(out))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
