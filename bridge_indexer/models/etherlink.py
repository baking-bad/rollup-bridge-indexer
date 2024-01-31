import uuid

from dipdup import fields
from dipdup.models import Model


class TokenHolder(Model):
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


