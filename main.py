"""Token 用量监控 —— 入口。

运行: python -X utf8 main.py
"""
import sys

from PySide6.QtWidgets import QApplication

import config
from widget import FloatingWidget, make_tray_icon
from settings import SettingsDialog

QSS = """
* { outline: none; }
QWidget {
    color:#e8e8ea; font-family:'Microsoft YaHei UI','Segoe UI',sans-serif;
    font-size:12px;
}
QFrame#container {
    background:rgba(20,20,24,245);
    border:1px solid rgba(255,255,255,0.06);
    border-radius:18px;
}
QFrame#container:hover {
    border-color: rgba(110,150,255,0.28);
}
QFrame#card {
    background:rgba(255,255,255,0.045);
    border:1px solid rgba(255,255,255,0.05);
    border-radius:13px;
}
QFrame#card:hover {
    background:rgba(255,255,255,0.07);
    border-color:rgba(110,140,255,0.22);
}
QLabel#apptitle { font-size:12px; font-weight:700; color:#dcdce0; letter-spacing:0.6px; }
QLabel#title     { font-size:13px; font-weight:600; color:#f2f2f4; }
QLabel#level     { font-size:10px; color:#8b9cf0;
                   background:rgba(110,140,255,0.14);
                   padding:2px 8px; border-radius:8px; }
QLabel#itemlabel { color:#c2c2c8; font-size:11px; }
QLabel#sub       { color:#6e6e74; font-size:10px; padding-left:2px; }
QLabel#pct       { font-weight:700; font-size:12px; }
QLabel#err       { color:#f0a020; font-size:11px; }
QLabel#tip       { color:#8a8a90; font-size:11px; }
QPushButton#iconbtn {
    background:transparent; border:none; color:#8a8a92;
    font-size:13px; border-radius:13px;
    padding:0;
}
QPushButton#iconbtn:hover  { background:rgba(110,140,255,0.18); color:#ffffff; }
QPushButton#iconbtn:pressed{ background:rgba(110,140,255,0.30); }
QPushButton#iconbtn:disabled { color:#444; }

QDialog { background:#1c1c20; }
QGroupBox { background:#232328; border:1px solid #33333a; border-radius:9px;
            margin-top:12px; padding:10px 10px 8px 10px; color:#aaa; }
QGroupBox::title { left:11px; padding:0 5px; }
QPushButton {
    background:#2c2c33; border:1px solid #3a3a42; border-radius:7px;
    padding:6px 14px; color:#e6e6e6;
}
QPushButton:hover { background:#383841; border-color:#4a90d9; }
QPushButton:disabled { color:#666; }
QLineEdit, QSpinBox, QDoubleSpinBox {
    background:#2a2a30; border:1px solid #3a3a42; border-radius:7px;
    padding:6px 9px; color:#e6e6e6; selection-background-color:#3b82f6;
}
QListWidget { background:#26262c; border:1px solid #33333a; border-radius:8px; }
QListWidget::item { padding:6px 8px; }
QListWidget::item:selected { background:#3b82f6; color:white; }
QCheckBox { color:#e6e6e6; }
QMenu { background:#26262c; border:1px solid #33333a; color:#e6e6e6; }
QMenu::item { padding:6px 20px; }
QMenu::item:selected { background:#3b82f6; }
QProgressBar { background:transparent; border:none; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TokenView")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(QSS)

    cfg = config.load()

    def open_settings():
        if SettingsDialog(cfg).exec():
            config.save(cfg)
            widget.apply_config()

    def quit_app():
        cfg["geometry"] = [widget.x(), widget.y(), widget.width(), widget.height()]
        config.save(cfg)
        app.quit()

    widget = FloatingWidget(cfg, on_settings=open_settings, on_quit=quit_app)
    tray = make_tray_icon(widget, open_settings, quit_app)
    tray.show()
    widget.show()

    code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    main()
