import uuid
from enum import Enum

from dipdup import fields
from dipdup.models import Model
from tortoise import ForeignKeyFieldInstance
from tortoise import OneToOneFieldInstance


class DatetimeModelMixin:
    created_at = fields.DatetimeField(index=True, auto_now_add=True)
    updated_at = fields.DatetimeField(index=True, auto_now=True)


class AbstractBlockchainOperation(Model):
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

    hash = fields.CharField(pk=True, max_length=78)
    ticketer_address = fields.CharField(max_length=36)
    ticket_id = fields.TextField(default='0')
    token: ForeignKeyFieldInstance[TezosToken] = fields.ForeignKeyField(
        model_name=TezosToken.Meta.model,
        source_field='token_id',
        to_field='id',
    )
    metadata = fields.TextField(null=True)
    outbox_interface = fields.TextField()
    whitelisted = fields.BooleanField(index=True, null=True, default=None)


class EtherlinkToken(Model):
    class Meta:
        table = 'etherlink_token'
        model = 'models.EtherlinkToken'

    id = fields.CharField(max_length=40, pk=True)
    name = fields.TextField(null=True)
    symbol = fields.TextField(null=True)
    decimals = fields.IntField(default=0)
    ticket: OneToOneFieldInstance[TezosTicket] = fields.OneToOneField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_hash',
        to_field='hash',
    )


EtherlinkToken._meta.fields_map['ticket'].__module__ = 'dipdup.fields'


class RollupCementedCommitment(DatetimeModelMixin, Model):
    class Meta:
        table = 'rollup_commitment'
        model = 'models.RollupCementedCommitment'

    id = fields.BigIntField(pk=True)
    inbox_level = fields.IntField(index=True)
    state = fields.CharField(max_length=54)
    hash = fields.CharField(max_length=54, index=True)

    outbox_messages: fields.ReverseRelation['RollupOutboxMessage']


class AbstractRollupMessage(DatetimeModelMixin, Model):
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
    message = fields.JSONField()
    parameters_hash = fields.CharField(max_length=32, index=True, null=True)


class RollupInboxMessageType(Enum):
    level_start: str = 'level_start'
    level_info: str = 'level_info'
    transfer: str = 'transfer'
    external: str = 'external'
    level_end: str = 'level_end'


class RollupInboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_inbox_message'
        model = 'models.RollupInboxMessage'

    type = fields.EnumField(RollupInboxMessageType)

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class RollupOutboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_outbox_message'
        model = 'models.RollupOutboxMessage'

    id = fields.UUIDField(pk=True)

    proof = fields.TextField(null=True)
    commitment: ForeignKeyFieldInstance[RollupCementedCommitment] = fields.ForeignKeyField(
        model_name=RollupCementedCommitment.Meta.model,
        source_field='commitment_id',
        to_field='id',
        null=True,
    )
    cemented_at = fields.DatetimeField(index=True, null=False)
    cemented_level = fields.IntField(null=False)

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractTezosOperation(AbstractBlockchainOperation):
    class Meta:
        abstract = True

    operation_hash = fields.CharField(max_length=64)
    counter = fields.IntField()
    nonce = fields.IntField(null=True)
    initiator = fields.CharField(max_length=36)
    sender = fields.CharField(max_length=36)
    target = fields.CharField(max_length=36)


class TezosDepositOperation(AbstractTezosOperation):
    class Meta:
        table = 'l1_deposit'
        model = 'models.TezosDepositOperation'

    l1_account = fields.CharField(max_length=36)
    l2_account = fields.CharField(max_length=40)
    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_hash',
        to_field='hash',
    )
    amount = fields.TextField()
    parameters_hash = fields.CharField(max_length=32, index=True, null=True)

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class TezosWithdrawOperation(AbstractTezosOperation):
    class Meta:
        table = 'l1_withdrawal'
        model = 'models.TezosWithdrawOperation'

    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        index=True,
    )

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractEtherlinkOperation(AbstractBlockchainOperation):
    class Meta:
        abstract = True

        ordering = ['-level', '-transaction_index', '-log_index']

    transaction_hash = fields.CharField(max_length=64)
    transaction_index = fields.IntField()
    log_index = fields.IntField(null=True)
    address = fields.CharField(max_length=40)


class EtherlinkDepositOperation(AbstractEtherlinkOperation):
    class Meta:
        table = 'l2_deposit'
        model = 'models.EtherlinkDepositOperation'

        unique_together = (
            'inbox_message_level',
            'inbox_message_index',
        )

    l2_account = fields.CharField(max_length=40)
    l2_token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
        model_name=EtherlinkToken.Meta.model,
        source_field='token_id',
        to_field='id',
        null=True,
    )
    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_hash',
        to_field='hash',
        null=True,
    )
    ticket_owner = fields.CharField(max_length=40)
    amount = fields.TextField()

    inbox_message_level = fields.IntField(null=True)
    inbox_message_index = fields.IntField(null=True)

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class EtherlinkWithdrawOperation(AbstractEtherlinkOperation):
    class Meta:
        table = 'l2_withdrawal'
        model = 'models.EtherlinkWithdrawOperation'

    l2_account = fields.CharField(max_length=40)
    l1_account = fields.CharField(max_length=36)
    l2_token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
        model_name=EtherlinkToken.Meta.model,
        source_field='token_id',
        to_field='id',
        null=True,
    )
    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_hash',
        to_field='hash',
    )
    l2_ticket_owner = fields.CharField(max_length=40)
    l1_ticket_owner = fields.CharField(max_length=36)
    amount = fields.TextField()
    parameters_hash = fields.CharField(max_length=32, index=True, null=True)
    kernel_withdrawal_id = fields.IntField(index=True, unique=True, null=False)

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractBridgeOperation(DatetimeModelMixin, Model):
    class Meta:
        abstract = True
        ordering = ['-created_at']

    id = fields.UUIDField(pk=True)


class BridgeOperationType(Enum):
    deposit: str = 'deposit'
    withdrawal: str = 'withdrawal'


class BridgeOperationStatus(Enum):
    created: str = 'CREATED'
    finished: str = 'FINISHED'
    failed: str = 'FAILED'

    revertable: str = 'FAILED_INVALID_ROUTING_INFO_REVERTABLE'
    proxy_not_whitelisted: str = 'FAILED_INVALID_ROUTING_PROXY_NOT_WHITELISTED'
    empty_proxy: str = 'FAILED_INVALID_ROUTING_PROXY_EMPTY_PROXY'
    invalid_proxy: str = 'FAILED_INVALID_ROUTING_INVALID_PROXY_ADDRESS'
    inbox_matching_timeout: str = 'FAILED_INBOX_MATCHING_TIMEOUT'

    sealed: str = 'SEALED'
    outbox_expired: str = 'FAILED_OUTBOX_EXPIRED'


class BridgeOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_operation'
        model = 'models.BridgeOperation'

    l1_account = fields.CharField(max_length=36, index=True)
    l2_account = fields.CharField(max_length=40, index=True)
    type = fields.EnumField(enum_type=BridgeOperationType, index=True)
    is_completed = fields.BooleanField(default=False, index=True)
    is_successful = fields.BooleanField(default=False, index=True)
    status = fields.EnumField(enum_type=BridgeOperationStatus, index=True, null=True)


class BridgeDepositOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_deposit'
        model = 'models.BridgeDepositOperation'

    l1_transaction: ForeignKeyFieldInstance[TezosDepositOperation] = fields.ForeignKeyField(
        model_name=TezosDepositOperation.Meta.model,
        source_field='l1_transaction_id',
        to_field='id',
        unique=True,
    )
    l2_transaction: ForeignKeyFieldInstance[EtherlinkDepositOperation] = fields.ForeignKeyField(
        model_name=EtherlinkDepositOperation.Meta.model,
        source_field='l2_transaction_id',
        to_field='id',
        null=True,
        unique=True,
    )
    inbox_message: ForeignKeyFieldInstance[RollupInboxMessage] = fields.ForeignKeyField(
        model_name=RollupInboxMessage.Meta.model,
        source_field='inbox_message_id',
        to_field='id',
        null=True,
    )


class BridgeWithdrawOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_withdrawal'
        model = 'models.BridgeWithdrawOperation'

    l1_transaction: ForeignKeyFieldInstance[TezosWithdrawOperation] = fields.ForeignKeyField(
        model_name=TezosWithdrawOperation.Meta.model,
        source_field='l1_transaction_id',
        to_field='id',
        null=True,
        unique=True,
    )
    l2_transaction: ForeignKeyFieldInstance[EtherlinkWithdrawOperation] = fields.ForeignKeyField(
        model_name=EtherlinkWithdrawOperation.Meta.model,
        source_field='l2_transaction_id',
        to_field='id',
        unique=True,
    )
    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        null=True,
    )


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
