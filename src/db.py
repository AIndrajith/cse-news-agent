"""
Database schema definition and connection helpers for SQLite.
Creates all required tables for prices, market summaries, logs, and future pipeline stages.
"""

from pathlib import Path
import sqlite3

# Resolve the default database path: cse-news-agent/data/cse.db
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "cse.db"

SCHEMA = """
-- Daily overall market metrics from dailyMarketSummery
CREATE TABLE IF NOT EXISTS daily_market (
    trade_date TEXT PRIMARY KEY,
    aspi REAL,
    snp_sl20 REAL,
    market_turnover REAL,
    market_cap REAL,
    companies_traded INTEGER,
    fetched_at TEXT
);

-- Daily individual company trades from detailedTrades
CREATE TABLE IF NOT EXISTS daily_prices (
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

-- Operational audit trail to detect silent fetch failures
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    status TEXT,
    records_fetched INTEGER,
    message TEXT,
    fetched_at TEXT
);

-- Stores fetched raw news articles (for news_fetcher.py)
CREATE TABLE IF NOT EXISTS raw_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT,
    title TEXT,
    url TEXT UNIQUE,
    published_at TEXT,
    raw_content TEXT,
    fetched_at TEXT
);

-- Stores processed & deduplicated stories (for cleaner.py / deduplicator.py)
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_hash TEXT UNIQUE,
    title TEXT,
    clean_text TEXT,
    published_date TEXT,
    primary_source TEXT,
    duplicate_count INTEGER DEFAULT 1
);

-- Stores AI classification outputs (for classifier.py)
CREATE TABLE IF NOT EXISTS story_classifications (
    article_id INTEGER PRIMARY KEY,
    sector TEXT,
    sentiment_score REAL,
    importance TEXT,
    reasoning TEXT,
    classified_at TEXT,
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

-- Stores daily sector aggregate results (for scorer.py)
CREATE TABLE IF NOT EXISTS sector_daily_scores (
    trade_date TEXT,
    sector TEXT,
    score REAL,
    article_count INTEGER,
    arguments_for TEXT,
    arguments_against TEXT,
    confidence TEXT,
    PRIMARY KEY (trade_date, sector)
);
"""


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection with row factory enabled."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Initializes the database schema and creates all tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    print(f"Database successfully initialized at: {db_path.resolve()}")


if __name__ == "__main__":
    init_db()