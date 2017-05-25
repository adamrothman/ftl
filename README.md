# ftl
An asynchronous HTTP/2 implementation in Python. The details of the protocol itself (encoding, state management, etc.) are handled by the excellent [h2](https://github.com/python-hyper/hyper-h2) library, and [asyncio](https://docs.python.org/3/library/asyncio.html) provides non-blocking I/O.

## Supported features
* HTTP/2 client
* Flow control
* Server push

## Not supported (yet)
* HTTP/2 server
* Connection upgrade from HTTP/1.x
* Stream priority
* Multiple threads

## Prior art and inspiration
* [aioh2](https://github.com/decentfox/aioh2)
* [hyper](https://github.com/Lukasa/hyper)
