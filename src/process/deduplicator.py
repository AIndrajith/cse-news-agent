"""
Article Deduplicator for CSE News Agent.
Groups repeated news stories across different websites using rapidfuzz
so that the same news event is counted only once before AI classification.
"""

import hashlib
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Tuple

from rapidfuzz import fuzz
from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"

def normalize_text(text: str) -> str:
    """
    Lowercases text, removes common source suffixes, and strips punctuation
    so fuzzy comparison focuses on meaningful words.
    """

    if not text:
        return ""
    text = text.lower()
    # strip trailing site names often appended to titles
    text = re.sub(r"\s*[-|]\s*(economynext|daily ft|daily mirror|lbo|ada derana).*$", "", text)
    # Keep alphanumeric characters and spaces
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def make_story_hash(title: str) -> str:
    """Creates a deterministic SHA-256 hash from a normalized title."""
    norm = normalize_text(title)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

def compute_similarity(
        title_a: str, text_a: str, title_b: str, text_b: str
) -> float:
    """
    Compute a 0-100 similarity score between two articles.
    Weights title similarity (70%) and lead paragraph similarity (30%).
    """
    norm_title_a = normalize_text(title_a)
    norm_title_b = normalize_text(title_b)

    if not norm_title_a or not norm_title_b:
        return 0.0

    title_score = fuzz.token_set_ratio(norm_title_b, norm_title_a)

    # compare the first 300 characters of clean text if available
    lead_a = normalize_text((text_a or "")[:300])
    lead_b = normalize_text((text_b or "")[:300])

    if lead_a and lead_b:
        lead_score = fuzz.token_set_ratio(lead_a, lead_b)
        return (0.7 * title_score) + (0.3 * lead_score)

    return float(title_score)

class ArticleDeduplicator:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        similarity_threshold: float = 75.0,
    ):
        self.db_path = Path(db_path)
        self.similarity_threshold = similarity_threshold
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """
        Ensures the articles table exists and raw_articles has a story_id column
        to track which raw articles have already been deduplicated.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    story_hash TEXT UNIQUE,
                    title TEXT,
                    clean_text TEXT,
                    published_date TEXT,
                    primary_source TEXT,
                    duplicate_count INTEGER DEFAULT 1
                )
                """
            )
            cur.execute("PRAGMA table_info(raw_articles)")
            columns = {row[1] for row in cur.fetchall()}
            if "clean_text" not in columns:
                cur.execute("ALTER TABLE raw_articles ADD COLUMN clean_text TEXT")
            if "story_id" not in columns:
                cur.execute("ALTER TABLE raw_articles ADD COLUMN story_id INTEGER")
            conn.commit()
        finally:
            conn.close()

    def get_pending_raw_articles(self) -> List[Dict[str, Any]]:
        """Fetches cleaned raw_articles that have not been assigned a story_id yet."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, source_name, title, published_at, clean_text
                FROM raw_articles
                WHERE story_id IS NULL
                    AND clean_text IS NOT NULL
                    AND clean_text != ''
                ORDER BY id ASC
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_existing_stories(self) -> List[Dict[str, Any]]:
        """Load recent canonical stories from the articles table for comparison."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, story_hash, title, clean_text, published_date, primary_source, duplicate_count
                FROM articles
                ORDER BY id DESC 
                LIMIT 500
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def run(self, show_output: bool = False) -> Tuple[int, int]:
        """
        Deduplicates pending raw articles against existing stories and each other.
        Returns (new_unique_stories_count, duplicate_articles_merged_count).
        """
        pending = self.get_pending_raw_articles()
        if not pending:
            if show_output:
                console.print("[yellow]No pending cleaned articles to deduplicate.[/yellow]")
            return 0, 0

        existing_stories = self.get_existing_stories()
        new_stories_count = 0
        merged_duplicates_count = 0
        touched_stories: Dict[int, Dict[str, Any]] = {}

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
                disable=not show_output,
            ) as progress:
                task = progress.add_task("[cyan]Deduplicating articles...", total=len(pending))

                for raw in pending:
                    short_title = raw["title"][:40]
                    progress.update(task, description=f"[cyan]Checking: {short_title}")

                    best_match = None
                    best_score = 0.0

                    for story in existing_stories:
                        score = compute_similarity(
                            raw["title"],
                            raw["clean_text"],
                            story["title"],
                            story["clean_text"],
                        )
                        if score > best_score:
                            best_score = score
                            best_match = story

                    if best_match and best_score >= self.similarity_threshold:
                        # Duplicate found: increment count and keep the richest text
                        story_id = best_match["id"]
                        best_match["duplicate_count"] += 1

                        if len(raw["clean_text"] or "") > len(best_match["clean_text"] or ""):
                            best_match["clean_text"] = raw["clean_text"]

                        cur.execute(
                            """
                            UPDATE articles
                            SET duplicate_count = ?, clean_text = ?
                            WHERE id = ?
                            """,
                            (best_match["duplicate_count"], best_match["clean_text"], story_id),
                        )
                        cur.execute(
                            "UPDATE raw_articles SET story_id = ? WHERE id = ?",
                            (story_id, raw["id"]),
                        )
                        merged_duplicates_count += 1
                        touched_stories[story_id] = best_match
                    else:
                        # Brand new unique story
                        story_hash = make_story_hash(raw["title"])
                        # Ensure hash uniqueness even if two short titles collide
                        cur.execute("SELECT id FROM articles WHERE story_hash = ?", (story_hash,))
                        if cur.fetchone():
                            story_hash = f"{story_hash}_{raw['id']}"

                        cur.execute(
                            """
                            INSERT INTO articles(
                                story_hash, title, clean_text, published_date, primary_source, duplicate_count
                            ) VALUES (?, ?, ?, ?, ?, 1)
                            """,
                            (
                                story_hash,
                                raw["title"],
                                raw["clean_text"],
                                raw["published_at"],
                                raw["source_name"]
                            ),
                        )
                        story_id = cur.lastrowid
                        cur.execute(
                            "UPDATE raw_articles SET story_id = ? WHERE id = ?",
                            (story_id, raw["id"]),
                        )

                        new_story = {
                            "id": story_id,
                            "story_hash": story_hash,
                            "title":raw["title"],
                            "clean_text":raw["clean_text"],
                            "published_date": raw["published_at"],
                            "primary_source": raw["source_name"],
                            "duplicate_count": 1,
                        }
                        existing_stories.append(new_story)
                        new_stories_count += 1
                        touched_stories[story_id] = new_story

                    progress.advance(task)

            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

        if show_output:
            stories_list = sorted(
                touched_stories.values(),
                key=lambda x:x["duplicate_count"],
                reverse=True,
            )

            table = Table(title="Deduplicated Stories Summary")
            table.add_column("Story ID", justify="right", style="dim")
            table.add_column("Primary Source", style="bold cyan")
            table.add_column("Canonical Title", style="white")
            table.add_column("Sighting", justify="right", style="yellow")

            for st in stories_list[:12]:
                sightings = (
                    f"[bold green]{st['duplicate_count']} sites [/bold green]"
                    if st["duplicate_count"] > 1
                    else "1 site"
                )
                table.add_row(
                    str(st["id"]),
                    st["primary_source"],
                    st["title"][:50],
                    sightings,
                )

            console.print(table)
            console.print("\n[bold cyan]Sample Canonical Stories (pprint preview):[/bold cyan]")
            rpprint(stories_list, max_length=2, max_string=100, expand_all=True)
            console.print(
                f"\n[bold green]✔ Processed {len(pending)} articles -> "
                f"{new_stories_count} unique stories, {merged_duplicates_count} duplicates merged.[/bold green]"
            )

        return new_stories_count, merged_duplicates_count

if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Story Deduplicator[/bold white]", border_style="cyan"))
    deduplicator = ArticleDeduplicator()
    deduplicator.run(show_output=True)