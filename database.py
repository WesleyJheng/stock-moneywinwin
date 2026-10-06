import sqlite3
from typing import List, Dict, Any, Optional
import pandas as pd
from config import DB_PATH, DEFAULT_WATCHLIST

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """初始化 SQLite 資料表結構與預設自選股"""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 股票基本資料表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            market TEXT NOT NULL,
            industry TEXT DEFAULT ''
        )
    """)

    # 2. 每日行情表 (開高低收量與估值)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_quotes (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            change REAL,
            change_pct REAL,
            volume REAL,
            amount REAL,
            pe REAL,
            pb REAL,
            yield_pct REAL,
            PRIMARY KEY (symbol, date)
        )
    """)

    # 3. 三大法人買賣超表 (張數)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS institutional (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            foreign_buy REAL DEFAULT 0,
            trust_buy REAL DEFAULT 0,
            dealer_buy REAL DEFAULT 0,
            total_buy REAL DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # 4. 技術與量能指標表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            ma5 REAL,
            ma20 REAL,
            ma60 REAL,
            vol5 REAL,
            vol20 REAL,
            vol_surge REAL,
            bias20 REAL,
            k9 REAL,
            d9 REAL,
            macd REAL,
            macd_signal REAL,
            macd_hist REAL,
            is_week_high INTEGER DEFAULT 0,
            is_month_high INTEGER DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # 5. 多用戶自選股表 (支援 WEI 與 YUN 獨立管理)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_watchlist (
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, symbol)
        )
    """)

    # 6. 每日策略推薦結果表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            date TEXT NOT NULL,
            strategy TEXT NOT NULL,
            rank INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            score REAL,
            close REAL,
            change_pct REAL,
            entry_price REAL,
            stop_loss REAL,
            target_price REAL,
            reasons TEXT,
            PRIMARY KEY (date, strategy, symbol)
        )
    """)

    # 7. 系統設定與初始化標記表 (防止已刪除的自選股在 rerun 時被重複寫回)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    cursor.execute("SELECT value FROM system_meta WHERE key = 'watchlist_initialized'")
    meta_row = cursor.fetchone()
    if not meta_row:
        # 僅在初次安裝/初始化時執行一次預設清單寫入與舊表遷移
        try:
            cursor.execute("SELECT symbol FROM watchlist")
            old_rows = cursor.fetchall()
            for r in old_rows:
                cursor.execute("INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES ('WEI', ?)", (r[0],))
        except Exception:
            pass

        wei_defaults = [("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"), ("1504", "東元")]
        yun_defaults = [("2382", "廣達"), ("2603", "長榮"), ("3231", "緯創"), ("2308", "台達電")]
        for s, n in wei_defaults:
            cursor.execute("INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES ('WEI', ?)", (s,))
            cursor.execute("INSERT OR IGNORE INTO stocks (symbol, name, market) VALUES (?, ?, '上市')", (s, n))
        for s, n in yun_defaults:
            cursor.execute("INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES ('YUN', ?)", (s,))
            cursor.execute("INSERT OR IGNORE INTO stocks (symbol, name, market) VALUES (?, ?, '上市')", (s, n))

        cursor.execute("INSERT OR REPLACE INTO system_meta (key, value) VALUES ('watchlist_initialized', '1')")

    # 清理舊的單一用戶 watchlist 廢棄資料表，避免殘留數據干擾
    cursor.execute("DROP TABLE IF EXISTS watchlist")

    conn.commit()
    conn.close()

    # 從獨立 watchlists.json 載入最新自選股清單（確保雲端重啟或排程更新時不會覆蓋用戶修改）
    load_watchlists_from_json()

import json
import base64
import requests
from config import BASE_DIR

WATCHLISTS_JSON_PATH = BASE_DIR / "watchlists.json"

def sync_watchlists_to_github():
    """若設定了 GITHUB_TOKEN，將 watchlists.json 即時同步至 GitHub 倉庫"""
    try:
        from config import get_secret
        token = get_secret("GITHUB_TOKEN", "")
        repo = get_secret("GITHUB_REPO", "WesleyJheng/stock-moneywinwin")
        if not token or not repo:
            return

        if not WATCHLISTS_JSON_PATH.exists():
            return

        with open(WATCHLISTS_JSON_PATH, "r", encoding="utf-8") as f:
            content_str = f.read()

        b64_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        url = f"https://api.github.com/repos/{repo}/contents/watchlists.json"

        resp = requests.get(url, headers=headers, timeout=5)
        sha = None
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": "Sync watchlists.json from Streamlit Cloud [skip ci]",
            "content": b64_content,
            "branch": "main"
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload, timeout=10)
    except Exception:
        pass

def load_watchlists_from_json():
    """從 watchlists.json 載入最新自選股並同步至 SQLite user_watchlist 表"""
    if not WATCHLISTS_JSON_PATH.exists():
        return
    try:
        with open(WATCHLISTS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_watchlist")
        for user_id, symbols in data.items():
            for sym in symbols:
                cursor.execute("INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES (?, ?)", (user_id, sym))
        conn.commit()
        conn.close()
    except Exception:
        pass

def save_watchlists_to_json():
    """把目前 SQLite 裡的 user_watchlist 轉存至 watchlists.json 並推送到 GitHub"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, symbol FROM user_watchlist ORDER BY added_at ASC")
        rows = cursor.fetchall()
        conn.close()

        data = {}
        for r in rows:
            uid, sym = r["user_id"], r["symbol"]
            if uid not in data:
                data[uid] = []
            if sym not in data[uid]:
                data[uid].append(sym)

        with open(WATCHLISTS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        sync_watchlists_to_github()
    except Exception:
        pass

def get_watchlist(user_id: str = "WEI") -> List[Dict[str, Any]]:
    """讀取指定用戶 (WEI 或 YUN) 的自選股清單"""
    conn = get_connection()
    query = """
        SELECT w.symbol, COALESCE(s.name, w.symbol) as name, s.market, s.industry
        FROM user_watchlist w
        LEFT JOIN stocks s ON w.symbol = s.symbol
        WHERE w.user_id = ?
        ORDER BY w.added_at ASC
    """
    df = pd.read_sql_query(query, conn, params=(user_id,))
    conn.close()
    return df.to_dict(orient="records")

def get_all_watchlist_symbols() -> List[str]:
    """取得所有用戶關注的所有股票代號不重複清單 (供排程同步使用)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM user_watchlist")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def add_to_watchlist(symbol: str, name: Optional[str] = None, user_id: str = "WEI"):
    """將股票新增至指定用戶 (WEI 或 YUN) 的自選股"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO user_watchlist (user_id, symbol) VALUES (?, ?)", (user_id, symbol))
    if name:
        cursor.execute("""
            INSERT INTO stocks (symbol, name, market) VALUES (?, ?, '上市')
            ON CONFLICT(symbol) DO UPDATE SET name=excluded.name
        """, (symbol, name))
    conn.commit()
    conn.close()
    save_watchlists_to_json()

def remove_from_watchlist(symbol: str, user_id: str = "WEI"):
    """自指定用戶 (WEI 或 YUN) 的自選股中移除股票"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?", (user_id, symbol))
    conn.commit()
    conn.close()
    save_watchlists_to_json()

def get_latest_quote_date() -> Optional[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date FROM daily_quotes
        GROUP BY date
        HAVING COUNT(*) >= 50
        ORDER BY date DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if not row:
        cursor.execute("SELECT MAX(date) as max_date FROM daily_quotes")
        row = cursor.fetchone()
        conn.close()
        return row["max_date"] if row and row["max_date"] else None
    conn.close()
    return row["date"]

def get_stock_detail(symbol: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    query = """
        SELECT q.symbol, s.name, q.date, q.open, q.high, q.low, q.close,
               COALESCE(q.change, (q.close - prev.close)) as change,
               COALESCE(q.change_pct, ROUND((q.close - prev.close)/prev.close * 100, 2)) as change_pct,
               q.volume,
               COALESCE(q.pe, (SELECT pe FROM daily_quotes WHERE symbol = q.symbol AND pe IS NOT NULL ORDER BY date DESC LIMIT 1)) as pe,
               COALESCE(q.pb, (SELECT pb FROM daily_quotes WHERE symbol = q.symbol AND pb IS NOT NULL ORDER BY date DESC LIMIT 1)) as pb,
               COALESCE(q.yield_pct, (SELECT yield_pct FROM daily_quotes WHERE symbol = q.symbol AND yield_pct IS NOT NULL ORDER BY date DESC LIMIT 1)) as yield_pct,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.trust_buy, 0) as trust_buy,
               COALESCE(i.dealer_buy, 0) as dealer_buy,
               COALESCE(i.total_buy, 0) as total_buy,
               ind.ma5, ind.ma20, ind.ma60, ind.vol5, ind.vol_surge, ind.bias20,
               ind.k9, ind.d9, ind.macd, ind.macd_signal, ind.macd_hist,
               ind.is_week_high, ind.is_month_high
        FROM daily_quotes q
        LEFT JOIN stocks s ON q.symbol = s.symbol
        LEFT JOIN daily_quotes prev ON q.symbol = prev.symbol AND prev.date = (
            SELECT MAX(date) FROM daily_quotes WHERE symbol = q.symbol AND date < q.date
        )
        LEFT JOIN institutional i ON q.symbol = i.symbol AND i.date = (
            SELECT MAX(date) FROM institutional WHERE symbol = q.symbol
        )
        LEFT JOIN indicators ind ON q.symbol = ind.symbol AND q.date = ind.date
        WHERE q.symbol = ?
        ORDER BY q.date DESC
        LIMIT 1
    """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    conn.close()
    if df.empty:
        return None
    return df.iloc[0].to_dict()

def get_stock_history(symbol: str, limit: int = 120) -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT q.date, q.open, q.high, q.low, q.close, q.volume, q.change_pct,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.trust_buy, 0) as trust_buy,
               COALESCE(i.dealer_buy, 0) as dealer_buy,
               COALESCE(i.total_buy, 0) as total_buy,
               ind.ma5, ind.ma20, ind.ma60, ind.bias20,
               ind.k9, ind.d9, ind.macd, ind.macd_signal, ind.macd_hist
        FROM daily_quotes q
        LEFT JOIN institutional i ON q.symbol = i.symbol AND q.date = i.date
        LEFT JOIN indicators ind ON q.symbol = ind.symbol AND q.date = ind.date
        WHERE q.symbol = ?
        ORDER BY q.date ASC
    """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    conn.close()
    if len(df) > limit:
        df = df.iloc[-limit:]
    return df
