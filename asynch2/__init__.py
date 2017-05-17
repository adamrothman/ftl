# -*- coding: utf-8 -*-
import asyncio

from asynch2.protocol import HTTP2Protocol
from asynch2.utils import get_http2_ssl_context


async def create_connection(host, port, *, loop=None, **kwargs):
    """Open an HTTP/2 connection to the specified host/port.
    """
    loop = loop or asyncio.get_event_loop()
    protocol = HTTP2Protocol(loop=loop, client_side=True)
    await loop.create_connection(
        lambda: protocol,
        host=host,
        port=port,
        ssl=get_http2_ssl_context(),
        **kwargs,
    )
    return protocol
