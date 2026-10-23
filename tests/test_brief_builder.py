import sqlite3
import pytest
from src.report.brief_builder import BriefBuilder, format_sector_block


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_cse.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_hash TEXT UNIQUE,
            title TEXT,
            clean_text TEXT,
            published_date TEXT,
            primary_source TEXT,
            duplicate_count INTEGER DEFAULT 1
        );

        CREATE TABLE story_classifications (
            article_id INTEGER PRIMARY KEY,
            sector TEXT,
            sentiment_score REAL,
            importance TEXT,
            is_new INTEGER DEFAULT 1,
            confidence TEXT,
            reasoning TEXT,
            counter_argument TEXT,
            classified_at TEXT
        );

        CREATE TABLE sector_daily_scores (
            trade_date TEXT,
            sector TEXT,
            score REAL,
            article_count INTEGER,
            arguments_for TEXT,
            arguments_against TEXT,
            confidence TEXT,
            PRIMARY KEY (trade_date, sector)
        );

        INSERT INTO articles (id, story_hash, title, clean_text, primary_source, duplicate_count)
        VALUES (1, 'h1', 'Commercial Bank Q3 profit rises 25%', 'Clean text', 'EconomyNext', 3);

        INSERT INTO story_classifications (
            article_id, sector, sentiment_score, importance, is_new, confidence, reasoning, counter_argument
        ) VALUES (
            1, 'Banks', 0.62, 'major', 1, 'MEDIUM',
            '3 companies reported higher profits; interest rate was cut',
            'bank margins shrink when rates fall; sector already up 8% this week'
        );

        INSERT INTO sector_daily_scores (
            trade_date, sector, score, article_count, arguments_for, arguments_against, confidence
        ) VALUES (
            '2026-10-23',
            'Banks',
            0.62,
            4,
            '3 companies reported higher profits; interest rate was cut',
            'bank margins shrink when rates fall; sector already up 8% this week',
            'MEDIUM'
        );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_format_sector_block_matches_figure_2():
    row = {
        "sector": "Banks",
        "score": 0.62,
        "article_count": 4,
        "arguments_for": "3 companies reported higher profits; interest rate was cut",
        "arguments_against": "bank margins shrink when rates fall; sector already up 8% this week",
        "confidence": "MEDIUM",
    }
    block = format_sector_block(row)
    assert "Banks — positive (+0.62)" in block
    assert "In favour:  3 companies reported higher profits; interest rate was cut" in block
    assert "Against:    bank margins shrink when rates fall; sector already up 8% this week" in block
    assert "How sure:   MEDIUM" in block
    assert "Based on:   4 news stories" in block


def test_brief_builder_creates_markdown_report(test_db, tmp_path):
    reports_dir = tmp_path / "reports"
    builder = BriefBuilder(db_path=test_db, reports_dir=reports_dir)
    report_path = builder.run(trade_date="2026-10-23", show_output=False)

    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")

    # Verify required sections and disclaimer
    assert "NOT investment advice" in content
    assert "Banks — positive (+0.62)" in content
    assert "Commercial Bank Q3 profit rises 25%" in content