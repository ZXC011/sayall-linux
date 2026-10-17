#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
"""无线麦 SayAll for Linux —— 图形界面（GTK4 + libadwaita）"""

from __future__ import annotations

import json
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

# 常见按键：键码 -> 中文名
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
    163: "下一曲 / TV",
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
    """合并磁盘上的最新配置，避免覆盖桥接进程写入的字段。"""
    fresh = load_json(CONFIG_PATH, {})
    merged = dict(current)
    merged.update(fresh)
    return merged


def action_to_text(action: str, cfg: dict) -> str:
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


def to_vokie_combo(combo: str) -> dict | None:
    """把 'CTRL+ALT+D' 或 'CTRL+SUPER' 转成 Vokie pttConfig 的组合键结构。"""
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
            "uniKey": keys[-1],
            "vkCodes": [],
            "label": "+".join(labels),
            "modifiers": modifiers,
            "keys": keys,
            "exclusive": True,
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
        "uniKey": token,
        "vkCodes": [],
        "label": "+".join(labels),
        "modifiers": modifiers,
        "keys": keys,
        "exclusive": True,
    }


def key_label(code: int, name: str | None = None) -> str:
    return KEY_LABELS.get(code) or (name or f"按键 {code}")


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


class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, page: str | None = None) -> None:
        super().__init__(application=app, title="无线麦")
        self.set_default_size(860, 700)

        self.cfg = load_json(CONFIG_PATH, {})
        self.status = load_json(STATUS_PATH, {})
        self.rows: dict[int, Adw.ActionRow] = {}
        self._learn_waiting = False
        self._service_state = "unknown"
        self._last_service_check = 0.0

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        toolbar = Adw.ToolbarView()
        self.toast_overlay.set_child(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        restore_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="重启桥接服务")
        restore_btn.connect("clicked", self.on_restart_service)
        header.pack_end(restore_btn)

        toolbar.set_content(self.stack)
        self.build_status_page()
        self.build_mapping_page()
        self.build_about_page()
        if page:
            self.stack.set_visible_child_name(page)
        self._gain_ready = False
        GLib.timeout_add(800, self._enable_gain)
        GLib.timeout_add(600, self.refresh_status)

    def _enable_gain(self) -> bool:
        self._gain_ready = True
        return False

    def capture(self, path: str) -> bool:
        """把窗口内容渲染成 PNG（用于 README 截图）。"""
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

    # ------------------------------------------------------------ 状态页
    def build_status_page(self) -> None:
        page = Adw.PreferencesPage()
        self.stack.add_titled_with_icon(page, "status", "状态", "audio-input-microphone-symbolic")

        self.group_device = Adw.PreferencesGroup(title="遥控器")
        page.add(self.group_device)
        self.row_conn = Adw.ActionRow(title="连接状态", subtitle="未连接")
        self.group_device.add(self.row_conn)
        self.row_battery = Adw.ActionRow(title="电量", subtitle="—")
        self.group_device.add(self.row_battery)
        self.row_lastkey = Adw.ActionRow(title="最近按键", subtitle="—")
        self.group_device.add(self.row_lastkey)

        group_voice = Adw.PreferencesGroup(title="语音输入")
        page.add(group_voice)

        self.engine_row = Adw.ComboRow(
            title="识别引擎",
            subtitle="遥控器语音键触发的软件",
            model=Gtk.StringList.new(["Vokie", "自定义命令"]),
        )
        engines = ["vokie", "custom"]
        current_engine = (self.cfg.get("voice_engine") or "vokie").lower()
        self.engine_row.set_selected(engines.index(current_engine) if current_engine in engines else 0)
        self.engine_row.connect("notify::selected", self.on_engine_changed)
        group_voice.add(self.engine_row)

        self.vokie_row = Adw.ActionRow(title="Vokie 运行", subtitle="点击右侧按钮启动/唤醒")
        start_vokie = Gtk.Button(label="启动 Vokie")
        start_vokie.set_valign(Gtk.Align.CENTER)
        start_vokie.connect("clicked", self.on_launch_vokie)
        self.vokie_row.add_suffix(start_vokie)
        group_voice.add(self.vokie_row)

        self.hotkey_row = Adw.ActionRow(
            title="触发快捷键",
            subtitle=f"按住 {self.vokie_ptt_label()}（建议 F 区按键）",
        )
        edit_hotkey = Gtk.Button(label="修改")
        edit_hotkey.set_valign(Gtk.Align.CENTER)
        edit_hotkey.connect("clicked", self.on_edit_hotkey)
        self.hotkey_row.add_suffix(edit_hotkey)
        group_voice.add(self.hotkey_row)

        self.cmd_start_row = Adw.EntryRow(title="启动命令", text=self.cfg.get("voice_start_command") or "")
        self.cmd_stop_row = Adw.EntryRow(title="停止命令", text=self.cfg.get("voice_stop_command") or "")
        self.cmd_start_row.connect("apply", self.on_custom_commands)
        self.cmd_stop_row.connect("apply", self.on_custom_commands)
        group_voice.add(self.cmd_start_row)
        group_voice.add(self.cmd_stop_row)

        self.row_service = Adw.ActionRow(title="桥接服务", subtitle="检查中…")
        group_voice.add(self.row_service)

        self.level = Gtk.LevelBar()
        self.level.set_min_value(0)
        self.level.set_max_value(1)
        self.level.set_size_request(180, -1)
        self.level.set_valign(Gtk.Align.CENTER)
        self.level.add_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH, 0.8)
        row_level = Adw.ActionRow(title="麦克风电平")
        row_level.add_suffix(self.level)
        group_voice.add(row_level)

        adj = Gtk.Adjustment(value=float(self.cfg.get("gain_db", 12.0)), lower=0, upper=24, step_increment=1)
        self.gain_row = Adw.SpinRow(title="输入增益", subtitle="遥控器声音偏小，建议 12–18 dB", adjustment=adj, digits=1)
        self.gain_row.connect("notify::value", self.on_gain_changed)
        group_voice.add(self.gain_row)

        self.preopen_row = Adw.SwitchRow(
            title="保持开麦",
            subtitle="遥控器开麦约 60 秒超时，自动续开以保证按键即有声音",
            active=bool(self.cfg.get("preopen_mic", True)),
        )
        self.preopen_row.connect("notify::active", self.on_preopen_changed)
        group_voice.add(self.preopen_row)

        hint = Adw.ActionRow(
            title="虚拟麦克风",
            subtitle="sayall-mic（按键时自动切换为系统默认录音源）",
        )
        group_voice.add(hint)
        self.update_engine_visibility()

    # ------------------------------------------------------------ 按键页
    def build_mapping_page(self) -> None:
        page = Adw.PreferencesPage()
        self.stack.add_titled_with_icon(page, "mapping", "按键", "input-keyboard-symbolic")

        self.group_map = Adw.PreferencesGroup(
            title="按键映射",
            description="点右侧按钮分配动作。语音键固定用于语音输入。",
        )
        page.add(self.group_map)

        learn_btn = Gtk.Button(label="学习新按键")
        learn_btn.add_css_class("suggested-action")
        learn_btn.set_valign(Gtk.Align.CENTER)
        learn_btn.connect("clicked", self.on_learn)
        learn_row = Adw.ActionRow(title="添加按键", subtitle="点击后按一下遥控器上的键")
        learn_row.add_suffix(learn_btn)
        self.group_map.add(learn_row)

        self.rebuild_rows()

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
            row = Adw.ActionRow(title=key_label(code, name), subtitle=f"键码 {code} · {action_to_text(action, self.cfg)}")

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

    # ------------------------------------------------------------ 关于页
    def build_about_page(self) -> None:
        page = Adw.PreferencesPage()
        self.stack.add_titled_with_icon(page, "about", "关于", "help-about-symbolic")

        group = Adw.PreferencesGroup(title="无线麦 SayAll for Linux")
        page.add(group)
        group.add(Adw.ActionRow(title="协议", subtitle="ATVV（小米蓝牙遥控器 2 / 2 Pro）"))
        group.add(Adw.ActionRow(title="参考实现", subtitle="HD838A/remote-mic-app、GetSayAll/remote-mic-app-windows"))
        group.add(Adw.ActionRow(title="配置文件", subtitle=CONFIG_PATH))
        group.add(Adw.ActionRow(title="状态文件", subtitle=STATUS_PATH))

        service_group = Adw.PreferencesGroup(title="服务")
        page.add(service_group)
        restart = Gtk.Button(label="重启桥接服务")
        restart.connect("clicked", self.on_restart_service)
        row = Adw.ActionRow(title="sayall-bridge")
        row.add_suffix(restart)
        service_group.add(row)

    # ------------------------------------------------------------ 交互
    def toast(self, text: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=2))

    def refresh_status(self) -> bool:
        st = load_json(STATUS_PATH, {})
        self.status = st
        connected = bool(st.get("connected"))
        self.row_conn.set_subtitle("已连接 · " + (st.get("address") or "") if connected else "未连接")
        battery = st.get("battery")
        self.row_battery.set_subtitle(f"{battery}%" if battery is not None else "—")

        last = st.get("last_key") or {}
        if last:
            self.row_lastkey.set_subtitle(f"{key_label(last.get('code', 0), last.get('name'))}（键码 {last.get('code')}）")

        try:
            if time.time() - self._last_service_check > 5:
                self._last_service_check = time.time()
                threading.Thread(target=self._check_service, daemon=True).start()
        except Exception:
            pass
        self.row_service.set_subtitle({"active": "运行中", "inactive": "已停止", "failed": "失败"}.get(self._service_state, self._service_state))

        level = float(st.get("level") or 0)
        self.level.set_value(min(1.0, level * 3))
        rec = st.get("recording")
        if rec:
            self.row_service.set_subtitle("运行中 · 正在录音")

        learned = st.get("learned_key")
        if learned and self._learn_waiting:
            self._learn_waiting = False
            self.on_learned(learned)

        return True

    def vokie_ptt_label(self) -> str:
        try:
            with open(os.path.expanduser("~/.config/vokie/app-settings.json"), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return (data.get("pttConfig") or {}).get("label") or "F9"
        except Exception:
            return "F9"

    def vokie_hotkey_text(self) -> str:
        return f"按住 {self.vokie_ptt_label()}"

    def on_edit_hotkey(self, _btn) -> None:
        self.capture_keys(self._apply_hotkey)

    def _apply_hotkey(self, combo: str | None) -> None:
        if not combo:
            return
        cfg = to_vokie_combo(combo)
        if not cfg:
            self.toast("不支持这个组合（可用 Ctrl/Alt/Shift/Super + 普通键）")
            return
        self.toast(f"正在把触发键改为 {cfg['label']}，Vokie 会重启…")
        threading.Thread(target=self._write_vokie_hotkey, args=(cfg,), daemon=True).start()

    def _write_vokie_hotkey(self, combo_cfg: dict) -> None:
        ok = False
        try:
            cfg_path = os.path.expanduser("~/.config/vokie/app-settings.json")
            import shutil

            shutil.copy2(cfg_path, cfg_path + ".bak." + str(int(time.time())))
            # 先退出 Vokie（它退出时会写回配置）
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

    def on_engine_changed(self, row, _param) -> None:
        engines = ["vokie", "custom"]
        engine = engines[row.get_selected()]
        self.cfg = reload_config(self.cfg)
        self.cfg["voice_engine"] = engine
        save_config(self.cfg)
        self.update_engine_visibility()
        self.toast("语音引擎已切换")

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

    def update_engine_visibility(self) -> None:
        engines = ["vokie", "custom"]
        engine = engines[self.engine_row.get_selected()]
        self.vokie_row.set_visible(engine == "vokie")
        self.hotkey_row.set_visible(engine == "vokie")
        self.cmd_start_row.set_visible(engine == "custom")
        self.cmd_stop_row.set_visible(engine == "custom")

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
        self.row_service.set_subtitle({"active": "运行中", "inactive": "已停止", "failed": "失败"}.get(state, state))
        return False

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

        # 按键组合：录制，不用手输
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

    def capture_keys(self, on_done) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="录制按键组合",
            body="按下要发送的按键组合，例如 Ctrl+Enter、Ctrl+Win（纯修饰键组合也可以；Esc 也会被录制；取消请点下方按钮）",
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


class App(Adw.Application):
    def __init__(self, screenshot: str | None = None, page: str | None = None) -> None:
        super().__init__(application_id="app.sayall.Linux")
        self.window = None
        self.screenshot = screenshot
        self.page = page

    def do_activate(self) -> None:
        if not self.window:
            self.window = Window(self, page=self.page)
        if self.screenshot:
            self.window.set_default_size(900, 1000)
        self.window.present()
        if self.screenshot:
            GLib.timeout_add(900, self.window.capture, self.screenshot)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--screenshot", metavar="PATH")
    parser.add_argument("--page", choices=["status", "mapping", "about"])
    args, _ = parser.parse_known_args()
    sys.exit(App(screenshot=args.screenshot, page=args.page).run([sys.argv[0]]))
