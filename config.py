import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# 基本路徑設置
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "stock.db"

# 預設自選股
DEFAULT_WATCHLIST = [
    ("2330", "台積電"),
    ("2317", "鴻海"),
    ("2454", "聯發科"),
    ("2382", "廣達"),
    ("2603", "長榮"),
    ("3231", "緯創"),
    ("2308", "台達電"),
]

# 技術指標週期設定
MA_PERIODS = [5, 20, 60]
KD_PERIOD = 9
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# 策略篩選門檻設定
MOMENTUM_SETTINGS = {
    "min_volume": 800,           # 最小日成交量(張)，濾除低流動性股
    "vol_surge_ratio": 1.5,       # 量能激增比率 (今日量 / 5日均量 >= 1.5)
    "min_bias": 0.0,             # 月線乖離率下限
    "max_bias": 12.0,            # 月線乖離率上限 (避免過熱)
    "min_kd_k": 40.0,            # 9日 K 值下限
}

VALUE_PULLBACK_SETTINGS = {
    "min_volume": 300,           # 最小日成交量(張)
    "max_pe": 16.0,              # 本益比上限
    "min_yield": 4.0,            # 殖利率下限 (%)
    "bias_lower": -3.5,          # 回測月線下緣 (-3.5%)
    "bias_upper": 3.0,           # 回測月線上緣 (+3.0%)
}

def get_secret(key: str, default: str = "") -> str:
    try:
        import streamlit as st
        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)

# 系統安全通行密碼
APP_PASSWORD = get_secret("APP_PASSWORD", "bch1373")

# Gemini API 配置
GEMINI_API_KEY = get_secret("GEMINI_API_KEY", "")
GEMINI_MODEL = get_secret("GEMINI_MODEL", "gemini-3.5-flash-lite")

