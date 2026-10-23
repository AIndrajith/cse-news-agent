import sqlite3
import pytest
from src.analyse.scorer import (
    SectorScorer,
    calculate_weighted_score,
    compute_sector_confidence,
)


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

        INSERT INTO articles (id, story_hash, title, clean_text)
        VALUES
            (1, 'h1', 'Commercial Bank profit rises', 'Clean body 1'),
            (2, 'h2', 'Sampath Bank net income grows', 'Clean body 2'),
            (3, 'h3', 'Sports news cricket match', 'Clean body 3');

        INSERT INTO story_classifications (
            article_id, sector, sentiment_score, importance, is_new, confidence, reasoning, counter_argument
        ) VALUES
            (
                1, 'Banks', 0.8, 'major', 1, 'HIGH',
                '3 companies reported higher profits',
                'Bank margins shrink when rates fall'
            ),
            (
                2, 'Banks', 0.4, 'moderate', 1, 'MEDIUM',
                'Loan growth recovered in Q3',
                'NPL ratios remain watched'
            ),
            (
                3, 'Unrelated', 0.0, 'trivial', 1, 'HIGH',
                'Sports story unrelated to CSE',
                'No market impact'
            );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_calculate_weighted_score_math():
    stories = [
        {"sentiment_score": 0.8, "importance": "major", "is_new": 1},     # weight 2.0 -> 1.6
        {"sentiment_score": 0.2, "importance": "moderate", "is_new": 1},  # weight 1.0 -> 0.2
    ]
    # (1.6 + 0.2) / (2.0 + 1.0) = 1.8 / 3.0 = 0.60
    assert calculate_weighted_score(stories) == 0.60


def test_compute_sector_confidence_caps_single_story():
    # Even if 1 story says HIGH, single-story sector confidence is capped at MEDIUM
    assert compute_sector_confidence(["HIGH"], article_count=1) == "MEDIUM"
    # Multiple HIGH stories achieve HIGH confidence
    assert compute_sector_confidence(["HIGH", "HIGH"], article_count=2) == "HIGH"


def test_scorer_run_aggregates_and_ignores_unrelated(test_db):
    scorer = SectorScorer(db_path=test_db)
    results = scorer.run(trade_date="2026-10-23", show_output=False)

    # 'Unrelated' must be excluded; only 'Banks' should be scored
    assert len(results) == 1
    banks = results[0]
    assert banks["sector"] == "Banks"
    assert banks["article_count"] == 2
    assert banks["score"] == 0.67  # (0.8*2 + 0.4*1) / 3 = 2.0 / 3 = 0.67
    assert "3 companies reported higher profits" in banks["arguments_for"]
    assert "Bank margins shrink when rates fall" in banks["arguments_against"]