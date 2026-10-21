import sqlite3
from unittest.mock import patch
import pytest

from src.collect.price_fetcher import parse_cse_timestamp, PriceFetcher


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_cse.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE daily_market (
            trade_date TEXT PRIMARY KEY,
            aspi REAL,
            snp_sl20 REAL,
            market_turnover REAL,
            market_cap REAL,
            companies_traded INTEGER,
            fetched_at TEXT
        );
        CREATE TABLE daily_prices (
            symbol TEXT,
            trade_date TEXT,
            company_name TEXT,
            close_price REAL,
            change_amount REAL,
            change_pct REAL,
            share_volume REAL,
            trade_count INTEGER,
            turnover REAL,
            fetched_at TEXT,
            PRIMARY KEY (symbol, trade_date)
        );
        CREATE TABLE fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            status TEXT,
            records_fetched INTEGER,
            message TEXT,
            fetched_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_parse_cse_timestamp_colombo_boundary():
    # 1754505000000 = 2025-08-06 18:30:00 UTC = 2025-08-07 00:00:00 UTC+5:30
    epoch_ms = 1754505000000
    assert parse_cse_timestamp(epoch_ms) == "2025-08-07"


@patch("src.collect.price_fetcher.PriceFetcher._post")
def test_price_fetcher_run(mock_post, temp_db, tmp_path):
    mock_market_json = {
        "tradeDate": 1754505000000,
        "asi": 12500.5,
        "spp": 3800.2,
        "marketTurnover": 150000000.0,
        "marketCap": 4500000000000.0,
        "tradeCompanyNumber": 210,
    }
    mock_trades_json = {
        "reqDetailTrades": [
            {
                "symbol": "COMB.N0000",
                "name": "COMMERCIAL BANK OF CEYLON PLC",
                "price": 115.5,
                "qty": 50000,
                "trades": 120,
                "change": 1.5,
                "changePercentage": 1.32,
            }
        ]
    }

    mock_post.side_effect = [mock_market_json, mock_trades_json]

    snapshots_dir = tmp_path / "snapshots"
    fetcher = PriceFetcher(db_path=temp_db, snapshots_dir=snapshots_dir)
    trade_date = fetcher.run()

    assert trade_date == "2025-08-07"

    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()

    cur.execute("SELECT trade_date, aspi FROM daily_market")
    market = cur.fetchone()
    assert market == ("2025-08-07", 12500.5)

    cur.execute("SELECT symbol, close_price, share_volume FROM daily_prices")
    price = cur.fetchone()
    assert price == ("COMB.N0000", 115.5, 50000.0)

    cur.execute("SELECT status, records_fetched FROM fetch_log")
    log = cur.fetchone()
    assert log == ("SUCCESS", 1)

    conn.close()

    assert (snapshots_dir / "2025-08-07_market_summary.json").exists()
    assert (snapshots_dir / "2025-08-07_detailed_trades.json").exists()