"""
News Fetcher for CSE News Agent.
Reads RSS feeds defined in data/sources.yaml (or .yml) and stores new raw articles in SQLite.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List
import feedparser
import yaml

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"


def resolve_default_sources_path() -> Path:
    """Checks for sources.yaml first, then falls back to sources.yml."""
    yaml_path = BASE_DIR / "data" / "sources.yaml"
    yml_path = BASE_DIR / "data" / "sources.yml"
    if yml_path.exists() and not yaml_path.exists():
        return yml_path
    return yaml_path


DEFAULT_SOURCES_PATH = resolve_default_sources_path()


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

            published_at = getattr(entry, "published", "") or getattr(entry, "updated", "")
            if not published_at and hasattr(entry, "published_parsed") and entry.published_parsed:
                published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
            if not published_at:
                published_at = now_utc

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

    def run(self, show_output: bool = False) -> int:
        """Reads all sources and inserts new articles."""
        sources = [s for s in load_sources(self.sources_path) if s.get("type") == "rss"]
        all_articles: List[Dict[str, str]] = []
        source_stats: List[Dict[str, Any]] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            disable=not show_output,
        ) as progress:
            task = progress.add_task("[cyan]Downloading RSS feeds...", total=len(sources))

            for src in sources:
                progress.update(task, description=f"[cyan]Fetching {src['name']}...")
                articles = self.fetch_feed(src)
                all_articles.extend(articles)
                source_stats.append({
                    "name": src["name"],
                    "url": src["url"],
                    "fetched": len(articles),
                })
                progress.advance(task)

        inserted_count = self.save_articles(all_articles)

        if show_output:
            table = Table(title="RSS Feed Collection Summary")
            table.add_column("Source Name", style="bold cyan")
            table.add_column("Feed URL", style="dim")
            table.add_column("Articles Found", justify="right", style="green")

            for stat in source_stats:
                table.add_row(stat["name"], stat["url"], str(stat["fetched"]))

            console.print(table)

            if all_articles:
                console.print("\n[bold cyan]Sample Fetched Articles (pprint preview):[/bold cyan]")
                rpprint(all_articles, max_length=2, max_string=80, expand_all=True)

            console.print(
                f"\n[bold green]✔ Total parsed: {len(all_articles)} | New articles inserted: {inserted_count}[/bold green]"
            )

        return inserted_count


if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Financial News Collector[/bold white]", border_style="cyan"))
    fetcher = NewsFetcher()
    fetcher.run(show_output=True)