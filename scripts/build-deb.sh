#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
#
# 构建 .deb 安装包：dist/sayall-linux_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${VERSION:-0.3.0}"
PKG=sayall-linux
ARCH=all
OUT="dist/${PKG}_${VERSION}_${ARCH}.deb"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/lib/systemd/user" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/scalable/apps" \
         "$ROOT/usr/share/sayall-linux" \
         "$ROOT/usr/share/doc/$PKG"

install -m 755 sayall_bridge.py "$ROOT/usr/bin/sayall-bridge"
install -m 755 sayall_gui.py "$ROOT/usr/bin/sayall-gui"
# .deb 里用系统路径；源码安装的 unit 用 %h/.local/bin
sed 's|ExecStart=%h/.local/bin/sayall-bridge|ExecStart=/usr/bin/sayall-bridge|' \
    sayall-bridge.service > "$ROOT/usr/lib/systemd/user/sayall-bridge.service"
chmod 644 "$ROOT/usr/lib/systemd/user/sayall-bridge.service"
install -m 644 app.sayall.Linux.desktop "$ROOT/usr/share/applications/app.sayall.Linux.desktop"
install -m 644 sayall-icon.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/app.sayall.Linux.svg"
install -m 644 assets/remote-rc003.png "$ROOT/usr/share/sayall-linux/remote-rc003.png"
install -m 644 README.md LICENSE "$ROOT/usr/share/doc/$PKG/"

SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: $ARCH
Maintainer: ZXC011 <877815284@qq.com>
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-bleak, python3-evdev, python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, pipewire-bin, wireplumber, bluez
Section: utils
Priority: optional
Homepage: https://github.com/ZXC011/sayall-linux
Description: 无线麦克风与语音输入快捷键（小米蓝牙遥控器）
 把小米蓝牙遥控器 2 / 2 Pro 变成 Linux 的无线麦克风与语音输入快捷键。
 通过 ATVV 连接遥控器，把音频解码到 PipeWire 虚拟麦克风，并触发
 Vokie 等语音输入软件。
EOF

mkdir -p dist
dpkg-deb --root-owner-group -Zxz -b "$ROOT" "$OUT"
echo "built $OUT"
