#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
# SayAll for Linux 安装脚本（用户级，不需要 root）
set -euo pipefail

cd "$(dirname "$0")"
DEST="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
APP_DIR="$HOME/.local/share/applications"

echo "==> 检查依赖"
for pkg in python3-bleak python3-evdev python3-gi-cairo; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        echo "缺少 $pkg，请先运行:"
        echo "    sudo apt install -y python3-bleak python3-evdev python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 pipewire-bin wireplumber bluez libglib2.0-bin"
        exit 1
    fi
done
python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw  # noqa: F401
gi.require_foreign("cairo")
print("GTK4 + libadwaita + cairo OK")
PY

for cmd in wpctl pw-loopback pw-cat bluetoothctl systemctl busctl pgrep gio; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "缺少命令 $cmd（需要 pipewire-bin / wireplumber / bluez / systemd）"
        exit 1
    fi
done

echo "==> 安装到 $DEST"
mkdir -p "$DEST" "$UNIT_DIR" "$APP_DIR"
install -m 755 sayall_bridge.py "$DEST/sayall-bridge"
install -m 755 sayall_gui.py "$DEST/sayall-gui"
install -m 644 sayall-bridge.service "$UNIT_DIR/sayall-bridge.service"
install -m 644 app.sayall.Linux.desktop "$APP_DIR/app.sayall.Linux.desktop"
rm -f "$APP_DIR/sayall-linux.desktop"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR"
install -m 644 sayall-icon.svg "$ICON_DIR/app.sayall.Linux.svg"
DATA_DIR="$HOME/.local/share/sayall-linux"
mkdir -p "$DATA_DIR"
install -m 644 assets/remote-rc003.png "$DATA_DIR/remote-rc003.png"

echo "==> 重新加载 systemd / 桌面数据库"
systemctl --user daemon-reload
update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true

echo
echo "安装完成。常用命令："
echo "  sayall-gui                               # 打开图形界面"
echo "  systemctl --user enable --now sayall-bridge.service   # 开机自启并立即运行"
echo "  journalctl --user -u sayall-bridge -f    # 查看日志"
echo
echo "首次使用：打开「无线麦」，按引导完成 Vokie 安装和遥控器配对。"
echo "还没有 Vokie？下载地址：https://vokie.com/download.html"
