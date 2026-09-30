"""
台股每日自動定時排程模組 (預設於每個交易日 15:30 執行)
可用於 Windows 工作排程器 (Task Scheduler) 或獨立背景常駐
"""
import datetime
import time
import logging
from pathlib import Path
from crawler import run_daily_crawler, sync_history_for_symbols
from engine import batch_update_all_indicators, run_recommendation_strategies
from database import get_all_watchlist_symbols
from config import BASE_DIR

LOG_FILE = BASE_DIR / "scheduler.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def daily_pipeline():
    today = datetime.date.today()
    # 週末不執行
    if today.weekday() >= 5:
        logging.info(f"今日 ({today}) 為週末休市，不執行排程採集。")
        return

    today_str = today.strftime("%Y-%m-%d")
    logging.info(f"=== 開始執行盤後全自動作業: {today_str} ===")

    # 1. 爬取 1800+ 檔上市櫃收盤與三大法人
    count = run_daily_crawler(today_str)
    if count > 0:
        # 2. 自動回補所有用戶自選股近期數據以防缺漏
        symbols = get_all_watchlist_symbols()
        sync_history_for_symbols(symbols, period="1mo")

        # 3. 批量更新技術與籌碼指標
        batch_update_all_indicators()

        # 4. 運行雙向選股推薦演算法 (Top 5 / 10 / 20)
        run_recommendation_strategies(today_str)

        logging.info(f"=== 盤後全自動作業順利完成！===")
    else:
        logging.warning("今日尚無結算資料或非交易日。")

if __name__ == "__main__":
    daily_pipeline()
