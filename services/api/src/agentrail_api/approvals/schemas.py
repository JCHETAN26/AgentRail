"""Public contracts for approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from agentrail_core.approvals import ApprovalState


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str
    run_item_id: str
    trajectory_id: str | None = None
    tool: str
    risk_level: str
    state: ApprovalState
    requested_arguments: dict[str, Any]
    edited_arguments: dict[str, Any] | None = None
    reason: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]


class DecideApprovalRequest(BaseModel):
    """Approve, approve-with-edits, or reject.

    ``edited_arguments`` is only meaningful on an approval: there is nothing to
    edit about an action that will not run.
    """

    approve: bool
    edited_arguments: dict[str, Any] | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _edits_belong_to_approvals(self) -> DecideApprovalRequest:
        if not self.approve and self.edited_arguments is not None:
            raise ValueError("edited_arguments is only valid when approving")
        return self
