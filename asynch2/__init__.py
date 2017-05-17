# -*- coding: utf-8 -*-
import asyncio

from asynch2.connection import HTTP2Connection
from asynch2.utils import get_ssl_context


async def create_connection(host, port, *, loop=None, **kwargs):
    """Open an HTTP/2 connection to the specified host/port.
    """
    loop = loop or asyncio.get_event_loop()
    connection = HTTP2Connection(loop=loop, client_side=True)
    await loop.create_connection(
        lambda: connection,
        host=host,
        port=port,
        ssl=get_ssl_context(),
        **kwargs,
    )
    return connection
