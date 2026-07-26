# SS13 Voice Input — 语音输入工具

> 为 Space Station 13（天关13 / TianGuan13）游戏客户端的 **Push-to-Talk 语音输入外挂**  
> 长按 PTT 键说话 → 百度短语音识别 → 自动填入 tgui_say 聊天框 → 可选自动发送

---

## 📁 项目结构

```
C:\Users\33922\Desktop\Hermes\
├── 启动语音输入.bat            ← 跳转 launcher（双击运行）
├── 校准点击位置.bat            ← 跳转 launcher（双击校准）
│
├── ss13-voice\                  ← 项目根目录
│   ├── ss13-voice-input.py     ← ★ 主脚本（GUI窗口 + 语音引擎 + 文本注入）
│   ├── ss13-voice-config.json  ← 配置文件
│   ├── venv\                   ← Python 虚拟环境（依赖全部装在里面）
│   │   └── Scripts\python.exe  ← 运行时 Python
│   ├── vosk-model-small-cn-0.22\ ← Vosk 离线中文语音模型（~42MB，回退用）
│   ├── 启动语音输入.bat         ← 实际启动 bat（激活 venv → 运行主脚本）
│   └── 校准点击位置.bat         ← 校准 bat（带 --calibrate 参数）
│
├── 已归档\                      ← 旧版本和辅助工具（不再使用）
│   └── ss13-voice-input\        ← 旧项目文件夹
│       ├── versions\            ← 各版本备份 v1/v2/v4
│       └── tools\               ← 测试工具
│
└── ss13-voice-test-enter\       ← 回车发送测试工具（独立项目）
    ├── test_enter.py
    └── 测试回车.bat
```

---

## 🚀 快速使用

### 前提条件

- Windows 10+（已安装 BYOND 客户端）
- 已连接麦克风
- Python 3.11（虚拟环境已配好，`ss13-voice\venv\`）

### 第一步：校准点击位置

双击 `校准点击位置.bat`：
1. 按 T 打开 tgui_say 聊天框
2. 鼠标移到输入栏上（光标闪烁的位置）
3. 左键点击一下
4. 自动计算偏移并保存

### 第二步：启动语音输入

双击 `启动语音输入.bat`，打开 GUI 窗口：

```
┌─────────────────────────────────────────────────┐
│  运行日志（实时显示按键/识别/填入状态）          │
│                                                 │
│  🎤 录音中... (松开 CapsLock 识别)              │
│  ☁ 百度识别中...                                │
│  📝 识别结果: "你好"                            │
│  🖱 点击 (780, 347) 等待 150ms                  │
│  ✓ 已填入: 你好                                 │
│                                                 │
├─────────────────────────────────────────────────┤
│ 麦克风: [0] Microsoft...  │  PTT键: [CapsLock ▼]│
│ [置顶窗口] [⏎ 自动T] [↩ 自动发送]              │
│ 状态摘要  │  [🎯校准点击] [💾保存设置]           │
└─────────────────────────────────────────────────┘
```

### 模式说明

| 自动T | 自动发送 | 效果 |
|-------|---------|------|
| ❌ | ❌ | 手动按T → 说话 → 手动回车（原始模式） |
| ❌ | ✅ | 手动按T → 说话 → 自动填入+发送 |
| ✅ | ❌ | 按住说话 → 自动按T → 填入 → 手动回车 |
| ✅ | ✅ | 按住说话 → 自动按T → 填入 → 自动发送 |

---

## 🧠 核心架构

### 流程图

```
[按键/鼠标监听]                     ← pynput / mouse 库
  ↓ PRESS
[开始录音]                         ← sounddevice InputStream (16kHz 16bit)
  ↓
[松开 PTT] → RELEASE
  ↓
[百度短语音识别]                   ← http://vop.baidu.com/server_api (dev_pid=15372)
  ↓ (失败时回退)
[Vosk 离线识别]                    ← vosk-model-small-cn-0.22
  ↓
[识别到文本] → "你好"
  ↓ (auto_open_t=true)
[SendInput 按 T]                   ← 物理按键，打开 tgui_say 聊天框
  ↓ 等待 400ms
[鼠标物理点击 tgui_say 输入栏]     ← mouse_lib.click() — SendInput 物理鼠标
  ↓ 等待 150ms
[PostMessage WM_CHAR 逐字输入]     ← 直接发到 CEF 窗口，不依赖焦点
  ↓ (auto_send_enter=true)
[PostMessage WM_KEYDOWN/UP Enter]  ← 触发 CEF JavaScript keydown → 提交
```

### 进程模型

```
启动语音输入.bat
  └─ venv\Scripts\python.exe -u ss13-voice-input.py
       ├─ MainWindow (tkinter GUI, 主线程)
       │    ├─ 日志显示
       │    ├─ 设置面板（麦克风/PTT/自动开关）
       │    └─ 校准按钮
       └─ VoiceEngine (后台线程, daemon)
            ├─ 键盘/鼠标监听 (pynput/mouse)
            ├─ sounddevice 录音
            ├─ 百度 ASR HTTP 请求
            └─ Win32 窗口操作 (PostMessage/mouse_lib)
```

---

## 🪟 窗口系统

### 窗口层级（关键）

```
桌面
├── BYOND 游戏主窗口
└── [#32770] ""                          ← tgui_say 独立顶层对话框
     └── [Chrome_WidgetWin_0] "127.0.0.1:xxx/tgui_say.html"
          └── [Chrome_RenderWidgetHostHWND]  ← CEF 渲染（实际输入处理）
```

**关键事实：** `tgui_say` 是**独立的顶层 `#32770` 对话框**，不是游戏主窗口的子窗口。  
CEF（Chromium Embedded Framework）在其中渲染 React 聊天界面。  
`Chrome_RenderWidgetHostHWND` 是实际处理键盘/鼠标输入的底层窗口。

### GUI 窗口（tkinter）

| 组件 | 说明 |
|------|------|
| 日志框 | `tk.Text`，只读，自动滚动，`Consolas 10` 字体 |
| 麦克风下拉 | `ttk.Combobox`，列出所有 `sounddevice` 输入设备 |
| PTT 键捕获 | `tk.Entry` 只读 + "🎯 捕获"按钮绑定 `<Key>` 事件 |
| 预设键下拉 | 预置 50+ 常用键（F1-F12、CapsLock、Shift等） |
| 置顶开关 | `tk.Checkbutton`，绑定 `attributes("-topmost")` |
| 自动T开关 | 即时生效（无需点保存），推送到 VoiceEngine |
| 自动发送开关 | 同上 |

---

## 🎤 语音识别

### 首选：百度短语音识别 API

| 参数 | 值 |
|------|-----|
| 接口 | `http://vop.baidu.com/server_api`（国内直连，不走境外流量） |
| 鉴权 | OAuth 2.0 `https://aip.baidubce.com/oauth/2.0/token` |
| 模型 | `dev_pid=15372`（普通话远场，适合麦克风输入） |
| 格式 | Raw PCM，16kHz，16bit，单声道 |
| 限额 | 5 万次/天免费 |
| SDK 方式 | RAW POST（自行构造 HTTP 请求，不用百度 SDK） |

**Access Token 缓存：** 30天有效期，脚本自动缓存 + 提前5分钟刷新。

### 回退：Vosk 离线识别

| 参数 | 值 |
|------|-----|
| 模型 | `vosk-model-small-cn-0.22`（~42MB） |
| 准确率 | ~80%（百度在线约~95%） |
| 加载方式 | 延迟加载（启动时检查，不存在则跳过） |
| 输出处理 | 需要 `replace(" ", "")` 去空格（Vosk 中文输出自带空格） |

### 识别流程

```python
text = _baidu_asr(audio_bytes)     # 1) 百度在线
if not text and _vosk_available:   # 2) 在线失败 → Vosk 离线
    text = _vosk_asr(audio_bytes)
```

---

## ⌨️ 文本注入（关键部分）

### 最终可行方案（2026-07-27 验证通过）

| 步骤 | 方法 | 说明 |
|------|------|------|
| 1. 聚焦 CEF | `mouse_lib.click()` | SendInput 物理鼠标点击输入栏位置 |
| 2. 打字 | `PostMessage(WM_CHAR, ord(ch))` | 逐字发送到 CEF HWND，不依赖焦点 |
| 3. 回车 | `PostMessage(WM_KEYDOWN, 0x0D)` + `WM_KEYUP` | 触发 CEF React keydown Enter → 提交 |

### 为什么是这个方案（历史探索）

| 尝试过的方案 | 结果 | 原因 |
|-------------|------|------|
| `SendInput` Ctrl+V（物理按键） | ❌ 文字不进框 | 键盘焦点没给到 CEF 输入元素 |
| `PostMessage` Ctrl+V | ❌ "VV" | CEF 把 Ctrl 和 V 当独立按键 |
| `SendInput` Unicode 逐字 | ❌ 文字不进框 | 焦点问题同上 |
| `WM_CHAR` 逐字 + `WM_CHAR(0x0D)` 回车 | ❌ 换行不发送 | WM_CHAR(Enter) 插入换行符，不触发提交 |
| `WM_CHAR` 逐字 + `SendInput` 物理回车 | ❌ 不触发提交 | 焦点不在 CEF，回车去了别处 |
| **`WM_CHAR` + `WM_KEYDOWN/UP Enter`** | **✅ 成功** | WM_CHAR 进框，WM_KEYDOWN 触发 JS keydown |

**关键发现：**
- `WM_CHAR` 直接发到 CEF HWND，**不需要焦点**就能插入文字（但 React 的 onChange 不会被触发——不过 Enter 提交时 DOM 值还在，所以文字会被发送）
- `WM_KEYDOWN` + `WM_KEYUP` 直接发到 CEF HWND 能触发 React 的 keydown 事件处理
- `SendInput` 鼠标点击用于聚焦 CEF 窗口（让窗口可见 + 激活）
- `SendInput` 键盘 **按 T**（打开聊天）工作正常，因为 BYOND 主窗口在前台

### 自动按 T（auto_open_t）

使用 `SendInput`（物理按键）发送 `VK_T` (0x54) 键。  
同时提供 scan code 0x14 以确保 DirectInput/RawInput 兼容。  
再补一层 `PostMessage(WM_KEYDOWN/UP)` 到前台窗口兜底。

### 关键数据结构

```python
# SendInput 键盘 INPUT 结构（ctypes）
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),     # 虚拟键码
        ("wScan", ctypes.c_ushort),   # 硬件扫描码
        ("dwFlags", ctypes.c_ulong),  # KEYEVENTF_KEYUP / KEYEVENTF_UNICODE 等
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_ulong),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),     # INPUT_KEYBOARD = 1
        ("ki", KEYBDINPUT),
    ]
```

---

## 📝 配置文件 (`ss13-voice-config.json`)

```json
{
  "mic_device": 0,                // 麦克风设备 ID，null = 默认设备
  "mic_device_name": "[0] Microsoft...",
  "ptt_key": {                    // PTT 键定义
    "type": "special",            // special / char / mouse
    "name": "caps_lock"           // 键名（pynput 格式）
  },
  "ptt_key_display": "CapsLock",  // 显示用名称
  "click_offset_x": null,         // 点击偏移 X（null = 居中）
  "click_offset_y": 25,           // 点击偏移 Y（距 CEF 窗口顶部的像素）
  "click_wait_ms": 150,           // 点击后等待时间（毫秒）
  "window_geometry": "680x520",   // 保存的窗口大小
  "window_topmost": true,         // 窗口置顶
  "auto_open_t": false,           // 自动按 T 打开聊天
  "auto_send_enter": false        // 自动回车发送
}
```

---

## 🖱️ 校准点击位置

用户运行 `--calibrate` 模式时：
1. 主窗口最小化
2. 弹出提示 Toplevel："将鼠标移到 tgui_say 输入栏上，左键点击一下"
3. `mouse.hook()` 等待鼠标点击
4. 获取点击坐标 `mouse.get_position()`
5. 计算相对于 CEF 窗口的偏移：`offset_x = click_x - cef_left`, `offset_y = click_y - cef_top`
6. 保存到配置

校准也可以在 GUI 窗口中点 "🎯 校准点击" 按钮完成。

---

## 🔧 依赖

```
pip install sounddevice numpy pynput pywin32 mouse vosk
```

国内镜像：`--trusted-host pypi.tuna.tsinghua.edu.cn -i https://pypi.tuna.tsinghua.edu.cn/simple/`

| 包 | 用途 |
|----|------|
| `sounddevice` | PortAudio 录音，16kHz 16bit PCM |
| `numpy` | 音频数据拼接（`np.concatenate`） |
| `pynput` | 全局键盘钩子（监听 PTT 键） |
| `pywin32` | Win32 API（`win32gui`, `win32con`, `win32api`） |
| `mouse` | 全局鼠标钩子 + 物理点击（`SendInput`） |
| `vosk` | 离线中文语音识别（回退方案） |

---

## 🧪 已知问题 / 限制

1. **React 状态不一致**：`WM_CHAR` 插入文字后 React 的 `onChange` 不触发，但 DOM 值已更新。Enter 提交时 React 从 DOM 读值（实际行为依赖 tgui 版本）。如果未来 tgui 改成严格受控组件，此方案可能失效。
2. **CEF 版本依赖**：`PostMessage` WM_CHAR / WM_KEYDOWN 的行为依赖 CEF 版本。BYOND 更新 CEF 后可能失效。
3. **SendInput 被拦截**：某些防作弊软件或 UIPI 可能拦截 `SendInput`。当前版本用 `PostMessage` 规避了这个问题，因为 PostMessage 不经过 UIPI 检查。
4. **校准偏移**：点击坐标偏移依赖窗口位置准确。如果游戏分辨率或 tgui_say 布局变化，需重新校准。
5. **多个 tgui_say 窗口**：如果同时打开了多个 tgui_say 窗口（罕见），脚本只会操作第一个找到的。

---

## 📜 开发历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-25 | v1 | 基础版：Vosk STT + WM_CHAR 注入 |
| 2026-07-25 | v2 | 百度 ASR 集成 + 配置系统 + tkinter 设置窗口 |
| 2026-07-26 | v3 | 校准点击位置 + 物理鼠标点击聚焦 CEF |
| 2026-07-26 | v4 | GUI 重写（日志实时显示 + 麦克风/PTT 设置 + 置顶） |
| 2026-07-26 | v5 | 自动T + 自动发送开关 |
| 2026-07-27 | v6 | SendInput 物理按键替代 WM_CHAR 打字（试验） |
| 2026-07-27 | **v7** | **最终方案：WM_CHAR 打字 + WM_KEYDOWN/UP Enter** |

---

## 💡 给后续 AI 的关键提示

1. **不要尝试用 `SendInput` 打字到 CEF** — 焦点问题导致文字进不去。`WM_CHAR` 直接发 HWND 不需要焦点，是唯一能在 CEF 中输入文字的方法。
2. **回车用 `WM_KEYDOWN(0x0D) + WM_KEYUP(0x0D)`** — 不要用 `WM_CHAR(0x0D)`（那是换行符）。`WM_KEYDOWN` 才能触发 JavaScript 的 keydown 事件。
3. **CEF 窗口查找**：`EnumWindows` 找 `#32770` 对话框 → `EnumChildWindows` 找 `"tgui_say"` 文本 → 那就是 CEF 的 HWND。
4. **剪贴板无用**：`PostMessage Ctrl+V` 会打出 "VV" 而不是粘贴内容。`SendInput Ctrl+V` 需要焦点，但焦点问题无法解决。
5. **点击是必要的**：虽然 `WM_CHAR` 不需要焦点就能打字，但 **必须点击一次** 让 CEF 窗口激活，否则后续的 `WM_KEYDOWN Enter` 不触发提交。
6. **auto_open_t 用 SendInput**：按 T 打开聊天框需要物理按键，因为要发到 BYOND 游戏窗口。`SendInput` 对此正常工作。
