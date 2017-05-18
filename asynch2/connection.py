# -*- coding: utf-8 -*-
import asyncio
import logging

import h2.events
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.settings import SettingCodes

from asynch2.stream import HTTP2Stream
from asynch2.utils import ConditionalEvent


logger = logging.getLogger(__name__)


class AsyncFrameIterator:
    """Async iterator for an HTTP2Stream's data frames.
    """

    def __init__(self, connection, stream_id):
        self.connection = connection
        self.stream = connection._get_stream(stream_id)

    def __aiter__(self):
        return self

    async def __anext__(self):
        frame = await self.connection.read_frame(self.stream.id)
        if not frame and self.stream.closed:
            raise StopAsyncIteration
        else:
            return frame


class HTTP2Connection(asyncio.Protocol):

    def __init__(self, *, loop=None, client_side=True):
        loop = loop or asyncio.get_event_loop()
        self.loop = loop

        self._h2 = H2Connection(
            config=H2Configuration(
                client_side=client_side,
                header_encoding='utf-8',
            ),
        )
        self._streams = {}

        self._transport = None
        self._paused = True

        self._writable = ConditionalEvent(
            lambda: not self._paused,
            loop=loop,
        )
        self._stream_creatable = ConditionalEvent(
            self._can_create_stream,
            loop=loop,
        )

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
        self._flush()

    def eof_received(self):
        self.close()

    def pause_writing(self):
        self._paused = True
        self._writable.update()

    def resume_writing(self):
        self._paused = False
        self._writable.update()

    # Internal helpers

    def _can_create_stream(self):
        current = self._h2.open_outbound_streams
        limit = self._h2.remote_settings.max_concurrent_streams
        return current < limit

    def _event_received(self, event):
        t = type(event)
        if t == h2.events.ConnectionTerminated:
            logger.warning(
                f'Connection terminated by remote peer: {event.error_code}'
            )
            self._transport.close()
        elif t == h2.events.DataReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_data(event.data, event.flow_controlled_length)
        elif t == h2.events.ResponseReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_headers(event.headers)
        elif t == h2.events.RemoteSettingsChanged:
            if SettingCodes.INITIAL_WINDOW_SIZE in event.changed_settings:
                for stream in self._streams.values():
                    stream.window_open.set()
            if SettingCodes.MAX_CONCURRENT_STREAMS in event.changed_settings:
                self._stream_creatable.update()
        elif t == h2.events.SettingsAcknowledged:
            logger.debug('Settings acknowledged')
        elif t == h2.events.StreamEnded:
            stream = self._get_stream(event.stream_id)
            stream.close()
            self._stream_creatable.update()
        elif t == h2.events.StreamReset:
            stream = self._get_stream(event.stream_id)
            stream.close()
            self._stream_creatable.update()
        elif t == h2.events.TrailersReceived:
            stream = self._get_stream(event.stream_id)
            stream.receive_trailers(event.headers)
        elif t == h2.events.WindowUpdated:
            if event.stream_id == 0:
                # Connection window updated
                for stream in self._streams.values():
                    stream.window_open.set()
            else:
                # Stream window updated
                stream = self._get_stream(event.stream_id)
                stream.window_open.set()
        else:
            logger.warning(f'Unhandled event received: {event}')

    def _flush(self):
        data = self._h2.data_to_send()
        if data and self._transport:
            self._transport.write(data)

    def _get_stream(self, stream_id):
        if stream_id not in self._streams:
            self._streams[stream_id] = HTTP2Stream(stream_id, loop=self.loop)
        return self._streams[stream_id]

    # Flow control

    async def _window_open(self, stream_id):
        """Wait until the identified stream's flow control window is open.
        """
        stream = self._get_stream(stream_id)
        return await stream.window_open.wait()

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

        await asyncio.gather(
            self._writable.wait(),
            self._stream_creatable.wait(),
        )
        stream_id = self._h2.get_next_available_stream_id()
        self._h2.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()

        return stream_id

    async def send_data(self, stream_id, data, end_stream=False):
        """Send data, respecting the receiver's flow control instructions. If
        the provided data is larger than the connection's maximum outbound
        frame size, it will be broken into several frames as appropriate.
        """
        remaining = data
        while len(remaining) > 0:
            await asyncio.gather(
                self._writable.wait(),
                self._window_open(stream_id),
            )

            remaining_size = len(remaining)
            window_size = self._h2.local_flow_control_window(stream_id)
            max_frame_size = self._h2.max_outbound_frame_size

            send_size = min(remaining_size, window_size, max_frame_size)
            if send_size == 0:
                continue

            logger.debug(
                f'[{stream_id}] Sending {send_size} of {remaining_size} bytes '
                f'(window {window_size}, max {max_frame_size})'
            )

            to_send = remaining[:send_size]
            remaining = remaining[send_size:]
            end = (end_stream is True and len(remaining) == 0)

            self._h2.send_data(stream_id, to_send, end_stream=end)
            self._flush()

            if self._h2.local_flow_control_window(stream_id) == 0:
                stream = self._get_stream(stream_id)
                stream.window_open.clear()

    # Receive

    async def read_data(self, stream_id: int) -> bytes:
        """Read data from the specified stream until it is closed by the remote
        peer. If the stream is never ended, this never returns.
        """
        frames = [f async for f in self.stream_frames(stream_id)]
        return b''.join(frames)

    async def read_frame(self, stream_id) -> bytes:
        """Read a single frame of data from the specified stream. If the stream
        is closed and no frames remain in its buffer, returns an empty bytes
        object.
        """
        stream = self._get_stream(stream_id)
        frame = await stream.read_frame()
        if frame.flow_controlled_length > 0:
            self._h2.acknowledge_received_data(
                frame.flow_controlled_length,
                stream_id,
            )
            self._flush()
        return frame.data

    async def read_headers(self, stream_id):
        stream = self._get_stream(stream_id)
        return await stream.read_headers()

    async def read_trailers(self, stream_id):
        stream = self._get_stream(stream_id)
        return await stream.read_trailers()

    def stream_frames(self, stream_id):
        """Returns an asynchronous iterator over the incoming data frames
        suitable for use with an "async for" loop.
        """
        return AsyncFrameIterator(self, stream_id)

    # Connection management

    async def close(self):
        await self._writable.wait()
        self._h2.close_connection()
        self._flush()

    # Stream management

    async def end_stream(self, stream_id):
        await self._writable.wait()
        self._h2.end_stream(stream_id)
        self._flush()

    async def reset_stream(self, stream_id, error_code=ErrorCodes.NO_ERROR):
        await self._writable.wait()
        self._h2.reset_stream(stream_id, error_code=error_code)
        self._flush()
        stream = self._get_stream(stream_id)
        stream.close()
