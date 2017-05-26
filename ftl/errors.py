# -*- coding: utf-8 -*-


class StreamConsumedError(Exception):

    def __init__(self, stream_id):
        self.stream_id = stream_id


class UnknownStreamError(Exception):

    def __init__(self, stream_id):
        self.stream_id = stream_id
