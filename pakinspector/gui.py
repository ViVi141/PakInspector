# -*- coding: utf-8 -*-
"""Tkinter GUI for browsing and extracting PAC1 .pak files."""

from __future__ import annotations

import base64
import json
import os
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from pakinspector.extract import extract_entry_to_disk
from pakinspector.i18n import I18n, default_language
from pakinspector.iff_format import parse_iff_bytes
from pakinspector.pac1 import (
    FileInfo,
    FolderInfo,
    PakEntry,
    PakFormatError,
    ParsedPak,
    iter_file_entries,
    parse_pak_file,
)

_MAX_EXPORT_WORKERS = min(32, (os.cpu_count() or 4))


def _bytes_to_hex(data: bytes) -> str:
    return data.hex().upper()


def _build_report(
    bundles: List[Tuple[str, ParsedPak]],
    files: List[Tuple[str, FileInfo, ParsedPak]],
) -> dict:
    """JSON report: per-source HEAD plus flat file list with sourcePak path."""
    sources = []
    for pak_disk, parsed in bundles:
        head_b64 = ""
        head = parsed.head_chunk()
        if head is not None:
            head_b64 = base64.b64encode(head.header).decode("ascii")
        sources.append({"pakPath": pak_disk, "head": head_b64})
    file_objs = []
    for rel_path, finfo, parsed in files:
        source_pak = ""
        for disk, p in bundles:
            if p is parsed:
                source_pak = disk
                break
        file_objs.append(
            {
                "path": rel_path.replace("/", "\\"),
                "sourcePak": source_pak,
                "offset": finfo.offset,
                "compressedLength": finfo.compressed_length,
                "originalLength": finfo.original_length,
                "unknown1": _bytes_to_hex(finfo.unknown1),
                "compressionType": finfo.compression_type,
                "unknown2": _bytes_to_hex(finfo.unknown2),
            }
        )
    head_single = ""
    if len(sources) == 1:
        head_single = sources[0].get("head", "")
    return {
        "head": head_single,
        "sources": sources,
        "filesCount": len(file_objs),
        "files": file_objs,
    }


def _insert_tree(
    tree: ttk.Treeview,
    parent: str,
    entry: PakEntry,
    base_path: str,
    file_by_iid: Dict[str, Tuple[str, FileInfo, ParsedPak]],
    parsed: ParsedPak,
) -> None:
    """Insert one pak root subtree (single-pak mode)."""
    if base_path:
        current = base_path + "\\" + entry.name
    else:
        current = entry.name
    node_id = current
    if isinstance(entry.info, FileInfo):
        display_path = current.replace("/", "\\")
        tree.insert(
            parent,
            tk.END,
            iid=node_id,
            text=entry.name,
            values=(display_path,),
            tags=("file",),
        )
        file_by_iid[node_id] = (display_path, entry.info, parsed)
        return
    if isinstance(entry.info, FolderInfo):
        tree.insert(
            parent,
            tk.END,
            iid=node_id,
            text=entry.name,
            values=("",),
            tags=("folder",),
        )
        for child in entry.info.children:
            _insert_tree(tree, node_id, child, current, file_by_iid, parsed)
        return


def _insert_merged_file(
    tree: ttk.Treeview,
    rel_path_win: str,
    finfo: FileInfo,
    parsed: ParsedPak,
    file_by_iid: Dict[str, Tuple[str, FileInfo, ParsedPak]],
) -> None:
    """Insert one file path into tree, creating folder nodes as needed."""
    parts = rel_path_win.split("\\")
    parent = ""
    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        node_id = "\\".join(parts[: i + 1])
        if is_last:
            tree.insert(
                parent,
                tk.END,
                iid=node_id,
                text=part,
                values=(rel_path_win,),
                tags=("file",),
            )
            file_by_iid[node_id] = (rel_path_win, finfo, parsed)
            return
        if not tree.exists(node_id):
            tree.insert(
                parent,
                tk.END,
                iid=node_id,
                text=part,
                values=("",),
                tags=("folder",),
            )
            parent = node_id


def _collect_merged_file_rows(
    bundles: List[Tuple[str, ParsedPak]],
) -> Tuple[List[Tuple[str, FileInfo, ParsedPak]], List[str]]:
    """
    Merge file rows from all bundles. Later bundle overwrites same path.

    Returns (flat_list_sorted, duplicate_paths_logged).
    """
    by_key: Dict[str, Tuple[str, FileInfo, ParsedPak]] = {}
    dups: List[str] = []
    for _pak_disk, parsed in bundles:
        fchunk = parsed.file_chunk()
        if fchunk is None:
            continue
        for rel, finfo in iter_file_entries(fchunk.root, ""):
            key = rel.replace("/", "\\")
            if key in by_key:
                dups.append(key)
            by_key[key] = (key, finfo, parsed)
    flat = [by_key[k] for k in sorted(by_key.keys())]
    return flat, dups


class PakInspectorApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self._i18n = I18n(default_language())
        self._export_active = False
        self.title(self._i18n.tr("window.title"))
        self.geometry("960x640")
        self.minsize(720, 480)

        self._bundle: List[Tuple[str, ParsedPak]] = []
        self._files_flat: List[Tuple[str, FileInfo, ParsedPak]] = []
        self._file_by_iid: Dict[str, Tuple[str, FileInfo, ParsedPak]] = {}

        self._last_folder: Optional[str] = None
        self._load_generation: int = 0

        self.protocol("WM_DELETE_WINDOW", self._on_delete_window)
        self._build_menubar()
        self._build_widgets()
        self._apply_ui_language()

    def _build_menubar(self) -> None:
        # tearoff=False so index 0 is the Language cascade (tear-off has no -label).
        menubar = tk.Menu(self, tearoff=False)
        self._lang_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label=self._i18n.tr("menu.language"), menu=self._lang_menu)
        self._lang_menu.add_command(
            label=self._i18n.tr("menu.lang_en"),
            command=lambda: self._set_language("en"),
        )
        self._lang_menu.add_command(
            label=self._i18n.tr("menu.lang_zh_cn"),
            command=lambda: self._set_language("zh_CN"),
        )
        self.config(menu=menubar)
        self._menubar = menubar

    def _set_language(self, code: str) -> None:
        self._i18n.set_language(code)
        self._apply_ui_language()

    def _apply_ui_language(self) -> None:
        self.title(self._i18n.tr("window.title"))
        if hasattr(self, "_notebook"):
            self._notebook.tab(self._pak_tab, text=self._i18n.tr("tab.pac1"))
            self._notebook.tab(self._iff_tab, text=self._i18n.tr("tab.iff"))
        if hasattr(self, "_btn_open_pak"):
            self._btn_open_pak.config(text=self._i18n.tr("btn.open_pak"))
            self._btn_open_folder.config(text=self._i18n.tr("btn.open_folder"))
            self._chk_recursive.config(text=self._i18n.tr("chk.recursive"))
            self._tree.heading("#0", text=self._i18n.tr("tree.col_name"))
            self._tree.heading("path", text=self._i18n.tr("tree.col_path"))
            self._chk_raw.config(text=self._i18n.tr("chk.raw_export"))
            self._btn_export_all.config(text=self._i18n.tr("btn.export_all"))
            self._btn_export_selected.config(text=self._i18n.tr("btn.export_selected"))
            self._btn_save_json.config(text=self._i18n.tr("btn.save_json"))
            self._detail_lab.config(text=self._i18n.tr("frame.detail"))
            self._head_lab.config(text=self._i18n.tr("frame.head"))
            self._log_lab.config(text=self._i18n.tr("frame.log"))
            self._btn_open_iff.config(text=self._i18n.tr("btn.open_iff"))
            self._iff_tree.heading("type_id", text=self._i18n.tr("iff.col_type"))
            self._iff_tree.heading("length", text=self._i18n.tr("iff.col_length"))
        if hasattr(self, "_menubar"):
            self._menubar.entryconfig(0, label=self._i18n.tr("menu.language"))
            self._lang_menu.entryconfig(0, label=self._i18n.tr("menu.lang_en"))
            self._lang_menu.entryconfig(1, label=self._i18n.tr("menu.lang_zh_cn"))
        self._refresh_detail_panel()

    def _build_widgets(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._notebook = notebook

        self._pak_tab = ttk.Frame(notebook)
        self._iff_tab = ttk.Frame(notebook)
        notebook.add(self._pak_tab, text=self._i18n.tr("tab.pac1"))
        notebook.add(self._iff_tab, text=self._i18n.tr("tab.iff"))

        self._build_pak_tab(self._pak_tab)
        self._build_iff_tab(self._iff_tab)

    def _build_pak_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)

        self._btn_open_pak = ttk.Button(
            top, text=self._i18n.tr("btn.open_pak"), command=self._on_open_pak
        )
        self._btn_open_pak.pack(side=tk.LEFT, padx=2)
        self._btn_open_folder = ttk.Button(
            top, text=self._i18n.tr("btn.open_folder"), command=self._on_open_pak_folder
        )
        self._btn_open_folder.pack(side=tk.LEFT, padx=2)
        self._recursive_var = tk.BooleanVar(value=True)
        self._chk_recursive = ttk.Checkbutton(
            top,
            text=self._i18n.tr("chk.recursive"),
            variable=self._recursive_var,
        )
        self._chk_recursive.pack(side=tk.LEFT, padx=6)
        self._pak_label = ttk.Label(top, text=self._i18n.tr("lbl.pak_none"))
        self._pak_label.pack(side=tk.LEFT, padx=8)

        mid = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left = ttk.Frame(mid, width=420)
        right = ttk.Frame(mid)
        mid.add(left, weight=2)
        mid.add(right, weight=1)

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        scroll_y = ttk.Scrollbar(tree_frame)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("path",),
            show="tree headings",
            yscrollcommand=scroll_y.set,
            selectmode="extended",
        )
        self._tree.heading("#0", text=self._i18n.tr("tree.col_name"))
        self._tree.heading("path", text=self._i18n.tr("tree.col_path"))
        self._tree.column("#0", width=180)
        self._tree.column("path", width=220)
        self._tree.pack(fill=tk.BOTH, expand=True)
        scroll_y.config(command=self._tree.yview)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        opts = ttk.Frame(right)
        opts.pack(fill=tk.X)
        self._raw_var = tk.BooleanVar(value=False)
        self._chk_raw = ttk.Checkbutton(
            opts,
            text=self._i18n.tr("chk.raw_export"),
            variable=self._raw_var,
        )
        self._chk_raw.pack(anchor=tk.W)

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=4)
        self._btn_export_all = ttk.Button(
            btn_row, text=self._i18n.tr("btn.export_all"), command=self._on_export_all
        )
        self._btn_export_all.pack(side=tk.LEFT, padx=2)
        self._btn_export_selected = ttk.Button(
            btn_row,
            text=self._i18n.tr("btn.export_selected"),
            command=self._on_export_selected,
        )
        self._btn_export_selected.pack(side=tk.LEFT, padx=2)
        self._btn_save_json = ttk.Button(
            btn_row, text=self._i18n.tr("btn.save_json"), command=self._on_save_json
        )
        self._btn_save_json.pack(side=tk.LEFT, padx=2)

        self._detail_lab = ttk.LabelFrame(right, text=self._i18n.tr("frame.detail"))
        self._detail_lab.pack(fill=tk.BOTH, expand=True, pady=4)
        self._detail = tk.Text(self._detail_lab, height=14, wrap=tk.WORD, state=tk.DISABLED)
        d_scroll = ttk.Scrollbar(self._detail_lab, command=self._detail.yview)
        self._detail.config(yscrollcommand=d_scroll.set)
        self._detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        d_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._head_lab = ttk.LabelFrame(parent, text=self._i18n.tr("frame.head"))
        self._head_lab.pack(fill=tk.X, padx=4, pady=4)
        self._head_text = tk.Text(self._head_lab, height=5, wrap=tk.WORD, state=tk.DISABLED)
        self._head_text.pack(fill=tk.X, padx=4, pady=4)

        self._log_lab = ttk.LabelFrame(parent, text=self._i18n.tr("frame.log"))
        self._log_lab.pack(fill=tk.BOTH, expand=False, padx=4, pady=4)
        self._log = tk.Text(self._log_lab, height=6, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(self._log_lab, command=self._log.yview)
        self._log.config(yscrollcommand=log_scroll.set)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    @staticmethod
    def _collect_pak_paths(folder: str, recursive: bool) -> List[str]:
        """Return sorted absolute paths to .pak files under folder."""
        root = Path(folder)
        if not root.is_dir():
            return []
        paths: List[str] = []
        if recursive:
            iterator = root.rglob("*")
        else:
            iterator = root.iterdir()
        for p in iterator:
            if p.is_file() and p.suffix.lower() == ".pak":
                paths.append(str(p.resolve()))
        paths.sort(key=lambda s: s.lower())
        return paths

    def _build_iff_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)
        self._btn_open_iff = ttk.Button(
            top, text=self._i18n.tr("btn.open_iff"), command=self._on_open_iff
        )
        self._btn_open_iff.pack(side=tk.LEFT, padx=2)
        self._iff_label = ttk.Label(top, text=self._i18n.tr("lbl.iff_none"))
        self._iff_label.pack(side=tk.LEFT, padx=8)

        cols = ("type_id", "length")
        self._iff_tree = ttk.Treeview(parent, columns=cols, show="headings", height=22)
        self._iff_tree.heading("type_id", text=self._i18n.tr("iff.col_type"))
        self._iff_tree.heading("length", text=self._i18n.tr("iff.col_length"))
        self._iff_tree.column("type_id", width=120)
        self._iff_tree.column("length", width=100)
        iff_scroll = ttk.Scrollbar(parent, command=self._iff_tree.yview)
        self._iff_tree.config(yscrollcommand=iff_scroll.set)
        self._iff_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        iff_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=4)

    def _log_line(self, msg: str) -> None:
        self._log.config(state=tk.NORMAL)
        self._log.insert(tk.END, msg + "\n")
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log.config(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.config(state=tk.DISABLED)

    def _export_busy(self) -> bool:
        if self._export_active:
            messagebox.showinfo(
                self._i18n.tr("msg.export_busy_title"),
                self._i18n.tr("msg.export_busy_body"),
            )
            return True
        return False

    def _on_open_pak(self) -> None:
        if self._export_busy():
            return
        path = filedialog.askopenfilename(
            title=self._i18n.tr("dlg.open_pak"),
            filetypes=[
                (self._i18n.tr("filetype.pak"), "*.pak"),
                (self._i18n.tr("filetype.all"), "*.*"),
            ],
        )
        if not path:
            return
        self._clear_log()
        resolved = str(Path(path).resolve())
        self._load_generation += 1
        gen = self._load_generation

        def worker() -> None:
            try:
                parsed = parse_pak_file(resolved)
            except (OSError, PakFormatError, ValueError) as exc:
                err_text = str(exc)
                self.after(
                    0,
                    lambda t=err_text, g=gen: self._load_failed_if_current(g, t),
                )
                return
            self.after(
                0,
                lambda g=gen: self._apply_bundles_if_current(
                    g, [(resolved, parsed)], resolved, merged=False
                ),
            )

        threading.Thread(target=worker, daemon=True).start()
        self._log_line(self._i18n.tr("log.parsing", path=resolved))

    def _on_open_pak_folder(self) -> None:
        if self._export_busy():
            return
        folder = filedialog.askdirectory(title=self._i18n.tr("dlg.pak_folder"))
        if not folder:
            return
        recursive = bool(self._recursive_var.get())
        paths = self._collect_pak_paths(folder, recursive)
        if not paths:
            if recursive:
                body = self._i18n.tr("msg.no_pak_recursive")
            else:
                body = self._i18n.tr("msg.no_pak_flat")
            messagebox.showinfo(self._i18n.tr("msg.no_pak_title"), body)
            return

        self._last_folder = folder
        self._clear_log()
        if recursive:
            scope_key = self._i18n.tr("log.scope_recursive")
        else:
            scope_key = self._i18n.tr("log.scope_flat")
        self._log_line(
            self._i18n.tr(
                "log.folder_line",
                folder=folder,
                scope=scope_key,
                count=len(paths),
            )
        )

        if recursive:
            paths_to_load = paths
            merged = True
            if len(paths) > 1:
                self._log_line(self._i18n.tr("log.merge_hint"))
        else:
            paths_to_load = paths[:1]
            merged = False
            if len(paths) > 1:
                self._log_line(
                    self._i18n.tr(
                        "log.first_only",
                        count=len(paths),
                        name=os.path.basename(paths_to_load[0]),
                    )
                )

        self._load_generation += 1
        gen = self._load_generation

        def worker() -> None:
            bundles: List[Tuple[str, ParsedPak]] = []
            for p in paths_to_load:
                try:
                    parsed = parse_pak_file(p)
                except (OSError, PakFormatError, ValueError) as exc:
                    err_s = str(exc)

                    def _log_skip_parse(pp: str = p, er: str = err_s) -> None:
                        self._log_line(self._i18n.tr("log.skip_parse", path=pp, err=er))

                    self.after(0, _log_skip_parse)
                    continue
                if parsed.file_chunk() is None:

                    def _log_skip_file(pp: str = p) -> None:
                        self._log_line(self._i18n.tr("log.skip_no_file", path=pp))

                    self.after(0, _log_skip_file)
                    continue
                bundles.append((p, parsed))

                def _log_ok(pp: str = p) -> None:
                    self._log_line(self._i18n.tr("log.parsed_ok", path=pp))

                self.after(0, _log_ok)

            self.after(
                0,
                lambda g=gen, b=bundles, f=folder, m=merged: self._folder_load_done_if_current(
                    g, b, f, m
                ),
            )

        threading.Thread(target=worker, daemon=True).start()
        self._log_line(self._i18n.tr("log.batch_start"))

    def _folder_load_done_if_current(
        self,
        gen: int,
        bundles: List[Tuple[str, ParsedPak]],
        folder: str,
        merged: bool,
    ) -> None:
        if gen != self._load_generation:
            return
        if not bundles:
            messagebox.showerror(
                self._i18n.tr("msg.load_fail_title"),
                self._i18n.tr("msg.load_fail_none"),
            )
            self._log_line(self._i18n.tr("log.no_pak_use"))
            return
        self._apply_bundles_if_current(gen, bundles, folder, merged=merged)

    def _load_failed_if_current(self, gen: int, err: str) -> None:
        if gen != self._load_generation:
            return
        self._log_line(self._i18n.tr("log.parse_fail_log", err=err))
        messagebox.showerror(self._i18n.tr("msg.parse_fail_title"), err)

    def _apply_bundles_if_current(
        self,
        gen: int,
        bundles: List[Tuple[str, ParsedPak]],
        label_hint: str,
        merged: bool,
    ) -> None:
        if gen != self._load_generation:
            return
        self._apply_bundles(bundles, label_hint, merged=merged)

    @staticmethod
    def _close_parsed_bundles(bundles: List[Tuple[str, ParsedPak]]) -> None:
        for _path, parsed in bundles:
            parsed.close()

    def _on_delete_window(self) -> None:
        self._close_parsed_bundles(self._bundle)
        self._bundle = []
        self.destroy()

    def _apply_bundles(
        self,
        bundles: List[Tuple[str, ParsedPak]],
        label_hint: str,
        merged: bool,
    ) -> None:
        """Rebuild tree and flat file list from one or more parsed paks."""
        self._close_parsed_bundles(self._bundle)
        self._bundle = list(bundles)
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._file_by_iid.clear()

        if merged:
            flat, dups = _collect_merged_file_rows(self._bundle)
            for rel_path, finfo, parsed in flat:
                _insert_merged_file(
                    self._tree, rel_path, finfo, parsed, self._file_by_iid
                )
            self._files_flat = flat
            if dups:
                uniq = sorted(set(dups))
                example = ""
                if uniq:
                    example = uniq[0]
                self._log_line(
                    self._i18n.tr(
                        "log.merge_dups",
                        count=len(dups),
                        example=example,
                    )
                )
            if len(self._bundle) > 1:
                self._pak_label.config(
                    text=self._i18n.tr(
                        "lbl.merge_preview",
                        n=len(self._bundle),
                        hint=label_hint,
                    )
                )
            else:
                self._pak_label.config(text=label_hint)
        else:
            pak_disk, parsed = self._bundle[0]
            fchunk = parsed.file_chunk()
            if fchunk is None:
                messagebox.showerror(
                    self._i18n.tr("msg.parse_fail_title"),
                    self._i18n.tr("msg.no_file_chunk"),
                )
                return
            root_entry = fchunk.root
            if isinstance(root_entry.info, FolderInfo):
                for child in root_entry.info.children:
                    _insert_tree(
                        self._tree, "", child, root_entry.name, self._file_by_iid, parsed
                    )
            elif isinstance(root_entry.info, FileInfo):
                fake = PakEntry(name=root_entry.name, info=root_entry.info)
                _insert_tree(self._tree, "", fake, "", self._file_by_iid, parsed)
            else:
                messagebox.showerror(
                    self._i18n.tr("msg.parse_fail_title"),
                    self._i18n.tr("msg.bad_root"),
                )
                return
            self._files_flat = [
                (rel.replace("/", "\\"), finfo, parsed)
                for rel, finfo in iter_file_entries(fchunk.root, "")
            ]
            self._pak_label.config(text=pak_disk)

        count = len(self._files_flat)
        self._log_line(self._i18n.tr("log.loaded_count", count=count))

        self._head_text.config(state=tk.NORMAL)
        self._head_text.delete("1.0", tk.END)
        if merged and len(self._bundle) > 1:
            self._head_text.insert(
                tk.END,
                self._i18n.tr("head.merge_intro", n=len(self._bundle)),
            )
            for pak_disk, parsed in self._bundle:
                head = parsed.head_chunk()
                b64 = ""
                if head is not None:
                    b64 = base64.b64encode(head.header).decode("ascii")
                self._head_text.insert(
                    tk.END,
                    "[%s]\n%s\n\n" % (os.path.basename(pak_disk), b64),
                )
        else:
            pak_disk, parsed = self._bundle[0]
            head = parsed.head_chunk()
            if head is not None:
                b64 = base64.b64encode(head.header).decode("ascii")
                self._head_text.insert(tk.END, b64)
        self._head_text.config(state=tk.DISABLED)
        self._refresh_detail_panel()

    def _refresh_detail_panel(self) -> None:
        sel = self._tree.selection()
        if not sel:
            self._detail.config(state=tk.NORMAL)
            self._detail.delete("1.0", tk.END)
            self._detail.config(state=tk.DISABLED)
            return
        first = sel[0]
        detail = ""
        if first in self._file_by_iid:
            rel_path, finfo, parsed = self._file_by_iid[first]
            source_disk = ""
            for disk, p in self._bundle:
                if p is parsed:
                    source_disk = disk
                    break
            ctype_hex = "0x%X" % finfo.compression_type
            lines = [
                self._i18n.tr("detail.path", path=rel_path),
                self._i18n.tr("detail.source_pak", path=source_disk),
                self._i18n.tr("detail.offset", n=finfo.offset),
                self._i18n.tr("detail.comp_len", n=finfo.compressed_length),
                self._i18n.tr("detail.orig_len", n=finfo.original_length),
                self._i18n.tr("detail.unknown1", hex=_bytes_to_hex(finfo.unknown1)),
                self._i18n.tr("detail.ctype", hex=ctype_hex),
                self._i18n.tr("detail.unknown2", hex=_bytes_to_hex(finfo.unknown2)),
            ]
            detail = "\n".join(lines)
        else:
            detail = self._i18n.tr("detail.folder_hint")

        self._detail.config(state=tk.NORMAL)
        self._detail.delete("1.0", tk.END)
        self._detail.insert(tk.END, detail)
        self._detail.config(state=tk.DISABLED)

    def _on_tree_select(self, _event: object) -> None:
        self._refresh_detail_panel()

    def _collect_files_under_tree_node(
        self, node: str
    ) -> List[Tuple[str, FileInfo, ParsedPak]]:
        out: List[Tuple[str, FileInfo, ParsedPak]] = []
        if node in self._file_by_iid:
            out.append(self._file_by_iid[node])
        for child in self._tree.get_children(node):
            out.extend(self._collect_files_under_tree_node(child))
        return out

    def _on_export_all(self) -> None:
        if not self._files_flat:
            messagebox.showinfo(
                self._i18n.tr("msg.tip_title"),
                self._i18n.tr("msg.open_first"),
            )
            return
        out_dir = filedialog.askdirectory(title=self._i18n.tr("dlg.export_dir"))
        if not out_dir:
            return
        raw = bool(self._raw_var.get())
        self._run_export_tasks(self._files_flat, out_dir, raw)

    def _on_export_selected(self) -> None:
        if not self._files_flat:
            messagebox.showinfo(
                self._i18n.tr("msg.tip_title"),
                self._i18n.tr("msg.open_first"),
            )
            return
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo(
                self._i18n.tr("msg.tip_title"),
                self._i18n.tr("msg.select_tree"),
            )
            return
        out_dir = filedialog.askdirectory(title=self._i18n.tr("dlg.export_dir"))
        if not out_dir:
            return
        raw = bool(self._raw_var.get())
        tasks: List[Tuple[str, FileInfo, ParsedPak]] = []
        for node in sel:
            tasks.extend(self._collect_files_under_tree_node(node))
        if not tasks:
            messagebox.showinfo(
                self._i18n.tr("msg.tip_title"),
                self._i18n.tr("msg.no_files_sel"),
            )
            return
        self._run_export_tasks(tasks, out_dir, raw)

    def _export_worker_finished(self, ok: int, total: int) -> None:
        self._log_line(self._i18n.tr("log.export_done", ok=ok, total=total))
        self._export_active = False

    def _run_export_tasks(
        self,
        tasks: List[Tuple[str, FileInfo, ParsedPak]],
        out_dir: str,
        raw: bool,
    ) -> None:
        def on_err(msg: str) -> None:
            self.after(0, lambda m=msg: self._log_line(m))

        def worker() -> None:
            ok = 0
            total = len(tasks)
            try:
                with ThreadPoolExecutor(max_workers=_MAX_EXPORT_WORKERS) as executor:
                    futures = [
                        executor.submit(
                            extract_entry_to_disk,
                            parsed,
                            rel_path,
                            finfo,
                            out_dir,
                            raw,
                            on_err,
                        )
                        for rel_path, finfo, parsed in tasks
                    ]
                    for future in as_completed(futures):
                        try:
                            if future.result():
                                ok += 1
                        except Exception:
                            pass
            except Exception as exc:
                err_text = str(exc)
                self.after(
                    0,
                    lambda e=err_text: self._log_line(
                        self._i18n.tr("log.export_error", err=e)
                    ),
                )
            finally:
                self.after(
                    0,
                    lambda o=ok, t=total: self._export_worker_finished(o, t),
                )

        self._export_active = True
        self._log_line(
            self._i18n.tr("log.export_start", count=len(tasks), dir=out_dir)
        )
        threading.Thread(target=worker, daemon=True).start()

    def _on_save_json(self) -> None:
        if not self._bundle:
            messagebox.showinfo(
                self._i18n.tr("msg.tip_title"),
                self._i18n.tr("msg.save_first"),
            )
            return
        default_name = "report.json"
        if len(self._bundle) == 1:
            base = os.path.splitext(os.path.basename(self._bundle[0][0]))[0]
            default_name = base + ".json"
        elif self._last_folder:
            default_name = (
                os.path.basename(self._last_folder.rstrip(os.sep)) + "-merged.json"
            )
        path = filedialog.asksaveasfilename(
            title=self._i18n.tr("dlg.save_json"),
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[
                (self._i18n.tr("filetype.json"), "*.json"),
                (self._i18n.tr("filetype.all"), "*.*"),
            ],
        )
        if not path:
            return
        report = _build_report(self._bundle, self._files_flat)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror(self._i18n.tr("msg.save_fail_title"), str(exc))
            return
        self._log_line(self._i18n.tr("log.report_saved", path=path))

    def _on_open_iff(self) -> None:
        path = filedialog.askopenfilename(
            title=self._i18n.tr("dlg.open_iff"),
            filetypes=[(self._i18n.tr("filetype.all"), "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            iff = parse_iff_bytes(data)
        except (OSError, ValueError) as exc:
            messagebox.showerror(self._i18n.tr("msg.iff_parse_fail"), str(exc))
            return

        for row in self._iff_tree.get_children():
            self._iff_tree.delete(row)
        self._iff_label.config(
            text=self._i18n.tr("lbl.iff_form", path=path, form=iff.form_type)
        )
        for ch in iff.chunks:
            self._iff_tree.insert("", tk.END, values=(ch.type_id, str(ch.length)))


def run_app() -> None:
    app = PakInspectorApp()
    app.mainloop()
