"""
SS13 Voice Input — API 配置向导
引导用户注册百度短语音识别 API 并写入 .env
"""
import os, sys, json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "ss13-voice-config.json")

print("=" * 50)
print("  SS13 语音输入 — API 配置")
print("=" * 50)
print()
print("本工具需要 百度短语音识别 API 密钥才能工作。")
print()
print("注册步骤：")
print("  1. 打开 https://console.bce.baidu.com/ai/#/ai/speech/overview")
print("  2. 登录百度账号")
print("  3. 在「语音技术」→「短语音识别」→「创建应用」")
print("  4. 创建后获得 API Key 和 Secret Key")
print("  5. 免费额度：5万次/天")
print()

ak = input("请输入 API Key: ").strip()
sk = input("请输入 Secret Key: ").strip()

if not ak or not sk:
    print("\n❌ API Key 和 Secret Key 不能为空")
    input("按 Enter 退出...")
    sys.exit(1)

with open(ENV_PATH, "w", encoding="utf-8") as f:
    f.write(f"BAIDU_API_KEY={ak}\n")
    f.write(f"BAIDU_SECRET_KEY={sk}\n")

# 确保配置里有 mic_device
if not os.path.exists(CONFIG_PATH):
    cfg = {"mic_device": None, "mic_device_name": "默认设备",
           "ptt_key": {"type": "special", "name": "caps_lock"},
           "ptt_key_display": "CapsLock",
           "click_offset_x": None, "click_offset_y": 25, "click_wait_ms": 150}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

print(f"\n✅ 已写入 {ENV_PATH}")
print("✅ 配置完成，可以启动语音输入了")
print()

# 验证
print("验证网络连接...", end=" ", flush=True)
import urllib.request, urllib.parse, json as _json
try:
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ak, "client_secret": sk})
    with urllib.request.urlopen(
        f"https://aip.baidubce.com/oauth/2.0/token?{params}", timeout=5) as r:
        data = _json.loads(r.read())
        if data.get("access_token"):
            print("✅ 连接成功")
        else:
            print(f"⚠ {data.get('error_description', '未知错误')}")
except Exception as e:
    print(f"⚠ {e}")

input("按 Enter 退出...")
