import sqlite3
from unittest.mock import patch
import pytest
from src.process.cleaner import ArticleCleaner, strip_html_fallback


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_cse.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE raw_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            title TEXT,
            url TEXT UNIQUE,
            published_at TEXT,
            raw_content TEXT,
            fetched_at TEXT
        );
        INSERT INTO raw_articles (source_name, title, url, published_at, raw_content, fetched_at)
        VALUES
            (
                'EconomyNext',
                'Banks post higher Q3 earnings',
                'https://economynext.com/banks-q3',
                '2026-10-22T08:00:00Z',
                '<p>Sri Lankan <b>commercial banks</b> reported strong profit growth.</p><script>ads()</script>',
                '2026-10-22T08:05:00Z'
            ),
            (
                'Daily FT',
                'Tourism arrivals rise in October',
                'https://ft.lk/tourism-oct',
                '2026-10-22T09:00:00Z',
                '<div>Hotel sector occupancy rose to 75% this month.</div>',
                '2026-10-22T09:05:00Z'
            );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_strip_html_fallback():
    dirty = "<p>Hello   <b>world</b>!</p><script>evil()</script>\n\n<div>More text</div>"
    assert strip_html_fallback(dirty) == "Hello world ! More text"


@patch("src.process.cleaner.ArticleCleaner.fetch_and_extract")
def test_cleaner_run_with_trafilatura_and_fallback(mock_extract, test_db):
    # First article succeeds with trafilatura; second article fails and uses RSS HTML fallback
    mock_extract.side_effect = [
        "Full extracted article body about Sri Lankan commercial banks posting strong Q3 earnings.",
        None,
    ]

    cleaner = ArticleCleaner(db_path=test_db)
    count = cleaner.run(fetch_online=True, show_output=False)
    assert count == 2

    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute("SELECT id, clean_text, clean_method FROM raw_articles ORDER BY id")
    rows = cur.fetchall()
    conn.close()

    assert rows[0][1] == "Full extracted article body about Sri Lankan commercial banks posting strong Q3 earnings."
    assert rows[0][2] == "trafilatura_full"

    assert rows[1][1] == "Hotel sector occupancy rose to 75% this month."
    assert rows[1][2] == "rss_summary_fallback"