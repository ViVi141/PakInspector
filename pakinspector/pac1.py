# -*- coding: utf-8 -*-
"""PAC1 (Arma Reforger .pak) binary format parsing."""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Iterator, List, Optional, Union

_FORM_MAGIC = b"FORM"
_PAC1_MAGIC = b"PAC1"

# Little-endian chunk type tags (same as historical Kaitai enum int values).
CHUNK_HEAD = 0x44414548  # HEAD
CHUNK_DATA = 0x41544144  # DATA
CHUNK_FILE = 0x454C4946  # FILE

# Whole-file buffer used for parsing and payload slices (bytes or read-only mmap).
BackingBuffer = Union[bytes, mmap.mmap]


class PakFormatError(ValueError):
    """Raised when bytes are not a valid PAC1 container."""


@dataclass
class HeadChunk:
    """HEAD chunk payload (opaque header bytes)."""

    header: bytes


@dataclass
class DataChunkRef:
    """DATA chunk body location in the backing buffer (no copy of large payload)."""

    abs_start: int
    length: int


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
class RawChunkRef:
    """Unknown chunk type body location in the backing buffer."""

    type_id: int
    abs_start: int
    length: int


@dataclass
class Chunk:
    """Top-level PAC1 chunk wrapper."""

    type_id: int
    body: Union[HeadChunk, DataChunkRef, FileChunk, RawChunkRef]


@dataclass
class ParsedPak:
    """Fully parsed .pak with backing store for resolving file data offsets."""

    _backing: BackingBuffer
    chunks: List[Chunk]
    _file_handle: Optional[BinaryIO] = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)

    @property
    def backing(self) -> BackingBuffer:
        """Whole-file buffer (bytes or mmap)."""
        return self._backing

    @property
    def raw(self) -> BackingBuffer:
        """Alias of ``backing`` for callers that slice ``pak.raw``."""
        return self._backing

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

    def close(self) -> None:
        """Release mmap and file handle when this pak was loaded from disk."""
        if self._closed:
            return
        self._closed = True
        buf = self._backing
        if isinstance(buf, mmap.mmap):
            buf.close()
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None


def _read_u1(data: BackingBuffer, pos: int) -> tuple[int, int]:
    return data[pos], pos + 1


def _read_u4le(data: BackingBuffer, pos: int) -> tuple[int, int]:
    (value,) = struct.unpack_from("<I", data, pos)
    return int(value), pos + 4


def _read_u4be(data: BackingBuffer, pos: int) -> tuple[int, int]:
    (value,) = struct.unpack_from(">I", data, pos)
    return int(value), pos + 4


def _read_utf8_name(data: BackingBuffer, pos: int, length: int) -> tuple[str, int]:
    raw = data[pos : pos + length]
    return raw.decode("utf-8"), pos + length


def _parse_pak_entry(data: BackingBuffer, pos: int) -> tuple[PakEntry, int]:
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


def _parse_chunks_from_buffer(data: BackingBuffer) -> List[Chunk]:
    """Parse PAC1 chunks from a bytes or mmap whole-file buffer."""
    if len(data) < 12:
        raise PakFormatError("File too small")
    if data[0:4] != _FORM_MAGIC:
        raise PakFormatError("Expected FORM magic")
    if data[8:12] != _PAC1_MAGIC:
        raise PakFormatError("Expected PAC1 form type")
    pos = 12
    chunks: List[Chunk] = []
    data_len = len(data)
    while pos < data_len:
        if pos + 8 > data_len:
            raise PakFormatError("Truncated chunk header")
        type_id, pos = _read_u4le(data, pos)
        length, pos = _read_u4be(data, pos)
        if pos + length > data_len:
            raise PakFormatError("Truncated chunk body")
        body_start = pos
        pos += length

        if type_id == CHUNK_HEAD:
            body_slice = data[body_start : body_start + length]
            parsed_body: Union[HeadChunk, DataChunkRef, FileChunk, RawChunkRef] = (
                HeadChunk(header=bytes(body_slice))
            )
        elif type_id == CHUNK_DATA:
            parsed_body = DataChunkRef(abs_start=body_start, length=length)
        elif type_id == CHUNK_FILE:
            body_slice = data[body_start : body_start + length]
            parsed_body = _parse_file_chunk_body(bytes(body_slice))
        else:
            parsed_body = RawChunkRef(
                type_id=type_id, abs_start=body_start, length=length
            )
        chunks.append(Chunk(type_id=type_id, body=parsed_body))
    return chunks


def parse_pak_bytes(data: bytes) -> ParsedPak:
    """Parse a PAC1 blob from memory (bytes backing; DATA/raw chunks are not copied)."""
    chunks = _parse_chunks_from_buffer(data)
    return ParsedPak(_backing=data, chunks=chunks, _file_handle=None)


def parse_pak_file(path: str) -> ParsedPak:
    """Memory-map a .pak path read-only, parse, and return a ParsedPak that owns the map."""
    handle = open(path, "rb")
    try:
        mm = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
    except OSError:
        handle.close()
        raise
    try:
        chunks = _parse_chunks_from_buffer(mm)
    except Exception:
        mm.close()
        handle.close()
        raise
    return ParsedPak(_backing=mm, chunks=chunks, _file_handle=handle)


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
    buf = pak.raw
    if end > len(buf):
        raise PakFormatError("File data out of range")
    return bytes(buf[finfo.offset : end])
