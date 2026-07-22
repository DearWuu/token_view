# PyInstaller spec for TokenView
# 打包: pyinstaller TokenView.spec
# 产物: dist/TokenView/TokenView.exe (onedir) 或 dist/TokenView.exe (onefile)

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

# 解析 spec 路径
SPEC_DIR = Path(SPECPATH).resolve() if 'SPECPATH' in dir() else Path('.').resolve()
PROJECT_ROOT = SPEC_DIR

# === 数据文件 (web 前端 + .gitignore 等) ===
# 让 web/ 在 EXE 同级可访问
datas = [
    (str(PROJECT_ROOT / 'web'), 'web'),
    (str(PROJECT_ROOT / 'assets' / 'icon.png'), 'assets'),
]

# === 隐式 import (pywebview / pystray / 一些动态 import) ===
hiddenimports = [
    # pywebview 平台相关
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    # pywebview 内部
    'webview',
    'webview.window',
    'webview.state',
    # pystray
    'pystray',
    'pystray._win32',
    # PIL
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    # websocket
    'websocket',
    'websocket._core',
    # 我们的包
    'config',
    'logger',
    'providers',
    'providers.base',
    'providers.cdp',
    'providers.zhipu',
    'providers.opencode',
    'providers.mimo',
    'api',
    'api.core',
    'api.chrome',
    'api.screen',
    'api.window',
    'api.providers',
    'api.state',
    'api.settings',
]

# === 排除 (大依赖不进 exe，运行时单独安装) ===
excludes = [
    'numpy',
    'pandas',
    'matplotlib',
    'PySide6',
    'PyQt5',
    'PyQt6',
    'tkinter',
    'unittest',
    'test',
    'setuptools',
    'pip',
    'wheel',
    'IPython',
    'jupyter',
]

# === 主程序 ===
a = Analysis(
    ['main.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile 模式：单 exe
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TokenView',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # 窗口模式，不弹 console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / 'assets' / 'icon.ico'),
)
