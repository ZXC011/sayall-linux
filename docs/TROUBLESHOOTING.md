# 常见问题与排查

## 按语音键浏览器刷新

说明桥接没有抓住遥控器设备，F5 落到了前台应用。

```bash
systemctl --user status sayall-bridge
journalctl --user -u sayall-bridge -n 50 | grep -E '已接管|抓取失败|读取中断'
```

- 确认服务在运行：`systemctl --user start sayall-bridge`
- 确认日志里有 `已接管遥控器键盘: /dev/input/eventXX (MI RC)`
- 遥控器重连后桥接会自动重抓；仍不行就重启服务

## 按了没反应 / 没有出字

1. **Vokie 是否运行**：`pgrep -af /opt/Vokie/vokie`；桥接在 Vokie 未运行时会跳过注入
2. **触发键是否一致**：`python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.config/vokie/app-settings.json')))['pttConfig'])"`
3. **麦克风是否开麦**：日志里有 `发送开麦 0c00` / `已预开麦`；会话结束后会自动续开
4. **增益**：数值太小会识别为空。用界面把增益调到 6–12 dB，观察电平条
5. **Vokie 未识别**：看 `~/.config/vokie/logs/app.log` 里是否有 `recording-asr-final-received` 和 `textLength`

调试模式可以看到最近一次会话的音频（仅本地，注意隐私）：

```bash
SAYALL_DEBUG=1 systemctl --user restart sayall-bridge
# 说一句话后
ls -l /tmp/sayall-last.wav
```

## 微信 / 部分应用不自动粘贴

Vokie 在 Linux 上用 `xprop -root _NET_ACTIVE_WINDOW` 判断焦点窗口，
GNOME Wayland 下该窗口经常没有 `WM_CLASS`，Vokie 会**误判为终端**并发送
`Ctrl+Shift+V`。微信 4.x（原生 Wayland）不认这个组合，所以不粘贴。

日志特征：

```
linux-paste-injected { pasteCombo: 'ctrl+shift+v', windowClass: null }
```

这是 Vokie 上游的行为，本项目未做侵入式修改。可选处理：

- 等 Vokie 修复 GNOME Wayland 的焦点窗口判断
- 手动 `Ctrl+V`（注意 Vokie 会在约 300 ms 后恢复剪贴板）
- 使用其他在 GNOME 下表现更好的语音输入软件

## 遥控器连不上

```bash
bluetoothctl
[bluetooth]# agent on
[bluetooth]# default-agent
[bluetooth]# pair <MAC>
[bluetooth]# trust <MAC>
[bluetooth]# connect <MAC>
```

配对时按住遥控器「主页 + 菜单」进入配对模式。配对失败常见原因是
没有 agent（`No agent available for request type 2`）。

## `/dev/uinput` 权限

桥接需要写 `/dev/uinput` 来转发按键和注入热键。Vokie 安装时会写入
`/etc/udev/rules.d/69-vokie-input.rules`（`TAG+="uaccess"`）。

```bash
ls -l /dev/uinput
getfacl /dev/uinput | head
```

没有权限时，重新插拔/重连设备，或让 udev 规则生效：

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=input --subsystem-match=misc
```

## 开机自启

```bash
systemctl --user enable --now sayall-bridge.service
```

Vokie 自启：`~/.config/autostart/com.vokie.desktop`（Vokie 设置里开启）。

## 日志位置

| 内容 | 位置 |
|---|---|
| 桥接服务 | `journalctl --user -u sayall-bridge -f` |
| Vokie | `~/.config/vokie/logs/app.log` |
| 配置 | `~/.config/sayall-linux/config.json` |
| 状态 | `~/.cache/sayall-linux/status.json` |
