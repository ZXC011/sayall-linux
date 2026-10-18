#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
"""无线麦 SayAll for Linux —— 图形界面（GTK4 + libadwaita）"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

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
    377: "TV",
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
class RemoteDiagram(Gtk.DrawingArea):
    """小米遥控器的简化示意图，点击按键可分配动作。"""

    BUTTONS = [
        (116, "circle", 52, 54, 13),
        (63, "circle", 208, 54, 20),
        (163, "rect", 186, 100, 44, 20),
        (103, "circle", 130, 150, 17),
        (108, "circle", 130, 250, 17),
        (105, "circle", 80, 200, 17),
        (106, "circle", 180, 200, 17),
        (28, "circle", 130, 200, 27),
        (172, "rect", 56, 318, 42, 24),
        (158, "rect", 109, 318, 42, 24),
        (139, "rect", 162, 318, 42, 24),
        (115, "rect", 198, 236, 24, 40),
        (114, "rect", 198, 284, 24, 40),
    ]

    def __init__(self, on_press) -> None:
        super().__init__()
        self.on_press = on_press
        self.mapped: set[int] = set()
        self.set_content_width(260)
        self.set_content_height(380)
        self.set_size_request(260, 380)
        self.set_draw_func(self._draw)
        gesture = Gtk.GestureClick()
        gesture.connect("pressed", self._pressed)
        self.add_controller(gesture)

    def set_mapped(self, codes: set[int]) -> None:
        self.mapped = codes
        self.queue_draw()

    def _hit(self, x: float, y: float) -> int | None:
        for entry in self.BUTTONS:
            code, kind, a, b, w = entry[:5]
            h = entry[5] if len(entry) > 5 else 0
            if kind == "circle":
                if math.hypot(x - a, y - b) <= w:
                    return code
            else:
                if a <= x <= a + w and b <= y <= b + h:
                    return code
        return None

    def _pressed(self, gesture, _n, x, y) -> None:
        code = self._hit(x, y)
        if code is not None:
            self.on_press(code)

    def _draw(self, _area, cr, width, height) -> None:
        # 机身
        self._rounded(cr, 30, 14, 200, 352, 42)
        cr.set_source_rgb(0.12, 0.13, 0.16)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.06)
        cr.set_line_width(1.2)
        cr.stroke()

        # 顶部高光
        self._rounded(cr, 42, 26, 176, 80, 26)
        cr.set_source_rgba(1, 1, 1, 0.04)
        cr.fill()

        for entry in self.BUTTONS:
            code, kind, a, b, w = entry[:5]
            h = entry[5] if len(entry) > 5 else 0
            accent = code == 63
            mapped = code in self.mapped
            cr.set_source_rgba(0.22, 0.24, 0.29, 1) if not accent else cr.set_source_rgb(0.18, 0.49, 0.96)
            if kind == "circle":
                cr.arc(a, b, w, 0, 2 * math.pi)
                cr.fill()
                if mapped:
                    cr.set_source_rgba(0.18, 0.76, 0.49, 0.95)
                    cr.set_line_width(2.4)
                    cr.arc(a, b, w + 3.5, 0, 2 * math.pi)
                    cr.stroke()
            else:
                self._rounded(cr, a, b, w, h, min(w, h) / 2.4)
                cr.fill()
                if mapped:
                    cr.set_source_rgba(0.18, 0.76, 0.49, 0.95)
                    cr.set_line_width(2.4)
                    self._rounded(cr, a - 3, b - 3, w + 6, h + 6, min(w, h) / 2)
                    cr.stroke()

        # 方向键箭头
        cr.set_source_rgba(1, 1, 1, 0.75)
        self._arrow(cr, 130, 150, "up")
        self._arrow(cr, 130, 250, "down")
        self._arrow(cr, 80, 200, "left")
        self._arrow(cr, 180, 200, "right")

        # OK 文本
        cr.select_font_face("Sans", 0, 1)
        cr.set_font_size(11)
        cr.set_source_rgba(1, 1, 1, 0.85)
        ext = cr.text_extents("OK")
        cr.move_to(130 - ext.width / 2, 204)
        cr.show_text("OK")

        # 电源图标
        cr.new_path()
        cr.set_line_width(2)
        cr.set_source_rgba(1, 1, 1, 0.7)
        cr.arc(52, 55, 5, -1.2, 4.3)
        cr.stroke()
        cr.move_to(52, 45)
        cr.line_to(52, 55)
        cr.stroke()

        # 麦克风图标
        cr.new_path()
        cr.set_source_rgba(1, 1, 1, 0.95)
        self._rounded(cr, 203, 40, 10, 18, 5)
        cr.fill()
        cr.set_line_width(2)
        cr.arc(208, 58, 9, 0.2, math.pi - 0.2)
        cr.stroke()
        cr.move_to(208, 67)
        cr.line_to(208, 73)
        cr.stroke()

        # 图标按钮
        cr.new_path()
        cr.set_font_size(10)
        cr.set_source_rgba(1, 1, 1, 0.8)
        ext = cr.text_extents("TV")
        cr.move_to(208 - ext.width / 2, 114)
        cr.show_text("TV")

        cr.set_line_width(2)
        # Home
        cr.move_to(69, 331)
        cr.line_to(77, 323)
        cr.line_to(85, 331)
        cr.move_to(72, 331)
        cr.line_to(72, 339)
        cr.line_to(82, 339)
        cr.line_to(82, 331)
        cr.stroke()
        # Back
        cr.move_to(136, 330)
        cr.line_to(124, 330)
        cr.move_to(124, 330)
        cr.line_to(130, 325)
        cr.move_to(124, 330)
        cr.line_to(130, 335)
        cr.stroke()
        # Menu
        for dy in (324, 330, 336):
            cr.move_to(176, dy)
            cr.line_to(190, dy)
        cr.stroke()
        # 音量
        cr.set_font_size(11)
        ext = cr.text_extents("+")
        cr.move_to(217 - ext.width / 2, 262)
        cr.show_text("+")
        ext = cr.text_extents("−")
        cr.move_to(217 - ext.width / 2, 312)
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
        size = 6
        if direction == "up":
            pts = [(x, y - size), (x - size, y + size), (x + size, y + size)]
        elif direction == "down":
            pts = [(x, y + size), (x - size, y - size), (x + size, y - size)]
        elif direction == "left":
            pts = [(x - size, y), (x + size, y - size), (x + size, y + size)]
        else:
            pts = [(x + size, y), (x - size, y - size), (x - size, y + size)]
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.fill()


# ---------------------------------------------------------------- 窗口
class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, page: str | None = None) -> None:
        super().__init__(application=app, title="无线麦")
        self.set_default_size(980, 760)

        self.cfg = load_json(CONFIG_PATH, {})
        self.status = load_json(STATUS_PATH, {})
        self.rows: dict[int, Adw.ActionRow] = {}
        self._learn_waiting = False
        self._service_state = "unknown"
        self._last_service_check = 0.0
        self._gain_ready = False

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
        self.toast_overlay.set_child(root)
        self.set_content(self.toast_overlay)

        if page:
            self._show_page(page)
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

        footer = Gtk.Label(label="v0.2.0 · GPL-3.0", xalign=0)
        footer.add_css_class("brand-sub")
        footer.set_margin_start(18)
        footer.set_margin_bottom(14)
        box.append(footer)
        return box

    def _on_nav_selected(self, _listbox, row: Gtk.ListBoxRow) -> None:
        if row is not None:
            self.stack.set_visible_child_name(row.get_name())

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

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(28)
        outer.set_margin_bottom(28)
        outer.set_margin_start(30)
        outer.set_margin_end(30)
        scrolled.set_child(outer)

        # Hero
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.add_css_class("hero")
        icon = Gtk.Image.new_from_icon_name("app.sayall.Linux")
        icon.set_pixel_size(72)
        hero.append(icon)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.hero_title = Gtk.Label(label="小米遥控器", xalign=0)
        self.hero_title.add_css_class("hero-title")
        self.hero_sub = Gtk.Label(label="等待连接…", xalign=0)
        self.hero_sub.add_css_class("hero-sub")
        texts.append(self.hero_title)
        texts.append(self.hero_sub)
        hero.append(texts)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_valign(Gtk.Align.CENTER)
        self.pill = Gtk.Label(label="未连接")
        self.pill.add_css_class("pill")
        self.pill.add_css_class("pill-off")
        self.pill.set_halign(Gtk.Align.END)
        self.battery_label = Gtk.Label(label="—")
        self.battery_label.add_css_class("battery")
        self.battery_label.set_halign(Gtk.Align.END)
        right.append(self.pill)
        right.append(self.battery_label)
        hero.append(right)
        outer.append(hero)

        # 语音输入
        outer.append(self._section_title("语音输入"))
        group = Adw.PreferencesGroup()
        outer.append(group)

        self.engine_row = Adw.ComboRow(
            title="识别引擎",
            subtitle="遥控器语音键触发的软件",
            model=Gtk.StringList.new(["Vokie", "自定义命令"]),
        )
        engines = ["vokie", "custom"]
        current_engine = (self.cfg.get("voice_engine") or "vokie").lower()
        self.engine_row.set_selected(engines.index(current_engine) if current_engine in engines else 0)
        self.engine_row.connect("notify::selected", self.on_engine_changed)
        group.add(self.engine_row)

        self.vokie_row = Adw.ActionRow(title="Vokie 运行", subtitle="点击右侧按钮启动/唤醒")
        start_vokie = Gtk.Button(label="启动 Vokie")
        start_vokie.set_valign(Gtk.Align.CENTER)
        start_vokie.connect("clicked", self.on_launch_vokie)
        self.vokie_row.add_suffix(start_vokie)
        group.add(self.vokie_row)

        self.hotkey_row = Adw.ActionRow(
            title="触发快捷键",
            subtitle=f"按住 {self.vokie_ptt_label()}（建议 F 区按键）",
        )
        edit_hotkey = Gtk.Button(label="修改")
        edit_hotkey.set_valign(Gtk.Align.CENTER)
        edit_hotkey.connect("clicked", self.on_edit_hotkey)
        self.hotkey_row.add_suffix(edit_hotkey)
        group.add(self.hotkey_row)

        self.cmd_start_row = Adw.EntryRow(title="启动命令", text=self.cfg.get("voice_start_command") or "")
        self.cmd_stop_row = Adw.EntryRow(title="停止命令", text=self.cfg.get("voice_stop_command") or "")
        self.cmd_start_row.connect("apply", self.on_custom_commands)
        self.cmd_stop_row.connect("apply", self.on_custom_commands)
        group.add(self.cmd_start_row)
        group.add(self.cmd_stop_row)

        # 音频
        outer.append(self._section_title("音频"))
        audio = Adw.PreferencesGroup()
        outer.append(audio)

        self.level = Gtk.LevelBar()
        self.level.set_min_value(0)
        self.level.set_max_value(1)
        self.level.set_size_request(200, -1)
        self.level.set_valign(Gtk.Align.CENTER)
        self.level.add_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH, 0.85)
        row_level = Adw.ActionRow(title="麦克风电平")
        row_level.add_suffix(self.level)
        audio.add(row_level)

        adj = Gtk.Adjustment(value=float(self.cfg.get("gain_db", 6.0)), lower=0, upper=24, step_increment=1)
        self.gain_row = Adw.SpinRow(title="输入增益", subtitle="遥控器声音偏小，建议 6–12 dB", adjustment=adj, digits=1)
        self.gain_row.connect("notify::value", self.on_gain_changed)
        audio.add(self.gain_row)

        self.preopen_row = Adw.SwitchRow(
            title="保持开麦",
            subtitle="开麦会话约 60 秒超时，自动续开以保证按键即有声音",
            active=bool(self.cfg.get("preopen_mic", True)),
        )
        self.preopen_row.connect("notify::active", self.on_preopen_changed)
        audio.add(self.preopen_row)

        audio.add(Adw.ActionRow(title="虚拟麦克风", subtitle="sayall-mic（按键时自动切换为默认录音源）"))

        # 服务
        outer.append(self._section_title("服务"))
        service_group = Adw.PreferencesGroup()
        outer.append(service_group)
        self.row_service = Adw.ActionRow(title="桥接服务", subtitle="检查中…")
        restart = Gtk.Button(label="重启")
        restart.set_valign(Gtk.Align.CENTER)
        restart.connect("clicked", self.on_restart_service)
        self.row_service.add_suffix(restart)
        service_group.add(self.row_service)

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

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(28)
        outer.set_margin_bottom(28)
        outer.set_margin_start(30)
        outer.set_margin_end(30)
        scrolled.set_child(outer)

        outer.append(self._section_title("遥控器"))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("diagram")
        card.set_halign(Gtk.Align.CENTER)
        card.set_size_request(420, -1)
        self.diagram = RemoteDiagram(self.on_diagram_press)
        self.diagram.set_halign(Gtk.Align.CENTER)
        card.append(self.diagram)
        hint = Gtk.Label(label="点击遥控器上的按键，为它分配动作")
        hint.add_css_class("diagram-hint")
        card.append(hint)
        outer.append(card)

        learn_btn = Gtk.Button(label="学习新按键")
        learn_btn.add_css_class("suggested-action")
        learn_btn.set_valign(Gtk.Align.CENTER)
        learn_btn.connect("clicked", self.on_learn)
        learn_row = Adw.ActionRow(title="添加按键", subtitle="点击后按一下遥控器上的键")
        learn_row.add_suffix(learn_btn)

        self.group_map = Adw.PreferencesGroup(title="按键映射", description="点右侧按钮分配动作。语音键固定用于语音输入。")
        self.group_map.add(learn_row)
        outer.append(self.group_map)

        self.rebuild_rows()
        return scrolled

    def on_diagram_press(self, code: int) -> None:
        if code == 63:
            self.toast("语音键固定用于语音输入")
            return
        self.on_edit_action(None, code)

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

    def refresh_status(self) -> bool:
        st = load_json(STATUS_PATH, {})
        self.status = st
        connected = bool(st.get("connected"))
        recording = bool(st.get("recording"))
        address = st.get("address") or ""

        if connected:
            self.hero_title.set_text("小米遥控器")
            self.hero_sub.set_text("已连接 · 按住遥控器语音键说话")
            self.pill.set_text("录音中" if recording else "已连接")
            self.pill.remove_css_class("pill-off")
            self.pill.add_css_class("pill-rec" if recording else "pill-on")
        else:
            self.hero_title.set_text("未连接")
            self.hero_sub.set_text("请在系统蓝牙设置中连接遥控器")
            self.pill.set_text("未连接")
            self.pill.remove_css_class("pill-on")
            self.pill.remove_css_class("pill-rec")
            self.pill.add_css_class("pill-off")

        battery = st.get("battery")
        self.battery_label.set_text(f"{battery}%  ·  {address}" if battery is not None else (address or "—"))

        level = float(st.get("level") or 0)
        self.level.set_value(min(1.0, level * 3))

        if time.time() - self._last_service_check > 5:
            self._last_service_check = time.time()
            threading.Thread(target=self._check_service, daemon=True).start()
        state_text = {"active": "运行中", "inactive": "已停止", "failed": "失败"}.get(self._service_state, self._service_state)
        self.row_service.set_subtitle(state_text + (" · 正在录音" if recording else ""))

        learned = st.get("learned_key")
        if learned and self._learn_waiting:
            self._learn_waiting = False
            self.on_learned(learned)
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
        return False

    # ------------------------------------------------------- 引擎/配置
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

    def on_custom_commands(self, _row) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["voice_start_command"] = self.cmd_start_row.get_text().strip()
        self.cfg["voice_stop_command"] = self.cmd_stop_row.get_text().strip()
        save_config(self.cfg)
        self.toast("自定义命令已保存")

    def on_launch_vokie(self, _btn) -> None:
        subprocess.Popen(
            ["gtk-launch", "vokie"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.toast("正在启动 Vokie…")

    def on_gain_changed(self, row, _param) -> None:
        if not getattr(self, "_gain_ready", False):
            return
        self.cfg = reload_config(self.cfg)
        self.cfg["gain_db"] = round(row.get_value(), 1)
        save_config(self.cfg)
        self.toast(f"增益已设为 {self.cfg['gain_db']} dB")

    def on_preopen_changed(self, row, _param) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["preopen_mic"] = bool(row.get_active())
        save_config(self.cfg)
        self.toast("已更新开麦策略")

    def on_restart_service(self, _btn) -> None:
        subprocess.Popen(["systemctl", "--user", "restart", SERVICE])
        self.toast("正在重启桥接服务…")

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
        for row in list(self.rows.values()):
            self.group_map.remove(row)
        self.rows.clear()

        mapping = self.cfg.get("button_map", {}) or {}
        order = [63, 28, 103, 108, 105, 106, 158, 172, 139, 163, 115, 114, 113, 116]

        def sort_key(code: int) -> int:
            try:
                return order.index(code)
            except ValueError:
                return len(order) + code

        for code in sorted(self.known_keys(), key=sort_key):
            name = self.known_keys()[code]
            action = mapping.get(str(code), "passthrough")
            row = Adw.ActionRow(
                title=key_label(code, name),
                subtitle=f"键码 {code} · {action_to_text(action)}",
            )
            if code == 63:
                badge = Gtk.Label(label="语音输入")
                badge.add_css_class("dim-label")
                row.add_suffix(badge)
            else:
                btn = Gtk.Button(label="设置")
                btn.add_css_class("flat")
                btn.connect("clicked", self.on_edit_action, code)
                row.add_suffix(btn)
            self.group_map.add(row)
            self.rows[code] = row

        self.diagram.set_mapped(set(int(k) for k in mapping.keys() if str(k).isdigit()))

    def on_learn(self, _btn) -> None:
        self.cfg = reload_config(self.cfg)
        self.cfg["learn_request"] = True
        save_config(self.cfg)
        self._learn_waiting = True
        self.toast("请按一下遥控器上的按键…")

    def on_learned(self, learned: dict) -> None:
        self.cfg = reload_config(self.cfg)
        code = int(learned.get("code"))
        name = learned.get("name") or f"按键 {code}"
        known = self.cfg.setdefault("known_keys", [])
        if not any(int(k.get("code", -1)) == code for k in known):
            known.append({"code": code, "name": name})
        self.cfg["learn_request"] = False
        save_config(self.cfg)
        self.rebuild_rows()
        self.toast(f"已添加：{key_label(code, name)}（键码 {code}）")

    # ------------------------------------------------------ 动作设置弹窗
    def on_edit_action(self, _btn, code: int) -> None:
        mapping = self.cfg.get("button_map", {}) or {}
        current = mapping.get(str(code), "passthrough")
        current_kind = current.split(":", 1)[0] if ":" in current else current

        dialog = Adw.MessageDialog(transient_for=self, heading=f"{key_label(code)} · 选择动作")
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
                mapping.pop(str(code), None)
            elif kind == "disabled":
                mapping[str(code)] = "disabled"
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
                mapping[str(code)] = f"{kind}:{value}"
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
                ["gtk-launch", "vokie"],
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
            self.hotkey_row.set_subtitle(f"按住 {key}（建议 F 区按键）")
            self.toast(f"已改为 {key}，Vokie 已重启")
        else:
            self.toast("修改失败，请查看终端输出")
        return False

    # ------------------------------------------------------ 截图（README）
    def capture(self, path: str) -> bool:
        try:
            width = self.get_width()
            height = self.get_height()
            paintable = Gtk.WidgetPaintable.new(self)
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            renderer = self.get_renderer()
            texture = renderer.render_texture(node, None)
            texture.save_to_png(path)
            print(f"saved {path} ({width}x{height})")
        except Exception as exc:
            print("screenshot failed:", exc)
        self.get_application().quit()
        return False


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
            self.window.set_default_size(1000, 900)
        self.window.present()
        if self.screenshot:
            GLib.timeout_add(1000, self.window.capture, self.screenshot)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--screenshot", metavar="PATH")
    parser.add_argument("--page", choices=["status", "mapping", "about"])
    args, _ = parser.parse_known_args()
    sys.exit(App(screenshot=args.screenshot, page=args.page).run([sys.argv[0]]))
