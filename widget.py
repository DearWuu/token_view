"""桌面悬浮窗 + 系统托盘 + 后台刷新。"""
import time

from PySide6.QtCore import (
    Qt, QObject, QThread, QTimer, Signal, Slot,
    QPropertyAnimation, QEasingCurve,
)
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar,
    QPushButton, QMenu, QSystemTrayIcon, QApplication, QSizeGrip,
    QGraphicsDropShadowEffect,
)

import providers

BADGE = {                          # 平台徽标：背景色 + 文字
    "zhipu":    ("#3b82f6", "智"),
    "opencode": ("#a855f7", "OC"),
}


def color_for(pct: float) -> str:
    if pct >= 90:
        return "#f85149"          # 红
    if pct >= 70:
        return "#f0a020"          # 黄
    return "#3fb950"              # 绿


def fmt_reset(reset_at, note=""):
    if note:
        return note
    if not reset_at:
        return ""
    remain = reset_at - time.time()
    if remain <= 0:
        return "即将重置"
    h = int(remain // 3600)
    m = int((remain % 3600) // 60)
    if h > 24:
        return f"{h // 24}天{h % 24}小时后重置"
    if h > 0:
        return f"{h}小时{m}分后重置"
    return f"{m}分钟后重置"


def animate_bar(bar: QProgressBar, target: int, prev: float = -1):
    """进度条平滑过渡到目标值；prev >= 0 时从旧值开始，否则从 target*0.7 开始。"""
    start = max(0, int(prev)) if prev >= 0 else max(0, int(target * 0.7))
    bar.setValue(start)
    anim = QPropertyAnimation(bar, b"value", bar)
    anim.setDuration(550)
    anim.setStartValue(start)
    anim.setEndValue(int(target))
    anim.setEasingCurve(QEasingCurve.OutCubic)
    bar._anim = anim
    anim.start()


class RefreshWorker(QObject):
    """在子线程里依次拉取所有 provider，避免阻塞 UI（必须是 QObject）。"""
    results_ready = Signal(list)

    @Slot(list)
    def refresh(self, providers_cfg):
        results = []
        for cfg in providers_cfg:
            if not cfg.get("enabled"):
                continue
            try:
                results.append(providers.build(cfg).fetch())
            except Exception as e:
                results.append(providers.UsageData(
                    provider_name=cfg.get("name") or cfg.get("type"),
                    status="error", error=str(e),
                ))
        self.results_ready.emit(results)


class UsageCard(QFrame):
    """单个 plan 的卡片。"""

    def __init__(self, ptype: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.ptype = ptype
        self.ptype = ptype
        self._rows = []
        self._compact = False
        self._dock = False
        self._scale = 1.0
        self._name = ""
        self._last_items = []
        self._last_status = "ok"
        self._last_error = ""
        self._prev_pct = {}          # 记录上次渲染时各 item 百分比，key=label

        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(13, 10, 13, 11)
        self.lay.setSpacing(7)
        self._normal_margins = (13, 10, 13, 11)
        self._dock_margins = (12, 6, 12, 6)

        # 标题行：徽标 + 名称 + 等级 + 状态点（dock 模式下隐藏）
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        color, txt = BADGE.get(ptype, ("#666", "?"))
        self._badge_color = color
        self.badge = QLabel(txt)
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setStyleSheet(
            f"background:{color};color:white;border-radius:12px;"
            "font-size:11px;font-weight:700;padding-bottom:1px;"
        )
        self._set_badge_size()
        self.title = QLabel()
        self.title.setObjectName("title")
        self.level = QLabel()
        self.level.setObjectName("level")
        self.dot = QLabel("●")
        self.dot.setObjectName("dot")
        self.head.addWidget(self.badge)
        self.head.addWidget(self.title, 1)
        self.head.addWidget(self.level)
        self.head.addWidget(self.dot)
        self.lay.addLayout(self.head)

        self.body = QVBoxLayout()
        self.body.setSpacing(9)
        self.lay.addLayout(self.body)

    # ---- 状态变更 ----
    def set_state(self, text: str, level: str, status: str):
        self._name = text
        self.title.setText(text)
        self.level.setText(level)
        self.level.setVisible(bool(level))
        colors = {"ok": "#3fb950", "empty": "#f0a020", "error": "#f85149", "loading": "#6b7280"}
        self.dot.setStyleSheet(f"color:{colors.get(status, '#555')};font-size:10px;")

    def set_items(self, items, status="ok", error=""):
        self._last_items = items
        self._last_status = status
        self._last_error = error
        self._render()

    def set_compact(self, on: bool):
        if self._compact != on:
            self._compact = on
            self._render()

    def set_dock(self, on: bool):
        if self._dock != on:
            self._dock = on
            self._set_head_visible(not on)
            if on:
                self.lay.setContentsMargins(10, 5, 12, 5)
                self.lay.setSpacing(0)
                self.body.setSpacing(0)
            else:
                self.lay.setContentsMargins(13, 10, 13, 11)
                self.lay.setSpacing(7)
                self.body.setSpacing(9)
            self._render()

    def _set_head_visible(self, vis: bool):
        for i in range(self.head.count()):
            w = self.head.itemAt(i).widget()
            if w:
                w.setVisible(vis)

    def set_scale(self, s: float):
        if abs(self._scale - s) > 0.02:
            self._scale = s
            self._set_badge_size()
            self._render()

    def _set_badge_size(self):
        sz = max(18, int(24 * self._scale))
        self.badge.setFixedSize(sz, sz)
        self.badge.setStyleSheet(
            self.badge.styleSheet().replace(
                "border-radius:12px", f"border-radius:{sz // 2}px")
        )

    # ---- 渲染 ----
    def _clear_body(self):
        """彻底清除 body 里的所有 widget / 子布局，避免累积。"""
        for w in self._rows:
            self.body.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._rows = []
        while self.body.count() > 0:
            it = self.body.takeAt(0)
            sub = it.widget() or it.layout()
            if isinstance(sub, QWidget):
                sub.setParent(None)
                sub.deleteLater()
            elif sub is not None:
                while sub.count() > 0:
                    ch = sub.takeAt(0)
                    cw = ch.widget()
                    if cw is not None:
                        cw.setParent(None)
                        cw.deleteLater()
                sub.deleteLater()

    def _render(self):
        items, status, error = self._last_items, self._last_status, self._last_error
        self._clear_body()

        s = self._scale

        if status == "error":
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 2, 0, 2)
            err = QLabel(f"⚠  {error[:50]}")
            err.setObjectName("err")
            err.setWordWrap(True)
            wl.addWidget(err)
            self.body.addWidget(wrap)
            self._rows.append(wrap)
            return

        if not items:
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 2, 0, 2)
            tip = QLabel("暂无数据")
            tip.setObjectName("err")
            wl.addWidget(tip)
            self.body.addWidget(wrap)
            self._rows.append(wrap)
            return

        if self._dock:
            self._render_dock(items, s)
        else:
            lbl_px = max(9, int(11 * s))
            pct_px = max(10, int(12 * s))
            title_px = max(11, int(13 * s))
            bar_h = max(5, int(7 * s))
            pct_w = max(34, int(40 * s))
            self.title.setStyleSheet(f"font-size:{title_px}px;")
            self._render_normal(items, s, lbl_px, pct_px, bar_h, pct_w)

        self._prev_pct = {it.label: it.used_percent for it in items}

    def _render_normal(self, items, s, lbl_px, pct_px, bar_h, pct_w):
        for it in items:
            wrap = QWidget()
            vl = QVBoxLayout(wrap)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(2 if self._compact else 3)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            lbl = QLabel(it.label)
            lbl.setObjectName("itemlabel")
            c = color_for(it.used_percent)
            pct = QLabel(f"{it.used_percent:.0f}%")
            pct.setObjectName("pct")
            pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pct.setFixedWidth(pct_w)

            if self._compact:
                lbl.setStyleSheet(f"color:#cfcfd4;font-size:{lbl_px}px;")
                pct.setStyleSheet(f"color:{c};font-weight:700;font-size:{pct_px}px;"
                                  "background:rgba(255,255,255,0.05);"
                                  "padding:0 4px;border-radius:4px;")
                row.addWidget(lbl)
                row.addStretch()
                row.addWidget(pct)
                vl.addLayout(row)
            else:
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setTextVisible(False)
                bar.setFixedHeight(bar_h)
                bar.setStyleSheet(
                    "QProgressBar{background:rgba(255,255,255,0.08);border-radius:3px;}"
                    f"QProgressBar::chunk{{background:{c};border-radius:3px;}}"
                )
                lbl.setStyleSheet(f"font-size:{lbl_px}px;")
                pct.setStyleSheet(f"color:{c};font-weight:700;font-size:{pct_px}px;")
                row.addWidget(lbl)
                row.addWidget(bar, 1)
                row.addWidget(pct)
                vl.addLayout(row)

                if it.note or it.reset_at:
                    sub = QLabel(fmt_reset(it.reset_at, it.note))
                    sub.setObjectName("sub")
                    sub_px = max(8, int(10 * s))
                    sub.setStyleSheet(f"font-size:{sub_px}px;")
                    vl.addWidget(sub)

            self.body.addWidget(wrap)
            self._rows.append(wrap)
            if not self._compact:
                bar = self._find_child_bar(wrap)
                if bar:
                    prev = self._prev_pct.get(it.label, -1)
                    animate_bar(bar, it.used_percent, prev)

    def _render_dock(self, items, s):
        """顶部 dock 模式：服务商名 + 各用量项横向一行，字体更大更粗、进度条更粗。"""
        lbl_px = max(14, int(16 * s))
        pct_px = max(16, int(18 * s))
        name_px = max(16, int(19 * s))
        bar_h = max(11, int(14 * s))
        pct_w = max(42, int(50 * s))
        label_w = max(60, int(72 * s))
        name_w = max(75, int(90 * s))
        bar_radius = max(4, int(5 * s))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(int(3 * s))

        name_lbl = QLabel(self._name)
        name_lbl.setObjectName("title")
        name_lbl.setFixedWidth(name_w)
        name_lbl.setStyleSheet(
            f"font-size:{name_px}px;font-weight:800;color:#f2f2f4;"
        )

        badge = self._make_dock_badge(s)
        row.addWidget(badge)
        row.addWidget(name_lbl)

        for i, it in enumerate(items):
            if i > 0:
                row.addSpacing(int(22 * s))
            c = color_for(it.used_percent)
            lbl = QLabel(it.label)
            lbl.setObjectName("itemlabel")
            lbl.setFixedWidth(label_w)
            lbl.setStyleSheet(
                f"font-size:{lbl_px}px;font-weight:600;color:#cfcfd4;"
            )
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(bar_h)
            bar.setMinimumWidth(int(45 * s))
            bar.setStyleSheet(
                "QProgressBar{background:rgba(255,255,255,0.10);"
                f"border-radius:{bar_radius}px;}}"
                "QProgressBar::chunk{"
                f"background:{c};border-radius:{bar_radius}px;}}"
            )
            pct = QLabel(f"{it.used_percent:.0f}%")
            pct.setObjectName("pct")
            pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pct.setFixedWidth(pct_w)
            pct.setStyleSheet(
                f"color:{c};font-weight:800;font-size:{pct_px}px;"
            )
            row.addWidget(lbl)
            row.addWidget(bar, 1)
            row.addWidget(pct)
            prev = self._prev_pct.get(it.label, -1)
            animate_bar(bar, it.used_percent, prev)
        self.body.addLayout(row)

    def _make_dock_badge(self, s):
        badge = QLabel(BADGE.get(self.ptype, ("#666", "?"))[1])
        badge.setAlignment(Qt.AlignCenter)
        sz = max(16, int(20 * s))
        badge.setFixedSize(sz, sz)
        c = BADGE.get(self.ptype, ("#666", ""))[0]
        badge.setStyleSheet(
            f"background:{c};color:white;border-radius:{sz // 2}px;"
            "font-size:10px;font-weight:700;"
        )
        return badge

    @staticmethod
    def _find_child_bar(wrap):
        for child in wrap.findChildren(QProgressBar):
            return child
        return None


class FloatingWidget(QWidget):
    request_refresh = Signal(list)

    def __init__(self, cfg: dict, on_settings, on_quit):
        super().__init__()
        self.cfg = cfg
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._drag_pos = None
        self._dragging = False
        self.cards = {}
        self._compact = bool(self.cfg.get("compact", False))
        self._dock = bool(self.cfg.get("dock", False))
        self._build_ui()
        self._start_worker()
        self._start_timer()
        self.refresh_now()
        if self._dock:
            QTimer.singleShot(0, self._apply_dock_geometry)

    # ---- UI ----
    def _build_ui(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.cfg.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(264)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.container = QFrame()
        self.container.setObjectName("container")
        outer.addWidget(self.container)

        # 卡片阴影
        shadow = QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        root = QVBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 顶部条：标题 + 快捷按钮
        self.bar_layout = QHBoxLayout()
        self.bar_layout.setContentsMargins(15, 12, 10, 6)
        self.apptitle = QLabel("⚡ Token 用量")
        self.apptitle.setObjectName("apptitle")
        self.bar_layout.addWidget(self.apptitle)
        self.bar_layout.addStretch()
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setObjectName("iconbtn")
        self.btn_refresh.setFixedSize(26, 26)
        self.btn_refresh.setToolTip("立即刷新")
        self.btn_refresh.clicked.connect(self.refresh_now)
        self.btn_compact = QPushButton("⤢")
        self.btn_compact.setObjectName("iconbtn")
        self.btn_compact.setFixedSize(26, 26)
        self.btn_compact.setToolTip("切换紧凑/展开")
        self.btn_compact.clicked.connect(self._toggle_compact)
        self.btn_dock = QPushButton("⤓")
        self.btn_dock.setObjectName("iconbtn")
        self.btn_dock.setFixedSize(26, 26)
        self.btn_dock.setToolTip("切换顶部模式")
        self.btn_dock.clicked.connect(self._toggle_dock)
        self.btn_set = QPushButton("⚙")
        self.btn_set.setObjectName("iconbtn")
        self.btn_set.setFixedSize(26, 26)
        self.btn_set.setToolTip("设置")
        self.btn_set.clicked.connect(self._on_settings)
        self.bar_layout.addWidget(self.btn_refresh)
        self.bar_layout.addWidget(self.btn_compact)
        self.bar_layout.addWidget(self.btn_dock)
        self.bar_layout.addWidget(self.btn_set)
        root.addLayout(self.bar_layout)

        self.cards_wrap = QWidget()
        cl = QVBoxLayout(self.cards_wrap)
        cl.setContentsMargins(8, 0, 8, 8)
        cl.setSpacing(8)
        root.addWidget(self.cards_wrap)

        self.tip = QLabel("加载中…")
        self.tip.setObjectName("tip")
        self.tip.setAlignment(Qt.AlignCenter)
        self.tip.setContentsMargins(0, 16, 0, 16)
        root.addWidget(self.tip)

        # 右下角 resize 手柄（无边框窗口手动调整大小）
        self.grip = QSizeGrip(self.container)
        self.grip.setFixedSize(14, 14)
        self.grip.setStyleSheet(
            "QSizeGrip{background:transparent;}"
            "QSizeGrip:hover{background:rgba(110,140,255,0.18);border-radius:3px;}"
        )
        root.addWidget(self.grip, 0, Qt.AlignRight | Qt.AlignBottom)

        geo = self.cfg.get("geometry")
        if geo and len(geo) == 4:
            self.setGeometry(*geo)
        self._target_opacity = float(self.cfg.get("opacity", 0.92))
        self.apptitle.setVisible(not self._dock)
        self._apply_compact()

    def rebuild_cards(self):
        want = [p for p in self.cfg.get("providers", []) if p.get("enabled")]
        want_ids = {p["id"] for p in want}
        cl = self.cards_wrap.layout()
        for pid in list(self.cards.keys()):
            if pid not in want_ids:
                cl.removeWidget(self.cards[pid])
                self.cards[pid].deleteLater()
                del self.cards[pid]
        for p in want:
            if p["id"] not in self.cards:
                card = UsageCard(p.get("type", ""))
                cl.addWidget(card)
                self.cards[p["id"]] = card
                card.set_state(p.get("name") or p.get("type", ""), "", "loading")
            else:
                card = self.cards[p["id"]]
            card.set_dock(self._dock)
            if hasattr(self, "_last_scale"):
                card.set_scale(self._last_scale)
        self.tip.setVisible(not want)

    # ---- 后台刷新 ----
    def _start_worker(self):
        self.thread = QThread(self)
        self.worker = RefreshWorker()
        self.worker.moveToThread(self.thread)
        self.worker.results_ready.connect(self._on_results)
        self.request_refresh.connect(self.worker.refresh)
        self.thread.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._cleanup)

    def _cleanup(self):
        self.timer.stop()
        self.thread.quit()
        self.thread.wait(2000)

    def _start_timer(self):
        secs = max(15, int(self.cfg.get("refresh_interval", 60)))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_now)
        self.timer.start(secs * 1000)

    def apply_config(self):
        self._target_opacity = float(self.cfg.get("opacity", 0.92))
        self.setWindowOpacity(self._target_opacity)
        secs = max(15, int(self.cfg.get("refresh_interval", 60)))
        self.timer.setInterval(secs * 1000)
        self._compact = bool(self.cfg.get("compact", False))
        self._apply_compact()
        _want_dock = bool(self.cfg.get("dock", False))
        if _want_dock != self._dock:
            self._dock = _want_dock
            self.apptitle.setVisible(not self._dock)
            for card in self.cards.values():
                card.set_dock(self._dock)
            cl = self.cards_wrap.layout()
            cl.setSpacing(2 if self._dock else (4 if self._compact else 8))
            self.btn_dock.setText("⤒" if self._dock else "⤓")
            self.grip.setVisible(not self._dock)
            self.btn_compact.setVisible(not self._dock)
            margin = 6 if self._dock else 12
            self.layout().setContentsMargins(margin, margin, margin, margin)
            if self._dock:
                self.bar_layout.setContentsMargins(15, 6, 10, 3)
            else:
                self.bar_layout.setContentsMargins(15, 12, 10, 6)
                self.btn_refresh.setVisible(True)
                self.btn_dock.setVisible(True)
                self.btn_set.setVisible(True)
        if self._dock:
            QTimer.singleShot(0, self._apply_dock_geometry)
            QTimer.singleShot(10, self._update_dock_buttons)
        self._apply_scale()
        self.refresh_now()

    def _toggle_compact(self):
        self._compact = not self._compact
        self.cfg["compact"] = self._compact
        self._apply_compact()
        self.adjustSize()
        self._apply_scale()

    def _apply_compact(self):
        # 紧凑模式：隐藏进度条副文本、缩小卡片间距、缩小整体 padding
        for card in self.cards.values():
            card.set_compact(self._compact)
        cl = self.cards_wrap.layout()
        cl.setSpacing(4 if self._compact else 8)
        self.btn_compact.setText("⤡" if self._compact else "⤢")

    # ---- 顶部 dock 模式 ----
    def _toggle_dock(self):
        self._dock = not self._dock
        self.cfg["dock"] = self._dock
        self.apptitle.setVisible(not self._dock)
        for card in self.cards.values():
            card.set_dock(self._dock)
        cl = self.cards_wrap.layout()
        cl.setSpacing(2 if self._dock else (4 if self._compact else 8))
        self.btn_dock.setText("⤒" if self._dock else "⤓")
        self.btn_compact.setVisible(not self._dock)
        self.grip.setVisible(not self._dock)
        margin = 6 if self._dock else 12
        self.layout().setContentsMargins(margin, margin, margin, margin)
        if self._dock:
            self.bar_layout.setContentsMargins(15, 6, 10, 3)
            self._apply_dock_geometry()
            self._update_dock_buttons()
        else:
            self.bar_layout.setContentsMargins(15, 12, 10, 6)
            self.btn_refresh.setVisible(True)
            self.btn_dock.setVisible(True)
            self.btn_set.setVisible(True)
            geo = self.cfg.get("geometry")
            if geo and len(geo) == 4:
                self.setGeometry(*geo)
            self.adjustSize()
        self._apply_scale()

    def _apply_dock_geometry(self):
        """移到屏幕顶部居中，宽度为屏幕可用宽-两侧留白。"""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        w = g.width() - 80
        rows = max(1, len(self.cards))
        h = min(120, 20 + rows * 28 + 12)
        self.setMinimumWidth(0)
        self.setGeometry(g.left() + 40, g.top() + 6, w, h)
        QApplication.processEvents()
        hh = min(120, self.container.sizeHint().height() + 16)
        self.resize(w, min(h, hh))

    # ---- 响应式缩放 ----
    def _apply_scale(self):
        if self._dock:
            s = self._dock_scale()
        else:
            s = self._scale_for_width(self.width())
        self._last_scale = s
        for card in self.cards.values():
            card.set_scale(s)

    def _dock_scale(self):
        """dock 模式下宽度和高度都会影响缩放，高度压扁时字体自动缩小。"""
        ref = min(self.width(), self.height() * 8)
        return max(0.78, min(1.25, ref / 900))

    @staticmethod
    def _scale_for_width(w, base=280, lo=0.85, hi=1.5):
        return max(lo, min(hi, w / base))

    def refresh_now(self):
        self.rebuild_cards()
        for c in self.cards.values():
            c.set_state(c.title.text(), c.level.text(), "loading")
        self.btn_refresh.setEnabled(False)
        self.request_refresh.emit(list(self.cfg.get("providers", [])))

    @Slot(list)
    def _on_results(self, results):
        self.btn_refresh.setEnabled(True)
        if not results:
            self.tip.setText("暂无账号，点右上 ⚙ 添加")
            self.tip.show()
            return
        self.tip.hide()
        providers_cfg = [p for p in self.cfg.get("providers", []) if p.get("enabled")]
        for cfg, data in zip(providers_cfg, results):
            card = self.cards.get(cfg["id"])
            if not card:
                continue
            card.set_state(cfg.get("name") or cfg.get("type"), "", data.status)
            if data.status == "error":
                card.set_items([], status="error", error=data.error)
            elif not data.items:
                card.set_items([], status="error", error=data.error or "暂无数据")
            else:
                card.set_items(data.items)
        if not self._dock:
            self.adjustSize()
        else:
            hh = min(110, self.container.sizeHint().height() + 16)
            if hh < self.height() - 4:
                self.resize(self.width(), hh)

    # ---- 拖动 + 丝滑 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = False
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & Qt.LeftButton):
            self._dragging = True
            self.setWindowOpacity(self._target_opacity * 0.55)
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self.setWindowOpacity(self._target_opacity)
        if self._dragging:
            if not self._dock:
                self.cfg["geometry"] = [self.x(), self.y(), self.width(), self.height()]
            self._dragging = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_scale()
        if self._dock:
            self._update_dock_buttons()
        if not self._dock and e.oldSize().isValid():
            self.cfg["geometry"] = [self.x(), self.y(), self.width(), self.height()]

    def _update_dock_buttons(self):
        """高度低于阈值时隐藏右上角按钮并压缩 bar 留白，让窗口能压得更扁。"""
        show = self.height() > 80
        self.btn_refresh.setVisible(show)
        self.btn_dock.setVisible(show)
        self.btn_set.setVisible(show)
        if show:
            self.bar_layout.setContentsMargins(15, 6, 10, 3)
        else:
            self.bar_layout.setContentsMargins(15, 2, 10, 1)

    def mouseDoubleClickEvent(self, e):
        self._on_settings()

    def showEvent(self, e):
        # 启动淡入
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(self._target_opacity)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._fade = anim
        super().showEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("🔄 立即刷新", self.refresh_now)
        m.addAction("⚙️ 设置…", self._on_settings)
        top = m.addAction("📌 取消置顶" if self.cfg.get("always_on_top") else "📌 置顶")
        top.triggered.connect(self._toggle_top)
        m.addSeparator()
        m.addAction("❌ 退出", self._on_quit)
        m.exec(e.globalPos())

    def _toggle_top(self):
        self.cfg["always_on_top"] = not self.cfg.get("always_on_top", True)
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.cfg["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.show()

    def closeEvent(self, e):
        e.ignore()
        self.hide()


def make_tray_icon(parent_widget: FloatingWidget, on_settings, on_quit) -> QSystemTrayIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#3b82f6"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(6, 6, 52, 52, 16, 16)
    p.setPen(QColor("white"))
    f = p.font()
    f.setBold(True)
    f.setPointSize(26)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignCenter, "T")
    p.end()

    tray = QSystemTrayIcon(QIcon(pix), parent=parent_widget)
    tray.setToolTip("Token 用量监控")
    menu = QMenu()
    menu.addAction("显示/隐藏", lambda: parent_widget.setVisible(not parent_widget.isVisible()))
    menu.addAction("立即刷新", parent_widget.refresh_now)
    menu.addAction("设置…", on_settings)
    menu.addSeparator()
    menu.addAction("退出", on_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: parent_widget.show() if r == QSystemTrayIcon.DoubleClick else None)
    return tray
