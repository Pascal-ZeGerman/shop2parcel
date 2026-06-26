"""aiohttp ↔ aioresponses compatibility shim for the API-client tests.

aioresponses (<= 0.7.9, the latest release) constructs ``aiohttp.ClientResponse``
without the ``stream_writer`` keyword argument that aiohttp 3.14 made **required**
(added alongside the still-present ``writer``). Under aiohttp >= 3.14, every
``with aioresponses()`` block in this directory otherwise dies with::

    TypeError: ClientResponse.__init__() missing 1 required keyword-only argument: 'stream_writer'

aioresponses passes ``writer=None``, which takes ClientResponse's ``if writer is None:``
branch — and that branch reads ``stream_writer.output_size``, so a literal ``None`` would
raise ``AttributeError``. We therefore inject a tiny stub exposing ``output_size``.

The patch installs ONLY when the running aiohttp's signature actually has ``stream_writer``
(aiohttp >= 3.14), so it is a no-op on the aiohttp 3.13.x found in many dev environments.
It uses ``setdefault``, so genuine ClientResponse construction (HA's real HTTP stack, which
passes a real ``stream_writer``) is never affected — only callers that omit it, i.e.
aioresponses.

Remove this shim once aioresponses ships a release that supplies ``stream_writer`` itself.
"""

from __future__ import annotations

import inspect

import aiohttp

if "stream_writer" in inspect.signature(aiohttp.ClientResponse.__init__).parameters:

    class _AioresponsesStreamWriter:
        """Stand-in stream writer.

        ClientResponse only reads ``.output_size`` on the request-already-sent path that
        aioresponses triggers by passing ``writer=None``.
        """

        output_size = 0

    _orig_client_response_init = aiohttp.ClientResponse.__init__

    def _client_response_init_compat(self, *args, **kwargs):
        kwargs.setdefault("stream_writer", _AioresponsesStreamWriter())
        return _orig_client_response_init(self, *args, **kwargs)

    aiohttp.ClientResponse.__init__ = _client_response_init_compat
