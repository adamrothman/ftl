# -*- coding: utf-8 -*-


class ConnectionClosedError(Exception):
    pass


class StreamConsumedError(Exception):

    def __init__(self, stream_id):
        self.stream_id = stream_id
