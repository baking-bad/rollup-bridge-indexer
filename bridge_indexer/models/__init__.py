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


class RollupInboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_inbox_message'
        model = 'models.RollupInboxMessage'

    type = fields.TextField()  # todo: fix type
    parameter = fields.JSONField()
    payload = fields.TextField(null=True)

    l1_deposits: fields.ReverseRelation['TezosDepositOperation']
    l2_deposits: fields.ReverseRelation['EtherlinkDepositOperation']


class RollupOutboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_outbox_message'
        model = 'models.RollupOutboxMessage'

    id = fields.UUIDField(pk=True)

    message = fields.JSONField()
    proof = fields.TextField(null=True)

    commitment: ForeignKeyFieldInstance[RollupCementedCommitment] = fields.ForeignKeyField(
        model_name=RollupCementedCommitment.Meta.model,
        source_field='commitment_id',
        to_field='id',
        null=True,
    )
    cemented_at = fields.DatetimeField(index=True, null=True)
    l1_withdrawals: fields.ReverseRelation['TezosWithdrawOperation']
    l2_withdrawals: fields.ReverseRelation['EtherlinkWithdrawOperation']


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
    inbox_message: ForeignKeyFieldInstance[RollupInboxMessage] = fields.ForeignKeyField(
        model_name=RollupInboxMessage.Meta.model,
        source_field='inbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class TezosWithdrawOperation(AbstractTezosOperation):
    class Meta:
        table = 'l1_withdrawal'
        model = 'models.TezosWithdrawOperation'

    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractEtherlinkOperation(AbstractBlockchainOperation):
    class Meta:
        abstract = True

    transaction_hash = fields.CharField(max_length=64)
    transaction_index = fields.IntField()
    log_index = fields.IntField()
    address = fields.CharField(max_length=40)


class EtherlinkDepositOperation(AbstractEtherlinkOperation):
    class Meta:
        table = 'l2_deposit'
        model = 'models.EtherlinkDepositOperation'

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
    )
    amount = fields.TextField()
    inbox_message: ForeignKeyFieldInstance[RollupInboxMessage] = fields.ForeignKeyField(
        model_name=RollupInboxMessage.Meta.model,
        source_field='inbox_message_id',
        to_field='id',
        unique=True,
        null=True,
    )

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
    amount = fields.TextField()
    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        unique=True,
    )

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractBridgeOperation(DatetimeModelMixin, Model):
    class Meta:
        abstract = True
        ordering = ['-created_at']

    id = fields.UUIDField(pk=True)


class BridgeOperationType(Enum):
    deposit: str = 'deposit'
    withdrawal: str = 'withdrawal'


class BridgeOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_operation'
        model = 'models.BridgeOperation'

    l1_account = fields.CharField(max_length=36, index=True)
    l2_account = fields.CharField(max_length=40, index=True)
    type = fields.EnumField(enum_type=BridgeOperationType, index=True)
    is_completed = fields.BooleanField(default=False, index=True)
    is_successful = fields.BooleanField(default=False, index=True)


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


# class DipDupHandlerLog(DatetimeModelMixin, Model):
#     class Meta:
#         table = 'dipdup_handler_log'
#         model = 'models.DipDupHandlerLog'
#
#     id = fields.IntField(primary_key=True)
#     tx_id = fields.TextField(index=True)
#     ctx_id = fields.CharField(max_length=16)
#     message = fields.TextField(null=False)
