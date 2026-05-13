# -*- coding: utf-8 -*-
"""Tkinter GUI for browsing and extracting PAC1 .pak files."""

from __future__ import annotations

import base64
import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from pakinspector.extract import extract_entry_to_disk
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
        self.title("PakInspector")
        self.geometry("960x640")
        self.minsize(720, 480)

        self._bundle: List[Tuple[str, ParsedPak]] = []
        self._files_flat: List[Tuple[str, FileInfo, ParsedPak]] = []
        self._file_by_iid: Dict[str, Tuple[str, FileInfo, ParsedPak]] = {}

        self._last_folder: Optional[str] = None
        self._load_generation: int = 0

        self.protocol("WM_DELETE_WINDOW", self._on_delete_window)
        self._build_widgets()

    def _build_widgets(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        pak_tab = ttk.Frame(notebook)
        iff_tab = ttk.Frame(notebook)
        notebook.add(pak_tab, text="PAC1 (.pak)")
        notebook.add(iff_tab, text="IFF 块")

        self._build_pak_tab(pak_tab)
        self._build_iff_tab(iff_tab)

    def _build_pak_tab(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=4, pady=4)

        ttk.Button(top, text="打开 .pak…", command=self._on_open_pak).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="打开文件夹…", command=self._on_open_pak_folder).pack(
            side=tk.LEFT, padx=2
        )
        self._recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top,
            text="包含子目录（开启时合并预览所有 .pak 的项目结构）",
            variable=self._recursive_var,
        ).pack(side=tk.LEFT, padx=6)
        self._pak_label = ttk.Label(top, text="未加载文件")
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
        self._tree.heading("#0", text="名称")
        self._tree.heading("path", text="包内路径")
        self._tree.column("#0", width=180)
        self._tree.column("path", width=220)
        self._tree.pack(fill=tk.BOTH, expand=True)
        scroll_y.config(command=self._tree.yview)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        opts = ttk.Frame(right)
        opts.pack(fill=tk.X)
        self._raw_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts,
            text="原始导出（不解压、不处理压缩）",
            variable=self._raw_var,
        ).pack(anchor=tk.W)

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=4)
        ttk.Button(btn_row, text="导出全部…", command=self._on_export_all).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_row, text="导出选中子树…", command=self._on_export_selected).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(btn_row, text="保存 JSON 报告…", command=self._on_save_json).pack(
            side=tk.LEFT, padx=2
        )

        detail_lab = ttk.LabelFrame(right, text="条目详情")
        detail_lab.pack(fill=tk.BOTH, expand=True, pady=4)
        self._detail = tk.Text(detail_lab, height=14, wrap=tk.WORD, state=tk.DISABLED)
        d_scroll = ttk.Scrollbar(detail_lab, command=self._detail.yview)
        self._detail.config(yscrollcommand=d_scroll.set)
        self._detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        d_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        head_lab = ttk.LabelFrame(parent, text="HEAD（Base64）")
        head_lab.pack(fill=tk.X, padx=4, pady=4)
        self._head_text = tk.Text(head_lab, height=5, wrap=tk.WORD, state=tk.DISABLED)
        self._head_text.pack(fill=tk.X, padx=4, pady=4)

        log_lab = ttk.LabelFrame(parent, text="日志")
        log_lab.pack(fill=tk.BOTH, expand=False, padx=4, pady=4)
        self._log = tk.Text(log_lab, height=6, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_lab, command=self._log.yview)
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
        ttk.Button(top, text="打开 IFF/FORM 文件…", command=self._on_open_iff).pack(
            side=tk.LEFT, padx=2
        )
        self._iff_label = ttk.Label(top, text="未加载")
        self._iff_label.pack(side=tk.LEFT, padx=8)

        cols = ("type_id", "length")
        self._iff_tree = ttk.Treeview(parent, columns=cols, show="headings", height=22)
        self._iff_tree.heading("type_id", text="TypeId")
        self._iff_tree.heading("length", text="Length")
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

    def _on_open_pak(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 .pak 文件",
            filetypes=[("PAC1 / pak", "*.pak"), ("所有文件", "*.*")],
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
        self._log_line("正在解析: %s" % resolved)

    def _on_open_pak_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择包含 .pak 的文件夹")
        if not folder:
            return
        recursive = bool(self._recursive_var.get())
        paths = self._collect_pak_paths(folder, recursive)
        if not paths:
            scope = "（含子目录）" if recursive else "（仅当前目录）"
            messagebox.showinfo(
                "未找到 .pak",
                "所选文件夹%s内没有 .pak 文件。" % scope,
            )
            return

        self._last_folder = folder
        self._clear_log()
        scope = "含子目录" if recursive else "仅当前目录"
        self._log_line(
            "文件夹: %s | %s | 发现 %d 个 .pak"
            % (folder, scope, len(paths))
        )

        if recursive:
            paths_to_load = paths
            merged = True
            if len(paths) > 1:
                self._log_line("已开启子目录扫描：将合并预览全部 .pak 的项目结构")
        else:
            paths_to_load = paths[:1]
            merged = False
            if len(paths) > 1:
                self._log_line(
                    "当前目录有 %d 个 .pak；未勾选「包含子目录」时仅载入第一个：%s"
                    % (len(paths), os.path.basename(paths_to_load[0]))
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
                        self._log_line("跳过（解析失败）: %s — %s" % (pp, er))

                    self.after(0, _log_skip_parse)
                    continue
                if parsed.file_chunk() is None:

                    def _log_skip_file(pp: str = p) -> None:
                        self._log_line("跳过（无 FILE 块）: %s" % pp)

                    self.after(0, _log_skip_file)
                    continue
                bundles.append((p, parsed))

                def _log_ok(pp: str = p) -> None:
                    self._log_line("已解析: %s" % pp)

                self.after(0, _log_ok)

            self.after(
                0,
                lambda g=gen, b=bundles, f=folder, m=merged: self._folder_load_done_if_current(
                    g, b, f, m
                ),
            )

        threading.Thread(target=worker, daemon=True).start()
        self._log_line("开始批量解析…")

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
            messagebox.showerror("载入失败", "没有成功载入任何 .pak 文件。")
            self._log_line("没有可用的 .pak")
            return
        self._apply_bundles_if_current(gen, bundles, folder, merged=merged)

    def _load_failed_if_current(self, gen: int, err: str) -> None:
        if gen != self._load_generation:
            return
        self._log_line("解析失败: %s" % err)
        messagebox.showerror("解析失败", err)

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
                self._log_line(
                    "合并时出现 %d 条路径重复，已以后载入的 .pak 为准（示例: %s）"
                    % (len(dups), uniq[0] if uniq else "")
                )
            if len(self._bundle) > 1:
                self._pak_label.config(
                    text="合并预览 · %d 个 .pak | %s"
                    % (len(self._bundle), label_hint)
                )
            else:
                self._pak_label.config(text=label_hint)
        else:
            pak_disk, parsed = self._bundle[0]
            fchunk = parsed.file_chunk()
            if fchunk is None:
                messagebox.showerror("解析失败", "未找到 FILE 块")
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
                messagebox.showerror("解析失败", "无法识别的根条目")
                return
            self._files_flat = [
                (rel.replace("/", "\\"), finfo, parsed)
                for rel, finfo in iter_file_entries(fchunk.root, "")
            ]
            self._pak_label.config(text=pak_disk)

        count = len(self._files_flat)
        self._log_line("已加载，共 %d 个文件条目" % count)

        self._head_text.config(state=tk.NORMAL)
        self._head_text.delete("1.0", tk.END)
        if merged and len(self._bundle) > 1:
            self._head_text.insert(
                tk.END,
                "（合并 %d 个 .pak，各包 HEAD 如下）\n\n" % len(self._bundle),
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

    def _on_tree_select(self, _event: object) -> None:
        sel = self._tree.selection()
        if not sel:
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
            lines = [
                "path: %s" % rel_path,
                "sourcePak: %s" % source_disk,
                "offset: %d" % finfo.offset,
                "compressedLength: %d" % finfo.compressed_length,
                "originalLength: %d" % finfo.original_length,
                "unknown1: %s" % _bytes_to_hex(finfo.unknown1),
                "compressionType: 0x%X" % finfo.compression_type,
                "unknown2: %s" % _bytes_to_hex(finfo.unknown2),
            ]
            detail = "\n".join(lines)
        else:
            detail = "文件夹（导出选中子树可包含其下所有文件）"

        self._detail.config(state=tk.NORMAL)
        self._detail.delete("1.0", tk.END)
        self._detail.insert(tk.END, detail)
        self._detail.config(state=tk.DISABLED)

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
            messagebox.showinfo("提示", "请先打开 .pak 或文件夹")
            return
        out_dir = filedialog.askdirectory(title="选择导出目录")
        if not out_dir:
            return
        raw = bool(self._raw_var.get())
        self._run_export_tasks(self._files_flat, out_dir, raw)

    def _on_export_selected(self) -> None:
        if not self._files_flat:
            messagebox.showinfo("提示", "请先打开 .pak 或文件夹")
            return
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请在树中选择节点")
            return
        out_dir = filedialog.askdirectory(title="选择导出目录")
        if not out_dir:
            return
        raw = bool(self._raw_var.get())
        tasks: List[Tuple[str, FileInfo, ParsedPak]] = []
        for node in sel:
            tasks.extend(self._collect_files_under_tree_node(node))
        if not tasks:
            messagebox.showinfo("提示", "选中节点下没有文件")
            return
        self._run_export_tasks(tasks, out_dir, raw)

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
            for rel_path, finfo, parsed in tasks:
                if extract_entry_to_disk(parsed, rel_path, finfo, out_dir, raw, on_err):
                    ok += 1
            total = len(tasks)
            self.after(
                0,
                lambda o=ok, t=total: self._log_line(
                    "导出完成：成功 %d / 共 %d" % (o, t)
                ),
            )

        self._log_line("开始导出 %d 个文件到 %s" % (len(tasks), out_dir))
        threading.Thread(target=worker, daemon=True).start()

    def _on_save_json(self) -> None:
        if not self._bundle:
            messagebox.showinfo("提示", "请先打开 .pak 或文件夹")
            return
        default_name = "report.json"
        if len(self._bundle) == 1:
            base = os.path.splitext(os.path.basename(self._bundle[0][0]))[0]
            default_name = base + ".json"
        elif self._last_folder:
            default_name = os.path.basename(self._last_folder.rstrip(os.sep)) + "-merged.json"
        path = filedialog.asksaveasfilename(
            title="保存 JSON",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        report = _build_report(self._bundle, self._files_flat)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self._log_line("已保存报告: %s" % path)

    def _on_open_iff(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 IFF 文件",
            filetypes=[("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            iff = parse_iff_bytes(data)
        except (OSError, ValueError) as exc:
            messagebox.showerror("解析失败", str(exc))
            return

        for row in self._iff_tree.get_children():
            self._iff_tree.delete(row)
        self._iff_label.config(text="%s  form=%s" % (path, iff.form_type))
        for ch in iff.chunks:
            self._iff_tree.insert("", tk.END, values=(ch.type_id, str(ch.length)))


def run_app() -> None:
    app = PakInspectorApp()
    app.mainloop()
