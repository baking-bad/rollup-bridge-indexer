class BridgeMatcherLocks:
    pending_tezos_deposits: bool = False
    pending_l2_withdrawals: bool = False
    pending_l2_deposits: bool = False
    pending_l2_xtz_deposits: bool = False
    pending_tezos_withdrawals: bool = False
    pending_inbox: bool = False
    pending_outbox: bool = False
    pending_claimed_fast_withdrawals: bool = False
    # Op-hash matching of L2 Michelson deposits (BridgeMatcher.check_pending_michelson_deposits).
    pending_michelson_deposits: bool = False

    @classmethod
    def set_pending_tezos_deposits(cls):
        BridgeMatcherLocks.pending_tezos_deposits = True

    @classmethod
    def set_pending_michelson_deposits(cls):
        BridgeMatcherLocks.pending_michelson_deposits = True

    @classmethod
    def set_pending_l2_withdrawals(cls):
        BridgeMatcherLocks.pending_l2_withdrawals = True

    @classmethod
    def set_pending_l2_deposits(cls):
        BridgeMatcherLocks.pending_l2_deposits = True

    @classmethod
    def set_pending_l2_xtz_deposits(cls):
        BridgeMatcherLocks.pending_l2_xtz_deposits = True

    @classmethod
    def set_pending_tezos_withdrawals(cls):
        BridgeMatcherLocks.pending_tezos_withdrawals = True

    @classmethod
    def set_pending_inbox(cls):
        BridgeMatcherLocks.pending_inbox = True

    @classmethod
    def set_pending_outbox(cls):
        BridgeMatcherLocks.pending_outbox = True

    @classmethod
    def set_pending_claimed_fast_withdrawals(cls):
        BridgeMatcherLocks.pending_claimed_fast_withdrawals = True
