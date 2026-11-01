from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preferences(StrictModel):
    minutes: int = Field(strict=True, ge=5, le=480)
    energy: Literal["low", "medium", "high"]


class RecommendationRequest(Preferences):
    exclude_ids: List[str] = Field(default_factory=list, max_length=3)


class Task(BaseModel):
    id: str
    name: str
    description: str = ""
    project_id: str = ""
    project_name: str = ""
    favorite_project: bool = False
    priority: int = 1  # Todoist API: 4 is P1, 1 is P4.
    due: Optional[str] = None
    deadline: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    duration_minutes: Optional[int] = None
    parent_id: Optional[str] = None
    parent_name: Optional[str] = None

    def ai_data(self) -> dict:
        """Send bounded task content, never credentials or unrelated account data."""
        return {
            "task_id": self.id,
            "name": self.name[:300],
            "description": self.description[:700],
            "description_truncated": len(self.description) > 700,
            "project": self.project_name[:150],
            "priority": "P{}".format(5 - self.priority),
            "due": self.due,
            "deadline": self.deadline,
            "labels": [label[:60] for label in self.labels[:8]],
            "recorded_duration_minutes": self.duration_minutes,
            "parent_task": self.parent_name[:300] if self.parent_name else None,
        }


class SuggestedAction(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=300)
    estimated_minutes: int = Field(strict=True, ge=1, le=480)
    why: str = Field(min_length=1, max_length=500)
    first_step: str = Field(min_length=1, max_length=500)
    assumptions: List[str] = Field(max_length=3)


class AIRecommendations(StrictModel):
    recommendations: List[SuggestedAction] = Field(min_length=1, max_length=3)


class EasierStep(StrictModel):
    action: str = Field(min_length=1, max_length=350)
    estimated_minutes: int = Field(strict=True, ge=1, le=480)


class AIBreakdown(StrictModel):
    steps: List[EasierStep] = Field(min_length=1, max_length=3)
    assumptions: List[str] = Field(max_length=3)


class EasierRequest(Preferences):
    # The server retrieves the actual recommendation; no model output is trusted
    # as the identity of a task or as permission to perform an action.
    recommendation_id: str = Field(min_length=1, max_length=100)
