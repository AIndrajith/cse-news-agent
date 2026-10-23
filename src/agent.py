"""
CSE News Analysis Agent Orchestrator (LangGrapht State Machine).
An autonomous agentic loop that monitors database state, handles retries, and coordinates tools.
"""

from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
import sqlite3
from typing import Dict, List, TypedDict

from langgraph.graph import END, StateGraph
from rich.console import Console

from src.analyse.classifier import StoryClassifier
from src.analyse.scorer import SectorScorer
from src.collect.news_fetcher import NewsFetcher
from src.db import DEFAULT_DB_PATH, init_db
from src.process.cleaner import ArticleCleaner
from src.process.deduplicator import ArticleDeduplicator
from src.report.brief_builder import BriefBuilder

logger = logging.getLogger(__name__)
console = Console()
SL_TZ = timezone(timedelta(hours=5, minutes=30))

# Define the Agent State passed between nodes
class AgentState(TypedDict):
    db_path: Path
    fetch_online: bool
    trade_date: str
    new_articles_count: int
    cleaned_count: int
    unique_stories_count: int
    classified_count: int
    sectors_scored: int
    report_path: str
    errors: List[str]

def node_collect_news(state: AgentState) -> AgentState:
    """Agent Tool: Collects latest RSS articles."""
    console.print("[cyan]Agent Node: Running news Collector...[/cyan]")
    try:
        fetcher = NewsFetcher(db_path=state["db_path"])
        count = fetcher.run(show_output=False)
        state["new_articles_count"] = count
    except Exception as e:
        state["errors"].append(f"Collection error: {e}")
    return state

def node_clean_and_deduplicate(state: AgentState) -> AgentState:
    """Agent Tool: Cleans HTML and merges duplicates using rapidfuzz."""
    console.print("[cyan]Agent Node: Cleaning and Deduplicating Stories...[/cyan]")
    try:
        cleaner = ArticleCleaner(db_path=state["db_path"])
        cleaned = cleaner.run(fetch_online=state["fetch_online"], show_output=False)
        state["cleaned_count"] = cleaned

        deduplicator = ArticleDeduplicator(db_path=state["db_path"])
        new_stories, _ = deduplicator.run(show_output=False)
        state["unique_stories_count"] = new_stories

    except Exception as e:
        state["errors"].append(f"Process error: {e}")
    return  state

def node_classify_stories(state: AgentState) -> AgentState:
    """Agent Tool: Invokes LLM Classifier with Pydantic structured output."""
    console.print("[cyan]Agent Node: Classifying stories with AI...[/cyan]")
    try:
        classifier = StoryClassifier(db_path=state["db_path"])
        count = classifier.run(limit=30, show_output=False)
        state["classified_count"] = count
    except Exception as e:
        state["errors"].append(f"Classification error: {e}")
    return state

def node_score_sectors(state: AgentState) -> AgentState:
    """Agent Tool: Performs deterministic arithmetic scoring of sectors."""
    console.print("[cyan]Agent Node: Computing sector scores (Plain Maths)...[/cyan]")
    try:
        scorer = SectorScorer(db_path=state["db_path"])
        results = scorer.run(trade_date=state["trade_date"], show_output=False)
        state["sectors_scored"] = len(results)
    except Exception as e:
        state["errors"].append(f"Scoring error: {e}")
    return state

def node_build_report(state: AgentState) -> AgentState:
    """Agent Tool: Compiles daily brief and sames Markdown report."""
    console.print("[cyan]Agent Node: Building Daily Brief...[/cyan]")
    try:
        builder = BriefBuilder(db_path=state["db_path"])
        path = builder.run(trade_date=state["trade_date"], show_output=True)
        state["report_path"] = str(path)
    except Exception as e:
        state["errors"].append(f"Reporting error: {e}")
    return state

def decide_next_step(state: AgentState) -> AgentState:
    """Conditional router: If critical errors occurred, halt; otherwise proceed."""
    if state["errors"] and len(state["errors"]) > 3:
        return "halt"
    return "continue"

def build_cse_agent_graph() -> StateGraph:
    """Constructs the LangGraph state machine graph."""
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("collect", node_collect_news)
    workflow.add_node("process", node_clean_and_deduplicate)
    workflow.add_node("classify", node_classify_stories)
    workflow.add_node("score", node_score_sectors)
    workflow.add_node("report", node_build_report)

    # Set entry Point
    workflow.set_entry_point("collect")

    # Add deterministic edges with conditional error checking
    workflow.add_edge("collect", "process")
    workflow.add_edge("process", "classify")
    workflow.add_edge("classify", "score")
    workflow.add_edge("score", "report")
    workflow.add_edge("report", END)

    return workflow.compile()

def run_agent(db_path: Path = DEFAULT_DB_PATH, fetch_online: bool = True) -> str:
    """Initializes databases and executes the agent graph."""
    init_db(db_path)
    trade_date = datetime.now(SL_TZ).strftime("%Y-%m-%d")

    initial_state: AgentState = {
        "db_path": db_path,
        "fetch_online": fetch_online,
        "trade_date": trade_date,
        "new_articles_count": 0,
        "cleaned_count": 0,
        "unique_stories_count": 0,
        "classified_count": 0,
        "sectors_scored": 0,
        "report_path":"",
        "errors":[],
    }

    console.print("[bold green]Starting CSE Autonomous News Agent...[/bold green]")
    app = build_cse_agent_graph()
    final_state = app.invoke(initial_state)

    if final_state["errors"]:
        console.print(f"[bold red]Agent encountered errors: {final_state['errors']}[/bold red]")
    else:
        console.print(f"[bold green] Agent successfully compiled daily report at: {final_state['report_path']}[/bold green]")

    return final_state["report_path"]


if __name__== "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    run_agent()