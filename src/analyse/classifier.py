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
from typing import Any, Optional, List, Dict

from dotenv import load_dotenv
from google import genai
from google.genai import types
from rich import columns

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.progress import Progress
from rich.table import Table

load_dotenv()
logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"

SYSTEM_PROMPT = """You are an objective financial news analyst for the Colombo Stock Exchange (CSE) in Sri Lanka.
Your task is to read one news story and classify it strictly into the requested JSON schema.

Rules you MUST follow:
1. NEVER give investment advice, never say 'buy' or 'sell', and never predict share price percentages.
2. Assign the story to the single best CSE sector. If the story is about Central Bank (CBSL) interest rates, inflation, IMF tranches, taxes, or the LKR exchange rate, choose 'Market-Wide'. If it has nothing to do with business or markets, choose 'Unrelated'.
3. Score sentiment from -1.0 (very negative for the sector) to +1.0 (very positive for the sector).
4. Compare the article against the provided list of recent headlines from the past 7 days: set is_new=False if it is a follow-up or repeat of an already-reported event.
5. Always provide both a one-line 'reasoning' and a one-line 'counter_argument' (caveat or opposing view) in plain English.
6. Be honest about uncertainty: if the article is vague or mixed, set confidence to 'LOW' or 'MEDIUM'.
"""

class StoryClassifier:
    def __init__(
            self,
            db_path: Path = DEFAULT_DB_PATH,
            model_name: str = "gemini-2.5-flash",
            api_key:Optional[str] = None
    ):
        self.db_path = db_path
        self.model_name = model_name
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._client = None
        self._ensure_schema()

    @property
    def client(self) -> genai.Client:
        """Lazily initializes the Gemini client only when an API call is needed."""
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "GEMINI_API_KEY is missing. Add it to your .env file or pass api_key to StoryClassifier."
                )
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _ensure_schema(self) -> None:
        """Ensures story_classifications table and all required columns exists."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS story_classifications(
                    article_id INTEGER PRIMARY KEY,
                    sector TEXT,
                    sentiment_score REAL,
                    importance TEXT,
                    is_new INTEGER DEFAULT 1,
                    confidence TEXT,
                    reasoning TEXT, 
                    counter_argument TEXT,
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
                cur.execute("ALTER TABLE story_classificatoins ADD COLUMN column_argument TEXT")
            conn.commit()
        finally:
            conn.close()

    def get_unclassified_stories(self, limit:int = 50) -> List[Dict[str, Any]]:
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

    def get_recent_titles(self, exfclued_id: int, limit:int = 30) -> List[str]:
        """Fetches recent already-classified headlines so the AI can detect 7-day repeats."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT a.title
                FROM articles a
                INNER JOIN story_classifier sc ON a.id = sc.article_id
                WHERE a.id != ?
                ORDER BY a.id DESC
                LIMIT ?
                """,
                (exfclued_id, limit),
            )
            return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

