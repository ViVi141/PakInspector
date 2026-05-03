# -*- coding: utf-8 -*-
"""Decompress and write PAC1 file payloads."""

from __future__ import annotations

import os
import zlib
from typing import Callable

from pakinspector.pac1 import FileInfo, ParsedPak, read_file_blob


def decompress_payload(finfo: FileInfo, blob: bytes) -> bytes:
    """Return uncompressed bytes for a stored file entry."""
    if finfo.compression_type == 0:
        return blob
    if finfo.compression_type == 0x106:
        # Match prior implementation: skip 2-byte zlib header then raw DEFLATE.
        if len(blob) < 2:
            raise ValueError("Compressed blob too short")
        return zlib.decompress(blob[2:], wbits=-15)
    raise ValueError(
        "Unknown compression type 0x%X for decompress" % finfo.compression_type
    )


def extract_entry_to_disk(
    pak: ParsedPak,
    rel_path: str,
    finfo: FileInfo,
    output_root: str,
    raw: bool,
    on_error: Callable[[str], None],
) -> bool:
    """Write one file under output_root preserving relative path. Returns success."""
    try:
        blob = read_file_blob(pak, finfo)
    except Exception as exc:
        on_error("%s: read failed: %s" % (rel_path, exc))
        return False

    if raw:
        payload = blob
    else:
        if finfo.compression_type == 0:
            payload = blob
        elif finfo.compression_type == 0x106:
            try:
                payload = decompress_payload(finfo, blob)
            except Exception as exc:
                on_error("%s: decompress failed: %s" % (rel_path, exc))
                return False
        else:
            on_error(
                "%s: unknown compression 0x%X"
                % (rel_path, finfo.compression_type)
            )
            return False

    norm = rel_path.replace("/", os.sep)
    full = os.path.join(output_root, norm)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(full, "wb") as handle:
            handle.write(payload)
    except OSError as exc:
        on_error("%s: write failed: %s" % (rel_path, exc))
        return False
    return True
