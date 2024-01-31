from dipdup import fields
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
    nonce = fields.IntField()
    initiator = fields.CharField(max_length=36)
    sender = fields.CharField(max_length=36)
    target = fields.CharField(max_length=36)


class TezosDepositEvent(TezosAbstractOperation, Model):
    class Meta:
        table = 'l1_deposit'
        model = 'models.TezosDepositEvent'

    ticket: ForeignKeyFieldInstance[TezosTicket] = fields.ForeignKeyField(
        model_name=TezosTicket.Meta.model,
        source_field='ticket_id',
        to_field='id',
    )
    l1_account = fields.CharField(max_length=36)
    l2_account = fields.CharField(max_length=40)
    amount = fields.TextField()


class TezosWithdrawEvent(TezosAbstractOperation, Model):
    class Meta:
        table = 'l1_withdraw'
        model = 'models.TezosWithdrawEvent'

    sender = fields.CharField(max_length=66)
    ticket_hash = fields.CharField(max_length=78)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=36)
    amount = fields.IntField()
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
    token: ForeignKeyFieldInstance[EtherlinkToken] = fields.ForeignKeyField(
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

    sender = fields.CharField(max_length=66)
    ticket_hash = fields.CharField(max_length=128)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=36)
    amount = fields.DecimalField(decimal_places=0, max_digits=78)
    outbox_level = fields.IntField(index=True)
    outbox_msg_id = fields.IntField(index=True)
