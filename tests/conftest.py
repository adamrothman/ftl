# -*- coding: utf-8 -*-
import asyncio

import pytest


@pytest.fixture()
def event_loop(request):
    """This default fixture from pytest-asyncio is overridden here because the
    testing event loop is shared. Without this explicit override – which leaves
    the loop open – it gets closed early and subsequent tests fail.
    """
    loop = asyncio.get_event_loop()
    yield loop
