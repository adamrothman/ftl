# -*- coding: utf-8 -*-
import ssl
from asyncio import Event


def default_ssl_context() -> ssl.SSLContext:
    """Creates an SSL context suitable for use with HTTP/2. See:
    https://python-hyper.org/projects/h2/en/stable/negotiating-http2.html#client-setup-example
    """
    # Get the basic context from the standard library.
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

    # RFC 7540 Section 9.2: Implementations of HTTP/2 MUST use TLS version 1.2
    # or higher. Disable TLS 1.1 and lower.
    ctx.options |= (
        ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    )

    # RFC 7540 Section 9.2.1: A deployment of HTTP/2 over TLS 1.2 MUST disable
    # compression.
    ctx.options |= ssl.OP_NO_COMPRESSION

    # RFC 7540 Section 9.2.2: "deployments of HTTP/2 that use TLS 1.2 MUST
    # support TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256". In practice, the
    # blacklist defined in this section allows only the AES GCM and ChaCha20
    # cipher suites with ephemeral key negotiation.
    ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")

    # We want to negotiate using NPN and ALPN. ALPN is mandatory, but NPN may
    # be absent, so allow that. This setup allows for negotiation of HTTP/1.1.
    ctx.set_alpn_protocols(["h2", "http/1.1"])

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
