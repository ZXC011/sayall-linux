# ATVV 协议与实现笔记

本文记录本项目用到的协议细节，方便移植和排查。内容来自公开资料与对
`HD838A/remote-mic-app`（Swift/macOS）、`GetSayAll/remote-mic-app-windows`
（Rust）的阅读，以及在本机 RC003 上的实测。

## 设备

| 项目 | 值 |
|---|---|
| 型号 | 小米蓝牙遥控器 2（RC001）/ 2 Pro（RC003） |
| 蓝牙名称 | `MI RC` / `Xiaomi Bluetooth Remote 2 Pro` / `小米蓝牙语音遥控器` |
| HID | Vendor `0x2717`，Product `0x32B8` |
| 传输 | BLE GATT，已配对后作为 HID 键盘出现 |

## GATT 服务

| 用途 | UUID |
|---|---|
| Service | `AB5E0001-5A21-4F05-BC7D-AF01F617B664` |
| Transmit（host → remote） | `AB5E0002-5A21-4F05-BC7D-AF01F617B664` |
| Audio（remote → host，notify） | `AB5E0003-5A21-4F05-BC7D-AF01F617B664` |
| Control（remote → host，notify） | `AB5E0004-5A21-4F05-BC7D-AF01F617B664` |

Transmit 同时支持 write 和 write-without-response；本项目优先用
write-without-response，失败时退回 write。

## 命令（写入 Transmit）

| 命令 | 字节 | 说明 |
|---|---|---|
| 查询能力（v1.0） | `0A 01 00 00 03 03` | 订阅 Audio/Control 之后发送 |
| MIC_OPEN | `0C 00`（v1.0+）<br>`0C 00 <codec>`（旧版） | 打开麦克风；遥控器随后推流 |
| MIC_CLOSE | `0D <session_id>`（v1.0+）<br>`0D`（旧版） | 关闭 |
| MIC_EXTEND | `0E <session_id>`（v1.0+） | 延长长会话 |

## 控制事件（Control 通知）

| 首字节 | 含义 | 解析 |
|---|---|---|
| `0x0B` | 能力包 | version = BE u16（`0x0100`），codecs、interaction、frame_size = BE u16；codec `0x02` = 16 kHz，否则 8 kHz |
| `0x08` | 遥控器请求开麦 | host 回复 MIC_OPEN |
| `0x04` | 流开始 | `[0x04, interaction, codec, session_id]` |
| `0x00` | 流结束 | 无负载 |
| `0x0A` | 解码器同步 | predictor = BE i16（字节 4–5），step index = 字节 6 |

RC003 实测：`version=0x0100 codec=0x02 rate=16000 frame=120`。

## 音频

- 16 kHz、单声道、**IMA/DVI ADPCM**，高半字节优先
- 每帧 `frame_size` 字节（RC003 为 120 字节 = 240 采样 = 15 ms）
- 音频通知按字节流累积，按 `frame_size` 切帧；同步包会重置 predictor/step
- 解码后做 3 点平滑 `(prev + 2*cur + next) / 4`，再乘增益（0–24 dB）

## 开麦时序（重要）

- 遥控器在按下语音键时会主动开始推流（`0x04`），但如果主机没有及时
  `MIC_OPEN`，麦克风保持静音，音频帧全是 0。
- `MIC_OPEN` 打开的会话大约 **60 秒**后自动结束；结束事件是 `0x00`。
- 本项目在连接时预开麦，并在每次会话结束后自动续开，保证按键时有声音。
- 不要等 `0x04` 之后再开麦（会慢一拍），也不要在用户说话期间重复发送
  `MIC_OPEN`（会重启音频流）。

## 语音键

- HID usage page `0x07`、usage `0x3E` → Linux `KEY_F5`（键码 63）
- 按住说话、松开结束；长按会重复上报按键事件（value 2），需要一起吞掉
- 本项目抓取遥控器的 HID 设备（`EVIOCGRAB`），只吞 F5，其余按键通过
  uinput 原样转发

## 注入 Vokie 热键

- 读取 `~/.config/vokie/app-settings.json` 的 `pttConfig`
- `keys` 使用 Vokie 的 Linux token，例如 `["LeftCtrl", "LeftAlt", "D"]`
- 通过 uinput 按下/松开整组键；Vokie 的 `vokie-input-helper` 监听 /dev/input
- 支持纯修饰键组合（如 `["LeftCtrl", "LeftMeta"]` = Ctrl+Win）

Vokie 的 Linux token 与键码（节选）：

```
Escape 1  Backspace 14  Tab 15  Return 28  LeftCtrl 29  LeftShift 42
LeftAlt 56  Space 57  F1 59 … F10 68  F11 87  F12 88  RightCtrl 97
RightAlt 100  Home 102 Up 103 PageUp 104 Left 105 Right 106 End 107
Down 108 PageDown 109 Insert 110 Delete 111 LeftMeta 125 RightMeta 126
F13 183 … F24 194
```

## 参考

- https://github.com/HD838A/remote-mic-app
- https://github.com/GetSayAll/remote-mic-app-windows
