from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preferences(StrictModel):
    minutes: int = Field(strict=True, ge=5, le=480)
    energy: Literal["low", "medium", "high"]


class RecommendationRequest(Preferences):
    exclude_ids: List[str] = Field(default_factory=list, max_length=3)


class OverviewRequest(StrictModel):
    refresh: bool = False


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


class OverviewWorkstream(StrictModel):
    stream_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    order: int = Field(strict=True, ge=0, le=1000)


class OverviewArea(StrictModel):
    area_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    order: int = Field(strict=True, ge=0, le=1000)
    workstreams: List[OverviewWorkstream] = Field(min_length=1, max_length=20)


class OverviewTaxonomy(StrictModel):
    areas: List[OverviewArea] = Field(min_length=1, max_length=12)


class OverviewPlacement(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    stream_id: str = Field(min_length=1, max_length=80)
    order: int = Field(strict=True, ge=0, le=10000)
    emphasis: Literal["focus", "standard", "quiet"]
    attention: Optional[str] = Field(default=None, max_length=240)


class OverviewLayout(StrictModel):
    items: List[OverviewPlacement] = Field(max_length=15)


class OverviewNewWorkstream(StrictModel):
    area_id: str = Field(min_length=1, max_length=80)
    area_title: str = Field(min_length=1, max_length=120)
    stream_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    order: int = Field(strict=True, ge=0, le=1000)


class OverviewMove(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)
    stream_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=240)


class OverviewAudit(StrictModel):
    new_workstreams: List[OverviewNewWorkstream] = Field(default_factory=list, max_length=20)
    moves: List[OverviewMove] = Field(default_factory=list, max_length=100)


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


class RecommendationAction(StrictModel):
    recommendation_id: str = Field(min_length=1, max_length=100)


class ProgressRequest(RecommendationAction):
    progress: str = Field(min_length=1, max_length=3000)


class ProgressPlan(StrictModel):
    reply: str = Field(min_length=1, max_length=1000)
    updated_title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    updated_description: Optional[str] = Field(default=None, max_length=3000)
    complete: bool = False
    new_tasks: List["TaskDraft"] = Field(default_factory=list, max_length=5)


class ProgressConfirm(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=100)
    updated_title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    updated_description: Optional[str] = Field(default=None, max_length=3000)
    complete: bool = False
    new_tasks: List["TaskDraft"] = Field(default_factory=list, max_length=5)


class TaskDraft(StrictModel):
    content: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=3000)
    project_id: Optional[str] = Field(default=None, max_length=100)
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    priority: int = Field(default=1, strict=True, ge=1, le=4)  # Todoist API: 4 is P1, 1 is P4.


class ChatTaskAction(StrictModel):
    operation: Literal["update", "complete"]
    task_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)
    content: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=3000)
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    clear_due: bool = False
    priority: Optional[int] = Field(default=None, strict=True, ge=1, le=4)


class ChatActionDecision(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    decision: Literal["confirm", "cancel"]


class AIChatReply(StrictModel):
    reply: str = Field(min_length=1, max_length=6000)
    drafts: List[TaskDraft] = Field(default_factory=list, max_length=35)
    actions: List[ChatTaskAction] = Field(default_factory=list, max_length=35)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=12000)


class ConfirmedDraft(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=3000)
    project_id: Optional[str] = Field(default=None, max_length=100)
    due_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    priority: int = Field(default=1, strict=True, ge=1, le=4)


class ConfirmDraftsRequest(StrictModel):
    drafts: List[ConfirmedDraft] = Field(min_length=1, max_length=35)
