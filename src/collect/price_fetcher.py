"""
CSE Daily Price Collector
Fetches daily market overview and closing equity trades from CSE endpoints,
resolves Sri Lanka midnight timestamps properly, saves raw audit snapshots,
and records daily closing prices to SQLite.
"""

from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

# Sri Lanka Standard Time: UTC+05:30
SL_TZ = timezone(timedelta(hours=5, minutes=30))

BASE_URL = "https://www.cse.lk/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def parse_cse_timestamp(epoch_ms: int) -> str:
    """
    Convert CSE epoch millisecond timestamp to 'YYYY-MM-DD' in UTC+05:30.
    Prevents UTC conversion from shifting Sri Lanka midnight back by one day.
    """
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=SL_TZ)
    return dt.strftime("%Y-%m-%d")


def save_raw_snapshot(snapshot_dir: Path, filename: str, data: Any) -> Path:
    """Save raw response as insurance against upstream schema changes."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    file_path = snapshot_dir / filename
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return file_path


class PriceFetcher:
    def __init__(self, db_path: Path, snapshots_dir: Path, timeout: int = 20):
        self.db_path = Path(db_path)
        self.snapshots_dir = Path(snapshots_dir)
        self.timeout = timeout

    def _post(self, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{BASE_URL}/{endpoint}"
        resp = requests.post(url, data=payload or {}, headers=HEADERS, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_market_summary(self) -> Tuple[str, Dict[str, Any], Any]:
        """
        Fetches dailyMarketSummery.
        Unwraps nested list layers ([[ { ... } ]]) returned by CSE.
        Returns:
            trade_date (YYYY-MM-DD),
            parsed market data dict,
            raw JSON response.
        """
        raw_data = self._post("dailyMarketSummery")

        # Unwrap nested lists until we reach the dictionary payload
        item = raw_data
        while isinstance(item, list):
            if not item:
                raise ValueError("Endpoint dailyMarketSummery returned an empty list")
            item = item[0]

        if not isinstance(item, dict):
            raise TypeError(f"Unexpected response structure from dailyMarketSummery: {type(item)}")

        raw_trade_date = item.get("tradeDate")
        if not raw_trade_date:
            raise ValueError(f"Endpoint dailyMarketSummery item missing 'tradeDate': {item}")

        trade_date = parse_cse_timestamp(int(raw_trade_date))

        market_record = {
            "trade_date": trade_date,
            "aspi": float(item.get("asi", 0.0) or 0.0),
            "snp_sl20": float(item.get("spp", 0.0) or 0.0),
            "market_turnover": float(item.get("marketTurnover", 0.0) or 0.0),
            "market_cap": float(item.get("marketCap", 0.0) or 0.0),
            "companies_traded": int(item.get("tradeCompanyNumber", 0) or 0),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        return trade_date, market_record, raw_data

    def fetch_detailed_trades(self, trade_date: str) -> Tuple[List[Dict[str, Any]], Any]:
        """
        Fetches detailedTrades for all listed companies.
        Handles both direct list responses and dictionary wrappers.
        Returns:
            list of parsed price records,
            raw JSON response.
        """
        raw_data = self._post("detailedTrades")

        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict):
            items = raw_data.get("reqDetailTrades") or raw_data.get("reqTradeSummery") or []
        else:
            items = []

        records = []
        now_utc = datetime.now(timezone.utc).isoformat()

        for row in items:
            symbol = row.get("symbol")
            if not symbol:
                continue

            price = float(row.get("price", 0.0) or 0.0)
            qty = float(row.get("qty", 0.0) or 0.0)
            turnover = float(row.get("turnover", price * qty) or 0.0)

            records.append({
                "symbol": symbol.strip(),
                "trade_date": trade_date,
                "company_name": row.get("name", "").strip(),
                "close_price": price,
                "change_amount": float(row.get("change", 0.0) or 0.0),
                "change_pct": float(row.get("changePercentage", 0.0) or 0.0),
                "share_volume": qty,
                "trade_count": int(row.get("trades", 0) or 0),
                "turnover": turnover,
                "fetched_at": now_utc,
            })

        return records, raw_data

    def save_to_db(self, market_record: Dict[str, Any], price_records: List[Dict[str, Any]]) -> None:
        """Saves daily market and price data in SQLite transactionally."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            # 1. Store daily market summary
            cur.execute(
                """
                INSERT INTO daily_market (trade_date, aspi, snp_sl20, market_turnover, market_cap, companies_traded,
                                          fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(trade_date) DO
                UPDATE SET
                    aspi=excluded.aspi,
                    snp_sl20=excluded.snp_sl20,
                    market_turnover=excluded.market_turnover,
                    market_cap=excluded.market_cap,
                    companies_traded=excluded.companies_traded,
                    fetched_at=excluded.fetched_at
                """,
                (
                    market_record["trade_date"],
                    market_record["aspi"],
                    market_record["snp_sl20"],
                    market_record["market_turnover"],
                    market_record["market_cap"],
                    market_record["companies_traded"],
                    market_record["fetched_at"],
                ),
            )

            # 2. Store individual company closing prices
            cur.executemany(
                """
                INSERT INTO daily_prices (symbol, trade_date, company_name, close_price, change_amount,
                                          change_pct, share_volume, trade_count, turnover, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, trade_date) DO
                UPDATE SET
                    company_name=excluded.company_name,
                    close_price=excluded.close_price,
                    change_amount=excluded.change_amount,
                    change_pct=excluded.change_pct,
                    share_volume=excluded.share_volume,
                    trade_count=excluded.trade_count,
                    turnover=excluded.turnover,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        r["symbol"],
                        r["trade_date"],
                        r["company_name"],
                        r["close_price"],
                        r["change_amount"],
                        r["change_pct"],
                        r["share_volume"],
                        r["trade_count"],
                        r["turnover"],
                        r["fetched_at"],
                    )
                    for r in price_records
                ],
            )

            # 3. Write success log
            cur.execute(
                """
                INSERT INTO fetch_log (source, status, records_fetched, message, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "cse_prices",
                    "SUCCESS",
                    len(price_records),
                    f"Saved {len(price_records)} companies for trade date {market_record['trade_date']}",
                    market_record["fetched_at"],
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def run(self) -> str:
        """Coordinates collection, raw snapshots, and database persistence."""
        logger.info("Starting daily price fetch...")
        trade_date, market_record, raw_market = self.fetch_market_summary()
        price_records, raw_trades = self.fetch_detailed_trades(trade_date)

        # Write audit snapshots
        save_raw_snapshot(self.snapshots_dir, f"{trade_date}_market_summary.json", raw_market)
        save_raw_snapshot(self.snapshots_dir, f"{trade_date}_detailed_trades.json", raw_trades)

        # Commit to database
        self.save_to_db(market_record, price_records)
        logger.info(f"Successfully processed {len(price_records)} records for {trade_date}")
        return trade_date


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    base_dir = Path(__file__).resolve().parents[2]
    db_file = base_dir / "data" / "cse.db"
    snapshots_dir = base_dir / "data" / "snapshots"

    fetcher = PriceFetcher(db_path=db_file, snapshots_dir=snapshots_dir)
    fetcher.run()