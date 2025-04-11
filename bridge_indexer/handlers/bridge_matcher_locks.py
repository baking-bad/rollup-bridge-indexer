class BridgeMatcherLocks:
    pending_tezos_deposits: bool = False
    pending_etherlink_withdrawals: bool = False
    pending_etherlink_deposits: bool = False
    pending_etherlink_xtz_deposits: bool = False
    pending_tezos_withdrawals: bool = False
    pending_inbox: bool = False
    pending_outbox: bool = False
    pending_claimed_fast_withdrawals: bool = False

    @classmethod
    def set_pending_tezos_deposits(cls):
        BridgeMatcherLocks.pending_tezos_deposits = True

    @classmethod
    def set_pending_etherlink_withdrawals(cls):
        BridgeMatcherLocks.pending_etherlink_withdrawals = True

    @classmethod
    def set_pending_etherlink_deposits(cls):
        BridgeMatcherLocks.pending_etherlink_deposits = True

    @classmethod
    def set_pending_etherlink_xtz_deposits(cls):
        BridgeMatcherLocks.pending_etherlink_xtz_deposits = True

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
