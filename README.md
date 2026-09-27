# Trae 每日自动签到

Trae（字节 Trae Work）每日积分自动领取。**零第三方依赖**，只用 Python 标准库，本机 / NAS / 青龙面板都能跑。

> ⚠️ 这是第三方逆向脚本，与 Trae 官方无关，可能违反服务条款，接口随时可能失效。请自行评估风险后使用。

---

## 文件结构

```
trae-checkin/
├── trae_get_token.py        本机用：从 Trae 客户端取出凭据，生成 accounts.json
├── trae_login.py            NAS 用：浏览器登录一次，生成 accounts.json
├── trae_checkin.py          签到（每天跑这个）
├── trae_credit_monitor.py   查积分余额（只读，可选）
├── config.json              设置，一般不用改
├── accounts.json            账号，由上面两个脚本生成
└── logs/                    运行日志，自动生成
```

前四个脚本共用同一份底层实现，**要放在同一目录**。

---

## 快速开始

### 本机（装了 Trae 客户端）

```bash
python trae_get_token.py     # 从客户端取出凭据 → 生成 accounts.json
python trae_checkin.py       # 签到
```

注意，别把客户端取出的凭据复制给青龙 / NAS 用，那会和客户端共用同一条轮换链，互相踢下线。

### NAS / 服务器（没装客户端）

```bash
python trae_login.py         # 浏览器登录一次 → 生成 accounts.json
python trae_checkin.py       # 签到
```

登录时会打印一条链接。脚本**不会**自动打开浏览器（无头环境用不了），需要你手动把链接复制到浏览器打开，用手机号验证码登录，然后**把地址栏里那条打不开的 `127.0.0.1` 链接整条复制回来**粘贴。

链接打不开是正常现象，不需要端口转发。

### 青龙面板

1. 把 4 个脚本上传到 `/ql/data/scripts/`
2. 把 `accounts.json` 也放进去（在哪台机器上生成都行）
3. 加定时任务：

```cron
23 0 * * *  python3 /ql/data/scripts/trae_checkin.py
0   * * * * python3 /ql/data/scripts/trae_credit_monitor.py
```

**建议避开整点**（整点所有人一起签），实测 00:15~00:45 成功率最高。

### 就这两步

无论哪种环境，套路都一样：

```
1. 生成 accounts.json（trae_get_token.py 或 trae_login.py，二选一）
2. python trae_checkin.py
```

---

## 脚本说明

### `trae_get_token.py` — 本机提取凭据

适用于**装了 Trae 客户端的机器**。直接从客户端本地数据里解密出凭据和设备号，不用抓包、不用登录。

```bash
python trae_get_token.py               # 提取 → 写入 accounts.json
python trae_get_token.py --verify      # 写入前先联网验证 token 是否有效
python trae_get_token.py --out FILE    # 写到指定路径
```

> ⚠️ 这里取出的是**桌面客户端自己的那条会话**。别把生成的 `accounts.json` 复制给青龙 / NAS 用，那会和客户端共用同一条轮换链，互相踢下线。远程部署请用 `trae_login.py`。

### `trae_login.py` — NAS 登录

适用于**没装 Trae 客户端的机器**。走浏览器 OAuth 登录，拿到一条**独立于桌面客户端**的会话链。

```bash
python trae_login.py                  # 登录并写入 accounts.json
python trae_login.py --checkin        # 登录后立刻签一次
python trae_login.py --list           # 查看已有账号
```

### `trae_checkin.py` — 签到

```bash
python trae_checkin.py
```

行为：先查状态（免费请求），已签到的不发领取请求；当日已成功的账号后续运行零请求；结束后按 `config.json` 的配置推送结果。

### `trae_credit_monitor.py` — 查积分（只读）

```bash
python trae_credit_monitor.py           # 查询并推送
python trae_credit_monitor.py --local   # 只看终端输出
```

```
📊 Trae 积分监控（只读）2026-09-27 13:23:06
  💰 账号1：总额 1,550 · 已用 651.12 · 剩余 898.88
```

只调查询接口，**绝不发起领取请求**，可以和签到脚本任意交叉运行。

---

## 账号放哪

优先读脚本同目录的 `accounts.json`。如果它不存在、且本机装了 Trae 客户端，会自动读客户端凭据，免配置。

仓库里带了一份 `accounts.example.json`（空模板，不含真实凭据），复制成 `accounts.json` 再填即可：

```bash
cp accounts.example.json accounts.json    # Linux / macOS
copy accounts.example.json accounts.json  # Windows
```

`accounts.json` 格式：

```json
{
  "accounts": [
    {
      "name": "账号1",
      "uid": "3053775021146851",
      "deviceId": "3124143766407755",
      "region": "CN",
      "accessToken": "...",
      "refreshToken": "..."
    }
  ]
}
```

多个账号就往数组里加多条，每个账号独立维护自己的 token 和设备号。

青龙也可以用环境变量 `TRAE_ACCOUNTS`（填上面 `accounts` 数组的内容），效果相同。

---

## 设置放哪

脚本同目录的 `config.json`。**一般不用改** —— 默认值就能正常工作。

只有两种情况需要动它：

**1. 配置推送**（最常用）

```json
{
  "notify": {
    "qywxToken": "企业微信机器人 key",
    "plusplusToken": "PushPlus token",
    "webhook": "自定义 webhook 地址"
  }
}
```

三项都留空就只打印到终端，不推送。

**2. 要调行为**

| 键 | 默认 | 说明 |
|---|---|---|
| `reqSource` | `2` | 签到请求体字段，**别改** |
| `claimTries` | `3` | 单轮领取次数，退避 20s / 40s |
| `cooldownMin` | `30` | 9074 后的冷却分钟数 |
| `gapMin` / `gapMax` | `8` / `20` | 账号之间的间隔秒数 |
| `refreshMarginH` | `2` | token 剩余有效期低于几小时就续期 |
| `jitter` | `true` | 启动随机抖动 0~15s |
| `noProxy` | `true` | 强制直连，不走系统代理 |
| `log` | `true` | 写日志文件 |
| `deviceId` | `""` | 全局兜底设备号，**留空才会给每个账号生成不同的** |
| `only` | `""` | 只跑指定账号：序号 / uid / 名字 |
| `local` | `auto` | `1` 强制读本机客户端，`0` 禁用 |

用记事本或 PowerShell 保存 `config.json` 会带上 UTF-8 BOM —— 脚本已做兼容，能正常读取。文件本身写错了会打印一条警告并退回默认值，不会崩。

> 青龙里也可以用环境变量覆盖同名配置（环境变量优先级更高）。完整对照表见 `python trae_checkin.py --help`。

---

## 添加多个账号

### 最重要的一件事

**每个账号都必须有属于自己的 `refreshToken`，它只能来自这个账号自己的一次登录。**

不能把同一个人的 token 复制几份、改个名字当多账号用——那是同一条轮换链复制了多份，跑起来必然互相踢。多加一个"人"（不管是自己的小号还是别人），就要多登录一次。

### 方式一：让脚本自动合并（推荐，别手抄）

**本机多个小号**（装了 Trae 客户端）：

```bash
# 在客户端里登录第 1 个账号 → 跑一次
python trae_get_token.py
# 切换到第 2 个账号 → 再跑一次
python trae_get_token.py
```

脚本按 UID 合并进同一个 `accounts.json`，不会互相覆盖。

**别人 / NAS 上的账号**：

```bash
python trae_login.py --import 朋友A.json
```

按 UID 去重，对方的设备号如果是非法格式（UUID / 32 位 hex）会就地修成 16 位数字。

### 方式二：手动编辑 accounts.json

如果确实要手填，往 `accounts` 数组里加对象就行：

```json
{
  "accounts": [
    {
      "name": "我的主号",
      "uid": "3053775021146851",
      "deviceId": "3124143766407755",
      "region": "CN",
      "accessToken": "eyJhbGciOiJSUzI1NiIs...",
      "refreshToken": "h_TCk8ak4vYLrkImyPumluquRqjEQKVvZfag5bst58o=.18d72c7172aa014f"
    },
    {
      "name": "我的小号",
      "uid": "9900112233",
      "deviceId": "",
      "region": "CN",
      "accessToken": "",
      "refreshToken": "他的refreshToken，必须来自他自己的登录"
    },
    {
      "name": "朋友A",
      "uid": "7788990011",
      "deviceId": "",
      "region": "CN",
      "accessToken": "",
      "refreshToken": "朋友A的refreshToken"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `refreshToken` | ✅ | 唯一的身份凭证，来自这个账号自己的登录 |
| `accessToken` | 否 | 可留空，脚本会自动用 `refreshToken` 换取 |
| `uid` | 强烈建议 | 账号唯一键。**留空可能导致设备号随 token 轮换而变**，进而触发风控 |
| `deviceId` | 否 | **留空**才会给每个账号生成不同的 16 位号；填了就用你给的 |
| `name` | 否 | 备注，日志和推送里显示 |
| `region` | 否 | 默认 `CN` |

**四条注意事项：**

1. **每个 `refreshToken` 必须来自各自的登录。** 这是唯一不能省、也不能借的字段。
2. **`uid` 必须各不相同。** 相同的 `uid` 会被去重逻辑当成同一个账号，后者覆盖前者。
3. **`deviceId` 留空。** 留空才会按 uid 给每个账号派生不同的号，且固定不变。填同一个值会让所有账号共用一个设备号。
4. **JSON 语法别写错** —— 数组最后一项后面**不能有多余逗号**，字符串必须用双引号。写错了脚本会打印一条警告并退回默认值（不会崩，但账号就读不进来了）。

### 怎么确认加成功了

```bash
python trae_login.py --list
```

会列出所有账号，并检查设备号是否合法：

```
共 3 个账号：
  1) 我的主号    UID 3053775021146851   设备号 3124143766407755
  2) 我的小号    UID 9900112233         设备号 -   （留空，首次签到自动生成）
  3) 朋友A       UID 7788990011         设备号 a1b2c3d4...   ⚠️ 设备号非法，会被自动替换
```

- 第 2 条显示 `-` 是**正常的**，首次签到时按 uid 生成，之后记进缓存
- 第 3 条那个警告要留意：说明填了个非法值（UUID / 32 位 hex），脚本会自动换成合法的 16 位数字

更直接的验证：

```bash
python trae_credit_monitor.py --local
```

能查到余额就说明这个账号的 token、设备号、接口契约都通了。它只查询不签到，随便跑。

## 帮朋友跑：朋友要做什么

### 唯一的前提

**朋友必须产生一条"独立"的新会话，不能把他桌面 Trae 客户端里那条给你。**

如果朋友用 `trae_get_token.py` 把他客户端的凭据提取出来发给你，那拿到的就是**他客户端自己的那条会话**——你这边一续期，他客户端手里的 token 就作废了，下次续期会失败、被迫重新登录。**朋友必须走浏览器登录（`trae_login.py`），产生一条全新独立的会话。**

### 方案 A：朋友只管登录，其余你来做（推荐）

朋友不需要装 Python，也不需要下载任何脚本 —— 他只需要一个浏览器。

| 步骤 | 谁做 | 做什么 |
|---|---|---|
| 1 | **你** | 跑 `python trae_login.py`，终端打印一条登录链接 |
| 2 | **你** | 把这条链接发给朋友 |
| 3 | **朋友** | 浏览器打开链接，用**自己的手机号 + 验证码**登录 |
| 4 | **朋友** | 登录后浏览器跳到一个打不开的 `127.0.0.1` 页面 —— 把**地址栏里完整的链接**复制下来发给你 |
| 5 | **你** | 把这条链接粘贴回终端，回车 |

拿到手的就是朋友账号的一条独立会话链，写进你的 `accounts.json`。

> - 打不开的 `127.0.0.1` 页面是**正常现象**，不用管它，只要地址栏那条链接就行。
> - 链接别放太久，让朋友尽快完成；万一时效过了，你重新跑一次拿条新链接即可。
> - 整个过程朋友的电脑不需要装任何东西。

### 方案 B：朋友自己跑脚本

朋友机器上需要 Python 3.8+，以及 `trae_checkin.py` 和 `trae_login.py` 两个文件（放同一目录）。

```bash
python trae_login.py
```

浏览器登录 → 把回调链接粘贴回终端 → 生成 `accounts.json` → 把这个文件发给你。

你收到后：

```bash
python trae_login.py --import 朋友A.json
```

### 朋友要注意的两件事

1. **务必用 `trae_login.py`，不要用 `trae_get_token.py`。** 后者提取的是他桌面客户端自己的会话，给你用会把他客户端踢下线。
2. **文件发给你之后，他自己就别再跑脚本了。** 他照常开着 Trae 客户端完全没问题（那是另一条链），但同一份凭据只能在一处运行，两边都跑就会互踢。

### 你这边要注意的

1. 导入后确认一下：`python trae_login.py --list`，看朋友那条在不在、UID 有没有跟你自己的重复。
2. 跑一次 `python trae_credit_monitor.py --local`，能查到他账号的余额就说明通了（只读，不影响）。
3. 账号多了之后同一出口 IP 上的请求量会上去。9074 的根因不是 IP，但量大了触发风控的概率客观上会涨，建议别一次塞太多。

### 安全提醒

- `accounts.json` 里是朋友的登录凭证，**等同于账号密码**。传输走私聊 / 加密渠道，别发公开群。
- 你这边保管好，别误提交到 GitHub —— 发布前记得配 `.gitignore`。
- 批量代签在服务条款里属于敏感地带，规模请自行把握。

---

## 常见问题

### 还是报 9074 怎么办？

按顺序排查：

1. **请求体是不是 `{"req_source": 2}`** —— 最常见的根因
2. **设备号是不是 16 位纯数字** —— 日志里会打印 `[设备号] xxx（来源）`，不是 16 位会跟着一条警告
3. **是不是在轮换设备号** —— 本脚本不会轮换，如果日志里看到设备号每轮都在变，说明用的不是本版本

日志在 `logs/checkin_YYYY-MM-DD.log`，里面有每次请求的原始响应，排查以它为准。

### 登录时浏览器一直「认证中」，不跳转怎么办？

**先确认一下「正常」长什么样** —— 成功时是这样的：

1. 浏览器打开登录链接 → 手机号 + 验证码登录 → 点「登录并使用 Trae」
2. 浏览器**地址栏**变成：

   ```
   http://127.0.0.1:18080/authorize?refreshToken=...&userInfo=...&userJwt=...
   ```

   同时页面显示「无法访问此网站」——**这是对的**。看地址栏，别看页面。
3. 把地址栏这条链接整条复制下来，粘回终端。

如果地址栏**没有**变成上面那样，而是一直停在 `trae.cn` 上转圈，那就是出问题了。

**最常见的原因：回调端口 18080 被别的程序占了。**

浏览器会把 token 发给 `127.0.0.1:18080`，如果那里已经有程序在监听，token 就被它接走了——脚本收不到，页面也就一直转圈。

**怎么处理：**

```powershell
netstat -ano | findstr :18080
```

看到 `LISTENING` 就记下最后一列的 PID，结束它：

```powershell
taskkill /F /PID <填PID>
```

常见占用者是**上次没关掉的命令行窗口**（比如之前跑过监听服务），也可能是官方 Trae 客户端本身。结束后重跑 `trae_login.py`，拿新链接重新登录。

> 脚本启动时会**自动探测 18080**，被占会直接把占用进程的 PID 报出来：

```
⚠️  回调端口 18080 已被占用（PID 27760）
   登录产生的 token 会被那个程序接走，脚本收不到，浏览器会一直转圈。
   除非那是你自己起的监听，否则请先结束它：
     taskkill /F /PID 27760
     netstat -ano | findstr :18080    # 复查
```

看到这个提示，照它给的命令执行即可。

**如果端口确实是空的、还是不跳转：**

- 再看一眼**地址栏**（不是页面内容）——链接有时已经在那儿了，只是页面还在转圈
- 链接有时效，别放太久；过期就 `Ctrl+C` 重跑一次拿新链接

### 提示「Token 无效」

- 走 `trae_login.py` 登录的：token 过期或被别处轮换，重新登录一次
- 从客户端提取的：大概率是解密用了错盐，换本项目的 `trae_get_token.py` 重新提取（它会校验 SHA-512，解错会直接报错而不是给你假 token）

### 本机和 NAS 会互相踢下线吗？

**不会，前提是两边的 token 来自两次独立登录。**

`refreshToken` 是轮换链，但同一个账号多处登录会产生**多条独立链**，互不影响 —— 和一个账号同时登 IDE + 网页版是一回事。

会互踢的只有一种情况：把同一份 `refreshToken` 复制到两个地方，那两条链其实是同一条，谁先续期谁就把对方手里那份作废。

所以：**同一份凭据只在一处运行。想跑两处就登录两次，拿两个不同的 token。**

> 在 Windows 上跑 `trae_login.py` 生成 token、然后把 `accounts.json` 交给青龙，是**安全**的 —— 生成 ≠ 运行，本机客户端走的是它自己的那条链。

### Windows 上怎么测试？

不用配环境变量，设置都在 `config.json` 里。改完直接跑：

```powershell
cd D:\path\to\trae-checkin
python trae_checkin.py
```

非要临时验某个环境变量，只在当前窗口生效（关掉窗口就没了）：

```powershell
$env:TRAE_COOLDOWN_MIN = "10"    # PowerShell
set TRAE_COOLDOWN_MIN=10         # cmd
```

### 今天已经签过了，怎么验证脚本能用？

跑 `python trae_credit_monitor.py`，它只查询不签到。能查到积分余额就说明 token、设备号、接口契约都通了。

### 设备号会怎么生成？

`config.json` 里 `deviceId` 留空时，会按账号的 uid 稳定派生一个 16 位数字 —— **每个账号不同，且固定不变**（换机器也不会变，因为是按 uid 算的）。

如果填了 `deviceId`，它会作为全局兜底补给所有账号，结果是大家共用一个。想让每个账号独立，就留空。

---

## 附录：相比上游修了什么

本项目不是从零写的，而是把社区里若干开源实现攒到一起、逐个定位并修掉它们各自没解决的问题后整理出的版本。改动集中在两件事：**9074 报错的根因**、**Token 无效的成因**。

### 1. 9074「当前参与用户太多」的真正根因：`req_source` 发错了值

Trae 有两个产品谱系，**请求体里的 `req_source` 必须与 token 谱系一致**：

| 谱系 | ClientID | req_source |
|---|---|---|
| TRAE | `ono9krqynydwx5` | 1 |
| SOLO | `en1oxy7wnw8j9n` | **2** |

本项目的 OAuth 走 SOLO 谱系（`en1oxy7wnw8j9n`），所以请求体必须是 `{"req_source": 2}`。上游不少脚本发的是 `req_source: 1` 或空 body —— **谱系不符会被服务端按风控直接拒成 9074**。

这意味着 9074 **不是限流**，跟出口 IP、请求头、并发都无关。所以"换 IP""换设备号""错峰重试"全部无效。

### 2. 9074 的第二根因：设备号格式

`x-device-id` 必须是 **16 位纯十进制数字**：

- ✅ 随便编一个 16 位数字 —— 服务端校验的是格式，不是这个号它认不认得
- ❌ UUID / GUID / 32 位 hex —— 会被判为"非客户端设备"

这是几个开源项目唯一没有分歧的结论。在此基础上本项目做了两件事：

- 配置里给了非法设备号（比如别的项目生成的 32 位 hex）时**不原样发出去**，直接忽略并重新生成
- **命中 9074 后绝不轮换设备号**。上游有脚本的做法是"换个新号重签"，等于换个没被信任的号继续撞风控，只会越戳越死

### 3. 请求头改回官方客户端的最小集合

只发 `Authorization` / `x-device-id` / `X-User-Region` / `User-Agent`。上游脚本额外编造 `x-os-version`、`x-app-version`、`x-device-type` 等头，反而偏离真实客户端指纹。

### 4.「Token 无效」的成因：凭据解密只认一种加密类型

桌面客户端的 `storage.json` 有两种加密写法，由头部 6 字节区分，**用的盐不同**。只认一种的实现碰到另一种时会用错盐去解，解出乱码后正则"碰巧"抠出一段假 token 就打印了 —— 填进青龙必然报 Token 无效。

本项目按头部判定加密类型，且**用明文里的 SHA-512 校验和做最终验证**：解错一定报错，绝不再吐出假 token。

### 5. 顺带修掉的两个必崩 Bug

- `pick_batch()` 使用了未定义变量 `size` —— 只要设置 `TRAE_BATCH` 就必崩 `NameError`
- `claim_with_retry()` 有一条分支只返回 3 个值，而调用方解包 4 个 —— 必崩

### 6. Token 轮换链持久化

`refreshToken` 每次续期都会轮换出新值。青龙的环境变量是静态的，不落盘就会"越刷越失效"。续期结果会写回 `accounts.json` 和 `.trae_token_cache.json`。

---

## 致谢

本项目综合了社区里多个开源实现，**所有接口端点、登录流程、加密方式、设备号与风控结论均来自这些项目**，在此向各位原作者致以诚挚感谢：

| 项目 | 作者 / 仓库 | 本项目的借鉴 |
|---|---|---|
| **Trae-AutoCheckin** | [L0NE-6/Trae-AutoCheckin](https://github.com/L0NE-6/Trae-AutoCheckin) | 主基调：多账号、青龙适配、token 缓存与轮换链回写、企业微信推送、积分监控脚本 |
| **WorkBuddy-Daily** | [L0NE-6/WorkBuddy-Daily](https://github.com/L0NE-6/WorkBuddy-Daily) | 同上作者；凭证落盘与日志设计 |
| **trae-work-checkin** | [baokun-l/trae-work-checkin](https://github.com/baokun-l/trae-work-checkin) | **Aha 设备号（`storage.json` 中 `iCubeAuthInfo://icube-dc:<16位数字>`）的发现与调研报告**，9074 定位的关键依据 |
| **trae-signin**（Go CLI） | [Maquer/trae-signin](https://github.com/Maquer/trae-signin) | 登录 → 签到 → 积分 → 推送 的整体流程 |
| **traework2api** | [Sliverkiss/traework2api](https://github.com/Sliverkiss/traework2api) | 接口端点的原始来源 |
| **trae-signin-gui**（Tauri + Rust） | Windows 桌面 GUI，业务逻辑用 Rust 重写 | **`req_source: 2` 谱系契约的决定性证据**，以及"官方客户端最小请求头集合"的结论 |
| **auto-checkin**（WorkBuddy + Trae 本机合订本） | 本机脚本，含登录 / UI 兜底 | OAuth 登录流程实现、Aha 设备号解析、refresh 被拒与网络错误的区分处理 |
| **TraeWork 每日自动签到** | Node.js 实现 | 最小请求头验证（证明设备号非必填也能成功） |
| **trae-check** | [inlayin/trae-check](https://github.com/inlayin/trae-check) | 设备 ID 去重策略 |
| **workbuddy-switch** | [changexbc/workbuddy-switch](https://github.com/changexbc/workbuddy-switch) | UI 与页面组织参考 |

特别感谢 **L0NE-6** 的 Trae-AutoCheckin —— 本项目直接在其基础上改进，它的多账号设计、青龙适配和推送体系是整个骨架的来源。

也感谢 **baokun-l** 那份调研报告，把 `x-device-id` 与 9074 的关系讲透了；以及 trae-signin-gui 的作者，其对 `req_source` 谱系契约的追踪让 9074 得以根治。

> 若上表中仓库归属或描述有误，欢迎提 Issue 指正，我会立刻更正。

---

## 开源协议与署名

建议以 **MIT** 发布。发布时请：

1. 仓库根目录附带 `LICENSE` 文件
2. **保留本 README 的致谢章节** —— 本项目建立在上述开源工作之上，署名不应被移除

## 免责声明

本工具为第三方逆向实现，与 Trae 官方无关。使用本工具产生的一切后果由使用者自行承担，作者不对任何账号风险负责。接口可能随时失效，请自行评估风险后使用。
