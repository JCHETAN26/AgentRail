from __future__ import annotations

import pytest

from agentrail_core.ids import is_sortable_id, new_sortable_id


class TestSortableIds:
    def test_has_the_ulid_length_and_alphabet(self) -> None:
        identifier = new_sortable_id()

        assert len(identifier) == 26
        assert is_sortable_id(identifier)

    def test_ids_generated_later_sort_later(self) -> None:
        earlier = new_sortable_id(timestamp_ms=1_700_000_000_000, randomness=b"\x00" * 10)
        later = new_sortable_id(timestamp_ms=1_700_000_000_001, randomness=b"\x00" * 10)

        assert earlier < later

    def test_lexicographic_order_matches_timestamp_order_across_many_values(self) -> None:
        base = 1_700_000_000_000
        ids = [
            new_sortable_id(timestamp_ms=base + step, randomness=b"\x01" * 10)
            for step in range(200)
        ]

        assert ids == sorted(ids)

    def test_same_millisecond_ids_are_distinct(self) -> None:
        ids = {new_sortable_id(timestamp_ms=1_700_000_000_000) for _ in range(1000)}

        assert len(ids) == 1000

    def test_encoding_is_deterministic_for_fixed_inputs(self) -> None:
        first = new_sortable_id(timestamp_ms=1_700_000_000_000, randomness=b"\x00" * 10)
        second = new_sortable_id(timestamp_ms=1_700_000_000_000, randomness=b"\x00" * 10)

        assert first == second

    @pytest.mark.parametrize("timestamp", [-1, 1 << 48])
    def test_out_of_range_timestamps_are_rejected(self, timestamp: int) -> None:
        with pytest.raises(ValueError, match="ULID range"):
            new_sortable_id(timestamp_ms=timestamp)

    def test_wrong_randomness_length_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="10 bytes"):
            new_sortable_id(randomness=b"\x00")

    @pytest.mark.parametrize("candidate", ["", "short", "U" * 26, "a" * 26])
    def test_invalid_candidates_are_not_sortable_ids(self, candidate: str) -> None:
        # 'U' is excluded from Crockford base32; lowercase is not the canonical form.
        assert is_sortable_id(candidate) is False
