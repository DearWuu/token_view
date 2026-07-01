# 任务交接：用 CDP 连接真实 Chrome，实现智谱团队用量实时抓取

你将接手一个 **PySide6 桌面悬浮窗** 项目，任务是把"智谱 GLM Coding Plan 团队版用量"的数据获取，从当前**所有方案都失效**的状态，改成通过 **CDP（Chrome DevTools Protocol）连接用户已登录的真实 Chrome** 来抓取。这是用户最终确认的唯一可行路线。

项目路径：`D:\code\token_view`（Windows，Python）

---

## 一、背景：要做什么

桌面悬浮窗实时显示两个 coding plan 的 token 用量。UI 已完成（悬浮、置顶、可拖拽、动画进度条），**唯一卡住的是"智谱团队版数据拿不到"**。OpenCode 部分结构已搭好但未实测，CDP 方案稳定后可顺带用同样的方式抓。

数据目标（智谱团队版）：拿到当前成员的 **5 小时 / 每周 / MCP 月度** 已用百分比。

---

## 二、卡点：智谱反爬（已穷尽验证，别再试这些）

接口：`https://bigmodel.cn/api/monitor/usage/sub-account-rank`
参数：`startTime` / `endTime`（格式 `YYYY-MM-DD HH:MM:SS`，覆盖最近 7 天）/ `pageNum=1` / `pageSize=20` / `keyword=`

**浏览器里能看到完整数据**，但以下**三种程序化方式全部返回空**：
```json
{"code":200,"msg":"操作成功","data":{},"success":true}
```
- ❌ `requests` + cookie + auth_token(JWT)
- ❌ `curl_cffi`（`impersonate="chrome"` 模拟 TLS 指纹）+ cookie + auth
- ❌ QWebEngineView（真 Chromium）注入已保存的 cookie + JWT，页面内 JS fetch

**结论**：已排除 TLS/JA3 指纹、JS 执行环境问题。根因是缺了"只有真实登录态浏览器才有"的动态要素（疑似阿里云 WAF 的 `acw_sc__v2` 动态 cookie —— 我们抓到的 cookie 里有 `acw_tc`/`_aliyunwaf_test_cookie` 但**没有 `acw_sc__v2`**；或某个团队上下文 header）。

cookie 我们通过 QWebEngine 的 `cookieAdded` 信号抓的，共 24 个 key，含 `bigmodel_token_production`、`acw_tc` 等，但**显然漏了关键的**。所以**不要再走"抓 cookie 再请求"的路**，走 CDP。

---

## 三、选定方案：CDP 连真实 Chrome（用户已确认）

### 原理
用户启动一个带调试端口的 Chrome 并正常登录 bigmodel.cn，程序通过 CDP（WebSocket）连进去，在**那个已登录页面的上下文**里执行 `fetch('/api/...sub-account-rank?...', {credentials:'include'})`。因为是真实登录态浏览器，所有 cookie/签名头齐全，WAF 已放行该浏览器，**100% 能拿到数据**。定时（默认 60s）重复查询更新悬浮窗。

### 为什么这条路一定能成
- `credentials:'include'` 自动带上该域全部 cookie（含我们漏抓的 `acw_sc__v2` 等）
- 请求发自浏览器自身上下文，TLS、指纹、WAF 挑战全部是浏览器原生处理的
- 等价于"用户在控制台手敲 fetch"，而用户控制台能拿到数据

---

## 四、要做的事

### 1. 新增 CDP 模式到 `providers.py` 的 `ZhipuProvider`
优先级：**CDP > cookie+auth > api_key**。配置加字段（在 `config.py` 的 `new_provider("zhipu")` 里）：
```python
"cdp_enabled": True,
"cdp_port": 9222,        # Chrome 调试端口
"cdp_url": "http://127.0.0.1:9222",
```

### 2. CDP 查询流程（在子线程里同步阻塞调用即可）
```
1. GET {cdp_url}/json            → 拿 page target 列表（每个含 webSocketDebuggerUrl）
2. 找一个 url 含 bigmodel.cn 的 Page target；没有就提示用户先在 Chrome 打开 bigmodel.cn
3. WebSocket 连到该 target 的 webSocketDebuggerUrl
4. 发 Runtime.evaluate：
     id=1, method="Runtime.evaluate",
     params={"expression": "<见下方 JS>", "awaitPromise": True, "returnByValue": True}
5. 从 result.result.value 拿到 JSON 字符串，交给现有 _parse_team 解析
```

evaluate 里执行的 JS（用 IIFE + await，return 出来 CDP 才拿得到值）：
```js
(async () => {
  const r = await fetch('/api/monitor/usage/sub-account-rank'
    + '?startTime=2026-06-19 00:00:00&endTime=2026-06-25 23:59:59'
    + '&pageNum=1&pageSize=20&keyword=',
    {credentials:'include', headers:{'Accept':'application/json'}});
  return await r.text();
})()
```
（startTime/endTime 用 Python 动态生成最近 7 天，格式同上）

### 3. 选库建议
- ✅ `websocket-client`（同步）+ 手写 CDP JSON —— **最推荐**，轻量，能塞进现有子线程 `RefreshWorker`（QObject，已 moveToThread）阻塞调用
- ✅ `pychrome`（纯 CDP 客户端，不自带浏览器）
- ❌ **不要用 playwright / selenium** —— 它们自管浏览器生命周期，不是"连接已存在的 Chrome"，会另开一个全新无登录态的浏览器

### 4. 接入刷新链路
现有数据流（**不要破坏**）：
```
QTimer(widget.py) → request_refresh signal → RefreshWorker(子线程)
  → providers.build(cfg).fetch() → UsageData
  → results_ready signal(回主线程) → FloatingWidget._on_results → UsageCard.set_items
```
CDP 查询就放在 `ZhipuProvider.fetch()` 里，返回同样的 `UsageData`，上层无感。

### 5. `settings.py` 加一个"启动 CDP Chrome"按钮
自动用调试端口启动 Chrome：
```
chrome.exe --remote-debugging-port=9222 --user-data-dir="%APPDATA%\token_view\chrome_profile" https://bigmodel.cn/coding-plan/team/usage-stats
```
（用独立 user-data-dir，避免污染用户主 Chrome；让用户在这个实例里登录）

### 6. 错误处理（友好提示，写入 `%APPDATA%/token_view/debug.log`）
- Chrome 没开 / 端口连不上 → "请先在设置里启动 CDP Chrome 并登录智谱"
- 没有 bigmodel.cn 标签页 → "请在 CDP Chrome 里打开 bigmodel.cn"
- fetch 返回空 data 或登录态失效 → "登录可能已过期，请在 CDP Chrome 重新登录"

---

## 五、已验证的关键事实（别踩这些坑）

- 智谱 Authorization **不加 `Bearer`**，是裸 JWT（`eyJ...` 开头，约 343 字符，2 个点）。CDP 方案里 `credentials:'include'` 已带身份，**通常不需要再手动加 Authorization**（浏览器自己会加）；若返回 1001 未授权再加。
- **解析逻辑已对**（用真实数据验证过 21/9/3）：`data.rankList[]`，按 `customerId` 匹配配置里的 `customer_id`（匹配不到取第一个），取 `rateLimitStatus` 的：
  - `fiveHourPercentage` → "5h 窗口"
  - `weekPercentage` → "每周窗口"
  - `mcpPercentage` → "MCP 月度"
- PySide6 6.9 的 `QWebEngineCookieStore` 信号是 **`cookieAdded`**，不是 `cookieReceived`；slot 签名 `_on_cookie(self, cookie, origin=None)`
- `RefreshWorker` **必须是 `QObject` 不能是 `QWidget`**（Widget 不允许 moveToThread）
- `UsageCard.set_items` 每项用包装 `QWidget` + `deleteLater` 清理，避免刷新时行累积
- WebEngine 必须在 GUI 线程
- 配置里已有 `customer_id`（用于成员匹配）和 `cookie`/`auth_token`（历史抓取值，CDP 方案下不再依赖）

---

## 六、最小验证脚本（先跑通这个，再改主程序）

先确认 CDP 通路能拿到数据，再动 providers.py：
```python
# 前提：已用 --remote-debugging-port=9222 启动 Chrome 并登录 bigmodel.cn
import json, requests, websocket
from datetime import datetime, timedelta

targets = requests.get("http://127.0.0.1:9222/json").json()
page = next(t for t in targets if "bigmodel.cn" in t.get("url",""))
ws = websocket.create_connection(page["webSocketDebuggerUrl"])

now = datetime.now()
st = (now - timedelta(days=6)).strftime("%Y-%m-%d") + " 00:00:00"
et = now.strftime("%Y-%m-%d") + " 23:59:59"
js = ("(async()=>{const r=await fetch('/api/monitor/usage/sub-account-rank"
      f"?startTime={st}&endTime={et}&pageNum=1&pageSize=20&keyword='"
      ",{credentials:'include'});return await r.text();})()")
ws.send(json.dumps({"id":1,"method":"Runtime.evaluate",
                    "params":{"expression":js,"awaitPromise":True,"returnByValue":True}}))
resp = json.loads(ws.recv())
print(resp["result"]["result"]["value"][:500])   # 含 rankList 即成功
```
`pip install websocket-client` 先装。**看到 rankList/fiveHourPercentage 就说明方案成立**，然后按第四节改主程序。

---

## 七、OpenCode Go 部分（结构已搭，未实测）

- 无官方用量 API。现有 `OpenCodeProvider` 用 auth cookie 抓 `https://opencode.ai/workspace/{workspace_id}/go` 的 HTML，正则找 `Rolling/Weekly/Monthly` 附近的百分比
- `workspace_id` 配置里已有（`wrq_` / `wrk_` 前缀，从登录 URL 提取）
- CDP 方案稳定后，OpenCode 也可用同样方式：CDP 连 Chrome，在 opencode.ai 页面上下文 fetch 或直接读 DOM，比正则抓 HTML 稳

---

## 八、文件清单

| 文件 | 作用 | 本次要改 |
|---|---|---|
| `main.py` | 入口 + QSS 暗色主题 | 可能（设置按钮接线） |
| `widget.py` | FloatingWidget / UsageCard / RefreshWorker / 托盘 | 否（数据流通即可） |
| `providers.py` | ZhipuProvider / OpenCodeProvider | **是**（加 CDP 模式） |
| `settings.py` | 设置 + 登录对话框 | **是**（加"启动 CDP Chrome"按钮） |
| `config.py` | 配置读写 + new_provider | **是**（加 cdp_* 字段） |
| `logger.py` | `log(msg)` 写 debug.log | 否 |
| `CLAUDE.md` | 架构/坑位文档 | 改完更新 |

配置：`%APPDATA%/token_view/config.json`　日志：`%APPDATA%/token_view/debug.log`

---

## 九、约束

- 全程中文：代码注释、日志、用户提示、commit message 均用中文
- Windows 运行用 `python -X utf8 main.py`
- 优先编辑现有文件，不过度抽象，不加无谓的新文件
- Chrome 调试端口默认 9222；用独立 user-data-dir 不污染用户主 Chrome
- 装依赖：`pip install websocket-client`（curl_cffi 已装，可留作备用）

**完成标准**：启动 CDP Chrome 登录后，悬浮窗能每 60s 刷新显示真实的 5h/每周/MCP 百分比。
