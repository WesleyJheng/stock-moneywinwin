import os
import json
import logging
from typing import Dict, Any, List, Optional
import pandas as pd
from config import GEMINI_API_KEY, GEMINI_MODEL
from database import get_connection, get_stock_detail, get_latest_quote_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def get_stock_summary(query: str) -> str:
    """查詢個股最新行情、三大法人籌碼、技術指標與估值資訊"""
    conn = get_connection()
    cursor = conn.cursor()
    # 支援代號或名稱搜尋
    cursor.execute("""
        SELECT symbol, name FROM stocks
        WHERE symbol = ? OR name LIKE ?
        LIMIT 1
    """, (query.strip(), f"%{query.strip()}%"))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return f"找不到股票代號或名稱包含 '{query}' 的標的。"

    sym = row["symbol"]
    name = row["name"]
    conn.close()

    detail = get_stock_detail(sym)
    if not detail:
        return f"找到股票 {sym} {name}，但資料庫中暫無最新行情數據，請先執行盤後數據更新。"

    # 估值狀態評斷
    pe = detail.get("pe")
    yield_p = detail.get("yield_pct")
    val_status = "合理"
    if pe:
        if pe < 12:
            val_status = "偏低/便宜 (低本益比)"
        elif pe > 25:
            val_status = "偏高/昂貴"

    # 籌碼面解讀
    f_buy = detail.get("foreign_buy", 0)
    t_buy = detail.get("trust_buy", 0)
    tot_buy = detail.get("total_buy", 0)

    # 均線狀態
    close_p = detail.get("close", 0)
    ma5 = detail.get("ma5")
    ma20 = detail.get("ma20")
    ma60 = detail.get("ma60")
    ma_status = []
    if ma5 and close_p > ma5:
        ma_status.append("站上5日線")
    elif ma5:
        ma_status.append("跌破5日線")
    if ma20 and close_p > ma20:
        ma_status.append("站上月線")
    elif ma20:
        ma_status.append("跌破月線")
    if ma60 and close_p > ma60:
        ma_status.append("站上季線")
    elif ma60:
        ma_status.append("跌破季線")

    result = {
        "基本資料": f"{sym} {name} (日期: {detail.get('date')})",
        "今日收盤": f"{detail.get('close')} 元 (漲跌: {detail.get('change')} / {detail.get('change_pct')}%)",
        "成交量": f"{detail.get('volume')} 張 (量增比: {detail.get('vol_surge')}倍)",
        "三大法人買賣超": {
            "外資": f"{f_buy} 張",
            "投信": f"{t_buy} 張",
            "自營商": f"{detail.get('dealer_buy', 0)} 張",
            "三大法人合計": f"{tot_buy} 張"
        },
        "技術位階": {
            "均線位置": ", ".join(ma_status) if ma_status else "均線計算中",
            "月線乖離率": f"{detail.get('bias20')}%",
            "KD指標": f"K={detail.get('k9')}, D={detail.get('d9')}",
            "MACD柱狀體": f"{detail.get('macd_hist')}",
            "創高狀態": "創月新高" if detail.get("is_month_high") else ("創周新高" if detail.get("is_week_high") else "無")
        },
        "估值水準": {
            "本益比": f"{pe} 倍" if pe else "N/A",
            "殖利率": f"{yield_p}%" if yield_p else "N/A",
            "評價區間": val_status
        }
    }
    return json.dumps(result, ensure_ascii=False, indent=2)

def get_top_picks(strategy: str = "momentum", top_n: int = 5) -> str:
    """查詢最新的 AI 推薦選股名單 (momentum: 波段動能, value_pullback: 價值防守)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM recommendations WHERE strategy = ?", (strategy,))
    row = cursor.fetchone()
    target_date = row[0] if row else None
    if not target_date:
        conn.close()
        return "資料庫中尚無推薦數據，請先於首頁執行資料掃描與策略計算。"

    cursor.execute("""
        SELECT rank, symbol, name, score, close, change_pct, entry_price, stop_loss, target_price, reasons
        FROM recommendations
        WHERE strategy = ? AND date = ?
        ORDER BY rank ASC
        LIMIT ?
    """, (strategy, target_date, top_n))
    rows = cursor.fetchall()
    conn.close()

    strat_name = "波段動能榜" if strategy == "momentum" else "價值防守/回測支撐榜"
    results = [f"【{strat_name} Top {len(rows)}】(評分基準日: {target_date})"]
    for r in rows:
        c_p = f"{float(r['close']):.2f}" if r.get('close') is not None else "--"
        e_p = f"{float(r['entry_price']):.2f}" if r.get('entry_price') is not None else "--"
        s_l = f"{float(r['stop_loss']):.2f}" if r.get('stop_loss') is not None else "--"
        t_p = f"{float(r['target_price']):.2f}" if r.get('target_price') is not None else "--"
        results.append(
            f"No.{r['rank']} {r['symbol']} {r['name']} | 現價: {c_p} ({r['change_pct']}%) | "
            f"評分: {r['score']} | 推薦理由: [{r['reasons']}] | "
            f"建議進場: {e_p} | 停損: {s_l} | 目標: {t_p}"
        )
    return "\n".join(results)

def get_market_overview() -> str:
    """取得台股大盤最新交易日、漲跌家數與三大法人全市場總體買賣超統計"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date FROM daily_quotes
        GROUP BY date
        HAVING COUNT(*) >= 500
        ORDER BY date DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    max_date = row[0] if row else get_latest_quote_date()
    if not max_date:
        conn.close()
        return "暫無市場資料。"

    df_q = pd.read_sql_query("SELECT change FROM daily_quotes WHERE date = ?", conn, params=(max_date,))
    df_i = pd.read_sql_query("SELECT foreign_buy, trust_buy, dealer_buy, total_buy FROM institutional WHERE date = ?", conn, params=(max_date,))
    conn.close()

    up_count = (df_q["change"] > 0).sum()
    down_count = (df_q["change"] < 0).sum()
    flat_count = (df_q["change"] == 0).sum()

    tot_foreign = round(df_i["foreign_buy"].sum(), 1) if not df_i.empty else 0
    tot_trust = round(df_i["trust_buy"].sum(), 1) if not df_i.empty else 0
    tot_dealer = round(df_i["dealer_buy"].sum(), 1) if not df_i.empty else 0

    return (
        f"台股大盤最新日報 ({max_date}):\n"
        f"- 上漲家數: {up_count} 檔 | 下跌家數: {down_count} 檔 | 平盤: {flat_count} 檔\n"
        f"- 全市場三大法人動態: 外資 {tot_foreign:+,.1f} 張、投信 {tot_trust:+,.1f} 張、自營商 {tot_dealer:+,.1f} 張"
    )

SYSTEM_PROMPT = """你是一位專業、嚴謹且客觀的台股量化投資分析師。
你具備即時調用本地資料庫工具獲取台股 1800+ 檔個股最新行情、三大法人籌碼、技術指標 (MA5/20/60, KD, MACD, 乖離率) 以及估價狀態的能力。

【分析規範】：
1. 涉及具體個股時，務必調用 `get_stock_summary` 工具獲取最新真實數據，切勿臆測。
2. 涉及推薦選股時，調用 `get_top_picks` 工具獲取推薦榜單。
3. 涉及大盤情勢時，調用 `get_market_overview`。
4. 解答結構應清晰條理，包含：
   - 【盤面與籌碼】：三大法人動向、量能爆量或萎縮。
   - 【技術指標與位階】：站上/跌破均線、KD/MACD狀態、乖離率是否過熱。
   - 【估值水準】：本益比與殖利率位階（便宜/合理/昂貴）。
   - 【操作風控建議】：明確指出進場區間、停損防守點與目標位。
5. 每次分析結尾請提醒投資風險，並建議嚴格執行資金管理與停損。
"""

def chat_with_gemini(messages: List[Dict[str, str]], api_key: Optional[str] = None, model: Optional[str] = None) -> str:
    """呼叫 Google Gemini API 進行股票對話 (支援工具調用與自動後備重試)"""
    key = api_key or GEMINI_API_KEY
    if not key:
        return "⚠️ 請先在側邊欄輸入有效的 Google Gemini API Key，或於 .env 檔中配置 GEMINI_API_KEY。"

    preferred_model = model or GEMINI_MODEL
    # 自動重試模型清單 (優先使用指定模型，若遇 404/停用則依序嘗試後備版本)
    candidates = [preferred_model]
    for m in ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-flash-lite-latest"]:
        if m not in candidates:
            candidates.append(m)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)

        # 封裝可用工具
        tools = [get_stock_summary, get_top_picks, get_market_overview]

        # 轉換歷史訊息格式
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])]))

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=tools,
            temperature=0.3,
        )

        last_error = None
        for candidate_model in candidates:
            try:
                response = client.models.generate_content(
                    model=candidate_model,
                    contents=contents,
                    config=config,
                )
                return response.text or "Gemini 無法生成回應，請再試一次。"
            except Exception as candidate_err:
                err_str = str(candidate_err)
                if "404" in err_str or "NOT_FOUND" in err_str or "no longer available" in err_str:
                    logging.warning(f"模型 {candidate_model} 不可用，正在嘗試後備模型...")
                    last_error = candidate_err
                    continue
                else:
                    raise candidate_err

        if last_error:
            raise last_error

        return "Gemini 無法生成回應，請再試一次。"
    except Exception as e:
        logging.error(f"Gemini 對話失敗: {e}")
        return f"呼叫 Gemini 發生錯誤: {str(e)}"

