"""
News Fetcher for CSE News Agent.
Reads RSS feeds defined in data/sources.yaml and stores new raw articles in SQLite.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List
import feedparser
import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"
DEFAULT_SOURCES_PATH = BASE_DIR / "data" / "sources.yaml"


def load_sources(sources_path: Path = DEFAULT_SOURCES_PATH) -> List[Dict[str, Any]]:
    """Loads configured RSS sources from YAML."""
    if not sources_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {sources_path}")
    with open(sources_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("sources", [])


class NewsFetcher:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH, sources_path: Path = DEFAULT_SOURCES_PATH):
        self.db_path = Path(db_path)
        self.sources_path = Path(sources_path)

    def fetch_feed(self, source: Dict[str, str]) -> List[Dict[str, str]]:
        """Parses an RSS feed and returns structured article entries."""
        name = source["name"]
        url = source["url"]
        logger.info(f"Checking feed: {name} ({url})")

        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning(f"Failed to fetch or parse feed '{name}': {feed.bozo_exception}")
            return []

        articles = []
        now_utc = datetime.now(timezone.utc).isoformat()

        for entry in feed.entries:
            link = getattr(entry, "link", "").strip()
            title = getattr(entry, "title", "").strip()
            if not link or not title:
                continue

            # Fallback published timestamp resolution
            published_at = getattr(entry, "published", "") or getattr(entry, "updated", "")
            if not published_at and hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            if not published_at:
                published_at = now_utc

            # Store the preliminary description/summary; full body will be cleaned in cleaner.py
            raw_content = getattr(entry, "summary", "") or getattr(entry, "description", "")

            articles.append({
                "source_name": name,
                "title": title,
                "url": link,
                "published_at": published_at,
                "raw_content": raw_content,
                "fetched_at": now_utc,
            })

        return articles

    def save_articles(self, articles: List[Dict[str, str]]) -> int:
        """
        Saves articles into raw_articles table.
        Uses INSERT OR IGNORE based on UNIQUE(url) constraint to prevent duplicates.
        """
        if not articles:
            return 0

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        new_count = 0
        try:
            for art in articles:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO raw_articles (
                        source_name, title, url, published_at, raw_content, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        art["source_name"],
                        art["title"],
                        art["url"],
                        art["published_at"],
                        art["raw_content"],
                        art["fetched_at"],
                    ),
                )
                if cur.rowcount > 0:
                    new_count += 1

            # Log execution stats
            cur.execute(
                """
                INSERT INTO fetch_log (source, status, records_fetched, message, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "rss_news",
                    "SUCCESS",
                    new_count,
                    f"Parsed {len(articles)} entries, inserted {new_count} new articles",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        return new_count

    def run(self) -> int:
        """Reads all sources and inserts new articles."""
        sources = load_sources(self.sources_path)
        all_articles = []

        for src in sources:
            if src.get("type") == "rss":
                articles = self.fetch_feed(src)
                all_articles.extend(articles)

        inserted_count = self.save_articles(all_articles)
        logger.info(f"Done. Collected {len(all_articles)} total items, {inserted_count} new entries saved.")
        return inserted_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    fetcher = NewsFetcher()
    fetcher.run()