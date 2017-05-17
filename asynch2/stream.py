# -*- coding: utf-8 -*-
import asyncio
import logging
from collections import deque


logger = logging.getLogger(__name__)


class HTTP2Stream:

    def __init__(self, stream_id, *, loop=None):
        loop = loop or asyncio.get_event_loop()

        self._id = stream_id

        self._data_frames = deque()
        self._data_frames_available = asyncio.Event(loop=loop)

        self._closed = False

        self._response = asyncio.Future(loop=loop)
        self._trailers = asyncio.Future(loop=loop)

    @property
    def stream_id(self):
        return self._id

    @property
    def closed(self):
        return self._closed

    # Input methods called by HTTP2Protocol

    def receive_response(self, headers):
        logger.info(f'[{self.stream_id}] received headers: {headers}')
        self._response.set_result(headers)

    def receive_data(self, data):
        logger.info(f'[{self.stream_id}] received data: {data}')
        if data:
            self._data_frames.append(data)
            self._data_frames_available.set()

    def receive_trailers(self, headers):
        self._trailers.set_result(headers)

    def end(self):
        logger.info(f'[{self.stream_id}] ended')
        self._closed = True
        self._data_frames_available.set()
        if not self._trailers.done():
            self._trailers.set_result([])

    # Getters

    async def read_frame(self):
        frame = b''
        if len(self._data_frames) == 0 and not self.closed:
            await self._data_frames_available.wait()
        if len(self._data_frames) > 0:
            frame = self._data_frames.popleft()
        if len(self._data_frames) == 0 and not self.closed:
            self._data_frames_available.clear()
        return frame

    async def read_frames(self):
        frames = []
        if len(self._data_frames) == 0 and not self.closed:
            await self._data_frames_available.wait()
        if len(self._data_frames) > 0:
            frames.extend(self._data_frames)
            self._data_frames.clear()
        if len(self._data_frames) == 0 and not self.closed:
            self._data_frames_available.clear()
        return frames

    async def read(self):
        frames = []
        while True:
            _frames = await self.read_frames()
            if _frames:
                frames.extend(_frames)
            elif self.closed:
                break
        return b''.join(frames)

    async def response(self):
        return await self._response

    async def trailers(self):
        return await self._trailers
