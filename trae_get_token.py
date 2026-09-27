#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trae 凭据提取器 · trae_get_token.py
════════════════════════════════════════════════════════════
从本机 Trae 客户端里取出登录凭据和设备号，直接生成 accounts.json。
零第三方依赖（纯标准库），免抓包、不联网（--verify 除外）。

用法
────────────────────────────────────────────────────────────
  python trae_get_token.py              提取 → 写入 accounts.json（默认就写）
  python trae_get_token.py --verify      写入前先联网验证 token 是否有效
  python trae_get_token.py --out FILE    写到指定路径

  写完直接跑：python trae_checkin.py

注意
────────────────────────────────────────────────────────────
  • 这里取出的是**桌面客户端自己的那条会话**。青龙 / NAS 要签到的话，
    别把生成的 accounts.json 复制过去用 —— 那会和客户端共用同一条轮换链，
    互相踢下线。远程部署请改用 trae_login.py 单独登录一次。
  • 本脚本只读本机文件，不上传任何数据。
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ══════════════════ 常量 ══════════════════
CLIENT_ID = "en1oxy7wnw8j9n"
APP_VERSION = "0.1.43"
UA = "Trae/%s" % APP_VERSION
EP_EXCHANGE = "https://api.trae.com.cn/cloudide/api/v3/trae/oauth/ExchangeToken"

STORAGE_KEY = "iCubeAuthInfo://icube.cloudide"
DC_PREFIX = "iCubeAuthInfo://icube-dc:"

# 两种加密类型：头部 6 字节决定用哪组盐（旧版漏了第二种 → 解出乱码 token）
HEADER_AES = bytes([0x74, 0x63, 0x05, 0x10, 0x00, 0x00])        # "tc\x05\x10\x00\x00"
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


# ══════════════════ 纯标准库 AES-128-CBC 解密 ══════════════════
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


def _addrk(s, k):
    return [s[i] ^ k[i] for i in range(16)]


def _isub(s):
    return [INV[b] for b in s]


def _ishift(s):
    return [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
            s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]


def _imix(s):
    o = []
    for c in range(4):
        a = s[c * 4:c * 4 + 4]
        o += [_gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9),
              _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13),
              _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11),
              _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)]
    return o


def aes128_cbc_decrypt(key, iv, data):
    w = _expand(key)
    out = b""
    prev = list(iv)
    for off in range(0, len(data) - len(data) % 16, 16):
        blk = list(data[off:off + 16])
        s = _addrk(blk, _rk(w, 10))
        for r in range(9, 0, -1):
            s = _ishift(s)
            s = _isub(s)
            s = _addrk(s, _rk(w, r))
            s = _imix(s)
        s = _ishift(s)
        s = _isub(s)
        s = _addrk(s, _rk(w, 0))
        out += bytes(a ^ b for a, b in zip(s, prev))
        prev = blk
    return out


# ══════════════════ 解密 storage.json 里的凭据 ══════════════════
def _key_iv(random_bytes, salt):
    """key/iv = sha512( sha512(随机数) + 盐 )[:16] / [16:32]"""
    h = hashlib.sha512(random_bytes).digest()
    fh = hashlib.sha512(h + salt).digest()
    return fh[:16], fh[16:32]


def _try_decrypt(buf, salt):
    """用指定盐解一次；返回 (明文bytes, 校验是否通过)。"""
    key, iv = _key_iv(buf[6:38], salt)
    plain = aes128_cbc_decrypt(key, iv, buf[38:])
    if len(plain) < 64:
        return None, False
    stored, text = plain[:64], plain[64:]
    pad = text[-1] if text else 0
    if 1 <= pad <= 16:
        text = text[:-pad]
    else:
        text = text.rstrip(b"\x00")
    return text, hashlib.sha512(text).digest() == stored


def decrypt_storage_value(b64):
    """解密 iCubeAuthInfo://icube.cloudide 的值。

    明文布局：[0:64] = sha512(凭据JSON)，[64:] = 凭据JSON（PKCS7 填充）。
    旧版不校验这 64 字节哈希，解错了也照样往外吐 token —— 这是"Token 无效"
    的直接来源。本版：先按头部选盐，失败则交叉试另一种，且必须校验通过。
    """
    buf = base64.b64decode(b64)
    if len(buf) < 38 + 16:
        raise ValueError("凭据串过短（%d 字节），不像是加密凭据" % len(buf))

    header = buf[0:6]
    if header == HEADER_AES:
        order = [("AES", SALT_AES), ("AES_PRIVATE", SALT_AES_PRIVATE)]
    elif header == HEADER_AES_PRIVATE:
        order = [("AES_PRIVATE", SALT_AES_PRIVATE), ("AES", SALT_AES)]
    else:
        order = [("AES", SALT_AES), ("AES_PRIVATE", SALT_AES_PRIVATE)]

    last_err = None
    for name, salt in order:
        try:
            text, ok = _try_decrypt(buf, salt)
        except Exception as e:          # noqa: BLE001 - 解密失败要换盐重试
            last_err = e
            continue
        if text is None:
            continue
        if ok:
            return text.decode("utf-8", "replace")
        last_err = ValueError("%s 解密结果未通过 SHA-512 校验" % name)

    raise ValueError("解密失败：%s（头部=%s）"
                     % (last_err or "未知原因", header.hex()))


# ══════════════════ 找客户端 storage.json ══════════════════
def _base_dirs():
    home = os.path.expanduser("~")
    cands = []
    for ev in ("APPDATA", "LOCALAPPDATA"):
        v = os.environ.get(ev)
        if v:
            cands.append(v)
    cands.append(os.path.join(home, "AppData", "Roaming"))
    cands.append(os.path.join(home, "AppData", "Local"))
    if os.name != "nt":
        cands += [os.path.join(home, "Library", "Application Support"),
                  os.path.join(home, ".config"), home]
    out = []
    for c in cands:
        if c and os.path.isdir(c) and c not in out:
            out.append(c)
    return out


def find_storage_files():
    """返回所有找到的 Trae 客户端 storage.json 路径。"""
    sub = ("User", "globalStorage", "storage.json")
    known = ("TRAE SOLO CN", "Trae CN", "TRAE SOLO", "Trae", "TraeWork", "TraeWork CN")
    found = []

    def add(p):
        if os.path.isfile(p) and p not in found:
            found.append(p)

    for base in _base_dirs():
        for n in known:
            add(os.path.join(base, n, *sub))
        # 通配兜底：客户端目录改名（SOLO 之类）也能扫到
        try:
            for entry in sorted(os.listdir(base)):
                if "trae" in entry.lower():
                    add(os.path.join(base, entry, *sub))
        except OSError:
            pass
    return found


def dc_device_id(storage):
    """客户端真实设备号（16 位 Aha 数字号）。

    签到接口风控要求 x-device-id 是这个号；用 UUID / 自造号会被判 9074。
    """
    best = ""
    for k in storage:
        if k.startswith(DC_PREFIX):
            d = k[len(DC_PREFIX):]
            if d.isdigit():
                if len(d) == 16:
                    return d
                if len(d) > len(best):
                    best = d
    return best


# ══════════════════ 解析凭据 ══════════════════
def _pick(d, *names):
    """宽容取值：忽略大小写与 _/- 差异，并兼顾 data 包装。"""
    def norm(s):
        return re.sub(r"[_－\-]", "", str(s)).lower()

    wanted = [norm(n) for n in names]
    for src in (d, d.get("data") if isinstance(d.get("data"), dict) else None):
        if not isinstance(src, dict):
            continue
        for k, v in src.items():
            if norm(k) in wanted and v not in (None, ""):
                return v
    return ""


def _jwt_obj(token):
    if not token or token.count(".") < 1:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        obj = json.loads(base64.urlsafe_b64decode(payload))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _jwt_uid(token):
    """uid 在 data 里（Cloud-IDE-JWT 把用户字段包在 data 下）。"""
    obj = _jwt_obj(token)
    for src in (obj.get("data"), obj):
        if not isinstance(src, dict):
            continue
        for k in ("id", "UserID", "userId", "uid"):
            if src.get(k):
                return str(src[k])
    return ""


def _jwt_exp(token):
    """exp 在顶层，不在 data 里。"""
    obj = _jwt_obj(token)
    for src in (obj, obj.get("data")):
        if isinstance(src, dict) and src.get("exp"):
            try:
                return int(src["exp"])
            except Exception:
                return 0
    return 0


def extract(path):
    """从一个 storage.json 里取出完整凭据；失败抛异常。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        storage = json.load(fh)
    enc = storage.get(STORAGE_KEY)
    if not enc:
        raise ValueError("未找到 %s（客户端可能没登录过）" % STORAGE_KEY)
    if isinstance(enc, dict):
        auth = enc
    else:
        enc = str(enc).strip()
        auth = json.loads(enc) if enc.startswith("{") else json.loads(decrypt_storage_value(enc))

    region = ""
    ur = auth.get("userRegion")
    if isinstance(ur, dict):
        region = str(ur.get("region") or "")
    elif isinstance(ur, str):
        region = ur

    token = str(_pick(auth, "token", "accessToken", "access_token") or "")
    refresh = str(_pick(auth, "refreshToken", "refresh_token") or "")
    uid = str(_pick(auth, "userId", "user_id", "uid", "UserID") or "") or _jwt_uid(token)
    nick = str(_pick(auth, "username", "screenName", "nickname", "nickName") or "")

    exp = _pick(auth, "tokenExpireAt", "expiresAt", "expireAt") or 0
    try:
        exp = int(exp)
        if exp > 10 ** 12:
            exp //= 1000
    except Exception:
        exp = 0
    if not exp:
        exp = _jwt_exp(token)      # 客户端没落盘有效期时从 JWT 里读

    return {
        "accessToken": token,
        "refreshToken": refresh,
        "uid": uid,
        "nickname": nick,
        "deviceId": dc_device_id(storage),
        "region": region,
        "expiresAt": exp,
        "source": path,
    }


# ══════════════════ 联网验证 refreshToken ══════════════════
def _post(url, body, headers, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:                       # noqa: BLE001
        return -1, str(e)


def verify_refresh(refresh_token):
    """用 refreshToken 换一次 accessToken，确认它真的能用。

    返回 (ok, 说明, 新refreshToken)。
    注意：ExchangeToken 会轮换 refreshToken —— 验证通过后请务必使用
    返回的新值（本脚本会自动写回 accounts.json）。
    """
    if not refresh_token:
        return False, "没有 refreshToken", ""
    status, text = _post(
        EP_EXCHANGE,
        {"ClientID": CLIENT_ID, "RefreshToken": refresh_token,
         "ClientSecret": "-", "UserID": ""},
        {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA})
    if status < 0:
        return False, "网络错误: %s" % text[:120], ""
    try:
        d = json.loads(text)
    except Exception:
        return False, "响应不是 JSON (HTTP %s): %s" % (status, text[:120]), ""

    err = ((d.get("ResponseMetadata") or {}).get("Error") or {})
    err_code, err_msg = err.get("Code", ""), err.get("Message", "")
    res = d.get("Result") or d.get("data") or {}
    token = res.get("Token") or res.get("AccessToken") or ""
    if not token:
        return False, "服务端拒绝 (HTTP %s) %s %s" % (status, err_code, err_msg), ""

    new_rt = res.get("RefreshToken") or refresh_token
    exp = res.get("TokenExpireAt") or 0
    try:
        exp = int(exp)
        if exp > 10 ** 12:
            exp //= 1000
    except Exception:
        exp = 0
    uid = _jwt_uid(token) or str(res.get("UserID") or "")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "未知"
    return True, "有效 · UID %s · 有效期至 %s" % (uid or "-", when), new_rt


# ══════════════════ accounts.json ══════════════════
def default_out_path():
    env = os.environ.get("TRAE_ACCOUNTS_FILE", "").strip()
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")


def load_existing(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            d = json.load(fh)
    except Exception:
        return []
    if isinstance(d, dict):
        d = d.get("accounts") or []
    return [x for x in d if isinstance(x, dict)]


def save_accounts(path, accounts):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"accounts": accounts}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def merge(base, new):
    """按 uid（退化为 refreshToken 前 24 位）合并，新凭据覆盖旧的。"""
    idx = {}
    for i, a in enumerate(base):
        k = str(a.get("uid") or "") or str(a.get("refreshToken", ""))[:24]
        if k:
            idx[k] = i
    for a in new:
        k = str(a.get("uid") or "") or str(a.get("refreshToken", ""))[:24]
        if k and k in idx:
            base[idx[k]].update(a)
        else:
            base.append(a)
    return base


# ══════════════════ 主流程 ══════════════════
def collect(do_verify=False, verbose=True):
    files = find_storage_files()
    if not files:
        return [], ["没找到 Trae 客户端的 storage.json。"
                    "请先安装 Trae 并在客户端里登录一次。"]

    entries, warns = [], []
    for p in files:
        try:
            rec = extract(p)
        except Exception as e:                   # noqa: BLE001
            warns.append("解析失败 %s：%s" % (p, e))
            continue
        if not rec["refreshToken"] and not rec["accessToken"]:
            warns.append("%s 里没有可用 token（客户端可能未登录）" % p)
            continue
        entries.append(rec)

    # 去重：同一个账号可能出现在多个客户端目录里
    uniq, seen = [], set()
    for e in entries:
        k = e["uid"] or e["refreshToken"][:24]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)

    if do_verify:
        for e in uniq:
            ok, msg, new_rt = verify_refresh(e["refreshToken"])
            e["verify"] = ok
            e["verifyMsg"] = msg
            if ok and new_rt:
                e["refreshToken"] = new_rt       # 轮换链：必须用新值
            if verbose:
                print("  %s 验证：%s" % ("✅" if ok else "❌", msg))
    return uniq, warns


HELP = __doc__


def main(argv):
    args = [a.lower() for a in argv[1:]]
    if any(a in ("-h", "--help", "help") for a in args):
        print(HELP)
        return 0

    do_verify = "--verify" in args or "-v" in args
    out = None
    if "--out" in args:
        i = args.index("--out")
        if i + 1 < len(argv):
            out = argv[i + 1]

    entries, warns = collect(do_verify=do_verify)

    for w in warns:
        print("[!] %s" % w, file=sys.stderr)

    if not entries:
        print("[X] 没找到任何可用凭据。")
        print("    请先安装 Trae 客户端并登录一次，再运行本脚本。")
        return 1

    path = out or default_out_path()
    accounts = load_existing(path)
    before = len(accounts)
    payload = [{"name": e.get("nickname") or ("账号%d" % (i + 1)),
                "uid": e.get("uid") or "",
                "deviceId": e.get("deviceId") or "",
                "region": e.get("region") or "CN",
                "accessToken": e.get("accessToken") or "",
                "refreshToken": e.get("refreshToken") or "",
                "expiresAt": e.get("expiresAt") or 0,
                "source": e.get("source") or ""}
               for i, e in enumerate(entries)]
    accounts = merge(accounts, payload)
    save_accounts(path, accounts)

    print()
    for i, e in enumerate(entries, 1):
        dev = e.get("deviceId") or "-"
        warn = "" if len(dev) == 16 else "  ⚠️ 非 16 位，可能触发 9074"
        print("  %d) %s  UID %s  设备号 %s%s"
              % (i, e.get("nickname") or "-", e.get("uid") or "-", dev, warn))
    print()
    print("✅ 已写入 %s（新增 %d 个，共 %d 个账号）"
          % (path, len(accounts) - before, len(accounts)))
    print()
    print("   现在可以直接跑：python trae_checkin.py")
    print()
    print("⚠️  这份凭据来自桌面客户端自己的会话：")
    print("   • 别把它复制给青龙 / NAS 用，会和客户端共用同一条轮换链、互相踢下线。")
    print("   • 远程部署请改用 python trae_login.py 单独登录一次。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(130)
