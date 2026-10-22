import sqlite3
from unittest.mock import patch
import pytest
import yaml

from src.collect.news_fetcher import NewsFetcher

@pytest.fixture
def test_setup(tmp_path):
    # Setup test DB
    db_file = tmp_path / "test_cse.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE raw_articles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            title TEXT,
            url TEXT UNIQUE ,
            published_at TEXT,
            raw_content TEXT,
            fetched_at TEXT
        );
        CREATE TABLE fetch_log(
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

    # Setup mock source.yaml
    sources_file = tmp_path/ "source.yaml"
    mock_config = {
        "sources": [
            {
                "name": "Mock News",
                 "type": "rss",
                 "url": "https://mocknews.lk/feed",
            }
        ]
    }
    sources_file.write_text(yaml.dump(mock_config), encoding="utf-8")
    return db_file, sources_file

@patch("src.collect.news_fetcher.feedparser.parse")
def test_news_fetcher_run(mock_parse, test_setup):
    db_file, sources_file = test_setup

    class MockEntry:
        def __init__(self, title, link, summary):
            self.title = title
            self.link = link
            self.summary = summary
            self.published = "Wed, 21 Oct 2026 08:30:00 +0530"

    class MockFeed:
        bozo = False
        entries = [
            MockEntry("Commercial Bank reports profit", "https://mocknews.lk/art-1", "Profits rose 12%"),
            MockEntry("Interest rate revised", "https://mocknews.lk/art-2", "CBSL cut rates"),
        ]

    mock_parse.return_value = MockFeed()

    fetcher = NewsFetcher(db_path=db_file, sources_path=sources_file)

    # First run inserts 2 records
    inserted = fetcher.run()
    assert inserted == 2

    # Second run with same feed should insert 0 (URL uniqueness)
    inserted_again = fetcher.run()
    assert inserted_again == 0

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM raw_articles")
    count = cur.fetchone()[0]
    assert count == 2
    conn.close()