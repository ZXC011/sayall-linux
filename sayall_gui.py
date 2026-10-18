#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
"""无线麦 SayAll for Linux —— 图形界面（GTK4 + libadwaita）"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

ASSET_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
    os.path.expanduser("~/.local/share/sayall-linux"),
    "/usr/share/sayall-linux",
]

CARD_ICONS = {
    116: "system-shutdown-symbolic",
    103: "go-up-symbolic",
    108: "go-down-symbolic",
    105: "go-previous-symbolic",
    106: "go-next-symbolic",
    28: "object-select-symbolic",
    158: "go-previous-symbolic",
    172: "go-home-symbolic",
    139: "open-menu-symbolic",
    163: "video-display-symbolic",
    115: "audio-volume-high-symbolic",
    114: "audio-volume-low-symbolic",
    113: "audio-volume-muted-symbolic",
    63: "audio-input-microphone-symbolic",
}

CONFIG_PATH = os.path.expanduser("~/.config/sayall-linux/config.json")
STATUS_PATH = os.path.expanduser("~/.cache/sayall-linux/status.json")
SERVICE = "sayall-bridge"

KEY_LABELS = {
    63: "语音键",
    103: "方向上",
    108: "方向下",
    105: "方向左",
    106: "方向右",
    28: "确定 / OK",
    158: "返回",
    172: "主页",
    139: "菜单",
    163: "TV",
    116: "电源",
    115: "音量 +",
    114: "音量 -",
    113: "静音",
}

ACTIONS = [
    ("passthrough", "透传（默认）"),
    ("disabled", "禁用"),
    ("app", "启动应用…"),
    ("command", "执行命令…"),
    ("key", "发送按键组合…"),
]

VOKIE_KEY_NAMES = {
    "ENTER": "Return", "ESC": "Escape", "SPACE": "Space", "TAB": "Tab",
    "BACKSPACE": "Backspace", "DELETE": "Delete", "INSERT": "Insert",
    "HOME": "Home", "END": "End", "PAGEUP": "PageUp", "PAGEDOWN": "PageDown",
    "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "MINUS": "Minus", "EQUAL": "Equal", "SLASH": "Slash", "COMMA": "Comma",
    "DOT": "Period", "SEMICOLON": "Semicolon", "APOSTROPHE": "Quote",
    "LEFTBRACE": "BracketLeft", "RIGHTBRACE": "BracketRight",
    "BACKSLASH": "Backslash", "GRAVE": "Backquote",
}

VOKIE_MODIFIERS = {
    "CTRL": ("LeftCtrl", "ctrl", "Ctrl"),
    "ALT": ("LeftAlt", "alt", "Alt"),
    "SHIFT": ("LeftShift", "shift", "Shift"),
    "SUPER": ("LeftMeta", "meta", "Win"),
}

MODIFIER_KEYS = {
    "Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R",
    "Super_L", "Super_R", "Meta_L", "Meta_R", "ISO_Level3_Shift",
    "Caps_Lock", "Num_Lock",
}

KEY_NAME_MAP = {
    "Return": "ENTER", "KP_Enter": "ENTER", "space": "SPACE", "Escape": "ESC",
    "BackSpace": "BACKSPACE", "Delete": "DELETE", "Tab": "TAB", "Insert": "INSERT",
    "Home": "HOME", "End": "END", "Page_Up": "PAGEUP", "Page_Down": "PAGEDOWN",
    "Left": "LEFT", "Right": "RIGHT", "Up": "UP", "Down": "DOWN",
    "minus": "MINUS", "equal": "EQUAL", "comma": "COMMA", "period": "DOT",
    "slash": "SLASH", "semicolon": "SEMICOLON", "apostrophe": "APOSTROPHE",
    "grave": "GRAVE", "bracketleft": "LEFTBRACE", "bracketright": "RIGHTBRACE",
    "backslash": "BACKSLASH",
}

CSS = """
.sidebar {
    background-color: @sidebar_bg_color;
    border-right: 1px solid alpha(@borders, 0.7);
}
.brand-title { font-size: 17px; font-weight: 800; }
.brand-sub { font-size: 11px; color: @insensitive_fg_color; }
.hero {
    border-radius: 20px;
    padding: 22px;
    background-image: linear-gradient(135deg, alpha(@accent_bg_color, 0.22), alpha(@accent_bg_color, 0.05));
    border: 1px solid alpha(@accent_bg_color, 0.22);
}
.hero-title { font-size: 22px; font-weight: 800; }
.hero-sub { color: @insensitive_fg_color; }
.pill { border-radius: 999px; padding: 3px 12px; font-size: 12px; font-weight: 700; }
.pill-on { background-color: alpha(#2ec27e, 0.18); color: #2ec27e; }
.pill-off { background-color: alpha(#e01b24, 0.14); color: #e01b24; }
.pill-rec { background-color: alpha(#e01b24, 0.18); color: #e01b24; }
.battery { font-size: 13px; color: @insensitive_fg_color; }
.card { background-color: @card_bg_color; border-radius: 16px; border: 1px solid alpha(@borders, 0.7); }
.section-title { font-size: 13px; font-weight: 700; color: @insensitive_fg_color; margin-left: 4px; }
.diagram { background-color: @card_bg_color; border-radius: 18px; border: 1px solid alpha(@borders, 0.7); padding: 10px; }
.diagram-hint { color: @insensitive_fg_color; font-size: 12px; }
.nav-row { border-radius: 10px; }
.page-title { font-size: 24px; font-weight: 800; }
.map-card {
    background-color: alpha(@window_fg_color, 0.055);
    border-radius: 10px;
    border: 1px solid alpha(@borders, 1.3);
    padding: 6px 9px;
}
.map-card:hover {
    background-color: alpha(@window_fg_color, 0.09);
    border-color: alpha(@accent_bg_color, 0.55);
}
.map-title { font-size: 13px; font-weight: 600; }
.map-icon { color: @accent_color; }
.chip { border-radius: 999px; padding: 2px 10px; font-size: 11px; background-color: alpha(@accent_bg_color, 0.14); color: @accent_color; }
.chip-default { background-color: alpha(@window_fg_color, 0.06); color: @insensitive_fg_color; }
.detail-title { font-size: 16px; font-weight: 700; }
.chip-label { font-size: 12px; font-weight: 500; color: @insensitive_fg_color; }
.chip-value { font-size: 12px; font-weight: 700; color: @insensitive_fg_color; }
.chip-value.set { color: @accent_color; }
.map-cell { background-color: alpha(@window_fg_color, 0.04); border-radius: 6px; padding: 4px 5px; }
.map-cell:hover { background-color: alpha(@accent_bg_color, 0.10); }
.map-card.locked { opacity: 0.55; }
.map-card.locked:hover { background-color: alpha(@window_fg_color, 0.055); border-color: alpha(@borders, 1.3); }
.voice-badge { font-size: 12px; font-weight: 600; padding: 2px 6px; border-radius: 6px; background-color: alpha(@window_fg_color, 0.06); color: @insensitive_fg_color; }
.voice-note { font-size: 11px; color: @insensitive_fg_color; }
.detail-line { font-size: 13px; color: @insensitive_fg_color; }
.badge { border-radius: 999px; padding: 3px 10px; font-size: 13px; font-weight: 600; }
.badge-on { background-color: alpha(#2ec27e, 0.16); color: #267148; }
.badge-off { background-color: alpha(@window_fg_color, 0.08); color: @insensitive_fg_color; }
.badge-rec { background-color: alpha(#ef9420, 0.18); color: #b4611a; }
.panel-card { background-color: @card_bg_color; border: 1px solid alpha(@borders, 0.9); border-radius: 12px; padding: 14px; box-shadow: 0 8px 30px rgba(33, 38, 56, 0.05); }
.card-title { font-size: 16px; font-weight: 700; }
.status-panel { background-color: alpha(@window_fg_color, 0.035); border-radius: 10px; padding: 10px 12px; }
.status-title { font-size: 14px; font-weight: 600; }
.status-detail { font-size: 12px; color: @insensitive_fg_color; }
.dot { min-width: 9px; min-height: 9px; border-radius: 999px; }
.dot-success { background-color: #2ec27e; }
.dot-pending { background-color: #b66d14; }
.device-chip { border-radius: 999px; padding: 5px 12px; border: 1px solid alpha(@borders, 0.9); background-color: alpha(@window_fg_color, 0.03); font-size: 12px; }
.row-title { font-size: 14px; font-weight: 600; }
.row-sub { font-size: 12px; color: @insensitive_fg_color; }
.gain-label { font-size: 13px; }
.gain-value { font-size: 13px; color: @insensitive_fg_color; min-width: 52px; }
.gain-help { font-size: 12px; color: @insensitive_fg_color; }

"""


# ------------------------------------------------------------------ 工具
def load_json(path: str, default: dict) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def reload_config(current: dict) -> dict:
    fresh = load_json(CONFIG_PATH, {})
    merged = dict(current)
    merged.update(fresh)
    return merged


def action_to_text(action: str) -> str:
    if not action or action == "passthrough":
        return "透传"
    if action == "disabled":
        return "禁用"
    if action.startswith("app:"):
        return "启动应用：" + action[4:]
    if action.startswith("command:"):
        return "执行命令：" + action[8:]
    if action.startswith("key:"):
        return "发送按键：" + action[4:]
    return action


def key_label(code: int, name: str | None = None) -> str:
    return KEY_LABELS.get(code) or (name or f"按键 {code}")


def normalize_key_name(name: str) -> str:
    if name in KEY_NAME_MAP:
        return KEY_NAME_MAP[name]
    if len(name) == 1 and name.isalpha():
        return name.upper()
    if len(name) == 1 and name.isdigit():
        return name
    if name.startswith("F") and name[1:].isdigit():
        return name
    return name.upper()


def mod_names(state) -> list[str]:
    mods = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        mods.append("CTRL")
    if state & Gdk.ModifierType.ALT_MASK:
        mods.append("ALT")
    if state & Gdk.ModifierType.SHIFT_MASK:
        mods.append("SHIFT")
    if state & Gdk.ModifierType.SUPER_MASK:
        mods.append("SUPER")
    return mods


def to_vokie_combo(combo: str) -> dict | None:
    parts = [p.strip().upper() for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    keys: list[str] = []
    labels: list[str] = []
    modifiers = {"ctrl": False, "alt": False, "shift": False, "meta": False}
    main = None
    for part in parts:
        if part in VOKIE_MODIFIERS:
            token, field, label = VOKIE_MODIFIERS[part]
            if modifiers[field]:
                return None
            keys.append(token)
            labels.append(label)
            modifiers[field] = "any"
        else:
            if main is not None:
                return None
            main = part
    if main is None:
        if len(keys) < 2:
            return None
        return {
            "uniKey": keys[-1], "vkCodes": [], "label": "+".join(labels),
            "modifiers": modifiers, "keys": keys, "exclusive": True,
        }
    token = VOKIE_KEY_NAMES.get(main)
    if token is None:
        if main.startswith("F") and main[1:].isdigit():
            token = main
        elif len(main) == 1 and main.isalnum():
            token = main
        else:
            return None
    keys.append(token)
    labels.append(main.capitalize() if len(main) > 1 else main)
    return {
        "uniKey": token, "vkCodes": [], "label": "+".join(labels),
        "modifiers": modifiers, "keys": keys, "exclusive": True,
    }


def load_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


# ------------------------------------------------------- 遥控器示意图
GESTURE_LABELS = {"single": "单击", "double": "双击", "long": "长按"}


def normalize_mapping(value) -> dict:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if isinstance(v, str) and v}
    if isinstance(value, str) and value:
        return {"single": value}
    return {}


def short_action(action: str) -> str:
    if not action or action == "passthrough":
        return "透传"
    if action == "disabled":
        return "禁用"
    if action.startswith("app:"):
        return "启动应用"
    if action.startswith("command:"):
        return "执行命令"
    if action.startswith("key:"):
        return action[4:]
    return action


class MappingCard(Gtk.Box):
    """按键卡片：名称 + 单击/双击/长按，每格可单独点击设置。"""

    def __init__(self, code: int, name: str, gestures: dict, is_voice: bool,
                 on_click, on_hover, on_leave) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.code = code
        self.add_css_class("map-card")
        if is_voice:
            self.add_css_class("locked")
            self.add_css_class("voice-card")
        self.set_size_request(280, 72)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name(CARD_ICONS.get(code, "input-keyboard-symbolic"))
        icon.set_pixel_size(15)
        icon.add_css_class("map-icon")
        top.append(icon)
        title = Gtk.Label(label=name, xalign=0)
        title.add_css_class("map-title")
        title.set_hexpand(True)
        top.append(title)
        if is_voice:
            badge = Gtk.Label(label="语音输入")
            badge.add_css_class("voice-badge")
            top.append(badge)
        self.append(top)

        if is_voice:
            note = Gtk.Label(label="固定用于语音输入，不可修改", xalign=0)
            note.add_css_class("voice-note")
            self.append(note)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_a: on_hover(code))
            motion.connect("leave", lambda *_a: on_leave())
            self.add_controller(motion)
            return

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for gesture in ("single", "double", "long"):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            cell.add_css_class("map-cell")
            cell.set_hexpand(True)
            lab = Gtk.Label(label=GESTURE_LABELS[gesture], xalign=0)
            lab.add_css_class("chip-label")
            action = gestures.get(gesture)
            if gesture == "single":
                text = short_action(action or "passthrough")
                configured = bool(action and action != "passthrough")
            else:
                text = short_action(action) if action else "未设置"
                configured = bool(action)
            val = Gtk.Label(label=text, xalign=0)
            val.add_css_class("chip-value")
            if configured:
                val.add_css_class("set")
            val.set_ellipsize(3)
            cell.append(lab)
            cell.append(val)
            if not is_voice:
                cell.set_cursor(Gdk.Cursor.new_from_name("pointer"))
                click = Gtk.GestureClick()
                click.connect("pressed", lambda *_a, g=gesture: on_click(code, g))
                cell.add_controller(click)
            row.append(cell)
        self.append(row)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: on_hover(code))
        motion.connect("leave", lambda *_a: on_leave())
        self.add_controller(motion)


class MappingCanvas(Gtk.Overlay):
    """参照 SayAll：遥控器居中，卡片分列左右，连线指向对应按键。"""

    WIDTH = 860
    HEIGHT = 570
    CARD_W = 300
    CARD_H = 72
    PHOTO_W = 250
    PHOTO_H = 508

    # 原项目 ButtonsPage.vue 的 PLACEMENTS：锚点是相对照片容器的归一化坐标
    PLACEMENTS = [
        (116, "left", 0.386, 0.099, 0.08),
        (103, "left", 0.502, 0.179, 0.23),
        (105, "left", 0.362, 0.246, 0.38),
        (158, "left", 0.406, 0.389, 0.53),
        (172, "left", 0.406, 0.479, 0.68),
        (139, "left", 0.406, 0.569, 0.83),
        (63, "right", 0.630, 0.099, 0.07),
        (106, "right", 0.638, 0.246, 0.215),
        (28, "right", 0.502, 0.246, 0.36),
        (108, "right", 0.502, 0.317, 0.505),
        (115, "right", 0.604, 0.390, 0.65),
        (114, "right", 0.604, 0.480, 0.795),
        (163, "right", 0.604, 0.569, 0.94),
    ]

    def __init__(self, on_click, on_hover, on_leave) -> None:
        super().__init__()
        self.on_click = on_click
        self.on_hover = on_hover
        self.on_leave = on_leave
        self.mapping: dict[str, str] = {}
        self.cards: dict[int, MappingCard] = {}
        self.hover: int | None = None
        self.photo = None
        for directory in ASSET_DIRS:
            path = os.path.join(directory, "remote-rc003.png")
            if os.path.exists(path):
                try:
                    import cairo

                    self.photo = cairo.ImageSurface.create_from_png(path)
                except Exception:
                    self.photo = None
                break

        self.area = Gtk.DrawingArea()
        self.area.set_content_width(self.WIDTH)
        self.area.set_content_height(self.HEIGHT)
        self.area.set_size_request(self.WIDTH, self.HEIGHT)
        self.area.set_draw_func(self._draw)
        self.fixed = Gtk.Fixed()
        self.fixed.set_size_request(self.WIDTH, self.HEIGHT)
        self.set_child(self.area)
        self.add_overlay(self.fixed)
        self.set_size_request(self.WIDTH, self.HEIGHT)

    def set_mappings(self, known: dict[int, str], mapping: dict[str, str]) -> None:
        self.mapping = mapping
        while True:
            child = self.fixed.get_first_child()
            if child is None:
                break
            self.fixed.remove(child)
        self.cards.clear()
        for code, side, ax, ay, target_y in self.PLACEMENTS:
            gestures = normalize_mapping(mapping.get(str(code)))
            card = MappingCard(
                code, key_label(code, known[code]), gestures, code == 63,
                self.on_click, self._hover, self._leave,
            )
            x = 0 if side == "left" else self.WIDTH - self.CARD_W
            y = self._card_y(target_y)
            self.fixed.put(card, x, y)
            self.cards[code] = card
        self.area.queue_draw()

    def _card_y(self, target_y: float) -> int:
        return int(max(0, min(self.HEIGHT - self.CARD_H, target_y * self.HEIGHT - self.CARD_H / 2)))

    def _hover(self, code: int) -> None:
        self.hover = code
        self.on_hover(code)
        self.area.queue_draw()

    def _leave(self) -> None:
        self.hover = None
        self.on_leave()
        self.area.queue_draw()

    def _anchor(self, ax: float, ay: float) -> tuple[float, float]:
        x0 = (self.WIDTH - self.PHOTO_W) / 2
        y0 = (self.HEIGHT - self.PHOTO_H) / 2
        return x0 + ax * self.PHOTO_W, y0 + ay * self.PHOTO_H


    def _draw(self, _area, cr, width, height) -> None:
        # 背景
        self._rounded(cr, 1, 1, width - 2, height - 2, 26)
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.fill()

        photo_left = (self.WIDTH - self.PHOTO_W) / 2
        photo_top = (self.HEIGHT - self.PHOTO_H) / 2
        if self.photo is not None:
            cr.save()
            cr.translate(photo_left, photo_top)
            cr.scale(self.PHOTO_W / self.photo.get_width(), self.PHOTO_H / self.photo.get_height())
            cr.set_source_surface(self.photo, 0, 0)
            cr.paint()
            cr.restore()
        else:
            self._draw_remote(cr)

        x0 = photo_left
        y0 = photo_top
        for code, side, ax, ay, target_y in self.PLACEMENTS:
            if code not in self.cards:
                continue
            px, py = self._anchor(ax, ay)
            edge_x = self.CARD_W if side == "left" else width - self.CARD_W
            direction = 1 if side == "left" else -1
            cy = self._card_y(target_y) + self.CARD_H / 2
            mapped = str(code) in self.mapping
            if mapped:
                cr.set_source_rgba(0.373, 0.384, 0.859, 0.8)
            else:
                cr.set_source_rgba(0.41, 0.44, 0.50, 0.5)
            cr.set_line_width(1.2)
            dx = (edge_x - px) * 0.55
            cr.move_to(px, py)
            cr.curve_to(px + dx, py, edge_x - dx, cy, edge_x, cy)
            cr.stroke()

            # 箭头指向卡片
            tip_x = edge_x
            base_x = edge_x + direction * 11
            cr.move_to(tip_x, cy)
            cr.line_to(base_x, cy - 4.5)
            cr.line_to(base_x, cy + 4.5)
            cr.close_path()
            cr.fill()

            if self.hover == code:
                cr.set_source_rgba(0.937, 0.580, 0.125, 0.95)
                cr.arc(px, py, 4.5, 0, 2 * math.pi)
                cr.fill()

    def _draw_remote(self, cr) -> None:
        bw, bh = 130, 480
        x0 = (self.WIDTH - bw) / 2
        y0 = (self.HEIGHT - bh) / 2

        self._rounded(cr, x0, y0, self.REMOTE_W, self.REMOTE_H, 30)
        cr.set_source_rgb(0.13, 0.14, 0.17)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.08)
        cr.set_line_width(1)
        cr.stroke()

        def circle(cx, cy, r, color=(0.24, 0.26, 0.31)):
            cr.new_path()
            cr.set_source_rgb(*color)
            cr.arc(x0 + cx, y0 + cy, r, 0, 2 * math.pi)
            cr.fill()

        def rect(rx, ry, rw, rh, color=(0.24, 0.26, 0.31)):
            cr.new_path()
            cr.set_source_rgb(*color)
            self._rounded(cr, x0 + rx, y0 + ry, rw, rh, min(rw, rh) / 2.6)
            cr.fill()

        # 顶部：电源 + 语音
        circle(30, 85, 10)
        circle(100, 85, 16, (0.18, 0.49, 0.96))
        rect(88, 122, 26, 14)

        # 方向键圆盘
        cr.new_path()
        cr.set_source_rgb(0.17, 0.18, 0.22)
        cr.arc(x0 + 65, y0 + 245, 46, 0, 2 * math.pi)
        cr.fill()
        cr.set_line_width(1)
        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.stroke()

        # 麦克风外环
        cr.new_path()
        cr.set_source_rgba(0.18, 0.49, 0.96, 0.28)
        cr.set_line_width(2)
        cr.arc(x0 + 100, y0 + 85, 21, 0, 2 * math.pi)
        cr.stroke()

        # 方向键 + OK
        circle(65, 205, 12)
        circle(65, 285, 12)
        circle(30, 245, 12)
        circle(100, 245, 12)
        circle(65, 245, 20)

        # 底部按键
        rect(20, 420, 26, 18)
        rect(52, 420, 26, 18)
        rect(84, 420, 26, 18)

        # 音量
        rect(102, 320, 18, 30)
        rect(102, 356, 18, 30)

        # 图标
        cr.new_path()
        cr.set_line_width(1.8)
        cr.set_source_rgba(1, 1, 1, 0.7)
        cr.arc(x0 + 30, y0 + 85, 4, -1.2, 4.3)
        cr.stroke()
        cr.move_to(x0 + 30, y0 + 78)
        cr.line_to(x0 + 30, y0 + 85)
        cr.stroke()

        cr.set_source_rgba(1, 1, 1, 0.95)
        self._rounded(cr, x0 + 96, y0 + 74, 8, 14, 4)
        cr.fill()
        cr.set_line_width(1.8)
        cr.arc(x0 + 100, y0 + 89, 7, 0.2, math.pi - 0.2)
        cr.stroke()
        cr.move_to(x0 + 100, y0 + 96)
        cr.line_to(x0 + 100, y0 + 100)
        cr.stroke()

        # 箭头
        cr.set_source_rgba(1, 1, 1, 0.75)
        self._arrow(cr, x0 + 65, y0 + 205, "up")
        self._arrow(cr, x0 + 65, y0 + 285, "down")
        self._arrow(cr, x0 + 30, y0 + 245, "left")
        self._arrow(cr, x0 + 100, y0 + 245, "right")

        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(9)
        cr.set_source_rgba(1, 1, 1, 0.85)
        ext = cr.text_extents("OK")
        cr.move_to(x0 + 65 - ext.width / 2, y0 + 248)
        cr.show_text("OK")

        cr.set_font_size(8)
        ext = cr.text_extents("TV")
        cr.move_to(x0 + 101 - ext.width / 2, y0 + 132)
        cr.show_text("TV")

        cr.set_line_width(1.6)
        # Home
        cr.move_to(x0 + 24, y0 + 433)
        cr.line_to(x0 + 33, y0 + 425)
        cr.line_to(x0 + 42, y0 + 433)
        cr.move_to(x0 + 27, y0 + 433)
        cr.line_to(x0 + 27, y0 + 441)
        cr.line_to(x0 + 39, y0 + 441)
        cr.line_to(x0 + 39, y0 + 433)
        cr.stroke()
        # Back
        cr.move_to(x0 + 71, y0 + 433)
        cr.line_to(x0 + 59, y0 + 433)
        cr.move_to(x0 + 59, y0 + 433)
        cr.line_to(x0 + 64, y0 + 428)
        cr.move_to(x0 + 59, y0 + 433)
        cr.line_to(x0 + 64, y0 + 438)
        cr.stroke()
        # Menu
        for dy in (426, 431, 436):
            cr.move_to(x0 + 90, y0 + dy)
            cr.line_to(x0 + 104, y0 + dy)
        cr.stroke()

        cr.set_font_size(9)
        cr.set_source_rgba(1, 1, 1, 0.8)
        cr.move_to(x0 + 108, y0 + 340)
        cr.show_text("+")
        cr.move_to(x0 + 109, y0 + 376)
        cr.show_text("−")

    @staticmethod
    def _rounded(cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @staticmethod
    def _arrow(cr, x, y, direction: str) -> None:
        size = 4.5
        if direction == "up":
            pts = [(x, y - size), (x - size, y + size), (x + size, y + size)]
        elif direction == "down":
            pts = [(x, y + size), (x - size, y - size), (x + size, y - size)]
        elif direction == "left":
            pts = [(x - size, y), (x + size, y - size), (x + size, y + size)]
        else:
            pts = [(x + size, y), (x - size, y - size), (x - size, y + size)]
        cr.move_to(*pts[0])
        for pt in pts[1:]:
            cr.line_to(*pt)
        cr.close_path()
        cr.fill()


def save_widget_png(widget, path: str) -> None:
    """把任意 GTK 控件渲染成 PNG（用于 README 截图）。"""
    try:
        width = widget.get_width()
        height = widget.get_height()
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        texture = widget.get_renderer().render_texture(node, None)
        texture.save_to_png(path)
        print(f"saved {path} ({width}x{height})")
    except Exception as exc:
        print("screenshot failed:", exc)


# ---------------------------------------------------------------- 窗口
class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, page: str | None = None) -> None:
        super().__init__(application=app, title="无线麦")
        self.set_default_size(1180, 820)

        self.cfg = load_json(CONFIG_PATH, {})
        self.status = load_json(STATUS_PATH, {})
        self.rows: dict[int, Adw.ActionRow] = {}
        self._service_state = "unknown"
        self._last_service_check = 0.0
        self._gain_ready = False

        header = Adw.HeaderBar()
        self.header_title = Gtk.Label(label="状态")
        self.header_title.add_css_class("title")
        header.set_title_widget(self.header_title)

        quit_all = Gio.SimpleAction.new("quit-all", None)
        quit_all.connect("activate", lambda *_: self.quit_all())
        self.add_action(quit_all)
        onboarding = Gio.SimpleAction.new("onboarding", None)
        onboarding.connect("activate", lambda *_: self.show_onboarding())
        self.add_action(onboarding)

        menu = Gio.Menu()
        menu.append("使用引导", "win.onboarding")
        menu.append("退出（停止桥接）", "win.quit-all")
        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name("open-menu-symbolic")
        menu_btn.set_menu_model(menu)
        menu_btn.set_tooltip_text("菜单")
        header.pack_end(menu_btn)

        self.toast_overlay = Adw.ToastOverlay()
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_status_page(), "status")
        self.stack.add_named(self._build_mapping_page(), "mapping")
        self.stack.add_named(self._build_about_page(), "about")
        root.append(self._build_sidebar())
        root.append(self.stack)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(root)
        self.toast_overlay.set_child(toolbar)
        self.set_content(self.toast_overlay)

        if page:
            self._show_page(page)
        self._onboarding = None
        GLib.timeout_add(700, self.maybe_show_onboarding)
        self._gain_ready = False
        GLib.timeout_add(600, self.refresh_status)
        GLib.timeout_add(800, self._enable_gain)

    # ---------------------------------------------------------- 侧边栏
    def _build_sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("sidebar")
        box.set_size_request(216, -1)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand.set_margin_top(18)
        brand.set_margin_bottom(16)
        brand.set_margin_start(18)
        brand.set_margin_end(18)
        icon = Gtk.Image.new_from_icon_name("app.sayall.Linux")
        icon.set_pixel_size(40)
        brand.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label="无线麦", xalign=0)
        title.add_css_class("brand-title")
        sub = Gtk.Label(label="SayAll for Linux", xalign=0)
        sub.add_css_class("brand-sub")
        text.append(title)
        text.append(sub)
        brand.append(text)
        box.append(brand)

        self.nav = Gtk.ListBox()
        self.nav.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("navigation-sidebar")
        self.nav.set_margin_start(10)
        self.nav.set_margin_end(10)
        for name, label, icon_name in (
            ("status", "状态", "audio-input-microphone-symbolic"),
            ("mapping", "按键映射", "input-keyboard-symbolic"),
            ("about", "关于", "help-about-symbolic"),
        ):
            row = Gtk.ListBoxRow()
            row.set_name(name)
            row.add_css_class("nav-row")
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            content.set_margin_top(9)
            content.set_margin_bottom(9)
            content.set_margin_start(12)
            content.set_margin_end(12)
            content.append(Gtk.Image.new_from_icon_name(icon_name))
            content.append(Gtk.Label(label=label, xalign=0))
            row.set_child(content)
            self.nav.append(row)
        self.nav.connect("row-selected", self._on_nav_selected)
        self.nav.select_row(self.nav.get_row_at_index(0))
        box.append(self.nav)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        box.append(spacer)

        footer = Gtk.Label(label="v0.3.0 · GPL-3.0", xalign=0)
        footer.add_css_class("brand-sub")
        footer.set_margin_start(18)
        footer.set_margin_bottom(14)
        box.append(footer)
        return box

    def _on_nav_selected(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if row is not None:
            name = row.get_name()
            self.stack.set_visible_child_name(name)
            if hasattr(self, "header_title"):
                self.header_title.set_text(
                    {"status": "状态", "mapping": "按键映射", "about": "关于"}.get(name, "无线麦")
                )

    def _show_page(self, name: str) -> None:
        index = {"status": 0, "mapping": 1, "about": 2}.get(name, 0)
        row = self.nav.get_row_at_index(index)
        if row is not None:
            self.nav.select_row(row)

    # ------------------------------------------------------------ 状态页
    def _build_status_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_top(26)
        outer.set_margin_bottom(26)
        outer.set_margin_start(26)
        outer.set_margin_end(26)
        scrolled.set_child(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        title = Gtk.Label(label="状态", xalign=0)
        title.add_css_class("page-title")
        title.set_hexpand(True)
        header.append(title)
        self.badge = Gtk.Label(label="未连接")
        self.badge.add_css_class("badge")
        self.badge.add_css_class("badge-off")
        header.append(self.badge)
        outer.append(header)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.set_homogeneous(True)
        row.set_valign(Gtk.Align.START)

        # 遥控器连接
        card1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card1.add_css_class("panel-card")
        tr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        h = Gtk.Label(label="遥控器连接", xalign=0)
        h.add_css_class("card-title")
        h.set_hexpand(True)
        tr.append(h)
        reconnect = Gtk.Button(label="重新连接")
        reconnect.add_css_class("flat")
        reconnect.connect("clicked", self.on_restart_service)
        tr.append(reconnect)
        card1.append(tr)

        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        panel.add_css_class("status-panel")
        self.conn_dot = Gtk.Box()
        self.conn_dot.add_css_class("dot")
        self.conn_dot.add_css_class("dot-pending")
        self.conn_dot.set_valign(Gtk.Align.START)
        self.conn_dot.set_margin_top(4)
        panel.append(self.conn_dot)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.conn_title = Gtk.Label(label="未连接", xalign=0)
        self.conn_title.add_css_class("status-title")
        self.conn_detail = Gtk.Label(label="在系统蓝牙设置中连接遥控器", xalign=0)
        self.conn_detail.add_css_class("status-detail")
        copy.append(self.conn_title)
        copy.append(self.conn_detail)
        panel.append(copy)
        card1.append(panel)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.battery_chip = Gtk.Label(label="电量 —")
        self.battery_chip.add_css_class("device-chip")
        self.ready_chip = Gtk.Label(label="语音按键待机")
        self.ready_chip.add_css_class("device-chip")
        chips.append(self.battery_chip)
        chips.append(self.ready_chip)
        card1.append(chips)
        hint = Gtk.Label(label="按住遥控器语音键说话，松开结束。", xalign=0)
        hint.add_css_class("gain-help")
        card1.append(hint)
        row.append(card1)

        # 语音设备
        card2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card2.add_css_class("panel-card")
        tr2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        h2 = Gtk.Label(label="语音设备", xalign=0)
        h2.add_css_class("card-title")
        h2.set_hexpand(True)
        tr2.append(h2)
        restart = Gtk.Button(label="重启桥接")
        restart.add_css_class("flat")
        restart.connect("clicked", self.on_restart_service)
        tr2.append(restart)
        card2.append(tr2)

        panel2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        panel2.add_css_class("status-panel")
        dot2 = Gtk.Box()
        dot2.add_css_class("dot")
        dot2.add_css_class("dot-success")
        dot2.set_valign(Gtk.Align.START)
        dot2.set_margin_top(4)
        panel2.append(dot2)
        copy2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        mic_title = Gtk.Label(label="sayall-mic", xalign=0)
        mic_title.add_css_class("status-title")
        mic_detail = Gtk.Label(label="遥控器音频 → PipeWire 虚拟麦克风", xalign=0)
        mic_detail.add_css_class("status-detail")
        copy2.append(mic_title)
        copy2.append(mic_detail)
        panel2.append(copy2)
        card2.append(panel2)

        level_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        level_label = Gtk.Label(label="电平", xalign=0)
        level_label.add_css_class("gain-label")
        self.level = Gtk.LevelBar()
        self.level.set_min_value(0)
        self.level.set_max_value(1)
        self.level.set_hexpand(True)
        self.level.set_valign(Gtk.Align.CENTER)
        self.level.add_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH, 0.85)
        level_row.append(level_label)
        level_row.append(self.level)
        card2.append(level_row)

        gain_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        gain_label = Gtk.Label(label="增益", xalign=0)
        gain_label.add_css_class("gain-label")
        self.gain_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 24, 1)
        self.gain_scale.set_draw_value(False)
        self.gain_scale.set_hexpand(True)
        self.gain_scale.set_value(float(self.cfg.get("gain_db", 6.0)))
        self.gain_scale.connect("value-changed", self.on_gain_changed)
        self.gain_value = Gtk.Label(label=f"{int(float(self.cfg.get('gain_db', 6.0)))} dB")
        self.gain_value.add_css_class("gain-value")
        gain_row.append(gain_label)
        gain_row.append(self.gain_scale)
        gain_row.append(self.gain_value)
        card2.append(gain_row)

        help_label = Gtk.Label(label="0 dB 保持原始音量；建议从 6–12 dB 开始。", xalign=0)
        help_label.add_css_class("gain-help")
        card2.append(help_label)

        keep_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        keep_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        keep_text.set_hexpand(True)
        keep_title = Gtk.Label(label="保持开麦", xalign=0)
        keep_title.add_css_class("status-title")
        keep_sub = Gtk.Label(label="开麦会话约 60 秒超时，自动续开", xalign=0)
        keep_sub.add_css_class("status-detail")
        keep_text.append(keep_title)
        keep_text.append(keep_sub)
        self.preopen_row = Gtk.Switch()
        self.preopen_row.set_active(bool(self.cfg.get("preopen_mic", True)))
        self.preopen_row.set_valign(Gtk.Align.CENTER)
        self.preopen_row.connect("notify::active", self.on_preopen_changed)
        keep_row.append(keep_text)
        keep_row.append(self.preopen_row)
        card2.append(keep_row)
        row.append(card2)
        outer.append(row)

        # 语音输入
        card3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card3.add_css_class("panel-card")
        h3 = Gtk.Label(label="语音输入", xalign=0)
        h3.add_css_class("card-title")
        card3.append(h3)

        engine_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        engine_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        engine_text.set_hexpand(True)
        e1 = Gtk.Label(label="识别引擎", xalign=0)
        e1.add_css_class("row-title")
        e2 = Gtk.Label(label="遥控器语音键触发的软件", xalign=0)
        e2.add_css_class("row-sub")
        engine_text.append(e1)
        engine_text.append(e2)
        self.engine_row = Gtk.DropDown.new_from_strings(["Vokie", "自定义命令"])
        engines = ["vokie", "custom"]
        current = (self.cfg.get("voice_engine") or "vokie").lower()
        self.engine_row.set_selected(engines.index(current) if current in engines else 0)
        self.engine_row.set_valign(Gtk.Align.CENTER)
        self.engine_row.connect("notify::selected", self.on_engine_changed)
        engine_row.append(engine_text)
        engine_row.append(self.engine_row)
        card3.append(engine_row)

        self.vokie_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        vt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        vt.set_hexpand(True)
        v1 = Gtk.Label(label="Vokie 运行", xalign=0)
        v1.add_css_class("row-title")
        v2 = Gtk.Label(label="点击右侧按钮启动/唤醒", xalign=0)
        v2.add_css_class("row-sub")
        vt.append(v1)
        vt.append(v2)
        start_vokie = Gtk.Button(label="启动 Vokie")
        start_vokie.connect("clicked", self.on_launch_vokie)
        self.vokie_row.append(vt)
        self.vokie_row.append(start_vokie)
        card3.append(self.vokie_row)

        self.hotkey_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ht = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        ht.set_hexpand(True)
        h1 = Gtk.Label(label="触发快捷键", xalign=0)
        h1.add_css_class("row-title")
        self.hotkey_value = Gtk.Label(label=f"按住 {self.vokie_ptt_label()}", xalign=0)
        self.hotkey_value.add_css_class("row-sub")
        ht.append(h1)
        ht.append(self.hotkey_value)
        edit_hotkey = Gtk.Button(label="修改")
        edit_hotkey.connect("clicked", self.on_edit_hotkey)
        self.hotkey_row.append(ht)
        self.hotkey_row.append(edit_hotkey)
        card3.append(self.hotkey_row)

        self.cmd_start_row = Gtk.Entry()
        self.cmd_start_row.set_placeholder_text("启动命令")
        self.cmd_start_row.set_text(self.cfg.get("voice_start_command") or "")
        self.cmd_start_row.connect("activate", self.on_custom_commands)
        self.cmd_stop_row = Gtk.Entry()
        self.cmd_stop_row.set_placeholder_text("停止命令")
        self.cmd_stop_row.set_text(self.cfg.get("voice_stop_command") or "")
        self.cmd_stop_row.connect("activate", self.on_custom_commands)
        card3.append(self.cmd_start_row)
        card3.append(self.cmd_stop_row)
        outer.append(card3)

        # 服务
        card4 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card4.add_css_class("panel-card")
        st = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        st.set_hexpand(True)
        s1 = Gtk.Label(label="桥接服务", xalign=0)
        s1.add_css_class("row-title")
        self.service_label = Gtk.Label(label="检查中…", xalign=0)
        self.service_label.add_css_class("row-sub")
        st.append(s1)
        st.append(self.service_label)
        restart2 = Gtk.Button(label="重启")
        restart2.connect("clicked", self.on_restart_service)
        card4.append(st)
        card4.append(restart2)
        outer.append(card4)

        self.update_engine_visibility()
        return scrolled

    def _section_title(self, text: str) -> Gtk.Widget:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("section-title")
        label.set_margin_start(4)
        return label

    # ------------------------------------------------------------ 按键页
    def _build_mapping_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(22)
        outer.set_margin_bottom(24)
        outer.set_margin_start(24)
        outer.set_margin_end(24)
        scrolled.set_child(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        titles.set_hexpand(True)
        title = Gtk.Label(label="按键映射", xalign=0)
        title.add_css_class("page-title")
        desc = Gtk.Label(label="悬停卡片可预览对应按键，点击卡片选择动作。语音键固定用于语音输入。", xalign=0)
        desc.add_css_class("hero-sub")
        desc.set_wrap(True)
        titles.append(title)
        titles.append(desc)
        header.append(titles)
        outer.append(header)

        self.canvas = MappingCanvas(self.on_canvas_click, self._show_detail, self._clear_detail)
        self.canvas.set_halign(Gtk.Align.CENTER)
        outer.append(self.canvas)

        self.detail_label = Gtk.Label(label="点击卡片设置动作", xalign=0.5)
        self.detail_label.add_css_class("detail-line")
        self.detail_label.set_halign(Gtk.Align.CENTER)
        outer.append(self.detail_label)

        self.rebuild_rows()
        return scrolled

    def on_canvas_click(self, code: int, gesture: str = "single") -> None:
        if code == 63:
            self.toast("语音键固定用于语音输入")
            return
        self.on_edit_action(None, code, gesture)

    def _show_detail(self, code: int) -> None:
        gestures = normalize_mapping((self.cfg.get("button_map", {}) or {}).get(str(code)))
        action = gestures.get("single", "passthrough")
        self.detail_label.set_text(f"{key_label(code)} · {action_to_text(action)}")

    def _clear_detail(self) -> None:
        self.detail_label.set_text("点击卡片设置动作")

    # ------------------------------------------------------------ 关于页
    def _build_about_page(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(28)
        outer.set_margin_bottom(28)
        outer.set_margin_start(30)
        outer.set_margin_end(30)
        scrolled.set_child(outer)

        group = Adw.PreferencesGroup(title="无线麦 SayAll for Linux")
        group.add(Adw.ActionRow(title="协议", subtitle="ATVV（小米蓝牙遥控器 2 / 2 Pro）"))
        group.add(Adw.ActionRow(title="参考实现", subtitle="HD838A/remote-mic-app、GetSayAll/remote-mic-app-windows"))
        group.add(Adw.ActionRow(title="许可证", subtitle="GPL-3.0-only"))
        group.add(Adw.ActionRow(title="配置文件", subtitle=CONFIG_PATH))
        group.add(Adw.ActionRow(title="状态文件", subtitle=STATUS_PATH))
        outer.append(group)

        links = Adw.PreferencesGroup(title="链接")
        links.add(Adw.ActionRow(title="项目主页", subtitle="github.com/ZXC011/sayall-linux"))
        outer.append(links)
        return scrolled

    # ------------------------------------------------------------ 交互
    def toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=2))

    def _enable_gain(self) -> bool:
        self._gain_ready = True
        return False

    def maybe_show_onboarding(self) -> bool:
        if getattr(self.get_application(), "screenshot", None):
            return False
        if not self.cfg.get("onboarding_done"):
            self.show_onboarding()
        return False

    def show_onboarding(self) -> None:
        if self._onboarding is not None:
            self._onboarding.present()
            return
        self._onboarding = OnboardingDialog(self, lambda: self.on_edit_hotkey(None), self._finish_onboarding)
        self._onboarding.connect("close-request", self._onboarding_closed)
        self._onboarding.present()

    def _onboarding_closed(self, _win) -> bool:
        self._onboarding = None
        return False

    def _finish_onboarding(self) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["onboarding_done"] = True
        save_config(self.cfg)

    def refresh_status(self) -> bool:
        st = load_json(STATUS_PATH, {})
        self.status = st
        connected = bool(st.get("connected"))
        recording = bool(st.get("recording"))
        address = st.get("address") or ""

        if connected:
            self.conn_title.set_text("小米蓝牙语音遥控器")
            self.conn_detail.set_text(f"已连接 · {address}" if address else "已连接")
            self.conn_dot.remove_css_class("dot-pending")
            self.conn_dot.add_css_class("dot-success")
            self.ready_chip.set_text("语音按键已就绪")
        else:
            self.conn_title.set_text("未连接")
            self.conn_detail.set_text("在系统蓝牙设置中连接遥控器")
            self.conn_dot.remove_css_class("dot-success")
            self.conn_dot.add_css_class("dot-pending")
            self.ready_chip.set_text("语音按键待机")

        self.badge.remove_css_class("badge-on")
        self.badge.remove_css_class("badge-off")
        self.badge.remove_css_class("badge-rec")
        if recording:
            self.badge.set_text("录音中")
            self.badge.add_css_class("badge-rec")
        elif connected:
            self.badge.set_text("已连接")
            self.badge.add_css_class("badge-on")
        else:
            self.badge.set_text("未连接")
            self.badge.add_css_class("badge-off")

        battery = st.get("battery")
        self.battery_chip.set_text(f"电量 {battery}%" if battery is not None else "电量 —")

        level = float(st.get("level") or 0)
        self.level.set_value(min(1.0, level * 3))

        if time.time() - self._last_service_check > 5:
            self._last_service_check = time.time()
            threading.Thread(target=self._check_service, daemon=True).start()

        return True

    def _check_service(self) -> None:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "is-active", SERVICE],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
        except Exception:
            out = "unknown"
        GLib.idle_add(self._apply_service_state, out)

    def _apply_service_state(self, state: str) -> bool:
        self._service_state = state
        label = {"active": "运行中", "inactive": "已停止", "failed": "失败"}.get(state, state)
        if getattr(self, "status", {}).get("recording"):
            label += " · 正在录音"
        self.service_label.set_text(label)
        return False

    def vokie_ptt_label(self) -> str:
        try:
            with open(os.path.expanduser("~/.config/vokie/app-settings.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return (data.get("pttConfig") or {}).get("label") or "F9"
        except Exception:
            return "F9"

    def on_engine_changed(self, row, _param) -> None:
        engines = ["vokie", "custom"]
        engine = engines[row.get_selected()]
        self.cfg = reload_config(self.cfg)
        self.cfg["voice_engine"] = engine
        save_config(self.cfg)
        self.update_engine_visibility()
        self.toast("语音引擎已切换")

    def update_engine_visibility(self) -> None:
        engines = ["vokie", "custom"]
        engine = engines[self.engine_row.get_selected()]
        self.vokie_row.set_visible(engine == "vokie")
        self.hotkey_row.set_visible(engine == "vokie")
        self.cmd_start_row.set_visible(engine == "custom")
        self.cmd_stop_row.set_visible(engine == "custom")

    def on_custom_commands(self, _widget) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["voice_start_command"] = self.cmd_start_row.get_text().strip()
        self.cfg["voice_stop_command"] = self.cmd_stop_row.get_text().strip()
        save_config(self.cfg)
        self.toast("自定义命令已保存")

    def on_launch_vokie(self, _btn) -> None:
        subprocess.Popen(
            ["/opt/Vokie/vokie"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.toast("正在启动 Vokie…")

    def on_gain_changed(self, scale) -> None:
        if not getattr(self, "_gain_ready", False):
            return
        value = int(round(scale.get_value()))
        self.gain_value.set_text(f"{value} dB")
        self.cfg = reload_config(self.cfg)
        self.cfg["gain_db"] = float(value)
        save_config(self.cfg)

    def on_preopen_changed(self, switch, _param) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["preopen_mic"] = bool(switch.get_active())
        save_config(self.cfg)
        self.toast("已更新开麦策略")

    def on_restart_service(self, _btn) -> None:
        subprocess.Popen(["systemctl", "--user", "restart", SERVICE])
        self.toast("正在重启桥接服务…")

    def quit_all(self) -> None:
        """停止桥接服务、关闭 Vokie，然后退出界面。"""
        try:
            subprocess.Popen(["systemctl", "--user", "stop", SERVICE])
        except Exception:
            pass
        try:
            pids = subprocess.run(
                ["pgrep", "-f", "[/]opt/Vokie/vokie"], capture_output=True, text=True
            ).stdout.split()
            for pid in pids:
                if pid.isdigit():
                    subprocess.run(["kill", pid], capture_output=True)
        except Exception:
            pass
        self.get_application().quit()

    # ------------------------------------------------------ 按键映射逻辑
    def known_keys(self) -> dict[int, str]:
        keys = {code: name for code, name in KEY_LABELS.items()}
        for item in self.cfg.get("known_keys", []) or []:
            try:
                keys[int(item["code"])] = item.get("name") or f"按键 {item['code']}"
            except Exception:
                pass
        return keys

    def rebuild_rows(self) -> None:
        mapping = self.cfg.get("button_map", {}) or {}
        self.canvas.set_mappings(self.known_keys(), mapping)

    # ------------------------------------------------------ 动作设置弹窗
    def on_edit_action(self, _btn, code: int, gesture: str = "single") -> None:
        mapping = self.cfg.get("button_map", {}) or {}
        gestures = normalize_mapping(mapping.get(str(code)))
        current = gestures.get(gesture, "passthrough")
        current_kind = current.split(":", 1)[0] if ":" in current else current

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=f"{key_label(code)} · {GESTURE_LABELS.get(gesture, '单击')}",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(420, -1)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        labels = [a[1] for a in ACTIONS]
        kinds = [a[0] for a in ACTIONS]
        try:
            index = kinds.index(current_kind)
        except ValueError:
            index = 0
        dropdown = Gtk.DropDown(model=Gtk.StringList.new(labels), selected=index)
        box.append(dropdown)

        entry = Gtk.Entry()
        entry.set_visible(False)
        box.append(entry)

        apps = self.installed_apps()
        app_names = Gtk.StringList.new([a[0] for a in apps] or ["（未找到应用）"])
        app_dropdown = Gtk.DropDown(model=app_names)
        app_dropdown.set_visible(False)
        if current.startswith("app:"):
            current_id = current[4:]
            for i, (_, app_id) in enumerate(apps):
                if app_id == current_id:
                    app_dropdown.set_selected(i)
                    break
        box.append(app_dropdown)

        key_value = current[4:] if current.startswith("key:") else ""
        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        key_box.set_visible(False)
        key_btn = Gtk.Button(label="录制按键…")
        key_display = Gtk.Label(label=key_value or "未录制")
        key_display.add_css_class("monospace")
        key_display.set_hexpand(True)
        key_display.set_halign(Gtk.Align.START)
        key_box.append(key_btn)
        key_box.append(key_display)
        box.append(key_box)

        def on_record(_b) -> None:
            def done(combo: str | None) -> None:
                nonlocal key_value
                if combo:
                    key_value = combo
                    key_display.set_text(combo)

            self.capture_keys(done)

        key_btn.connect("clicked", on_record)

        def on_kind_changed(*_a) -> None:
            kind = kinds[dropdown.get_selected()]
            entry.set_visible(False)
            app_dropdown.set_visible(False)
            key_box.set_visible(False)
            if kind == "app":
                app_dropdown.set_visible(True)
            elif kind == "command":
                entry.set_visible(True)
                entry.set_placeholder_text("命令，如 gedit")
                entry.set_text(current[8:] if current.startswith("command:") else "")
            elif kind == "key":
                key_box.set_visible(True)

        dropdown.connect("notify::selected", on_kind_changed)
        on_kind_changed()

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "取消")
        dialog.add_response("ok", "保存")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_d, response: str) -> None:
            if response != "ok":
                return
            kind = kinds[dropdown.get_selected()]
            if kind == "passthrough":
                gestures.pop(gesture, None)
            elif kind == "disabled":
                gestures[gesture] = "disabled"
            else:
                if kind == "app":
                    idx = app_dropdown.get_selected()
                    value = apps[idx][1] if apps and idx < len(apps) else ""
                elif kind == "key":
                    value = key_value
                    if not value:
                        self.toast("请先录制按键")
                        return
                else:
                    value = entry.get_text().strip()
                if not value:
                    self.toast("请输入内容")
                    return
                gestures[gesture] = f"{kind}:{value}"
            if not gestures:
                mapping.pop(str(code), None)
            elif set(gestures) == {"single"}:
                mapping[str(code)] = gestures["single"]
            else:
                mapping[str(code)] = gestures
            self.cfg = reload_config(self.cfg)
            self.cfg["button_map"] = mapping
            save_config(self.cfg)
            self.rebuild_rows()
            self.toast("映射已保存")

        dialog.connect("response", on_response)
        dialog.present()

    def installed_apps(self) -> list[tuple[str, str]]:
        apps = []
        try:
            for info in Gio.AppInfo.get_all():
                if not info.should_show():
                    continue
                app_id = info.get_id()
                if app_id:
                    apps.append((info.get_display_name() or app_id, app_id))
        except Exception:
            pass
        apps.sort(key=lambda x: x[0].lower())
        return apps

    # ------------------------------------------------------ 按键录制弹窗
    def capture_keys(self, on_done) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="录制按键组合",
            body="按下要发送的按键组合，例如 Ctrl+Enter、Ctrl+Win（纯修饰键组合也可以）",
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(360, -1)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        display = Gtk.Label(label="等待按键…")
        display.add_css_class("title-1")
        display.set_focusable(True)
        box.append(display)
        hint = Gtk.Label(label="提示：Ctrl+Win 这类纯修饰键也可以；取消请点「取消」")
        hint.add_css_class("dim-label")
        box.append(hint)
        dialog.set_extra_child(box)

        finished = {"done": False}
        held: list[str] = []

        def finish(combo: str | None) -> None:
            if finished["done"]:
                return
            finished["done"] = True
            dialog.close()
            on_done(combo)

        def mod_token(name: str) -> str | None:
            if name in ("Control_L", "Control_R"):
                return "CTRL"
            if name in ("Alt_L", "Alt_R", "ISO_Level3_Shift"):
                return "ALT"
            if name in ("Shift_L", "Shift_R"):
                return "SHIFT"
            if name in ("Super_L", "Super_R", "Meta_L", "Meta_R"):
                return "SUPER"
            return None

        def update_display() -> None:
            display.set_text("+".join(held) + "+…" if held else "等待按键…")

        def on_pressed(_controller, keyval, _keycode, _state) -> bool:
            name = Gdk.keyval_name(keyval) or ""
            token = mod_token(name)
            if token:
                if token not in held:
                    held.append(token)
                update_display()
                return True
            key = normalize_key_name(name)
            if not key:
                return True
            finish("+".join(held + [key]))
            return True

        def on_released(_controller, keyval, _keycode, _state) -> bool:
            name = Gdk.keyval_name(keyval) or ""
            token = mod_token(name)
            if token:
                before = list(held)
                if token in held:
                    held.remove(token)
                if len(before) >= 2 and not finished["done"]:
                    finish("+".join(before))
                else:
                    update_display()
                return True
            return True

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", on_pressed)
        controller.connect("key-released", on_released)
        dialog.add_controller(controller)

        dialog.add_response("cancel", "取消")
        dialog.connect("response", lambda _d, _r: finish(None))
        dialog.present()
        GLib.timeout_add(150, lambda: (display.grab_focus(), False)[1])

    # ------------------------------------------------------ Vokie 热键
    def on_edit_hotkey(self, _btn) -> None:
        self.capture_keys(self._apply_hotkey)

    def _apply_hotkey(self, combo: str | None) -> None:
        if not combo:
            return
        cfg = to_vokie_combo(combo)
        if not cfg:
            self.toast("不支持这个组合（可用 Ctrl/Alt/Shift/Win + 普通键）")
            return
        self.toast(f"正在把触发键改为 {cfg['label']}，Vokie 会重启…")
        threading.Thread(target=self._write_vokie_hotkey, args=(cfg,), daemon=True).start()

    def _write_vokie_hotkey(self, combo_cfg: dict) -> None:
        ok = False
        try:
            cfg_path = os.path.expanduser("~/.config/vokie/app-settings.json")
            import shutil

            shutil.copy2(cfg_path, cfg_path + ".bak." + str(int(time.time())))
            for sig in ([], ["-9"]):
                pids = subprocess.run(
                    ["pgrep", "-f", "[/]opt/Vokie/vokie"], capture_output=True, text=True
                ).stdout.split()
                for pid in pids:
                    if pid.isdigit():
                        subprocess.run(["kill"] + sig + [pid], capture_output=True)
                time.sleep(2)
                if not subprocess.run(
                    ["pgrep", "-f", "[/]opt/Vokie/vokie"], capture_output=True
                ).stdout.strip():
                    break
            data = load_json(cfg_path, {})
            data["pttConfig"] = combo_cfg
            tmp = cfg_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent="\t")
            os.replace(tmp, cfg_path)
            subprocess.Popen(
                ["/opt/Vokie/vokie"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(8)
            ok = True
        except Exception as exc:
            print("修改 Vokie 热键失败:", exc)
        GLib.idle_add(self._after_hotkey, combo_cfg.get("label") or "", ok)

    def _after_hotkey(self, key: str, ok: bool) -> bool:
        if ok:
            self.hotkey_value.set_text(f"按住 {key}")
            self.toast(f"已改为 {key}，Vokie 已重启")
        else:
            self.toast("修改失败，请查看终端输出")
        return False

    # ------------------------------------------------------ 截图（README）
    def capture(self, path: str) -> bool:
        save_widget_png(self, path)
        self.get_application().quit()
        return False


class OnboardingDialog(Adw.Window):
    """首次使用引导：安装 Vokie、配对遥控器、启动桥接。"""

    def __init__(self, parent, on_hotkey, on_done) -> None:
        super().__init__(transient_for=parent, modal=True, title="开始使用")
        self.set_default_size(620, 660)
        self.on_hotkey = on_hotkey
        self.on_done = on_done
        self.rows: dict[str, tuple[Gtk.Widget, Gtk.Label, Gtk.Label]] = {}

        header = Adw.HeaderBar()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_top(20)
        content.set_margin_bottom(20)
        content.set_margin_start(24)
        content.set_margin_end(24)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        icon = Gtk.Image.new_from_icon_name("app.sayall.Linux")
        icon.set_pixel_size(64)
        hero.append(icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        h1 = Gtk.Label(label="欢迎使用无线麦", xalign=0)
        h1.add_css_class("page-title")
        h2 = Gtk.Label(label="按顺序完成下面几步，就可以按住遥控器说话了", xalign=0)
        h2.add_css_class("hero-sub")
        texts.append(h1)
        texts.append(h2)
        hero.append(texts)
        content.append(hero)

        steps = [
            ("vokie", "安装 Vokie", "语音识别引擎；安装后从桌面或应用列表启动它", "下载 Vokie", self.open_vokie),
            ("pair", "配对遥控器", "遥控器同时长按「主页 + 菜单」进入配对，再在系统蓝牙中连接 MI RC", "打开蓝牙设置", self.open_bluetooth_settings),
            ("bridge", "启动桥接服务", "负责连接遥控器、虚拟麦克风和按键触发", "启动桥接", self.start_bridge),
            ("hotkey", "设置触发快捷键", "建议使用 F 区按键或 Ctrl+Win 这类组合", "设置", self.edit_hotkey),
        ]
        for key, title, desc, action, callback in steps:
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            card.add_css_class("panel-card")
            dot = Gtk.Box()
            dot.add_css_class("dot")
            dot.add_css_class("dot-pending")
            dot.set_valign(Gtk.Align.START)
            dot.set_margin_top(5)
            card.append(dot)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            text.set_hexpand(True)
            t = Gtk.Label(label=title, xalign=0)
            t.add_css_class("status-title")
            d = Gtk.Label(label=desc, xalign=0)
            d.add_css_class("status-detail")
            d.set_wrap(True)
            text.append(t)
            text.append(d)
            card.append(text)
            btn = Gtk.Button(label=action)
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_a, cb=callback: cb())
            card.append(btn)
            content.append(card)
            self.rows[key] = (dot, t, d, btn)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        content.append(spacer)

        done = Gtk.Button(label="完成")
        done.add_css_class("suggested-action")
        done.set_halign(Gtk.Align.CENTER)
        done.connect("clicked", lambda *_a: self.finish())
        content.append(done)

        toolbar.set_content(content)
        self.set_content(toolbar)
        self.refresh()
        GLib.timeout_add(1500, self._tick)

    def _tick(self) -> bool:
        if not self.get_visible():
            return False
        self.refresh()
        return True

    def refresh(self) -> None:
        vokie = self.vokie_installed()
        paired = self.remote_paired()
        bridge = self.bridge_active()
        hotkey = vokie and self.vokie_ptt_ready()
        for key, ok in (("vokie", vokie), ("pair", paired), ("bridge", bridge), ("hotkey", hotkey)):
            dot = self.rows[key][0]
            dot.remove_css_class("dot-success")
            dot.remove_css_class("dot-pending")
            dot.add_css_class("dot-success" if ok else "dot-pending")

        vokie_btn = self.rows["vokie"][3]
        vokie_btn.set_label("打开 Vokie" if vokie else "下载 Vokie")

        pair_btn = self.rows["pair"][3]
        pair_btn.set_label("已配对" if paired else "打开蓝牙设置")
        pair_btn.set_sensitive(not paired)

        bridge_btn = self.rows["bridge"][3]
        bridge_btn.set_label("运行中" if bridge else "启动桥接")
        bridge_btn.set_sensitive(not bridge)

        hotkey_btn = self.rows["hotkey"][3]
        hotkey_btn.set_label("修改" if hotkey else "设置")
        hotkey_btn.set_sensitive(vokie)

    @staticmethod
    def remote_paired() -> bool:
        try:
            out = subprocess.run(
                ["bluetoothctl", "devices", "Paired"], capture_output=True, text=True, timeout=4
            ).stdout
            if not out.strip():
                out = subprocess.run(
                    ["bluetoothctl", "devices"], capture_output=True, text=True, timeout=4
                ).stdout
            return any(n in out for n in ("MI RC", "Xiaomi", "小米", "Remote"))
        except Exception:
            return False

    @staticmethod
    def vokie_installed() -> bool:
        return os.path.exists("/opt/Vokie/vokie") or bool(shutil.which("vokie"))

    @staticmethod
    def vokie_ptt_ready() -> bool:
        try:
            with open(os.path.expanduser("~/.config/vokie/app-settings.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            cfg = data.get("pttConfig") or {}
            return bool(cfg.get("keys") or cfg.get("label"))
        except Exception:
            return False

    @staticmethod
    def bridge_active() -> bool:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "is-active", SERVICE], capture_output=True, text=True, timeout=3
            ).stdout.strip()
            return out == "active"
        except Exception:
            return False

    def open_vokie(self) -> None:
        if self.vokie_installed():
            subprocess.Popen(
                ["/opt/Vokie/vokie"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            subprocess.Popen(["gio", "open", "https://vokie.com/download.html"])

    def open_bluetooth_settings(self) -> None:
        try:
            subprocess.Popen(["gnome-control-center", "bluetooth"])
        except Exception:
            subprocess.Popen(["bluetoothctl"])

    def start_bridge(self) -> None:
        # 先 reload，让新装的 user unit 可见；enable 保证下次登录自启
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.Popen(["systemctl", "--user", "enable", "--now", SERVICE])

    def edit_hotkey(self) -> None:
        self.close()
        self.on_hotkey()

    def finish(self) -> None:
        self.on_done()
        self.close()


class App(Adw.Application):
    def __init__(self, screenshot: str | None = None, page: str | None = None) -> None:
        super().__init__(application_id="app.sayall.Linux")
        self.window = None
        self.screenshot = screenshot
        self.page = page

    def do_activate(self) -> None:
        load_css()
        if not self.window:
            self.window = Window(self, page=self.page)
        if self.screenshot:
            self.window.set_default_size(1200, 900)
        if self.screenshot and self.page == "onboarding":
            self.window.set_visible(False)
            dialog = OnboardingDialog(self.window, lambda: None, lambda: None)
            dialog.set_default_size(620, 660)
            dialog.present()
            GLib.timeout_add(1200, self._capture_onboarding, dialog)
            return
        self.window.present()
        if self.screenshot:
            GLib.timeout_add(1000, self.window.capture, self.screenshot)

    def _capture_onboarding(self, dialog) -> bool:
        save_widget_png(dialog, self.screenshot)
        self.quit()
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--screenshot", metavar="PATH")
    parser.add_argument("--page", choices=["status", "mapping", "about", "onboarding"])
    args, _ = parser.parse_known_args()
    sys.exit(App(screenshot=args.screenshot, page=args.page).run([sys.argv[0]]))
