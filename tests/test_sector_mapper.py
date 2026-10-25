import csv
import sqlite3
import pytest
from src.collect.sector_mapper import SectorMapper, classify_company_sector


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_cse.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE daily_prices (
            symbol TEXT,
            trade_date TEXT,
            company_name TEXT,
            close_price REAL,
            change_amount REAL,
            change_pct REAL,
            share_volume REAL,
            trade_count INTEGER,
            turnover REAL,
            fetched_at TEXT,
            PRIMARY KEY (symbol, trade_date)
        );

        INSERT INTO daily_prices (symbol, trade_date, company_name, close_price)
        VALUES
            ('COMB.N0000', '2026-10-23', 'COMMERCIAL BANK OF CEYLON PLC', 115.5),
            ('COMB.X0000', '2026-10-23', 'COMMERCIAL BANK OF CEYLON PLC (NON-VOTING)', 92.0),
            ('JKH.N0000', '2026-10-23', 'JOHN KEELLS HOLDINGS PLC', 195.0),
            ('CITW.N0000', '2026-10-23', 'WASKADUWA BEACH RESORT PLC', 3.5);
        """
    )
    conn.commit()
    conn.close()
    return db_file


def test_classify_company_sector_accuracy():
    assert classify_company_sector("COMB.N0000", "COMMERCIAL BANK OF CEYLON PLC") == "Banks"
    assert classify_company_sector("DIAL.N0000", "DIALOG AXIATA PLC") == "Telecommunications & IT"
    assert classify_company_sector("CITW.N0000", "WASKADUWA BEACH RESORT PLC") == "Hotels & Travels"
    assert classify_company_sector("WATA.N0000", "WATAWALA PLANTATIONS PLC") == "Plantations"


def test_sector_mapper_writes_csv(test_db, tmp_path):
    csv_file = tmp_path / "sector_map.csv"
    mapper = SectorMapper(db_path=test_db, output_csv=csv_file)
    results = mapper.run(show_output=False)

    assert len(results) == 4
    assert csv_file.exists()

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 4
    assert reader[0] == {
        "symbol": "CITW.N0000",
        "company_name": "WASKADUWA BEACH RESORT PLC",
        "sector": "Hotels & Travels",
    }
    assert reader[1]["sector"] == "Banks"