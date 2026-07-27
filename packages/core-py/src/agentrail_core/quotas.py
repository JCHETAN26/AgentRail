"""Durable organisation quota accounting."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from agentrail_core.db import Base


class OrganisationQuotaPeriod(Base):
    """One monthly usage counter for one organisation.

    The row is charged in the same transaction that creates the evaluation run,
    so retries and failed writes cannot drift the budget ledger away from the
    work it protects.
    """

    __tablename__ = "organisation_quota_periods"
    __table_args__ = (
        CheckConstraint(
            "evaluation_item_limit >= 1", name="ck_organisation_quota_periods_limit_positive"
        ),
        CheckConstraint(
            "evaluation_items_used >= 0", name="ck_organisation_quota_periods_used_non_negative"
        ),
        CheckConstraint(
            "evaluation_items_used <= evaluation_item_limit",
            name="ck_organisation_quota_periods_used_within_limit",
        ),
        Index(
            "uq_organisation_quota_periods_organisation_period",
            "organisation_id",
            "period_start",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    organisation_id: Mapped[str] = mapped_column(
        String(26), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    evaluation_item_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluation_items_used: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
