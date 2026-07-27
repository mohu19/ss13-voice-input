"""SS13 Voice Input - Push to Talk (Baidu ASR + Vosk)
带设置窗口的语音输入工具

功能：
- 实时日志：按键状态、识别结果、填入情况
- 麦克风选择
- PTT 键自定义绑定（捕获按键 + 预设列表）
- 置顶开关
- API 密钥配置（窗口内直接填）
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

# ── 常量 ──
BAIDU_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_ASR_URL = "http://vop.baidu.com/server_api"
DEV_PID = 1537  # 普通话（近场），旧版 15372 已被百度废弃
CUID = "ss13-voice-input-win10"
VERSION = "v7.1-ready"

# ── 路径 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
CONFIG_PATH = os.path.join(SCRIPT_DIR, "ss13-voice-config.json")
API_FILE_PATH = os.path.join(os.path.dirname(SCRIPT_DIR) if getattr(sys, 'frozen', False) else SCRIPT_DIR, "api.txt")
CALIBRATE_FILE_PATH = os.path.join(os.path.dirname(SCRIPT_DIR) if getattr(sys, 'frozen', False) else SCRIPT_DIR, "calibrate.txt")
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
        "window_geometry": "680x640",
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

# ── api.txt 读写 ──

def load_api_from_file() -> dict:
    """从 api.txt 读取所有 API 配置，返回 dict"""
    cfg = {"provider": "baidu", "baidu_api_key": "", "baidu_secret_key": "",
           "iflytek_app_id": "", "iflytek_api_key": "", "iflytek_api_secret": ""}
    if not os.path.exists(API_FILE_PATH):
        return cfg
    try:
        with open(API_FILE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if k in cfg:
                        cfg[k] = v
        return cfg
    except Exception:
        return cfg

def save_api_to_file(api_cfg: dict):
    with open(API_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(f"provider={api_cfg.get('provider', 'baidu')}\n")
        f.write(f"baidu_api_key={api_cfg.get('baidu_api_key', '')}\n")
        f.write(f"baidu_secret_key={api_cfg.get('baidu_secret_key', '')}\n")
        f.write(f"iflytek_app_id={api_cfg.get('iflytek_app_id', '')}\n")
        f.write(f"iflytek_api_key={api_cfg.get('iflytek_api_key', '')}\n")
        f.write(f"iflytek_api_secret={api_cfg.get('iflytek_api_secret', '')}\n")

# ── calibrate.txt 读写 ──

DEFAULT_CLICK_OFFSET_Y = 25

def load_calibrate_from_file() -> tuple:
    """从 calibrate.txt 读取点击偏移，返回 (offset_x, offset_y)"""
    if not os.path.exists(CALIBRATE_FILE_PATH):
        return (None, DEFAULT_CLICK_OFFSET_Y)
    try:
        with open(CALIBRATE_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        if len(lines) >= 2:
            return (int(lines[0].strip()), int(lines[1].strip()))
        return (None, DEFAULT_CLICK_OFFSET_Y)
    except Exception:
        return (None, DEFAULT_CLICK_OFFSET_Y)

def save_calibrate_to_file(ox: int, oy: int):
    with open(CALIBRATE_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(f"{ox}\n{oy}\n")

# ═══════════════════════════════════════════
# 百度 ASR（API key 由调用方传入）
# ═══════════════════════════════════════════

_access_token = None
_token_expiry = 0.0

def _get_access_token(api_key: str, secret_key: str) -> str:
    global _access_token, _token_expiry
    if not api_key or not secret_key:
        return ""
    now = time.time()
    if _access_token and now < _token_expiry - 300:
        return _access_token
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
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
    except Exception:
        return ""

def verify_api(api_key: str, secret_key: str) -> str:
    """验证 API 密钥，成功返回空字符串，失败返回错误信息"""
    if not api_key or not secret_key:
        return "API Key 和 Secret Key 不能为空"
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    })
    url = f"{BAIDU_TOKEN_URL}?{params}"
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("access_token"):
                return ""
            return data.get("error_description", "未知错误")
    except Exception as e:
        return f"网络错误: {e}"

def _baidu_asr(audio_bytes: bytes, api_key: str, secret_key: str, log_func=None) -> str:
    token = _get_access_token(api_key, secret_key)
    if not token:
        if log_func: log_func("! 百度 token 获取失败（检查 API Key/Secret Key 或网络）")
        return ""
    params = urllib.parse.urlencode({"dev_pid": DEV_PID, "cuid": CUID, "token": token})
    url = f"{BAIDU_ASR_URL}?{params}"
    req = urllib.request.Request(url, data=audio_bytes, method="POST")
    req.add_header("Content-Type", "audio/pcm;rate=16000")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            err_no = result.get("err_no", -1)
            if err_no == 0:
                texts = result.get("result", [])
                if texts:
                    return texts[0].strip()
            else:
                err_msg = result.get("err_msg", "未知错误")
                if log_func: log_func(f"! 百度 ASR 返回错误 [{err_no}]: {err_msg}")
            return ""
    except urllib.error.HTTPError as e:
        if log_func: log_func(f"! 百度 ASR HTTP 错误: {e.code} {e.reason}")
        return ""
    except urllib.error.URLError as e:
        if log_func: log_func(f"! 百度 ASR 网络错误: {e.reason}")
        return ""
    except Exception as e:
        if log_func: log_func(f"! 百度 ASR 未知错误: {e}")
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
# 讯飞 ASR（WebSocket + HMAC-SHA256）
# ═══════════════════════════════════════════

import hmac, hashlib, base64
from urllib.parse import urlencode, urlparse

def _build_iflytek_url(app_id: str, api_key: str, api_secret: str) -> str:
    host = "iat-api.xfyun.cn"
    path = "/v2/iat"
    # 强制英文 locale 确保 RFC1123 格式正确
    import locale as _locale
    _saved = _locale.getlocale(_locale.LC_TIME)
    try:
        _locale.setlocale(_locale.LC_TIME, "C")
    except Exception:
        pass
    date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    try:
        if _saved[0]:
            _locale.setlocale(_locale.LC_TIME, _saved)
    except Exception:
        pass

    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature_sha = hmac.new(api_secret.encode(), signature_origin.encode(), hashlib.sha256).digest()
    signature = base64.b64encode(signature_sha).decode()

    auth_origin = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    authorization = base64.b64encode(auth_origin.encode()).decode()

    params = urlencode({"host": host, "date": date, "authorization": authorization})
    return f"wss://{host}{path}?{params}"

def _iflytek_asr(audio_bytes: bytes, app_id: str, api_key: str, api_secret: str, log_func=None) -> str:
    if not app_id or not api_key or not api_secret:
        if log_func: log_func("! 讯飞配置不完整")
        return ""
    try:
        import websocket
    except ImportError:
        if log_func: log_func("! 缺少 websocket-client 库")
        return ""

    url = _build_iflytek_url(app_id, api_key, api_secret)
    result_text = [""]
    error_msg = [""]
    done = [False]

    def on_message(ws, msg):
        try:
            data = json.loads(msg)
            if data.get("code") != 0:
                error_msg[0] = f"讯飞错误 {data['code']}: {data.get('message', '')}"
                done[0] = True
                return
            result = data.get("data", {}).get("result", {})
            if result:
                ws_list = result.get("ws", [])
                words = []
                for w in ws_list:
                    for cw in w.get("cw", []):
                        words.append(cw.get("w", ""))
                result_text[0] = "".join(words)
            if data.get("data", {}).get("status") == 2:
                done[0] = True
        except Exception as e:
            error_msg[0] = f"讯飞解析错误: {e}"
            done[0] = True

    def on_error(ws, err):
        error_msg[0] = f"讯飞 WebSocket 错误: {err}"
        done[0] = True

    def on_close(ws, status, msg):
        if not done[0]:
            done[0] = True

    try:
        ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
        wst = threading.Thread(target=ws.run_forever, daemon=True)
        wst.start()
        time.sleep(0.5)

        if not ws.sock or not ws.sock.connected:
            error_msg[0] = "讯飞 WebSocket 连接失败"
            ws.close()
            done[0] = True
            return ""

        # 发送音频（快速发送，不等待帧间隔）
        b64_audio = base64.b64encode(audio_bytes).decode()
        frame_size = 1280
        total = len(b64_audio)
        sent = 0
        first = True
        while sent < total:
            chunk = b64_audio[sent:sent + frame_size]
            status = 0 if first else (2 if sent + frame_size >= total else 1)
            payload = {"data": {"status": status, "format": "audio/L16;rate=16000",
                                "encoding": "raw", "audio": chunk}}
            if first:
                payload["common"] = {"app_id": app_id}
                payload["business"] = {"language": "zh_cn", "domain": "iat",
                                       "accent": "mandarin", "ptt": 1}
                first = False
            try:
                ws.send(json.dumps(payload))
            except Exception as e:
                error_msg[0] = f"讯飞发送失败: {e}"
                done[0] = True
                break
            sent += frame_size
            time.sleep(0.002)  # 极小间隔，仅让出 CPU

        # 等待结果，最多15秒
        for _ in range(150):
            if done[0]:
                break
            time.sleep(0.1)
        ws.close()

        if error_msg[0]:
            if log_func: log_func(f"! {error_msg[0]}")
            return ""
        return result_text[0].strip()
    except Exception as e:
        if log_func: log_func(f"! 讯飞 ASR 异常: {e}")
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

# ── 键盘辅助 ──
VK_T = 0x54
VK_RETURN = 0x0D

def press_key(vk_code: int, duration=0.08):
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

    inp_down = INPUT(INPUT_KEYBOARD, KEYBDINPUT(vk_code, scan, 0, 0, 0))
    inp_up = INPUT(INPUT_KEYBOARD, KEYBDINPUT(vk_code, scan, KEYEVENTF_KEYUP, 0, 0))
    user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
    time.sleep(duration)
    user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
    time.sleep(0.03)

    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd:
        user32.PostMessageW(fg_hwnd, 0x100, vk_code, 0)
        time.sleep(0.02)
        user32.PostMessageW(fg_hwnd, 0x101, vk_code, 0)

def click_and_fill(text: str, cfg: dict, log_func) -> bool:
    cef_hwnd, cef_rect, cef_class = find_tgui_say_cef()
    if not cef_hwnd:
        log_func("! tgui_say 窗口未找到（是否已按 T 打开聊天？）")
        return False
    left, top, right, bottom = cef_rect
    w = right - left
    h = bottom - top
    ox, oy = load_calibrate_from_file()
    click_x = left + (ox if ox is not None else w // 2)
    click_y = top + oy
    wait = 0.150

    log_func(f"🖱 点击 ({click_x}, {click_y}) 等待 {wait*1000:.0f}ms")
    mouse_lib.move(click_x, click_y)
    mouse_lib.click()
    time.sleep(wait)

    for ch in text:
        win32gui.PostMessage(cef_hwnd, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.003)

    time.sleep(0.05)

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
        duration_ms = len(audio) / SAMPLE_RATE * 1000
        if len(audio) < SAMPLE_RATE * 0.3:
            self.log(f"(录音太短: {duration_ms:.0f}ms，需至少 300ms)")
            return
        self.log(f"☁ 识别中 ({duration_ms:.0f}ms 音频)...", end="")
        audio_bytes = audio.tobytes()

        api_cfg = load_api_from_file()
        provider = api_cfg.get("provider", "baidu")
        text = ""
        if provider == "baidu":
            ak = api_cfg.get("baidu_api_key", "")
            sk = api_cfg.get("baidu_secret_key", "")
            if ak and sk:
                text = _baidu_asr(audio_bytes, ak, sk, log_func=self.log)
            else:
                self.log("⚠ 百度 API 未配置", end="")
        elif provider == "iflytek":
            app_id = api_cfg.get("iflytek_app_id", "")
            ik = api_cfg.get("iflytek_api_key", "")
            i_secret = api_cfg.get("iflytek_api_secret", "")
            if app_id and ik and i_secret:
                text = _iflytek_asr(audio_bytes, app_id, ik, i_secret, log_func=self.log)
            else:
                self.log("⚠ 讯飞 API 未配置", end="")
        else:
            self.log(f"⚠ 未知识别引擎: {provider}", end="")

        if not text and self._vosk_available:
            self.log(" ↻ Vosk 回退...", end="")
            text = _vosk_asr(self._vosk_recognizer, audio_bytes)
        if text:
            self.log(f"📝 识别结果: \"{text}\"")

            if self.cfg.get("auto_open_t", False):
                self.log("⌨ 自动按 T 打开聊天")
                press_key(VK_T)
                time.sleep(0.4)

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
        self._log_api_status()

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

    def _log_api_status(self):
        api_cfg = load_api_from_file()
        provider = api_cfg.get("provider", "baidu")
        if provider == "baidu":
            ak = api_cfg.get("baidu_api_key", "")
            if ak:
                masked = ak[:4] + "****" + ak[-4:] if len(ak) > 8 else "****"
                self.log(f"🔑 百度 API: {masked}")
            else:
                self.log("⚠ 百度 API 未配置 — 在「API 配置」中填入密钥")
        elif provider == "iflytek":
            ik = api_cfg.get("iflytek_api_key", "")
            if ik:
                masked = ik[:4] + "****" + ik[-4:] if len(ik) > 8 else "****"
                self.log(f"🔑 讯飞 API: {masked}")
            else:
                self.log("⚠ 讯飞 API 未配置 — 在「API 配置」中填入密钥")

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
        self.root.title(f"SS13 语音输入 {VERSION}")
        self.cfg.setdefault("window_geometry", "680x640")
        self.root.geometry(self.cfg["window_geometry"])
        self.root.minsize(500, 500)
        self.root.configure(bg="#2b2b2b")

        topmost = self.cfg.get("window_topmost", True)
        self.root.attributes("-topmost", topmost)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-topmost", False))

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

        # ── 主布局：上(日志) + 中(设置) + 下(API) ──
        main = tk.Frame(self.root, bg=bg)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # ── 日志区域 ──
        log_frame = tk.LabelFrame(main, text=" 运行日志 ", font=("Microsoft YaHei", 9),
                                  bg=bg, fg=fg, relief="groove", padx=6, pady=4)
        log_frame.pack(fill="both", expand=True, pady=(0, 6))

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

        # ── 第1行：麦克风 ──
        row1 = tk.Frame(settings_frame, bg=bg)
        row1.pack(fill="x", pady=(0, 4))

        self._key_var = tk.StringVar(value=self.cfg.get("ptt_key_display", "CapsLock"))
        tk.Label(row1, text="麦克风:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 6))
        self._devices = self._get_input_devices()
        self._dev_names = [d["label"] for d in self._devices]
        self._mic_var = tk.StringVar(value=self._find_current_device_label())
        mic_combo = ttk.Combobox(row1, textvariable=self._mic_var,
                                 values=self._dev_names, state="readonly")
        mic_combo.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self._status_var = tk.StringVar(value=self._build_summary())
        status_label = tk.Label(row1, textvariable=self._status_var,
                                font=("Microsoft YaHei", 9),
                                bg="#1e1e1e", fg="#aaa",
                                relief="sunken", bd=1,
                                anchor="w", padx=6, pady=2)
        status_label.pack(side="left", padx=(0, 0))

        # ── 第2行：PTT键 ──
        row2 = tk.Frame(settings_frame, bg=bg)
        row2.pack(fill="x", pady=(0, 4))

        tk.Label(row2, text="PTT键:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 6))
        key_entry = tk.Entry(row2, textvariable=self._key_var,
                             font=("Consolas", 12, "bold"),
                             width=10, justify="center",
                             bg=sel_bg, fg=accent, relief="solid", bd=1,
                             state="readonly")
        key_entry.pack(side="left", padx=(0, 4), ipady=2)

        self._capture_btn = tk.Button(row2, text="🎯 捕获",
                                       font=("Microsoft YaHei", 9),
                                       bg=accent, fg="white", relief="flat",
                                       padx=8, cursor="hand2",
                                       command=self._start_capture)
        self._capture_btn.pack(side="left", padx=(0, 4))

        tk.Label(row2, text="预设:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 2))
        self._preset_var = tk.StringVar()
        preset_combo = ttk.Combobox(row2, textvariable=self._preset_var,
                                    values=[k[0] for k in COMMON_KEYS],
                                    state="readonly", width=14)
        preset_combo.pack(side="left")
        preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)

        # ── 第3行：开关 + 偏移 + 按钮 ──
        row3 = tk.Frame(settings_frame, bg=bg)
        row3.pack(fill="x", pady=(0, 6))

        self._topmost_var = tk.BooleanVar(value=topmost)
        topmost_cb = tk.Checkbutton(row3, text="置顶",
                                    variable=self._topmost_var,
                                    font=("Microsoft YaHei", 9),
                                    bg=bg, fg=fg, selectcolor=bg,
                                    activebackground=bg, activeforeground=fg,
                                    cursor="hand2",
                                    command=self._on_topmost_toggle)
        topmost_cb.pack(side="left", padx=(0, 6))

        self._auto_t_var = tk.BooleanVar(value=self.cfg.get("auto_open_t", False))
        auto_t_cb = tk.Checkbutton(row3, text="⏎T",
                                   variable=self._auto_t_var,
                                   font=("Microsoft YaHei", 9),
                                   bg=bg, fg=fg, selectcolor=bg,
                                   activebackground=bg, activeforeground=fg,
                                   cursor="hand2",
                                   command=self._on_auto_toggle)
        auto_t_cb.pack(side="left", padx=(0, 2))

        self._auto_enter_var = tk.BooleanVar(value=self.cfg.get("auto_send_enter", False))
        auto_enter_cb = tk.Checkbutton(row3, text="↩发送",
                                       variable=self._auto_enter_var,
                                       font=("Microsoft YaHei", 9),
                                       bg=bg, fg=fg, selectcolor=bg,
                                       activebackground=bg, activeforeground=fg,
                                       cursor="hand2",
                                       command=self._on_auto_toggle)
        auto_enter_cb.pack(side="left", padx=(0, 10))

        # 偏移 X/Y 微调
        _cal_ox, _cal_oy = load_calibrate_from_file()
        tk.Label(row3, text="偏移", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg).pack(side="left", padx=(0, 2))
        self._cal_ox_var = tk.StringVar(value=str(_cal_ox) if _cal_ox is not None else "居中")
        cal_ox_spin = tk.Spinbox(row3, from_=0, to=500, textvariable=self._cal_ox_var,
                                 font=("Consolas", 8), width=4, justify="center",
                                 bg=sel_bg, fg=fg, buttonbackground=sel_bg,
                                 relief="solid", bd=1)
        cal_ox_spin.pack(side="left", padx=(1, 0))
        tk.Label(row3, text="X", font=("Microsoft YaHei", 8),
                 bg=bg, fg="#888").pack(side="left")
        self._cal_oy_var = tk.StringVar(value=str(_cal_oy))
        cal_oy_spin = tk.Spinbox(row3, from_=0, to=200, textvariable=self._cal_oy_var,
                                 font=("Consolas", 8), width=4, justify="center",
                                 bg=sel_bg, fg=fg, buttonbackground=sel_bg,
                                 relief="solid", bd=1)
        cal_oy_spin.pack(side="left", padx=(1, 0))
        tk.Label(row3, text="Y", font=("Microsoft YaHei", 8),
                 bg=bg, fg="#888").pack(side="left", padx=(0, 8))

        calib_btn = tk.Button(row3, text="🎯校准",
                              font=("Microsoft YaHei", 9),
                              bg="#ff9800", fg="white", relief="flat",
                              padx=8, cursor="hand2",
                              command=self._calibrate_click)
        calib_btn.pack(side="left", padx=(0, 4))

        save_btn = tk.Button(row3, text="💾保存",
                             font=("Microsoft YaHei", 9),
                             bg=accent, fg="white", relief="flat",
                             padx=10, cursor="hand2",
                             command=self._on_save)
        save_btn.pack(side="left", padx=(0, 0))

        # ── API 配置区域 ──
        _api_cfg = load_api_from_file()
        self._api_provider_var = tk.StringVar(value=_api_cfg.get("provider", "baidu"))
        api_frame = tk.LabelFrame(settings_frame, text=" API 配置 ",
                                  font=("Microsoft YaHei", 9),
                                  bg=bg, fg="#ff9800", relief="groove", padx=8, pady=6)
        api_frame.pack(fill="x", pady=(0, 6))

        api_row0 = tk.Frame(api_frame, bg=bg)
        api_row0.pack(fill="x", pady=(0, 4))
        tk.Label(api_row0, text="识别引擎:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        provider_combo = ttk.Combobox(api_row0, textvariable=self._api_provider_var,
                                      values=["baidu", "iflytek"],
                                      state="readonly", width=14)
        provider_combo.pack(side="left")
        provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        # 百度 API 字段
        self._api_frame_baidu = tk.Frame(api_frame, bg=bg)
        self._api_frame_baidu.pack(fill="x")
        bd_row1 = tk.Frame(self._api_frame_baidu, bg=bg)
        bd_row1.pack(fill="x", pady=(0, 4))
        tk.Label(bd_row1, text="API Key:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self._baidu_ak_var = tk.StringVar(value=_api_cfg.get("baidu_api_key", ""))
        tk.Entry(bd_row1, textvariable=self._baidu_ak_var,
                 font=("Consolas", 9), width=50,
                 bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0",
                 relief="sunken", bd=2).pack(side="left", padx=(0, 8), ipady=2)
        tk.Label(bd_row1, text="申请: console.bce.baidu.com",
                 font=("Microsoft YaHei", 8), bg=bg, fg="#888").pack(side="left")

        bd_row2 = tk.Frame(self._api_frame_baidu, bg=bg)
        bd_row2.pack(fill="x", pady=(0, 4))
        tk.Label(bd_row2, text="Secret Key:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self._baidu_sk_var = tk.StringVar(value=_api_cfg.get("baidu_secret_key", ""))
        tk.Entry(bd_row2, textvariable=self._baidu_sk_var,
                 font=("Consolas", 9), width=50,
                 bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0",
                 relief="sunken", bd=2).pack(side="left", padx=(0, 8), ipady=2)

        # 讯飞 API 字段
        self._api_frame_iflytek = tk.Frame(api_frame, bg=bg)
        if_row1 = tk.Frame(self._api_frame_iflytek, bg=bg)
        if_row1.pack(fill="x", pady=(0, 4))
        tk.Label(if_row1, text="APPID:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self._iflytek_appid_var = tk.StringVar(value=_api_cfg.get("iflytek_app_id", ""))
        tk.Entry(if_row1, textvariable=self._iflytek_appid_var,
                 font=("Consolas", 9), width=50,
                 bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0",
                 relief="sunken", bd=2).pack(side="left", padx=(0, 8), ipady=2)
        tk.Label(if_row1, text="申请: xfyun.cn",
                 font=("Microsoft YaHei", 8), bg=bg, fg="#888").pack(side="left")

        if_row2 = tk.Frame(self._api_frame_iflytek, bg=bg)
        if_row2.pack(fill="x", pady=(0, 4))
        tk.Label(if_row2, text="API Key:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self._iflytek_ak_var = tk.StringVar(value=_api_cfg.get("iflytek_api_key", ""))
        tk.Entry(if_row2, textvariable=self._iflytek_ak_var,
                 font=("Consolas", 9), width=50,
                 bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0",
                 relief="sunken", bd=2).pack(side="left", padx=(0, 8), ipady=2)

        if_row3 = tk.Frame(self._api_frame_iflytek, bg=bg)
        if_row3.pack(fill="x", pady=(0, 4))
        tk.Label(if_row3, text="API Secret:", font=("Microsoft YaHei", 9),
                 bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self._iflytek_secret_var = tk.StringVar(value=_api_cfg.get("iflytek_api_secret", ""))
        tk.Entry(if_row3, textvariable=self._iflytek_secret_var,
                 font=("Consolas", 9), width=50,
                 bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0",
                 relief="sunken", bd=2).pack(side="left", padx=(0, 8), ipady=2)

        # 测试按钮行
        api_test_row = tk.Frame(api_frame, bg=bg)
        api_test_row.pack(fill="x", pady=(4, 0))
        self._api_test_btn = tk.Button(api_test_row, text="验证连接",
                                       font=("Microsoft YaHei", 9),
                                       bg="#4CAF50", fg="white", relief="flat",
                                       padx=12, cursor="hand2",
                                       command=self._on_api_test)
        self._api_test_btn.pack(side="left")
        self._api_status_label = tk.Label(api_test_row, text="",
                                          font=("Microsoft YaHei", 9),
                                          bg=bg, fg="#aaa", anchor="w")
        self._api_status_label.pack(side="left", padx=(8, 0))

        # 根据当前 provider 显示/隐藏对应字段
        self._toggle_api_fields()

        # ── 启动引擎 ──
        ox, oy = load_calibrate_from_file()
        self._log(f"SS13 语音输入 {VERSION}")
        self._log(f"配置文件: {CONFIG_PATH}")
        self._log(f"点击偏移: ({ox or '居中'}, {oy})")
        self._log(f"自动模式: {'T' if self.cfg.get('auto_open_t') else '-'}/{'↩' if self.cfg.get('auto_send_enter') else '-'}")
        self._log_api_startup()
        self._log("---")

        self._engine = VoiceEngine(self.cfg, self._log)
        self._engine.start()

    def _build_api_cfg_from_gui(self) -> dict:
        return {
            "provider": self._api_provider_var.get(),
            "baidu_api_key": self._baidu_ak_var.get().strip(),
            "baidu_secret_key": self._baidu_sk_var.get().strip(),
            "iflytek_app_id": self._iflytek_appid_var.get().strip(),
            "iflytek_api_key": self._iflytek_ak_var.get().strip(),
            "iflytek_api_secret": self._iflytek_secret_var.get().strip(),
        }

    def _toggle_api_fields(self):
        p = self._api_provider_var.get()
        self._api_frame_baidu.pack_forget()
        self._api_frame_iflytek.pack_forget()
        if p == "baidu":
            self._api_frame_baidu.pack(fill="x")
            self._api_test_btn.config(text="验证连接(百度)")
        else:
            self._api_frame_iflytek.pack(fill="x")
            self._api_test_btn.config(text="验证连接(讯飞)")

    def _on_provider_change(self, event=None):
        self._toggle_api_fields()

    def _log_api_startup(self):
        api_cfg = load_api_from_file()
        provider = api_cfg.get("provider", "baidu")
        if provider == "baidu":
            ak = api_cfg.get("baidu_api_key", "")
            if ak:
                masked = ak[:4] + "****" + ak[-4:] if len(ak) > 8 else "****"
                self._log(f"🔑 百度 API: {masked}")
            else:
                self._log("⚠ 百度 API 未配置 — 在「API 配置」中填入密钥")
        elif provider == "iflytek":
            ik = api_cfg.get("iflytek_api_key", "")
            if ik:
                masked = ik[:4] + "****" + ik[-4:] if len(ik) > 8 else "****"
                self._log(f"🔑 讯飞 API: {masked}")
            else:
                self._log("⚠ 讯飞 API 未配置 — 在「API 配置」中填入密钥")

    # ── 日志 ──
    def _log(self, msg, end=""):
        def _do():
            self._log_text.configure(state="normal")
            self._log_text.insert(tk.END, msg + "\n")
            self._log_text.see(tk.END)
            self._log_text.configure(state="disabled")
        self.root.after(0, _do)

    # ── API 验证 ──
    def _on_api_test(self):
        api_cfg = self._build_api_cfg_from_gui()
        provider = api_cfg["provider"]

        if provider == "baidu":
            ak, sk = api_cfg["baidu_api_key"], api_cfg["baidu_secret_key"]
            if not ak or not sk:
                self._api_status_label.config(text="❌ 百度 API Key/Secret 不能为空", fg="#ff5252")
                return
            self._api_status_label.config(text="⏳ 验证百度连接...", fg="#ff9800")
            self.root.update()
            err = verify_api(ak, sk)
            if err:
                self._api_status_label.config(text=f"❌ {err}", fg="#ff5252")
            else:
                self._api_status_label.config(text="✅ 百度连接成功", fg="#4CAF50")
                save_api_to_file(api_cfg)
                self._log("🔑 百度 API 已验证并保存")
        else:
            # 讯飞只检查配置完整性，不实际调用（WebSocket 鉴权在发送时校验）
            if not api_cfg["iflytek_app_id"] or not api_cfg["iflytek_api_key"] or not api_cfg["iflytek_api_secret"]:
                self._api_status_label.config(text="❌ 讯飞 APPID/Key/Secret 不能为空", fg="#ff5252")
                return
            self._api_status_label.config(text="✅ 讯飞配置已保存（实际连接在说话时验证）", fg="#4CAF50")
            save_api_to_file(api_cfg)
            self._log("🔑 讯飞 API 配置已保存")

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
        self._log("🎯 校准模式：请在 tgui_say 输入栏上点击一下...")
        self.root.iconify()
        self.root.update()

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
            save_calibrate_to_file(ox, oy)
            self._cal_ox_var.set(str(ox))
            self._cal_oy_var.set(str(oy))
            self._log(f"✅ 校准完成: 偏移 ({ox}, {oy}) 已保存")
        else:
            self._log("⚠ 未找到 tgui_say 窗口")

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
        mic_label = self._mic_var.get()
        mic_id = None
        for d in self._devices:
            if d["label"] == mic_label:
                mic_id = d["id"]
                break

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
        api_cfg = self._build_api_cfg_from_gui()
        save_api_to_file(api_cfg)
        # 保存偏移
        try:
            ox = int(self._cal_ox_var.get())
            oy = int(self._cal_oy_var.get())
            save_calibrate_to_file(ox, oy)
        except ValueError:
            pass
        self._log("💾 设置已保存")

        if old_cfg.get("ptt_key") != ptt_key or old_cfg.get("ptt_key_display") != self._key_var.get():
            self._engine.update_config(self.cfg)
            self._log(f"🔄 PTT 键已切换为: {self._key_var.get()}")

        if (old_cfg.get("auto_open_t") != self._auto_t_var.get()
                or old_cfg.get("auto_send_enter") != self._auto_enter_var.get()):
            self._engine.update_config(self.cfg)
            self._log(f"🔄 自动模式: {'T' if self._auto_t_var.get() else '-'}/{'↩' if self._auto_enter_var.get() else '-'}")

    # ── 关闭 ──
    def _on_close(self):
        self.cfg["window_geometry"] = self.root.geometry()
        self.cfg["window_topmost"] = self._topmost_var.get()
        # 保存 PTT 键
        if self._captured_key:
            ptt_key = self._captured_key
        else:
            display = self._key_var.get()
            ptt_key = {"type": "special", "name": "caps_lock"}
            for kd, kt, kn in COMMON_KEYS:
                if kd == display:
                    ptt_key = {"type": kt, "name": kn}
                    break
        self.cfg["ptt_key"] = ptt_key
        self.cfg["ptt_key_display"] = self._key_var.get()
        # 保存 API
        api_cfg = self._build_api_cfg_from_gui()
        save_config(self.cfg)
        save_api_to_file(api_cfg)
        # 保存偏移
        try:
            ox = int(self._cal_ox_var.get())
            oy = int(self._cal_oy_var.get())
            save_calibrate_to_file(ox, oy)
        except ValueError:
            pass
        self._engine.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ═══════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    if "--calibrate" in sys.argv:
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
            save_calibrate_to_file(ox, oy)
            print(f"✅ 已保存偏移: ({ox}, {oy})")
        else:
            print("⚠ 未找到 tgui_say 窗口")
        sys.exit(0)

    MainWindow().run()
