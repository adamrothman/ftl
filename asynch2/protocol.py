# -*- coding: utf-8 -*-
import asyncio
import logging

import h2.config
import h2.connection
import h2.events

from asynch2.stream import HTTP2Stream


logger = logging.getLogger(__name__)


class HTTP2Protocol(asyncio.Protocol):

    def __init__(self, *, loop=None, client_side=False):
        self.loop = loop or asyncio.get_event_loop()

        self._conn = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=client_side),
        )
        self._streams = {}
        self._transport = None

    # Connection callbacks

    def connection_made(self, transport):
        self._transport = transport
        self._conn.initiate_connection()
        self.flush()

    def connection_lost(self, exc):
        self._conn = None
        self._transport = None

    # Streaming callbacks

    def data_received(self, data):
        events = self._conn.receive_data(data)
        for event in events:
            self.event_received(event)

    def eof_received(self):
        self._conn.close_connection()
        self.flush()

    # Flow control

    def pause_writing(self):
        ...

    def resume_writing(self):
        ...

    # Helpers

    def event_received(self, event):
        t = type(event)
        if t == h2.events.DataReceived:
            stream = self.get_stream(event.stream_id)
            stream.receive_data(event.data)
        elif t == h2.events.ResponseReceived:
            stream = self.get_stream(event.stream_id)
            stream.receive_response(event.headers)
        elif t == h2.events.SettingsAcknowledged:
            logger.info(f'settings acknowledged')
        elif t == h2.events.StreamEnded:
            stream = self.get_stream(event.stream_id)
            stream.end()
        elif t == h2.events.TrailersReceived:
            stream = self.get_stream(event.stream_id)
            stream.receive_trailers(event.headers)
        elif t == h2.events.WindowUpdated:
            logger.info(
                f'stream {event.stream_id} window updated: {event.delta}'
            )
            # TODO: Handle these events
        else:
            logger.info('event received')
            logger.info(event)

    def flush(self):
        data = self._conn.data_to_send()
        if data and self._transport:
            self._transport.write(data)

    def get_stream(self, stream_id):
        if stream_id not in self._streams:
            self._streams[stream_id] = HTTP2Stream(stream_id, loop=self.loop)
        return self._streams[stream_id]

    # Client to server

    def request(
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

        stream_id = self._conn.get_next_available_stream_id()
        self._conn.send_headers(stream_id, headers, end_stream=end_stream)
        self.flush()

        return stream_id

    def send_data(self, stream_id, data, end_stream=False):
        self._conn.send_data(stream_id, data, end_stream=end_stream)
        self.flush()

    # Stuff

    async def read_response(self, stream_id):
        stream = self.get_stream(stream_id)
        return await stream.response()

    async def read(self, stream_id):
        stream = self.get_stream(stream_id)
        return await stream.read()

    async def read_trailers(self, stream_id):
        stream = self.get_stream(stream_id)
        return await stream.trailers()

    def stream(self, stream_id):
        # TODO: Would be cool to provide an async iterator
        ...
