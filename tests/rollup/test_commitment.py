import pytest

from bridge_indexer.handlers.rollup_message import OutboxMessageService


class TestCommitment:
    @pytest.mark.parametrize(
        ['outbox_level', 'lcc_level', 'commitment_period', 'challenge_window', 'expected'],
        [
            (726573, 726523, 20, 40, 726623),
            (783296, 783243, 20, 39, 783343),
            (803999, 803943, 20, 38, 804043),
            (803999, 803943, 20, 41, 804063),
        ],
    )
    def test_estimated_cemented_level(
        self, outbox_level: int, lcc_level: int, commitment_period: int, challenge_window: int, expected: int
    ):
        cemented_level = OutboxMessageService._estimate_outbox_message_cemented_level(
            outbox_level,
            lcc_level,
            commitment_period,
            challenge_window,
        )
        assert cemented_level == expected
