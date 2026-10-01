#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03 · 签到 · 03_checkin.py
════════════════════════════════════════════════════════════
每天跑这个。零第三方依赖（纯 Python 标准库），NAS / 青龙都能跑。

用法
────────────────────────────────────────────────────────────
  python 03_checkin.py              单次签到
  python 03_checkin.py --24h        每 24 小时自动签到一次

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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import trae_core as T
except ImportError:
    print("❌ 找不到 trae_core.py，请确认本脚本与它放在同一目录。")
    sys.exit(1)


def run_24h():
    # 以本次启动时间为基准，每隔严格 24 小时执行一轮。
    # 计算绝对目标时间，避免“签到耗时 + sleep(86400)”导致周期越来越长。
    interval = 24 * 60 * 60
    next_run = time.time()
    round_no = 0

    while True:
        round_no += 1
        print("\n" + "=" * 60)
        print("🕒 24 小时定时模式：第 %d 轮签到" % round_no)
        print("=" * 60)

        try:
            T.main()
        except Exception as e:
            print("❌ 本轮签到异常：%s" % e)

        next_run += interval
        wait = max(0, next_run - time.time())
        hours = int(wait // 3600)
        minutes = int((wait % 3600) // 60)
        seconds = int(wait % 60)
        print("⏰ 下一轮将在约 %02d:%02d:%02d 后运行。" % (hours, minutes, seconds))

        # 分段等待，便于 Ctrl+C 中断；如果程序运行超过 24 小时则立即进入下一轮。
        while wait > 0:
            time.sleep(min(wait, 60))
            wait = max(0, next_run - time.time())


if __name__ == "__main__":
    if any(a.lower() in ("-h", "--help", "help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    try:
        if "--24h" in sys.argv[1:]:
            run_24h()
        else:
            sys.exit(T.main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
