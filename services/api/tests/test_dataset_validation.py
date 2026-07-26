"""Dataset parser and validation unit tests."""

from __future__ import annotations

import pytest

from agentrail_api.datasets.service import validate_dataset_content


def test_validates_jsonl_records_and_partitions() -> None:
    validation = validate_dataset_content(
        "jsonl",
        "\n".join(
            [
                '{"id":"one","input":{"q":"a"},"expected":{"ok":true},"partition":"dev"}',
                '{"id":"two","input":{"q":"b"},"expected":{"ok":false}}',
            ]
        ),
    )

    assert validation.item_count == 2
    assert validation.rejected_count == 0
    assert validation.partition_counts == {"default": 1, "dev": 1}
    assert validation.record_schema["required"] == ["id", "input", "expected"]


def test_validation_report_points_to_malformed_jsonl_line() -> None:
    validation = validate_dataset_content("jsonl", '{"id":"ok"}\n{not-json')

    assert validation.item_count == 0
    assert validation.rejected_count == 2
    assert validation.validation_report["rejections"][0] == {
        "line": 2,
        "message": "Expecting property name enclosed in double quotes",
    }
    assert validation.validation_report["rejections"][1]["fields"] == ["input", "expected"]


def test_validates_csv_required_columns() -> None:
    validation = validate_dataset_content("csv", "id,input\ncase-1,hello\n")

    assert validation.item_count == 0
    assert validation.rejected_count == 1
    assert validation.validation_report["rejections"][0]["fields"] == ["expected"]


@pytest.mark.parametrize("content", ['id,input,expected\ncase-1,"{\\"q\\":1}","{\\"ok\\":true}"\n'])
def test_accepts_csv_rows(content: str) -> None:
    validation = validate_dataset_content("csv", content)

    assert validation.item_count == 1
    assert validation.rejected_count == 0
    assert validation.partition_counts == {"default": 1}
