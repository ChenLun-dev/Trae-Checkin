#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae 每日自动签到 · trae_checkin.py
════════════════════════════════════════════════════════════
零第三方依赖（纯 Python 标准库），本机 / NAS / 青龙面板都能直接跑。

用法
────────────────────────────────────────────────────────────
  本机（装了 Trae 客户端）：
      1. python trae_get_token.py      # 从客户端取出凭据，生成 accounts.json
      2. python trae_checkin.py        # 之后每天签到

  NAS / 服务器（没装客户端）：
      1. python trae_login.py          # 浏览器登录一次，生成 accounts.json
      2. python trae_checkin.py

  青龙：脚本放进 /ql/data/scripts/，把 accounts.json 也放进去，
        定时 23 0 * * *（避开整点，实测 00:15~00:45 最好签）。

  其它：python trae_checkin.py login    等价于 trae_login.py
        python trae_checkin.py --help   查看全部说明

账号放哪
────────────────────────────────────────────────────────────
  优先读脚本同目录的 accounts.json（由上面两个脚本生成）。
  装了客户端时，accounts.json 不存在会自动读客户端凭据，免配置。
  青龙也可用环境变量 TRAE_ACCOUNTS（填 accounts.json 的 accounts 数组）。

设置放哪
────────────────────────────────────────────────────────────
  脚本同目录的 config.json，一般不用改；要配推送才动 notify 那三项。
  环境变量优先级更高（青龙可用来覆盖），完整列表见 README。
"""

import base64
import glob
import hashlib
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ── Windows 控制台中文/emoji 不乱码 ──
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ══════════════════ 配置层 ══════════════════
# 取值优先级：环境变量 > config.json > 内置默认值
#   • 本机 / NAS：把设置写进脚本同目录的 config.json，不需要碰环境变量
#   • 青龙：用环境变量即可（环境变量优先，可覆盖 config.json）
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.getenv("TRAE_CONFIG_FILE", "").strip() or os.path.join(HERE, "config.json")


def read_json(path, default=None, warn=False):
    """读 JSON 文件，对用户手写的文件保持宽容。

    记事本、PowerShell（Set-Content / 管道）写出的 JSON 常带 UTF-8 BOM，
    而且可能带**多个**（PowerShell 实测会写两个）。utf-8-sig 只吃掉第一个，
    剩下的会让 json.loads 报 "Unexpected UTF-8 BOM"。所以这里统一把开头的
    BOM 全清掉再解析，避免用户手改配置后静默失效。
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except FileNotFoundError:
        return default
    except OSError as e:
        if warn:
            print("⚠️ [配置] 读取 %s 失败: %s" % (os.path.basename(path), e))
        return default
    text = text.lstrip("\ufeff").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception as e:                       # noqa: BLE001
        if warn:
            print("⚠️ [配置] %s 解析失败，改用默认值: %s"
                  % (os.path.basename(path), e))
        return default


def _load_config():
    d = read_json(CONFIG_FILE, None, warn=True)
    return d if isinstance(d, dict) else {}


CONFIG = _load_config()

# config.json 的键 → 对应的环境变量名
CFG_MAP = {
    "appVersion": "TRAE_APP_VERSION",
    "reqSource": "TRAE_REQ_SOURCE",
    "deviceId": "TRAE_DEVICE_ID",
    "only": "TRAE_ONLY",
    "local": "TRAE_LOCAL",
    "log": "TRAE_LOG",
    "noProxy": "TRAE_NO_PROXY",
    "jitter": "TRAE_JITTER",
    "claimTries": "TRAE_CLAIM_TRIES",
    "cooldownMin": "TRAE_COOLDOWN_MIN",
    "gapMin": "TRAE_GAP_MIN",
    "gapMax": "TRAE_GAP_MAX",
    "refreshMarginH": "TRAE_REFRESH_MARGIN_H",
    "accountsFile": "TRAE_ACCOUNTS_FILE",
    "tokenCache": "TRAE_TOKEN_CACHE",
    "stateFile": "TRAE_STATE_FILE",
    "accountDir": "TRAE_ACCOUNT_DIR",
    "qywxToken": "QYWX_TOKEN",
    "plusplusToken": "PLUSPLUS_TOKEN",
    "webhook": "TRAE_WEBHOOK",
}


NOTIFY_KEYS = ("qywxToken", "plusplusToken", "webhook")


def cfg(key, default=None):
    """读一项配置：环境变量优先，其次 config.json，最后用默认值。

    推送令牌在 config.json 里放在 notify 子对象下（也可平铺在根级）。
    """
    env_name = CFG_MAP.get(key, "")
    if env_name:
        v = os.getenv(env_name, "")
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    v = CONFIG.get(key)
    if v is None and key in NOTIFY_KEYS:
        node = CONFIG.get("notify")
        if isinstance(node, dict):
            v = node.get(key)
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return default
    return v


def cfg_str(key, default=""):
    v = cfg(key, default)
    return str(v).strip() if v is not None else ""


def cfg_int(key, default, lo=0, hi=86400):
    try:
        return max(lo, min(hi, int(str(cfg(key, default)).strip())))
    except Exception:
        return default


def cfg_bool(key, default=False):
    """布尔配置：JSON 里可以写 true/false，也可以写 1/0/yes/on。"""
    v = cfg(key, default)
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return bool(default)


# ══════════════════ 常量 ══════════════════
CLIENT_ID = "en1oxy7wnw8j9n"                     # SOLO 谱系
APP_VERSION = cfg_str("appVersion", "0.1.43") or "0.1.43"
UA = "Trae/%s" % APP_VERSION

UG_HOST = "https://api.trae.cn"
OAUTH_HOST = "https://api.trae.com.cn"
LOGIN_PAGE = "https://www.trae.cn/authorization"
PLUGIN_VERSION = "2.3.62834"
LOGIN_PORT = 18080                            # OAuth 回调端口，官方客户端也用这个段
EP_STATUS = UG_HOST + "/trae/api/v2/ug/checkin_credits/status"
EP_CLAIM = UG_HOST + "/trae/api/v2/ug/checkin_credits/claim"
EP_EXCHANGE = OAUTH_HOST + "/cloudide/api/v3/trae/oauth/ExchangeToken"
EP_USAGE = UG_HOST + "/trae/api/v2/pay/ide_user_ent_usage"
EP_ENTITLE = UG_HOST + "/trae/api/v2/pay/user_current_entitlement_list"

# SOLO 谱系契约：body 必须 {"req_source":2}，空 body 或 1 都会被判 9074
REQ_SOURCE = cfg_int("reqSource", 2, 0, 99)
REQ_BODY = json.dumps({"req_source": REQ_SOURCE}, separators=(",", ":"))

AUTH_FAIL_CODES = (1001, 1002)
RATE_CODE = 9074
ALREADY_CODE = 9095
RATE_HTTP = (429, 500, 502, 503, 504)
CONGESTION_HINTS = ("太多", "拥挤", "繁忙", "忙碌", "稍后", "频繁", "限流",
                    "too many", "busy", "later", "retry", "frequent")
ALREADY_HINTS = ("已签", "已领", "明日", "already", "claimed", "checked")

QYWX_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
PUSHPLUS_URL = "https://www.pushplus.plus/send"

STATE_FILE = cfg_str("stateFile") or os.path.join(HERE, ".trae_state.json")
CACHE_FILE = cfg_str("tokenCache") or os.path.join(HERE, ".trae_token_cache.json")
ACCOUNTS_FILE = cfg_str("accountsFile") or os.path.join(HERE, "accounts.json")
LOG_DIR = os.path.join(HERE, "logs")

STORAGE_KEY = "iCubeAuthInfo://icube.cloudide"
DC_PREFIX = "iCubeAuthInfo://icube-dc:"
HEADER_AES = bytes([0x74, 0x63, 0x05, 0x10, 0x00, 0x00])
HEADER_AES_PRIVATE = bytes([18, 57, 32, 32, 2, 3])
SALT_A = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251,
                124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203,
                84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78,
                8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109, 139, 209, 37])
SALT_B = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95,
                96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239,
                160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97,
                23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33, 12, 125])
SALT_C = bytes([191, 192, 216, 250, 122, 246, 220, 97, 31, 254, 98, 27, 8, 72, 71, 176,
                135, 99, 96, 18, 127, 101, 203, 104, 211, 102, 191, 125, 37, 72, 150, 156,
                51, 229, 121, 35, 17, 153, 141, 177, 110, 131, 150, 128, 172, 255, 254, 6,
                18, 140, 55, 62, 236, 249, 135, 64, 135, 12, 117, 4, 89, 149, 168, 209])
SALT_D = bytes([246, 204, 26, 232, 232, 70, 129, 109, 223, 146, 169, 242, 23, 241, 105, 145,
                50, 196, 165, 42, 254, 120, 3, 54, 244, 207, 209, 85, 53, 6, 138, 106,
                175, 148, 31, 204, 186, 186, 165, 182, 87, 142, 49, 10, 39, 110, 26, 154,
                86, 56, 173, 125, 18, 64, 198, 225, 99, 99, 83, 82, 191, 134, 76, 170])
SALT_AES = bytes(a ^ b for a, b in zip(SALT_A, SALT_B))
SALT_AES_PRIVATE = bytes(a ^ b for a, b in zip(SALT_C, SALT_D))


# ══════════════════ 日志（同时写文件，便于排查 9074） ══════════════════
class _Tee(object):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def setup_log():
    if not cfg_bool("log", True):
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fp = open(os.path.join(LOG_DIR, "checkin_%s.log" % today_str()), "a", encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, fp)
        sys.stderr = _Tee(sys.__stderr__, fp)
    except Exception:
        pass


# ══════════════════ 纯标准库 AES-128-CBC（读桌面端凭据用） ══════════════════
def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _build_sbox():
    inv = [0] * 256
    for i in range(1, 256):
        for j in range(1, 256):
            if _gmul(i, j) == 1:
                inv[i] = j
                break
    sb = [0] * 256
    for i in range(256):
        x = inv[i] if i else 0
        s = x
        for _ in range(4):
            x = ((x << 1) | (x >> 7)) & 0xFF
            s ^= x
        sb[i] = s ^ 0x63
    return sb


SBOX = _build_sbox()
INV = [0] * 256
for _i, _v in enumerate(SBOX):
    INV[_v] = _i
RC = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand(key):
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = w[i - 1][:]
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RC[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return w


def _rk(w, r):
    ws = w[r * 4:r * 4 + 4]
    return [ws[c][k] for c in range(4) for k in range(4)]


def aes128_cbc_decrypt(key, iv, data):
    def addrk(s, k):
        return [s[i] ^ k[i] for i in range(16)]

    def ishift(s):
        return [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
                s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]

    def imix(s):
        o = []
        for c in range(4):
            a = s[c * 4:c * 4 + 4]
            o += [_gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9),
                  _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13),
                  _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11),
                  _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)]
        return o

    w = _expand(key)
    out = b""
    prev = list(iv)
    for off in range(0, len(data) - len(data) % 16, 16):
        blk = list(data[off:off + 16])
        s = addrk(blk, _rk(w, 10))
        for r in range(9, 0, -1):
            s = ishift(s)
            s = [INV[b] for b in s]
            s = addrk(s, _rk(w, r))
            s = imix(s)
        s = ishift(s)
        s = [INV[b] for b in s]
        s = addrk(s, _rk(w, 0))
        out += bytes(a ^ b for a, b in zip(s, prev))
        prev = blk
    return out


def _try_decrypt(buf, salt):
    h = hashlib.sha512(buf[6:38]).digest()
    fh = hashlib.sha512(h + salt).digest()
    plain = aes128_cbc_decrypt(fh[:16], fh[16:32], buf[38:])
    if len(plain) < 64:
        return None, False
    stored, text = plain[:64], plain[64:]
    pad = text[-1] if text else 0
    text = text[:-pad] if 1 <= pad <= 16 else text.rstrip(b"\x00")
    return text, hashlib.sha512(text).digest() == stored


def decrypt_storage_value(b64):
    """解密客户端凭据；两种加密类型都支持，且必须过 SHA-512 校验。"""
    buf = base64.b64decode(b64)
    if len(buf) < 54:
        raise ValueError("凭据串过短，不像加密凭据")
    header = buf[0:6]
    if header == HEADER_AES_PRIVATE:
        order = [("AES_PRIVATE", SALT_AES_PRIVATE), ("AES", SALT_AES)]
    else:
        order = [("AES", SALT_AES), ("AES_PRIVATE", SALT_AES_PRIVATE)]
    last = None
    for name, salt in order:
        try:
            text, ok = _try_decrypt(buf, salt)
        except Exception as e:                   # noqa: BLE001
            last = e
            continue
        if text is None:
            continue
        if ok:
            return text.decode("utf-8", "replace")
        last = ValueError("%s 解密未通过 SHA-512 校验" % name)
    raise ValueError("解密失败：%s（头部=%s）" % (last, header.hex()))


# ══════════════════ 时间与工具 ══════════════════
def bj_now():
    return time.gmtime(time.time() + 8 * 3600)


def bj(fmt="%Y-%m-%d %H:%M:%S"):
    return time.strftime(fmt, bj_now())


def today_str():
    return bj("%Y-%m-%d")


def _fmt(v):
    try:
        return "{:,.2f}".format(float(v)).rstrip("0").rstrip(".") or "0"
    except Exception:
        return str(v)


def _wlen(s):
    n = 0
    for ch in s:
        o = ord(ch)
        n += 2 if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
                   or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60
                   or 0x1F300 <= o <= 0x1FAFF) else 1
    return n


def log_box(lines, top=""):
    W = 54
    print()
    print("╔" + "═" * W + "╗")
    for s in ([top] if top else []) + list(lines):
        body = " " + s
        print("║" + body + " " * max(0, W - _wlen(body)) + "║")
    print("╚" + "═" * W + "╝")


def log_head(title):
    W = 54
    body = " " + title
    print()
    print("┌" + "─" * W + "┐")
    print("│" + body + " " * max(0, W - _wlen(body)) + "│")
    print("└" + "─" * W + "┘")


def _norm_key(s):
    return re.sub(r"[_－\-]", "", str(s)).lower()


def pick(d, *names):
    """宽容取值：忽略大小写与 _/- 差异，兼顾 data 包装。"""
    if not isinstance(d, dict):
        return ""
    wanted = [_norm_key(n) for n in names]
    for src in (d, d.get("data") if isinstance(d.get("data"), dict) else None):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if _norm_key(k) in wanted and v not in (None, ""):
                return v
    return ""


# ══════════════════ HTTP ══════════════════
if cfg_bool("noProxy", True):
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
else:
    _opener = urllib.request.build_opener()


def http_post(url, headers, body="{}", timeout=30):
    """返回 (http_status, 解析后的dict或None, 原始文本)。"""
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        status, text = e.code, text
    except Exception as e:                       # noqa: BLE001
        return -1, None, str(e)
    try:
        return status, json.loads(text), text
    except Exception:
        return status, None, text


def ug_headers(token, device_id, region="CN"):
    """官方客户端的最小头集：多编造版本头反而偏离真实指纹。"""
    h = {"Authorization": "Cloud-IDE-JWT " + token,
         "X-User-Region": region or "CN",
         "Content-Type": "application/json",
         "Accept": "application/json",
         "User-Agent": UA}
    if device_id:
        h["x-device-id"] = device_id
    return h


# ══════════════════ 状态 / 缓存文件 ══════════════════
def atomic_write_json(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def load_json(path, default):
    d = read_json(path)
    return d if isinstance(d, type(default)) else default


def load_state():
    st = load_json(STATE_FILE, {})
    if st.get("date") != today_str():
        st = {"date": today_str(), "done": {}, "cooldown": {}}
    st.setdefault("done", {})
    st.setdefault("cooldown", {})
    return st


def save_state(st):
    try:
        atomic_write_json(STATE_FILE, st)
    except Exception as e:
        print("⚠️ [状态] 写入失败: %s" % e)


def mark_done(key, info):
    st = load_state()
    st["done"][str(key)] = info
    save_state(st)


def set_cooldown(key, minutes):
    st = load_state()
    st["cooldown"][str(key)] = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(time.time() + minutes * 60))
    save_state(st)


def cooldown_left(key):
    until = load_state().get("cooldown", {}).get(str(key)) or ""
    try:
        return max(0, int(time.mktime(time.strptime(until, "%Y-%m-%d %H:%M")) - time.time()))
    except Exception:
        return 0


def _acct_key(acc):
    """账号唯一键：uid > name > refreshToken 前缀。绝不能撞键。"""
    return (str(acc.get("uid") or "").strip()
            or str(acc.get("name") or "").strip()
            or str(acc.get("refreshToken", ""))[:24]
            or "default")


# ══════════════════ 设备号 ══════════════════
def _base_dirs():
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

    for base in _base_dirs():
        for n in known:
            add(os.path.join(base, n, *sub))
        try:
            for entry in sorted(os.listdir(base)):
                if "trae" in entry.lower():
                    add(os.path.join(base, entry, *sub))
        except OSError:
            pass
    return found


def real_device_ids():
    """所有客户端 storage.json 里的真实 16 位 Aha 设备号（去重）。"""
    out = []
    for p in find_storage_files():
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                storage = json.load(fh)
        except Exception:
            continue
        for k in storage:
            if k.startswith(DC_PREFIX):
                d = k[len(DC_PREFIX):]
                if d.isdigit() and len(d) == 16 and d not in out:
                    out.append(d)
    return out


def is_valid_device_id(d):
    """设备号唯一合法的形态：16 位纯十进制数字。

    UUID / GUID / 32 位 hex 会被风控判为"非客户端设备"→ 9074。
    这是几个开源项目唯一没有分歧的结论。
    """
    d = str(d or "").strip()
    return d.isdigit() and len(d) == 16


def stable_device_id(seed):
    """兜底：按账号稳定派生一个 16 位数字号（种子必须稳定，不能每次变）。"""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    return str(int.from_bytes(h[:8], "big") % (10 ** 16 - 10 ** 15) + 10 ** 15)


def resolve_device_id(acc):
    """优先级：合法配置值 > 客户端真实 Aha 号 > 缓存里的号 > 稳定派生号。

    两条铁律：
      1. 配置里给了不合法的设备号（UUID / 32 位 hex）**绝不原样发出去**，
         直接忽略并重新生成 —— trae_work_checkin.py 的 credentials.json
         就是 32 位 hex，照单全收等于白送一个 9074。
      2. **命中 9074 后不要换设备号**。旧脚本换个新号重签，等于换个没被
         信任的号继续撞风控，只会越戳越死。
    """
    real = real_device_ids()
    manual = str(acc.get("deviceId") or "").strip()
    if is_valid_device_id(manual):
        return manual, ("客户端真实号" if manual in real else "配置值")
    if real:
        return real[0], "客户端真实号"

    key = _acct_key(acc)
    cache = load_json(CACHE_FILE, {})
    rec = cache.get(key) or {}
    saved = str(rec.get("deviceId") or "")
    if is_valid_device_id(saved):
        return saved, "缓存"

    new = stable_device_id(key)
    rec["deviceId"] = new
    cache[key] = rec
    try:
        atomic_write_json(CACHE_FILE, cache)
    except Exception:
        pass
    note = "自动生成"
    if manual:
        note += "（配置值 %s 不是 16 位数字，已忽略）" % manual[:32]
    return new, note


# ══════════════════ Token ══════════════════
def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0


def exchange_token(refresh_token, uid=""):
    """用 refreshToken 换 accessToken。返回 (ok, data, msg, hard_fail)。

    hard_fail=True 表示服务端明确拒绝（refreshToken 作废），必须重新登录，
    与网络抖动区分开。
    """
    body = json.dumps({"ClientID": CLIENT_ID, "RefreshToken": refresh_token,
                       "ClientSecret": "-", "UserID": uid or ""})
    status, d, text = http_post(
        EP_EXCHANGE,
        {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA},
        body, 30)
    if status < 0:
        return False, None, "网络错误: %s" % text[:120], False
    if not isinstance(d, dict):
        return False, None, "响应异常 (HTTP %s): %s" % (status, text[:120]), False

    err = ((d.get("ResponseMetadata") or {}).get("Error") or {})
    res = d.get("Result") or d.get("data") or {}
    token = res.get("Token") or res.get("AccessToken") or ""
    if not token:
        return False, None, "服务端拒绝 (HTTP %s) %s %s" % (
            status, err.get("Code", ""), err.get("Message", ""))[:160], True

    exp = res.get("TokenExpireAt") or 0
    try:
        exp = int(exp)
        if exp > 10 ** 12:
            exp //= 1000
    except Exception:
        exp = 0
    if not exp:
        try:
            exp = int(time.time()) + int(res.get("TokenExpireDuration") or 1209600)
        except Exception:
            exp = int(time.time()) + 3600
    return True, {"accessToken": token,
                  "refreshToken": res.get("RefreshToken") or refresh_token,
                  "expiresAt": exp}, "ok", False


def ensure_token(acc, force=False):
    """保证 acc['accessToken'] 可用；必要时用 refreshToken 续期。
    返回 (ok, msg)。续期成功立刻落盘（轮换链断不得）。"""
    margin = cfg_int("refreshMarginH", 2, 0, 24 * 30) * 3600
    at = acc.get("accessToken") or ""
    # 配置里没给有效期时，从 JWT 里读，否则永远不会提前续期
    exp = acc.get("expiresAt") or (jwt_exp(at) if at else 0)
    if at and not force and (not exp or exp - time.time() > margin):
        return True, "accessToken 有效"
    rt = acc.get("refreshToken") or ""
    if not rt:
        return False, "没有 refreshToken，且 accessToken 不可用"
    ok, data, msg, hard = exchange_token(rt, acc.get("uid") or "")
    if not ok:
        # refresh 被拒但旧 accessToken 还没过期 → 继续用旧的
        if at and (not exp or exp > time.time()):
            return True, "续期失败，沿用未过期的 accessToken"
        return False, msg
    acc.update(data)
    if not acc.get("uid"):
        acc["uid"] = jwt_uid(data["accessToken"])
    persist(acc)
    return True, "已续期 accessToken"


def jwt_uid(token):
    if not token or token.count(".") < 1:
        return ""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        obj = json.loads(base64.urlsafe_b64decode(payload))
        data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        for k in ("id", "UserID", "userId", "uid"):
            v = data.get(k) if isinstance(data, dict) else None
            if v:
                return str(v)
    except Exception:
        pass
    return ""


def persist(acc):
    """把轮换后的 token 写回缓存；若该账号来自 accounts.json 也一并回写。"""
    key = _acct_key(acc)
    cache = load_json(CACHE_FILE, {})
    rec = cache.get(key) or {}
    rec.update({"accessToken": acc.get("accessToken", ""),
                "refreshToken": acc.get("refreshToken", ""),
                "expiresAt": acc.get("expiresAt") or 0,
                "deviceId": acc.get("deviceId", ""),
                "uid": acc.get("uid", ""),
                "updatedAt": int(time.time())})
    cache[key] = rec
    try:
        atomic_write_json(CACHE_FILE, cache)
    except Exception as e:
        print("⚠️ [缓存] 写入失败: %s" % e)

    if not os.path.isfile(ACCOUNTS_FILE):
        return
    try:
        doc = read_json(ACCOUNTS_FILE)
        accounts = doc.get("accounts") if isinstance(doc, dict) else doc
        if not isinstance(accounts, list):
            return
        hit = False
        for a in accounts:
            if not isinstance(a, dict):
                continue
            same = (acc.get("uid") and str(a.get("uid")) == str(acc["uid"])) \
                or (a.get("refreshToken") and a["refreshToken"] == acc.get("refreshToken"))
            if same:
                a.update({"accessToken": acc.get("accessToken", ""),
                          "refreshToken": acc.get("refreshToken", ""),
                          "expiresAt": acc.get("expiresAt") or 0,
                          "deviceId": acc.get("deviceId", a.get("deviceId", "")),
                          "uid": acc.get("uid", a.get("uid", ""))})
                hit = True
                break
        if hit:
            atomic_write_json(ACCOUNTS_FILE, doc if isinstance(doc, dict) else {"accounts": accounts})
            print("💾 [回写] 已更新 %s" % os.path.basename(ACCOUNTS_FILE))
    except Exception as e:
        print("⚠️ [回写] accounts.json 失败: %s" % e)


def apply_cache(accounts):
    """用缓存里更新的 token 覆盖静态配置（青龙环境变量是静态的）。"""
    cache = load_json(CACHE_FILE, {})
    if not cache:
        return accounts
    hit = 0
    for a in accounts:
        rec = cache.get(_acct_key(a)) or {}
        if rec.get("accessToken"):
            a["accessToken"] = rec["accessToken"]
            if rec.get("refreshToken"):
                a["refreshToken"] = rec["refreshToken"]
            if rec.get("expiresAt"):
                a["expiresAt"] = rec["expiresAt"]
            hit += 1
    if hit:
        print("🔁 [缓存] 已用上次续期的 token 覆盖 %d 个账号" % hit)
    return accounts


# ══════════════════ 账号来源 ══════════════════
def normalize(raw, source, name=""):
    region = pick(raw, "region", "userRegion")
    # 客户端里 userRegion 是 {"region":"CN"}，直接 str() 会把整个 dict 塞进请求头
    if isinstance(region, dict):
        region = pick(region, "region") or ""
    region = str(region or "").strip().upper()
    if not re.match(r"^[A-Z]{2}(-[A-Z0-9]+)?$", region):
        region = "CN"
    return {"accessToken": str(pick(raw, "accessToken", "token", "access_token") or ""),
            "refreshToken": str(pick(raw, "refreshToken", "refresh_token") or ""),
            "deviceId": str(pick(raw, "deviceId", "device_id") or ""),
            "uid": str(pick(raw, "uid", "userId", "user_id", "UserID") or ""),
            "region": region,
            "name": str(pick(raw, "name", "nickname", "screenName", "username") or name or ""),
            "source": source}


def _accounts_from(data, source):
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        try:
            out.append(normalize(item, source, "账号%d" % (i + 1)))
        except Exception as e:                   # noqa: BLE001
            print("❌ [配置] %s 账号%d 解析失败: %s" % (source, i + 1, e))
    return out


def from_env_accounts():
    raw = os.getenv("TRAE_ACCOUNTS", "").strip()
    if not raw:
        return []
    try:
        return _accounts_from(json.loads(raw), "TRAE_ACCOUNTS")
    except Exception as e:                       # noqa: BLE001
        print("❌ [配置] TRAE_ACCOUNTS 不是合法 JSON: %s" % e)
        return []


def from_accounts_file():
    out = []
    paths = [ACCOUNTS_FILE]
    acc_dir = cfg_str("accountDir")
    if acc_dir and os.path.isdir(acc_dir):
        paths += sorted(glob.glob(os.path.join(acc_dir, "trae-*.json")))
    for p in paths:
        if not os.path.isfile(p):
            continue
        doc = read_json(p)
        if doc is None:
            continue
        items = doc.get("accounts") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if isinstance(item, dict):
                out.append(normalize(item, os.path.basename(p), "账号%d" % (i + 1)))
    return out


def from_local_storage():
    """直接读本机 Trae 客户端的凭据（免配置，本机推荐）。"""
    out = []
    for p in find_storage_files():
        try:
            with open(p, "r", encoding="utf-8-sig", errors="replace") as fh:
                storage = json.load(fh)
            enc = storage.get(STORAGE_KEY)
            if not enc:
                continue
            if isinstance(enc, dict):
                auth = enc
            else:
                enc = str(enc).strip()
                auth = json.loads(enc) if enc.startswith("{") else json.loads(decrypt_storage_value(enc))
            acc = normalize(auth, os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(p)))) or "本机客户端")
            if not acc.get("deviceId"):
                for k in storage:
                    if k.startswith(DC_PREFIX) and k[len(DC_PREFIX):].isdigit() \
                            and len(k[len(DC_PREFIX):]) == 16:
                        acc["deviceId"] = k[len(DC_PREFIX):]
                        break
            if acc.get("refreshToken") or acc.get("accessToken"):
                out.append(acc)
        except Exception as e:                   # noqa: BLE001
            print("⚠️ [本机] 读取 %s 失败: %s" % (p, e))
    return out


def filter_only(accounts):
    only = cfg_str("only")
    if not only:
        return accounts
    picked = [a for i, a in enumerate(accounts)
              if only in (str(i + 1), str(a.get("uid")), str(a.get("name")))]
    if picked:
        print("🎯 [筛选] TRAE_ONLY=%s -> 仅处理 %d 个账号" % (only, len(picked)))
        return picked
    print("⚠️ [筛选] TRAE_ONLY=%s 未匹配，处理全部" % only)
    return accounts


def load_accounts():
    accounts = from_env_accounts() + from_accounts_file()
    local_flag = cfg_str("local").lower()
    if local_flag == "1" or (local_flag != "0" and not accounts):
        local = from_local_storage()
        if local:
            print("🖥  [本机] 从 Trae 客户端读到 %d 个账号" % len(local))
        accounts += local

    # 去重（同一个账号可能来自多个来源）
    uniq, seen = [], set()
    for a in accounts:
        if not (a.get("refreshToken") or a.get("accessToken")):
            continue
        k = a.get("uid") or a.get("refreshToken", "")[:24] or a.get("accessToken", "")[:24]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)

    # 全局兜底设备号：config.json / TRAE_DEVICE_ID 给了就补给没填的账号
    default_dev = cfg_str("deviceId")
    if default_dev:
        filled = 0
        for a in uniq:
            if not a.get("deviceId"):
                a["deviceId"] = default_dev
                filled += 1
        if filled:
            print("📱 [设备号] 用配置里的默认设备号 %s 补齐 %d 个账号" % (default_dev, filled))
    return filter_only(apply_cache(uniq))


# ══════════════════ OAuth 登录（独立会话链） ══════════════════
def gen_device_id():
    """16 位纯十进制数字设备号。

    NAS 上没有 Trae 客户端、也就没有真实 Aha 号，只能自己编一个 —— 这没问题，
    服务端校验的是"16 位纯数字"这个格式，不是这个号它认不认得
    （trae-signin-gui 全程随机生成、从不读 storage.json，9074 照样消失）。
    但**绝不能是 UUID / 32 位 hex**，且生成后**固定不变，永不轮换**。
    """
    return str(secrets.randbelow(9 * 10 ** 15) + 10 ** 15)


def gen_machine_id():
    """机器号，仅用于拼登录链接，不进任何签到请求头。"""
    return secrets.token_hex(16)


def port_in_use(port=LOGIN_PORT, host="127.0.0.1", timeout=0.6):
    """回调端口是否已被别的程序监听。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def port_owner(port=LOGIN_PORT):
    """尽力查占用端口的进程 PID；查不到返回 ''（不影响主流程）。"""
    try:
        cmd = ["netstat", "-ano"] if os.name == "nt" else ["ss", "-lptn"]
        r = subprocess.run(cmd, capture_output=True, timeout=8)
        text = (r.stdout + r.stderr).decode("utf-8", "replace")
        for line in text.splitlines():
            if (":%d" % port) not in line:
                continue
            if "LISTEN" not in line.upper():
                continue
            m = re.search(r"pid=(\d+)", line)          # ss: users:(("x",pid=123,...))
            if m:
                return m.group(1)
            parts = line.split()                        # netstat: ... LISTENING <pid>
            if len(parts) > 1 and parts[-1].isdigit():
                return parts[-1]
    except Exception:
        pass
    return ""


def build_login_url(machine_id, device_id, port=LOGIN_PORT):
    params = {
        "login_version": "1",
        "auth_from": "solo",
        "login_channel": "native_ide",
        "plugin_version": PLUGIN_VERSION,
        "auth_type": "local",
        "client_id": CLIENT_ID,
        "redirect": "0",
        "login_trace_id": secrets.token_hex(8),
        "auth_callback_url": "http://127.0.0.1:%d/authorize" % port,
        "machine_id": machine_id,
        "device_id": device_id,
        "x_device_id": device_id,
        "x_machine_id": machine_id,
        "x_device_brand": "PC",
        "x_device_type": "PC",
        "x_os_version": "1.0",
        "x_app_version": APP_VERSION,
        "x_app_type": "stable",
    }
    return LOGIN_PAGE + "?" + urllib.parse.urlencode(params)


def _json_param(raw):
    if not raw:
        return {}
    for val in (raw, urllib.parse.unquote(raw)):
        try:
            o = json.loads(val)
            if isinstance(o, dict):
                return o
        except Exception:
            continue
    return {}


def parse_callback_url(url):
    """解析登录回调链接（实测含 refreshToken / data / userInfo / userJwt）。"""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    def one(k):
        v = qs.get(k) or [""]
        return v[0]

    ui = _json_param(one("userInfo"))
    uj = _json_param(one("userJwt"))
    rt = one("refreshToken") or one("refresh_token") or one("data") \
        or str(uj.get("RefreshToken") or "")
    region = ""
    ur = _json_param(one("userRegion")) or ui.get("userRegion")
    if isinstance(ur, dict):
        region = str(ur.get("region") or "")
    return {"refreshToken": rt,
            "accessToken": str(uj.get("Token") or ""),
            "uid": str(ui.get("UserID") or uj.get("UserID") or ""),
            "nickname": str(ui.get("ScreenName") or ""),
            "region": region}


def get_user_info(access_token):
    """补 uid / 昵称；失败不影响登录（uid 还能从 JWT 里取）。"""
    try:
        _, d, _ = http_post(
            OAUTH_HOST + "/cloudide/api/v3/trae/GetUserInfo",
            {"Content-Type": "application/json",
             "x-cloudide-token": access_token, "User-Agent": UA},
            json.dumps({"ReqSource": "IDE", "IDEVersion": APP_VERSION}), 15)
        res = (d or {}).get("Result") or d or {}
        return str(res.get("UserID") or ""), str(res.get("ScreenName") or "")
    except Exception:
        return "", ""


def _read_accounts_doc():
    doc = read_json(ACCOUNTS_FILE, None, warn=True)
    if not isinstance(doc, (dict, list)):
        return {"accounts": []}
    if isinstance(doc, dict) and isinstance(doc.get("accounts"), list):
        return doc
    if isinstance(doc, list):
        return {"accounts": doc}
    return {"accounts": []}


def upsert_account(account):
    """按 uid 写入 accounts.json（已存在则覆盖），返回 (路径, 是否新增)。"""
    doc = _read_accounts_doc()
    accounts = doc["accounts"]
    uid = str(account.get("uid") or "")
    for i, a in enumerate(accounts):
        if isinstance(a, dict) and uid and str(a.get("uid")) == uid:
            accounts[i] = account
            atomic_write_json(ACCOUNTS_FILE, doc)
            return ACCOUNTS_FILE, False
    accounts.append(account)
    atomic_write_json(ACCOUNTS_FILE, doc)
    return ACCOUNTS_FILE, True


def cmd_login(argv):
    """交互式 OAuth 登录：拿一条**独立于桌面客户端**的 refreshToken 链。

    关键：这样 NAS 和本机 Trae 各有一条会话链，互不干扰。
    如果反过来把本机 storage.json 里的 token 复制到 NAS，两边会
    互相把对方踢下线 —— 那是共用同一条轮换链，不是账号级别的限制。
    """
    setup_log()
    print()
    print("=" * 66)
    print("  Trae 登录（NAS / 无客户端环境专用）")
    print("=" * 66)
    print()
    print("  这条登录会产生一条**新的独立会话链**，与你本机 Trae 客户端")
    print("  那条互不干扰 —— 两边可以同时在线、互不影响。")
    print()
    print("  步骤：")
    print("    1. 在浏览器打开下面的链接，用手机号 / 验证码登录")
    print("    2. 登录后会跳到一个打不开的 127.0.0.1 地址（正常现象）")
    print("    3. 复制浏览器**地址栏里的完整链接**，粘贴回这里")
    print()

    device_id = gen_device_id()
    machine_id = gen_machine_id()
    url = build_login_url(machine_id, device_id)

    # 回调端口被占是最常见的"登录卡住"原因：浏览器把 token 发给那个程序，
    # 脚本收不到，页面就一直转圈。开跑前先探一次，把 PID 直接报出来。
    port_busy = port_in_use()
    if port_busy:
        pid = port_owner()
        print()
        print("⚠️  回调端口 %d 已被占用%s" % (LOGIN_PORT, ("（PID %s）" % pid) if pid else ""))
        print("   登录产生的 token 会被那个程序接走，脚本收不到，浏览器会一直转圈。")
        print("   除非那是你自己起的监听，否则请先结束它：")
        if pid:
            print("     taskkill /F /PID %s" % pid)
        print("     netstat -ano | findstr :%d    # 复查" % LOGIN_PORT)
        print()

    print("  登录链接（整条复制到浏览器打开）：")
    print()
    print(url)
    print()
    print("  正常流程：")
    print("    1. 浏览器打开上面的链接 → 手机号 + 验证码登录 → 点「登录并使用 Trae」")
    print("    2. 地址栏变成这样的才算成功：")
    print("         http://127.0.0.1:%d/authorize?refreshToken=..." % LOGIN_PORT)
    print("       页面显示「无法访问此网站」是**对的** —— 看地址栏，别看页面")
    print("    3. 把地址栏那条链接整条复制下来，粘回下面")
    print()
    print("  卡住了（一直「认证中」、地址栏没变成上面那样）？")
    if port_busy:
        print("    → 就是上面提示的端口被占，先按那里的命令结束占用进程")
        print("      然后 Ctrl+C 重跑一次，拿新链接重新登录")
    else:
        print("    → 多半是 %d 端口被别的程序占了，token 被它接走了。" % LOGIN_PORT)
        print("      先查一下：")
        print("        netstat -ano | findstr :%d" % LOGIN_PORT)
        print("      看到 LISTENING 就记下最后一列的 PID，结束它：")
        print("        taskkill /F /PID <填PID>")
        print("      常见占用者是上次没关掉的命令行窗口，也可能是 Trae 客户端本身。")
        print("      结束后 Ctrl+C 重跑一次，拿新链接重新登录。")
    print("    • 链接有时效，别放太久；过期就 Ctrl+C 重跑一次")
    print()

    # 回车（空输入）只是没粘上，重新要一次；只有 Ctrl+C / 输入流结束才取消
    callback = ""
    while not callback:
        sys.stdout.flush()
        try:
            raw = input("  粘贴回调链接（Ctrl+C 取消）: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
        # 只保留可打印 ASCII：粘贴时常见的隐形字符（BOM、零宽空格、NBSP）和
        # PowerShell 管道塞进来的 UTF-8 BOM 会全被清掉。用白名单而不是逐个黑名单
        # —— BOM 在不同解码下会变成 '\ufeff' 或 'ï»¿'，黑名单清不干净。
        # 不清的话，"空输入"会被当成真 token 发出真实请求。
        callback = re.sub(r"[^\x20-\x7e]", "", raw).strip()
        if not callback:
            print("  没读到内容，请再粘一次（要取消请按 Ctrl+C）")

    # 容错：用户可能直接粘贴裸 token 而不是完整链接。
    # 注意不能用 "有没有 =" 判断 —— refreshToken 本身就带 base64 的 = 和点号，
    # 所以只在看起来像 URL / 回调地址时才走解析，否则整串当 token。
    looks_like_url = callback.lower().startswith(("http://", "https://")) or "authorize" in callback
    info = parse_callback_url(callback) if looks_like_url else {}
    if not info.get("refreshToken"):
        info = {"refreshToken": callback, "accessToken": "", "uid": "",
                "nickname": "", "region": ""}

    rt = info["refreshToken"]
    if not rt:
        print("\n❌ 回调里没找到 refreshToken。")
        print("   请确认复制的是浏览器地址栏的**完整**链接。")
        return 1
    # 发出真实请求前的最后一道闸门：明显不是 token 就不要去打扰服务端
    if len(rt) < 12 or re.search(r"\s", rt):
        print("\n❌ 取到的 refreshToken 看起来不对（长度 %d）。" % len(rt))
        print("   请确认复制的是浏览器地址栏的**完整**链接。")
        return 1

    print("\n  正在用 refreshToken 换取 accessToken ...")
    ok, data, msg, _ = exchange_token(rt, info["uid"])
    if not ok:
        print("❌ 换 token 失败：%s" % msg)
        return 1

    access_token, refresh_token = data["accessToken"], data["refreshToken"]
    expires_at = data["expiresAt"]
    uid = info["uid"] or jwt_uid(access_token)
    gid, gname = get_user_info(access_token)
    uid = uid or gid
    nickname = info["nickname"] or gname

    account = {"name": nickname or ("账号%d" % (len(_read_accounts_doc()["accounts"]) + 1)),
               "uid": uid,
               "deviceId": device_id,
               "region": info["region"] or "CN",
               "accessToken": access_token,
               "refreshToken": refresh_token,
               "expiresAt": expires_at,
               "source": "oauth-login"}

    path, is_new = upsert_account(account)
    persist(account)

    print()
    print("=" * 66)
    print("  ✅ 登录成功")
    print("=" * 66)
    print("  账号      : %s" % (nickname or "-"))
    print("  UID       : %s" % (uid or "-"))
    print("  设备号    : %s（16 位数字，已固定，永不轮换）" % device_id)
    if expires_at:
        print("  有效期至  : %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(expires_at)))
    print("  凭证文件  : %s%s" % (path, "（新增）" if is_new else "（已覆盖同 UID 的旧记录）"))
    print()
    print("  青龙部署：把 %s 的内容整段填进环境变量 TRAE_ACCOUNTS。" % os.path.basename(path))
    print("  本机直接跑：python trae_checkin.py")
    print("=" * 66)

    if "--checkin" in [a.lower() for a in argv]:
        print()
        return main()
    return 0


# ══════════════════ 签到接口 ══════════════════
def api_status(acc, device_id):
    """返回 (ok, checked_in, credits, enable, code, raw)。"""
    status, d, text = http_post(EP_STATUS, ug_headers(acc["accessToken"], device_id,
                                                      acc.get("region")), REQ_BODY)
    print("   ↳ status HTTP %s: %s" % (status, text[:200]))
    if not isinstance(d, dict):
        return False, None, None, None, -1, text
    code = d.get("code", 0)
    if status >= 400:
        return False, None, None, None, code, text
    root = d.get("data") if isinstance(d.get("data"), dict) else d
    checked = pick(root, "checked_in", "checkedIn")
    enable = pick(root, "enable")
    credits = pick(root, "credits")
    return True, bool(checked), credits, enable if enable == "" else bool(enable), code, text


def api_claim(acc, device_id):
    status, d, text = http_post(EP_CLAIM, ug_headers(acc["accessToken"], device_id,
                                                     acc.get("region")), REQ_BODY)
    print("   ↳ claim  HTTP %s: %s" % (status, text[:200]))
    if not isinstance(d, dict):
        return status, None, text, None
    code = d.get("code", -1)
    msg = str(d.get("message") or "")
    root = d.get("data") if isinstance(d.get("data"), dict) else d
    gained = pick(root, "points", "credits")
    return status, code, msg, gained


def is_rate(code, status, msg):
    if code == RATE_CODE or status in RATE_HTTP:
        return True
    low = (msg or "").lower()
    return any(h in low for h in CONGESTION_HINTS)


def is_already(code, msg):
    if code == ALREADY_CODE:
        return True
    low = (msg or "").lower()
    return any(h in low for h in ALREADY_HINTS)


def api_usage(acc, device_id):
    """查积分余额：先 ide_user_ent_usage，不行再 user_current_entitlement_list。"""
    headers = ug_headers(acc["accessToken"], device_id, acc.get("region"))
    for url, body in ((EP_USAGE, "{}"), (EP_ENTITLE, '{"require_usage": true}')):
        status, d, text = http_post(url, headers, body, 15)
        if status >= 400 or not isinstance(d, dict):
            continue
        root = d.get("data") if isinstance(d.get("data"), dict) else d
        packs = pick(root, "user_entitlement_pack_list", "userEntitlementPackList")
        if isinstance(packs, list) and packs:
            total = 0.0
            for p in packs:
                if not isinstance(p, dict):
                    continue
                quota = p.get("entitlement_base_info") or p
                limit = pick(quota, "credits_limit", "creditsLimit") or 0
                used = pick(p.get("usage") or {}, "credits_amount", "creditsAmount") or 0
                try:
                    total += max(float(limit) - float(used), 0.0)
                except Exception:
                    pass
            if total > 0:
                return total
        us = pick(root, "usage_summary", "usageSummary")
        if isinstance(us, dict):
            try:
                total = float(us.get("total_amount") or 0)
                used = float(us.get("consumed_amount") or 0)
                if total > 0:
                    return total - used
            except Exception:
                pass
    return None


# ══════════════════ 单个账号 ══════════════════
def run_account(acc, state):
    name = acc.get("name") or acc.get("uid") or _acct_key(acc)
    res = {"name": name, "uid": acc.get("uid") or "-", "icon": "❌",
           "status": "失败", "credits": "-", "detail": ""}

    ok, msg = ensure_token(acc)
    if not ok:
        res.update(icon="🔑", status="鉴权失败",
                   detail="%s —— 需重新取 refreshToken（本机客户端重新登录一次，或重跑 trae_get_token.py）" % msg)
        return res

    device_id, dev_src = resolve_device_id(acc)
    acc["deviceId"] = device_id
    print("📱 [设备号] %s（%s）" % (device_id, dev_src))
    if not (device_id.isdigit() and len(device_id) == 16):
        print("   ⚠️ 设备号不是 16 位数字，9074 风险高")

    log_head("📡 %s 签到中" % name)

    ok, checked, credits, enable, code, raw = api_status(acc, device_id)
    if not ok or code in AUTH_FAIL_CODES:
        print("🔑 [续期] 鉴权失败(code=%s)，刷新 token 后重试..." % code)
        rok, rmsg = ensure_token(acc, force=True)
        if not rok:
            res.update(icon="🔑", status="鉴权失败", detail=rmsg)
            return res
        ok, checked, credits, enable, code, raw = api_status(acc, device_id)

    if not ok:
        res.update(icon="⏳", status="待重试", detail="状态查询失败：%s" % (raw or "")[:80])
        return res
    if code in AUTH_FAIL_CODES:
        res.update(icon="🔑", status="鉴权失败",
                   detail="token 无效且续期失败，需重新取 refreshToken")
        return res

    if checked:
        pts = api_usage(acc, device_id)
        res.update(icon="☑️", status="已签到",
                   credits=_fmt(pts) if pts is not None else _fmt(credits),
                   detail="今日已签到，积分余额 %s" % (_fmt(pts) if pts is not None else _fmt(credits)))
        return res

    if enable is False:
        res.update(icon="🚫", status="未开放", detail="该账号未开放签到(enable=false)")
        return res

    tries = cfg_int("claimTries", 3, 1, 5)
    for attempt in range(1, tries + 1):
        status, code, msg, gained = api_claim(acc, device_id)

        if code == 0:
            pts = api_usage(acc, device_id)
            tail = "，本次 +%s" % _fmt(gained) if gained else ""
            res.update(icon="✅", status="签到成功",
                       credits=_fmt(pts) if pts is not None else _fmt(gained or credits),
                       detail="签到成功%s，当前余额 %s" % (tail, _fmt(pts) if pts is not None else "-"))
            return res

        if is_already(code, msg):
            res.update(icon="☑️", status="已签到", credits=_fmt(credits),
                       detail="今日已签到(%s)" % (msg or code))
            return res

        if code in AUTH_FAIL_CODES:
            rok, rmsg = ensure_token(acc, force=True)
            if not rok:
                res.update(icon="🔑", status="鉴权失败", detail=rmsg)
                return res
            continue

        if is_rate(code, status, msg):
            if attempt < tries:
                wait = 20 * attempt + random.randint(0, 8)
                print("⏳ [限流] code=%s，%ds 后第 %d 次重试" % (code, wait, attempt + 1))
                time.sleep(wait)
                continue
            cd = cfg_int("cooldownMin", 30, 1, 720)
            set_cooldown(_acct_key(acc), cd)
            res.update(icon="⏳", status="待重试",
                       detail="参与用户太多(%s)：%s。已冷却 %d 分钟，等下轮 cron 补签"
                              % (code, msg or "-", cd))
            return res

        res.update(icon="❌", status="失败", detail="code=%s %s" % (code, msg))
        return res

    res.update(icon="⏳", status="待重试", detail="claim 未成功，等下轮补签")
    return res


# ══════════════════ 推送 ══════════════════
def push(title, text):
    qywx = cfg_str("qywxToken") or os.getenv("WECHAT_WEBHOOK", "").strip()
    key = qywx.split("key=")[-1].strip()
    if key:
        try:
            body = ("%s\n%s" % (title, text)).encode("utf-8")[:2000].decode("utf-8", "ignore")
            req = urllib.request.Request(
                QYWX_URL + key,
                data=json.dumps({"msgtype": "text", "text": {"content": body}}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
            print("%s [企业微信] errcode=%s" % ("✅" if r.get("errcode") == 0 else "❌", r.get("errcode")))
        except Exception as e:
            print("❌ [企业微信] 推送异常: %s" % e)

    pp = cfg_str("plusplusToken")
    if pp:
        try:
            req = urllib.request.Request(
                PUSHPLUS_URL,
                data=json.dumps({"token": pp, "title": title, "content": text,
                                 "template": "txt"}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10)
            print("✅ [PushPlus] 推送成功")
        except Exception as e:
            print("❌ [PushPlus] 推送失败: %s" % e)

    hook = cfg_str("webhook")
    if hook:
        try:
            req = urllib.request.Request(
                hook,
                data=json.dumps({"title": title, "content": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10)
            print("✅ [Webhook] 推送成功")
        except Exception as e:
            print("❌ [Webhook] 推送失败: %s" % e)


# ══════════════════ 主流程 ══════════════════
def main():
    setup_log()
    accounts = load_accounts()
    if not accounts:
        print("❌ 没有可用账号。")
        print("   本机（装了 Trae 客户端）：先运行 python trae_get_token.py")
        print("   NAS / 无客户端          ：先运行 python trae_login.py")
        print("   青龙                    ：把 accounts.json 放进脚本目录")
        return 1

    log_box(["🕒 北京时间: " + bj(), "👥 账号数量: %d" % len(accounts),
             "🔧 req_source: %d（SOLO 谱系契约）" % REQ_SOURCE],
            "🤖 Trae 每日自动签到")

    state = load_state()
    results, pending = [], []
    for acc in accounts:
        key = _acct_key(acc)
        done = state.get("done", {}).get(key)
        if done:
            results.append({"name": acc.get("name") or key, "uid": acc.get("uid") or "-",
                            "icon": "☑️", "status": "已签到",
                            "credits": done.get("credits", "-"),
                            "detail": "今日已完成(%s)，本轮跳过" % done.get("at", "-")})
            print("☑️  [%s] 今日已完成，跳过" % (acc.get("name") or key))
            continue
        left = cooldown_left(key)
        if left > 0:
            print("⏳ [冷却] %s 冷却中，剩 %d 分钟，本轮跳过"
                  % (acc.get("name") or key, (left + 59) // 60))
            results.append({"name": acc.get("name") or key, "uid": acc.get("uid") or "-",
                            "icon": "⏳", "status": "待重试",
                            "credits": "-", "detail": "9074 冷却中，剩 %d 分钟" % ((left + 59) // 60)})
            continue
        pending.append(acc)

    if not pending:
        print("✅ 全部账号今日已处理，本轮不发任何请求。")

    if pending and cfg_bool("jitter", True):
        wait = random.randint(0, 15)
        print("🎲 [错峰] 随机等待 %d 秒再请求…" % wait)
        time.sleep(wait)

    gap_lo = cfg_int("gapMin", 8, 0, 600)
    gap_hi = max(gap_lo, cfg_int("gapMax", 20, 0, 900))

    for idx, acc in enumerate(pending):
        if idx > 0:
            gap = random.randint(gap_lo, gap_hi)
            print("\n⏳ [间隔] 等待 %d 秒后处理下一个账号…" % gap)
            time.sleep(gap)
        try:
            r = run_account(acc, state)
        except Exception as e:                   # noqa: BLE001
            r = {"name": acc.get("name") or _acct_key(acc), "uid": acc.get("uid") or "-",
                 "icon": "❌", "status": "异常", "credits": "-", "detail": str(e)}
        results.append(r)
        if r["status"] in ("签到成功", "已签到", "未开放"):
            mark_done(_acct_key(acc), {"status": r["status"], "credits": r.get("credits"),
                                       "at": bj("%H:%M")})
        if r["status"] == "鉴权失败":
            persist(acc)

    ok_n = sum(1 for r in results if r["status"] == "签到成功")
    already_n = sum(1 for r in results if r["status"] == "已签到")
    soft_n = sum(1 for r in results if r["status"] == "待重试")
    bad_n = len(results) - ok_n - already_n - soft_n

    log_box(["✅ 成功 %d   ☑️ 已签 %d   ⏳ 待重试 %d   ❌ 失败 %d" % (ok_n, already_n, soft_n, bad_n),
             "🕒 结束时间: %s" % bj()], "🏁 Trae 签到完成")

    out = ["🕒 北京时间：%s" % bj(),
           "✅ 成功 %d   ☑️ 已签 %d   ⏳ 待重试 %d   ❌ 失败 %d" % (ok_n, already_n, soft_n, bad_n)]
    for r in results:
        out.append("%s %s(%s)：%s | %s" % (r["icon"], r["name"], r["uid"], r["status"], r["detail"]))
    push("🤖 Trae 每日签到", "\n".join(out))
    return 0 if bad_n == 0 else 2


if __name__ == "__main__":
    argv = sys.argv[1:]
    if any(a.lower() in ("-h", "--help", "help") for a in argv):
        print(__doc__)
        sys.exit(0)
    try:
        if argv and argv[0].lower() == "login":
            sys.exit(cmd_login(argv[1:]))
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
