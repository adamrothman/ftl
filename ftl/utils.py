# -*- coding: utf-8 -*-
import ssl
from asyncio import Event


def default_ssl_context() -> ssl.SSLContext:
    """Creates an SSL context suitable for use with HTTP/2. See
    https://tools.ietf.org/html/rfc7540#section-9.2 for what this entails.
    Specifically, we are interested in these points:

        § 9.2: Implementations of HTTP/2 MUST use TLS version 1.2 or higher.
        § 9.2.1: A deployment of HTTP/2 over TLS 1.2 MUST disable compression.

    The h2 project has its own ideas about how this context should be
    constructed but the resulting context doesn't work for us in the standard
    Python Docker images (though it does work under macOS). See
    https://python-hyper.org/projects/h2/en/stable/negotiating-http2.html#client-setup-example
    for more.
    """
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

    # OP_NO_SSLv2, OP_NO_SSLv3, and OP_NO_COMPRESSION are already set by default
    # so we just need to disable the old versions of TLS.
    ctx.options |= (ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1)

    # ALPN and NPN allow upgrades from HTTP/1.1, but these extensions are only
    # supported by recent versions of OpenSSL. Try to set them up, but don't cry
    # if they fail.
    try:
        ctx.set_alpn_protocols(["h2", "http/1.1"])
    except NotImplementedError:
        pass

    try:
        ctx.set_npn_protocols(["h2", "http/1.1"])
    except NotImplementedError:
        pass

    return ctx


class ConditionalEvent(Event):

    def __init__(self, predicate, *, loop=None):
        super().__init__(loop=loop)
        self._predicate = predicate

    def is_set(self) -> bool:
        self.update()
        return super().is_set()

    async def wait(self):
        while not self.update():
            await super().wait()

    def update(self) -> bool:
        result = self._predicate()
        if result:
            self.set()
        else:
            self.clear()
        return result
