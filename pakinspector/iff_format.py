# -*- coding: utf-8 -*-
"""Minimal IFF (FORM + chunks) listing for arbitrary files."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List


_FORM_MAGIC = b"FORM"


@dataclass
class IffChunk:
    """One IFF chunk: 4-byte type id (ASCII) and body length (big-endian)."""

    type_id: str
    length: int
    body: bytes


@dataclass
class IffFile:
    """Parsed IFF container."""

    form_type: str
    chunks: List[IffChunk]


def parse_iff_bytes(data: bytes) -> IffFile:
    """Parse IFF/FORM file bytes."""
    if len(data) < 12:
        raise ValueError("IFF file too small")
    if data[0:4] != _FORM_MAGIC:
        raise ValueError("Not an IFF FORM file")
    form_type = data[8:12].decode("ascii", errors="replace")
    pos = 12
    chunks: List[IffChunk] = []
    while pos + 8 <= len(data):
        type_ascii = data[pos : pos + 4].decode("ascii", errors="replace")
        pos += 4
        (length,) = struct.unpack_from(">I", data, pos)
        pos += 4
        body = data[pos : pos + length]
        pos += length
        chunks.append(IffChunk(type_id=type_ascii, length=length, body=body))
    return IffFile(form_type=form_type, chunks=chunks)
