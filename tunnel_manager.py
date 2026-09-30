import os
import sys
import subprocess
import re
import time
import requests

STOCK_DIR = os.path.dirname(os.path.abspath(__file__))
URL_FILE = os.path.join(STOCK_DIR, "tunnel_url.txt")
LOG_FILE = os.path.join(STOCK_DIR, "cloudflared.log")
CLOUDFLARED_EXE = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"

def is_tunnel_alive(url):
    """檢查遠端隧道網址是否正常響應"""
    if not url:
        return False
    try:
        r = requests.get(f"{url}/_stcore/health", timeout=5)
        return r.status_code == 200
    except Exception:
        try:
            r = requests.get(url, timeout=5)
            return r.status_code == 200
        except Exception:
            return False

def kill_existing_cloudflared():
    """終止既有的 cloudflared 進程"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "cloudflared.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
    except Exception:
        pass

def start_new_tunnel():
    """啟動新的 cloudflared 隧道並解析網址"""
    if not os.path.exists(CLOUDFLARED_EXE):
        return None

    kill_existing_cloudflared()
    time.sleep(1)

    # 清空或建立 log 檔
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
    except Exception:
        pass

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

    cmd = [
        CLOUDFLARED_EXE,
        "tunnel",
        "--url", "http://localhost:8501",
        "--protocol", "http2",
        "--logfile", LOG_FILE,
        "--no-autoupdate"
    ]

    subprocess.Popen(
        cmd,
        cwd=STOCK_DIR,
        creationflags=creationflags
    )

    # 從 log 檔中解析 URL (最多等待 25 秒)
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    url_found = None
    for _ in range(50):
        time.sleep(0.5)
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                    matches = url_pattern.findall(content)
                    if matches:
                        url_found = matches[-1]
                        break
            except Exception:
                pass

    if url_found:
        try:
            with open(URL_FILE, "w", encoding="utf-8") as f:
                f.write(url_found)
        except Exception:
            pass

    return url_found

def main_loop():
    """監控與守護迴圈：電腦休眠喚醒或斷線時自動重新連線"""
    current_url = None
    if os.path.exists(URL_FILE):
        try:
            with open(URL_FILE, "r", encoding="utf-8") as f:
                current_url = f.read().strip()
        except Exception:
            pass

    # 若目前無有效網址或隧道已斷，立即啟動
    if not current_url or not is_tunnel_alive(current_url):
        current_url = start_new_tunnel()

    # 背景常駐守護迴圈 (每 20 秒檢查一次)
    consecutive_failures = 0
    while True:
        time.sleep(20)
        # 檢查本機 Streamlit 是否正常運行
        streamlit_ok = False
        try:
            r = requests.get("http://localhost:8501/_stcore/health", timeout=3)
            streamlit_ok = (r.status_code == 200)
        except Exception:
            pass

        if not streamlit_ok:
            # 本機 Streamlit 尚未就緒，稍後再測
            continue

        if is_tunnel_alive(current_url):
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            # 連續 2 次探測失敗 (40 秒) 判定為斷線 (如休眠喚醒、網路切換)，自動重啟隧道
            if consecutive_failures >= 2:
                current_url = start_new_tunnel()
                consecutive_failures = 0

if __name__ == "__main__":
    main_loop()
