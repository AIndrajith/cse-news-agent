import sqlite3
import pytest
from src.process.deduplicator import ArticleDeduplicator, compute_similarity


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
            fetched_at TEXT,
            clean_text TEXT,
            clean_method TEXT
        );

        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_hash TEXT UNIQUE,
            title TEXT,
            clean_text TEXT,
            published_date TEXT,
            primary_source TEXT,
            duplicate_count INTEGER DEFAULT 1
        );

        INSERT INTO raw_articles (source_name, title, url, published_at, clean_text)
        VALUES
            (
                'EconomyNext',
                'Commercial Bank Q3 net profit rises 25% on lower impairments',
                'https://economynext.com/comb-q3',
                '2026-10-22T08:00:00Z',
                'Commercial Bank of Ceylon reported a 25 percent increase in third quarter net profit.'
            ),
            (
                'Daily FT',
                'Commercial Bank posts 25% rise in Q3 net profit',
                'https://ft.lk/comb-q3-profit',
                '2026-10-22T09:15:00Z',
                'Commercial Bank of Ceylon PLC posted a 25 percent rise in Q3 net profit driven by lower impairment charges.'
            ),
            (
                'Daily Mirror',
                'Sri Lanka tourism earnings cross $2.5 billion in October',
                'https://dailymirror.lk/tourism-oct',
                '2026-10-22T10:00:00Z',
                'Tourist arrivals and hotel bookings pushed cumulative tourism revenue above 2.5 billion dollars.'
            );
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_compute_similarity_matches_same_story():
    score_same = compute_similarity(
        "Sampath Bank Q3 profit jumps 30% - EconomyNext",
        "Sampath Bank reported a 30% jump in third quarter profit.",
        "Sampath Bank posts 30% jump in Q3 profit | Daily FT",
        "Third quarter profit at Sampath Bank jumped 30 percent this year.",
    )
    score_different = compute_similarity(
        "Sampath Bank Q3 profit jumps 30%",
        "Sampath Bank reported a 30% jump in third quarter profit.",
        "Tokyo Cement opens new factory in Trincomalee",
        "Construction sector demand boosts cement production capacity.",
    )
    assert score_same >= 75.0
    assert score_different < 50.0


def test_deduplicator_groups_duplicates_and_is_idempotent(test_db):
    dedup = ArticleDeduplicator(db_path=test_db, similarity_threshold=75.0)

    # First run: 3 raw articles -> 2 unique stories, 1 merged duplicate
    new_stories, merged_dups = dedup.run(show_output=False)
    assert new_stories == 2
    assert merged_dups == 1

    # Second run: everything already has a story_id -> 0 new, 0 merged
    new_again, merged_again = dedup.run(show_output=False)
    assert new_again == 0
    assert merged_again == 0

    conn = sqlite3.connect(test_db)
    cur = conn.cursor()
    cur.execute("SELECT title, duplicate_count, clean_text FROM articles ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 2
    # Story 1 (Commercial Bank) was seen on 2 sites and kept the longer clean_text
    assert rows[0][1] == 2
    assert "driven by lower impairment charges" in rows[0][2]
    # Story 2 (Tourism) was seen on 1 site
    assert rows[1][1] == 1