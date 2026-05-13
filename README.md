# PakInspector

用于浏览与解压 **《Arma Reforger》PAC1 格式 `.pak` 包** 的 Python 工具，带 **Tkinter 图形界面**（标准库，无额外依赖）。

## 仓库与上游

- **本项目（维护中的 Python 版）**：[https://github.com/ViVi141/PakInspector](https://github.com/ViVi141/PakInspector)
- **原项目（C# / Kaitai 版）**：[https://github.com/rvost/PakInspector](https://github.com/rvost/PakInspector)

本仓库在 [rvost/PakInspector](https://github.com/rvost/PakInspector) 的基础上改写为纯 Python 实现并加入 GUI；格式与解压逻辑仍面向同一 PAC1 生态。若你更关心上游发布与历史 Issue，请优先查看原仓库。

## 功能

- **打开文件夹**：扫描 `.pak`；勾选 **「包含子目录」** 时会递归收集子文件夹内所有分包，并在界面中 **合并为一套项目树**（同名路径以后解析到的分包为准）；未勾选时仅载入当前目录下排序后的 **第一个** `.pak`
- **打开单个 .pak**：仍支持单文件浏览
- 查看合并树或单包内的文件与元数据（详情中含 `sourcePak`，标明条目来自哪个 `.pak`）
- 导出全部或选中子树（按条目所属分包从对应文件解压）；支持「原始导出」（不解压）
- 支持压缩类型：`0`（存储）、`0x106`（zlib 风格头 + DEFLATE，与常见 Reforger 资源一致）
- 保存 JSON 报告：单包时含 `head`；多包合并时含 `sources`（每包 `pakPath` + `head`）及 `files[].sourcePak`
- 附加页：列出任意 **IFF / FORM** 文件的块（TypeId、Length）

## 限制

- `.pak` 使用 **只读 mmap** 映射整文件，且 **不在内存中重复保存 DATA / 未知块的大段副本**；常驻占用通常仍与映射规模及访问模式相关，但已低于「整包 `bytes` + 块体再拷贝一份」的旧行为。切换打开文件时 GUI 会释放旧映射。
- 未知压缩类型需在 `pakinspector/extract.py` 中扩展。

## 环境要求

- Python **3.10+**
- 带 **Tk** 的 Python（Windows 官方安装包默认包含；部分 Linux 需安装 `python3-tk`）

## 安装与运行

在仓库根目录：

```bash
pip install .
pakinspector
```

或直接运行模块（无需安装时，请在仓库根目录执行，以便解析包路径）：

```bash
python -m pakinspector
```

## 从源码构建发行包

```bash
pip install build
python -m build
```

产物在 `dist/`（wheel 与 sdist）。

## 问题反馈

Bug 与功能建议请在本仓库 [Issues](https://github.com/ViVi141/PakInspector/issues) 提出。若涉及与上游 C# 版行为是否一致，可对照 [rvost/PakInspector](https://github.com/rvost/PakInspector) 的说明与发布。

## 致谢

PAC1 相关探索离不开社区既有工具与资料；原 C# 实现及讨论见 [rvost/PakInspector](https://github.com/rvost/PakInspector)，其 README 亦致谢了 [@FlipperPlz](https://github.com/FlipperPlz) 的 [PakExplorer](https://github.com/FlipperPlz/PakExplorer) 等先行工作。

## 许可证

Apache License 2.0，见仓库根目录 `LICENSE`。
