# -*- coding: utf-8 -*-
import asyncio
import logging
from collections import defaultdict
from typing import Optional

import h2.events
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.settings import SettingCodes

from ftl.errors import StreamClosedError
from ftl.errors import UnknownStreamError
from ftl.stream import HTTP2Stream
from ftl.utils import ConditionalEvent


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
        try:
            return await self.connection.read_frame(self.stream.id)
        except StreamClosedError:
            raise StopAsyncIteration


class HTTP2Connection(asyncio.Protocol):

    def __init__(self, *, loop=None, secure=True):
        loop = loop or asyncio.get_event_loop()
        self._loop = loop
        self._secure = secure

        self._transport = None
        self._paused = True
        self._writable = ConditionalEvent(
            lambda: not self._paused,
            loop=loop,
        )

        self._h2_config = None
        self._h2 = None

        self._streams = {}
        self._stream_creatable = ConditionalEvent(
            self._can_create_stream,
            loop=loop,
        )

        self._dispatch = {
            h2.events.AlternativeServiceAvailable: self._alternative_service_available,
            h2.events.ConnectionTerminated: self._connection_terminated,
            h2.events.DataReceived: self._data_received,
            h2.events.InformationalResponseReceived: self._informational_response_received,
            h2.events.PingAcknowledged: self._ping_acknowledged,
            h2.events.PriorityUpdated: self._priority_updated,
            h2.events.PushedStreamReceived: self._pushed_stream_received,
            h2.events.RemoteSettingsChanged: self._remote_settings_changed,
            h2.events.RequestReceived: self._request_received,
            h2.events.ResponseReceived: self._response_received,
            h2.events.SettingsAcknowledged: self._settings_acknowledged,
            h2.events.StreamEnded: self._stream_ended,
            h2.events.StreamReset: self._stream_reset,
            h2.events.TrailersReceived: self._trailers_received,
            h2.events.UnknownFrameReceived: self._unknown_frame_received,
            h2.events.WindowUpdated: self._window_updated,
        }

    # asyncio.Protocol callbacks

    def connection_made(self, transport):
        self._transport = transport
        self._h2 = H2Connection(config=self._h2_config)
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
            handler = self._dispatch[type(event)]
            handler(event)
        self._flush()

    def eof_received(self):
        self.close()

    def pause_writing(self):
        self._paused = True
        self._writable.update()

    def resume_writing(self):
        self._paused = False
        self._writable.update()

    # Properties

    @property
    def secure(self):
        return self._secure

    # Internal helpers

    def _can_create_stream(self):
        current = self._h2.open_outbound_streams
        limit = self._h2.remote_settings.max_concurrent_streams
        return current < limit

    def _flush(self):
        data = self._h2.data_to_send()
        if data and self._transport:
            self._transport.write(data)

    def _get_stream(self, stream_id):
        if stream_id not in self._streams:
            self._streams[stream_id] = HTTP2Stream(stream_id, loop=self._loop)
        return self._streams[stream_id]

    # Event handlers

    def _alternative_service_available(self, event):
        pass

    def _connection_terminated(self, event):
        logger.warning(
            f'Connection terminated by remote peer: {event.error_code}'
        )
        self._transport.close()

    def _data_received(self, event):
        logger.debug(
            f'Stream {event.stream_id} received data (window '
            f'{self._h2.remote_flow_control_window(event.stream_id)})'
        )
        stream = self._get_stream(event.stream_id)
        stream.receive_data(event.data, event.flow_controlled_length)

    def _informational_response_received(self, event):
        pass

    def _ping_acknowledged(self, event):
        pass

    def _priority_updated(self, event):
        pass

    def _pushed_stream_received(self, event):
        pass

    def _request_received(self, event):
        pass

    def _response_received(self, event):
        stream = self._get_stream(event.stream_id)
        stream.receive_response(event.headers)

    def _remote_settings_changed(self, event):
        if SettingCodes.INITIAL_WINDOW_SIZE in event.changed_settings:
            for stream in self._streams.values():
                stream.window_open.set()
        if SettingCodes.MAX_CONCURRENT_STREAMS in event.changed_settings:
            self._stream_creatable.update()

    def _settings_acknowledged(self, event):
        pass

    def _stream_ended(self, event):
        logger.debug(f'Stream {event.stream_id} ended')
        stream = self._get_stream(event.stream_id)
        stream.close()
        self._stream_creatable.update()

    def _stream_reset(self, event):
        stream = self._get_stream(event.stream_id)
        stream.close()
        self._stream_creatable.update()

    def _trailers_received(self, event):
        stream = self._get_stream(event.stream_id)
        stream.receive_trailers(event.headers)

    def _unknown_frame_received(self, event):
        pass

    def _window_updated(self, event):
        if event.stream_id == 0:
            for stream in self._streams.values():
                stream.window_open.set()
        else:
            stream = self._get_stream(event.stream_id)
            stream.window_open.set()

    # Flow control

    def _acknowledge_data(self, size, stream_id):
        self._h2.acknowledge_received_data(size, stream_id)
        self._flush()

    async def _window_open(self, stream_id):
        """Wait until the identified stream's flow control window is open.
        """
        stream = self._get_stream(stream_id)
        return await stream.window_open.wait()

    # Send

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
                f'Stream {stream_id} sending {send_size} of {remaining_size} '
                f'bytes (window {window_size}, max {max_frame_size})'
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
        """Read a single frame of data from the specified stream, waiting until
        frames are available if none are present in the local buffer. If the
        stream is closed and all buffered frames have been consumed, raises a
        StreamClosedError.
        """
        stream = self._get_stream(stream_id)
        frame = await stream.read_frame()
        if frame.flow_controlled_length > 0:
            self._acknowledge_data(frame.flow_controlled_length, stream_id)
        return frame.data

    def read_frame_nowait(self, stream_id) -> Optional[bytes]:
        stream = self._get_stream(stream_id)
        frame = stream.read_frame_nowait()
        if frame is None:
            return frame
        elif frame.flow_controlled_length > 0:
            self._acknowledge_data(frame.flow_controlled_length, stream_id)
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


class HTTP2ClientConnection(HTTP2Connection):

    def __init__(self, host, *, loop=None, secure=True):
        super().__init__(loop=loop, secure=secure)
        self._host = host

        self._h2_config = H2Configuration(
            client_side=True,
            header_encoding='utf-8',
        )

        self._pushed_streams = defaultdict(list)

    # Properties

    @property
    def host(self):
        return self._host

    # Event handlers

    def _pushed_stream_received(self, event):
        logger.debug(
            f'Stream {event.parent_stream_id} received pushed stream '
            f'{event.pushed_stream_id}: {event.headers}'
        )

        stream = self._get_stream(event.pushed_stream_id)
        stream.receive_promise(event.headers)

        parent = self._get_stream(event.parent_stream_id)
        self._pushed_streams[parent.id].append(stream.id)
        parent.pushed_streams_available.set()

    # Send

    async def send_request(
        self,
        method,
        path,
        additional_headers=None,
        end_stream=False,
    ):
        headers = [
            (':method', method),
            (':scheme', 'https' if self.secure else 'http'),
            (':authority', self.host),
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

    # Receive

    async def get_pushed_stream_ids(self, parent_stream_id):
        """Return a list of all streams pushed by the remote peer that are
        children of the specified stream. If no streams have been pushed when
        this method is called, waits until at least one stream has been pushed.
        """
        if parent_stream_id not in self._streams:
            raise UnknownStreamError(
                f'Parent stream {parent_stream_id} unknown to this connection'
            )
        parent = self._get_stream(parent_stream_id)

        await parent.pushed_streams_available.wait()
        pushed_streams = self._pushed_streams[parent.id]

        stream_ids = []
        if len(pushed_streams) > 0:
            stream_ids.extend(pushed_streams)
            pushed_streams.clear()
            parent.pushed_streams_available.clear()

        return stream_ids
