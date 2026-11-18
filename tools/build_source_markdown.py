"""Generate an editable source-code submission document in Markdown."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "软著申报材料" / "04_源程序拼版文档_前30页后30页.md"


def source_files():
    backend = [
        ROOT / "run.py",
        ROOT / "server" / "__init__.py",
        ROOT / "server" / "main.py",
        ROOT / "server" / "core" / "config.py",
        ROOT / "server" / "core" / "utils.py",
        ROOT / "server" / "core" / "store.py",
    ]
    ordered = (
        backend
        + sorted((ROOT / "server" / "services").glob("*.py"))
        + sorted((ROOT / "server" / "api").glob("*.py"))
        + sorted((ROOT / "server" / "tools").glob("*.py"))
        + [ROOT / "web" / "css" / "app.css"]
        + [
            ROOT / "web" / "js" / "app.js",
            ROOT / "web" / "js" / "api.js",
            ROOT / "web" / "js" / "store.js",
            ROOT / "web" / "js" / "utils.js",
        ]
        + sorted((ROOT / "web" / "js" / "components").glob("*.js"))
        + sorted((ROOT / "web" / "js" / "pages").glob("*.js"))
        + [ROOT / "web" / "index.html", ROOT / "web" / "favicon.svg"]
    )
    return [p for p in ordered if p.exists()]


def wrap_line(line: str, width: int = 108):
    line = line.replace("\t", "    ").rstrip()
    if not line:
        return [""]
    return [line[i : i + width] for i in range(0, len(line), width)]


def main():
    files = source_files()
    lines = []
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(ROOT).as_posix()
        lines.append(f"===== FILE {index:02d}: {rel} =====")
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            lines.extend(wrap_line(raw))
        lines.append("")

    sampled = lines if len(lines) < 3000 else lines[:1500] + lines[-1500:]
    total_pages = (len(sampled) + 49) // 50

    doc = [
        "# FreeFarmNRS V1.0 源程序拼版文档",
        "",
        "> 本文件为可编辑源程序拼版稿。正式转 Word/PDF 时，以每页 50 行排版，并只提交前 30 页和后 30 页。",
        "",
        "## 材料信息",
        "",
        "| 项目 | 内容 |",
        "|---|---|",
        "| 软件名称 | FreeFarmNRS |",
        "| 版本 | V1.0 |",
        f"| 自有源文件数 | {len(files)} |",
        f"| 展开后源程序行数 | {len(lines):,} |",
        f"| 本文件代码页数 | {total_pages} |",
        "| 每页行数 | 50 |",
        "| 选页规则 | 前 30 页 + 后 30 页 |",
        "| 排除内容 | `web/vendor`、`.venv`、示例数据、二进制模型和生成文件 |",
        "",
        "## 排版说明",
        "",
        "- 长行按 108 个字符强制换行，避免转版后超出页面。",
        "- 空行保留代码结构。",
        "- 行号为本拼版文档的连续代码行号。",
        "- 转入 Word 时使用 Consolas 6.8–7.5 pt，固定每页 50 行。",
        "- 每页之间插入分页符。",
        "- 前后代码页均属于同一完整源程序的连续摘录，不修改源代码语义。",
        "",
    ]

    for page in range(total_pages):
        group = sampled[page * 50 : (page + 1) * 50]
        section = "前 30 页" if page < 30 else "后 30 页"
        doc.append(f"## 代码第 {page + 1} 页（{section}）")
        doc.extend(["", "```text"])
        for offset, text in enumerate(group, start=1):
            number = page * 50 + offset
            doc.append(f"{number:05d}  {text}")
        doc.extend(["```", ""])

    OUT.write_text("\n".join(doc), encoding="utf-8")
    print(OUT)
    print(f"source_files={len(files)} lines={len(lines)} pages={total_pages}")


if __name__ == "__main__":
    main()
