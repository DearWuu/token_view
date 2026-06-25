"""设置对话框：账号增删改 + 内嵌浏览器登录抓取凭证。

- 智谱团队版：CDP 模式（推荐）—— 启动独立调试 Chrome 登录后实时抓取；
  另保留内嵌浏览器登录抓 Cookie+usage_url 的旧路径作为备用。
- OpenCode：内嵌浏览器登录 opencode.ai，抓 auth cookie + workspace_id。
登录流程带日志（debug.log）和异常捕获，便于排查。
"""
import copy
import json
import os
import subprocess
import traceback
import winreg

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPushButton, QCheckBox, QSpinBox, QDoubleSpinBox,
    QWidget, QFormLayout, QGroupBox, QMessageBox,
)

import config
from logger import log

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import (
        QWebEngineProfile, QWebEnginePage, QWebEngineScript,
    )
    HAS_WEBENGINE = True
except Exception as _e:
    HAS_WEBENGINE = False
    log(f"WebEngine 导入失败: {_e}")


CHROME_TEAM_URL = "https://bigmodel.cn/coding-plan/team/usage-stats"
CHROME_OC_URL = "https://opencode.ai"


def _find_chrome() -> str:
    """常见位置 + 注册表查找 chrome.exe，找不到返回空串。"""
    candidates = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in candidates:
        if not base:
            continue
        p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(p):
            return p
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
        ) as k:
            v, _ = winreg.QueryValueEx(k, None)
            if v and os.path.exists(v):
                return v
    except OSError:
        pass
    return ""


def launch_cdp_chrome(port: int, cdp_profile: str = None, start_url: str = None):
    """启动带调试端口的独立 Chrome（不污染用户主 Chrome），登录后供 CDP 连接。

    cdp_profile 默认 %APPDATA%/token_view/chrome_profile。
    start_url 默认智谱团队用量页，可传入 opencode.ai 等。
    """
    chrome = _find_chrome()
    if not chrome:
        msg = "找不到 chrome.exe，请安装 Chrome 或手动用 "
        f"--remote-debugging-port={port} 启动。"
        log(msg)
        return False, msg
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    profile = cdp_profile or os.path.join(base, "token_view", "chrome_profile")
    os.makedirs(os.path.dirname(profile), exist_ok=True)
    url = start_url or CHROME_TEAM_URL
    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    try:
        subprocess.Popen(args)
    except OSError as e:
        msg = f"启动 Chrome 失败: {e}"
        log(msg)
        return False, msg
    log(f"已启动 CDP Chrome: port={port} profile={profile}")
    return True, ""


ZHIPU_HOOK_JS = r"""
(function(){
  if(window.__zhHook) return; window.__zhHook=true; window.__zhCaps=[]; window.__zhAuth=null;
  function abs(u){ try{ return new URL(u, location.origin).href; }catch(e){ return String(u); } }
  function grabAuth(h){
    try{
      if(!h) return;
      if(typeof h.get === 'function'){ var a=h.get('Authorization')||h.get('authorization'); if(a){window.__zhAuth=a;return;} }
      if(h.Authorization){ window.__zhAuth=h.Authorization; return; }
      if(h.authorization){ window.__zhAuth=h.authorization; return; }
      if(Array.isArray(h)){ h.forEach(function(p){ if(p[0]&&String(p[0]).toLowerCase()==='authorization'){window.__zhAuth=p[1];} }); }
    }catch(e){}
  }
  function tryCap(u, t){
    try{
      if(t && (t.indexOf('limits')>-1 || t.indexOf('TOKENS_LIMIT')>-1 || t.indexOf('percentage')>-1)){
        window.__zhCaps.push({url: abs(u), body: t});
      }
    }catch(e){}
  }
  var of = window.fetch;
  if(of){
    window.fetch = function(){
      try { grabAuth(arguments[1] && arguments[1].headers); } catch(e) {}
      var p = of.apply(this, arguments);
      var u = arguments[0];
      try { u = (u && u.url) || u; } catch(e) {}
      try {
        p.then(function(r){
          r.clone().text().then(function(t){ tryCap(u, t); }).catch(function(){});
        });
      } catch(e) {}
      return p;
    };
  }
  var oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  var osh = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(m, u){ this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function(k, v){
    try { if(k && String(k).toLowerCase() === 'authorization'){ window.__zhAuth = v; } } catch(e) {}
    return osh.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(){
    var x = this;
    try { x.addEventListener('load', function(){ try { tryCap(x.__u, x.responseText); } catch(e) {} }); } catch(e) {}
    return os.apply(this, arguments);
  };
})();
"""


class ZhipuLoginDialog(QDialog):
    """内嵌浏览器登录智谱，自动抓取团队用量接口 URL + Cookie。"""

    TEAM_URL = "https://bigmodel.cn/coding-plan/team/usage-stats"

    def __init__(self, parent=None):
        super().__init__(parent)
        log("ZhipuLoginDialog.__init__ 开始")
        self.setWindowTitle("登录智谱 · 抓取团队用量")
        self.resize(1040, 760)
        self.usage_url = ""
        self.cookie = ""
        self.auth_token = ""
        self.team_json = ""
        self._cookies = {}
        self._tries = 0
        self._done = False

        lay = QVBoxLayout(self)
        hint = QLabel("若弹出登录页请先完成登录；登录后会自动打开团队用量页。"
                      "抓到用量请求后本窗口会自动关闭。也可手动点下方按钮。")
        hint.setWordWrap(True)
        hint.setStyleSheet("padding:6px;color:#aaa;")
        lay.addWidget(hint)

        bar = QHBoxLayout()
        b_load = QPushButton("🔁 打开团队用量页")
        b_load.clicked.connect(self._open_team)
        self.status = QLabel("准备中…")
        self.status.setStyleSheet("color:#8b9cf0;")
        bar.addWidget(b_load)
        bar.addStretch()
        bar.addWidget(self.status)
        lay.addLayout(bar)

        try:
            log("创建 QWebEngineView")
            self.view = QWebEngineView()
        except Exception as e:
            log("QWebEngineView 创建失败: " + traceback.format_exc())
            raise
        lay.addWidget(self.view)

        try:
            log("创建 QWebEngineProfile")
            self.profile = QWebEngineProfile(self)
            script = QWebEngineScript()
            script.setName("zhipu_hook")
            script.setSourceCode(ZHIPU_HOOK_JS)
            script.setInjectionPoint(QWebEngineScript.DocumentCreation)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setRunsOnSubFrames(True)
            self.profile.scripts().insert(script)
            log("hook script 已注入")

            page = QWebEnginePage(self.profile, self)
            self.view.setPage(page)
            self.profile.cookieStore().cookieAdded.connect(self._on_cookie)
            self.view.loadFinished.connect(self._on_load)
            self.view.setUrl(QUrl(self.TEAM_URL))
            log("已 setUrl 团队用量页")
        except Exception:
            log("WebEngine 初始化异常: " + traceback.format_exc())
            raise

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(1200)
        log("ZhipuLoginDialog.__init__ 完成")

    def _open_team(self):
        log("手动打开团队用量页")
        self.view.setUrl(QUrl(self.TEAM_URL))

    def _on_cookie(self, cookie, origin=None):
        try:
            name = bytes(cookie.name()).decode("utf-8", "ignore")
            val = bytes(cookie.value()).decode("utf-8", "ignore")
            domain = cookie.domain() or ""
        except Exception:
            return
        if "bigmodel" in domain or "zhipu" in domain:
            before = len(self._cookies)
            self._cookies[name] = val
            if before == 0 and self._cookies:
                log(f"收到首个 bigmodel cookie: {name} (domain={domain})")
            if before < len(self._cookies) and len(self._cookies) in (2, 5, 10):
                log(f"累计 cookie 数: {len(self._cookies)}")

    def _on_load(self, ok):
        url = self.view.url().toString()
        log(f"loadFinished ok={ok} url={url[:80]}")
        self.status.setText("页面已加载，正在抓取用量请求…" if ok else "页面加载失败")

    def _poll(self):
        if self._done:
            return
        self._tries += 1
        try:
            self.view.page().runJavaScript(
                "JSON.stringify({caps:(window.__zhCaps||[]),auth:(window.__zhAuth||null)})",
                self._on_caps)
        except Exception:
            log("runJavaScript 异常: " + traceback.format_exc())
        if self._tries % 3 == 0:
            log(f"轮询第 {self._tries} 次，已捕获 cookie {len(self._cookies)} 个")
        if self._tries > 25:
            self._timer.stop()
            self.status.setText("暂未抓到用量请求。请确认已登录且页面显示了用量数字，再点上方按钮重试。")
            log("轮询超时，未捕获用量请求")

    def _on_caps(self, s):
        try:
            data = json.loads(s) if s else {}
        except ValueError:
            data = {}
        caps = data.get("caps", []) if isinstance(data, dict) else []
        if isinstance(data, dict) and data.get("auth"):
            self.auth_token = data["auth"]
            log(f"捕获到 Authorization 头，长度={len(self.auth_token)}")
        if caps:
            log(f"__zhCaps 捕获到 {len(caps)} 条响应，首条 url={caps[0].get('url','')[:80]}")
        for c in caps:
            body = c.get("body", "")
            if "limits" in body or "TOKENS_LIMIT" in body or "percentage" in body:
                self.usage_url = c.get("url", "")
                self.cookie = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
                self._done = True
                self._timer.stop()
                log(f"命中用量响应！url={self.usage_url} cookie长度={len(self.cookie)} auth={'有' if self.auth_token else '无'}")
                log("用浏览器内核 JS fetch sub-account-rank 验证能否拿到数据…")
                self._js_fetch_team()
                return

    def _js_fetch_team(self):
        from datetime import datetime, timedelta
        import json as _json
        n = datetime.now()
        st = (n - timedelta(days=6)).strftime("%Y-%m-%d") + " 00:00:00"
        et = n.strftime("%Y-%m-%d") + " 23:59:59"
        url = ("/api/monitor/usage/sub-account-rank?startTime=" + st
               + "&endTime=" + et + "&pageNum=1&pageSize=20&keyword=")
        auth_js = _json.dumps(self.auth_token or "")
        js = (
            "fetch(" + _json.dumps(url) + ",{credentials:'include',"
            "headers:{'Authorization':" + auth_js + "}})"
            ".then(function(r){return r.text();}).then(function(t){window.__team=t;})"
            ".catch(function(e){window.__team='ERR:'+e;})"
        )
        try:
            self.view.page().runJavaScript(js)
            QTimer.singleShot(1500, self._read_team)
        except Exception:
            log("JS fetch 异常: " + traceback.format_exc())
            self._finish()

    def _read_team(self):
        try:
            self.view.page().runJavaScript("(window.__team||'')", self._on_team)
        except Exception:
            log("读取 __team 异常: " + traceback.format_exc())
            self._finish()

    def _on_team(self, s):
        self.team_json = s or ""
        log(f"JS fetch 返回({len(self.team_json)}字符): {self.team_json[:500]}")
        self._finish()

    def _finish(self):
        QMessageBox.information(self, "抓取成功",
                                f"已捕获团队用量接口：\n{self.usage_url}\n\n凭证已记录，可关闭。")
        self.accept()


class LoginWebview(QDialog):
    """OpenCode 内嵌登录，抓 auth cookie + workspace_id。"""

    def __init__(self, start_url="https://opencode.ai/", parent=None):
        super().__init__(parent)
        log("LoginWebview.__init__ 开始")
        self.setWindowTitle("登录 OpenCode")
        self.resize(960, 720)
        self.auth_cookie = None
        self.workspace_id = None
        self._done = False

        lay = QVBoxLayout(self)
        hint = QLabel("请在打开的页面里完成登录（GitHub / Google）。登录成功后会自动抓取凭证并关闭。")
        hint.setWordWrap(True)
        hint.setStyleSheet("padding:8px;color:#aaa;")
        lay.addWidget(hint)

        self.view = QWebEngineView()
        lay.addWidget(self.view)
        self.profile = QWebEngineProfile(self)
        page = QWebEnginePage(self.profile, self)
        self.view.setPage(page)
        self.profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self.view.urlChanged.connect(self._on_url)
        self.view.setUrl(QUrl(start_url))
        log("LoginWebview.__init__ 完成")

    def _on_cookie(self, cookie, origin=None):
        try:
            name = bytes(cookie.name()).decode("utf-8", "ignore")
        except Exception:
            return
        if name == "auth":
            try:
                self.auth_cookie = bytes(cookie.value()).decode("utf-8", "ignore")
            except Exception:
                self.auth_cookie = None
            log(f"OpenCode auth cookie 已获取，长度={len(self.auth_cookie or '')}")
            self._maybe_done()

    def _on_url(self, url):
        s = url.toString()
        if "/wrk_" in s:
            import re
            m = re.search(r"(wrk_[A-Z0-9]+)", s)
            if m:
                self.workspace_id = m.group(1)
                log(f"OpenCode workspace_id={self.workspace_id}")
        self._maybe_done()

    def _maybe_done(self):
        if self._done:
            return
        if self.auth_cookie and self.workspace_id:
            self._done = True
            QMessageBox.information(self, "登录成功", f"已抓取凭证\nworkspace: {self.workspace_id}")
            self.accept()


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Token 用量监控 · 设置")
        self.resize(800, 600)
        self.cfg = cfg
        self.work = copy.deepcopy(cfg.get("providers", []))
        self._index = -1

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.addWidget(QLabel("账号"))
        self.listw = QListWidget()
        self.listw.currentRowChanged.connect(self._on_select)
        left.addWidget(self.listw, 1)
        btnrow = QHBoxLayout()
        b_add_z = QPushButton("＋ 智谱")
        b_add_o = QPushButton("＋ OpenCode")
        b_del = QPushButton("删除")
        b_add_z.clicked.connect(lambda: self._add("zhipu"))
        b_add_o.clicked.connect(lambda: self._add("opencode"))
        b_del.clicked.connect(self._del)
        btnrow.addWidget(b_add_z)
        btnrow.addWidget(b_add_o)
        btnrow.addWidget(b_del)
        left.addLayout(btnrow)
        body.addLayout(left, 1)

        right = QVBoxLayout()

        common_top = QFormLayout()
        self.f_name = QLineEdit()
        self.f_enabled = QCheckBox("启用该账号")
        self.f_enabled.setChecked(True)
        common_top.addRow("名称", self.f_name)
        common_top.addRow("", self.f_enabled)
        right.addLayout(common_top)

        self.zhipu_box = QGroupBox("智谱 GLM Coding Plan")
        zf = QFormLayout(self.zhipu_box)
        self.f_apikey = QLineEdit()
        self.f_apikey.setEchoMode(QLineEdit.Password)
        self.f_apikey.setPlaceholderText("个人版可填（团队版请用下方登录抓取）")
        self.f_show = QCheckBox("显示密钥")
        self.f_show.toggled.connect(
            lambda c: self.f_apikey.setEchoMode(QLineEdit.Normal if c else QLineEdit.Password))
        self.f_login_z = QPushButton("🔑 登录智谱抓取团队用量…")
        self.f_login_z.clicked.connect(self._login_zhipu)
        self.f_cookie_z = QLineEdit()
        self.f_cookie_z.setPlaceholderText("点上方按钮登录后自动填入（CDP 模式下可留空）")
        self.f_usage_url = QLineEdit()
        self.f_usage_url.setPlaceholderText("登录后自动填入（CDP 模式下可留空）")
        self.f_customer_id = QLineEdit()
        self.f_customer_id.setPlaceholderText("团队版：填你的账号 id（如 9951…），留空则取排名第一的成员")
        self.f_help_z = QLabel(
            "【推荐 CDP 模式】点“启动调试 Chrome”登录后实时抓取，绕开智谱反爬。\n"
            "Cookie/用量接口为旧备用路径数据，CDP 启用时可留空。\n"
            "个人版可填 API Key（来自 open.bigmodel.cn 的 API Keys）。")
        self.f_help_z.setWordWrap(True)
        zf.addRow("API Key", self.f_apikey)
        zf.addRow("", self.f_show)
        zf.addRow("", self.f_login_z)
        zf.addRow("Cookie", self.f_cookie_z)
        zf.addRow("用量接口", self.f_usage_url)
        zf.addRow("我的账号ID", self.f_customer_id)

        self.f_cdp_enabled = QCheckBox("启用 CDP（连接已登录的调试 Chrome 抓取，推荐）")
        self.f_cdp_enabled.setChecked(True)
        self.f_cdp_port = QSpinBox()
        self.f_cdp_port.setRange(1024, 65535)
        self.f_cdp_port.setValue(9222)
        self.b_cdp = QPushButton("🚀 启动调试 Chrome 登录智谱…")
        self.b_cdp.clicked.connect(self._launch_cdp)
        zf.addRow("", self.f_cdp_enabled)
        zf.addRow("调试端口", self.f_cdp_port)
        zf.addRow("", self.b_cdp)
        zf.addRow(self.f_help_z)
        right.addWidget(self.zhipu_box)

        self.opencode_box = QGroupBox("OpenCode Go")
        of = QFormLayout(self.opencode_box)
        self.f_workspace = QLineEdit()
        self.f_workspace.setPlaceholderText("wrk_xxxxxxxx")
        self.f_cookie = QLineEdit()
        self.f_cookie.setPlaceholderText("登录后自动填入，或手动粘贴 auth cookie（CDP 模式下可留空）")
        self.f_login = QPushButton("🔑 内嵌浏览器登录…")
        self.f_login.clicked.connect(self._login_opencode)

        self.f_oc_cdp_enabled = QCheckBox("启用 CDP（连接已登录的调试 Chrome 抓页面，推荐）")
        self.f_oc_cdp_enabled.setChecked(True)
        self.f_oc_cdp_port = QSpinBox()
        self.f_oc_cdp_port.setRange(1024, 65535)
        self.f_oc_cdp_port.setValue(9222)
        self.b_oc_cdp = QPushButton("🚀 启动调试 Chrome 登录 OpenCode…")
        self.b_oc_cdp.clicked.connect(self._launch_oc_cdp)

        self.f_help_o = QLabel(
            "【推荐 CDP 模式】点“启动调试 Chrome”登录 opencode.ai 后自动抓取用量。\n"
            "Cookie 为旧备用路径数据，CDP 启用时可留空。")
        self.f_help_o.setWordWrap(True)
        of.addRow("Workspace ID", self.f_workspace)
        of.addRow("Cookie", self.f_cookie)
        of.addRow("", self.f_login)
        of.addRow("", self.f_oc_cdp_enabled)
        of.addRow("调试端口", self.f_oc_cdp_port)
        of.addRow("", self.b_oc_cdp)
        of.addRow(self.f_help_o)
        right.addWidget(self.opencode_box)

        gbox = QGroupBox("通用")
        cf = QFormLayout(gbox)
        self.f_interval = QSpinBox()
        self.f_interval.setRange(15, 3600)
        self.f_interval.setSuffix(" 秒")
        self.f_interval.setSingleStep(15)
        self.f_opacity = QDoubleSpinBox()
        self.f_opacity.setRange(0.3, 1.0)
        self.f_opacity.setSingleStep(0.05)
        cf.addRow("刷新间隔", self.f_interval)
        cf.addRow("窗口透明度", self.f_opacity)
        b_open = QPushButton("打开配置目录")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config.config_dir())))
        cf.addRow("", b_open)
        right.addWidget(gbox)
        right.addStretch()
        body.addLayout(right, 2)

        bot = QHBoxLayout()
        bot.addStretch()
        b_ok = QPushButton("保存")
        b_cancel = QPushButton("取消")
        b_ok.clicked.connect(self._save)
        b_cancel.clicked.connect(self.reject)
        bot.addWidget(b_ok)
        bot.addWidget(b_cancel)
        root.addLayout(bot)

        self._fill_globals()
        if not HAS_WEBENGINE:
            self.f_login_z.setEnabled(False)
            self.f_login_z.setText("🔑 WebEngine 未安装（请手动粘贴 Cookie）")
            self.f_login.setEnabled(False)
            self.f_login.setText("🔑 WebEngine 未安装")
        self._reload_list()
        if self.work:
            self.listw.setCurrentRow(0)
        log(f"SettingsDialog 初始化完成，WebEngine={HAS_WEBENGINE}，账号数={len(self.work)}")

    def _fill_globals(self):
        self.f_interval.setValue(int(self.cfg.get("refresh_interval", 60)))
        self.f_opacity.setValue(float(self.cfg.get("opacity", 0.95)))

    def _reload_list(self):
        self.listw.blockSignals(True)
        self.listw.clear()
        for p in self.work:
            mark = "" if p.get("enabled", True) else "（已停用）"
            label = p.get("name") or p.get("type")
            self.listw.addItem(f"[{p['type']}] {label}{mark}")
        self.listw.blockSignals(False)

    def _add(self, ptype):
        p = config.new_provider(ptype)
        self.work.append(p)
        self._reload_list()
        self.listw.setCurrentRow(len(self.work) - 1)

    def _del(self):
        i = self.listw.currentRow()
        if 0 <= i < len(self.work):
            if QMessageBox.question(self, "删除", "确定删除该账号？") == QMessageBox.Yes:
                del self.work[i]
                self._reload_list()
                if self.work:
                    self.listw.setCurrentRow(0)

    def _on_select(self, row):
        self._flush()
        self._index = row
        if row < 0 or row >= len(self.work):
            return
        p = self.work[row]
        self.f_name.setText(p.get("name", ""))
        self.f_enabled.setChecked(p.get("enabled", True))
        is_z = p["type"] == "zhipu"
        self.zhipu_box.setVisible(is_z)
        self.opencode_box.setVisible(not is_z)
        if is_z:
            self.f_apikey.setText(p.get("api_key", ""))
            self.f_cookie_z.setText(p.get("cookie", ""))
            self.f_usage_url.setText(p.get("usage_url", ""))
            self.f_customer_id.setText(p.get("customer_id", ""))
            self.f_cdp_enabled.setChecked(p.get("cdp_enabled", True))
            self.f_cdp_port.setValue(int(p.get("cdp_port") or 9222))
        else:
            self.f_workspace.setText(p.get("workspace_id", ""))
            self.f_cookie.setText(p.get("cookie", ""))
            self.f_oc_cdp_enabled.setChecked(p.get("cdp_enabled", True))
            self.f_oc_cdp_port.setValue(int(p.get("cdp_port") or 9222))

    def _flush(self):
        i = self._index
        if i < 0 or i >= len(self.work):
            return
        p = self.work[i]
        p["name"] = self.f_name.text().strip()
        p["enabled"] = self.f_enabled.isChecked()
        if p["type"] == "zhipu":
            p["api_key"] = self.f_apikey.text().strip()
            p["cookie"] = self.f_cookie_z.text().strip()
            p["usage_url"] = self.f_usage_url.text().strip()
            p["customer_id"] = self.f_customer_id.text().strip()
            p["cdp_enabled"] = self.f_cdp_enabled.isChecked()
            p["cdp_port"] = self.f_cdp_port.value()
            p["cdp_url"] = f"http://127.0.0.1:{self.f_cdp_port.value()}"
        else:
            p["workspace_id"] = self.f_workspace.text().strip()
            p["cookie"] = self.f_cookie.text().strip()
            p["cdp_enabled"] = self.f_oc_cdp_enabled.isChecked()
            p["cdp_port"] = self.f_oc_cdp_port.value()
            p["cdp_url"] = f"http://127.0.0.1:{self.f_oc_cdp_port.value()}"

    def _launch_cdp(self):
        log(">>> 点击：启动调试 Chrome（智谱）")
        port = self.f_cdp_port.value()
        ok, err = launch_cdp_chrome(port)
        if not ok:
            QMessageBox.warning(self, "启动失败", err)
            return
        QMessageBox.information(
            self, "已启动调试 Chrome",
            f"请在新打开的 Chrome 里登录智谱 GLM Coding Plan，并保持“团队用量”页打开。\n"
            f"调试端口：{port}\n保存设置后悬浮窗会自动刷新。")

    def _launch_oc_cdp(self):
        log(">>> 点击：启动调试 Chrome（OpenCode）")
        port = self.f_oc_cdp_port.value()
        ok, err = launch_cdp_chrome(port, start_url=CHROME_OC_URL)
        if not ok:
            QMessageBox.warning(self, "启动失败", err)
            return
        QMessageBox.information(
            self, "已启动调试 Chrome",
            f"请在新打开的 Chrome 里登录 opencode.ai。\n"
            f"调试端口：{port}\n保存设置后悬浮窗会自动刷新。")

    def _login_zhipu(self):
        log(">>> 点击：登录智谱抓取")
        if not HAS_WEBENGINE:
            log("WebEngine 不可用，弹警告")
            QMessageBox.warning(self, "不可用", "WebEngine 未安装，无法内嵌登录。")
            return
        try:
            log("准备创建 ZhipuLoginDialog")
            dlg = ZhipuLoginDialog(parent=self)
            log("ZhipuLoginDialog 已创建，进入 exec()")
            dlg.exec()
            log(f"对话框结束：usage_url={'有' if dlg.usage_url else '无'}, cookie长度={len(dlg.cookie or '')}")
            if dlg.usage_url:
                self.f_usage_url.setText(dlg.usage_url)
                self.f_cookie_z.setText(dlg.cookie or "")
                if 0 <= self._index < len(self.work):
                    self.work[self._index]["auth_token"] = dlg.auth_token or ""
                log("已回填智谱凭证，自动保存并应用")
                self._save()        # 抓取成功即保存并关闭设置，主程序会自动刷新
            else:
                log("未抓到 usage_url，不回填")
        except Exception:
            msg = traceback.format_exc()
            log("ZhipuLoginDialog 异常:\n" + msg)
            QMessageBox.critical(self, "登录出错", msg[-800:])

    def _login_opencode(self):
        log(">>> 点击：登录 OpenCode")
        if not HAS_WEBENGINE:
            QMessageBox.warning(self, "不可用", "WebEngine 未安装，无法内嵌登录。")
            return
        try:
            dlg = LoginWebview("https://opencode.ai/", parent=self)
            dlg.exec()
            if dlg.workspace_id:
                self.f_workspace.setText(dlg.workspace_id)
                self.f_cookie.setText(dlg.auth_cookie or "")
        except Exception:
            msg = traceback.format_exc()
            log("LoginWebview 异常:\n" + msg)
            QMessageBox.critical(self, "登录出错", msg[-800:])

    def _save(self):
        self._flush()
        self.cfg["providers"] = self.work
        self.cfg["refresh_interval"] = self.f_interval.value()
        self.cfg["opacity"] = self.f_opacity.value()
        self.accept()
