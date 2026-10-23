"""
Main Daily Pipeline for CSE News Analysis Agent.
Runs Steps 1 to 5 in fixed sequential order using plain Python functions:
1. Collect News
2. Clean + Deduplicates
3. Classify with AI
4. Score Sectors (Plain maths)
5. Build Daily Brief
"""

import logging
from pathlib import Path
import sys

from src.analyse.classifier import StoryClassifier
from src.analyse.scorer import SectorScorer
from src.collect.news_fetcher import NewsFetcher
from src.db import DEFAULT_DB_PATH, init_db
from src.process.cleaner import ArticleCleaner
from src.process.deduplicator import ArticleDeduplicator
from src.report.brief_builder import BriefBuilder

# Ensure project root is on sys.path when running `pipeline.py`
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()

def run_daily_pipeline(
        db_path: Path = DEFAULT_DB_PATH,
        fetch_online: bool = True,
        show_output: bool = True,
) -> Path:
    """Execute all 5 daily news analysis steps in sequence."""
    init_db(db_path)

    if show_output:
        console.print(Panel.fit("[bold cyan]CSE News Analysis Agent - Daily Pipeline[/bold cyan]"))

    # Step 1: Collect News
    if show_output:
        console.rule("[bold white]Step 1: Collect News[/bold white]")
    news_fetcher = NewsFetcher(db_path=db_path)
    news_fetcher.run(show_output=show_output)

    # Step 2a : Clean Articles
    if show_output:
        console.rule("[bold white]Step 2a: Clean Articles[/bold white]")
    cleaner = ArticleCleaner(db_path=db_path)
    cleaner.run(fetch_online=fetch_online, show_output=show_output)

    # Step 2b: Remove Duplicates
    if show_output:
        console.rule("[bold white]Step 2b: Deduplicate Stories[/bold white]")
    deduplicator = ArticleDeduplicator(db_path=db_path)
    deduplicator.run(show_output=show_output)

    # Step 3: understand the News (AI classifier)
    if show_output:
        console.rule("[bold white]Step 3: Classify stories (AI)[/bold white]")
    classifer = StoryClassifier(db_path=db_path)
    classifer.run(show_output=show_output)

    # Step 4: Score Each Sector (Plain maths)
    if show_output:
        console.rule("[bold white]Step 4: Score Sectors[/bold white]")
    scorer = SectorScorer(db_path=db_path)
    scorer.run(show_output=show_output)

    # Step 5: Build Daily Report
    if show_output:
        console.rule("[bold white]Step 5: Build Daily Report[/bold white]")
    builder = BriefBuilder(db_path=db_path)
    report_path = builder.run(show_output=show_output)

    if show_output:
        console.rule("[bold green]Pipeline Complete[/bold green]")

    return report_path

if __name__== "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run_daily_pipeline(fetch_online=True, show_output=True)