"""
AI Story Classifier for CSE News Agent.
Reads deduplicated stories from SQLite, asks the LLM to classify each story
using the Pydantic StoryClassification schema, and stores the results.
"""

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from src.analyse.schema import StoryClassification

load_dotenv()
logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"

SYSTEM_PROMPT = """You are an objective financial news analyst for the Colombo Stock Exchange (CSE) in Sri Lanka.
Classify the news story strictly into the requested JSON schema.

Rules:
1. NEVER give investment advice, never say 'buy' or 'sell', and never predict share price percentages.
2. Assign the single best CSE sector. Use 'Market-Wide' for Central Bank (CBSL) interest rates, inflation, IMF, taxes, or LKR exchange rates. Use 'Unrelated' for non-business news.
3. Score sentiment from -1.0 (very negative) to +1.0 (very positive).
4. Set is_new=False if the story repeats an event in RECENT HEADLINES.
5. Provide one concise sentence for 'reasoning' and one concise sentence for 'counter_argument'.
6. Set confidence to 'LOW', 'MEDIUM', or 'HIGH'.
"""


class StoryClassifier:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        model_name: str = "gemini-3.1-flash",
        fallback_model: str = "gemini-2.5-flash-lite",
        api_key: Optional[str] = None,
        request_delay: float = 2.0,
    ):
        self.db_path = Path(db_path)
        self.model_name = model_name
        self.fallback_model = fallback_model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.request_delay = request_delay
        self._client = None
        self._ensure_schema()

    @property
    def client(self) -> genai.Client:
        """Lazily initializes the Gemini client with a strict 30-second HTTP timeout."""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "GEMINI_API_KEY is missing. Add it to your .env file or pass api_key to StoryClassifier."
                )
            # timeout is in milliseconds (30,000 ms = 30 seconds) so a hung connection never freezes
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=30000),
            )
        return self._client

    def _ensure_schema(self) -> None:
        """Ensures story_classifications table and all required columns exist."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS story_classifications (
                    article_id INTEGER PRIMARY KEY,
                    sector TEXT,
                    sentiment_score REAL,
                    importance TEXT,
                    is_new INTEGER DEFAULT 1,
                    confidence TEXT,
                    reasoning TEXT,
                    counter_argument TEXT,
                    classified_at TEXT,
                    FOREIGN KEY(article_id) REFERENCES articles(id)
                )
                """
            )
            cur.execute("PRAGMA table_info(story_classifications)")
            columns = {row[1] for row in cur.fetchall()}
            if "is_new" not in columns:
                cur.execute("ALTER TABLE story_classifications ADD COLUMN is_new INTEGER DEFAULT 1")
            if "confidence" not in columns:
                cur.execute("ALTER TABLE story_classifications ADD COLUMN confidence TEXT")
            if "counter_argument" not in columns:
                cur.execute("ALTER TABLE story_classifications ADD COLUMN counter_argument TEXT")
            if "classified_at" not in columns:
                cur.execute("ALTER TABLE story_classifications ADD COLUMN classified_at TEXT")
            conn.commit()
        finally:
            conn.close()

    def get_unclassified_stories(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Fetches deduplicated stories from 'articles' that have not been classified yet."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT a.id, a.title, a.clean_text, a.published_date, a.primary_source
                FROM articles a
                LEFT JOIN story_classifications sc ON a.id = sc.article_id
                WHERE sc.article_id IS NULL
                ORDER BY a.id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_recent_titles(self, exclude_id: int, limit: int = 8) -> List[str]:
        """
        Fetches recent already-classified headlines from the past 7 days.
        Uses sc.classified_at (ISO-8601) because RSS published_date formats vary.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT a.title
                FROM articles a
                INNER JOIN story_classifications sc ON a.id = sc.article_id
                WHERE a.id != ?
                  AND (
                      sc.classified_at IS NULL
                      OR datetime(sc.classified_at) >= datetime('now', '-7 days')
                  )
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (exclude_id, limit),
            )
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    def classify_story(
        self,
        title: str,
        clean_text: str,
        recent_titles: Optional[List[str]] = None,
    ) -> StoryClassification:
        """
        Calls the LLM with structured Pydantic output to classify one story.
        Disables thinking tokens for speed and falls back to flash-lite if 503 busy.
        """
        recent_block = (
            "\n".join(f"- {t}" for t in recent_titles[:8])
            if recent_titles
            else "None"
        )
        # First 1,500 chars (~250 words) contain the lead and key financial facts
        body_snippet = (clean_text or "")[:1500]
        prompt = (
            f"RECENT HEADLINES (last 7 days):\n{recent_block}\n\n"
            f"STORY TO CLASSIFY:\n"
            f"Headline: {title}\n"
            f"Body: {body_snippet}"
        )

        max_retries = 4
        base_delay = 4.0

        for attempt in range(max_retries):
            # Switch to fallback model if primary model is overloaded on retry
            active_model = self.model_name if attempt < 2 else self.fallback_model
            try:
                response = self.client.models.generate_content(
                    model=active_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=StoryClassification,
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )

                if response.parsed and isinstance(response.parsed, StoryClassification):
                    return response.parsed

                return StoryClassification.model_validate_json(response.text)

            except Exception as e:
                error_str = str(e).upper()
                is_transient = any(
                    k in error_str
                    for k in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "HIGH DEMAND", "TIMEOUT", "DEADLINE")
                )
                if is_transient and attempt < max_retries - 1:
                    sleep_time = base_delay * (attempt + 1)
                    console.print(
                        f"[yellow]⚠ API busy ({active_model}). Retrying in {sleep_time:.0f}s "
                        f"(Attempt {attempt + 1}/{max_retries})...[/yellow]"
                    )
                    time.sleep(sleep_time)
                    continue
                raise e

    def save_classification(self, article_id: int, result: StoryClassification) -> None:
        """Saves a single validated StoryClassification into SQLite."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO story_classifications (
                    article_id, sector, sentiment_score, importance,
                    is_new, confidence, reasoning, counter_argument, classified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_id) DO UPDATE SET
                    sector=excluded.sector,
                    sentiment_score=excluded.sentiment_score,
                    importance=excluded.importance,
                    is_new=excluded.is_new,
                    confidence=excluded.confidence,
                    reasoning=excluded.reasoning,
                    counter_argument=excluded.counter_argument,
                    classified_at=excluded.classified_at
                """,
                (
                    article_id,
                    result.sector,
                    result.sentiment_score,
                    result.importance,
                    1 if result.is_new else 0,
                    result.confidence,
                    result.reasoning,
                    result.counter_argument,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def run(self, limit: int = 20, show_output: bool = False) -> int:
        """Classifies pending stories in the database."""
        pending = self.get_unclassified_stories(limit=limit)
        if not pending:
            if show_output:
                console.print("[yellow]No unclassified stories found in articles.[/yellow]")
            return 0

        classified_records: List[Dict[str, Any]] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            disable=not show_output,
        ) as progress:
            task = progress.add_task("[cyan]Classifying stories with AI...", total=len(pending))

            for idx, story in enumerate(pending):
                short_title = story["title"][:40]
                progress.update(task, description=f"[cyan]Analysing ({idx + 1}/{len(pending)}): {short_title}...")

                recent_titles = self.get_recent_titles(exclude_id=story["id"], limit=8)
                result = self.classify_story(
                    title=story["title"],
                    clean_text=story["clean_text"],
                    recent_titles=recent_titles,
                )
                self.save_classification(story["id"], result)

                classified_records.append({
                    "article_id": story["id"],
                    "title": story["title"],
                    **result.model_dump(),
                })
                progress.advance(task)

                # Small pause between live API calls to respect free-tier RPM limits
                if show_output and idx < len(pending) - 1 and self.request_delay > 0:
                    time.sleep(self.request_delay)

        if show_output:
            table = Table(title="AI Story Classification Summary")
            table.add_column("ID", justify="right", style="dim")
            table.add_column("Title", style="white")
            table.add_column("Sector", style="bold cyan")
            table.add_column("Score", justify="right")
            table.add_column("Size", style="yellow")
            table.add_column("Sure?", style="magenta")

            for rec in classified_records[:12]:
                score = rec["sentiment_score"]
                score_str = f"[green]+{score:.2f}[/green]" if score >= 0 else f"[red]{score:.2f}[/red]"
                table.add_row(
                    str(rec["article_id"]),
                    rec["title"][:42],
                    rec["sector"],
                    score_str,
                    rec["importance"],
                    rec["confidence"],
                )

            console.print(table)
            console.print("\n[bold cyan]Sample AI Structured Output (pprint preview):[/bold cyan]")
            rpprint(classified_records, max_length=2, max_string=120, expand_all=True)
            console.print(f"\n[bold green]✔ Successfully classified {len(classified_records)} stories.[/bold green]")

        return len(classified_records)


if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE AI Story Classifier[/bold white]", border_style="cyan"))
    classifier = StoryClassifier()
    classifier.run(limit=20, show_output=True)