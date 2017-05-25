# -*- coding: utf-8 -*-
from secrets import token_bytes
from zlib import crc32

import pytest

from ftl import create_connection


@pytest.mark.asyncio
async def test_put_multiple_frames():
    # 4 MB of junk to exceed single frame maximum
    junk = token_bytes(4 * 1024 * 1024)
    cksum = crc32(junk)

    http2 = await create_connection('http2.golang.org', 443)
    stream_id = await http2.send_request('PUT', '/crc32')
    await http2.send_data(stream_id, junk, end_stream=True)

    response = await http2.read_response(stream_id)
    assert response.status == 200
    assert response.headers['content-type'] == 'text/plain'

    data = await http2.read_data(stream_id)
    assert data.decode() == f'bytes={len(junk)}, CRC32={cksum:08x}'

    trailers = await response.trailers()
    assert trailers == {}
