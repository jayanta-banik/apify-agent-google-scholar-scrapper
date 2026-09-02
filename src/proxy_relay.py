"""A local HTTP proxy relay that injects Proxy-Authorization for upstream proxies.

Chrome's `--proxy-server` flag silently drops any credentials embedded in the URL,
so an authenticated upstream (like Apify Proxy) cannot be handed to Chrome directly.
This relay listens on localhost, accepts Chrome's CONNECT / absolute-URI requests,
adds the upstream credentials to the request head, and then pipes bytes both ways.
"""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import unquote, urlsplit

_HEAD_TERMINATOR = b'\r\n\r\n'
_MAX_HEAD_BYTES = 64 * 1024


class ProxyAuthRelay:
    """Forwards Chrome's proxy traffic to an authenticated upstream proxy."""

    def __init__(self, upstream_url: str) -> None:
        parts = urlsplit(upstream_url)
        if not parts.hostname:
            raise ValueError(f'Cannot parse upstream proxy URL: {upstream_url}')
        self._upstream_host = parts.hostname
        self._upstream_port = parts.port or 8000
        username = unquote(parts.username or '')
        password = unquote(parts.password or '')
        self._auth_header = None
        if username or password:
            token = base64.b64encode(f'{username}:{password}'.encode()).decode()
            self._auth_header = f'Proxy-Authorization: Basic {token}'.encode()
        self._server: asyncio.AbstractServer | None = None
        self.port: int | None = None

    @property
    def chrome_proxy_server(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    async def start(self) -> ProxyAuthRelay:
        self._server = await asyncio.start_server(self._handle_client, '127.0.0.1', 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def __aenter__(self) -> ProxyAuthRelay:
        return await self.start()

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.stop()

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            head = await self._read_head(client_reader)
            if head is None:
                return
            upstream_reader, upstream_writer = await asyncio.open_connection(self._upstream_host, self._upstream_port)
            upstream_writer.write(self._with_auth(head))
            await upstream_writer.drain()
            await asyncio.gather(
                self._pipe(client_reader, upstream_writer),
                self._pipe(upstream_reader, client_writer),
            )
        except (OSError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            for writer in (upstream_writer, client_writer):
                if writer is not None:
                    _close_quietly(writer)

    async def _read_head(self, reader: asyncio.StreamReader) -> bytes | None:
        try:
            head = await reader.readuntil(_HEAD_TERMINATOR)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            return None
        return head if len(head) <= _MAX_HEAD_BYTES else None

    def _with_auth(self, head: bytes) -> bytes:
        if self._auth_header is None:
            return head
        lines = [line for line in head.split(b'\r\n') if not line.lower().startswith(b'proxy-authorization:')]
        # The head ends with two empty entries from the trailing \r\n\r\n; insert before them.
        insert_at = max(len(lines) - 2, 1)
        lines.insert(insert_at, self._auth_header)
        return b'\r\n'.join(lines)

    @staticmethod
    async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            _close_quietly(writer)


def _close_quietly(writer: asyncio.StreamWriter) -> None:
    try:
        if not writer.is_closing():
            writer.close()
    except (OSError, ConnectionError):
        pass
