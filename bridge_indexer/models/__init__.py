import uuid

import orjson
from dipdup import fields
from dipdup.models import Model
from tortoise import ForeignKeyFieldInstance
from tortoise import OneToOneFieldInstance
from tortoise.fields.data import JSONField

from bridge_indexer.models.enum import BridgeOperationKind
from bridge_indexer.models.enum import BridgeOperationStatus
from bridge_indexer.models.enum import BridgeOperationType
from bridge_indexer.models.enum import RollupInboxMessageType
from bridge_indexer.models.enum import RollupOutboxMessageBuilder
from bridge_indexer.models.enum import _custom_default


class DatetimeModelMixin:
    created_at = fields.DatetimeField(db_index=True, auto_now_add=True)
    updated_at = fields.DatetimeField(db_index=True, auto_now=True)


class AbstractBlockchainOperation(Model):
    class Meta:
        abstract = True

    id = fields.UUIDField(primary_key=True)
    timestamp = fields.DatetimeField(db_index=True)
    level = fields.IntField(db_index=True)


class TezosToken(Model):
    class Meta:
        table = 'tezos_token'
        model = 'models.TezosToken'

    id = fields.TextField(primary_key=True)
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

    hash = fields.CharField(primary_key=True, max_length=78)
    ticketer_address = fields.CharField(max_length=36)
    ticket_id = fields.TextField(default='0')
    token: ForeignKeyFieldInstance[TezosToken] = fields.ForeignKeyField(
        model_name=TezosToken.Meta.model,
        source_field='token_id',
        to_field='id',
        null=True,
    )
    metadata = fields.TextField(null=True)
    whitelisted = fields.BooleanField(db_index=True, null=True, default=None)


class EtherlinkToken(Model):
    class Meta:
        table = 'etherlink_token'
        model = 'models.EtherlinkToken'

    id = fields.CharField(max_length=40, primary_key=True)
    name = fields.TextField(null=True)
    symbol = fields.TextField(null=True)
    decimals = fields.IntField(default=0)
    ticket: OneToOneFieldInstance[TezosTicket] = fields.OneToOneField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_hash',
        to_field='hash',
    )


class RollupCementedCommitment(DatetimeModelMixin, Model):
    class Meta:
        table = 'rollup_commitment'
        model = 'models.RollupCementedCommitment'

    id = fields.BigIntField(primary_key=True)
    inbox_level = fields.IntField(db_index=True)
    state = fields.CharField(max_length=54)
    hash = fields.CharField(max_length=54, db_index=True)

    outbox_messages: fields.ReverseRelation['RollupOutboxMessage']


class AbstractRollupMessage(DatetimeModelMixin, Model):
    class Meta:
        abstract = True

    level = fields.IntField(db_index=True)
    index = fields.IntField(db_index=True)
    message = JSONField(
        encoder=lambda x: orjson.dumps(x, default=_custom_default, option=orjson.OPT_INDENT_2).decode(),
    )
    parameters_hash = fields.CharField(max_length=32, db_index=True, null=True)


class RollupInboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_inbox_message'
        model = 'models.RollupInboxMessage'

        unique_together = ('level', 'index')
        ordering = ('level', 'index')

    id = fields.BigIntField(primary_key=True)
    type = fields.EnumField(RollupInboxMessageType)

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class RollupOutboxMessage(AbstractRollupMessage):
    class Meta:
        table = 'rollup_outbox_message'
        model = 'models.RollupOutboxMessage'

        unique_together = ('level', 'index')
        ordering = ('level', 'index')

    id = fields.UUIDField(primary_key=True)
    builder = fields.EnumField(RollupOutboxMessageBuilder, null=False, default=RollupOutboxMessageBuilder.kernel)
    proof = fields.TextField(null=True)
    commitment: ForeignKeyFieldInstance[RollupCementedCommitment] = fields.ForeignKeyField(
        model_name=RollupCementedCommitment.Meta.model,
        source_field='commitment_id',
        to_field='id',
        null=True,
    )
    cemented_at = fields.DatetimeField(db_index=True, null=False)
    cemented_level = fields.IntField(null=False)

    failure_count = fields.IntField(null=True, default=None)

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
    parameters_hash = fields.CharField(max_length=32, db_index=True, null=True)

    bridge_deposits: fields.ReverseRelation['BridgeDepositOperation']


class TezosWithdrawOperation(AbstractTezosOperation):
    class Meta:
        table = 'l1_withdrawal'
        model = 'models.TezosWithdrawOperation'

    outbox_message: ForeignKeyFieldInstance[RollupOutboxMessage] = fields.ForeignKeyField(
        model_name=RollupOutboxMessage.Meta.model,
        source_field='outbox_message_id',
        to_field='id',
        db_index=True,
        unique=True,
        null=False,
    )
    amount = fields.TextField(null=True)

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractEtherlinkOperation(AbstractBlockchainOperation):
    class Meta:
        abstract = True

    transaction_hash = fields.CharField(max_length=64)
    transaction_index = fields.IntField()
    log_index = fields.IntField(null=True)
    address = fields.CharField(max_length=40)


class EtherlinkDepositOperation(AbstractEtherlinkOperation):
    class Meta:
        table = 'l2_deposit'
        model = 'models.EtherlinkDepositOperation'

        ordering = ('-level', '-transaction_index', '-log_index')

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

        ordering = ('-level', '-transaction_index', '-log_index')

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
    fast_payload = fields.BinaryField(null=True)
    parameters_hash = fields.CharField(max_length=32, db_index=True, null=True)
    kernel_withdrawal_id = fields.IntField(db_index=True, unique=True, null=True)

    bridge_withdrawals: fields.ReverseRelation['BridgeWithdrawOperation']


class AbstractBridgeOperation(DatetimeModelMixin, Model):
    class Meta:
        abstract = True

    id = fields.UUIDField(primary_key=True)


class BridgeOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_operation'
        model = 'models.BridgeOperation'

        ordering = ('-created_at',)

    l1_account = fields.CharField(max_length=36, db_index=True)
    l2_account = fields.CharField(max_length=40, db_index=True)
    type = fields.EnumField(enum_type=BridgeOperationType, db_index=True)
    kind = fields.EnumField(enum_type=BridgeOperationKind, db_index=True, null=True)
    is_completed = fields.BooleanField(default=False, db_index=True)
    is_successful = fields.BooleanField(default=False, db_index=True)
    status = fields.EnumField(enum_type=BridgeOperationStatus, db_index=True, null=True)


class BridgeDepositOperation(AbstractBridgeOperation):
    class Meta:
        table = 'bridge_deposit'
        model = 'models.BridgeDepositOperation'

        ordering = ('-created_at',)

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

        ordering = ('-created_at',)

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

    id = fields.UUIDField(primary_key=True)
    token = fields.TextField(db_index=True)
    holder = fields.TextField(db_index=True)
    balance = fields.DecimalField(decimal_places=0, max_digits=78, default=0)
    turnover = fields.DecimalField(decimal_places=0, max_digits=96, default=0)
    tx_count = fields.BigIntField(default=0)
    last_seen = fields.BigIntField(null=True)

    @classmethod
    def get_pk(cls, token: str, holder: str) -> uuid.UUID:
        return uuid.uuid5(namespace=uuid.NAMESPACE_OID, name=f'{token}_{holder}')
