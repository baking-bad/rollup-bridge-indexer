import uuid

from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup.models import Model
from dipdup.models import Model
from tortoise import ForeignKeyFieldInstance


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
    ticket_hash = fields.CharField(max_length=78, index=True)


class EtherlinkToken(Model):
    class Meta:
        table = 'etherlink_token'
        model = 'models.EtherlinkToken'

    id = fields.CharField(max_length=40, pk=True)
    name = fields.TextField(null=True)
    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_id',
        to_field='id',
    )


class TezosAbstractOperation:
    class Meta:
        abstract = True

    id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(index=True)
    level = fields.IntField(index=True)
    operation_hash = fields.CharField(max_length=64)
    counter = fields.IntField()
    nonce = fields.IntField(null=True)
    initiator = fields.CharField(max_length=36)
    sender = fields.CharField(max_length=36)
    target = fields.CharField(max_length=36)


class TezosDepositEvent(TezosAbstractOperation, Model):
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


class TezosWithdrawEvent(TezosAbstractOperation, Model):
    class Meta:
        table = 'l1_withdraw'
        model = 'models.TezosWithdrawEvent'

    # l1_account = fields.CharField(max_length=36)
    # ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
    #     model_name=TezosTicket.Meta.model,
    #     source_field='ticket_id',
    #     to_field='id',
    # )
    # amount = fields.TextField()
    outbox_level = fields.IntField(index=True)
    outbox_msg_id = fields.IntField(index=True)


class EtherlinkEventBasedModel:
    class Meta:
        abstract = True

    id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(index=True)
    level = fields.IntField(index=True)
    address = fields.CharField(max_length=40)
    log_index = fields.IntField()
    transaction_hash = fields.CharField(max_length=64)
    transaction_index = fields.IntField()


class EtherlinkDepositEvent(Model, EtherlinkEventBasedModel):
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
    inbox_level = fields.IntField(index=True)
    inbox_msg_id = fields.IntField(index=True)


class EtherlinkWithdrawEvent(Model, EtherlinkEventBasedModel):
    class Meta:
        table = 'l2_withdraw'
        model = 'models.EtherlinkWithdrawEvent'

    l2_account = fields.CharField(max_length=40)
    l1_account = fields.CharField(max_length=36)
    l2_token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
        model_name=EtherlinkToken.Meta.model,
        source_field='token_id',
        to_field='id',
    )
    amount = fields.TextField()
    outbox_level = fields.IntField(index=True)
    outbox_msg_id = fields.IntField(index=True)


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


class EtherlinkTokenHolder(Model):
    class Meta:
        table = 'l2_token_holder'
        model = 'models.etherlink.TokenHolder'
        maxsize = 2 ** 20
        unique_together = ('token', 'holder',)

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
