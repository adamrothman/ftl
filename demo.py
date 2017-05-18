# -*- coding: utf-8 -*-
"""This file demonstrates usage of asynch2 and serves as a very basic
functionality test. It makes requests to https://http2.golang.org which
provides a number of simple HTTP/2 endpoints to play with.
"""
import asyncio
import logging
import signal
from argparse import ArgumentParser
from pathlib import Path

from asynch2 import create_connection


HOST = 'http2.golang.org'


def _print_headers(headers):
    for k, v in headers.items():
        print(f'{k}:\t{v}')


def print_headers(headers):
    print('∨∨∨∨ HEADERS ∨∨∨∨')
    _print_headers(headers)
    print('∧∧∧∧ HEADERS ∧∧∧∧\n')


def print_data(data):
    print('∨∨∨∨ DATA ∨∨∨∨')
    print(data.decode())
    print('∧∧∧∧ DATA ∧∧∧∧\n')


def print_trailers(trailers):
    print('∨∨∨∨ TRAILERS ∨∨∨∨')
    _print_headers(trailers)
    print('∧∧∧∧ TRAILERS ∧∧∧∧\n')


async def clockstream(http2):
    stream_id = await http2.send_request(
        'GET',
        'https',
        HOST,
        '/clockstream',
        end_stream=True,
    )

    response = await http2.read_headers(stream_id)
    print_headers(response)

    signal.signal(
        signal.SIGINT,
        lambda s, f: asyncio.ensure_future(http2.reset_stream(stream_id)),
    )

    print('∨∨∨∨ DATA ∨∨∨∨')
    async for frame in http2.stream_frames(stream_id):
        print(frame.decode(), end='')
    print('∧∧∧∧ DATA ∧∧∧∧\n')


async def crc32(http2, data):
    stream_id = await http2.send_request('PUT', 'https', HOST, '/crc32')
    await http2.send_data(stream_id, data, end_stream=True)

    response = await http2.read_headers(stream_id)
    print_headers(response)
    data = await http2.read_data(stream_id)
    print_data(data)
    trailers = await http2.read_trailers(stream_id)
    print_trailers(trailers)


async def echo(http2, data):
    stream_id = await http2.send_request('PUT', 'https', HOST, '/ECHO')
    await http2.send_data(stream_id, data, end_stream=True)

    response = await http2.read_headers(stream_id)
    print_headers(response)
    data = await http2.read_data(stream_id)
    print_data(data)
    trailers = await http2.read_trailers(stream_id)
    print_trailers(trailers)


async def reqinfo(http2):
    stream_id = await http2.send_request(
        'GET',
        'https',
        HOST,
        '/reqinfo',
        additional_headers=[('foo', 'bar')],
        end_stream=True,
    )

    response = await http2.read_headers(stream_id)
    print_headers(response)
    data = await http2.read_data(stream_id)
    print_data(data)
    trailers = await http2.read_trailers(stream_id)
    print_trailers(trailers)


async def main(args, loop):
    http2 = await create_connection(HOST, 443, loop=loop)

    if args.endpoint == 'clockstream':
        await clockstream(http2)
    elif args.endpoint == 'crc32':
        path = Path(args.input).expanduser()
        with path.open(mode='rb') as f:
            data = f.read()
        await crc32(http2, data)
    elif args.endpoint == 'echo':
        data = args.input.encode()
        await echo(http2, data)
    elif args.endpoint == 'reqinfo':
        await reqinfo(http2)


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='display debug output',
    )
    parser.add_argument(
        'endpoint',
        nargs='?',
        default='reqinfo',
        type=str,
        choices=['clockstream', 'crc32', 'echo', 'reqinfo'],
        help='demo endpoint to use',
    )
    parser.add_argument(
        'input',
        nargs='?',
        default=None,
        type=str,
        help='input for endpoints that require it',
    )

    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)
    logging.getLogger('hpack.hpack').setLevel(logging.WARNING)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main(args, loop))
