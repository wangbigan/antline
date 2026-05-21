"""Tests for assessment Markdown rendering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from antline.core.assessment_md import render_assessment_report
from antline.core.models import (
    AssessmentRisk,
    FieldMapping,
    Requirement,
    RequirementAssessment,
    RequirementStatus,
)


def _make_requirement(with_assessment: bool = True) -> Requirement:
    req = Requirement(
        id="REQ-20260508-001",
        name="Test Requirement",
        background="Hospital needs data",
        goal="Build patient table",
        status=RequirementStatus.ASSESSED,
    )
    if with_assessment:
        req.assessment = RequirementAssessment(
            feasible=True,
            report_path="reports/REQ-20260508-001_assessment.md",
            source_ids=["SRC-001"],
            field_mappings=[
                FieldMapping(
                    target_field="patients.id",
                    source_field="patient_id",
                    source_table="patients",
                    mapping_type="direct",
                    risk="low",
                ),
                FieldMapping(
                    target_field="patients.name",
                    source_field="patient_name",
                    source_table="patients",
                    mapping_type="transform",
                    risk="medium",
                ),
            ],
            risks=[
                AssessmentRisk(
                    level="high",
                    description="Missing data for name",
                    source_table="patients",
                    target_field="patients.name",
                ),
            ],
            assessed_at=datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone(timedelta(hours=8))),
        )
    return req


def test_render_assessment_report_with_data() -> None:
    """Render should produce Markdown with all assessment sections."""
    req = _make_requirement(with_assessment=True)
    md = render_assessment_report(req)

    assert "REQ-20260508-001" in md
    assert "Test Requirement" in md
    assert "Hospital needs data" in md
    assert "Build patient table" in md
    assert "FEASIBLE" in md
    assert "SRC-001" in md
    assert "2026-05-08" in md
    assert "patients.id" in md
    assert "patient_id" in md
    assert "direct" in md
    assert "transform" in md
    assert "high" in md
    assert "Missing data for name" in md
    assert "## Risks" in md
    assert "## Reviewer Notes" in md


def test_render_assessment_report_no_assessment() -> None:
    """Render without assessment should return empty string."""
    req = _make_requirement(with_assessment=False)
    md = render_assessment_report(req)
    assert md == ""


def test_render_assessment_report_no_background_goal() -> None:
    """Render should omit optional fields when absent."""
    req = _make_requirement(with_assessment=True)
    req.background = ""
    req.goal = ""
    md = render_assessment_report(req)

    assert "**Background:**" not in md
    assert "**Goal:**" not in md
    assert "Test Requirement" in md  # name still present


def test_render_assessment_report_no_risks() -> None:
    """Render without risks should not include risks section."""
    req = _make_requirement(with_assessment=True)
    req.assessment.risks = []  # type: ignore[union-attr]
    md = render_assessment_report(req)

    assert "## Risks" not in md
