"""HTTP client for upstream JSON APIs.

get_json   — one-shot fetch with timeout + exponential-backoff retry (memory mode).
stream_items — streaming fetch + incremental ijson parse (stream mode); NOT retried,
               since a partially-consumed stream cannot be safely replayed. A failed
               streaming job is simply re-run on its next cron cycle.
"""
import gzip
import json
import logging
import time
import zlib
from typing import Iterator, Optional

import httpx
import ijson

logger = logging.getLogger(__name__)

# gzip 魔数。上游(如 traffic/status、areaLinkPub)可能返回 gzip 压缩的 body 却不带
# Content-Encoding: gzip 响应头,httpx 因此不会自动解压,原始 gzip 字节直送 JSON 解析器
# 会报 UnicodeDecodeError(0x8b)/ijson lexical error。这里按魔数兜底手动解压。
_GZIP_MAGIC = b"\x1f\x8b"


def _maybe_gunzip(data: bytes) -> bytes:
    """body 以 gzip 魔数开头则解压,否则原样返回(httpx 已按 Content-Encoding 解压的情形)。"""
    if data[:2] == _GZIP_MAGIC:
        return gzip.decompress(data)
    return data


def _maybe_gunzip_stream(byte_iter):
    """流式版:首块以 gzip 魔数开头则增量解压整条流,否则透传。"""
    it = iter(byte_iter)
    try:
        first = next(it)
    except StopIteration:
        return
    if first[:2] != _GZIP_MAGIC:
        yield first
        yield from it
        return
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)  # 16 = 期望 gzip 头
    yield dec.decompress(first)
    for chunk in it:
        yield dec.decompress(chunk)
    tail = dec.flush()
    if tail:
        yield tail


class _BytesIterReader:
    """Adapt an iterator of bytes (httpx iter_bytes) into a read(size) file-like for ijson."""

    def __init__(self, byte_iter):
        self._it = byte_iter
        self._buf = b""

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks = [self._buf]
            self._buf = b""
            chunks.extend(self._it)
            return b"".join(chunks)
        while len(self._buf) < size:
            try:
                self._buf += next(self._it)
            except StopIteration:
                break
        out, self._buf = self._buf[:size], self._buf[size:]
        return out


class HttpClient:
    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
        headers: Optional[dict] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.max_retries = max(1, max_retries)
        self.backoff_seconds = backoff_seconds
        self._client = httpx.Client(timeout=timeout_seconds, headers=headers or {}, transport=transport)

    def get_json(self, url: str, params: Optional[dict] = None):
        """GET + raise_for_status + JSON parse, with retry. Integers stay arbitrary-precision int."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.get(url, params=params)
                resp.raise_for_status()
                # json.loads 保留任意精度整数(64 位 linkId 不丢精度),与 httpx .json() 一致。
                return json.loads(_maybe_gunzip(resp.content))
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
        assert last_exc is not None
        raise last_exc

    def stream_items(self, url: str, prefix: str, params: Optional[dict] = None) -> Iterator:
        """Stream GET and yield each JSON element at `prefix` (e.g. 'linkCoordList.item').

        Note: ijson yields Decimal for fractional numbers — callers/parsers must normalize.
        """
        with self._client.stream("GET", url, params=params) as resp:
            resp.raise_for_status()
            reader = _BytesIterReader(_maybe_gunzip_stream(resp.iter_bytes()))
            yield from ijson.items(reader, prefix)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
