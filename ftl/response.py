# -*- coding: utf-8 -*-
import asyncio
from typing import List
from typing import Tuple

from multidict import MultiDict
from multidict import MultiDictProxy


class Response:

    def __init__(
        self,
        stream_id: int,
        headers: List[Tuple[str, str]],
        trailers: asyncio.Future,
        *,
        loop=None,
    ) -> None:
        self._stream_id = stream_id
        self._headers = MultiDictProxy(MultiDict(headers))
        self._trailers = trailers
        self._status = int(self._headers[':status'])

    @property
    def stream_id(self) -> int:
        return self._stream_id

    @property
    def status(self) -> int:
        return self._status

    @property
    def headers(self) -> MultiDictProxy:
        return self._headers

    async def trailers(self) -> MultiDictProxy:
        return await self._trailers
