#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae 登录 · trae_login.py（独立脚本）
════════════════════════════════════════════════════════════
在 NAS / 无 Trae 客户端的环境里完成一次浏览器登录，拿到**独立于桌面客户端**
的 refreshToken 会话链，写入 accounts.json 供 trae_checkin.py 使用。

登录实现复用 trae_checkin.py 里的同一份代码（不是复制一份），所以不存在
两套逻辑走岔的问题。trae_login.py 与 trae_checkin.py 需放在同一目录。

用法
────────────────────────────────────────────────────────────
  python trae_login.py                 浏览器登录一次，写入 accounts.json
  python trae_login.py --checkin       登录后立刻签一次
  python trae_login.py --import FILE   把别人的 accounts.json 合并进来
  python trae_login.py --list          只看已有账号，不登录

为什么必须"独立登录"而不是复制 token
────────────────────────────────────────────────────────────
  refreshToken 是轮换链：同一个账号多处登录会产生**多条独立链**，互不影响
  （和一个账号同时登 IDE + 网页版是一回事）。
  会互踢的只有一种情况 —— 把同一份 refreshToken 复制到两个地方，那两条链
  其实是同一条，谁先续期谁就把对方手里那份作废。
  所以 NAS 上请走登录流程，不要从本机 storage.json 复制 token。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import trae_checkin as T
except ImportError:
    print("❌ 找不到 trae_checkin.py，请确认本脚本与它放在同一目录。")
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
        # 设备号非法（UUID / 32 位 hex）会触发 9074，导入时就地修掉
        if not T.is_valid_device_id(a.get("deviceId")):
            a["deviceId"] = T.stable_device_id(uid or str(a.get("refreshToken", ""))[:24] or "imported")
            print("   🔧 %s 的设备号非法，已换成 16 位数字 %s"
                  % (a.get("name") or uid, a["deviceId"]))
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
            flag = "   ⚠️ 设备号非法，会被自动替换"
        else:
            flag = "   （留空，首次签到自动生成）"
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
