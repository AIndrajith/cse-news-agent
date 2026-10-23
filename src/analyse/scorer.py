"""
Sector Scorer for CSE News Agent.
Aggregates AI story classifications into daily sector scores using plain arithmetic (no AI).
Builds 'In favour' and 'Against' evidence lists and calibrates sector confidence.
"""

from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"

# Sri Lanka Standard Time (UTC+05:30)
SL_TZ = timezone(timedelta(hours=5, minutes=30))

IMPORTANCE_WEIGHTS: Dict[str, float] = {
    "trivial": 0.5,
    "moderate": 1.0,
    "major": 2.0,
}

CONFIDENCE_SCORES: Dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


def compute_sector_confidence(confidences: List[str], article_count: int) -> str:
    """
    Determines overall sector confidence from individual story confidences and sample size.
    A single story is capped at MEDIUM so the report never sounds overconfident on 1 article.
    """
    if not confidences or article_count <= 0:
        return "LOW"

    numeric = [CONFIDENCE_SCORES.get(c.upper(), 1) for c in confidences]
    avg_conf = sum(numeric) / len(numeric)

    if article_count == 1:
        return "LOW" if avg_conf < 1.5 else "MEDIUM"

    if avg_conf >= 2.5 and article_count >= 2:
        return "HIGH"
    if avg_conf >= 1.6:
        return "MEDIUM"
    return "LOW"


def calculate_weighted_score(stories: List[Dict[str, Any]]) -> float:
    """
    Calculates weighted average sentiment score in [-1.0, +1.0].
    Weights stories by importance and discounts 7-day repeat stories (is_new=0).
    """
    if not stories:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0

    for st in stories:
        imp = str(st.get("importance", "moderate")).lower()
        base_weight = IMPORTANCE_WEIGHTS.get(imp, 1.0)

        # Discount repeat stories from the last 7 days so they don't dominate today's score
        is_new = bool(st.get("is_new", 1))
        weight = base_weight if is_new else (base_weight * 0.25)

        score = float(st.get("sentiment_score", 0.0))
        weighted_sum += score * weight
        total_weight += weight

    if total_weight == 0.0:
        return 0.0

    final_score = max(-1.0, min(1.0, weighted_sum / total_weight))
    return round(final_score, 2)


class SectorScorer:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensures sector_daily_scores table exists in SQLite."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_daily_scores (
                    trade_date TEXT,
                    sector TEXT,
                    score REAL,
                    article_count INTEGER,
                    arguments_for TEXT,
                    arguments_against TEXT,
                    confidence TEXT,
                    PRIMARY KEY (trade_date, sector)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def get_classified_stories(self) -> List[Dict[str, Any]]:
        """
        Fetches classified stories (excluding 'Unrelated') to compute sector scores.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    sc.article_id,
                    a.title,
                    sc.sector,
                    sc.sentiment_score,
                    sc.importance,
                    sc.is_new,
                    sc.confidence,
                    sc.reasoning,
                    sc.counter_argument
                FROM story_classifications sc
                INNER JOIN articles a ON a.id = sc.article_id
                WHERE sc.sector IS NOT NULL
                  AND sc.sector != 'Unrelated'
                ORDER BY sc.article_id ASC
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def aggregate_by_sector(
        self, stories: List[Dict[str, Any]], trade_date: str
    ) -> List[Dict[str, Any]]:
        """Groups classified stories by sector and computes scores and evidence lists."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for st in stories:
            sector = st["sector"]
            grouped.setdefault(sector, []).append(st)

        sector_results: List[Dict[str, Any]] = []

        for sector, sec_stories in grouped.items():
            score = calculate_weighted_score(sec_stories)
            confidences = [st.get("confidence", "MEDIUM") for st in sec_stories]
            confidence = compute_sector_confidence(confidences, len(sec_stories))

            args_for: List[str] = []
            args_against: List[str] = []

            for st in sec_stories:
                sent = float(st.get("sentiment_score", 0.0))
                reason = (st.get("reasoning") or "").strip()
                counter = (st.get("counter_argument") or "").strip()

                if sent >= 0.0:
                    if reason and reason not in args_for:
                        args_for.append(reason)
                    if counter and counter not in args_against:
                        args_against.append(counter)
                else:
                    if reason and reason not in args_against:
                        args_against.append(reason)
                    if counter and counter not in args_for:
                        args_for.append(counter)

            sector_results.append({
                "trade_date": trade_date,
                "sector": sector,
                "score": score,
                "article_count": len(sec_stories),
                "arguments_for": "; ".join(args_for[:4]) or "No major positive catalysts reported.",
                "arguments_against": "; ".join(args_against[:4]) or "No immediate downside risks noted.",
                "confidence": confidence,
            })

        # Sort by absolute score impact descending
        sector_results.sort(key=lambda x: abs(x["score"]), reverse=True)
        return sector_results

    def save_sector_scores(self, sector_results: List[Dict[str, Any]]) -> int:
        """Saves aggregated sector scores into sector_daily_scores table."""
        if not sector_results:
            return 0

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.executemany(
                """
                INSERT INTO sector_daily_scores (
                    trade_date, sector, score, article_count,
                    arguments_for, arguments_against, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, sector) DO UPDATE SET
                    score=excluded.score,
                    article_count=excluded.article_count,
                    arguments_for=excluded.arguments_for,
                    arguments_against=excluded.arguments_against,
                    confidence=excluded.confidence
                """,
                [
                    (
                        r["trade_date"],
                        r["sector"],
                        r["score"],
                        r["article_count"],
                        r["arguments_for"],
                        r["arguments_against"],
                        r["confidence"],
                    )
                    for r in sector_results
                ],
            )
            conn.commit()
            return len(sector_results)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def run(self, trade_date: Optional[str] = None, show_output: bool = False) -> List[Dict[str, Any]]:
        """Runs the sector scoring step and saves results to SQLite."""
        target_date = trade_date or datetime.now(SL_TZ).strftime("%Y-%m-%d")
        stories = self.get_classified_stories()

        if not stories:
            if show_output:
                console.print("[yellow]No classified stories found to score.[/yellow]")
            return []

        sector_results = self.aggregate_by_sector(stories, trade_date=target_date)
        self.save_sector_scores(sector_results)

        if show_output:
            table = Table(title=f"CSE Daily Sector Scores ({target_date})")
            table.add_column("Sector", style="bold cyan")
            table.add_column("Score", justify="right")
            table.add_column("Mood", style="bold")
            table.add_column("Stories", justify="right", style="yellow")
            table.add_column("How Sure", style="magenta")

            for row in sector_results:
                sc = row["score"]
                if sc > 0.05:
                    score_str = f"[green]+{sc:.2f}[/green]"
                    mood = "[green]positive[/green]"
                elif sc < -0.05:
                    score_str = f"[red]{sc:.2f}[/red]"
                    mood = "[red]negative[/red]"
                else:
                    score_str = f"{sc:.2f}"
                    mood = "[dim]neutral[/dim]"

                table.add_row(
                    row["sector"],
                    score_str,
                    mood,
                    str(row["article_count"]),
                    row["confidence"],
                )

            console.print(table)
            console.print("\n[bold cyan]Sample Sector Score Records (pprint preview):[/bold cyan]")
            rpprint(sector_results, max_length=3, max_string=120, expand_all=True)
            console.print(
                f"\n[bold green]✔ Scored {len(sector_results)} sectors from {len(stories)} classified stories.[/bold green]"
            )

        return sector_results


if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Sector Scorer (Plain Maths)[/bold white]", border_style="cyan"))
    scorer = SectorScorer()
    scorer.run(show_output=True)