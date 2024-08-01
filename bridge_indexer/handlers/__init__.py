from dipdup.context import HandlerContext


def setup_handler_logger(ctx: HandlerContext):
    ctx_id = 'ctx' + str(id(ctx.transactions.in_transaction))
    ctx.logger.fmt = ctx_id + ': {}'


def set_handler_logger_prefix(ctx: HandlerContext, prefix: str):
    ctx.logger.fmt = ''.join([prefix, ': {}'])
