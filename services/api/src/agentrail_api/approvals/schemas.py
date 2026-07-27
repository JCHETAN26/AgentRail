"""Public contracts for approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, WithJsonSchema, model_validator

from agentrail_core.approvals import ApprovalState

#: A free-form JSON object that the *client* sends.
#:
#: Bare ``dict[str, Any]`` emits ``{"type": "object"}`` with no
#: ``additionalProperties``, which openapi-typescript renders as
#: ``Record<string, never>`` — an object permitting no properties at all. That
#: is unusable from the console and, worse, is a lie about what the API accepts.
#: Response-only dictionaries get away with it because reading an object never
#: checks the index signature; a request body does not.
JsonObject = Annotated[
    dict[str, Any], WithJsonSchema({"type": "object", "additionalProperties": True})
]


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
    edited_arguments: JsonObject | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _edits_belong_to_approvals(self) -> DecideApprovalRequest:
        if not self.approve and self.edited_arguments is not None:
            raise ValueError("edited_arguments is only valid when approving")
        return self
