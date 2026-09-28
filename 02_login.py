#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
02 · 登录 · 02_login.py
════════════════════════════════════════════════════════════
在跑签到的机器（NAS / 青龙）上完成一次浏览器登录，拿到一条**独立于
Trae 桌面客户端**的 refreshToken 会话链，写进 accounts.json。

实现复用 trae_core.py 里的同一份代码，所以本脚本要和 trae_core.py 放同一目录。

用法
────────────────────────────────────────────────────────────
  python 02_login.py --device-id 1234567890123456   登录（推荐用法）
  python 02_login.py --checkin                      登录后立刻签一次
  python 02_login.py --import FILE                  合并别人的 accounts.json
  python 02_login.py --list                          只看已有账号，不登录

⚠️ 必须传 --device-id
────────────────────────────────────────────────────────────
  签到用的 x-device-id 必须是 Trae 客户端**真实注册的 Aha 号**。
  同账号同时刻的对照实测：
      自动生成的 16 位号 → claim 9074
      客户端真实 Aha 号   → claim 9095（通过设备检查）
  OAuth 登录**不会**把设备号注册成可信设备，所以生成的号注定失败。

  真实号取法：在装了 Trae 客户端的机器上跑 01_get_device_id.py。

⚠️ 一个设备号一天只能签一个账号
────────────────────────────────────────────────────────────
  限额按**设备**算，跟账号无关。3 个账号想每天各签一次，就得有 3 个
  真实设备号（= 3 台装过 Trae 客户端的机器）。

为什么必须"独立登录"而不是复制 token
────────────────────────────────────────────────────────────
  refreshToken 是轮换链：同一个账号多处登录会产生**多条独立链**，互不影响
  （和一个账号同时登 IDE + 网页版是一回事）。
  会互踢的只有一种情况 —— 把同一份 refreshToken 复制到两个地方，那两条链
  其实是同一条，谁先续期谁就把对方手里那份作废。
  所以这里走独立登录流程，**不要**去复制客户端 storage.json 里的 token。

  token 要"独立"，设备号要"真实" —— 两者来源正好相反。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import trae_core as T
except ImportError:
    print("❌ 找不到 trae_core.py，请确认本脚本与它放在同一目录。")
    sys.exit(1)


def import_accounts(path):
    """把别人的 accounts.json 合并进自己的（按 uid 去重）。

    用于"帮别人签到"：对方把他登录后生成的 accounts.json 发给你，
    合并后 trae_checkin.py 就会一起签。
    前提：对方自己**不要**再跑同一份凭据，否则两条轮换链会互踢。
    """
    if not os.path.isfile(path):
        print("❌ 文件不存在: %s" % path)
        return 1
    doc = T.read_json(path, None, warn=True)
    if not isinstance(doc, (dict, list)):
        print("❌ 解析失败: %s" % path)
        return 1
    incoming = doc.get("accounts") if isinstance(doc, dict) else doc
    if not isinstance(incoming, list) or not incoming:
        print("❌ 里面没有 accounts 数组。")
        return 1

    mine = T._read_accounts_doc()
    accounts = mine["accounts"]
    known = {str(a.get("uid")) for a in accounts if isinstance(a, dict) and a.get("uid")}

    added, skipped = [], []
    for a in incoming:
        if not isinstance(a, dict):
            continue
        if not (a.get("refreshToken") or a.get("accessToken")):
            print("   ⏭  跳过无 token 的记录: %s" % (a.get("name") or a.get("uid") or "?"))
            continue
        uid = str(a.get("uid") or "")
        # 设备号必须是客户端真实号（生成号会被 9074 拒）。
        # 非法值就地清空，让人去 01_get_device_id.py 补，而不是编一个假的。
        if a.get("deviceId") and not T.is_valid_device_id(a.get("deviceId")):
            print("   🔧 %s 的设备号格式非法（%s…），已清空"
                  % (a.get("name") or uid, str(a["deviceId"])[:16]))
            a["deviceId"] = ""
        if not a.get("deviceId"):
            print("   ⚠️ %s 还没有设备号 —— 请在对方装了 Trae 的机器上跑"
                  " 01_get_device_id.py，把号填进它的 deviceId" % (a.get("name") or uid))
        if uid and uid in known:
            skipped.append(a.get("name") or uid)
            continue
        if uid:
            known.add(uid)
        accounts.append(a)
        added.append(a.get("name") or uid or "?")

    T.atomic_write_json(T.ACCOUNTS_FILE, mine)
    print()
    print("✅ 合并完成 → %s" % T.ACCOUNTS_FILE)
    if added:
        print("   新增 %d 个：%s" % (len(added), "、".join(added)))
    if skipped:
        print("   跳过 %d 个（UID 已存在）：%s" % (len(skipped), "、".join(skipped)))
    print("   当前共 %d 个账号。" % len(accounts))
    print()
    print("⚠️  请确保对方不再用同一份凭据跑脚本，否则两条轮换链会互踢。")
    return 0


def list_accounts():
    doc = T._read_accounts_doc()
    accounts = [a for a in doc["accounts"] if isinstance(a, dict)]
    if not accounts:
        print("（accounts.json 里还没有账号，先跑 python trae_login.py）")
        return 1
    print()
    print("共 %d 个账号：" % len(accounts))
    for i, a in enumerate(accounts, 1):
        dev = str(a.get("deviceId") or "")
        # 留空是正常用法（首次签到按 uid 自动生成），只有填了非法值才需要警告
        if T.is_valid_device_id(dev):
            flag = ""
        elif dev:
            flag = "   ⚠️ 格式非法，会被忽略"
        else:
            flag = "   ⚠️ 留空会生成，而生成号必被 9074 拒 —— 请填客户端真实 Aha 号"
        print("  %d) %-12s UID %-20s 设备号 %s%s"
              % (i, a.get("name") or "-", a.get("uid") or "-", dev or "-", flag))
    print()
    return 0


def main(argv):
    low = [a.lower() for a in argv]
    if "--import" in low:
        i = low.index("--import")
        if i + 1 >= len(argv):
            print("❌ --import 后面要跟文件路径")
            return 1
        return import_accounts(argv[i + 1])
    if "--list" in low or "-l" in low:
        return list_accounts()
    if any(a in ("-h", "--help", "help") for a in low):
        print(__doc__)
        return 0
    return T.cmd_login(argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
