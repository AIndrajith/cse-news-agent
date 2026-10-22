import sqlite3
from unittest.mock import patch
import pytest
from pydantic import ValidationError

from src.analyse.schema import StoryClassification
from src.analyse.classifier import StoryClassifier


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

        INSERT INTO articles (story_hash, title, clean_text, published_date, primary_source, duplicate_count)
        VALUES
            (
                'hash1',
                'Commercial Bank Q3 profit rises 25%',
                'Commercial Bank of Ceylon PLC reported a 25% increase in net profit for the third quarter.',
                '2026-10-22T08:00:00Z',
                'EconomyNext',
                2
            ),
            (
                'hash2',
                'Central Bank cuts policy interest rate by 50 bps',
                'The Central Bank of Sri Lanka reduced its overnight policy rate to support credit growth.',
                '2026-10-22T09:00:00Z',
                'Daily FT',
                3
            );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_schema_validation_rejects_out_of_bounds_score():
    # Valid classification works and rounds score
    valid = StoryClassification(
        sector="Banks",
        sentiment_score=0.756,
        importance="major",
        is_new=True,
        confidence="HIGH",
        reasoning="Strong third-quarter profit growth across commercial lending.",
        counter_argument="Falling interest rates may narrow net interest margins next quarter.",
    )
    assert valid.sentiment_score == 0.76

    # Invalid score (+2.5) must raise ValidationError
    with pytest.raises(ValidationError):
        StoryClassification(
            sector="Banks",
            sentiment_score=2.5,
            importance="major",
            is_new=True,
            confidence="HIGH",
            reasoning="Valid length reasoning string.",
            counter_argument="Valid length counter argument string.",
        )


@patch.object(StoryClassifier, "classify_story")
def test_classifier_run_saves_to_db(mock_classify, test_db):
    mock_classify.side_effect = [
        StoryClassification(
            sector="Banks",
            sentiment_score=0.8,
            importance="major",
            is_new=True,
            confidence="HIGH",
            reasoning="Commercial Bank reported a 25% jump in Q3 net profit.",
            counter_argument="Higher tax rates could temper full-year earnings.",
        ),
        StoryClassification(
            sector="Market-Wide",
            sentiment_score=0.6,
            importance="major",
            is_new=True,
            confidence="HIGH",
            reasoning="Central Bank cut policy interest rates by 50 bps, lowering borrowing costs.",
            counter_argument="Lower yields may reduce fixed-income returns for savers.",
        ),
    ]

    classifier = StoryClassifier(db_path=test_db, api_key="fake-key")
    count = classifier.run(show_output=False)
    assert count == 2

    # Second run should process 0 because both stories are now classified
    count_again = classifier.run(show_output=False)
    assert count_again == 0

    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute(
        "SELECT article_id, sector, sentiment_score, importance, confidence FROM story_classifications ORDER BY article_id"
    )
    rows = cur.fetchall()
    conn.close()

    assert rows[0] == (1, "Banks", 0.8, "major", "HIGH")
    assert rows[1] == (2, "Market-Wide", 0.6, "major", "HIGH")