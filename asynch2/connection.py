# -*- coding: utf-8 -*-
import asyncio
import logging

import h2.events
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes

from asynch2.stream import HTTP2Stream
from asynch2.utils import chunks


logger = logging.getLogger(__name__)


class HTTP2Connection(asyncio.Protocol):

    def __init__(self, *, loop=None, client_side=False):
        loop = loop or asyncio.get_event_loop()
        self.loop = loop

        self._h2 = H2Connection(
            config=H2Configuration(client_side=client_side),
        )
        self._streams = {}
        self._transport = None
        self._writable = asyncio.Event(loop=loop)

    # asyncio.Protocol callbacks

    def connection_made(self, transport):
        self._transport = transport
        self._h2.initiate_connection()
        self._flush()
        self.resume_writing()

    def connection_lost(self, exc):
        self.pause_writing()
        self._h2 = None
        self._transport = None

    def data_received(self, data):
        events = self._h2.receive_data(data)
        for event in events:
            self._event_received(event)

    def eof_received(self):
        self.close()

    def pause_writing(self):
        self._writable.clear()

    def resume_writing(self):
        self._writable.set()

    # Internal helpers

    def _event_received(self, event):
        t = type(event)
        if t == h2.events.ConnectionTerminated:
            logger.warning(
                f'Connection terminated by remote peer: {event.error_code}'
            )
            self._transport.close()
        elif t == h2.events.DataReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_data(event.data)
        elif t == h2.events.ResponseReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_headers(event.headers)
        elif t == h2.events.RemoteSettingsChanged:
            logger.info('Remote settings changed')
        elif t == h2.events.SettingsAcknowledged:
            logger.debug('Settings acknowledged')
            # TODO: Handle new settings if necessary
        elif t == h2.events.StreamEnded:
            stream = self._get_stream(event.stream_id)
            stream.receive_end()
        elif t == h2.events.TrailersReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_trailers(event.headers)
        elif t == h2.events.WindowUpdated:
            logger.debug(
                f'Stream [{event.stream_id}] window update: {event.delta}'
            )
        else:
            logger.info(f'Unhandled event received: {event}')

    def _flush(self):
        data = self._h2.data_to_send()
        if data and self._transport:
            self._transport.write(data)

    def _get_stream(self, stream_id):
        if stream_id not in self._streams:
            self._streams[stream_id] = HTTP2Stream(stream_id, loop=self.loop)
        return self._streams[stream_id]

    # Flow control

    def _window_open(self, stream_id, size=None):
        if not isinstance(size, int) or size <= 0:
            size = 0
        return self._h2.local_flow_control_window(stream_id) > size

    async def window_open(self, stream_id, size=None):
        """Wait until the identified stream's flow control window is large
        enough to accomodate data of the given size (or open at all if no size
        is given).
        """
        stream = self._get_stream(stream_id)
        await stream.window_condition.wait_for(
            lambda: self._window_open(stream_id, size=size)
        )

    async def writable(self):
        await self._writable.wait()

    # Send

    async def send_request(
        self,
        method,
        scheme,
        authority,
        path,
        additional_headers=None,
        end_stream=False,
    ):
        headers = [
            (':method', method),
            (':scheme', scheme),
            (':authority', authority),
            (':path', path),
        ]
        if additional_headers is not None:
            headers.extend(additional_headers)

        await self.writable()
        stream_id = self._h2.get_next_available_stream_id()
        self._h2.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()

        return stream_id

    async def send_data(self, stream_id, data, end_stream=False):
        """Send data, respecting the receiver's flow control instructions. If
        the provided data is larger than the connection's maximum outbound
        frame size, it will be broken into several frames as appropriate.
        """
        max_frame_size = self._h2.max_outbound_frame_size

        if isinstance(max_frame_size, int) and len(data) > max_frame_size:
            frames = list(chunks(data, max_frame_size))
        else:
            frames = [data]
        frame_count = len(frames)

        for i, frame in enumerate(frames):
            await asyncio.gather(
                self.writable(),
                self.window_open(stream_id, size=len(frame)),
            )
            end = (end_stream is True and i == frame_count - 1)
            self._h2.send_data(stream_id, frame, end_stream=end)
            self._flush()

    # Receive

    async def read_headers(self, stream_id):
        stream = self._get_stream(stream_id)
        return await stream.read_headers()

    async def read_data(self, stream_id):
        stream = self._get_stream(stream_id)
        return await stream.read()

    async def read_trailers(self, stream_id):
        stream = self._get_stream(stream_id)
        return await stream.read_trailers()

    def stream_frames(self, stream_id):
        stream = self._get_stream(stream_id)
        return stream.stream_frames()

    # Connection management

    def close(self):
        self._h2.close_connection()
        self._flush()

    # Stream management

    async def end_stream(self, stream_id):
        await self.writable()
        self._h2.end_stream(stream_id)
        self._flush()

    async def reset_stream(self, stream_id, error_code=ErrorCodes.NO_ERROR):
        await self.writable()
        self._h2.reset_stream(stream_id, error_code=error_code)
        self._flush()
        stream = self._get_stream(stream_id)
        stream.close()
