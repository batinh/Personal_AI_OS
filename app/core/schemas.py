# app/core/schemas.py
from pydantic import BaseModel, Field
from typing import List, Literal

# ==========================================
# RUN ANALYSIS SCHEMAS
# ==========================================
class RunAnalysisResult(BaseModel):
    """Schema for the AI's run analysis output."""
    analysis_text: str = Field(description="Detailed run analysis following the required format.")
    gcs_score: int = Field(description="Goal Confidence Score (0-100).")

# ==========================================
# MEMORY EXTRACTION SCHEMAS (ANTI-BLOAT)
# ==========================================
class MemoryItem(BaseModel):
    """Schema for a single extracted memory fact."""
    domain: Literal["sports", "health", "physiological", "lifestyle", "nutrition", "psychology", "general"] = Field(
        description="Strictly select the most relevant domain."
    )
    # [ARCHITECTURE UPDATE] Literal enforces Strict JSON Schema Enum at the API level
    category: Literal[
        "main_goal", 
        "injury_status", 
        "physiological_metrics", 
        "gear_preference", 
        "race_strategy", 
        "training_preference", 
        "general_lifestyle",
        "other"
    ] = Field(
        description="MUST be selected strictly from this exact list. Do NOT invent new categories."
    )
    fact: str = Field(
        description="The core fact or state extracted from the conversation."
    )
    status: Literal["active", "inactive"] = Field(
        description="Use 'active' if the state is ongoing. Use 'inactive' if resolved, healed, or canceled."
    )

class MemoryExtractionResult(BaseModel):
    """Schema for the final JSON output of the Memory Extraction Agent."""
    items: List[MemoryItem] = Field(
        description="List of newly extracted or updated memory states."
    )