# -*- coding: utf-8 -*-
import asyncio
import logging
from collections import deque
from typing import List
from typing import Tuple


logger = logging.getLogger(__name__)


Headers = List[Tuple[bytes, bytes]]


class AsyncFrameIterator:
    """Async iterator for an HTTP2Stream's data frames.
    """

    def __init__(self, stream):
        self.stream = stream

    def __aiter__(self):
        return self

    async def __anext__(self):
        frame = await self.stream.read_frame()
        if self.stream.closed and not frame:
            raise StopAsyncIteration
        else:
            return frame


class HTTP2Stream:

    def __init__(self, stream_id, *, loop=None):
        loop = loop or asyncio.get_event_loop()

        self._id = stream_id

        self._window_condition = asyncio.Condition(loop=loop)

        self._data_frames = deque()
        self._data_frames_available = asyncio.Event(loop=loop)

        self._headers = asyncio.Future(loop=loop)
        self._trailers = asyncio.Future(loop=loop)

        self._closed = False

    @property
    def id(self) -> int:
        return self._id

    @property
    def window_condition(self) -> asyncio.Condition:
        return self._window_condition

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self):
        if self.closed:
            return
        self._closed = True
        self._data_frames_available.set()
        if not self._trailers.done():
            self._trailers.set_result([])

    # Input methods called by HTTP2Protocol

    def receive_headers(self, headers: Headers):
        logger.debug(f'[{self.id}] Received headers')
        self._headers.set_result(headers)

    def receive_data(self, data: bytes):
        logger.debug(f'[{self.id}] Received data')
        if data:
            self._data_frames.append(data)
            self._data_frames_available.set()

    def receive_trailers(self, headers: Headers):
        logger.debug(f'[{self.id}] Received trailers')
        self._trailers.set_result(headers)

    def receive_end(self):
        logger.debug(f'[{self.id}] Received end')
        self.close()

    # Readers

    async def read(self) -> bytes:
        """Reads all of the stream's data, until it is closed. If it's never
        closed, this never returns.
        """
        frames = []
        while True:
            _frames = await self.read_frames()
            if _frames:
                frames.extend(_frames)
            elif self.closed:
                break
        return b''.join(frames)

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

    async def read_frames(self) -> List[bytes]:
        """Read all available frames.

        Similar to `read_frame`, except that all available frames in the buffer
        are returned as a list.
        """
        frames = []
        if len(self._data_frames) == 0 and not self.closed:
            await self._data_frames_available.wait()
        if len(self._data_frames) > 0:
            frames.extend(self._data_frames)
            self._data_frames.clear()
        if len(self._data_frames) == 0 and not self.closed:
            self._data_frames_available.clear()
        return frames

    async def read_headers(self) -> Headers:
        return await self._headers

    async def read_trailers(self) -> Headers:
        return await self._trailers

    # Streaming

    def stream_frames(self):
        """Returns an asynchronous iterator over the incoming data frames
        suitable for use with an "async for" loop.
        """
        return AsyncFrameIterator(self)
