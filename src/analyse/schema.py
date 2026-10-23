"""
Pydantic Schema for CSE News Agent AI Classifier.
Forces the LLM to return predictable, validated fields for every news story.
"""

from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Official CSE-aligned sector buckets + Market-Wide (rates/FX/budget) + Unrelated
SectorType = Literal[
    "Banks",
    "Diversified Financials",
    "Insurance",
    "Hotels & Travels",
    "Construction & Engineering",
    "Manufacturing & Materials",
    "Food, Beverage & Tobacco",
    "Plantations",
    "Telecommunications & IT",
    "Energy & Utilities",
    "Healthcare",
    "Retails & Consumer",
    "Real Estate",
    "Transportation & Logistics",
    "Market-Wide",
    "Unrelated"
]

ImportanceType = Literal["trivial","moderate","major"]
ConfidenceType = Literal["LOW", "MEDIUM", "HIGH"]

class StoryClassification(BaseModel):
    """Structured output format for a single news story."""

    sector: SectorType = Field(
        description=(
            "The primary CSE market sector this story affects."
            "Use 'Market-Wide' for interset rates, exchange rates, inflation, or national budget news."
            "Use 'Unrelated' for now-financial news, sports, or politics with no market impact"
        )
    )
    sentiment_score: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Score from -1.0 (very bad news for the sector) to +1.0 (very good news for the sector)."
            "Use 0.0 for neutral news."
        ),
    )
    importance: ImportanceType = Field(
        description="How significant the story is for the sector: 'trivial', 'moderate' or 'major'."
    )
    is_new: bool = Field(
        description="True if this is a genuinely new evnet; False if it repeats a story from the last 7 days."
    )
    confidence: ConfidenceType = Field(
        description="How certain the classification is: 'LOW', 'MEDIUM' OR 'HIGH'."
    )
    reasoning: str = Field(
        min_length=5,
        max_length=220,
        description="One concise sentence explaining why this story is good or bad for the sector."
    )
    counter_argument: str = Field(
        min_length=5,
        max_length=220,
        description=(
            "One concise sentence nothing a caveat, risk, or opposing angle "
            "(e.g., 'Lower interest rates boost borrowing but shrink bank net interest margins)."
        ),
    )

    @field_validator("sentiment_score")
    @classmethod
    def round_score(cls, val:float) -> float:
        """Rounds the sentiment score to 2 decimal places."""
        return round(float(val),2)