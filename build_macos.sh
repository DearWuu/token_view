#!/bin/bash
# macOS 打包脚本 — 在 macOS 上运行此文件
# 用法: chmod +x build_macos.sh && ./build_macos.sh

set -e

echo "=== Token View macOS 打包 ==="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 检查依赖
echo "1. 安装依赖..."
pip3 install -r requirements.txt
pip3 install pyinstaller pyobjc

# 打包
echo "2. 开始打包..."
python3 -m PyInstaller --noconfirm --onefile --windowed --name TokenView \
  --add-data "web:web" \
  --hidden-import=webview.platforms.cocoa \
  --collect-all webview \
  main.py

# 清理
rm -rf build TokenView.spec

echo ""
echo "=== 打包完成 ==="
echo "产物: dist/TokenView.app"
echo ""
echo "首次打开如果提示「无法验证开发者」:"
echo "  右键点击 TokenView.app → 打开 → 确认"
echo "  或终端执行: xattr -cr dist/TokenView.app"
echo ""
echo "如需分发给其他用户，需用 Apple Developer 证书签名:"
echo "  codesign --deep --force --sign '你的证书ID' dist/TokenView.app"
