import uuid

from dipdup import fields
from dipdup.models import Model
from tortoise import ForeignKeyFieldInstance


class BlockchainAbstractOperation(Model):
    class Meta:
        abstract = True

    id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(index=True)
    level = fields.IntField(index=True)


class TezosToken(Model):
    class Meta:
        table = 'tezos_token'
        model = 'models.TezosToken'

    id = fields.TextField(pk=True)
    contract_address = fields.CharField(max_length=36)
    token_id = fields.TextField(default='0')
    name = fields.TextField(null=True)
    symbol = fields.TextField(null=True)
    decimals = fields.IntField(default=0)
    type = fields.CharField(max_length=10)


class TezosTicket(Model):
    class Meta:
        table = 'tezos_ticket'
        model = 'models.TezosTicket'

    id = fields.TextField(pk=True)
    token: ForeignKeyFieldInstance[TezosToken] = fields.ForeignKeyField(
        model_name=TezosToken.Meta.model,
        source_field='token_id',
        to_field='id',
    )
    ticketer_address = fields.CharField(max_length=36)
    ticket_id = fields.TextField(default='0')
    ticket_hash = fields.CharField(max_length=78, index=True, unique=True)


class EtherlinkToken(Model):
    class Meta:
        table = 'etherlink_token'
        model = 'models.EtherlinkToken'

    id = fields.CharField(max_length=40, pk=True)
    name = fields.TextField(null=True)
    tezos_ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='tezos_ticket_id',
        to_field='id',
        null=True,
    )
    tezos_ticket_hash = fields.CharField(max_length=78, index=True)


class RollupCommitment(Model):
    class Meta:
        table = 'rollup_commitment'
        model = 'models.RollupCommitment'

    id = fields.BigIntField(pk=True)
    inbox_level = fields.IntField()
    first_level = fields.IntField()
    first_time = fields.DatetimeField()
    # last_level = fields.IntField()
    # last_time = fields.DatetimeField()
    state = fields.CharField(max_length=54)
    hash = fields.CharField(max_length=54)
    status = fields.CharField(max_length=16)

    outbox_messages: fields.ReverseRelation['RollupOutboxMessage']


class AbstractRollupMessage(Model):
    class Meta:
        abstract = True
        unique_together = (
            'level',
            'index',
        )
        ordering = ['level', 'index']

    id = fields.BigIntField(pk=True)
    level = fields.IntField(index=True)
    index = fields.IntField(index=True)


class RollupInboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_inbox_message'
        model = 'models.RollupInboxMessage'

    type = fields.TextField()  # todo: fix type
    parameter = fields.JSONField()
    payload = fields.TextField(null=True)

    l1_deposits: fields.ReverseRelation['TezosDepositEvent']
    l2_deposits: fields.ReverseRelation['EtherlinkDepositEvent']


class RollupOutboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_outbox_message'
        model = 'models.RollupOutboxMessage'

    id = fields.UUIDField(pk=True)

    message = fields.JSONField()
    proof = fields.TextField(null=True)

    commitment: ForeignKeyFieldInstance[RollupCommitment] = fields.ForeignKeyField(
        model_name=RollupCommitment.Meta.model,
        source_field='commitment_id',
        to_field='id',
        null=True,
    )

    l1_withdrawals: fields.ReverseRelation['TezosWithdrawEvent']
    l2_withdrawals: fields.ReverseRelation['EtherlinkWithdrawEvent']


class TezosAbstractOperation(BlockchainAbstractOperation):
    class Meta:
        abstract = True

    operation_hash = fields.CharField(max_length=64)
    counter = fields.IntField()
    nonce = fields.IntField(null=True)
    initiator = fields.CharField(max_length=36)
    sender = fields.CharField(max_length=36)
    target = fields.CharField(max_length=36)


class TezosDepositEvent(TezosAbstractOperation):
    class Meta:
        table = 'l1_deposit'
        model = 'models.TezosDepositEvent'

    l1_account = fields.CharField(max_length=36)
    l2_account = fields.CharField(max_length=40)
    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_id',
        to_field='id',
    )
    amount = fields.TextField()
    inbox_message: ForeignKeyFieldInstance[RollupInboxMessage] = fields.ForeignKeyField(
        model_name=RollupInboxMessage.Meta.model,
        source_field='inbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_deposits: fields.ReverseRelation['BridgeDepositTransaction']


class TezosWithdrawEvent(TezosAbstractOperation):
    class Meta:
        table = 'l1_withdrawal'
        model = 'models.TezosWithdrawEvent'

    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawTransaction']


class EtherlinkAbstractEvent(BlockchainAbstractOperation):
    class Meta:
        abstract = True

    transaction_hash = fields.CharField(max_length=64)
    transaction_index = fields.IntField()
    log_index = fields.IntField()
    address = fields.CharField(max_length=40)


class EtherlinkDepositEvent(EtherlinkAbstractEvent):
    class Meta:
        table = 'l2_deposit'
        model = 'models.EtherlinkDepositEvent'

    l2_account = fields.CharField(max_length=40)
    l2_token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
        model_name=EtherlinkToken.Meta.model,
        source_field='token_id',
        to_field='id',
    )
    amount = fields.TextField()
    inbox_message: ForeignKeyFieldInstance[RollupInboxMessage] = fields.ForeignKeyField(
        model_name=RollupInboxMessage.Meta.model,
        source_field='inbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_deposits: fields.ReverseRelation['BridgeDepositTransaction']


class EtherlinkWithdrawEvent(EtherlinkAbstractEvent):
    class Meta:
        table = 'l2_withdrawal'
        model = 'models.EtherlinkWithdrawEvent'

    l2_account = fields.CharField(max_length=40)
    l1_account = fields.CharField(max_length=36)
    l2_token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
        model_name=EtherlinkToken.Meta.model,
        source_field='token_id',
        to_field='id',
    )
    amount = fields.TextField()
    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawTransaction']


class BridgeDepositTransaction(Model):
    class Meta:
        table = 'bridge_deposit'
        model = 'models.BridgeDepositTransaction'

    id = fields.UUIDField(pk=True)
    l1_transaction: ForeignKeyFieldInstance[TezosDepositEvent] = fields.ForeignKeyField(
        model_name=TezosDepositEvent.Meta.model,
        source_field='l1_transaction_id',
        to_field='id',
        unique=True,
    )
    l2_transaction: ForeignKeyFieldInstance[EtherlinkDepositEvent] = fields.ForeignKeyField(
        model_name=EtherlinkDepositEvent.Meta.model,
        source_field='l2_transaction_id',
        to_field='id',
        null=True,
        unique=True,
    )
    updated_at = fields.DatetimeField(index=True, auto_now=True)


class BridgeWithdrawTransaction(Model):
    class Meta:
        table = 'bridge_withdrawal'
        model = 'models.BridgeWithdrawTransaction'

    id = fields.UUIDField(pk=True)
    l1_transaction: ForeignKeyFieldInstance[TezosWithdrawEvent] = fields.ForeignKeyField(
        model_name=TezosWithdrawEvent.Meta.model,
        source_field='l1_transaction_id',
        to_field='id',
        null=True,
        unique=True,
    )
    l2_transaction: ForeignKeyFieldInstance[EtherlinkWithdrawEvent] = fields.ForeignKeyField(
        model_name=EtherlinkWithdrawEvent.Meta.model,
        source_field='l2_transaction_id',
        to_field='id',
        unique=True,
    )
    updated_at = fields.DatetimeField(index=True, auto_now=True)


class EtherlinkTokenHolder(Model):
    class Meta:
        table = 'l2_token_holder'
        model = 'models.TokenHolder'
        maxsize = 2**20
        unique_together = (
            'token',
            'holder',
        )

    id = fields.UUIDField(pk=True)
    token = fields.TextField(index=True)
    holder = fields.TextField(index=True)
    balance = fields.DecimalField(decimal_places=0, max_digits=78, default=0)
    turnover = fields.DecimalField(decimal_places=0, max_digits=96, default=0)
    tx_count = fields.BigIntField(default=0)
    last_seen = fields.BigIntField(null=True)

    @classmethod
    def get_pk(cls, token: str, holder: str) -> uuid.UUID:
        return uuid.uuid5(namespace=uuid.NAMESPACE_OID, name=f'{token}_{holder}')
