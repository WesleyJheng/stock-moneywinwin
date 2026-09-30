import pandas as pd
import numpy as np
import datetime
import logging
from typing import Dict, List, Any, Optional
from database import get_connection, get_stock_history
from config import MOMENTUM_SETTINGS, VALUE_PULLBACK_SETTINGS

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算單檔個股各項技術指標 (MA5, MA20, MA60, KD, MACD, BIAS, 量增, 創高)"""
    if df.empty or len(df) < 5:
        return df

    df = df.copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["open"] = pd.to_numeric(df["open"], errors="coerce")
    df["high"] = pd.to_numeric(df["high"], errors="coerce")
    df["low"] = pd.to_numeric(df["low"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    # 1. 均線 (MA5, MA20, MA60)
    df["ma5"] = df["close"].rolling(window=5).mean().round(2)
    df["ma20"] = df["close"].rolling(window=20).mean().round(2)
    df["ma60"] = df["close"].rolling(window=60).mean().round(2)

    # 2. 成交量均線與爆量倍數
    df["vol5"] = df["volume"].rolling(window=5).mean().round(1)
    df["vol20"] = df["volume"].rolling(window=20).mean().round(1)
    # 今日量 vs 5日均量 (避免除以零)
    df["vol_surge"] = (df["volume"] / df["vol5"].shift(1).replace(0, np.nan)).round(2)

    # 3. 月線乖離率 BIAS20 (%) = (Close - MA20) / MA20 * 100
    df["bias20"] = ((df["close"] - df["ma20"]) / df["ma20"] * 100).round(2)

    # 4. 創單周(5日)新高 / 單月(20日)新高
    rolling_high_5 = df["high"].rolling(window=5).max()
    rolling_high_20 = df["high"].rolling(window=20).max()
    df["is_week_high"] = (df["high"] >= rolling_high_5).astype(int)
    df["is_month_high"] = (df["high"] >= rolling_high_20).astype(int)

    # 5. 9日 KD 指標計算
    low_9 = df["low"].rolling(window=9).min()
    high_9 = df["high"].rolling(window=9).max()
    # RSV = (Close - 9日最低) / (9日最高 - 9日最低) * 100
    denom = (high_9 - low_9).replace(0, np.nan)
    rsv = ((df["close"] - low_9) / denom * 100).fillna(50)

    # 平滑計算 K, D
    k_vals = []
    d_vals = []
    k_curr = 50.0
    d_curr = 50.0
    for r in rsv:
        if np.isnan(r):
            k_vals.append(np.nan)
            d_vals.append(np.nan)
        else:
            k_curr = (2.0 / 3.0) * k_curr + (1.0 / 3.0) * r
            d_curr = (2.0 / 3.0) * d_curr + (1.0 / 3.0) * k_curr
            k_vals.append(round(k_curr, 2))
            d_vals.append(round(d_curr, 2))
    df["k9"] = k_vals
    df["d9"] = d_vals

    # 6. MACD (EMA12, EMA26, DIF, MACD9, OSC)
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = (dif - dea) * 2  # 柱狀體 OSC

    df["macd"] = dif.round(2)
    df["macd_signal"] = dea.round(2)
    df["macd_hist"] = macd_hist.round(2)

    return df

def update_indicators_for_stock(symbol: str):
    """計算並寫入單檔股票指標至 SQLite"""
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, volume FROM daily_quotes WHERE symbol = ? ORDER BY date ASC",
        conn, params=(symbol,)
    )
    if df.empty or len(df) < 5:
        conn.close()
        return

    df_ind = calculate_technical_indicators(df)
    cursor = conn.cursor()
    for _, row in df_ind.iterrows():
        cursor.execute("""
            INSERT INTO indicators (
                symbol, date, ma5, ma20, ma60, vol5, vol20, vol_surge, bias20,
                k9, d9, macd, macd_signal, macd_hist, is_week_high, is_month_high
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                ma5=excluded.ma5, ma20=excluded.ma20, ma60=excluded.ma60,
                vol5=excluded.vol5, vol20=excluded.vol20, vol_surge=excluded.vol_surge,
                bias20=excluded.bias20, k9=excluded.k9, d9=excluded.d9,
                macd=excluded.macd, macd_signal=excluded.macd_signal, macd_hist=excluded.macd_hist,
                is_week_high=excluded.is_week_high, is_month_high=excluded.is_month_high
        """, (
            symbol, row["date"], row["ma5"], row["ma20"], row["ma60"],
            row["vol5"], row["vol20"], row["vol_surge"], row["bias20"],
            row["k9"], row["d9"], row["macd"], row["macd_signal"], row["macd_hist"],
            int(row["is_week_high"]), int(row["is_month_high"])
        ))
    conn.commit()
    conn.close()

def batch_update_all_indicators():
    """對資料庫中所有有報價的股票計算指標"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT symbol FROM daily_quotes")
    symbols = [row[0] for row in cursor.fetchall()]
    conn.close()

    logging.info(f"開始批次計算 {len(symbols)} 檔股票之技術指標...")
    for sym in symbols:
        try:
            update_indicators_for_stock(sym)
        except Exception as e:
            logging.warning(f"計算 {sym} 指標失敗: {e}")
    logging.info("全市場技術指標計算完成")

def run_recommendation_strategies(target_date: Optional[str] = None):
    """執行波段動能榜與價值防守榜之多因子評分並儲存"""
    conn = get_connection()
    if not target_date:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM daily_quotes")
        row = cursor.fetchone()
        target_date = row[0] if row else None

    if not target_date:
        conn.close()
        return

    logging.info(f"開始執行策略選股多因子評分 (基準日期: {target_date})...")

    query = """
        SELECT q.symbol, s.name, q.date, q.open, q.high, q.low, q.close, q.change_pct, q.volume,
               q.pe, q.pb, q.yield_pct,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.trust_buy, 0) as trust_buy,
               COALESCE(i.dealer_buy, 0) as dealer_buy,
               COALESCE(i.total_buy, 0) as total_buy,
               ind.ma5, ind.ma20, ind.ma60, ind.vol5, ind.vol_surge, ind.bias20,
               ind.k9, ind.d9, ind.macd, ind.macd_signal, ind.macd_hist,
               ind.is_week_high, ind.is_month_high
        FROM daily_quotes q
        JOIN stocks s ON q.symbol = s.symbol
        LEFT JOIN institutional i ON q.symbol = i.symbol AND q.date = i.date
        LEFT JOIN indicators ind ON q.symbol = ind.symbol AND q.date = ind.date
        WHERE q.date = ?
    """
    df = pd.read_sql_query(query, conn, params=(target_date,))
    if df.empty:
        conn.close()
        return

    # 濾除 ETF、認購權證代號（通常 > 5 位數或以 00 開頭）
    df = df[df["symbol"].str.match(r"^[1-9]\d{3}$")].copy()

    # --- 1. 波段動能榜 (Momentum Strategy) ---
    # 條件：成交量 > 800張，量增 >= 1.5，乖離率 0%~12%，均線站上月線或季線，KD向上
    m_candidates = []
    for _, row in df.iterrows():
        reasons = []
        score = 0.0
        vol = row["volume"] or 0
        surge = row["vol_surge"] or 1.0
        bias = row["bias20"] or 0.0
        close_p = row["close"] or 0.0
        ma5 = row["ma5"] or close_p
        ma20 = row["ma20"] or close_p
        ma60 = row["ma60"] or close_p
        k9 = row["k9"] or 50.0
        d9 = row["d9"] or 50.0
        hist = row["macd_hist"] or 0.0
        inst_buy = row["total_buy"] or 0.0
        trust_buy = row["trust_buy"] or 0.0
        foreign_buy = row["foreign_buy"] or 0.0

        if vol < MOMENTUM_SETTINGS["min_volume"]:
            continue
        if bias < MOMENTUM_SETTINGS["min_bias"] or bias > MOMENTUM_SETTINGS["max_bias"]:
            continue

        # 均線多頭或站上月線
        if close_p > ma20:
            score += 20
            reasons.append("站上月線")
        if close_p > ma5 and ma5 > ma20:
            score += 15
            reasons.append("均線多頭")

        # 量能激增
        if surge >= 1.5:
            score += min(surge * 10, 30)
            reasons.append(f"爆量{surge:.1f}倍")

        # 法人籌碼
        if trust_buy > 100:
            score += 20
            reasons.append(f"投信大買{int(trust_buy)}張")
        elif foreign_buy > 300:
            score += 15
            reasons.append(f"外資買超{int(foreign_buy)}張")
        elif inst_buy > 200:
            score += 10
            reasons.append(f"法人合計買超{int(inst_buy)}張")

        # 技術動能 (KD, MACD, 創高)
        if k9 > 50 and k9 > d9:
            score += 10
            reasons.append("KD多頭")
        if hist > 0:
            score += 10
            reasons.append("MACD翻紅")
        if row["is_month_high"] == 1:
            score += 15
            reasons.append("創月新高")
        elif row["is_week_high"] == 1:
            score += 8
            reasons.append("創周新高")

        if score >= 45:
            # 均線防呆
            supp_ma = ma20 if (ma20 and not np.isnan(ma20)) else close_p * 0.97
            entry_low = round(max(ma5 if (ma5 and not np.isnan(ma5)) else close_p * 0.98, close_p * 0.985), 2)
            entry_high = round(close_p, 2)
            stop_loss = round(min(supp_ma * 0.98, close_p * 0.93), 2)
            target = round(close_p * 1.10, 2)

            m_candidates.append({
                "date": target_date,
                "strategy": "momentum",
                "symbol": row["symbol"],
                "name": row["name"],
                "score": round(score, 1),
                "close": close_p,
                "change_pct": row["change_pct"],
                "entry_price": entry_low,
                "stop_loss": stop_loss,
                "target_price": target,
                "reasons": " | ".join(reasons)
            })

    # --- 2. 價值防守 / 回測支撐榜 (Value Pullback Strategy) ---
    v_candidates = []
    for _, row in df.iterrows():
        reasons = []
        score = 0.0
        vol = row["volume"] or 0
        pe = row["pe"]
        yield_p = row["yield_pct"]
        bias = row["bias20"] or 0.0
        close_p = row["close"] or 0.0
        ma20 = row["ma20"] or close_p
        ma60 = row["ma60"] or close_p
        k9 = row["k9"] or 50.0
        trust_buy = row["trust_buy"] or 0.0

        if vol < VALUE_PULLBACK_SETTINGS["min_volume"]:
            continue
        # 乖離率在月線附近 -3.5% ~ +3.0% (回測支撐)
        if bias < VALUE_PULLBACK_SETTINGS["bias_lower"] or bias > VALUE_PULLBACK_SETTINGS["bias_upper"]:
            continue

        # 估值優勢 (PE < 15 或 高殖利率)
        is_value = False
        if pe and 0 < pe <= 15:
            score += 25
            is_value = True
            reasons.append(f"低本益比{pe:.1f}倍")
        if yield_p and yield_p >= 4.5:
            score += 25
            is_value = True
            reasons.append(f"高殖利率{yield_p:.1f}%")

        if not is_value:
            continue

        # 支撐位階確認 (不跌破季線)
        if close_p >= ma60 * 0.98:
            score += 20
            reasons.append("回測季線支撐")
        if abs(bias) <= 1.5:
            score += 15
            reasons.append("貼緊月線支撐")

        # 籌碼防守
        if trust_buy > 30:
            score += 15
            reasons.append(f"投信逆勢低接{int(trust_buy)}張")

        # KD 低檔蓄勢
        if 20 <= k9 <= 50:
            score += 10
            reasons.append(f"KD低檔(K={int(k9)})")

        if score >= 45:
            supp_base = ma60 if (ma60 and not np.isnan(ma60)) else (ma20 if (ma20 and not np.isnan(ma20)) else close_p * 0.96)
            entry_low = round(min(close_p, supp_base), 2)
            entry_high = round(close_p, 2)
            stop_loss = round(min(supp_base * 0.96, close_p * 0.94), 2)
            target = round(close_p * 1.08, 2)

            v_candidates.append({
                "date": target_date,
                "strategy": "value_pullback",
                "symbol": row["symbol"],
                "name": row["name"],
                "score": round(score, 1),
                "close": close_p,
                "change_pct": row["change_pct"],
                "entry_price": entry_low,
                "stop_loss": stop_loss,
                "target_price": target,
                "reasons": " | ".join(reasons)
            })

    # 依分數排序並寫入資料庫
    cursor = conn.cursor()
    cursor.execute("DELETE FROM recommendations WHERE date = ?", (target_date,))

    # 存動能榜
    m_candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(m_candidates[:30], 1):
        cursor.execute("""
            INSERT INTO recommendations (
                date, strategy, rank, symbol, name, score, close, change_pct,
                entry_price, stop_loss, target_price, reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c["date"], c["strategy"], idx, c["symbol"], c["name"], c["score"],
            c["close"], c["change_pct"], c["entry_price"], c["stop_loss"], c["target_price"], c["reasons"]
        ))

    # 存價值防守榜
    v_candidates.sort(key=lambda x: x["score"], reverse=True)
    for idx, c in enumerate(v_candidates[:30], 1):
        cursor.execute("""
            INSERT INTO recommendations (
                date, strategy, rank, symbol, name, score, close, change_pct,
                entry_price, stop_loss, target_price, reasons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            c["date"], c["strategy"], idx, c["symbol"], c["name"], c["score"],
            c["close"], c["change_pct"], c["entry_price"], c["stop_loss"], c["target_price"], c["reasons"]
        ))

    conn.commit()
    conn.close()
    logging.info(f"策略評分完成：波段動能榜入選 {len(m_candidates)} 檔，價值防守榜入選 {len(v_candidates)} 檔")

def get_recommendations(strategy: str = "momentum", top_n: int = 10, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """讀取指定策略的 Top N 推薦清單"""
    conn = get_connection()
    if not target_date:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM recommendations WHERE strategy = ?", (strategy,))
        row = cursor.fetchone()
        target_date = row[0] if row else None

    if not target_date:
        conn.close()
        return []

    query = """
        SELECT r.rank, r.symbol, r.name, r.score, r.close, r.change_pct,
               r.entry_price, r.stop_loss, r.target_price, r.reasons,
               q.volume, q.pe, q.yield_pct,
               COALESCE(i.foreign_buy, 0) as foreign_buy,
               COALESCE(i.trust_buy, 0) as trust_buy
        FROM recommendations r
        LEFT JOIN daily_quotes q ON r.symbol = q.symbol AND r.date = q.date
        LEFT JOIN institutional i ON r.symbol = i.symbol AND r.date = i.date
        WHERE r.strategy = ? AND r.date = ?
        ORDER BY r.rank ASC
        LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=(strategy, target_date, top_n))
    conn.close()
    return df.to_dict(orient="records")

def analyze_stock_signals_and_prices(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    針對任一個股之最新行情、技術指標與籌碼，
    提供結構化技術指標訊號判讀與推薦進出場價格指引
    """
    if not detail or not detail.get("close"):
        return {
            "signals": ["數據不足"],
            "ma_status": "計算中",
            "kd_status": "計算中",
            "macd_status": "計算中",
            "bias_status": "計算中",
            "action_type": "觀望",
            "entry_low": None,
            "entry_high": None,
            "stop_loss": None,
            "target_price": None,
            "strategy_text": "暫無足夠指標數據供評估進出場價位。"
        }

    close_p = float(detail.get("close"))
    ma5 = float(detail["ma5"]) if detail.get("ma5") and not np.isnan(detail["ma5"]) else None
    ma20 = float(detail["ma20"]) if detail.get("ma20") and not np.isnan(detail["ma20"]) else None
    ma60 = float(detail["ma60"]) if detail.get("ma60") and not np.isnan(detail["ma60"]) else None
    
    vol = float(detail.get("volume")) if detail.get("volume") else 0.0
    vol_surge = float(detail.get("vol_surge")) if detail.get("vol_surge") and not np.isnan(detail["vol_surge"]) else 1.0
    bias = float(detail.get("bias20")) if detail.get("bias20") and not np.isnan(detail["bias20"]) else 0.0
    k9 = float(detail.get("k9")) if detail.get("k9") and not np.isnan(detail["k9"]) else 50.0
    d9 = float(detail.get("d9")) if detail.get("d9") and not np.isnan(detail["d9"]) else 50.0
    hist = float(detail.get("macd_hist")) if detail.get("macd_hist") and not np.isnan(detail["macd_hist"]) else 0.0
    
    signals = []

    # 1. 均線診斷
    if ma5 and ma20 and ma60:
        if close_p >= ma5 >= ma20 >= ma60:
            ma_status = "三線多頭排列 (強勢)"
            signals.append("三線多頭")
        elif close_p < ma5 < ma20 < ma60:
            ma_status = "三線空頭排列 (偏弱)"
            signals.append("三線空頭")
        elif close_p >= ma20:
            ma_status = "站上月線 (偏多整理)"
            signals.append("站上月線")
        else:
            ma_status = "跌破月線 (修正整理)"
            signals.append("跌破月線")
    elif ma20:
        ma_status = "站上月線" if close_p >= ma20 else "跌破月線"
    else:
        ma_status = "均線計算中"

    # 2. KD 動能診斷
    if k9 >= 80:
        kd_status = f"超買區高檔鈍化 (K={k9:.0f})"
        signals.append("KD高檔超買")
    elif k9 <= 20:
        kd_status = f"超賣區築底 (K={k9:.0f})"
        signals.append("KD低檔超賣")
    elif k9 > d9:
        kd_status = f"多頭向上 (K={k9:.0f} > D={d9:.0f})"
        signals.append("KD金叉/向上")
    else:
        kd_status = f"空頭回落 (K={k9:.0f} < D={d9:.0f})"
        signals.append("KD死叉/回落")

    # 3. MACD 趨勢
    if hist > 0:
        macd_status = f"柱狀體翻紅擴大 ({hist:+.2f})"
        signals.append("MACD翻紅")
    else:
        macd_status = f"柱狀體翻綠修正 ({hist:+.2f})"
        signals.append("MACD翻綠")

    # 4. 價量與乖離
    if vol_surge >= 1.5:
        signals.append(f"爆量{vol_surge:.1f}x")
    if detail.get("is_month_high"):
        signals.append("創月新高")
    elif detail.get("is_week_high"):
        signals.append("創周新高")

    if bias > 8.0:
        bias_status = f"正乖離偏大 ({bias:+.1f}%)"
        signals.append("乖離偏高")
    elif bias < -8.0:
        bias_status = f"負乖離超跌 ({bias:+.1f}%)"
        signals.append("負乖離過大")
    else:
        bias_status = f"正常區間 ({bias:+.1f}%)"

    # 5. 操作策略與進出場價格計算
    ref_ma20 = ma20 if ma20 else close_p * 0.98
    ref_ma5 = ma5 if ma5 else close_p * 0.99
    ref_ma60 = ma60 if ma60 else ref_ma20 * 0.97

    if close_p >= ref_ma20 and (ma5 is None or close_p >= ref_ma5 * 0.99):
        # 偏多趨勢格局
        if bias > 10.0:
            action_type = "偏多但過熱 (防追高)"
            entry_low = round(ref_ma5 * 0.99, 2)
            entry_high = round(close_p * 0.985, 2)
            stop_loss = round(ref_ma20 * 0.98, 2)
            target_price = round(close_p * 1.08, 2)
            strategy_text = f"目前多頭格局明確，但月線乖離達 {bias:.1f}% 偏高，切忌追高。建議耐心等待回測 5MA 附近 ({entry_low:.2f} ~ {entry_high:.2f} 元) 再行分批承接，跌破月線 ({stop_loss:.2f} 元) 停損出場，波段目標看 {target_price:.2f} 元。"
        else:
            action_type = "波段偏多 (順勢進場)"
            entry_low = round(min(ref_ma5, close_p * 0.99), 2)
            entry_high = round(close_p, 2)
            stop_loss = round(min(ref_ma20 * 0.98, close_p * 0.94), 2)
            target_price = round(close_p * 1.10, 2)
            strategy_text = f"均線多頭排列且動能健康，建議進場區間為現價至 5 日線 ({entry_low:.2f} ~ {entry_high:.2f} 元)，以月線下緣 {stop_loss:.2f} 元為防守停損點，波段目標價看 {target_price:.2f} 元 (預估空間 +10%)。"
    elif close_p >= ref_ma60 and abs(bias) <= 4.0:
        # 回測支撐格局
        action_type = "回測支撐 (逢低布局)"
        entry_low = round(ref_ma60, 2)
        entry_high = round(close_p, 2)
        stop_loss = round(ref_ma60 * 0.96, 2)
        target_price = round(close_p * 1.08, 2)
        strategy_text = f"股價回測關鍵支撐帶 ({ref_ma20:.2f} / {ref_ma60:.2f} 元)，下檔風險可控。可於 {entry_low:.2f} ~ {entry_high:.2f} 元逢低分批布局，跌破季線 ({stop_loss:.2f} 元) 嚴格停損，反彈目標看前高 {target_price:.2f} 元。"
    else:
        # 偏弱空頭格局
        action_type = "偏弱整理 (觀望防守)"
        entry_low = round(ref_ma60 * 0.95, 2)
        entry_high = round(ref_ma20, 2)
        stop_loss = round(close_p * 0.94, 2)
        target_price = round(ref_ma20, 2)
        strategy_text = f"股價處於月線下方，短線均線反壓較重。暫不建議盲目摸底進場；空手者建議觀望，待帶量長紅重回月線 ({ref_ma20:.2f} 元) 再考慮進場；若跌破 {stop_loss:.2f} 元需嚴防下行破底風險。"

    return {
        "signals": signals,
        "ma_status": ma_status,
        "kd_status": kd_status,
        "macd_status": macd_status,
        "bias_status": bias_status,
        "action_type": action_type,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "strategy_text": strategy_text
    }

