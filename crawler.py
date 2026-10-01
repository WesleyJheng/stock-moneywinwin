import requests
import datetime
import re
import logging
from typing import Dict, List, Any, Optional
import pandas as pd
import yfinance as yf
from database import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01"
}

def clean_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    s = str(val).replace(",", "").replace("+", "").strip()
    if s in ["", "--", "N/A", "None", "X0.00"]:
        return None
    try:
        return float(s)
    except ValueError:
        return None

def roc_to_ad_date(roc_str: str) -> str:
    """民國日期轉西元 YYYY-MM-DD (例如 1150910 或 115/09/10 -> 2026-09-10)"""
    digits = re.findall(r"\d+", roc_str)
    if len(digits) == 3:
        y, m, d = int(digits[0]), int(digits[1]), int(digits[2])
    elif len(digits) == 1 and len(digits[0]) >= 6:
        s = digits[0]
        y = int(s[:-4])
        m = int(s[-4:-2])
        d = int(s[-2:])
    else:
        return datetime.date.today().strftime("%Y-%m-%d")
    ad_year = y + 1911
    return f"{ad_year:04d}-{m:02d}-{d:02d}"

def ad_to_roc_date(ad_date_str: str, delimiter: str = "/") -> str:
    """西元 YYYY-MM-DD 轉民國格式 (例如 2026-09-10 -> 115/09/10)"""
    parts = ad_date_str.split("-")
    if len(parts) == 3:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        roc_y = y - 1911
        return f"{roc_y}{delimiter}{m:02d}{delimiter}{d:02d}"
    return ""

def fetch_twse_quotes() -> (str, List[Dict[str, Any]]):
    """抓取 TWSE 上市全股票日收盤行情 (優先使用 RWD 即時 API 取得當日最新數據，次選 OpenAPI)"""
    url_rwd = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999"
    logging.info("正在請求 TWSE 上市日收盤行情 (RWD API)...")
    try:
        resp = requests.get(url_rwd, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            raw_date = data.get("date", "")
            if raw_date:
                actual_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date) == 8 else roc_to_ad_date(raw_date)
                tables = data.get("tables", [])
                target_rows = []
                for t in tables:
                    title = t.get("title", "") or ""
                    if "每日收盤行情" in title or len(t.get("data", [])) > 500:
                        target_rows = t.get("data", [])
                        break
                if target_rows:
                    results = []
                    for r in target_rows:
                        if len(r) < 11:
                            continue
                        code = r[0].strip()
                        if not (len(code) == 4 and code.isdigit()):
                            continue
                        close_p = clean_float(r[8])
                        if close_p is None:
                            continue
                        open_p = clean_float(r[5]) or close_p
                        high_p = clean_float(r[6]) or close_p
                        low_p = clean_float(r[7]) or close_p
                        chg_val = clean_float(r[10]) or 0.0
                        dir_str = str(r[9])
                        change = -abs(chg_val) if ("-" in dir_str or "green" in dir_str) else abs(chg_val)
                        vol_shares = clean_float(r[2]) or 0.0
                        volume_lots = round(vol_shares / 1000.0, 2)
                        amount = clean_float(r[4]) or 0.0
                        prev_close = close_p - change if close_p else 0
                        change_pct = round((change / prev_close * 100), 2) if prev_close else 0.0
                        results.append({
                            "symbol": code,
                            "name": r[1].strip(),
                            "market": "上市",
                            "date": actual_date,
                            "open": open_p,
                            "high": high_p,
                            "low": low_p,
                            "close": close_p,
                            "change": change,
                            "change_pct": change_pct,
                            "volume": volume_lots,
                            "amount": amount
                        })
                    if results:
                        logging.info(f"TWSE 上市行情抓取成功 (RWD): {len(results)} 檔 (日期: {actual_date})")
                        return actual_date, results
    except Exception as e:
        logging.warning(f"RWD TWSE 抓取失敗 ({e})，切換至 OpenAPI 備用...")

    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    logging.info("正在請求 TWSE 上市日收盤行情 (OpenAPI)...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                return "", []
            actual_date = roc_to_ad_date(data[0].get("Date", ""))
            results = []
            for item in data:
                code = item.get("Code", "").strip()
                # 僅篩選 4 碼純數字個股
                if not (len(code) == 4 and code.isdigit()):
                    continue
                close_p = clean_float(item.get("ClosingPrice"))
                if close_p is None:
                    continue
                open_p = clean_float(item.get("OpeningPrice")) or close_p
                high_p = clean_float(item.get("HighestPrice")) or close_p
                low_p = clean_float(item.get("LowestPrice")) or close_p
                change = clean_float(item.get("Change")) or 0.0
                vol_shares = clean_float(item.get("TradeVolume")) or 0.0
                volume_lots = round(vol_shares / 1000.0, 2)
                amount = clean_float(item.get("TradeValue")) or 0.0

                prev_close = close_p - change if close_p else 0
                change_pct = round((change / prev_close * 100), 2) if prev_close else 0.0

                results.append({
                    "symbol": code,
                    "name": item.get("Name", "").strip(),
                    "market": "上市",
                    "date": actual_date,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": volume_lots,
                    "amount": amount
                })
            logging.info(f"TWSE 上市行情抓取完成 (OpenAPI): {len(results)} 檔 (日期: {actual_date})")
            return actual_date, results
    except Exception as e:
        logging.error(f"抓取 TWSE 上市行情失敗: {e}")
    return "", []

def fetch_twse_pe_yield() -> Dict[str, Dict[str, float]]:
    """抓取 TWSE 上市全股票本益比、殖利率、淨值比"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
    pe_dict = {}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = item.get("Code", "").strip()
                pe = clean_float(item.get("PEratio"))
                dividend = clean_float(item.get("DividendYield"))
                pb = clean_float(item.get("PBratio"))
                pe_dict[code] = {"pe": pe, "yield_pct": dividend, "pb": pb}
    except Exception as e:
        logging.error(f"抓取 TWSE 本益比失敗: {e}")
    return pe_dict

def fetch_twse_institutional(target_date: str) -> Dict[str, Dict[str, float]]:
    """抓取 TWSE 上市三大法人買賣超 (T86)"""
    date_compact = target_date.replace("-", "")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_compact}&selectType=ALLBUT0999&response=json"
    inst_dict = {}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("data", [])
            for r in rows:
                if len(r) > 18:
                    code = r[0].strip()
                    # r[4]: 外資買賣超股數, r[10]: 投信買賣超, r[11]: 自營商買賣超, r[18]: 合計
                    foreign = (clean_float(r[4]) or 0.0) / 1000.0
                    trust = (clean_float(r[10]) or 0.0) / 1000.0
                    dealer = (clean_float(r[11]) or 0.0) / 1000.0
                    total = (clean_float(r[18]) or 0.0) / 1000.0
                    inst_dict[code] = {
                        "foreign_buy": round(foreign, 1),
                        "trust_buy": round(trust, 1),
                        "dealer_buy": round(dealer, 1),
                        "total_buy": round(total, 1),
                    }
    except Exception as e:
        logging.error(f"抓取 TWSE 三大法人失敗: {e}")
    return inst_dict

def fetch_tpex_quotes() -> (str, List[Dict[str, Any]]):
    """抓取 TPEX 上櫃全股票日收盤行情"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    logging.info("正在請求 TPEX 上櫃日收盤行情...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if not data:
                return "", []
            actual_date = roc_to_ad_date(data[0].get("Date", ""))
            results = []
            for item in data:
                code = item.get("SecuritiesCompanyCode", "").strip()
                # 僅篩選 4 碼純數字個股
                if not (len(code) == 4 and code.isdigit()):
                    continue
                close_p = clean_float(item.get("Close"))
                if close_p is None:
                    continue
                open_p = clean_float(item.get("Open")) or close_p
                high_p = clean_float(item.get("High")) or close_p
                low_p = clean_float(item.get("Low")) or close_p
                change = clean_float(item.get("Change")) or 0.0
                vol_shares = clean_float(item.get("TradingShares")) or 0.0
                volume_lots = round(vol_shares / 1000.0, 2)
                amount = clean_float(item.get("TransactionAmount")) or 0.0

                prev_close = close_p - change if close_p else 0
                change_pct = round((change / prev_close * 100), 2) if prev_close else 0.0

                results.append({
                    "symbol": code,
                    "name": item.get("CompanyName", "").strip(),
                    "market": "上櫃",
                    "date": actual_date,
                    "open": open_p,
                    "high": high_p,
                    "low": low_p,
                    "close": close_p,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": volume_lots,
                    "amount": amount
                })
            logging.info(f"TPEX 上櫃行情抓取完成: {len(results)} 檔 (日期: {actual_date})")
            return actual_date, results
    except Exception as e:
        logging.error(f"抓取 TPEX 上櫃行情失敗: {e}")
    return "", []

def fetch_tpex_pe_yield() -> Dict[str, Dict[str, float]]:
    """抓取 TPEX 上櫃全股票本益比、殖利率"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
    pe_dict = {}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = item.get("SecuritiesCompanyCode", "").strip()
                pe = clean_float(item.get("PriceEarningRatio"))
                dividend = clean_float(item.get("YieldRatio"))
                pb = clean_float(item.get("PriceBookRatio"))
                pe_dict[code] = {"pe": pe, "yield_pct": dividend, "pb": pb}
    except Exception as e:
        logging.error(f"抓取 TPEX 本益比失敗: {e}")
    return pe_dict

def fetch_tpex_institutional(target_date: str) -> Dict[str, Dict[str, float]]:
    """抓取 TPEX 上櫃三大法人買賣超"""
    roc_date = ad_to_roc_date(target_date)
    url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&d={roc_date}&se=EW&t=D"
    inst_dict = {}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            tables = data.get("tables", [])
            if tables:
                rows = tables[0].get("data", [])
                for r in rows:
                    if len(r) > 23:
                        code = r[0].strip()
                        foreign = (clean_float(r[10]) or 0.0) / 1000.0
                        trust = (clean_float(r[13]) or 0.0) / 1000.0
                        dealer = (clean_float(r[22]) or 0.0) / 1000.0
                        total = (clean_float(r[23]) or 0.0) / 1000.0
                        inst_dict[code] = {
                            "foreign_buy": round(foreign, 1),
                            "trust_buy": round(trust, 1),
                            "dealer_buy": round(dealer, 1),
                            "total_buy": round(total, 1),
                        }
    except Exception as e:
        logging.error(f"抓取 TPEX 三大法人失敗: {e}")
    return inst_dict

def run_daily_crawler(target_date: Optional[str] = None) -> int:
    """執行台股全市場 (上市 1000+ 與 上櫃 800+) 每日行情、籌碼、估值之採集與入庫"""
    logging.info("開始執行全市場每日數據採集 (TWSE + TPEX)...")

    # 1. 抓取上市與上櫃行情
    tw_date, twse_quotes = fetch_twse_quotes()
    tp_date, tpex_quotes = fetch_tpex_quotes()
    all_quotes = twse_quotes + tpex_quotes

    if not all_quotes:
        logging.warning("今日無可用之市場報價。")
        return 0

    quote_date = tw_date or tp_date or datetime.date.today().strftime("%Y-%m-%d")

    # 2. 抓取估價資訊 (PE, PB, Yield)
    twse_pe = fetch_twse_pe_yield()
    tpex_pe = fetch_tpex_pe_yield()
    all_pe = {**twse_pe, **tpex_pe}

    # 3. 抓取三大法人買賣超
    twse_inst = fetch_twse_institutional(quote_date)
    tpex_inst = fetch_tpex_institutional(quote_date)
    all_inst = {**twse_inst, **tpex_inst}

    conn = get_connection()
    cursor = conn.cursor()

    saved_count = 0
    for q in all_quotes:
        sym = q["symbol"]
        pe_info = all_pe.get(sym, {})
        inst_info = all_inst.get(sym, {})

        pe = pe_info.get("pe")
        pb = pe_info.get("pb")
        yield_pct = pe_info.get("yield_pct")

        # 更新基本檔
        cursor.execute("""
            INSERT INTO stocks (symbol, name, market) VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, market=excluded.market
        """, (sym, q["name"], q["market"]))

        # 寫入行情檔
        cursor.execute("""
            INSERT INTO daily_quotes (
                symbol, date, open, high, low, close, change, change_pct, volume, amount, pe, pb, yield_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                change=excluded.change, change_pct=excluded.change_pct, volume=excluded.volume,
                amount=excluded.amount, pe=excluded.pe, pb=excluded.pb, yield_pct=excluded.yield_pct
        """, (
            sym, quote_date, q["open"], q["high"], q["low"], q["close"],
            q["change"], q["change_pct"], q["volume"], q["amount"], pe, pb, yield_pct
        ))

        # 寫入三大法人
        if inst_info:
            cursor.execute("""
                INSERT INTO institutional (symbol, date, foreign_buy, trust_buy, dealer_buy, total_buy)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    foreign_buy=excluded.foreign_buy, trust_buy=excluded.trust_buy,
                    dealer_buy=excluded.dealer_buy, total_buy=excluded.total_buy
            """, (
                sym, quote_date,
                inst_info.get("foreign_buy", 0),
                inst_info.get("trust_buy", 0),
                inst_info.get("dealer_buy", 0),
                inst_info.get("total_buy", 0)
            ))

        saved_count += 1

    conn.commit()
    conn.close()
    logging.info(f"採集完成！已成功儲存 {saved_count} 檔台股個股之完整日行情 (日期: {quote_date})")
    return saved_count

def sync_history_for_symbols(symbols: List[str], period: str = "3mo") -> int:
    """利用 yfinance 快速回補個股歷史數據 (供計算 MA60, KD, MACD)"""
    if not symbols:
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    total_saved = 0

    batch_size = 15
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        ticker_map = {}
        yf_tickers = []
        for s in batch:
            cursor.execute("SELECT market, name FROM stocks WHERE symbol = ?", (s,))
            row = cursor.fetchone()
            market = row["market"] if row else "上市"
            suffix = ".TWO" if market == "上櫃" else ".TW"
            yf_sym = f"{s}{suffix}"
            ticker_map[yf_sym] = (s, row["name"] if row else s, market)
            yf_tickers.append(yf_sym)

        try:
            logging.info(f"正在透過 yfinance 回補歷史 K 線: {batch}...")
            df = yf.download(yf_tickers, period=period, interval="1d", group_by="ticker", progress=False, auto_adjust=False)
            
            for yf_sym, (sym, name, market) in ticker_map.items():
                try:
                    if yf_sym in df:
                        data = df[yf_sym]
                    elif isinstance(df.columns, pd.MultiIndex) and yf_sym in df.columns.levels[0]:
                        data = df[yf_sym]
                    else:
                        data = df

                    data = data.dropna(subset=["Close"])
                    for idx, row in data.iterrows():
                        d_str = idx.strftime("%Y-%m-%d")
                        o = round(float(row["Open"]), 2)
                        h = round(float(row["High"]), 2)
                        l = round(float(row["Low"]), 2)
                        c = round(float(row["Close"]), 2)
                        vol_lots = round(float(row["Volume"]) / 1000.0, 2)
                        
                        cursor.execute("""
                            INSERT INTO daily_quotes (symbol, date, open, high, low, close, volume)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(symbol, date) DO UPDATE SET
                                open=excluded.open, high=excluded.high, low=excluded.low,
                                close=excluded.close, volume=excluded.volume
                        """, (sym, d_str, o, h, l, c, vol_lots))
                        total_saved += 1
                except Exception as ex:
                    logging.warning(f"處理 {sym} 歷史數據異常: {ex}")
            conn.commit()
        except Exception as e:
            logging.error(f"批次回補失敗: {e}")

    conn.close()
    return total_saved
