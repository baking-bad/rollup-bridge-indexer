import pytest
from pydantic import Field
from pydantic import create_model

from rollup_bridge_indexer.handlers.rollup_message import OutboxMessageService
from rollup_bridge_indexer.handlers.service_container import ProtocolConstantStorage

TestProtocolConstantStorage: type[ProtocolConstantStorage] = create_model(
    'TestProtocolConstantStorage',
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
        ['outbox_level', 'origination_level', 'commitment_period', 'challenge_window', 'expected'],
        [
            # legacy cases — origination is the LCC inbox level shifted onto the same residue class;
            # ETA value must remain identical since the math depends only on the anchor's residue mod P.
            (726573, 726523, 20, 40, 726628),
            (726585, 726523, 20, 40, 726648),
            (726585, 726523, 20, 60, 726668),
            (783296, 783243, 20, 39, 783348),
            (803999, 803943, 20, 38, 804048),
            (803999, 803943, 20, 41, 804068),
            # fresh-rollup cases — origination is far below outbox (no LCC has been produced yet);
            # these are exactly the situations where the old LCC-based heuristic crashed.
            (1015, 1000, 60, 40, 1125),
            (1060, 1000, 60, 40, 1125),
            (1015, 100, 60, 40, 1125),  # multi-period gap, equivalent residue
            (100, 5, 30, 20, 160),
            (50, 0, 25, 50, 105),
        ],
    )
    def test_estimated_cemented_level(
        self, outbox_level: int, origination_level: int, commitment_period: int, challenge_window: int, expected: int
    ):

        protocol = TestProtocolConstantStorage.model_validate(locals())
        cemented_level = OutboxMessageService.estimate_outbox_message_cemented_level(
            outbox_level=outbox_level,
            origination_level=origination_level,
            protocol=protocol,
        )
        assert cemented_level == expected
