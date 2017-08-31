# -*- coding: utf-8 -*-
import asyncio
import logging
from collections import defaultdict
from typing import AsyncIterator
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

import h2.events
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.exceptions import NoSuchStreamError
from h2.exceptions import StreamClosedError
from h2.settings import SettingCodes
from hpack import HeaderTuple
from hpack import NeverIndexedHeaderTuple

from ftl.errors import ConnectionClosedError
from ftl.errors import StreamConsumedError
from ftl.response import Response
from ftl.stream import HTTP2Stream
from ftl.utils import ConditionalEvent


logger = logging.getLogger(__name__)


class AsyncFrameIterator(AsyncIterator[bytes]):
    """Async iterator for an HTTP2Stream's data frames.
    """

    def __init__(self, connection, stream_id):
        self.connection = connection
        self.stream = connection._get_stream(stream_id)

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.connection.read_frame(self.stream.id)
        except StreamConsumedError:
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
        self._peername = transport.get_extra_info('peername')
        logger.debug(f'Connection to {self._peername} established')
        self._transport = transport
        self._h2 = H2Connection(config=self._h2_config)
        self._h2.initiate_connection()
        self._flush()
        self.resume_writing()

    def connection_lost(self, exc):
        logger.debug(f'Connection to {self._peername} lost: {exc}')
        self.pause_writing()
        self._h2 = None

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
    def closed(self):
        return self._transport.is_closing()

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

    def _create_stream(self, stream_id) -> HTTP2Stream:
        if stream_id not in self._streams:
            stream = HTTP2Stream(stream_id, loop=self._loop)
            if self._h2.local_flow_control_window(stream_id) > 0:
                stream.window_open.set()
            self._streams[stream_id] = stream
        return self._streams[stream_id]

    def _get_stream(self, stream_id) -> HTTP2Stream:
        if stream_id not in self._streams:
            raise NoSuchStreamError(stream_id)
        return self._streams[stream_id]

    # Event handlers

    def _alternative_service_available(
        self,
        event: h2.events.AlternativeServiceAvailable,
    ):
        pass

    def _connection_terminated(self, event: h2.events.ConnectionTerminated):
        try:
            code = ErrorCodes(event.error_code)
        except ValueError:
            code = event.error_code

        if code == ErrorCodes.NO_ERROR:
            logger.debug('Connection terminated gracefully by remote peer')
        else:
            logger.warning(f'Connection terminated by remote peer: {code!r}')

        self._transport.close()

    def _data_received(self, event: h2.events.DataReceived):
        stream = self._get_stream(event.stream_id)
        fc_size = event.flow_controlled_length

        message = f'[{event.stream_id}] Received data ({fc_size} bytes; '
        if event.stream_ended is None:
            window = self._h2.remote_flow_control_window(event.stream_id)
            message += f'{window} remaining in window)'
        else:
            message += 'stream ended)'
        logger.debug(message)

        stream.receive_data(event.data, fc_size)

    def _informational_response_received(
        self,
        event: h2.events.InformationalResponseReceived,
    ):
        pass

    def _ping_acknowledged(self, event: h2.events.PingAcknowledged):
        pass

    def _priority_updated(self, event: h2.events.PriorityUpdated):
        pass

    def _pushed_stream_received(self, event: h2.events.PushedStreamReceived):
        pass

    def _request_received(self, event: h2.events.RequestReceived):
        pass

    def _response_received(self, event: h2.events.ResponseReceived):
        logger.debug(f'[{event.stream_id}] Received response')
        stream = self._create_stream(event.stream_id)
        stream.receive_response(event.headers)

    def _remote_settings_changed(self, event: h2.events.RemoteSettingsChanged):
        if SettingCodes.INITIAL_WINDOW_SIZE in event.changed_settings:
            for stream in self._streams.values():
                stream.window_open.set()
        if SettingCodes.MAX_CONCURRENT_STREAMS in event.changed_settings:
            self._stream_creatable.update()

    def _settings_acknowledged(self, event: h2.events.SettingsAcknowledged):
        pass

    def _stream_ended(self, event: h2.events.StreamEnded):
        logger.debug(f'[{event.stream_id}] Ended')
        stream = self._get_stream(event.stream_id)
        stream.close()
        self._stream_creatable.update()

    def _stream_reset(self, event: h2.events.StreamReset):
        stream = self._get_stream(event.stream_id)
        stream.close()
        self._stream_creatable.update()

    def _trailers_received(self, event: h2.events.TrailersReceived):
        stream = self._get_stream(event.stream_id)
        stream.receive_trailers(event.headers)

    def _unknown_frame_received(self, event: h2.events.UnknownFrameReceived):
        pass

    def _window_updated(self, event: h2.events.WindowUpdated):
        if event.stream_id == 0:
            for stream in self._streams.values():
                stream.window_open.set()
        else:
            stream = self._get_stream(event.stream_id)
            stream.window_open.set()

    # Flow control

    def _acknowledge_data(self, size: int, stream_id: int):
        self._h2.acknowledge_received_data(size, stream_id)
        self._flush()

    async def _window_open(self, stream_id: int):
        """Wait until the identified stream's flow control window is open.
        """
        stream = self._get_stream(stream_id)
        return await stream.window_open.wait()

    # Send

    async def send_data(
        self,
        stream_id: int,
        data: bytes,
        end_stream: bool = False,
    ):
        """Send data, respecting the receiver's flow control instructions. If
        the provided data is larger than the connection's maximum outbound
        frame size, it will be broken into several frames as appropriate.
        """
        if self.closed:
            raise ConnectionClosedError
        stream = self._get_stream(stream_id)
        if stream.closed:
            raise StreamClosedError(stream_id)

        remaining = data
        while len(remaining) > 0:
            await asyncio.gather(
                self._writable.wait(),
                self._window_open(stream.id),
            )

            remaining_size = len(remaining)
            window_size = self._h2.local_flow_control_window(stream.id)
            max_frame_size = self._h2.max_outbound_frame_size

            send_size = min(remaining_size, window_size, max_frame_size)
            if send_size == 0:
                continue

            logger.debug(
                f'[{stream.id}] Sending {send_size} of {remaining_size} '
                f'bytes (window {window_size}, frame max {max_frame_size})'
            )

            to_send = remaining[:send_size]
            remaining = remaining[send_size:]
            end = (end_stream is True and len(remaining) == 0)

            self._h2.send_data(stream.id, to_send, end_stream=end)
            self._flush()

            if self._h2.local_flow_control_window(stream.id) == 0:
                stream.window_open.clear()

    # Receive

    async def read_data(self, stream_id: int) -> bytes:
        """Read data from the specified stream until it is closed by the remote
        peer. If the stream is never ended, this never returns.
        """
        frames = [f async for f in self.stream_frames(stream_id)]
        return b''.join(frames)

    async def read_frame(self, stream_id: int) -> bytes:
        """Read a single frame of data from the specified stream, waiting until
        frames are available if none are present in the local buffer. If the
        stream is closed and all buffered frames have been consumed, raises a
        StreamConsumedError.
        """
        stream = self._get_stream(stream_id)
        frame = await stream.read_frame()
        if frame.flow_controlled_length > 0:
            self._acknowledge_data(frame.flow_controlled_length, stream_id)
        return frame.data

    def read_frame_nowait(self, stream_id: int) -> Optional[bytes]:
        stream = self._get_stream(stream_id)
        frame = stream.read_frame_nowait()
        if frame is None:
            return None
        elif frame.flow_controlled_length > 0:
            self._acknowledge_data(frame.flow_controlled_length, stream_id)
        return frame.data

    async def read_response(self, stream_id: int) -> Response:
        stream = self._get_stream(stream_id)
        return await stream.response()

    def stream_frames(self, stream_id: int) -> AsyncFrameIterator:
        """Returns an asynchronous iterator over the incoming data frames
        suitable for use with an "async for" loop.
        """
        return AsyncFrameIterator(self, stream_id)

    # Connection management

    async def close(self):
        await self._writable.wait()
        self._h2.close_connection()
        self._flush()
        self._transport.close()

    # Stream management

    async def end_stream(self, stream_id: int):
        await self._writable.wait()
        self._h2.end_stream(stream_id)
        self._flush()

    async def reset_stream(
        self,
        stream_id: int,
        error_code: ErrorCodes = ErrorCodes.NO_ERROR,
    ):
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

        self._pushed_stream_ids: Dict[int, List[int]] = defaultdict(list)

    # Properties

    @property
    def host(self):
        return self._host

    # Event handlers

    def _pushed_stream_received(self, event: h2.events.PushedStreamReceived):
        parent_id = event.parent_stream_id
        pushed_id = event.pushed_stream_id
        logger.debug(
            f'[{parent_id}] Received pushed stream {pushed_id}: {event.headers}'
        )

        pushed = self._create_stream(pushed_id)
        pushed.receive_promise(event.headers)

        parent = self._get_stream(parent_id)
        self._pushed_stream_ids[parent.id].append(pushed.id)
        parent.pushed_streams_available.set()

    # Send

    async def send_request(
        self,
        method: str,
        path: str,
        additional_headers: Optional[Iterable] = None,
        end_stream: bool = False,
    ) -> int:
        if self.closed:
            raise ConnectionClosedError

        scheme = 'https' if self.secure else 'http'
        headers = [
            HeaderTuple(':method', method),
            HeaderTuple(':scheme', scheme),
            HeaderTuple(':authority', self.host),
            NeverIndexedHeaderTuple(':path', path),
        ]
        if additional_headers is not None:
            headers.extend(additional_headers)

        await asyncio.gather(
            self._writable.wait(),
            self._stream_creatable.wait(),
        )

        stream_id = self._h2.get_next_available_stream_id()
        logger.debug(
            f'[{stream_id}] Sending request: {method} '
            f'{scheme}://{self.host}{path}'
        )
        self._h2.send_headers(stream_id, headers, end_stream=end_stream)
        self._flush()

        stream = self._create_stream(stream_id)
        return stream.id

    # Receive

    async def get_pushed_stream_ids(self, parent_stream_id: int) -> List[int]:
        """Return a list of all streams pushed by the remote peer that are
        children of the specified stream. If no streams have been pushed when
        this method is called, waits until at least one stream has been pushed.
        """
        if parent_stream_id not in self._streams:
            logger.error(
                f'Parent stream {parent_stream_id} unknown to this connection'
            )
            raise NoSuchStreamError(parent_stream_id)
        parent = self._get_stream(parent_stream_id)

        await parent.pushed_streams_available.wait()
        pushed_streams_ids = self._pushed_stream_ids[parent.id]

        stream_ids: List[int] = []
        if len(pushed_streams_ids) > 0:
            stream_ids.extend(pushed_streams_ids)
            pushed_streams_ids.clear()
            parent.pushed_streams_available.clear()

        return stream_ids
