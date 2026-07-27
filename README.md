# SS13 Voice Input 🎤

> **Space Station 13（天关13）Push-to-Talk 语音输入外挂**  
> 按住 PTT 键说话 → 自动语音识别 → 填入 tgui_say 聊天框 → 自动发送

---

## 🚀 快速开始

### 前提条件
- Windows 10+
- Python 3.8+
- 已连接麦克风

### 第〇步：安装依赖

双击 **`安装依赖.bat`**，自动安装所需库。

### 第一步：配 API

打开程序后在「API 配置」区域填入百度短语音识别的 **API Key** 和 **Secret Key**，点 **「验证连接」**。

> 申请地址：[百度短语音识别](https://console.bce.baidu.com/ai/#/ai/speech/overview) — 免费 5 万次/天

### 第二步：校准点击位置

点窗口里的 **「🎯校准」** 按钮，鼠标移到 tgui_say 输入栏上点击一下，自动计算偏移。

### 第三步：启动语音输入

双击 **`启动语音输入.bat`**，按住 **CapsLock** 说话，松开自动识别+填入+发送。

---

## 📁 文件结构

```
ss13-voice-input/
├── ss13-voice-input.py       ← ★ 主程序
├── api.txt                   ← 你的 API 密钥（自动生成）
├── calibrate.txt             ← 你的点击偏移（自动生成）
├── ss13-voice-config.json    ← 其它设置（麦克风、PTT键、开关）
├── 安装依赖.bat              ← ★ 首次运行：装 Python 库
├── 启动语音输入.bat          ← ★ 双击启动
├── 校准点击位置.bat          ← 重新校准时用
└── README.md
```

---

## 🧠 技术方案

| 模块 | 方案 |
|------|------|
| 语音识别 | 百度短语音 API (`dev_pid=1537`) |
| 离线回退 | Vosk `vosk-model-small-cn-0.22` |
| 文本注入 | `PostMessage WM_CHAR` → `WM_KEYDOWN/UP Enter` |
| 窗口查找 | `EnumWindows` → `#32770` → `"tgui_say"` CEF 子窗口 |
| GUI | tkinter 深色主题 |

> 详细技术文档见 [旧版 README](https://github.com/mohu19/ss13-voice-input/blob/old/README.md)
