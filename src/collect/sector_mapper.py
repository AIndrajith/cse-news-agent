"""
Sector Mapper for CSE News Agent.
Generates data/sector_map.csv by mapping CSE company symbols and names
from daily_prices into the project's canonical market sectors.
"""

from collections import Counter
import csv
import logging
from pathlib import Path
import sqlite3
from typing import Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.pretty import pprint as rpprint
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "cse.db"
DEFAULT_SECTOR_MAP_PATH = BASE_DIR / "data" / "sector_map.csv"

# 1. Explicit ticker prefix overrides for major CSE companies whose names don't state their sector
TICKER_SECTOR_OVERRIDES: Dict[str, str] = {
    # Banks
    "COMB": "Banks",
    "SAMP": "Banks",
    "HNB": "Banks",
    "NDB": "Banks",
    "DFCC": "Banks",
    "SEYB": "Banks",
    "NTB": "Banks",
    "PABC": "Banks",
    "UBC": "Banks",
    "ABL": "Banks",
    "SDB": "Banks",
    "CBNK": "Banks",
    "HDFC": "Banks",
    # Diversified Financials
    "JKH": "Diversified Financials",
    "LOLC": "Diversified Financials",
    "LFIN": "Diversified Financials",
    "CDB": "Diversified Financials",
    "PLC": "Diversified Financials",
    "VFIN": "Diversified Financials",
    "CFIN": "Diversified Financials",
    "COCR": "Diversified Financials",
    "FCT": "Diversified Financials",
    "CALT": "Diversified Financials",
    "SFIN": "Diversified Financials",
    "BFL": "Diversified Financials",
    "alli": "Diversified Financials",
    "ALLI": "Diversified Financials",
    # Insurance
    "CINS": "Insurance",
    "JINS": "Insurance",
    "SINS": "Insurance",
    "UAL": "Insurance",
    "AAIC": "Insurance",
    "HASU": "Insurance",
    "PINS": "Insurance",
    "AINS": "Insurance",
    # Telecommunications & IT
    "DIAL": "Telecommunications & IT",
    "SLTL": "Telecommunications & IT",
    "HSIG": "Telecommunications & IT",
    "KAP": "Telecommunications & IT",
    # Energy & Utilities
    "LIOC": "Energy & Utilities",
    "LLUB": "Energy & Utilities",
    "LGL": "Energy & Utilities",
    "WIND": "Energy & Utilities",
    "VPEL": "Energy & Utilities",
    "LVL": "Energy & Utilities",
    "PAP": "Energy & Utilities",
    "HPWR": "Energy & Utilities",
    "vidul": "Energy & Utilities",
    "VIDU": "Energy & Utilities",
    # Construction & Engineering
    "AEL": "Construction & Engineering",
    "TKYO": "Construction & Engineering",
    "ACL": "Construction & Engineering",
    "KCAB": "Construction & Engineering",
    "SIER": "Construction & Engineering",
    "SCAP": "Construction & Engineering",
    # Manufacturing & Materials
    "HAYL": "Manufacturing & Materials",
    "TILE": "Manufacturing & Materials",
    "RCL": "Manufacturing & Materials",
    "LWL": "Manufacturing & Materials",
    "CERA": "Manufacturing & Materials",
    "PARQ": "Manufacturing & Materials",
    "ALUM": "Manufacturing & Materials",
    "LALU": "Manufacturing & Materials",
    "DIPD": "Manufacturing & Materials",
    "HAYC": "Manufacturing & Materials",
    "CIC": "Manufacturing & Materials",
    "TJL": "Manufacturing & Materials",
    "HEXP": "Manufacturing & Materials",
    "HHL": "Manufacturing & Materials",
    "GLAS": "Manufacturing & Materials",
    "REG": "Manufacturing & Materials",
    "ACL": "Construction & Engineering",
    # Food, Beverage & Tobacco
    "CTC": "Food, Beverage & Tobacco",
    "MELS": "Food, Beverage & Tobacco",
    "DIST": "Food, Beverage & Tobacco",
    "LION": "Food, Beverage & Tobacco",
    "CCS": "Food, Beverage & Tobacco",
    "GRAN": "Food, Beverage & Tobacco",
    "TAFL": "Food, Beverage & Tobacco",
    "BFL": "Food, Beverage & Tobacco",
    "SUN": "Food, Beverage & Tobacco",
    "Leche": "Food, Beverage & Tobacco",
    "LMF": "Food, Beverage & Tobacco",
    "NEST": "Food, Beverage & Tobacco",
    "BREW": "Food, Beverage & Tobacco",
    "renu": "Food, Beverage & Tobacco",
    "COCO": "Food, Beverage & Tobacco",
    "KFP": "Food, Beverage & Tobacco",
    "SOY": "Food, Beverage & Tobacco",
    # Plantations
    "WATA": "Plantations",
    "KELN": "Plantations",
    "AGAL": "Plantations",
    "BALA": "Plantations",
    "BPL": "Plantations",
    "ELPL": "Plantations",
    "HAPU": "Plantations",
    "HOPL": "Plantations",
    "KAHA": "Plantations",
    "KOTA": "Plantations",
    "MADU": "Plantations",
    "MAL": "Plantations",
    "MASK": "Plantations",
    "NAMU": "Plantations",
    "TPL": "Plantations",
    "UDPL": "Plantations",
    # Hotels & Travels
    "AHUN": "Hotels & Travels",
    "AHPL": "Hotels & Travels",
    "KHL": "Hotels & Travels",
    "SERV": "Hotels & Travels",
    "TRANS": "Hotels & Travels",
    "CITH": "Hotels & Travels",
    "EDEN": "Hotels & Travels",
    "CONN": "Hotels & Travels",
    "GHLL": "Hotels & Travels",
    "HUNA": "Hotels & Travels",
    "JETS": "Hotels & Travels",
    "MARA": "Hotels & Travels",
    "NEH": "Hotels & Travels",
    "PALM": "Hotels & Travels",
    "RENU": "Hotels & Travels",
    "RHTL": "Hotels & Travels",
    "SIGV": "Hotels & Travels",
    "STAF": "Hotels & Travels",
    "TAJ": "Hotels & Travels",
    "TANG": "Hotels & Travels",
    # Healthcare
    "ASIR": "Healthcare",
    "AMS": "Healthcare",
    "NHL": "Healthcare",
    "LHCL": "Healthcare",
    "SINH": "Healthcare",
    "MEDI": "Healthcare",
    "CHL": "Healthcare",
    # Retail & Consumer
    "CARG": "Retail & Consumer",
    "CARS": "Retail & Consumer",
    "SINS": "Retail & Consumer",
    "SING": "Retail & Consumer",
    "ABAN": "Retail & Consumer",
    "ODEL": "Retail & Consumer",
    "DIMO": "Retail & Consumer",
    "UML": "Retail & Consumer",
    "CMHR": "Retail & Consumer",
    # Real Estate
    "OSEA": "Real Estate",
    "CTLD": "Real Estate",
    "LDEV": "Real Estate",
    "EAST": "Real Estate",
    "ONAL": "Real Estate",
    "CABO": "Real Estate",
    "MBSL": "Diversified Financials",
    # Transportation & Logistics
    "DOCK": "Transportation & Logistics",
    "CHR": "Transportation & Logistics",
    "MERC": "Transportation & Logistics",
}

# 2. Ordered keyword rules checked against company_name if ticker is not in overrides
KEYWORD_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("BANK", "BANKING"), "Banks"),
    (("INSURANCE", "ASSURANCE", "TAKAFUL"), "Insurance"),
    (
        (
            "FINANCE",
            "FINANCIAL",
            "LEASING",
            "CAPITAL",
            "INVESTMENT",
            "TRUST",
            "FUND",
            "EQUITY",
            "CREDIT",
            "MERCHANT",
            "VENTURE",
            "BROKERS",
        ),
        "Diversified Financials",
    ),
    (
        (
            "HOTEL",
            "HOTELS",
            "RESORT",
            "RESORTS",
            "LEISURE",
            "TRAVEL",
            "TRAVELS",
            "TOURIST",
            "HOSPITALITY",
            "BEACH",
            "VILLA",
            "PALMS",
            "RETREAT",
        ),
        "Hotels & Travels",
    ),
    (
        (
            "PLANTATION",
            "PLANTATIONS",
            "ESTATES",
            "ESTATE",
            "TEA",
            "AGRI",
            "AGRICULTURE",
            "CROP",
        ),
        "Plantations",
    ),
    (
        (
            "HOSPITAL",
            "HOSPITALS",
            "HEALTH",
            "HEALTHCARE",
            "MEDICAL",
            "PHARMA",
            "DIAGNOSTIC",
            "WELLNESS",
        ),
        "Healthcare",
    ),
    (
        (
            "POWER",
            "ENERGY",
            "HYDRO",
            "SOLAR",
            "WIND",
            "PETROLEUM",
            "LUBRICANTS",
            "GAS",
            "ELECTRIC",
        ),
        "Energy & Utilities",
    ),
    (
        (
            "TELECOM",
            "DIALOG",
            "DIGITAL",
            "SOFTWARE",
            "TECH",
            "INFORMATION",
        ),
        "Telecommunications & IT",
    ),
    (
        (
            "CONSTRUCTION",
            "ENGINEERING",
            "CEMENT",
            "CABLE",
            "CABLES",
            "INFRASTRUCTURE",
            "DEVELOPERS",
        ),
        "Construction & Engineering",
    ),
    (
        (
            "PROPERTY",
            "PROPERTIES",
            "LAND",
            "REAL ESTATE",
            "REALTY",
            "CONDOMINIUM",
            "OVERSEAS REALTY",
        ),
        "Real Estate",
    ),
    (
        (
            "LOGISTICS",
            "SHIPPING",
            "CARGO",
            "FREIGHT",
            "MARITIME",
            "DOCKYARD",
            "TRANSPORT",
            "PORT",
            "AVIATION",
        ),
        "Transportation & Logistics",
    ),
    (
        (
            "FOOD",
            "FOODS",
            "BEVERAGE",
            "BEVERAGES",
            "DISTILLERIES",
            "BREWERY",
            "TOBACCO",
            "DAIRY",
            "MILK",
            "SUGAR",
            "POULTRY",
            "FARMS",
            "CONFECTIONERY",
            "TEA SMALLHOLDER",
            "BAKER",
            "MILL",
        ),
        "Food, Beverage & Tobacco",
    ),
    (
        (
            "SUPERMARKET",
            "RETAIL",
            "MOTORS",
            "MOTOR",
            "AUTOMOBILE",
            "TRADING",
            "CONSUMER",
            "STORES",
            "FASHION",
            "MARKETING",
        ),
        "Retail & Consumer",
    ),
    (
        (
            "TILE",
            "TILES",
            "CERAMIC",
            "CERAMICS",
            "ALUMINIUM",
            "GLASS",
            "PACKAGING",
            "PRINT",
            "TEXTILE",
            "FABRIC",
            "APPAREL",
            "RUBBER",
            "POLYMER",
            "CHEMICAL",
            "METAL",
            "STEEL",
            "MANUFACTURING",
            "INDUSTRIES",
            "INDUSTRIAL",
            "PRODUCTS",
            "FIBRE",
            "COIR",
            "WOOD",
            "TIMBER",
        ),
        "Manufacturing & Materials",
    ),
]


def classify_company_sector(symbol: str, company_name: str) -> str:
    """
    Deterministically maps a CSE company symbol and name to one of the 14 canonical sectors.
    1. Checks ticker prefix in TICKER_SECTOR_OVERRIDES.
    2. Checks company name against KEYWORD_RULES.
    3. Defaults conglomerate/holding companies to 'Diversified Financials' or 'Manufacturing & Materials'.
    """
    prefix = (symbol or "").split(".")[0].strip().upper()
    if prefix in TICKER_SECTOR_OVERRIDES:
        return TICKER_SECTOR_OVERRIDES[prefix]

    upper_name = (company_name or "").upper()
    for keywords, sector in KEYWORD_RULES:
        for kw in keywords:
            if kw in upper_name:
                return sector

    if "HOLDINGS" in upper_name or "CONGLOMERATE" in upper_name or "GROUP" in upper_name:
        return "Diversified Financials"

    return "Manufacturing & Materials"


class SectorMapper:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        output_csv: Path = DEFAULT_SECTOR_MAP_PATH,
    ):
        self.db_path = Path(db_path)
        self.output_csv = Path(output_csv)

    def get_companies_from_db(self) -> List[Dict[str, str]]:
        """Reads all distinct (symbol, company_name) pairs from daily_prices."""
        if not self.db_path.exists():
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT symbol, MAX(company_name) AS company_name
                FROM daily_prices
                WHERE symbol IS NOT NULL AND symbol != ''
                GROUP BY symbol
                ORDER BY symbol ASC
                """
            )
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def build_sector_map(self, companies: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Assigns a sector to every company in the list."""
        mapped: List[Dict[str, str]] = []
        for comp in companies:
            symbol = comp["symbol"].strip()
            name = (comp.get("company_name") or "").strip()
            sector = classify_company_sector(symbol, name)
            mapped.append({
                "symbol": symbol,
                "company_name": name,
                "sector": sector,
            })
        return mapped

    def save_to_csv(self, mapped_rows: List[Dict[str, str]]) -> Path:
        """Writes symbol, company_name, and sector to data/sector_map.csv."""
        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "company_name", "sector"])
            writer.writeheader()
            writer.writerows(mapped_rows)
        return self.output_csv

    def run(self, show_output: bool = False) -> List[Dict[str, str]]:
        """Generates data/sector_map.csv from the companies stored in daily_prices."""
        companies = self.get_companies_from_db()
        if not companies:
            if show_output:
                console.print(
                    "[yellow]No companies found in daily_prices. Run price_fetcher.py first![/yellow]"
                )
            return []

        mapped_rows = self.build_sector_map(companies)
        csv_path = self.save_to_csv(mapped_rows)

        if show_output:
            counts = Counter(r["sector"] for r in mapped_rows)

            table = Table(title="CSE Company Sector Mapping Summary")
            table.add_column("Sector", style="bold cyan")
            table.add_column("Companies Mapped", justify="right", style="green")

            for sector, count in counts.most_common():
                table.add_row(sector, str(count))

            console.print(table)
            console.print("\n[bold cyan]Sample Mapped Companies (pprint preview):[/bold cyan]")
            rpprint(mapped_rows, max_length=5, expand_all=True)
            console.print(
                f"\n[bold green]✔ Mapped {len(mapped_rows)} companies to {csv_path}[/bold green]"
            )

        return mapped_rows


if __name__ == "__main__":
    console.print(Panel.fit("[bold white]CSE Company -> Sector Mapper[/bold white]", border_style="cyan"))
    mapper = SectorMapper()
    mapper.run(show_output=True)