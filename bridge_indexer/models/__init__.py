from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
from dipdup import fields
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
from dipdup.models import Model
from dipdup.models import Model


class EtherlinkEventBasedModel:
    class Meta:
        abstract = True

    id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(index=True)
    level = fields.IntField(index=True)
    address = fields.CharField(max_length=42)
    log_index = fields.IntField()
    transaction_hash = fields.CharField(max_length=66)
    transaction_index = fields.IntField()


class EtherlinkDepositEvent(Model, EtherlinkEventBasedModel):
    class Meta:
        table = 'l2_deposit'
        model = 'models.EtherlinkDepositEvent'

    ticket_hash = fields.CharField(max_length=128)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=66)
    amount = fields.DecimalField(decimal_places=0, max_digits=78)
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

    ticket_hash = fields.CharField(max_length=78)
    ticket_owner = fields.CharField(max_length=66)
    ticketer = fields.CharField(max_length=66)
    asset_id = fields.TextField()
    l2_receiver = fields.CharField(max_length=66)
    l2_proxy = fields.CharField(max_length=66, null=True)
    amount = fields.IntField()


class TezosWithdrawEvent(TezosAbstractOperation, Model):
    class Meta:
        table = 'l1_withdraw'
        model = 'models.TezosWithdrawEvent'

    sender = fields.CharField(max_length=66)
    ticket_hash = fields.CharField(max_length=128)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=36)
    amount = fields.IntField()
    outbox_level = fields.IntField(index=True)
    outbox_msg_id = fields.IntField(index=True)
