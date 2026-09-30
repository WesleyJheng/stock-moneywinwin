import os
import sys
import subprocess
import requests

def main():
    stock_dir = os.path.dirname(os.path.abspath(__file__))
    pythonw = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python311\pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = "pythonw"

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW

    # 1. 檢查並啟動 Streamlit
    streamlit_running = False
    try:
        r = requests.get("http://localhost:8501/_stcore/health", timeout=1)
        if r.status_code == 200:
            streamlit_running = True
    except Exception:
        pass

    if not streamlit_running:
        cmd_st = [
            pythonw,
            "-m", "streamlit", "run", "app.py",
            "--server.headless=true",
            "--server.port=8501"
        ]
        subprocess.Popen(cmd_st, cwd=stock_dir, creationflags=creationflags)

    # 2. 啟動 Cloudflare 遠端安全通道
    cmd_tunnel = [pythonw, "tunnel_manager.py"]
    subprocess.Popen(cmd_tunnel, cwd=stock_dir, creationflags=creationflags)

if __name__ == "__main__":
    main()
