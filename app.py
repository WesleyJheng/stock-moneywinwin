import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import os

from config import DEFAULT_WATCHLIST, GEMINI_API_KEY, GEMINI_MODEL, APP_PASSWORD
from database import (
    init_db, get_watchlist, add_to_watchlist, remove_from_watchlist,
    get_stock_detail, get_stock_history, get_latest_quote_date, get_connection,
    get_all_watchlist_symbols
)
from crawler import run_daily_crawler, sync_history_for_symbols
from engine import (
    batch_update_all_indicators, run_recommendation_strategies,
    get_recommendations, update_indicators_for_stock,
    analyze_stock_signals_and_prices
)
from agent import chat_with_gemini

# 頁面配置
st.set_page_config(
    page_title="台股全自動分析機器人 & 戰情室",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 初始化資料庫
init_db()

# 自訂 CSS 樣式
st.markdown("""
<style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #e9ecef;
        margin-bottom: 10px;
    }
    .badge-momentum {
        background-color: #fee2e2;
        color: #991b1b;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.85em;
    }
    .badge-value {
        background-color: #dbeafe;
        color: #1e40af;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.85em;
    }
</style>
""", unsafe_allow_html=True)

# ----------------- 登入通行密碼防護 -----------------
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    _, col_login, _ = st.columns([1, 2, 1])
    with col_login:
        st.write("")
        st.write("")
        st.markdown("### 🔐 台股量化戰情室・安全登入")
        st.caption("此戰情室受專屬加密通道與密碼保護，請輸入通關密碼解鎖")
        pwd_input = st.text_input("請輸入通行密碼：", type="password", key="login_pwd")
        if st.button("解鎖進入戰情室 🚀", use_container_width=True):
            if pwd_input == APP_PASSWORD:
                st.session_state["authenticated"] = True
                st.success("密碼正確！正在進入戰情室...")
                st.rerun()
            else:
                st.error("❌ 密碼錯誤，請重新輸入！")
    st.stop()

# ----------------- 側邊欄 -----------------
with st.sidebar:
    st.title("⚙️ 系統控制台")
    col_auth1, col_auth2 = st.columns([2, 1])
    with col_auth1:
        st.caption("🟢 已安全登入")
    with col_auth2:
        if st.button("登出", key="btn_logout", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()

    # 介面版型切換 (手機 vs 電腦)
    st.divider()
    st.subheader("📱 介面版型切換")
    view_mode = st.radio(
        "選擇顯示版型：",
        ["💻 電腦專業版", "📱 手機極簡版"],
        index=0,
        key="ui_view_mode",
        help="📱 手機極簡版：直式卡片流、圖表自動縮放、按鈕防誤觸\n💻 電腦專業版：14欄完整表格 + 4子圖大圖"
    )
    is_mobile = (view_mode == "📱 手機極簡版")
    st.divider()

    # 遠端手機/外網安全網址
    tunnel_file = os.path.join(os.path.dirname(__file__), "tunnel_url.txt")
    if os.path.exists(tunnel_file):
        try:
            with open(tunnel_file, "r", encoding="utf-8") as f:
                remote_url = f.read().strip()
            if remote_url:
                st.success(f"📱 **外網手機專屬看盤網址**\n\n[{remote_url}]({remote_url})")
        except Exception:
            pass

    # API Key 安全設定
    if GEMINI_API_KEY:
        st.caption("🔑 Gemini API：系統已加載安全金鑰 ✅")
        api_key_override = st.text_input(
            "更換 Gemini API Key (選填)",
            value="",
            type="password",
            help="若留空則預設使用系統安全金鑰"
        )
        api_key_input = api_key_override.strip() if api_key_override.strip() else GEMINI_API_KEY
    else:
        api_key_input = st.text_input(
            "Google Gemini API Key",
            value="",
            type="password",
            help="輸入 Gemini API Key 以啟用 AI 股友對話功能"
        )

    gemini_model_choice = st.selectbox(
        "Gemini 模型版本",
        options=["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-flash-lite-latest"],
        format_func=lambda x: {
            "gemini-3.5-flash-lite": "⚡ gemini-3.5-flash-lite (極速推薦)",
            "gemini-3.6-flash": "🧠 gemini-3.6-flash (深度推理)",
            "gemini-flash-lite-latest": "🚀 gemini-flash-lite-latest (最新輕量)"
        }.get(x, x),
        index=0,
        help="推薦使用 gemini-3.5-flash-lite，兼具秒級回應與完整量化分析"
    )

    st.divider()

    # 資料庫狀態與更新
    latest_date = get_latest_quote_date()
    st.subheader("📊 資料庫狀態")
    st.info(f"最新報價日期: **{latest_date if latest_date else '尚無數據'}**")

    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT symbol) FROM stocks")
    stock_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM daily_quotes")
    quote_count = c.fetchone()[0]
    conn.close()
    st.caption(f"已收錄股票: {stock_count} 檔 | 行情筆數: {quote_count} 筆")

    # 手動更新按鈕
    if st.button("🚀 執行全市場 1800+ 檔日更", use_container_width=True):
        with st.spinner("正在連線 TWSE / TPEX 抓取全市場行情與法人資料..."):
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            count = run_daily_crawler(today_str)
            if count > 0:
                st.spinner("正在計算技術指標與推薦模型...")
                batch_update_all_indicators()
                run_recommendation_strategies(today_str)
                st.success(f"更新成功！已儲存 {count} 檔股票數據與推薦榜單。")
                st.rerun()
            else:
                st.warning("今日無新交易資料（可能為假日或尚未收盤）。")

    if st.button("📈 回補所有自選股 3 個月歷史 K 線", use_container_width=True):
        symbols = get_all_watchlist_symbols()
        with st.spinner(f"正在透過 yfinance 回補 {len(symbols)} 檔歷史資料..."):
            saved = sync_history_for_symbols(symbols, period="3mo")
            for s in symbols:
                update_indicators_for_stock(s)
            run_recommendation_strategies()
            st.success(f"回補完成！已更新 {saved} 筆歷史 K 線並重新計算指標。")
            st.rerun()

    st.divider()

    # 自選股管理
    st.subheader("⭐ 自選股管理")
    target_user = st.radio("選擇目標用戶帳戶：", ["WEI", "YUN"], horizontal=True, key="sidebar_target_user")
    new_symbol = st.text_input(f"輸入股票代號或名稱 (新增至 {target_user}):", key="new_sym_input")
    if st.button(f"加入至 {target_user} 的自選", use_container_width=True):
        if new_symbol.strip():
            query_txt = new_symbol.strip()
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT symbol, name FROM stocks WHERE symbol = ? OR name LIKE ? LIMIT 1", (query_txt, f"%{query_txt}%"))
            row = c.fetchone()
            conn.close()
            if row:
                sym = row["symbol"]
                name = row["name"]
                add_to_watchlist(sym, name, user_id=target_user)
                with st.spinner(f"正在為 {sym} {name} 載入歷史數據..."):
                    sync_history_for_symbols([sym], period="3mo")
                    update_indicators_for_stock(sym)
                st.success(f"已成功加入 {target_user} 的自選：{sym} {name}！")
            else:
                add_to_watchlist(query_txt, user_id=target_user)
                with st.spinner(f"正在為 {query_txt} 載入歷史數據..."):
                    sync_history_for_symbols([query_txt], period="3mo")
                    update_indicators_for_stock(query_txt)
                st.success(f"已加入 {target_user} 的自選：{query_txt}！")
            st.rerun()

# ----------------- 主介面分頁 -----------------
tab1, tab2, tab3 = st.tabs(["📊 自選股戰情室", "🎯 AI 雙向推薦榜", "🤖 Gemini 智能股友"])

# ----------------- 分頁 1: 自選股戰情室 -----------------
def handle_add_stock(query_text: str, user_id: str):
    """處理新增自選股並同步歷史 K 線"""
    q_txt = query_text.strip()
    if not q_txt:
        return
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT symbol, name FROM stocks WHERE symbol = ? OR name LIKE ? LIMIT 1", (q_txt, f"%{q_txt}%"))
    row = c.fetchone()
    conn.close()
    if row:
        sym, name = row["symbol"], row["name"]
        add_to_watchlist(sym, name, user_id=user_id)
        with st.spinner(f"正在為 {sym} {name} 載入歷史數據..."):
            sync_history_for_symbols([sym], period="3mo")
            update_indicators_for_stock(sym)
        st.success(f"已加入 {user_id} 的自選：{sym} {name}！")
    else:
        add_to_watchlist(q_txt, user_id=user_id)
        with st.spinner(f"正在為 {q_txt} 載入歷史數據..."):
            sync_history_for_symbols([q_txt], period="3mo")
            update_indicators_for_stock(q_txt)
        st.success(f"已加入 {user_id} 的自選：{q_txt}！")
    st.rerun()

# ----------------- 分頁 1: 自選股戰情室 -----------------
def render_watchlist_view(user_id: str, is_mobile: bool = False):
    """渲染指定用戶 (WEI 或 YUN) 的自選股戰情室儀表板 (支援電腦/手機雙模)"""
    # 頂部快速加入欄位
    if is_mobile:
        quick_input = st.text_input(
            f"➕ 加入至 【{user_id}】 (輸入代號或名稱):",
            key=f"quick_add_m_{user_id}",
            placeholder="例: 2330 或 台積電"
        )
        if st.button(f"加入至 {user_id}", key=f"btn_quick_m_{user_id}", use_container_width=True):
            handle_add_stock(quick_input, user_id)
    else:
        col_add_in, col_add_btn = st.columns([4, 1])
        with col_add_in:
            quick_input = st.text_input(
                f"➕ 快速加入股票至 【{user_id}】 自選清單 (輸入代號或名稱):",
                key=f"quick_add_{user_id}",
                placeholder="例: 2330 或 台積電"
            )
        with col_add_btn:
            st.write("")
            st.write("")
            if st.button(f"加入至 {user_id}", key=f"btn_quick_{user_id}", use_container_width=True):
                handle_add_stock(quick_input, user_id)

    watchlist = get_watchlist(user_id=user_id)

    if not watchlist:
        st.info(f"💡 目前 【{user_id}】 尚未加入任何自選股，請利用上方輸入框或側邊欄新增股票！")
        return

    # 自選股行情數據彙整
    summary_rows = []
    for item in watchlist:
        sym = item["symbol"]
        detail = get_stock_detail(sym)
        if detail and detail.get("close"):
            advice = analyze_stock_signals_and_prices(detail)
            entry_str = f"{advice['entry_low']:.2f} ~ {advice['entry_high']:.2f}" if advice.get('entry_low') is not None and advice.get('entry_high') is not None else "--"
            summary_rows.append({
                **detail,
                "action_type": advice["action_type"],
                "signals": " | ".join(advice["signals"][:3]),
                "entry_range": entry_str,
                "stop_loss": advice["stop_loss"],
                "target_price": advice["target_price"]
            })
        else:
            summary_rows.append({
                "symbol": sym,
                "name": item["name"],
                "close": None,
                "change_pct": None,
                "action_type": "數據加載中",
                "signals": "--",
                "entry_range": "--",
                "stop_loss": "--",
                "target_price": "--",
                "volume": None,
                "foreign_buy": None,
                "trust_buy": None,
                "pe": None,
                "yield_pct": None,
                "bias20": None
            })

    # ---------- 手機版專屬：直式卡片流視圖 ----------
    if is_mobile:
        st.caption(f"📱 【{user_id}】 自選股清單 ({len(summary_rows)} 檔)")
        for row in summary_rows:
            sym = row["symbol"]
            name = row["name"]
            close_p = row.get("close")
            chg = row.get("change_pct") or 0.0
            p_color = "#dc2626" if chg >= 0 else "#16a34a"
            act_type = row.get("action_type") or "觀察中"

            if "波段偏多" in act_type or "順勢進場" in act_type:
                badge_bg, badge_fg = "#fee2e2", "#b91c1c"
            elif "回測支撐" in act_type or "逢低布局" in act_type:
                badge_bg, badge_fg = "#dbeafe", "#1d4ed8"
            elif "防追高" in act_type or "過熱" in act_type:
                badge_bg, badge_fg = "#fef3c7", "#b45309"
            else:
                badge_bg, badge_fg = "#f3f4f6", "#4b5563"

            entry_r = row.get("entry_range") or "--"
            stop_l = f"{row['stop_loss']:.2f}" if isinstance(row.get('stop_loss'), (int, float)) else str(row.get('stop_loss') or "--")
            target_p = f"{row['target_price']:.2f}" if isinstance(row.get('target_price'), (int, float)) else str(row.get('target_price') or "--")
            close_str = f"{close_p:.2f}" if close_p is not None else "--"
            chg_str = f"{chg:+.2f}%" if close_p is not None else "--"
            f_buy = f"{row.get('foreign_buy', 0):+,.0f}" if row.get('foreign_buy') is not None else "--"
            t_buy = f"{row.get('trust_buy', 0):+,.0f}" if row.get('trust_buy') is not None else "--"
            bias_val = row.get('bias20')
            bias_str = f"{bias_val:+.1f}%" if bias_val is not None else "--"

            st.markdown(f"""
            <div style="background:#ffffff; border-radius:12px; padding:12px 14px; margin-bottom:10px; border:1px solid #e5e7eb; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:1.15em; font-weight:800; color:#111827;">{sym} {name}</span>
                    <div>
                        <span style="font-size:1.15em; font-weight:800; color:{p_color};">{close_str}</span>
                        <span style="font-size:0.95em; font-weight:700; color:{p_color}; margin-left:6px;">{chg_str}</span>
                    </div>
                </div>
                <div style="margin: 6px 0;">
                    <span style="background:{badge_bg}; color:{badge_fg}; font-size:0.82em; font-weight:800; padding:2px 8px; border-radius:4px;">{act_type}</span>
                    <span style="font-size:0.82em; color:#4b5563; margin-left:8px;">{row.get('signals', '')}</span>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr 1fr; background:#f9fafb; border-radius:8px; padding:6px 2px; text-align:center; font-size:0.82em; margin-top:6px;">
                    <div><span style="color:#6b7280;">建議進場</span><br><b style="color:#1f2937;">{entry_r}</b></div>
                    <div><span style="color:#6b7280;">防守停損</span><br><b style="color:#dc2626;">{stop_l}</b></div>
                    <div><span style="color:#6b7280;">波段目標</span><br><b style="color:#16a34a;">{target_p}</b></div>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.75em; color:#6b7280; margin-top:6px; padding:0 2px;">
                    <span>外資: {f_buy} 張</span>
                    <span>投信: {t_buy} 張</span>
                    <span>月線乖離: {bias_str}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)

            with st.expander(f"📊 展開 {sym} {name} K 線與深度健檢"):
                detail = get_stock_detail(sym)
                if detail:
                    adv = analyze_stock_signals_and_prices(detail)
                    st.info(f"💡 **【操作指引】** {adv['strategy_text']}")

                    m1, m2 = st.columns(2)
                    m1.markdown(f"**📐 均線**：`{adv['ma_status']}`")
                    m2.markdown(f"**⚡ KD**：`{adv['kd_status']}`")
                    m1.markdown(f"**🌊 MACD**：`{adv['macd_status']}`")
                    m2.markdown(f"**📏 乖離**：`{adv['bias_status']}`")

                    hist = get_stock_history(sym, limit=60)
                    if not hist.empty and len(hist) >= 5:
                        m_fig = make_subplots(
                            rows=2, cols=1,
                            shared_xaxes=True,
                            vertical_spacing=0.05,
                            row_heights=[0.68, 0.32],
                            subplot_titles=[f"{sym} {name} 日 K 線", "成交量 (張)"]
                        )
                        m_fig.add_trace(go.Candlestick(
                            x=hist["date"], open=hist["open"], high=hist["high"],
                            low=hist["low"], close=hist["close"], name="K線",
                            increasing_line_color="#ef4444", decreasing_line_color="#22c55e"
                        ), row=1, col=1)
                        if "ma5" in hist:
                            m_fig.add_trace(go.Scatter(x=hist["date"], y=hist["ma5"], name="MA5", line=dict(color="#f59e0b", width=1.2)), row=1, col=1)
                        if "ma20" in hist:
                            m_fig.add_trace(go.Scatter(x=hist["date"], y=hist["ma20"], name="MA20", line=dict(color="#3b82f6", width=1.5)), row=1, col=1)

                        v_colors = ["#ef4444" if r["close"] >= r["open"] else "#22c55e" for _, r in hist.iterrows()]
                        m_fig.add_trace(go.Bar(x=hist["date"], y=hist["volume"], name="成交量", marker_color=v_colors), row=2, col=1)
                        m_fig.update_layout(
                            height=380,
                            xaxis_rangeslider_visible=False,
                            margin=dict(l=5, r=5, t=30, b=5),
                            showlegend=False
                        )
                        st.plotly_chart(m_fig, use_container_width=True, key=f"m_chart_{user_id}_{sym}")

                if st.button(f"🗑️ 從 {user_id} 移除此自選股", key=f"m_del_{user_id}_{sym}", use_container_width=True):
                    remove_from_watchlist(sym, user_id=user_id)
                    st.toast(f"已從 {user_id} 移除 {sym}！")
                    st.rerun()
        return

    # ---------- 電腦版：14 欄專業總表 ----------
    df_summary = pd.DataFrame(summary_rows)

    if not df_summary.empty and "close" in df_summary.columns:
        display_cols = {
            "symbol": "代號",
            "name": "名稱",
            "close": "最新價",
            "change_pct": "漲跌(%)",
            "action_type": "操作評估",
            "signals": "技術訊號",
            "entry_range": "建議進場價",
            "stop_loss": "防守停損價",
            "target_price": "波段目標價",
            "volume": "成交量(張)",
            "foreign_buy": "外資買超(張)",
            "trust_buy": "投信買超(張)",
            "bias20": "月線乖離(%)",
            "pe": "本益比",
            "yield_pct": "殖利率(%)"
        }
        show_df = df_summary[[c for c in display_cols.keys() if c in df_summary.columns]].rename(columns=display_cols)

        def color_change_pct(val):
            if pd.isna(val):
                return ''
            try:
                v = float(val)
                if v > 0:
                    return 'color: #dc2626; font-weight: 800;'
                elif v < 0:
                    return 'color: #16a34a; font-weight: 800;'
                else:
                    return 'color: #6b7280;'
            except:
                return ''

        def color_action_type(val):
            if not isinstance(val, str):
                return ''
            if '波段偏多' in val or '順勢進場' in val:
                return 'background-color: #fee2e2; color: #b91c1c; font-weight: 800; border-radius: 4px; padding: 2px 6px;'
            elif '回測支撐' in val or '逢低布局' in val:
                return 'background-color: #dbeafe; color: #1d4ed8; font-weight: 800; border-radius: 4px; padding: 2px 6px;'
            elif '防追高' in val or '過熱' in val:
                return 'background-color: #fef3c7; color: #b45309; font-weight: 800; border-radius: 4px; padding: 2px 6px;'
            elif '偏弱' in val or '觀望' in val or '防守' in val:
                return 'background-color: #f3f4f6; color: #4b5563; font-weight: 800; border-radius: 4px; padding: 2px 6px;'
            return ''

        def fmt_2f(v):
            try:
                if pd.notna(v) and v != "--" and v is not None:
                    return f"{float(v):.2f}"
            except:
                pass
            return "--"

        styled_df = (
            show_df.style
            .map(color_change_pct, subset=["漲跌(%)"])
            .map(color_action_type, subset=["操作評估"])
            .format({
                "最新價": "{:.2f}",
                "漲跌(%)": "{:+.2f}%",
                "防守停損價": fmt_2f,
                "波段目標價": fmt_2f,
                "成交量(張)": "{:,.0f}",
                "外資買超(張)": "{:+,.0f}",
                "投信買超(張)": "{:+,.0f}",
                "月線乖離(%)": "{:+.2f}%",
                "本益比": "{:.1f}",
                "殖利率(%)": "{:.1f}%",
            }, na_rep="--")
        )
        st.dataframe(styled_df, use_container_width=True, hide_index=True)

    st.divider()

    # 個股深度互動圖表
    col_select, col_del = st.columns([4, 1])
    with col_select:
        selected_symbol = st.selectbox(
            f"選擇要深入分析的個股 【{user_id}】：",
            options=[item["symbol"] for item in watchlist],
            format_func=lambda x: f"{x} - {next((i['name'] for i in watchlist if i['symbol'] == x), '')}",
            key=f"select_{user_id}"
        )
    with col_del:
        st.write("")
        st.write("")
        if st.button("❌ 移除此自選股", key=f"del_{user_id}"):
            remove_from_watchlist(selected_symbol, user_id=user_id)
            st.rerun()

    if selected_symbol:
        hist_df = get_stock_history(selected_symbol, limit=90)
        if len(hist_df) < 15:
            with st.spinner(f"正在為 {selected_symbol} 載入完整歷史 K 線與指標..."):
                sync_history_for_symbols([selected_symbol], period="3mo")
                update_indicators_for_stock(selected_symbol)
                hist_df = get_stock_history(selected_symbol, limit=90)

        detail = get_stock_detail(selected_symbol)
        if detail and detail.get("close"):
            c1, c2, c3, c4, c5 = st.columns(5)
            close_p = detail.get("close")
            chg = detail.get("change") or 0.0
            chg_p = detail.get("change_pct") or 0.0
            delta_color = "normal" if chg >= 0 else "inverse"
            c1.metric(
                "收盤價",
                f"{close_p:.2f}" if close_p is not None else "--",
                f"{chg:+.2f} ({chg_p:+.2f}%)",
                delta_color=delta_color
            )

            vol = detail.get("volume")
            vol_str = f"{vol:,.0f} 張" if vol is not None else "--"
            surge = detail.get("vol_surge")
            surge_str = f"量增比 {surge:.1f}x" if surge is not None else "量增比 --"
            c2.metric("成交量", vol_str, surge_str)

            tot_buy = detail.get("total_buy")
            tot_str = f"{tot_buy:+,.0f} 張" if tot_buy is not None else "--"
            f_buy = detail.get("foreign_buy")
            t_buy = detail.get("trust_buy")
            f_str = f"外資: {f_buy:+,.0f}" if f_buy is not None else "外資: --"
            t_str = f"投信: {t_buy:+,.0f}" if t_buy is not None else "投信: --"
            c3.metric("三大法人買賣超", tot_str, f"{f_str} | {t_str}")

            bias = detail.get("bias20")
            bias_str = f"{bias:.2f}%" if bias is not None else "計算中"
            bias_status = "偏高過熱" if (bias is not None and bias > 10) else ("偏低超跌" if (bias is not None and bias < -10) else "正常")
            c4.metric("月線乖離率", bias_str, bias_status)

            pe_val = detail.get("pe")
            pe_v = f"{pe_val:.1f}x" if pe_val is not None else "N/A"
            yd_val = detail.get("yield_pct")
            yd_v = f"殖利率: {yd_val:.1f}%" if yd_val is not None else "殖利率: N/A"
            c5.metric("估值位階", pe_v, yd_v)

            advice = analyze_stock_signals_and_prices(detail)
            action = advice["action_type"]
            if "波段偏多" in action or "順勢進場" in action:
                b_color = "#b91c1c"
                b_bg = "#fee2e2"
                b_icon = "🚀"
            elif "回測支撐" in action or "逢低布局" in action:
                b_color = "#1d4ed8"
                b_bg = "#dbeafe"
                b_icon = "🛡️"
            elif "防追高" in action or "過熱" in action:
                b_color = "#b45309"
                b_bg = "#fef3c7"
                b_icon = "⚠️"
            else:
                b_color = "#374151"
                b_bg = "#f3f4f6"
                b_icon = "⏸️"

            st.markdown(f"""
            <div style="display: flex; align-items: center; gap: 12px; margin-top: 15px; margin-bottom: 8px;">
                <h4 style="margin: 0;">🎯 量化決策與推薦進出場價格</h4>
                <span style="background-color: {b_bg}; color: {b_color}; font-weight: 800; padding: 4px 12px; border-radius: 6px; font-size: 0.95em; border: 1px solid {b_color}33;">
                    {b_icon} {action}
                </span>
            </div>
            """, unsafe_allow_html=True)

            st.info(f"💡 **【操作指引】** {advice['strategy_text']}")

            # 4 列指標卡片
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("策略定位", advice["action_type"], " | ".join(advice["signals"][:2]))
            entry_display = f"{advice['entry_low']:.2f} ~ {advice['entry_high']:.2f} 元" if advice.get('entry_low') is not None and advice.get('entry_high') is not None else "--"
            q2.metric("🎯 建議進場區間", entry_display, "回測均線支撐逢低承接")
            stop_display = f"{advice['stop_loss']:.2f} 元" if advice.get('stop_loss') is not None else "--"
            q3.metric("🛑 防守停損價位", stop_display, "跌破關鍵均線/破前低")
            target_display = f"{advice['target_price']:.2f} 元" if advice.get('target_price') is not None else "--"
            q4.metric("🚀 波段目標價位", target_display, "預估波段滿足點")

            # 即時技術指標綜合診斷標籤
            st.markdown("#### 📊 即時技術指標綜合診斷")
            t1, t2, t3, t4 = st.columns(4)
            t1.markdown(f"**📐 均線狀態**：`{advice['ma_status']}`")
            t2.markdown(f"**⚡ KD 動能**：`{advice['kd_status']}`")
            t3.markdown(f"**🌊 MACD 趨勢**：`{advice['macd_status']}`")
            t4.markdown(f"**📏 乖離監控**：`{advice['bias_status']}`")

            if not hist_df.empty and len(hist_df) >= 5:
                fig = make_subplots(
                    rows=4, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.03,
                    row_heights=[0.5, 0.18, 0.16, 0.16],
                    subplot_titles=["K 線與均線系統 (MA5, MA20, MA60)", "成交量與法人籌碼", "9日 KD 動能指標", "MACD 柱狀體與趨勢"]
                )

                # 1. K線圖 + MA
                fig.add_trace(go.Candlestick(
                    x=hist_df["date"],
                    open=hist_df["open"],
                    high=hist_df["high"],
                    low=hist_df["low"],
                    close=hist_df["close"],
                    name="K線",
                    increasing_line_color="#ef4444",
                    decreasing_line_color="#22c55e"
                ), row=1, col=1)

                if "ma5" in hist_df:
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["ma5"], name="MA5", line=dict(color="#f59e0b", width=1.2)), row=1, col=1)
                if "ma20" in hist_df:
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["ma20"], name="MA20(月線)", line=dict(color="#3b82f6", width=1.5)), row=1, col=1)
                if "ma60" in hist_df:
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["ma60"], name="MA60(季線)", line=dict(color="#8b5cf6", width=1.5)), row=1, col=1)

                # 2. 成交量
                vol_colors = ["#ef4444" if r["close"] >= r["open"] else "#22c55e" for _, r in hist_df.iterrows()]
                fig.add_trace(go.Bar(x=hist_df["date"], y=hist_df["volume"], name="成交量(張)", marker_color=vol_colors), row=2, col=1)

                # 3. KD 指標
                if "k9" in hist_df and "d9" in hist_df:
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["k9"], name="K (9)", line=dict(color="#ef4444", width=1.2)), row=3, col=1)
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["d9"], name="D (9)", line=dict(color="#3b82f6", width=1.2)), row=3, col=1)
                    fig.add_hline(y=80, line_dash="dash", line_color="gray", row=3, col=1)
                    fig.add_hline(y=20, line_dash="dash", line_color="gray", row=3, col=1)

                # 4. MACD
                if "macd" in hist_df and "macd_signal" in hist_df:
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["macd"], name="DIF", line=dict(color="#3b82f6", width=1)), row=4, col=1)
                    fig.add_trace(go.Scatter(x=hist_df["date"], y=hist_df["macd_signal"], name="DEA", line=dict(color="#f59e0b", width=1)), row=4, col=1)
                    hist_colors = ["#ef4444" if v >= 0 else "#22c55e" for v in hist_df["macd_hist"]]
                    fig.add_trace(go.Bar(x=hist_df["date"], y=hist_df["macd_hist"], name="MACD柱", marker_color=hist_colors), row=4, col=1)

                fig.update_layout(
                    height=750,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=20, r=20, t=40, b=20),
                    hovermode="x unified"
                )
                st.plotly_chart(fig, use_container_width=True, key=f"chart_{user_id}_{selected_symbol}")
            else:
                st.info("該股票歷史 K 線筆數較少，系統已安排排程自動回補。")
        else:
            st.warning(f"股票 {selected_symbol} 暫無今日行情，請先執行全市場日更。")

# ----------------- 分頁 1: 自選股戰情室 -----------------
with tab1:
    st.header("📊 自選股戰情室")
    subtab_wei, subtab_yun = st.tabs(["👤 WEI 的自選股", "👤 YUN 的自選股"])
    with subtab_wei:
        render_watchlist_view("WEI", is_mobile=is_mobile)
    with subtab_yun:
        render_watchlist_view("YUN", is_mobile=is_mobile)

# ----------------- 分頁 2: AI 推薦選股榜 -----------------
with tab2:
    st.header("🎯 AI 雙向推薦榜單")

    col_strat, col_top = st.columns([3, 2])
    with col_strat:
        strategy_mode = st.radio(
            "選擇選股推薦策略：",
            options=["momentum", "value_pullback"],
            format_func=lambda x: "🚀 波段動能榜 (突破創高・法人追買・均線多頭)" if x == "momentum" else "🛡️ 價值防守榜 (低本益比・高殖利率・回測月季線支撐)",
            horizontal=True
        )
    with col_top:
        top_n = st.select_slider("推薦名額：", options=[5, 10, 20], value=10)

    recs = get_recommendations(strategy=strategy_mode, top_n=top_n)

    if not recs:
        st.info("目前尚無推薦選股資料。請先點擊側邊欄「執行全市場 1800+ 檔日更」以觸發演算法評分。")
    else:
        # 展示 Top 推薦卡片
        st.subheader(f"🏆 Top {top_n} 精選清單")
        
        for r in recs:
            e_p = f"{float(r['entry_price']):.2f}" if r.get('entry_price') is not None else "--"
            s_l = f"{float(r['stop_loss']):.2f}" if r.get('stop_loss') is not None else "--"
            t_p = f"{float(r['target_price']):.2f}" if r.get('target_price') is not None else "--"
            chg = r.get("change_pct") or 0.0
            p_color = "#dc2626" if chg >= 0 else "#16a34a"

            if is_mobile:
                with st.container():
                    badges = [f"`{b.strip()}`" for b in r['reasons'].split("|") if b.strip()]
                    f_buy = f"{r.get('foreign_buy', 0):+,.0f}"
                    t_buy = f"{r.get('trust_buy', 0):+,.0f}"
                    st.markdown(f"""
                    <div style="background:#ffffff; border-radius:12px; padding:12px 14px; margin-bottom:10px; border:1px solid #e5e7eb; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div>
                                <span style="background:#ef4444; color:#ffffff; font-size:0.8em; font-weight:800; padding:2px 8px; border-radius:6px; margin-right:6px;">#{r['rank']}</span>
                                <span style="font-size:1.15em; font-weight:800; color:#111827;">{r['symbol']} {r['name']}</span>
                            </div>
                            <div>
                                <span style="font-size:1.15em; font-weight:800; color:{p_color};">{r['close']}</span>
                                <span style="font-size:0.95em; font-weight:700; color:{p_color}; margin-left:4px;">{chg:+.2f}%</span>
                            </div>
                        </div>
                        <div style="margin: 6px 0;">
                            <span style="font-size:0.82em; color:#4b5563;">{" ".join(badges)}</span>
                        </div>
                        <div style="display:grid; grid-template-columns:1fr 1fr 1fr; background:#f9fafb; border-radius:8px; padding:6px 2px; text-align:center; font-size:0.82em; margin-top:6px;">
                            <div><span style="color:#6b7280;">建議進場</span><br><b style="color:#1f2937;">{e_p}</b></div>
                            <div><span style="color:#6b7280;">防守停損</span><br><b style="color:#dc2626;">{s_l}</b></div>
                            <div><span style="color:#6b7280;">波段目標</span><br><b style="color:#16a34a;">{t_p}</b></div>
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:0.75em; color:#6b7280; margin-top:6px; padding:0 2px;">
                            <span>外資: {f_buy} 張</span>
                            <span>投信: {t_buy} 張</span>
                            <span>評分: {r['score']}</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    btn_m1, btn_m2 = st.columns(2)
                    with btn_m1:
                        if st.button("➕ 加入 WEI", key=f"m_add_wei_{strategy_mode}_{r['symbol']}", use_container_width=True):
                            add_to_watchlist(r["symbol"], r["name"], user_id="WEI")
                            st.toast(f"已將 {r['symbol']} 加入 WEI 的自選股！")
                    with btn_m2:
                        if st.button("➕ 加入 YUN", key=f"m_add_yun_{strategy_mode}_{r['symbol']}", use_container_width=True):
                            add_to_watchlist(r["symbol"], r["name"], user_id="YUN")
                            st.toast(f"已將 {r['symbol']} 加入 YUN 的自選股！")
                    st.divider()
            else:
                with st.container():
                    c_rank, c_info, c_price, c_signals, c_action = st.columns([0.8, 2.2, 2.5, 3.3, 2.2])
                    with c_rank:
                        st.markdown(f"### #{r['rank']}")
                        st.caption(f"評分: {r['score']}")
                    with c_info:
                        st.markdown(f"**{r['symbol']} {r['name']}**")
                        color = "red" if chg >= 0 else "green"
                        st.markdown(f"收盤: **{r['close']}** (<span style='color:{color}'>{chg:+.2f}%</span>)", unsafe_allow_html=True)
                    with c_price:
                        st.markdown(f"🎯 **建議進場**: `{e_p}`")
                        st.markdown(f"🛑 **防守停損**: `{s_l}` | 🚀 **目標**: `{t_p}`")
                    with c_signals:
                        # 推薦理由標籤
                        badges = [f"`{b.strip()}`" for b in r['reasons'].split("|") if b.strip()]
                        st.markdown("理由: " + " ".join(badges))
                        st.caption(f"外資: {r.get('foreign_buy', 0):+,.0f} 張 | 投信: {r.get('trust_buy', 0):+,.0f} 張")
                    with c_action:
                        st.write("")
                        btn_c1, btn_c2 = st.columns(2)
                        with btn_c1:
                            if st.button("➕WEI", key=f"add_wei_{strategy_mode}_{r['symbol']}", use_container_width=True):
                                add_to_watchlist(r["symbol"], r["name"], user_id="WEI")
                                st.toast(f"已將 {r['symbol']} 加入 WEI 的自選股！")
                        with btn_c2:
                            if st.button("➕YUN", key=f"add_yun_{strategy_mode}_{r['symbol']}", use_container_width=True):
                                add_to_watchlist(r["symbol"], r["name"], user_id="YUN")
                                st.toast(f"已將 {r['symbol']} 加入 YUN 的自選股！")
                    st.divider()

# ----------------- 分頁 3: Gemini 智能股友對話 -----------------
with tab3:
    st.header("🤖 Gemini 股票分析助理")
    st.caption("具備調用本地數據庫 (1800+ 檔即時籌碼、均線、估價指標與推薦名單) 的智能量化顧問")

    # 快捷問題按鈕
    preset_q = None
    if is_mobile:
        st.caption("💡 點選下方推薦快捷提問：")
        if st.button("📊 今日大盤與法人動向總結", key="m_q1", use_container_width=True):
            preset_q = "請幫我總結今日台股大盤情勢、漲跌家數與三大法人買賣動向。"
        if st.button("🚀 推薦第一名動能股分析", key="m_q2", use_container_width=True):
            preset_q = "請調用推薦工具，幫我詳細分析今日波段動能榜的第一名股票，說明進出場建議與風險。"
        if st.button("💎 台積電 (2330) 深度籌碼與技術評析", key="m_q3", use_container_width=True):
            preset_q = "請調用工具查詢 2330 台積電的最新籌碼、均線、乖離率與估價水準，並給出操作建議。"
    else:
        col_q1, col_q2, col_q3 = st.columns(3)
        if col_q1.button("📊 今日大盤與法人動向總結"):
            preset_q = "請幫我總結今日台股大盤情勢、漲跌家數與三大法人買賣動向。"
        if col_q2.button("🚀 推薦第一名動能股分析"):
            preset_q = "請調用推薦工具，幫我詳細分析今日波段動能榜的第一名股票，說明進出場建議與風險。"
        if col_q3.button("💎 台積電 (2330) 深度籌碼與技術評析"):
            preset_q = "請調用工具查詢 2330 台積電的最新籌碼、均線、乖離率與估價水準，並給出操作建議。"

    # 對話歷史
    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role": "model", "content": "你好！我是你的 Gemini 台股分析助理。我可以幫你查詢全市場 1800+ 檔股票的最新籌碼、均線位階、估價狀況，或解說今日 Top 動能與價值推薦股。請問你想了解哪一檔標的？"}
        ]

    # 顯示對話歷史
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 處理輸入
    user_input = st.chat_input("請輸入你想詢問的股票代號、名稱或問題 (例: 2317 鴻海現在適合進場嗎？)")
    prompt_to_send = preset_q if preset_q else user_input

    if prompt_to_send:
        # 顯示用戶訊息
        st.session_state["messages"].append({"role": "user", "content": prompt_to_send})
        with st.chat_message("user"):
            st.markdown(prompt_to_send)

        # Gemini 回覆
        with st.chat_message("model"):
            with st.status("🤖 Gemini 正在檢索本地資料庫與深度分析中...", expanded=True) as status_box:
                status_box.write("🔍 連線本地資料庫，檢索即時行情與三大法人籌碼...")
                status_box.write("📐 計算均線、KD、MACD 指標與量價結構...")
                response_text = chat_with_gemini(st.session_state["messages"], api_key=api_key_input, model=gemini_model_choice)
                status_box.update(label="✅ 分析完成！", state="complete", expanded=False)
            st.markdown(response_text)
            st.session_state["messages"].append({"role": "model", "content": response_text})
