# -*- coding: utf-8 -*-
import asyncio
from ssl import SSLContext

from ftl.connection import HTTP2ClientConnection
from ftl.utils import default_ssl_context


async def create_connection(
    host,
    port,
    *,
    loop=None,
    secure=True,
    ssl_context=None,
    **kwargs,
):
    """Open an HTTP/2 connection to the specified host/port.
    """
    loop = loop or asyncio.get_event_loop()

    secure = True if port == 443 else secure
    connection = HTTP2ClientConnection(host, loop=loop, secure=secure)
    if not isinstance(ssl_context, SSLContext):
        ssl_context = default_ssl_context()

    await loop.create_connection(
        lambda: connection,
        host=host,
        port=port,
        ssl=ssl_context,
    )

    return connection
