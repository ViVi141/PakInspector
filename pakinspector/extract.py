# -*- coding: utf-8 -*-
"""Decompress and write PAC1 file payloads."""

from __future__ import annotations

import os
import zlib
from typing import Callable

from pakinspector.pac1 import FileInfo, ParsedPak, read_file_blob


def _safe_output_file_path(output_root: str, rel_path: str) -> str:
    """
    Resolve a safe absolute path under output_root for a pak entry rel_path.

    Rejects absolute paths, UNC-style names, '..', Windows alternate stream
    markers (':'), and any resolved path that would escape output_root.
    """
    if not output_root:
        raise ValueError("output_root must be non-empty")
    if not rel_path:
        raise ValueError("entry path must be non-empty")

    root_abs = os.path.realpath(os.path.abspath(output_root))
    norm = rel_path.replace("/", os.sep)
    if os.path.isabs(norm):
        raise ValueError("absolute entry path not allowed: %r" % rel_path)
    norm = norm.lstrip(os.sep)
    if norm.startswith("\\\\"):
        raise ValueError("UNC or device-style entry path not allowed: %r" % rel_path)

    if os.name == "nt":
        if ":" in norm:
            raise ValueError("':' not allowed in entry path on Windows: %r" % rel_path)

    parts: list[str] = []
    for part in norm.split(os.sep):
        if part == "":
            continue
        if part == "..":
            raise ValueError("path traversal ('..') in entry path: %r" % rel_path)
        if part == ".":
            continue
        parts.append(part)
    if not parts:
        raise ValueError("empty relative path after normalization: %r" % rel_path)

    safe_rel = os.sep.join(parts)
    candidate = os.path.join(root_abs, safe_rel)
    full = os.path.realpath(candidate)
    try:
        common = os.path.commonpath([root_abs, full])
    except ValueError as exc:
        raise ValueError(
            "entry path resolves outside output directory: %r" % rel_path
        ) from exc
    if os.path.normcase(common) != os.path.normcase(root_abs):
        raise ValueError("entry path escapes output directory: %r" % rel_path)
    return full


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

    try:
        full = _safe_output_file_path(output_root, rel_path)
    except ValueError as exc:
        on_error("%s: %s" % (rel_path, exc))
        return False

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
