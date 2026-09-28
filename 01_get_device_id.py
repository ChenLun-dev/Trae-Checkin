#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01 · 取真实设备号 · 01_get_device_id.py
════════════════════════════════════════════════════════════
【在哪跑】必须在一台**装了 Trae 客户端的机器**上跑。

【干什么】读取客户端本地的 storage.json，把里面注册的真实设备号打印出来。
签到用的 x-device-id 必须是这个号 —— 自动生成的号会被服务端风控直接拒成
9074（同一账号同时刻的对照实测：生成号 → 9074，真实号 → 9095）。

设备号位数不固定：多数机器是 16 位，但也有 15 位的，都能正常用。

【安全】纯读本地文件：**不联网、不解密、不碰任何 token**。

【为什么单文件】它是独立脚本，不依赖本项目其它文件。所以可以直接把这一个
文件发给朋友，让他在自己电脑上跑，把设备号给你。

用法
────────────────────────────────────────────────────────────
  python 01_get_device_id.py            打印本机设备号
  python 01_get_device_id.py --json     机器可读输出

拿到后填进 accounts.json 对应账号的 deviceId：

  {
    "name": "我的账号",
    "uid": "1000000000000001",
    "deviceId": "1234567890123456",     ← 就是这里
    "refreshToken": "..."
  }

⚠️ 两个必须记住的限制
  • 一个设备号**一天只能签一个账号**（限额按设备算，跟账号无关）
  • 3 个账号每天各签一次 = 3 台装过客户端的机器
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DC_PREFIX = "iCubeAuthInfo://icube-dc:"

# ⚠️ 下面这段必须和 trae_core.py 里的同名实现保持一致（本文件是独立单文件，
#    不 import 那个模块，所以只能各存一份）。
#
# 长度**不是**判据。早期版本要求必须 16 位，结果有人 storage.json 里的 Aha 号
# 是 15 位，本脚本直接报"里面没有设备号"，人以为客户端没登录。放宽后那位登录、
# 换 token、签到全部正常 —— 服务端认的是"客户端注册过的那串数字"，不是位数。
DEVICE_ID_MIN_LEN = 12
DEVICE_ID_MAX_LEN = 20


def is_valid_device_id(d):
    """设备号是否像客户端注册过的那串十进制数字（长度不限于 16 位）。"""
    d = str(d or "").strip()
    return d.isdigit() and DEVICE_ID_MIN_LEN <= len(d) <= DEVICE_ID_MAX_LEN


def device_id_from_key(key, value=None):
    """从 storage.json 的键（值）里解析设备号，取不到返回 ""。

    正常情况号在**键名**冒号后面；也认"键名就是 `iCubeAuthInfo://icube-dc`、
    号写在值里"的写法。
    """
    k = str(key or "")
    if k.startswith(DC_PREFIX):
        cand = k[len(DC_PREFIX):].strip()
        return cand if is_valid_device_id(cand) else ""
    if k.rstrip(":") == DC_PREFIX.rstrip(":") and value is not None:
        cand = str(value).strip()
        return cand if is_valid_device_id(cand) else ""
    return ""


def base_dirs():
    home = os.path.expanduser("~")
    cands = [os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"),
             os.path.join(home, "AppData", "Roaming"),
             os.path.join(home, "AppData", "Local")]
    if os.name != "nt":
        cands += [os.path.join(home, "Library", "Application Support"),
                  os.path.join(home, ".config"), home]
    out = []
    for c in cands:
        if c and os.path.isdir(c) and c not in out:
            out.append(c)
    return out


def find_storage_files():
    sub = ("User", "globalStorage", "storage.json")
    known = ("TRAE SOLO CN", "Trae CN", "TRAE SOLO", "Trae", "TraeWork", "TraeWork CN")
    found = []

    def add(p):
        if os.path.isfile(p) and p not in found:
            found.append(p)

    for base in base_dirs():
        for n in known:
            add(os.path.join(base, n, *sub))
        try:
            for entry in sorted(os.listdir(base)):
                if "trae" in entry.lower():
                    add(os.path.join(base, entry, *sub))
        except OSError:
            pass
    return found


def read_storage(path):
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:                            # noqa: BLE001
        return {}


def device_ids_in(storage):
    """设备号写在**键名**里，不需要解密那个值。长度不限 16 位。"""
    out = []
    for k, v in storage.items():
        d = device_id_from_key(k, v)
        if d and d not in out:
            out.append(d)
    return out


def main(argv):
    as_json = "--json" in [a.lower() for a in argv]

    files = find_storage_files()
    if not files:
        print("❌ 没找到 Trae 客户端的 storage.json。")
        print("   请确认这台机器装过 Trae，并且在客户端里**登录过一次**。")
        print("   常见位置：")
        print(r"     %APPDATA%\Trae CN\User\globalStorage\storage.json")
        print(r"     %APPDATA%\TRAE SOLO CN\User\globalStorage\storage.json")
        return 1

    found = []
    for p in files:
        storage = read_storage(p)
        ids = device_ids_in(storage)
        if ids:
            found.append((p, ids, str(storage.get("has_device_id_updated_to_aha") or "")))

    if not found:
        print("❌ 找到了 storage.json，但里面没有设备号。")
        print("   说明客户端还没登录过 —— 先在客户端里登录一次，再跑本脚本。")
        return 1

    if as_json:
        print(json.dumps([{"client": p, "deviceIds": ids, "ahaUpgraded": up}
                          for p, ids, up in found], ensure_ascii=False, indent=2))
        return 0

    print()
    print("=" * 66)
    print("  Trae 真实设备号")
    print("=" * 66)
    for p, ids, up in found:
        print()
        print("  客户端: %s" % p)
        if up:
            print("  已升级为 Aha 号: %s" % up)
        for d in ids:
            print("  >>> 设备号: %s" % d)

    total = sum(len(ids) for _, ids, _ in found)
    if total > 1:
        print()
        print("  ⚠️ 本机有 %d 个设备号（可能装过多个 Trae 客户端）。" % total)
        print("     每个号一天只能签一个账号，别混用。")

    first = found[0][1][0]
    print()
    print("=" * 66)
    print("  填进 accounts.json 对应账号的 deviceId：")
    print()
    print('      "deviceId": "%s"' % first)
    print()
    print("  同一个设备号在别的机器上登录时，也可以直接传给 02_login.py：")
    print()
    print("      python 02_login.py --device-id %s" % first)
    print()
    print("  ⚠️ 一个设备号一天只能签一个账号 —— 多个账号不能共用同一个号。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
