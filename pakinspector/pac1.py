# -*- coding: utf-8 -*-
"""PAC1 (Arma Reforger .pak) binary format parsing."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Union


_FORM_MAGIC = b"FORM"
_PAC1_MAGIC = b"PAC1"

# Little-endian chunk type tags (same as historical Kaitai enum int values).
CHUNK_HEAD = 0x44414548  # HEAD
CHUNK_DATA = 0x41544144  # DATA
CHUNK_FILE = 0x454C4946  # FILE


class PakFormatError(ValueError):
    """Raised when bytes are not a valid PAC1 container."""


@dataclass
class HeadChunk:
    """HEAD chunk payload (opaque header bytes)."""

    header: bytes


@dataclass
class DataChunk:
    """DATA chunk payload."""

    content: bytes


@dataclass
class FolderInfo:
    """Directory node inside FILE chunk tree."""

    children: List["PakEntry"] = field(default_factory=list)


@dataclass
class FileInfo:
    """File metadata and slice into the outer .pak blob."""

    offset: int
    compressed_length: int
    original_length: int
    unknown1: bytes
    compression_type: int
    unknown2: bytes


@dataclass
class PakEntry:
    """Recursive entry: folder (type 0) or file (type 1)."""

    name: str
    info: Union[FolderInfo, FileInfo]


@dataclass
class FileChunk:
    """FILE chunk: single root entry (usually a folder)."""

    root: PakEntry


@dataclass
class RawChunk:
    """Unknown chunk type."""

    type_id: int
    body: bytes


@dataclass
class Chunk:
    """Top-level PAC1 chunk wrapper."""

    type_id: int
    body: Union[HeadChunk, DataChunk, FileChunk, RawChunk]


@dataclass
class ParsedPak:
    """Fully parsed .pak with raw bytes kept for resolving file data offsets."""

    raw: bytes
    chunks: List[Chunk]

    def head_chunk(self) -> Optional[HeadChunk]:
        for c in self.chunks:
            if c.type_id == CHUNK_HEAD and isinstance(c.body, HeadChunk):
                return c.body
        return None

    def file_chunk(self) -> Optional[FileChunk]:
        for c in self.chunks:
            if c.type_id == CHUNK_FILE and isinstance(c.body, FileChunk):
                return c.body
        return None


def _read_u1(data: bytes, pos: int) -> tuple[int, int]:
    return data[pos], pos + 1


def _read_u4le(data: bytes, pos: int) -> tuple[int, int]:
    (value,) = struct.unpack_from("<I", data, pos)
    return int(value), pos + 4


def _read_u4be(data: bytes, pos: int) -> tuple[int, int]:
    (value,) = struct.unpack_from(">I", data, pos)
    return int(value), pos + 4


def _read_utf8_name(data: bytes, pos: int, length: int) -> tuple[str, int]:
    raw = data[pos : pos + length]
    return raw.decode("utf-8"), pos + length


def _parse_pak_entry(data: bytes, pos: int) -> tuple[PakEntry, int]:
    entry_type, pos = _read_u1(data, pos)
    name_len, pos = _read_u1(data, pos)
    name, pos = _read_utf8_name(data, pos, name_len)
    if entry_type == 0:
        child_count, pos = _read_u4le(data, pos)
        children: List[PakEntry] = []
        for _ in range(child_count):
            child, pos = _parse_pak_entry(data, pos)
            children.append(child)
        return PakEntry(name=name, info=FolderInfo(children=children)), pos
    if entry_type == 1:
        offset, pos = _read_u4le(data, pos)
        comp_len, pos = _read_u4le(data, pos)
        orig_len, pos = _read_u4le(data, pos)
        unk1 = data[pos : pos + 4]
        pos += 4
        comp_type, pos = _read_u4be(data, pos)
        unk2 = data[pos : pos + 4]
        pos += 4
        finfo = FileInfo(
            offset=offset,
            compressed_length=comp_len,
            original_length=orig_len,
            unknown1=unk1,
            compression_type=comp_type,
            unknown2=unk2,
        )
        return PakEntry(name=name, info=finfo), pos
    raise PakFormatError("Unknown pak entry type: %d" % entry_type)


def _parse_file_chunk_body(body: bytes) -> FileChunk:
    root, _ = _parse_pak_entry(body, 0)
    return FileChunk(root=root)


def parse_pak_bytes(data: bytes) -> ParsedPak:
    """Parse a PAC1 blob from memory."""
    if len(data) < 12:
        raise PakFormatError("File too small")
    if data[0:4] != _FORM_MAGIC:
        raise PakFormatError("Expected FORM magic")
    if data[8:12] != _PAC1_MAGIC:
        raise PakFormatError("Expected PAC1 form type")
    pos = 12
    chunks: List[Chunk] = []
    while pos < len(data):
        if pos + 8 > len(data):
            raise PakFormatError("Truncated chunk header")
        type_id, pos = _read_u4le(data, pos)
        length, pos = _read_u4be(data, pos)
        if pos + length > len(data):
            raise PakFormatError("Truncated chunk body")
        body = data[pos : pos + length]
        pos += length

        if type_id == CHUNK_HEAD:
            parsed_body: Union[HeadChunk, DataChunk, FileChunk, RawChunk] = HeadChunk(
                header=body
            )
        elif type_id == CHUNK_DATA:
            parsed_body = DataChunk(content=body)
        elif type_id == CHUNK_FILE:
            parsed_body = _parse_file_chunk_body(body)
        else:
            parsed_body = RawChunk(type_id=type_id, body=body)
        chunks.append(Chunk(type_id=type_id, body=parsed_body))
    return ParsedPak(raw=data, chunks=chunks)


def parse_pak_file(path: str) -> ParsedPak:
    """Read a .pak path as binary and parse."""
    with open(path, "rb") as handle:
        data = handle.read()
    return parse_pak_bytes(data)


def iter_file_entries(entry: PakEntry, base_path: str) -> Iterator[tuple[str, FileInfo]]:
    """Yield (relative_path, FileInfo) for all files under entry."""
    if base_path:
        current = base_path + "\\" + entry.name
    else:
        current = entry.name
    if isinstance(entry.info, FileInfo):
        yield (current, entry.info)
        return
    if isinstance(entry.info, FolderInfo):
        for child in entry.info.children:
            for item in iter_file_entries(child, current):
                yield item
        return


def read_file_blob(pak: ParsedPak, finfo: FileInfo) -> bytes:
    """Read compressed (or stored) bytes from the outer .pak using absolute offset."""
    end = finfo.offset + finfo.compressed_length
    if end > len(pak.raw):
        raise PakFormatError("File data out of range")
    return pak.raw[finfo.offset : end]
