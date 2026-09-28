#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03 · 签到 · 03_checkin.py
════════════════════════════════════════════════════════════
每天跑这个。零第三方依赖（纯 Python 标准库），NAS / 青龙都能跑。

用法
────────────────────────────────────────────────────────────
  python 03_checkin.py

行为
────────────────────────────────────────────────────────────
  • 先查状态（免费请求），已签到的不发领取请求
  • 当日已成功的账号，之后运行零请求
  • 遇到 9074 **不重试** —— 那是服务端容量门 / 设备号问题，当场连轰没用，
    记冷却后交给下一轮 cron 补签
  • 结束后按 config.json 的配置推送结果

青龙定时建议（避开整点，实测 00:15~00:45 最好签）
────────────────────────────────────────────────────────────
  23 3 * * *    python3 /ql/data/scripts/trae/03_checkin.py

前提
────────────────────────────────────────────────────────────
  每个账号都要在 accounts.json 里配好 deviceId（客户端真实 Aha 号）。
  没有的话本脚本会直接报「缺设备号」，而不是去签一个注定失败的请求。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import trae_core as T
except ImportError:
    print("❌ 找不到 trae_core.py，请确认本脚本与它放在同一目录。")
    sys.exit(1)


if __name__ == "__main__":
    if any(a.lower() in ("-h", "--help", "help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    try:
        sys.exit(T.main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
