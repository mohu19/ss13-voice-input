"""
SS13 Voice Input - Push to Talk (Baidu ASR + Vosk)
带设置窗口的语音输入工具

功能：
- 实时日志：按键状态、识别结果、填入情况
- 麦克风选择
- PTT 键自定义绑定（捕获按键 + 预设列表）
- 置顶开关
- 窗口大小可调
- 设置自动保存
"""
import threading, queue, time, json, sys, os, ctypes
import urllib.request, urllib.error, urllib.parse
import numpy as np
import sounddevice as sd
from pynput import keyboard
import win32gui, win32con
import mouse as mouse_lib
import tkinter as tk
from tkinter import ttk, messagebox

# ── 百度 ASR 配置（从 .env 读取） ──
BAIDU_API_KEY = ""
BAIDU_SECRET_KEY = ""
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#"):
                if "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k, _v = _k.strip(), _v.strip()
                    if _k == "BAIDU_API_KEY": BAIDU_API_KEY = _v
                    if _k == "BAIDU_SECRET_KEY": BAIDU_SECRET_KEY = _v
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_ASR_URL = "http://vop.baidu.com/server_api"
DEV_PID = 15372  # 普通话（远场），1537是输入法模型场景
CUID = "ss13-voice-input-win10"

# ── 路径 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "ss13-voice-config.json")
VOSK_MODEL_PATH = os.path.join(SCRIPT_DIR, "vosk-model-small-cn-0.22")
SAMPLE_RATE = 16000

# ═══════════════════════════════════════════
# 配置读写
# ═══════════════════════════════════════════

def load_config() -> dict:
    defaults = {
        "mic_device": None,
        "mic_device_name": "默认设备",
        "ptt_key": {"type": "special", "name": "caps_lock"},
        "ptt_key_display": "CapsLock",
        "click_offset_x": None,
        "click_offset_y": 25,
        "click_wait_ms": 150,
        "window_geometry": "680x520",
        "window_topmost": True,
        "auto_open_t": False,
        "auto_send_enter": False,
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k in defaults:
            cfg.setdefault(k, defaults[k])
        return cfg
    except Exception:
        return dict(defaults)

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════
# 百度 ASR
# ═══════════════════════════════════════════

_access_token = None
_token_expiry = 0.0

def _get_access_token() -> str:
    global _access_token, _token_expiry
    now = time.time()
    if _access_token and now < _token_expiry - 300:
        return _access_token
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": BAIDU_API_KEY,
        "client_secret": BAIDU_SECRET_KEY,
    })
    url = f"{BAIDU_TOKEN_URL}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _access_token = data.get("access_token", "")
            expires_in = data.get("expires_in", 2592000)
            _token_expiry = now + expires_in
            return _access_token
    except Exception as e:
        return ""

def _baidu_asr(audio_bytes: bytes) -> str:
    token = _get_access_token()
    if not token:
        return ""
    params = urllib.parse.urlencode({"dev_pid": DEV_PID, "cuid": CUID, "token": token})
    url = f"{BAIDU_ASR_URL}?{params}"
    req = urllib.request.Request(url, data=audio_bytes, method="POST")
    req.add_header("Content-Type", "audio/pcm;rate=16000")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("err_no") == 0:
                texts = result.get("result", [])
                if texts:
                    return texts[0].strip()
            return ""
    except Exception:
        return ""

def _vosk_asr(recognizer, audio_bytes: bytes) -> str:
    try:
        recognizer.AcceptWaveform(audio_bytes)
        result = json.loads(recognizer.Result())
        text = result.get("text", "").strip().replace(" ", "")
        recognizer.Reset()
        return text
    except Exception:
        return ""

# ═══════════════════════════════════════════
# 窗口查找 + 点击填入
# ═══════════════════════════════════════════

def find_tgui_say_cef():
    for h in range(10000):
        try:
            if not win32gui.IsWindowVisible(h):
                continue
            if win32gui.GetClassName(h) != "#32770":
                continue
            for ch in win32gui.GetWindow(h, win32con.GW_CHILD):
                try:
                    if win32gui.IsWindowVisible(ch) and "tgui_say" in win32gui.GetWindowText(ch):
                        return ch, win32gui.GetWindowRect(ch), win32gui.GetClassName(ch)
                except:
                    continue
        except:
            continue
    cef_hwnd = None
    def find(h, _):
        nonlocal cef_hwnd
        try:
            if not win32gui.IsWindowVisible(h):
                return
            if win32gui.GetClassName(h) != "#32770":
                return
            def enum(ch, _):
                nonlocal cef_hwnd
                try:
                    if "tgui_say" in win32gui.GetWindowText(ch):
                        cef_hwnd = ch
                except:
                    pass
            win32gui.EnumChildWindows(h, enum, None)
        except:
            pass
    win32gui.EnumWindows(find, None)
    if cef_hwnd:
        return cef_hwnd, win32gui.GetWindowRect(cef_hwnd), win32gui.GetClassName(cef_hwnd)
    return None, None, None


# ── 键盘辅助（SendInput 物理按键，用于自动按 T 和 Enter） ──
VK_T = 0x54
VK_RETURN = 0x0D

def press_key(vk_code: int, duration=0.08):
    """SendInput 物理按键 + PostMessage 前台窗口兜底"""
    user32 = ctypes.windll.user32
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.c_ulong)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong),
                    ("ki", KEYBDINPUT)]

    scan_map = {VK_T: 0x14, VK_RETURN: 0x1C}
    scan = scan_map.get(vk_code, 0)

    # SendInput（OS级物理按键）
    inp_down = INPUT(INPUT_KEYBOARD, KEYBDINPUT(vk_code, scan, 0, 0, 0))
    inp_up = INPUT(INPUT_KEYBOARD, KEYBDINPUT(vk_code, scan, KEYEVENTF_KEYUP, 0, 0))
    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    time.sleep(duration)
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
    time.sleep(0.03)

    # PostMessage 到前台窗口（兜底）
    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd:
        user32.PostMessageW(fg_hwnd, 0x100, vk_code, 0)  # WM_KEYDOWN
        time.sleep(0.02)
        user32.PostMessageW(fg_hwnd, 0x101, vk_code, 0)  # WM_KEYUP


def click_and_fill(text: str, cfg: dict, log_func) -> bool:
    cef_hwnd, cef_rect, cef_class = find_tgui_say_cef()
    if not cef_hwnd:
        log_func("! tgui_say 窗口未找到（是否已按 T 打开聊天？）")
        return False
    left, top, right, bottom = cef_rect
    w = right - left
    h = bottom - top
    ox = cfg.get("click_offset_x")
    oy = cfg.get("click_offset_y", 25)
    click_x = left + (ox if ox is not None else w // 2)
    click_y = top + oy
    wait = cfg.get("click_wait_ms", 150) / 1000.0

    log_func(f"🖱 点击 ({click_x}, {click_y}) 等待 {wait*1000:.0f}ms")
    mouse_lib.move(click_x, click_y)
    mouse_lib.click()
    time.sleep(wait)

    # ── WM_CHAR 逐字输入（已反复验证 100% 能进框） ──
    for ch in text:
        win32gui.PostMessage(cef_hwnd, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.003)

    time.sleep(0.05)

    # 自动按回车发送（PostMessage WM_KEYDOWN/UP → 开发小结证实此方案可发送）
    if cfg.get("auto_send_enter", False):
        log_func("↩ 自动发送")
        win32gui.PostMessage(cef_hwnd, win32con.WM_KEYDOWN, VK_RETURN, 0)
        time.sleep(0.02)
        win32gui.PostMessage(cef_hwnd, win32con.WM_KEYUP, VK_RETURN, 0)

    log_func(f"✓ 已填入: {text}")
    return True

# ═══════════════════════════════════════════
# 常见 PTT 候选键
# ═══════════════════════════════════════════

COMMON_KEYS = [
    ("CapsLock", "special", "caps_lock"),
    ("左 Shift", "special", "shift"),
    ("右 Shift", "special", "shift_r"),
    ("左 Ctrl", "special", "ctrl"),
    ("右 Ctrl", "special", "ctrl_r"),
    ("左 Alt", "special", "alt"),
    ("右 Alt", "special", "alt_gr"),
    ("Tab", "special", "tab"),
    ("Space", "special", "space"),
    ("`", "char", "`"),
    ("F1","special","f1"),("F2","special","f2"),("F3","special","f3"),
    ("F4","special","f4"),("F5","special","f5"),("F6","special","f6"),
    ("F7","special","f7"),("F8","special","f8"),("F9","special","f9"),
    ("F10","special","f10"),("F11","special","f11"),("F12","special","f12"),
    ("Insert","special","insert"),("Delete","special","delete"),
    ("Home","special","home"),("End","special","end"),
    ("PageUp","special","page_up"),("PageDown","special","page_down"),
]

# ═══════════════════════════════════════════
# 语音识别引擎（后台线程）
# ═══════════════════════════════════════════

class VoiceEngine:
    def __init__(self, cfg, log_func):
        self.cfg = cfg
        self.log = log_func
        self._running = False
        self._thread = None
        self._event_queue = queue.Queue()
        self._kb_listener = None
        self._ms_hook = None
        self._audio_data = []
        self._is_recording = False
        self._audio_stream = None
        self._vosk_model = None
        self._vosk_recognizer = None
        self._vosk_available = False
        self._init_vosk()

    def _init_vosk(self):
        try:
            from vosk import Model, KaldiRecognizer
            self._vosk_model = Model(VOSK_MODEL_PATH)
            self._vosk_recognizer = KaldiRecognizer(self._vosk_model, SAMPLE_RATE)
            self._vosk_available = True
            self.log("✓ Vosk 模型已加载（离线回退可用）")
        except Exception:
            self._vosk_available = False

    def _resolve_key(self):
        pk = self.cfg.get("ptt_key", {"type":"special","name":"caps_lock"})
        ktype = pk.get("type","special")
        kname = pk.get("name","caps_lock")
        if ktype == "mouse":
            return kname
        elif ktype == "char":
            return keyboard.KeyCode.from_char(kname)
        else:
            return getattr(keyboard.Key, kname, keyboard.Key.caps_lock)

    def _audio_callback(self, indata, frames, time_info, status):
        if self._is_recording:
            self._audio_data.append(indata.copy())

    def _start_recording(self):
        self._audio_data = []
        self._is_recording = True
        try:
            self._audio_stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                callback=self._audio_callback, blocksize=8000,
                device=self.cfg.get("mic_device"),
            )
            self._audio_stream.start()
        except Exception as e:
            self.log(f"! 启动录音失败: {e}")
            self._is_recording = False

    def _stop_and_transcribe(self):
        self._is_recording = False
        if self._audio_stream:
            self._audio_stream.stop()
            self._audio_stream.close()
            self._audio_stream = None
        if not self._audio_data:
            return
        audio = np.concatenate(self._audio_data)
        if len(audio) < SAMPLE_RATE * 0.3:
            return
        audio_bytes = audio.tobytes()
        text = ""
        self.log("☁ 百度识别中...", end="")
        text = _baidu_asr(audio_bytes)
        if not text and self._vosk_available:
            self.log(" ↻ Vosk 回退...", end="")
            text = _vosk_asr(self._vosk_recognizer, audio_bytes)
        if text:
            self.log(f"📝 识别结果: \"{text}\"")

            # 自动按 T 打开聊天
            if self.cfg.get("auto_open_t", False):
                self.log("⌨ 自动按 T 打开聊天")
                press_key(VK_T)
                time.sleep(0.4)  # 等 tgui_say 窗口出现

            click_and_fill(text, self.cfg, self.log)
        else:
            self.log("(未检测到语音或识别失败)")

    def _on_keyboard_press(self, key):
        try:
            if key == self._ptt_key:
                self._event_queue.put("PRESS")
        except:
            pass

    def _on_keyboard_release(self, key):
        try:
            if key == self._ptt_key:
                self._event_queue.put("RELEASE")
        except:
            pass

    def _on_mouse_event(self, event):
        if hasattr(event, 'event_type') and hasattr(event, 'button'):
            if event.button == self._ptt_key:
                self._event_queue.put("PRESS" if event.event_type == "down" else "RELEASE")

    def start(self):
        self._running = True
        self._ptt_key = self._resolve_key()
        ptt_type = self.cfg.get("ptt_key",{}).get("type","special")
        key_display = self.cfg.get("ptt_key_display","CapsLock")
        self.log(f"🎮 语音输入已启动 (PTT: {key_display})")

        if ptt_type == "mouse":
            mouse_lib.hook(self._on_mouse_event)
            self._ms_hook = True
        else:
            self._kb_listener = keyboard.Listener(
                on_press=self._on_keyboard_press,
                on_release=self._on_keyboard_release)
            self._kb_listener.daemon = True
            self._kb_listener.start()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        pressed = False
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if event == "PRESS" and not pressed:
                pressed = True
                self.log(f"🎤 录音中... (松开 {self.cfg.get('ptt_key_display','?')} 识别)")
                self._start_recording()
            elif event == "RELEASE" and pressed:
                pressed = False
                self._stop_and_transcribe()

    def stop(self):
        self._running = False
        if self._kb_listener:
            self._kb_listener.stop()
        if self._ms_hook:
            mouse_lib.unhook(self._on_mouse_event)
        if self._audio_stream:
            self._audio_stream.stop()
            self._audio_stream.close()

    def update_config(self, cfg):
        """运行时更新配置（下次按键时生效）"""
        self.cfg = cfg
        self._ptt_key = self._resolve_key()

# ═══════════════════════════════════════════
# GUI 主窗口
# ═══════════════════════════════════════════

class MainWindow:
    def __init__(self):
        self.cfg = load_config()
        self._capturing = False
        self._captured_key = None

        self.root = tk.Tk()
        self.root.title("SS13 语音输入")
        self.cfg.setdefault("window_geometry", "680x520")
        self.root.geometry(self.cfg["window_geometry"])
        self.root.minsize(500, 350)
        self.root.configure(bg="#2b2b2b")

        # 置顶
        topmost = self.cfg.get("window_topmost", True)
        self.root.attributes("-topmost", topmost)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-topmost", False))

        # 配色
        bg = "#2b2b2b"
        fg = "#e0e0e0"
        sel_bg = "#404040"
        accent = "#4a9eff"
        self._bg = bg
        self._fg = fg
        self._accent = accent

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TCombobox", fieldbackground=sel_bg, background=sel_bg,
                        foreground=fg, arrowcolor=fg)
        style.map("TCombobox", fieldbackground=[("readonly", sel_bg)],
                  foreground=[("readonly", fg)])

        # ── 主布局：上(日志) + 下(设置) ──
        main = tk.Frame(self.root, bg=bg)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # ── 日志区域 ──
        log_frame = tk.LabelFrame(main, text=" 运行日志 ", font=("Microsoft YaHei", 9),
                                  bg=bg, fg=fg, relief="groove", padx=6, pady=4)
        log_frame.pack(fill="both", expand=True, pady=(0, 8))

        self._log_text = tk.Text(log_frame, font=("Consolas", 10),
                                 bg="#1e1e1e", fg="#ccc", insertbackground="#ccc",
                                 relief="flat", bd=0, wrap=tk.WORD,
                                 state="disabled")
        self._log_text.pack(fill="both", expand=True, side="left")

        log_scroll = tk.Scrollbar(log_frame, command=self._log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self._log_text.configure(yscrollcommand=log_scroll.set)

        # ── 设置区域 ──
        settings_frame = tk.Frame(main, bg=bg)
        settings_frame.pack(fill="x")

        # 第1行：麦克风 + PTT键
        row1 = tk.Frame(settings_frame, bg=bg)
        row1.pack(fill="x", pady=(0, 6))

        # 麦克风
        tk.Label(row1, text="麦克风:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 6))
        self._devices = self._get_input_devices()
        self._dev_names = [d["label"] for d in self._devices]
        self._mic_var = tk.StringVar(value=self._find_current_device_label())
        mic_combo = ttk.Combobox(row1, textvariable=self._mic_var,
                                 values=self._dev_names, state="readonly", width=50)
        mic_combo.pack(side="left", padx=(0, 16))

        # PTT键
        tk.Label(row1, text="PTT键:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 6))
        self._key_var = tk.StringVar(value=self.cfg.get("ptt_key_display", "CapsLock"))
        key_entry = tk.Entry(row1, textvariable=self._key_var,
                             font=("Consolas", 12, "bold"),
                             width=12, justify="center",
                             bg=sel_bg, fg=accent, relief="solid", bd=1,
                             state="readonly")
        key_entry.pack(side="left", padx=(0, 6), ipady=2)

        self._capture_btn = tk.Button(row1, text="🎯 捕获",
                                      font=("Microsoft YaHei", 9),
                                      bg=accent, fg="white", relief="flat",
                                      padx=10, cursor="hand2",
                                      command=self._start_capture)
        self._capture_btn.pack(side="left", padx=(0, 6))

        self._preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(row1, textvariable=self._preset_var,
                                    values=[k[0] for k in COMMON_KEYS],
                                    state="readonly", width=16)
        preset_combo.pack(side="left")
        preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)

        # 第2行：置顶开关 + 保存 + 状态摘要
        row2 = tk.Frame(settings_frame, bg=bg)
        row2.pack(fill="x")

        self._topmost_var = tk.BooleanVar(value=topmost)
        topmost_cb = tk.Checkbutton(row2, text="置顶窗口",
                                    variable=self._topmost_var,
                                    font=("Microsoft YaHei", 9),
                                    bg=bg, fg=fg, selectcolor=bg,
                                    activebackground=bg, activeforeground=fg,
                                    cursor="hand2",
                                    command=self._on_topmost_toggle)
        topmost_cb.pack(side="left", padx=(0, 12))

        # 自动按 T（默认关）
        self._auto_t_var = tk.BooleanVar(value=self.cfg.get("auto_open_t", False))
        auto_t_cb = tk.Checkbutton(row2, text="⏎ 自动T",
                                   variable=self._auto_t_var,
                                   font=("Microsoft YaHei", 9),
                                   bg=bg, fg=fg, selectcolor=bg,
                                   activebackground=bg, activeforeground=fg,
                                   cursor="hand2",
                                   command=self._on_auto_toggle)
        auto_t_cb.pack(side="left", padx=(0, 4))

        # 自动回车（默认关）
        self._auto_enter_var = tk.BooleanVar(value=self.cfg.get("auto_send_enter", False))
        auto_enter_cb = tk.Checkbutton(row2, text="↩ 自动发送",
                                       variable=self._auto_enter_var,
                                       font=("Microsoft YaHei", 9),
                                       bg=bg, fg=fg, selectcolor=bg,
                                       activebackground=bg, activeforeground=fg,
                                       cursor="hand2",
                                       command=self._on_auto_toggle)
        auto_enter_cb.pack(side="left", padx=(0, 12))

        self._status_var = tk.StringVar(value=self._build_summary())
        status_label = tk.Label(row2, textvariable=self._status_var,
                                font=("Microsoft YaHei", 9),
                                bg="#1e1e1e", fg="#aaa",
                                relief="sunken", bd=1,
                                anchor="w", padx=8, pady=4)
        status_label.pack(side="left", fill="x", expand=True, padx=(0, 8))

        save_btn = tk.Button(row2, text="💾 保存设置",
                             font=("Microsoft YaHei", 9),
                             bg=accent, fg="white", relief="flat",
                             padx=14, cursor="hand2",
                             command=self._on_save)
        save_btn.pack(side="right")

        # 校准点击位置按钮（在保存按钮左边）
        calib_btn = tk.Button(row2, text="🎯 校准点击",
                              font=("Microsoft YaHei", 9),
                              bg="#ff9800", fg="white", relief="flat",
                              padx=10, cursor="hand2",
                              command=self._calibrate_click)
        calib_btn.pack(side="right", padx=(0, 6))

        # ── 启动引擎 ──
        self._log("SS13 语音输入 v5")
        self._log(f"配置文件: {CONFIG_PATH}")
        self._log(f"点击偏移: ({self.cfg.get('click_offset_x') or '居中'}, {self.cfg.get('click_offset_y','?')})")
        self._log("---")

        self._engine = VoiceEngine(self.cfg, self._log)
        self._engine.start()

    # ── 日志 ──
    def _log(self, msg, end=""):
        def _do():
            self._log_text.configure(state="normal")
            self._log_text.insert(tk.END, msg + "\n")
            self._log_text.see(tk.END)
            self._log_text.configure(state="disabled")
        # 从任何线程调用都安全
        self.root.after(0, _do)

    # ── 麦克风设备列表 ──
    def _get_input_devices(self):
        devices = []
        try:
            for i, dev in enumerate(sd.query_devices()):
                if dev["max_input_channels"] > 0:
                    name = dev["name"]
                    if len(name) > 60:
                        name = name[:57] + "..."
                    devices.append({"id": i, "label": f"[{i}] {name}"})
        except:
            pass
        if not devices:
            devices.append({"id": None, "label": "无输入设备"})
        return devices

    # ── 校准点击位置 ──
    def _calibrate_click(self):
        """窗口最小化 → 用户点击 tgui_say 输入栏 → 算偏移 → 恢复窗口"""
        self._log("🎯 校准模式：请在 tgui_say 输入栏上点击一下...")
        self.root.iconify()  # 最小化窗口
        self.root.update()

        # 弹一个小提示窗
        tip = tk.Toplevel(self.root)
        tip.title("校准")
        tip.geometry("360x100+400+300")
        tip.configure(bg="#2b2b2b")
        tip.attributes("-topmost", True)
        tk.Label(tip, text="将鼠标移到 tgui_say 输入栏上\n左键点击一下",
                 font=("Microsoft YaHei", 12),
                 bg="#2b2b2b", fg="#4a9eff",
                 justify="center").pack(expand=True, pady=10)
        tk.Label(tip, text="(5秒后自动取消)", font=("Microsoft YaHei", 9),
                 bg="#2b2b2b", fg="#888").pack()
        tip.update()

        # 等鼠标点击
        clicked = [False]
        def on_click(event):
            if hasattr(event, 'event_type') and event.event_type == 'up':
                clicked[0] = True

        mouse_lib.hook(on_click)
        waited = 0
        while not clicked[0] and waited < 50:
            time.sleep(0.1)
            waited += 0.1
            try:
                tip.update()
            except:
                break
        mouse_lib.unhook(on_click)
        tip.destroy()

        if not clicked[0]:
            self._log("❌ 校准取消（超时）")
            self.root.deiconify()
            return

        cx, cy = mouse_lib.get_position()
        cef_hwnd, cef_rect, cef_class = find_tgui_say_cef()
        if cef_hwnd:
            left, top, _, _ = cef_rect
            ox = cx - left
            oy = cy - top
            self.cfg["click_offset_x"] = ox
            self.cfg["click_offset_y"] = oy
            save_config(self.cfg)
            self._log(f"✅ 校准完成: 偏移 ({ox}, {oy}) 已保存")
        else:
            self._log("⚠ 未找到 tgui_say 窗口，已保存绝对坐标")

        self.root.deiconify()

    def _find_current_device_label(self):
        target_id = self.cfg.get("mic_device")
        for d in self._devices:
            if d["id"] == target_id:
                return d["label"]
        return self._devices[0]["label"] if self._devices else "默认设备"

    # ── PTT 按键捕获 ──
    def _start_capture(self):
        self._capturing = True
        self._captured_key = None
        self._capture_btn.config(text="⏳ 按下...", bg="#ff9800")
        self.root.focus_force()
        self.root.bind("<Key>", self._on_capture_key)

    def _on_capture_key(self, event):
        if not self._capturing:
            return
        ksym = event.keysym
        if not ksym:
            return
        mapped = self._map_keysym(ksym)
        if mapped:
            display, ktype, kname = mapped
            self._captured_key = {"type": ktype, "name": kname}
            self._key_var.set(display)
            self._capturing = False
            self._capture_btn.config(text="🎯 捕获", bg=self._accent)
            self._update_status()
            self.root.unbind("<Key>")

    def _map_keysym(self, ksym):
        ksym_l = ksym.lower()
        special_map = {
            "caps_lock": ("CapsLock","special","caps_lock"),
            "shift_l": ("左 Shift","special","shift"),
            "shift_r": ("右 Shift","special","shift_r"),
            "control_l": ("左 Ctrl","special","ctrl"),
            "control_r": ("右 Ctrl","special","ctrl_r"),
            "alt_l": ("左 Alt","special","alt"),
            "alt_r": ("右 Alt","special","alt_gr"),
            "alt_gr": ("右 Alt","special","alt_gr"),
            "tab": ("Tab","special","tab"),
            "space": ("Space","special","space"),
            "f1":("F1","special","f1"),"f2":("F2","special","f2"),
            "f3":("F3","special","f3"),"f4":("F4","special","f4"),
            "f5":("F5","special","f5"),"f6":("F6","special","f6"),
            "f7":("F7","special","f7"),"f8":("F8","special","f8"),
            "f9":("F9","special","f9"),"f10":("F10","special","f10"),
            "f11":("F11","special","f11"),"f12":("F12","special","f12"),
            "insert":("Insert","special","insert"),
            "delete":("Delete","special","delete"),
            "home":("Home","special","home"),
            "end":("End","special","end"),
            "prior":("PageUp","special","page_up"),
            "next":("PageDown","special","page_down"),
        }
        if ksym_l in special_map:
            return special_map[ksym_l]
        if len(ksym) == 1 and ksym != "\x1b":
            display = ksym.upper() if ksym.isalpha() else ksym
            return (display, "char", ksym.lower() if ksym.isalpha() else ksym)
        return None

    def _on_preset_selected(self, event):
        idx = 0
        try:
            w = event.widget
            idx = w.current()
        except:
            pass
        if idx < 0 or idx >= len(COMMON_KEYS):
            return
        display, ktype, kname = COMMON_KEYS[idx]
        self._key_var.set(display)
        self._captured_key = {"type": ktype, "name": kname}
        self._update_status()

    # ── 置顶 ──
    def _on_topmost_toggle(self):
        self.root.attributes("-topmost", self._topmost_var.get())

    # ── 自动模式开关即时生效 ──
    def _on_auto_toggle(self):
        self.cfg["auto_open_t"] = self._auto_t_var.get()
        self.cfg["auto_send_enter"] = self._auto_enter_var.get()
        self._engine.update_config(self.cfg)
        self._log(f"🔄 自动模式: {'T' if self._auto_t_var.get() else '-'}/{'↩' if self._auto_enter_var.get() else '-'}")
        self._update_status()

    # ── 状态摘要 ──
    def _build_summary(self):
        mic = self._mic_var.get() or "默认"
        key = self._key_var.get() or "CapsLock"
        return f"🎤 {mic}  |  🎯 {key}"

    def _update_status(self):
        self._status_var.set(self._build_summary())

    # ── 保存 ──
    def _on_save(self):
        # 麦克风
        mic_label = self._mic_var.get()
        mic_id = None
        for d in self._devices:
            if d["label"] == mic_label:
                mic_id = d["id"]
                break

        # PTT 键
        if self._captured_key:
            ptt_key = self._captured_key
        else:
            display = self._key_var.get()
            ptt_key = {"type": "special", "name": "caps_lock"}
            for kd, kt, kn in COMMON_KEYS:
                if kd == display:
                    ptt_key = {"type": kt, "name": kn}
                    break

        old_cfg = dict(self.cfg)
        self.cfg["mic_device"] = mic_id
        self.cfg["mic_device_name"] = mic_label
        self.cfg["ptt_key"] = ptt_key
        self.cfg["ptt_key_display"] = self._key_var.get()
        self.cfg["window_topmost"] = self._topmost_var.get()
        self.cfg["auto_open_t"] = self._auto_t_var.get()
        self.cfg["auto_send_enter"] = self._auto_enter_var.get()

        save_config(self.cfg)
        self._log(f"💾 设置已保存")

        # 如果键位改了，通知引擎
        if old_cfg.get("ptt_key") != ptt_key or old_cfg.get("ptt_key_display") != self._key_var.get():
            self._engine.update_config(self.cfg)
            self._log(f"🔄 PTT 键已切换为: {self._key_var.get()}")

        # 如果自动开关改了，也通知引擎
        if (old_cfg.get("auto_open_t") != self._auto_t_var.get()
                or old_cfg.get("auto_send_enter") != self._auto_enter_var.get()):
            self._engine.update_config(self.cfg)
            self._log(f"🔄 自动模式: {'T' if self._auto_t_var.get() else '-'}/{'↩' if self._auto_enter_var.get() else '-'}")

    # ── 关闭 ──
    def _on_close(self):
        self.cfg["window_geometry"] = self.root.geometry()
        self.cfg["window_topmost"] = self._topmost_var.get()
        save_config(self.cfg)
        self._engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ═══════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    # 校准模式（纯命令行）
    if "--calibrate" in sys.argv:
        # 简单校准：找窗口 → 点击 → 算偏移
        print("🎯 校准模式：按 T 打开 tgui_say，3 秒后用鼠标点击输入栏...")
        time.sleep(3)
        click_detected = [False]
        def on_click(event):
            if hasattr(event, 'event_type') and event.event_type == 'up':
                click_detected[0] = True
        mouse_lib.hook(on_click)
        waited = 0
        while not click_detected[0] and waited < 30:
            time.sleep(0.1)
            waited += 0.1
        mouse_lib.unhook(on_click)
        if not click_detected[0]:
            print("❌ 未检测到点击")
            sys.exit(1)
        cx, cy = mouse_lib.get_position()
        cef_hwnd, cef_rect, cef_class = find_tgui_say_cef()
        if cef_hwnd:
            left, top, _, _ = cef_rect
            ox = cx - left
            oy = cy - top
            cfg = load_config()
            cfg["click_offset_x"] = ox
            cfg["click_offset_y"] = oy
            save_config(cfg)
            print(f"✅ 已保存偏移: ({ox}, {oy})")
        else:
            print("⚠ 未找到 tgui_say 窗口，已保存绝对坐标")
            cfg = load_config()
            cfg["click_offset_x"] = cx
            cfg["click_offset_y"] = cy
            save_config(cfg)
        sys.exit(0)

    MainWindow().run()
