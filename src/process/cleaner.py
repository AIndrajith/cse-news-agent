"""
Article Cleaner for CSE News Agent.
Downloads full article text using trafilatura (removing ads, menus, and boilerplate),
falls back to BeautifulSoup HTML stripping on RSS summaries if needed,
and stores the clean text in SQLite.
"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
import requests
import trafilatura

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

def strip_html_fallback(raw_html: str) -> str:
    """
    Removes HTML tags, scripts, and extra whitespaces from raw RSS summary text.
    Used when full-page extraction is unavailable or fails.
    """
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    # collapse multiple spaces/ newlines into single space
    return re.sub(r"\s+", " ", text).strip()

class ArticleCleaner:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH, timeout:int = 15):
        self.db_path = Path(db_path)
        self.timeout = timeout
        self._ensure_clean_text_column()

    def _ensure_clean_text_column(self) -> None:
        """Adds clean_text and cleaned_method columns to raw_articles if then don't exist yet."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA table_info(raw_articles)")
            columns = {row[1] for row in cur.fetchall()}
            if "clean_text" not in columns:
                cur.execute("ALTER TABLE raw_articles ADD COLUMN clean_text TEXT")
            if "clean_method" not in columns:
                cur.execute("ALTER TABLE raw_articles ADD COLUMN clean_method TEXT")
            conn.commit()
        finally:
            conn.close()

    def fetch_and_extract(self, url: str) -> Optional[str]:
        """
        Downloads the article webpage and uses trafilatura to extract only th main story text.
        Returns None if download or extraction fails.
        """
        try:
            resp = requests.get(url, headers=HEADERS, timeout=self.timeout)
            resp.raise_for_status()
            extracted = trafilatura.extract(
                resp.text,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if extracted:
                return re.sub(r"\s+", " ", extracted).strip()
        except Exception as e:
            logger.debug(f"Full extraction failed for {url}: {e}")
        return None

    def clean_single_article(self, url: str, raw_content: str, fetch_online: bool = True) -> tuple[str, str]:
        """
        Cleans on article.
        Tries full webpage extraction first; falls back to RSS summary HTML stripping.
        Returns (clean_text, method_used).
        """
        if fetch_online and url:
            full_text = self.fetch_and_extract(url)
            if full_text and len(full_text) >= 40:
                return full_text, "trafilatura_full"

        fallback_text = strip_html_fallback(raw_content)
        return fallback_text, "rss_summary_fallback"

    def get_uncleaned_articles(self) -> List[Dict[str, Any]]:
        """Fetches rows from raw_articles that have not been cleaned yet."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT id, source_name, title, url, raw_content
                FROM raw_articles
                WHERE clean_text IS NULL OR clean_text = ''
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def save_cleaned_articles(self, cleaned_rows: List[Dict[str, Any]]) -> int:
        """Updates raw_articles with cleaned text and extraction method."""
        if not cleaned_rows:
            return 0

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.executemany(
                """
                UPDATE raw_articles
                SET clean_text = ?, clean_method = ?
                WHERE id = ?
                """,
                [(r["clean_text"], r["clean_method"], r["id"]) for r in cleaned_rows],
            )
            conn.commit()
            return len(cleaned_rows)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def run(self, fetch_online: bool = True, show_output: bool = False) -> int:
        """Finds uncleaned articles, cleans them, and saves them back to SQLite."""
        pending = self.get_uncleaned_articles()
        if not pending:
            if show_output:
                console.print("[yellow]No uncleaned articles found in raw_articles.[/yellow]")
            return 0

        cleaned_results: List[Dict[str, Any]] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            disable=not show_output,
        ) as progress:
            task = progress.add_task("[cyan]Cleaning articles...", total=len(pending))

            for item in pending:
                short_title = item["title"][:40]
                progress.update(task, description=f"[cyan]Cleaning: {short_title}...")

                clean_text, method = self.clean_single_article(
                    url=item["url"],
                    raw_content=item["raw_content"] or "",
                    fetch_online=fetch_online
                )

                # If both body and summary were empty, fal back to the title so it's never blank
                if not clean_text:
                    clean_text = item["title"]
                    method = "title_only"

                cleaned_results.append({
                    "id": item["id"],
                    "source_name": item["source_name"],
                    "title":item["title"],
                    "clean_text": clean_text,
                    "clean_method": method
                })
                progress.advance(task)

        update_count = self.save_cleaned_articles(cleaned_results)

        if show_output:
            table = Table(title="Article Cleaning Summary")
            table.add_column("ID", justify="right", style="dim")
            table.add_column("Source", style="bold cyan")
            table.add_column("Title", style="white")
            table.add_column("Method", style="yellow")
            table.add_column("Clean Length", justify="right", style="green")

            for row in cleaned_results[:10]:
                table.add_row(
                    str(row["id"]),
                    row["source_name"],
                    row["title"][:45],
                    row["clean_method"],
                    f"{len(row['clean_text'])} chars"
                )

            console.print(table)
            console.print("\n[bold cyan]Sample Cleaned Output (pprint preview):[/bold cyan]")
            rpprint(cleaned_results, max_length=2, max_string=120, expand_all=True)
            console.print(f"\n[bold green]✔ Successfully cleaned {update_count} articles.[/bold green]")

        return update_count

if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Article Cleaner[/bold white]", border_style="cyan"))
    cleaner = ArticleCleaner()
    cleaner.run(fetch_online=True, show_output=True)