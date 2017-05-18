# -*- coding: utf-8 -*-
import asyncio
import logging
from collections import deque
from typing import List
from typing import Tuple

from multidict import MultiDict


logger = logging.getLogger(__name__)


class HTTP2Stream:

    def __init__(self, stream_id, *, loop=None):
        loop = loop or asyncio.get_event_loop()

        self._id = stream_id

        self._window_open = asyncio.Event(loop=loop)

        self._data_frames = deque()
        self._data_frames_available = asyncio.Event(loop=loop)

        self._headers = asyncio.Future(loop=loop)
        self._trailers = asyncio.Future(loop=loop)

        self._closed = False

    @property
    def id(self) -> int:
        return self._id

    @property
    def window_open(self) -> asyncio.Event:
        return self._window_open

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self):
        if self.closed:
            return
        self._closed = True
        self._data_frames_available.set()
        if not self._trailers.done():
            self._trailers.set_result(MultiDict())

    # Input methods called by HTTP2Protocol

    def receive_headers(self, headers: List[Tuple[str, str]]):
        self._headers.set_result(MultiDict(headers))

    def receive_data(self, data: bytes):
        if data:
            self._data_frames.append(data)
            self._data_frames_available.set()

    def receive_trailers(self, trailers: List[Tuple[str, str]]):
        self._trailers.set_result(MultiDict(trailers))

    def receive_end(self):
        self.close()

    # Readers

    async def read_frame(self) -> bytes:
        """Read a single frame.

        If unconsumed frames remain in the stream's buffer, the first one is
        returned immediately. If the stream is open and no frames remain, waits
        for a new frame to arrive. If the stream is closed and no frames
        remain, returns an empty bytes object.
        """
        frame = b''
        if len(self._data_frames) == 0 and not self.closed:
            await self._data_frames_available.wait()
        if len(self._data_frames) > 0:
            frame = self._data_frames.popleft()
        if len(self._data_frames) == 0 and not self.closed:
            self._data_frames_available.clear()
        return frame

    async def read_headers(self) -> MultiDict:
        return await self._headers

    async def read_trailers(self) -> MultiDict:
        return await self._trailers
