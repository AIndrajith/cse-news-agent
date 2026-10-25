"""
FastAPI Backend for CSE News Analysis Agent.
Exposes endpoints for running the agent, fetching sector scores, top stories,
search filters, and accessing historical Markdown briefs, with modular CORS enabled.
"""

from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

# Corrected import matching src/cors.py placement
from src.cors import setup_cors

# Import your agent runner and database paths
from src.agent import run_agent
from src.db import DEFAULT_DB_PATH

logger = logging.getLogger(__name__)
SL_TZ = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(
    title="CSE Market Intelligence Agent API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Apply modular CORS configuration
setup_cors(app)

BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "data" / "reports"


class AgentRunRequest(BaseModel):
    fetch_online: bool = True


@app.get("/")
def root():
    return {"status": "online", "message": "CSE Market Intelligence Agent API is running."}


@app.post("/api/run-agent")
def trigger_agent(payload: AgentRunRequest):
    """Triggers the LangGraph agent pipeline execution."""
    try:
        report_path = run_agent(db_path=DEFAULT_DB_PATH, fetch_online=payload.fetch_online)
        return {
            "status": "success",
            "message": "Agent execution completed successfully.",
            "report_path": str(report_path),
        }
    except Exception as e:
        logger.exception("Agent execution failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sectors")
def get_sectors(trade_date: Optional[str] = None, search: Optional[str] = None):
    """Fetches daily sector sentiment scores for a given trade date, with optional text search."""
    target_date = trade_date or datetime.now(SL_TZ).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        query = """
            SELECT trade_date, sector, score, article_count,
                   arguments_for, arguments_against, confidence
            FROM sector_daily_scores
            WHERE trade_date = ?
        """
        params = [target_date]

        if search:
            query += " AND sector LIKE ?"
            params.append(f"%{search}%")

        query += " ORDER BY ABS(score) DESC"

        cur.execute(query, params)
        rows = [dict(row) for row in cur.fetchall()]
        return {"trade_date": target_date, "sectors": rows}
    finally:
        conn.close()


@app.get("/api/stories")
def get_top_stories(
    limit: int = 15,
    importance: Optional[str] = None,
    sector: Optional[str] = None,
    search: Optional[str] = None
):
    """Fetches top market stories with AI classifications, filterable by importance, sector, and search query."""
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        query = """
            SELECT
                a.id,
                a.title,
                a.primary_source,
                a.duplicate_count,
                sc.sector,
                sc.sentiment_score,
                sc.importance,
                sc.confidence,
                sc.reasoning,
                sc.counter_argument
            FROM story_classifications sc
            INNER JOIN articles a ON a.id = sc.article_id
            WHERE sc.sector != 'Unrelated'
        """
        params = []

        # Filter by importance level if provided (e.g., 'major', 'moderate')
        if importance:
            query += " AND LOWER(sc.importance) = ?"
            params.append(importance.lower())

        # Filter by explicit sector if provided
        if sector:
            query += " AND sc.sector = ?"
            params.append(sector)

        # Filter by search string across title, reasoning, or sector
        if search:
            query += " AND (a.title LIKE ? OR sc.reasoning LIKE ? OR sc.sector LIKE ?)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])

        query += """
            ORDER BY
                CASE sc.importance
                    WHEN 'major' THEN 3
                    WHEN 'moderate' THEN 2
                    ELSE 1
                END DESC,
                ABS(sc.sentiment_score) DESC
            LIMIT ?
        """
        params.append(limit)

        cur.execute(query, params)
        rows = [dict(row) for row in cur.fetchall()]
        return {"stories": rows}
    finally:
        conn.close()

@app.get("/api/reports/archive")
def list_reports():
    """Lists all generated historical markdown briefs."""
    if not REPORTS_DIR.exists():
        return {"reports": []}

    files = sorted(REPORTS_DIR.glob("*_brief.md"), reverse=True)
    report_list = []
    for f in files:
        report_list.append({
            "filename": f.name,
            "date": f.name.split("_")[0],
            "size_kb": round(f.stat().st_size / 1024, 1),
        })
    return {"reports": report_list}


@app.get("/api/market-stats")
def get_market_stats(trade_date: Optional[str] = None):
    """
    Fetches market statistics for the requested trade date.
    If closed (weekend/holiday) or data is missing for that date,
    it automatically falls back to the last recorded trading day (e.g. Friday).
    """
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        # Auto-patch older database schemas if columns are missing
        for col_def in [("aspi_change", "REAL"), ("snp_change", "REAL"), ("market_turnover", "REAL"), ("companies_traded", "INTEGER")]:
            try:
                cur.execute(f"ALTER TABLE daily_market ADD COLUMN {col_def[0]} {col_def[1]}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Column already exists

        row = None
        if trade_date:
            # 1. Try exact match for the requested date first
            cur.execute(
                """
                SELECT trade_date, aspi, aspi_change, snp_sl20, snp_change, market_turnover, companies_traded
                FROM daily_market
                WHERE trade_date = ?
                """,
                (trade_date,)
            )
            row = cur.fetchone()

            # 2. If no exact match (e.g., weekend/holiday), find the closest prior trading day
            if not row:
                cur.execute(
                    """
                    SELECT trade_date, aspi, aspi_change, snp_sl20, snp_change, market_turnover, companies_traded
                    FROM daily_market
                    WHERE trade_date <= ?
                    ORDER BY trade_date DESC
                    LIMIT 1
                    """,
                    (trade_date,)
                )
                row = cur.fetchone()

        # 3. If no trade_date specified or still no row found, get the absolute latest market record
        if not row:
            cur.execute(
                """
                SELECT trade_date, aspi, aspi_change, snp_sl20, snp_change, market_turnover, companies_traded
                FROM daily_market
                ORDER BY trade_date DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()

        if row:
            return {
                "trade_date": row["trade_date"],
                "aspi": row["aspi"] or 0.0,
                "aspi_change": row["aspi_change"] or 0.0,
                "snp_sl20": row["snp_sl20"] or 0.0,
                "snp_change": row["snp_change"] or 0.0,
                "market_turnover": row["market_turnover"] or 0.0,
                "companies_traded": row["companies_traded"] or 0
            }

        # 4. If the daily_market table is completely empty, return zeroed state instead of hardcoded fake stats
        return {
            "trade_date": trade_date or datetime.now(SL_TZ).strftime("%Y-%m-%d"),
            "aspi": 0.0,
            "aspi_change": 0.0,
            "snp_sl20": 0.0,
            "snp_change": 0.0,
            "market_turnover": 0.0,
            "companies_traded": 0,
            "message": "No market data records found in database."
        }
    finally:
        conn.close()


@app.get("/api/reports/{filename}", response_class=PlainTextResponse)
def get_report_content(filename: str):
    """Retrieves the raw Markdown content of a specific past report."""
    file_path = REPORTS_DIR / filename
    if not file_path.exists() or not file_path.name.endswith("_brief.md"):
        raise HTTPException(status_code=404, detail="Report not found.")
    return file_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=True)