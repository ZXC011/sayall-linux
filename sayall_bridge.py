#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 ZXC011
"""
SayAll for Linux —— 小米蓝牙遥控器 2 / 2 Pro → Linux 语音输入桥接

借鉴 HD838A/remote-mic-app (SayAll.app) 与 GetSayAll/remote-mic-app-windows
的 ATVV 协议实现：

  遥控器(BLE, ATVV GATT)
     ├─ Audio   AB5E0003  16kHz 单声道 IMA/DVI ADPCM
     └─ Control AB5E0004  能力 / 流开始 / 流结束 / 解码器同步
  → 解码 PCM → PipeWire 虚拟麦克风（sayall-mic）
  → 遥控器语音键（F5）按住/松开 → 触发 Vokie 录音

用法:
  sayall-bridge run        启动桥接（前台）
  sayall-bridge doctor     环境自检
  sayall-bridge scan       扫描附近设备
"""

from __future__ import annotations

import argparse
import array
import asyncio
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Optional

# ---------------------------------------------------------------- ATVV 常量
SERVICE_UUID = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
TRANSMIT_UUID = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
AUDIO_UUID = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
CONTROL_UUID = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

DEVICE_NAMES = {
    "mi rc",
    "xiaomi bluetooth remote 2",
    "xiaomi bluetooth remote 2 pro",
    "小米蓝牙语音遥控器",
    "小米蓝牙遥控器",
}
XIAOMI_VID = 0x2717
REMOTE_PIDS = {0x32B8, 0x32B9, 0x32BA, 0x32BB}  # RC003 及同系列

VIRTUAL_MIC_NAME = "sayall-mic"
VIRTUAL_SINK_NAME = "sayall-mic-sink"
SAMPLE_RATE = 16000
DEBUG = os.environ.get("SAYALL_DEBUG") == "1"

# 手势时间窗口
DOUBLE_MS = 300
LONG_MS = 500


def normalize_mapping(value) -> dict:
    """把配置里的按键动作归一化为 {single/double/long: action}。"""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if isinstance(v, str) and v}
    if isinstance(value, str) and value:
        return {"single": value}
    return {}

KEY_F5 = 63
KEY_F20 = 191
KEY_REFRESH = 173
KEY_POWER = 116
# 遥控器语音键实测是 KEY_F5（code 63）；F20 预留给 hwdb 重映射，REFRESH 兼容其他型号
VOICE_KEYS = {KEY_F5, KEY_F20, KEY_REFRESH}
SWALLOWED_KEYS = VOICE_KEYS | {KEY_POWER}

VOKIE_SETTINGS = os.path.expanduser("~/.config/vokie/app-settings.json")

# Electron 按键名 -> evdev 名字
VOKIE_KEY_MAP = {
    "RightAlt": "RIGHTALT", "LeftAlt": "LEFTALT",
    "RightCtrl": "RIGHTCTRL", "LeftCtrl": "LEFTCTRL",
    "RightShift": "RIGHTSHIFT", "LeftShift": "LEFTSHIFT",
    "Super": "LEFTMETA", "Meta": "LEFTMETA", "Cmd": "LEFTMETA", "Win": "LEFTMETA",
    "Ctrl": "LEFTCTRL", "Control": "LEFTCTRL", "Alt": "LEFTALT", "Option": "LEFTALT",
    "Shift": "LEFTSHIFT", "Return": "ENTER", "Enter": "ENTER",
}


def read_vokie_hotkey() -> list[int]:
    """读取 Vokie 的按住说话热键（默认右 Alt）。"""
    import evdev

    keys = ["RightAlt"]
    try:
        with open(VOKIE_SETTINGS, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        keys = (data.get("pttConfig") or {}).get("keys") or keys
    except Exception:
        pass
    codes = []
    for key in keys:
        name = VOKIE_KEY_MAP.get(key, key).upper().replace("-", "")
        code = getattr(evdev.ecodes, "KEY_" + name, None)
        if code is not None:
            codes.append(code)
    return codes or [evdev.ecodes.KEY_RIGHTALT]


class ImaAdpcmDecoder:
    """与 sayall-core/src/adpcm.rs 逐位一致：高半字节优先。"""

    STEP_TABLE = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
        45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
        209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
        796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
        2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
        7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
        20350, 22385, 24623, 27086, 29794, 32767,
    ]
    INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8]

    def __init__(self) -> None:
        self.predictor = 0
        self.step_index = 0

    def reset(self, predictor: int, step_index: int) -> None:
        self.predictor = max(-32768, min(32767, int(predictor)))
        self.step_index = max(0, min(88, int(step_index)))

    def decode(self, data: bytes) -> array.array:
        out = array.array("h")
        append = out.append
        for byte in data:
            for nibble in (byte >> 4, byte & 0x0F):
                step = self.STEP_TABLE[self.step_index]
                diff = step >> 3
                if nibble & 1:
                    diff += step >> 2
                if nibble & 2:
                    diff += step >> 1
                if nibble & 4:
                    diff += step
                if nibble & 8:
                    self.predictor -= diff
                else:
                    self.predictor += diff
                self.predictor = max(-32768, min(32767, self.predictor))
                self.step_index = max(
                    0, min(88, self.step_index + self.INDEX_TABLE[nibble & 7])
                )
                append(self.predictor)
        return out


def process_pcm(samples: array.array, gain_db: float = 0.0) -> array.array:
    """3 点平滑 + 安全增益（对齐 sayall-core/src/pcm.rs）。"""
    n = len(samples)
    if n == 0:
        return samples
    out = array.array("h", samples)
    if n >= 3:
        for i in range(1, n - 1):
            out[i] = (samples[i - 1] + 2 * samples[i] + samples[i + 1]) >> 2
    if not math.isfinite(gain_db):
        gain_db = 0.0
    gain_db = max(-24.0, min(24.0, gain_db))
    if gain_db != 0.0:
        gain = 10.0 ** (gain_db / 20.0)
        for i in range(n):
            out[i] = max(-32768, min(32767, int(round(out[i] * gain))))
    return out


# --------------------------------------------------------------- ATVV 解析
class AtvvCapabilities:
    def __init__(self) -> None:
        self.version = 0x0100
        self.codecs = 0x02
        self.interaction = 0x03
        self.frame_size = 120
        self.selected_codec = 0x02
        self.sample_rate = SAMPLE_RATE

    @classmethod
    def parse(cls, b: bytes) -> "AtvvCapabilities":
        if len(b) < 7 or b[0] != 0x0B:
            raise ValueError("不是 ATVV 能力包")
        cap = cls()
        cap.version = (b[1] << 8) | b[2]
        if cap.version >= 0x0100:
            cap.codecs = b[3]
            cap.interaction = b[4]
        else:
            if len(b) < 9:
                raise ValueError("能力包过短")
            cap.codecs = b[4]
            cap.interaction = 0
        if cap.version >= 0x0100 and cap.codecs == 0 and len(b) >= 9 and b[4] & 0x03:
            cap.codecs = b[4]
            cap.interaction = 0x03
        cap.selected_codec = 0x02 if cap.codecs & 0x02 else 0x01
        cap.sample_rate = SAMPLE_RATE if cap.selected_codec == 0x02 else 8000
        frame_size = (b[5] << 8) | b[6]
        cap.frame_size = frame_size or 120
        return cap

    @property
    def is_supported(self) -> bool:
        return self.sample_rate == SAMPLE_RATE


class FrameAccumulator:
    def __init__(self) -> None:
        self.buf = bytearray()

    def append(self, data: bytes, frame_size: int) -> list[bytes]:
        if frame_size <= 0:
            return []
        self.buf.extend(data)
        frames = []
        while len(self.buf) >= frame_size:
            frames.append(bytes(self.buf[:frame_size]))
            del self.buf[:frame_size]
        return frames


# ----------------------------------------------------------- PipeWire 虚拟麦
class VirtualMic:
    """pw-loopback 暴露 sayall-mic，pw-cat 把 PCM 灌进 sayall-mic-sink。"""

    def __init__(self, gain_db: float = 0.0, log=print) -> None:
        self.gain_db = gain_db
        self.log = log
        self.loopback: Optional[subprocess.Popen] = None
        self.pwcat: Optional[subprocess.Popen] = None
        self.node_id: Optional[int] = None

    def start(self) -> None:
        self.stop()
        self.loopback = subprocess.Popen(
            [
                "pw-loopback",
                "-n", VIRTUAL_MIC_NAME,
                "-c", "1",
                "-m", "[ MONO ]",
                "--capture-props",
                f'media.class=Audio/Sink node.name={VIRTUAL_SINK_NAME} '
                f'node.description="SayAll Mic Sink"',
                "--playback-props",
                f'media.class=Audio/Source node.name={VIRTUAL_MIC_NAME} '
                f'node.description="SayAll Mic"',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.node_id = self._wait_for_node()
        self.pwcat = subprocess.Popen(
            [
                "pw-cat", "--playback", "--target", VIRTUAL_SINK_NAME,
                "--rate", str(SAMPLE_RATE), "--channels", "1",
                "--format", "s16", "--raw", "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.log(f"虚拟麦克风就绪: {VIRTUAL_MIC_NAME} (node {self.node_id})")

    def _wait_for_node(self, timeout: float = 5.0) -> Optional[int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                out = subprocess.run(
                    ["pw-dump"], capture_output=True, text=True, timeout=4
                ).stdout
                for obj in json.loads(out or "[]"):
                    props = obj.get("info", {}).get("props", {})
                    if props.get("node.name") == VIRTUAL_MIC_NAME:
                        return int(obj["id"])
            except Exception:
                pass
            time.sleep(0.15)
        self.log("警告: 没等到虚拟麦克风节点")
        return None

    def write(self, pcm: array.array) -> None:
        if not self.pwcat or self.pwcat.stdin is None:
            return
        try:
            self.pwcat.stdin.write(process_pcm(pcm, self.gain_db).tobytes())
            self.pwcat.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def stop(self) -> None:
        for proc in (self.pwcat, self.loopback):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.pwcat = None
        self.loopback = None


# ----------------------------------------------------------------- 系统工具
def run(cmd: list[str], timeout: float = 6.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_default_source_id() -> Optional[int]:
    try:
        out = run(["wpctl", "inspect", "@DEFAULT_SOURCE@"]).stdout
        first = out.splitlines()[0] if out else ""
        if first.startswith("id "):
            return int(first.split(",")[0].split()[1])
    except Exception:
        pass
    return None


def set_default_source(node_id: Optional[int]) -> bool:
    if node_id is None:
        return False
    return run(["wpctl", "set-default", str(node_id)]).returncode == 0


CONFIG_PATH = os.path.expanduser("~/.config/sayall-linux/config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def find_paired_remote() -> Optional[str]:
    """从 BlueZ 已配对/已连接设备里找遥控器（已连接时设备不会广播）。"""
    try:
        out = run(["bluetoothctl", "devices"]).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.strip().split(" ", 2)
        if len(parts) < 3 or parts[0] != "Device":
            continue
        address, name = parts[1], parts[2].strip().lower()
        if name in DEVICE_NAMES or "xiaomi bluetooth remote" in name:
            return address
    return None


def bluez_device_path(address: str) -> Optional[str]:
    """已连接设备不会广播，直接查 BlueZ D-Bus 对象路径供 bleak 使用。"""
    import re

    try:
        out = run(["busctl", "--system", "tree", "org.bluez"]).stdout
    except Exception:
        return None
    tag = "dev_" + address.upper().replace(":", "_")
    m = re.search(r"(/org/bluez/hci\d+/" + re.escape(tag) + r")\b", out)
    return m.group(1) if m else None


def find_real_source_id() -> Optional[int]:
    """找一个真实麦克风（排除 sayall-mic 和 monitor）。"""
    try:
        out = run(["pw-dump"]).stdout
        for obj in json.loads(out or "[]"):
            props = obj.get("info", {}).get("props", {})
            name = props.get("node.name", "")
            media_class = props.get("media.class", "")
            if media_class != "Audio/Source":
                continue
            if name.startswith(VIRTUAL_MIC_NAME) or name.endswith(".monitor"):
                continue
            return int(obj["id"])
    except Exception:
        pass
    return None


# --------------------------------------------------------------- 语音引擎
class RemoteHid:
    """抓取遥控器键盘设备：语音键(F5)自己用，其它按键通过 uinput 转发。"""

    def __init__(self, on_voice_down, on_voice_up, log=print, get_action=None, perform_action=None) -> None:
        self.on_voice_down = on_voice_down
        self.on_voice_up = on_voice_up
        self.log = log
        self.get_action = get_action
        self.perform_action = perform_action
        self.dev = None
        self.ui = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.handled_keys: set[int] = set()
        self.pending_single: dict[int, threading.Timer] = {}
        self.press_times: dict[int, float] = {}
        self.long_timers: dict[int, threading.Timer] = {}
        self.long_fired: set[int] = set()

    @staticmethod
    def find_keyboard():
        import evdev

        candidates = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except Exception:
                continue
            info = dev.info
            name = (dev.name or "").lower()
            caps = dev.capabilities(verbose=False)
            if "sayall" in name:  # 排除自己创建的虚拟设备
                dev.close()
                continue
            is_keyboard = evdev.ecodes.EV_KEY in caps
            vendor_match = info.vendor == XIAOMI_VID
            name_match = name in DEVICE_NAMES or "xiaomi bluetooth remote" in name
            if is_keyboard and (vendor_match or name_match):
                candidates.append(dev)
            else:
                dev.close()
        return candidates

    def start(self) -> bool:
        if not self.find_keyboard():
            self.log("没找到遥控器键盘设备（先配对/连接遥控器）")
            return False
        self.running = True
        self.thread = threading.Thread(target=self._supervise, daemon=True)
        self.thread.start()
        return True

    def _ensure_uinput(self, dev) -> None:
        import evdev

        if self.ui is not None:
            return
        keys = [c for c in dev.capabilities(verbose=False).get(evdev.ecodes.EV_KEY, []) if c not in SWALLOWED_KEYS]
        try:
            from evdev import UInput

            self.ui = UInput(
                {evdev.ecodes.EV_KEY: keys},
                name="SayAll Remote Passthrough",
                vendor=XIAOMI_VID,
                product=dev.info.product,
                version=dev.info.version,
            )
        except Exception as exc:
            self.log(f"uinput 创建失败（其它按键将无法转发）: {exc}")
            self.ui = None

    def _attach(self) -> bool:
        devices = self.find_keyboard()
        if not devices:
            return False
        dev = devices[0]
        try:
            dev.grab()
        except Exception as exc:
            self.log(f"抓取遥控器设备失败: {exc}")
            dev.close()
            return False
        self.dev = dev
        self._ensure_uinput(dev)
        self.log(f"已接管遥控器键盘: {dev.path} ({dev.name})")
        return True

    def _detach(self) -> None:
        if self.dev:
            try:
                self.dev.ungrab()
            except Exception:
                pass
            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None

    def _fire(self, code: int, action: str | None) -> None:
        if not action:
            return
        if action == "passthrough":
            self._emit_key(code)
            return
        if self.perform_action:
            self.perform_action(action, code)

    def _emit_key(self, code: int) -> None:
        """手势按键的「透传」：把按键补发到虚拟键盘。"""
        import evdev

        if not self.ui:
            return
        try:
            for value in (1, 0):
                self.ui.write_event(evdev.InputEvent(0, 0, evdev.ecodes.EV_KEY, code, value))
            self.ui.syn()
        except Exception:
            pass

    def _long_press(self, code: int, gestures: dict) -> None:
        if code not in self.press_times:
            return
        self.long_fired.add(code)
        self._fire(code, gestures.get("long") or gestures.get("single"))

    def _fire_single(self, code: int, gestures: dict) -> None:
        self.pending_single.pop(code, None)
        if code in self.long_fired:
            self.long_fired.discard(code)
            return
        self._fire(code, gestures.get("single"))

    def _handle_gesture(self, code: int, value: int, gestures: dict) -> None:
        now = time.monotonic()
        if value == 1:
            pending = self.pending_single.pop(code, None)
            if pending is not None:
                pending.cancel()
                self._fire(code, gestures.get("double") or gestures.get("single"))
                return
            self.press_times[code] = now
            if gestures.get("long"):
                timer = threading.Timer(LONG_MS / 1000, self._long_press, args=(code, gestures))
                timer.daemon = True
                self.long_timers[code] = timer
                timer.start()
        elif value == 0:
            pressed = self.press_times.pop(code, None)
            timer = self.long_timers.pop(code, None)
            if timer:
                timer.cancel()
            if pressed is None:
                return
            if code in self.long_fired:
                self.long_fired.discard(code)
                return
            single = threading.Timer(DOUBLE_MS / 1000, self._fire_single, args=(code, gestures))
            single.daemon = True
            self.pending_single[code] = single
            single.start()

    def _supervise(self) -> None:
        while self.running:
            if not self._attach():
                time.sleep(1)
                continue
            try:
                self._read_events()
            except Exception as exc:
                if self.running:
                    self.log(f"遥控器读取中断，重连中: {exc}")
            self._detach()
            time.sleep(0.5)

    def _read_events(self) -> None:
        import evdev

        assert self.dev
        for event in self.dev.read_loop():
            if not self.running:
                break
            if event.type == evdev.ecodes.EV_KEY:
                code = event.code
                if code in VOICE_KEYS:
                    if event.value in (1, 0):
                        self.log(f"遥控器语音键 code={code} value={event.value}")
                        if event.value == 1:
                            self.on_voice_down()
                        else:
                            self.on_voice_up()
                    continue
                if code in SWALLOWED_KEYS:
                    continue
                gestures = self.get_action(code) if self.get_action else {"single": "passthrough"}
                if not isinstance(gestures, dict):
                    gestures = {"single": gestures} if gestures else {}
                if gestures.get("double") or gestures.get("long"):
                    if event.value == 1:
                        key_name = evdev.ecodes.KEY.get(code, str(code))
                        self.log(f"按键 code={code} ({key_name})")
                    self._handle_gesture(code, event.value, gestures)
                    continue
                action = gestures.get("single", "passthrough")
                if event.value == 1:
                    key_name = evdev.ecodes.KEY.get(code, str(code))
                    self.log(f"按键 code={code} ({key_name})")
                    if action and action != "passthrough":
                        self.handled_keys.add(code)
                        if self.perform_action:
                            self.perform_action(action, code)
                        continue
                    self.handled_keys.discard(code)
                elif event.value == 0 and code in self.handled_keys:
                    self.handled_keys.discard(code)
                    continue
                elif event.value == 2 and code in self.handled_keys:
                    continue
                if self.ui:
                    try:
                        self.ui.write_event(event)
                    except Exception:
                        pass
                continue
            if self.ui:
                try:
                    self.ui.write_event(event)
                except Exception:
                    pass

    def stop(self) -> None:
        self.running = False
        self._detach()
        if self.ui:
            try:
                self.ui.close()
            except Exception:
                pass
            self.ui = None


# ------------------------------------------------------------------- 桥接器
class Bridge:
    def __init__(self, gain_db: float = 0.0, log=print) -> None:
        self.log = log
        self.mic = VirtualMic(gain_db=gain_db, log=log)
        self.hid: Optional[RemoteHid] = None
        self.client = None
        self.caps = AtvvCapabilities()
        self.acc = FrameAccumulator()
        self.decoder = ImaAdpcmDecoder()
        self.session_id: Optional[int] = None
        self.recording = False
        self.saved_default: Optional[int] = None
        self.real_default: Optional[int] = None
        self.loop = None
        self.pending = array.array("h")  # 开流到语音软件开始录音之间的预录缓冲
        self.session_pcm = array.array("h")  # 本次会话音频，用于排查
        self.session_raw = bytearray()  # 本次会话原始字节，用于排查解码方式
        self.send_mic_commands = False  # 遥控器自己会开流，默认不再发 0x0C/0x0D
        self.frame_count = 0
        self.sample_count = 0
        self.peak = 0
        self.trigger_mode = "hid"
        self.preopen_mic = True
        self._lock = threading.Lock()
        # 按键映射 / 状态上报
        self.button_map: dict[str, str] = {}
        self.level = 0.0
        self.last_key = None
        self.learned_key = None
        self.learn_pending = False
        self.learned_at = 0.0
        self.battery: Optional[int] = None
        self.address: Optional[str] = None
        self._stop = threading.Event()
        self.status_path = os.path.expanduser("~/.cache/sayall-linux/status.json")
        # 语音引擎：vokie / custom
        self.voice_engine = "vokie"
        self.vokie_codes: list[int] = []
        self.voice_start_cmd = ""
        self.voice_stop_cmd = ""
        self.inject_ui = None
        self._applied_engine = None

    # ---------------------------------------------------------- BLE 连接
    async def connect(self, address: Optional[str] = None) -> None:
        from bleak import BleakClient, BleakScanner

        target = address
        if not target:
            cfg = load_config()
            cached = cfg.get("address")
            known = set()
            try:
                for line in run(["bluetoothctl", "devices"]).stdout.splitlines():
                    parts = line.strip().split(" ", 2)
                    if len(parts) == 3 and parts[0] == "Device":
                        known.add(parts[1].upper())
            except Exception:
                pass
            if cached and cached.upper() in known:
                self.log(f"使用已保存的地址 {cached}")
                target = cached
            if not target:
                target = find_paired_remote()
                if target:
                    self.log(f"从已配对设备找到遥控器 {target}")
        if not target:
            self.log("扫描遥控器…")
            found = {}

            def on_detect(device, adv):
                name = (adv.local_name or device.name or "").strip().lower()
                uuids = {u.lower() for u in (adv.service_uuids or [])}
                if name in DEVICE_NAMES or SERVICE_UUID in uuids:
                    found[device.address] = (device, name)

            async with BleakScanner(detection_callback=on_detect):
                for _ in range(60):
                    if found:
                        break
                    await asyncio.sleep(0.5)
            if not found:
                raise RuntimeError("没有找到遥控器：确认已配对/连接，或先用 bluetoothctl 配对")
            target = next(iter(found))

        self.log(f"连接 {target} …")
        device_path = bluez_device_path(target)
        if device_path:
            from bleak.backends.device import BLEDevice

            self.log(f"BlueZ 设备路径 {device_path}")
            device = BLEDevice(
                target,
                "MI RC",
                details={
                    "path": device_path,
                    "props": {"Address": target, "AddressType": "public"},
                },
            )
            client = BleakClient(device)
        else:
            client = BleakClient(target)
        try:
            await client.connect()
        except Exception as exc:
            # BlueZ 已作为 HID 连接时可能报 AlreadyConnected
            if "AlreadyConnected" in str(exc) or "already connected" in str(exc).lower():
                self.log("设备已被 BlueZ 连接，继续使用现有连接")
            else:
                raise
        self.client = client
        self.address = target
        self._read_battery()
        cfg = load_config()
        cfg["address"] = target
        save_config(cfg)

        await client.start_notify(AUDIO_UUID, self._on_audio)
        await client.start_notify(CONTROL_UUID, self._on_control)
        await client.write_gatt_char(TRANSMIT_UUID, bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03]), response=False)
        self.log("ATVV 已订阅，等待能力包…")

        for _ in range(80):
            if self.caps.frame_size:
                break
            await asyncio.sleep(0.1)
        self.log(
            f"能力: version=0x{self.caps.version:04X} codec=0x{self.caps.selected_codec:02X} "
            f"rate={self.caps.sample_rate} frame={self.caps.frame_size}"
        )
        if not self.caps.is_supported:
            raise RuntimeError("遥控器只提供 8kHz，本实现仅支持 16kHz")

        cfg = load_config()
        if cfg.get("preopen_mic", True):
            await self._mic_open()
            self.log("已预开麦（保持麦克风开启）")

    # ------------------------------------------------------- BLE 回调
    def _on_control(self, _sender, data: bytearray) -> None:
        b = bytes(data)
        if not b:
            return
        op = b[0]
        if op == 0x0B:
            try:
                self.caps = AtvvCapabilities.parse(b)
                self.log(f"能力包: {self.caps.__dict__}")
            except Exception as exc:
                self.log(f"能力包解析失败: {exc}")
        elif op == 0x08:
            self.log("遥控器请求开麦 (0x08)，回复 0x0C")
            asyncio.get_running_loop().create_task(self._mic_open())
        elif op == 0x04 and len(b) >= 4:
            self.session_id = b[3]
            self.acc = FrameAccumulator()
            self.decoder = ImaAdpcmDecoder()
            del self.pending[:]
            del self.session_pcm[:]
            del self.session_raw[:]
            self.frame_count = 0
            self.sample_count = 0
            self.peak = 0
            self.log(f"音频流开始 session={self.session_id}")
            if self.trigger_mode == "atvv":
                asyncio.get_running_loop().create_task(asyncio.to_thread(self.voice_down))
        elif op == 0x00:
            self.log(
                f"音频流结束 session={self.session_id} "
                f"共 {self.frame_count} 帧 / {self.sample_count} 采样 / 峰值 {self.peak / 32768:.3f}"
            )
            self.session_id = None
            if self.trigger_mode == "atvv":
                asyncio.get_running_loop().create_task(asyncio.to_thread(self.voice_up))
            if self.preopen_mic and not self.recording:
                asyncio.get_running_loop().create_task(self._reopen_mic())
        elif op == 0x0A and len(b) >= 7:
            predictor = int.from_bytes(b[4:6], "big", signed=True)
            step = b[6]
            self.decoder.reset(predictor, step)
            self.log(f"解码器同步 predictor={predictor} step={step}")
        else:
            self.log(f"控制事件 op=0x{op:02X} data={b.hex()}")

    def _on_audio(self, _sender, data: bytearray) -> None:
        for frame in self.acc.append(bytes(data), self.caps.frame_size):
            pcm = self.decoder.decode(frame)
            self.session_raw.extend(frame)
            if pcm:
                frame_peak = max(abs(x) for x in pcm) / 32768
                self.level = max(self.level * 0.6, frame_peak)
            if DEBUG and self.frame_count < 8:
                self.log(f"原始帧[{self.frame_count}] {frame[:12].hex()}")
            self.frame_count += 1
            self.sample_count += len(pcm)
            if pcm:
                self.peak = max(self.peak, max(abs(x) for x in pcm))
                self.session_pcm.extend(pcm)
                limit = SAMPLE_RATE * 30
                if len(self.session_pcm) > limit:
                    del self.session_pcm[: len(self.session_pcm) - limit]
            if self.recording:
                self.mic.write(pcm)
            else:
                # 流已经开了但语音软件还没开始录，先存最多 2 秒
                self.pending.extend(pcm)
                limit = SAMPLE_RATE * 2
                if len(self.pending) > limit:
                    del self.pending[: len(self.pending) - limit]
            if self.frame_count % 40 == 0:
                self.log(
                    f"音频帧 {self.frame_count} 采样 {self.sample_count} "
                    f"峰值 {self.peak / 32768:.3f}"
                )

    # --------------------------------------------------------- PTT 流程
    async def _mic_open(self) -> None:
        assert self.client
        cmd = bytes([0x0C, 0x00]) if self.caps.version >= 0x0100 else bytes([0x0C, 0x00, self.caps.selected_codec])
        try:
            await self.client.write_gatt_char(TRANSMIT_UUID, cmd, response=False)
        except Exception:
            await self.client.write_gatt_char(TRANSMIT_UUID, cmd, response=True)
        self.log(f"发送开麦 {cmd.hex()}")

    async def _mic_close(self) -> None:
        assert self.client
        sid = self.session_id or 0
        cmd = bytes([0x0D, sid]) if self.caps.version >= 0x0100 else bytes([0x0D])
        try:
            await self.client.write_gatt_char(TRANSMIT_UUID, cmd, response=False)
        except Exception:
            await self.client.write_gatt_char(TRANSMIT_UUID, cmd, response=True)
        self.log(f"发送关麦 {cmd.hex()}")

    def voice_down(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self.recording:
                return
            self.recording = True
            saved = get_default_source_id()
            if saved is None or saved == self.mic.node_id:
                saved = self.real_default
            self.saved_default = saved
            set_default_source(self.mic.node_id)
            # 等默认录音源真正切换过去，避免语音软件抢跑到笔记本麦克风
            for _ in range(40):
                if get_default_source_id() == self.mic.node_id:
                    break
                time.sleep(0.01)
            # 先开麦：遥控器收到 MIC_OPEN 才真正启用麦克风（已预开麦时跳过）
            if not self.preopen_mic and self.loop and self.client:
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._mic_open(), self.loop)
                    fut.result(timeout=2)
                except Exception as exc:
                    self.log(f"开麦命令失败: {exc}")
            if self.voice_engine == "vokie":
                if not self._vokie_running():
                    self.log("Vokie 未运行，跳过热键注入")
                    self.recording = False
                    set_default_source(self.saved_default)
                    return
                self._inject_hotkey(True)
                started = True
            elif self.voice_engine == "custom" and self.voice_start_cmd:
                subprocess.Popen(self.voice_start_cmd, shell=True, start_new_session=True)
                started = True
            else:
                self.log(f"语音引擎 {self.voice_engine} 不可用")
                started = False
            if not started:
                self.recording = False
                set_default_source(self.saved_default)
                return
            if self.pending:
                self.mic.write(self.pending)
                del self.pending[:]
        except Exception:
            self.log(traceback.format_exc())
        finally:
            self._lock.release()

    def voice_up(self) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            if not self.recording:
                return
            self.recording = False
            time.sleep(0.15)
            if self.voice_engine == "vokie":
                self._inject_hotkey(False)
                time.sleep(0.2)
            elif self.voice_engine == "custom" and self.voice_stop_cmd:
                subprocess.Popen(self.voice_stop_cmd, shell=True, start_new_session=True)
            if DEBUG and self.session_pcm:
                try:
                    import wave

                    with open("/tmp/sayall-raw.bin", "wb") as fh:
                        fh.write(self.session_raw)
                    with wave.open("/tmp/sayall-last.wav", "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(self.session_pcm.tobytes())
                    self.log(
                        f"本次音频已保存 /tmp/sayall-last.wav "
                        f"({len(self.session_pcm) / SAMPLE_RATE:.1f}s)"
                    )
                except Exception as exc:
                    self.log(f"保存 WAV 失败: {exc}")
            del self.session_pcm[:]
            set_default_source(self.saved_default)
            if self.saved_default and self.saved_default != self.mic.node_id:
                self.real_default = self.saved_default
            self.acc = FrameAccumulator()
            self.decoder = ImaAdpcmDecoder()
            del self.pending[:]
        except Exception:
            self.log(traceback.format_exc())
        finally:
            self._lock.release()

    def _setup_inject_device(self) -> None:
        import evdev

        if self.inject_ui is not None:
            return
        codes = [
            evdev.ecodes.KEY_LEFTCTRL, evdev.ecodes.KEY_RIGHTCTRL,
            evdev.ecodes.KEY_LEFTALT, evdev.ecodes.KEY_RIGHTALT,
            evdev.ecodes.KEY_LEFTSHIFT, evdev.ecodes.KEY_RIGHTSHIFT,
            evdev.ecodes.KEY_LEFTMETA, evdev.ecodes.KEY_RIGHTMETA,
        ]
        for i in range(1, 25):
            code = getattr(evdev.ecodes, f"KEY_F{i}", None)
            if code is not None:
                codes.append(code)
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
            code = getattr(evdev.ecodes, "KEY_" + ch, None)
            if code is not None:
                codes.append(code)
        try:
            self.inject_ui = evdev.UInput(
                {evdev.ecodes.EV_KEY: sorted(set(codes))},
                name="SayAll Inject",
                vendor=XIAOMI_VID,
                product=0x32B9,
                version=1,
            )
        except Exception as exc:
            self.log(f"创建注入键盘失败（Vokie 模式不可用）: {exc}")

    def _inject_hotkey(self, down: bool) -> None:
        import evdev

        if self.inject_ui is None:
            return
        self.vokie_codes = read_vokie_hotkey()
        codes = self.vokie_codes or [evdev.ecodes.KEY_RIGHTALT]
        try:
            if down:
                for code in codes:
                    self.inject_ui.write(evdev.ecodes.EV_KEY, code, 1)
            else:
                for code in reversed(codes):
                    self.inject_ui.write(evdev.ecodes.EV_KEY, code, 0)
            self.inject_ui.syn()
            self.log(f"注入 Vokie 热键 {'按下' if down else '松开'}: {codes}")
        except Exception as exc:
            self.log(f"注入热键失败: {exc}")

    def _vokie_running(self) -> bool:
        try:
            out = run(["pgrep", "-af", "/opt/Vokie/vokie"], timeout=3).stdout
            for line in out.splitlines():
                parts = line.split(maxsplit=1)
                if len(parts) != 2:
                    continue
                argv0 = parts[1].split()[0]
                if os.path.basename(argv0) == "vokie":
                    return True
        except Exception:
            pass
        return False

    def _apply_engine_config(self, cfg: dict) -> None:
        self.voice_engine = (cfg.get("voice_engine") or "vokie").lower()
        self.voice_start_cmd = cfg.get("voice_start_command") or ""
        self.voice_stop_cmd = cfg.get("voice_stop_command") or ""

    # ------------------------------------------------------------ 主循环
    def request_stop(self) -> None:
        if getattr(self, "stop_event", None) is not None:
            self.stop_event.set()

    # ------------------------------------------------------- 映射 / 状态
    def _get_action(self, code: int) -> str:
        import evdev

        if self.learn_pending:
            self.learn_pending = False
            name = evdev.ecodes.KEY.get(code, str(code))
            self.learned_key = {"code": code, "name": name}
            self.learned_at = time.time()
            self.log(f"学习到按键 {code} ({name})")
            cfg = load_config()
            cfg["learn_request"] = False
            save_config(cfg)
        self.last_key = {"code": code, "name": evdev.ecodes.KEY.get(code, str(code))}
        return normalize_mapping(self.button_map.get(str(code)))

    def _perform_action(self, action: str, code: int) -> None:
        try:
            if action.startswith("command:"):
                subprocess.Popen(action[8:], shell=True, start_new_session=True)
                self.log(f"执行命令: {action[8:]}")
            elif action.startswith("app:"):
                subprocess.Popen(
                    ["gio", "launch", action[4:]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.log(f"启动应用: {action[4:]}")
            elif action.startswith("key:"):
                self._emit_keys(action[4:])
            else:
                self.log(f"按键 {code} 的动作: {action}")
        except Exception as exc:
            self.log(f"执行动作失败: {exc}")

    def _emit_keys(self, combo: str) -> None:
        import evdev

        aliases = {
            "CTRL": "LEFTCTRL", "CONTROL": "LEFTCTRL",
            "ALT": "LEFTALT", "OPTION": "LEFTALT",
            "SHIFT": "LEFTSHIFT",
            "SUPER": "LEFTMETA", "META": "LEFTMETA", "CMD": "LEFTMETA", "WIN": "LEFTMETA",
            "RETURN": "ENTER", "ESC": "ESC", "ESCAPE": "ESC",
            "DEL": "DELETE", "INS": "INSERT",
            "PAGEUP": "PAGEUP", "PAGEDOWN": "PAGEDOWN",
        }
        if not self.hid or not self.hid.ui:
            return
        codes = []
        for part in combo.split("+"):
            part = part.strip().upper()
            if not part:
                continue
            part = aliases.get(part, part)
            value = getattr(evdev.ecodes, "KEY_" + part, None)
            if value is not None:
                codes.append(value)
        if not codes:
            self.log(f"无法识别的按键组合: {combo}")
            return
        for c in codes:
            self.hid.ui.write(evdev.ecodes.EV_KEY, c, 1)
        for c in reversed(codes):
            self.hid.ui.write(evdev.ecodes.EV_KEY, c, 0)
        self.hid.ui.syn()
        self.log(f"发送按键组合: {combo}")

    def _config_loop(self) -> None:
        mtime = None
        while not self._stop.is_set():
            try:
                st = os.stat(CONFIG_PATH)
                if mtime is None or st.st_mtime != mtime:
                    mtime = st.st_mtime
                    cfg = load_config()
                    self.button_map = cfg.get("button_map", {}) or {}
                    gain = cfg.get("gain_db")
                    if gain is not None:
                        self.mic.gain_db = float(gain)
                    if cfg.get("learn_request"):
                        self.learn_pending = True
                    self._apply_engine_config(cfg)
                    self.vokie_codes = read_vokie_hotkey()
                    self.log(f"配置已加载（映射 {len(self.button_map)} 项）")
            except Exception:
                pass
            time.sleep(1)

    def _status_loop(self) -> None:
        while not self._stop.is_set():
            try:
                os.makedirs(os.path.dirname(self.status_path), exist_ok=True)
                if self.learned_key and time.time() - self.learned_at > 15:
                    self.learned_key = None
                data = {
                    "connected": bool(self.client and self.client.is_connected),
                    "address": self.address,
                    "recording": self.recording,
                    "level": round(self.level, 4),
                    "last_key": self.last_key,
                    "learned_key": self.learned_key,
                    "battery": self.battery,
                    "gain_db": self.mic.gain_db,
                    "timestamp": time.time(),
                }
                tmp = self.status_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
                os.replace(tmp, self.status_path)
            except Exception:
                pass
            time.sleep(0.4)

    def _read_battery(self) -> None:
        if not self.address:
            return
        try:
            out = run(["bluetoothctl", "info", self.address]).stdout
            import re

            for line in out.splitlines():
                if "Battery Percentage" in line:
                    m = re.search(r"0x([0-9a-fA-F]+)", line)
                    if m:
                        self.battery = int(m.group(1), 16)
        except Exception:
            pass

    def _battery_loop(self) -> None:
        while not self._stop.is_set():
            self._read_battery()
            time.sleep(60)

    async def run(self, address: Optional[str], loop_forever: bool = True) -> None:
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        self.mic.start()
        self.real_default = find_real_source_id()
        current = get_default_source_id()
        if current == self.mic.node_id and self.real_default:
            set_default_source(self.real_default)
            self.log(f"默认录音源残留为虚拟麦克风，已恢复为 id {self.real_default}")
        cfg = load_config()
        self.button_map = cfg.get("button_map", {}) or {}
        self.mic.gain_db = float(cfg.get("gain_db", self.mic.gain_db) or 0.0)
        self._apply_engine_config(cfg)
        self.vokie_codes = read_vokie_hotkey()
        self._setup_inject_device()
        self._applied_engine = self.voice_engine
        self._stop.clear()
        threading.Thread(target=self._config_loop, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        threading.Thread(target=self._battery_loop, daemon=True).start()
        await self.connect(address)
        self.hid = RemoteHid(
            self._voice_down_threadsafe,
            self._voice_up_threadsafe,
            log=self.log,
            get_action=self._get_action,
            perform_action=self._perform_action,
        )
        if not self.hid.start():
            raise RuntimeError("未能接管遥控器键盘（检查是否已连接遥控器）")
        self.log("就绪：按住遥控器语音键说话")
        if not loop_forever:
            return
        await self.stop_event.wait()

    async def _reopen_mic(self) -> None:
        """遥控器的开麦会话约 60 秒超时，结束后立刻续开，保证按键时有声音。"""
        await asyncio.sleep(0.3)
        if self.client and not self.recording:
            try:
                await self._mic_open()
                self.log("已续开麦克风")
            except Exception as exc:
                self.log(f"续开麦克风失败: {exc}")

    def _voice_down_threadsafe(self) -> None:
        self.voice_down()

    def _voice_up_threadsafe(self) -> None:
        self.voice_up()

    async def close(self) -> None:
        self._stop.set()
        if self.hid:
            self.hid.stop()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        if self.inject_ui:
            try:
                self.inject_ui.close()
            except Exception:
                pass
            self.inject_ui = None
        self.mic.stop()


# ---------------------------------------------------------------- 自检/工具
def doctor() -> int:
    ok = True

    def check(name: str, cond: bool, hint: str = "") -> None:
        nonlocal ok
        print(f"[{'ok' if cond else 'fail'}] {name}" + (f" — {hint}" if not cond and hint else ""))
        if not cond:
            ok = False

    check("Python 3.10+", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        import bleak  # noqa: F401
        check("bleak", True)
    except Exception:
        check("bleak", False, "sudo apt install python3-bleak")
    try:
        import evdev  # noqa: F401
        check("evdev", True)
    except Exception:
        check("evdev", False, "sudo apt install python3-evdev")
    for cmd in ("pw-loopback", "pw-cat", "wpctl"):
        check(f"命令 {cmd}", shutil.which(cmd) is not None)
    check("/dev/uinput 可写", os.access("/dev/uinput", os.W_OK), "需要 uinput 的 uaccess 规则")
    try:
        import evdev

        devs = RemoteHid.find_keyboard()
        check("遥控器键盘设备", bool(devs), "先配对并连接遥控器")
        for d in devs:
            print(f"      {d.path}  {d.name}  vid=0x{d.info.vendor:04X} pid=0x{d.info.product:04X}")
    except Exception as exc:
        check("遥控器键盘设备", False, str(exc))
    try:
        out = run(["bluetoothctl", "devices", "Connected"]).stdout.strip()
        check("蓝牙已连接设备", bool(out), "确认遥控器已连接")
        for line in out.splitlines():
            print(f"      {line}")
    except Exception:
        pass
    print("\n结果:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


async def scan() -> int:
    from bleak import BleakScanner

    print("扫描 8 秒…")
    seen = []

    def cb(device, adv):
        name = (adv.local_name or device.name or "").strip()
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE_UUID in uuids or (name and name.lower() in DEVICE_NAMES):
            seen.append((device.address, name, uuids))

    async with BleakScanner(detection_callback=cb):
        await asyncio.sleep(8)
    if not seen:
        print("没发现遥控器（可能已连接而不广播，可用 bluetoothctl devices 查看已配对设备）")
    for addr, name, uuids in seen:
        print(f"  {addr}  {name or '(无名称)'}  ATVV={'是' if SERVICE_UUID in uuids else '否'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SayAll for Linux —— 小米遥控器语音输入桥接")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="启动桥接服务")
    p_run.add_argument("--address", help="遥控器蓝牙地址（默认自动扫描）")
    p_run.add_argument("--gain", type=float, default=0.0, help="增益 dB，0~24")
    p_run.add_argument("--once", action="store_true", help="只初始化不常驻")
    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("scan", help="扫描遥控器")

    args = parser.parse_args()
    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "scan":
        return asyncio.run(scan())

    logging = lambda msg: print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    cfg = load_config()
    gain = args.gain if args.gain else float(cfg.get("gain_db", 0.0) or 0.0)
    bridge = Bridge(gain_db=gain, log=logging)
    bridge.send_mic_commands = bool(cfg.get("send_mic_commands", False))
    bridge.trigger_mode = cfg.get("trigger_mode", "atvv")
    bridge.preopen_mic = bool(cfg.get("preopen_mic", True))

    def shutdown(*_):
        logging("退出中…")
        asyncio.get_event_loop().stop()

    async def amain():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, bridge.request_stop)
            except NotImplementedError:
                pass
        try:
            await bridge.run(args.address, loop_forever=not args.once)
        finally:
            await bridge.close()

    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging(traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
