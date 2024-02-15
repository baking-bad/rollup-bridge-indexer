insert into tezos_token (id, contract_address, token_id, name, symbol, decimals, type)
values ('xtz', 'KT1000000000000000000000000000000000', '0', 'Tezos', 'XTZ', 6, 'native');

insert into tezos_ticket (hash, ticketer_address, ticket_id, token_id)
values (
    '10666650643273303508566200220257708314889526103361559239516955374962850039068',
    'KT1Q6aNZ9aGro4DvBKwhKvVdia2UmVGsS9zE',
    '0',
    'xtz'
);

insert into etherlink_token (id, name, ticket_hash)
values (
    'xtz',
    'ethXTZ',
    '10666650643273303508566200220257708314889526103361559239516955374962850039068'
);
