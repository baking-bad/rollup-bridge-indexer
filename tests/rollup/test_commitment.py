import pytest
from pydantic import Field
from pydantic import create_model

from bridge_indexer.handlers.rollup_message import OutboxMessageService
from bridge_indexer.handlers.service_container import ProtocolConstantStorage

TestProtocolConstantStorage = create_model(
    "TestProtocolConstantStorage",
    __base__=ProtocolConstantStorage,
    time_between_blocks=(int, 0),
    smart_rollup_commitment_period=(int, Field(validation_alias='commitment_period')),
    smart_rollup_challenge_window=(int, Field(validation_alias='challenge_window')),
    smart_rollup_timeout_period=(int, 0),
    smart_rollup_max_active_outbox_levels=(int, 0),
    smart_rollup_max_outbox_messages_per_level=(int, 0),
)


class TestCommitment:
    @pytest.mark.parametrize(
        ['outbox_level', 'lcc_inbox_level', 'commitment_period', 'challenge_window', 'expected'],
        [
            (726573, 726523, 20, 40, 726628),
            (726585, 726523, 20, 40, 726648),
            (726585, 726523, 20, 60, 726668),
            (783296, 783243, 20, 39, 783348),
            (803999, 803943, 20, 38, 804048),
            (803999, 803943, 20, 41, 804068),
        ],
    )
    def test_estimated_cemented_level(
        self, outbox_level: int, lcc_inbox_level: int, commitment_period: int, challenge_window: int, expected: int
    ):

        protocol = TestProtocolConstantStorage.model_validate(locals())
        cemented_level = OutboxMessageService.estimate_outbox_message_cemented_level(
            outbox_level,
            lcc_inbox_level,
            protocol,
        )
        assert cemented_level == expected
