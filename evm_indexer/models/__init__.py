import uuid

from dipdup import fields
from dipdup.models import Model


class TokenHolder(Model):
    class Meta:
        table = 'token_holder'
        model = 'models.TokenHolder'
        maxsize = 2 ** 20
        unique_together = ('token', 'holder',)

    id = fields.UUIDField(pk=True)
    token = fields.TextField(index=True)
    holder = fields.TextField(index=True)
    balance = fields.DecimalField(decimal_places=6, max_digits=20, default=0)
    turnover = fields.DecimalField(decimal_places=6, max_digits=20, default=0)
    tx_count = fields.BigIntField(default=0)
    last_seen = fields.BigIntField(null=True)

    @classmethod
    def get_pk(cls, token: str, holder: str) -> uuid.UUID:
        return uuid.uuid5(namespace=uuid.NAMESPACE_OID, name=f'{token}_{holder}')


class EventBasedModel:
    class Meta:
        abstract = True

    id = fields.UUIDField(pk=True)
    timestamp = fields.DatetimeField(index=True)
    level = fields.IntField(index=True)
    address = fields.CharField(max_length=42)
    log_index = fields.IntField()
    transaction_hash = fields.CharField(max_length=66)
    transaction_index = fields.IntField()


class DepositEvent(Model, EventBasedModel):
    class Meta:
        table = 'event_deposit'
        model = 'models.DepositEvent'

    ticket_hash = fields.CharField(max_length=128)
    ticket_owner = fields.CharField(max_length=66)
    receiver = fields.CharField(max_length=66)
    amount = fields.IntField()
    inbox_level = fields.IntField(index=True)
    inbox_msg_id = fields.IntField(index=True)


class WithdrawEvent(Model, EventBasedModel):
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
