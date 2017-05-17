# -*- coding: utf-8 -*-
"""This file demonstrates usage of asynch2 and serves as a very basic
functionality test. It makes requests to https://http2.golang.org which
provides a number of simple HTTP/2 endpoints to play with.
"""
import asyncio
import logging

from asynch2 import create_connection


async def clockstream(http2):
    stream_id = http2.request(
        'GET',
        'https',
        host,
        '/clockstream',
        end_stream=True,
    )
    response = await http2.read_response(stream_id)

    print()
    print('response:')
    print(response)
    print()


async def echo(http2):
    stream_id = http2.request('PUT', 'https', host, '/ECHO')
    http2.send_data(stream_id, b'hello world', end_stream=True)
    response = await http2.read_response(stream_id)
    data = await http2.read(stream_id)

    print()
    print('response:')
    print(response)
    print('data:')
    print(data.decode())
    print()


async def reqinfo(http2):
    stream_id = http2.request(
        'GET',
        'https',
        host,
        '/reqinfo',
        additional_headers=[('foo', 'bar')],
        end_stream=True,
    )
    response = await http2.read_response(stream_id)
    data = await http2.read(stream_id)

    print()
    print('response:')
    print(response)
    print('data:')
    print(data.decode())
    print()


async def main(http2):
    await reqinfo(http2)
    await echo(http2)
    await clockstream(http2)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    host = 'http2.golang.org'

    loop = asyncio.get_event_loop()
    http2 = loop.run_until_complete(
        create_connection(host, 443, server_hostname=host),
    )
    asyncio.ensure_future(main(http2), loop=loop)
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
