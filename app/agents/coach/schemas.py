from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class WorkoutDay(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    workout_type: Literal[
        "Easy", "Tempo", "Interval", "LongRun", "Recovery", "Rest", "CrossTraining"
    ]
    title: str = Field(
        description="Vietnamese workout title, e.g. 'Chạy nhẹ dưỡng sức'"
    )
    description: str = Field(description="Vietnamese coaching cue, 2-4 sentences")
    target_distance_km: Optional[float] = None
    target_duration_min: Optional[int] = None
    target_pace_range: Optional[str] = None
    target_hr_zone: Optional[int] = Field(default=None, ge=1, le=5)
    target_hr_range: Optional[str] = None
    rpe_target: Optional[int] = Field(default=None, ge=1, le=10)
    nutrition_alert: Optional[str] = None


class WeeklyPlanResult(BaseModel):
    week_start_date: str = Field(description="Monday YYYY-MM-DD")
    week_total_km: float
    training_rationale: str = Field(
        description="Vietnamese 3-4 sentence explanation of this week's logic"
    )
    acwr_projection: float = Field(
        description="Projected ACWR after completing this week"
    )
    days: List[WorkoutDay] = Field(description="Exactly 7 entries Mon-Sun")
    adaptations_made: List[str] = Field(
        description="What the AI adjusted vs ideal plan, Vietnamese"
    )
    recovery_warning: Optional[str] = None
