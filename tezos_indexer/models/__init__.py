from dipdup import fields
from dipdup.models import Model


class EventBasedModel:
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


class DepositEvent(EventBasedModel, Model):
    class Meta:
        table = 'event_deposit'
        model = 'models.DepositEvent'

    ticket_hash = fields.CharField(max_length=78)
    ticket_owner = fields.CharField(max_length=66)
    ticketer = fields.CharField(max_length=66)
    asset_id = fields.TextField()
    l2_receiver = fields.CharField(max_length=66)
    l2_proxy = fields.CharField(max_length=66)
    amount = fields.IntField()


class WithdrawEvent(EventBasedModel, Model):
    class Meta:
        table = 'event_withdraw'
        model = 'models.WithdrawEvent'

    sender = fields.CharField(max_length=66)
    ticket_hash = fields.CharField(max_length=128)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=36)
    amount = fields.IntField()
    outbox_level = fields.IntField(index=True)
    outbox_msg_id = fields.IntField(index=True)
