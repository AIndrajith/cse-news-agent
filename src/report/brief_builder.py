"""
Daily Brief Builder for CSE News Agent.
Formats daily sector scores and top stories into the exact evidence-based layout
required by the project design (In favour, Against, Confidence, Story count).
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
DEFAULT_REPORTS_DIR = BASE_DIR / "data" / "reports"

# Sri Lanka Standard Time (UTC+05:30)
SL_TZ = timezone(timedelta(hours=5, minutes=30))

DISCLAIMER = (
    "NOTICE: This report is an automated research and information tool for the "
    "Colombo Stock Exchange (CSE). It reports news evidence only and is NOT "
    "investment advice. It does not predict share prices or recommend buying "
    "or selling any security."
)


def format_mood_label(score: float) -> str:
    """Converts numeric score into a human-readable mood label."""
    if score > 0.05:
        return "positive"
    if score < -0.05:
        return "negative"
    return "neutral" 


def format_sector_block(sector_row: Dict[str, Any]) -> str:
    """
    Formats a single sector's evidence block exactly as specified in Figure 2:
    Banks — positive (+0.62)
    In favour:  ...
    Against:    ...
    How sure:   MEDIUM
    Based on:   4 news stories
    """
    sector = sector_row["sector"]
    score = float(sector_row["score"])
    mood = format_mood_label(score)
    sign = "+" if score >= 0 else ""
    count = int(sector_row["article_count"])
    story_word = "story" if count == 1 else "stories"

    lines = [
        f"{sector} — {mood} ({sign}{score:.2f})",
        f"In favour:  {sector_row['arguments_for']}",
        f"Against:    {sector_row['arguments_against']}",
        f"How sure:   {sector_row['confidence']}",
        f"Based on:   {count} news {story_word}",
    ]
    return "\n".join(lines)


class BriefBuilder:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        reports_dir: Path = DEFAULT_REPORTS_DIR,
    ):
        self.db_path = Path(db_path)
        self.reports_dir = Path(reports_dir)

    def get_latest_trade_date(self) -> str:
        """Finds the latest trade_date in sector_daily_scores or defaults to today (SL time)."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute("SELECT MAX(trade_date) FROM sector_daily_scores")
            row = cur.fetchone()
            if row and row[0]:
                return str(row[0])
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
        return datetime.now(SL_TZ).strftime("%Y-%m-%d")

    def get_sector_scores(self, trade_date: str) -> List[Dict[str, Any]]:
        """Loads sector scores for the given trade_date ordered by strongest signal."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT trade_date, sector, score, article_count,
                       arguments_for, arguments_against, confidence
                FROM sector_daily_scores
                WHERE trade_date = ?
                ORDER BY ABS(score) DESC, article_count DESC
                """,
                (trade_date,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_top_stories(self, limit: int = 15) -> List[Dict[str, Any]]:
        """
        Loads the top 10-20 most important classified stories
        ordered by importance and duplicate sightings.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    a.id,
                    a.title,
                    a.primary_source,
                    a.duplicate_count,
                    sc.sector,
                    sc.sentiment_score,
                    sc.importance,
                    sc.confidence,
                    sc.reasoning
                FROM story_classifications sc
                INNER JOIN articles a ON a.id = sc.article_id
                WHERE sc.sector != 'Unrelated'
                ORDER BY
                    CASE sc.importance
                        WHEN 'major' THEN 3
                        WHEN 'moderate' THEN 2
                        ELSE 1
                    END DESC,
                    ABS(sc.sentiment_score) DESC,
                    a.duplicate_count DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def build_report_text(
        self,
        trade_date: str,
        sector_scores: List[Dict[str, Any]],
        top_stories: List[Dict[str, Any]],
    ) -> str:
        """Combines sector blocks, top stories, and legal disclaimer into a daily brief."""
        sections = [
            f"# CSE Daily Sector News Brief — {trade_date}",
            "",
            f"> {DISCLAIMER}",
            "",
            "## 1. Sector Evidence Summary",
            "",
        ]

        if not sector_scores:
            sections.append("No sector news scored for this date.")
        else:
            for row in sector_scores:
                sections.append("```text")
                sections.append(format_sector_block(row))
                sections.append("```")
                sections.append("")

        sections.append("## 2. Top Market Stories")
        sections.append("")

        if not top_stories:
            sections.append("No market stories available.")
        else:
            for idx, st in enumerate(top_stories, start=1):
                score = float(st["sentiment_score"])
                sign = "+" if score >= 0 else ""
                sections.append(
                    f"{idx}. **[{st['sector']}] {st['title']}** "
                    f"({sign}{score:.2f}, {st['importance']}, seen on {st['duplicate_count']} site(s))"
                )
                sections.append(f"   - *Why:* {st['reasoning']}")

        sections.append("")
        return "\n".join(sections)

    def save_report_file(self, trade_date: str, report_text: str) -> Path:
        """Saves the generated daily report to data/reports/YYYY-MM-DD_brief.md."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.reports_dir / f"{trade_date}_brief.md"
        report_path.write_text(report_text, encoding="utf-8")
        return report_path

    def run(self, trade_date: Optional[str] = None, show_output: bool = False) -> Path:
        """Builds, displays, and saves the daily CSE sector report."""
        target_date = trade_date or self.get_latest_trade_date()
        sector_scores = self.get_sector_scores(target_date)
        top_stories = self.get_top_stories(limit=15)

        report_text = self.build_report_text(target_date, sector_scores, top_stories)
        report_path = self.save_report_file(target_date, report_text)

        if show_output:
            console.print(f"\n[bold yellow]{DISCLAIMER}[/bold yellow]\n")

            for row in sector_scores:
                block_text = format_sector_block(row)
                border = "green" if row["score"] > 0.05 else ("red" if row["score"] < -0.05 else "white")
                console.print(Panel(block_text, border_style=border, expand=False))

            if top_stories:
                table = Table(title=f"Top {len(top_stories)} Market Stories ({target_date})")
                table.add_column("#", justify="right", style="dim")
                table.add_column("Sector", style="bold cyan")
                table.add_column("Headline", style="white")
                table.add_column("Score", justify="right")
                table.add_column("Size", style="yellow")
                table.add_column("Sites", justify="right", style="dim")

                for idx, st in enumerate(top_stories, start=1):
                    sc = float(st["sentiment_score"])
                    sc_str = f"[green]+{sc:.2f}[/green]" if sc >= 0 else f"[red]{sc:.2f}[/red]"
                    table.add_row(
                        str(idx),
                        st["sector"],
                        st["title"][:48],
                        sc_str,
                        st["importance"],
                        str(st["duplicate_count"]),
                    )
                console.print(table)

                console.print("\n[bold cyan]Sample Top Story Record (pprint preview):[/bold cyan]")
                rpprint(top_stories, max_length=2, max_string=100, expand_all=True)

            console.print(f"\n[bold green]✔ Daily report saved to: {report_path}[/bold green]")

        return report_path


if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Daily Brief Builder[/bold white]", border_style="cyan"))
    builder = BriefBuilder()
    builder.run(show_output=True)