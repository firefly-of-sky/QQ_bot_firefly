"""Render Markdown as readable QQ text, without HTML or network access."""
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode


def _math_literal(state, silent):
    # Preserve common LaTeX delimiters and their contents instead of interpreting
    # underscores, stars and backslashes as Markdown formatting inside formulas.
    for opening, closing in ((r"\(", r"\)"), (r"\[", r"\]"), ("$$", "$$"), ("$", "$")):
        if not state.src.startswith(opening, state.pos, state.posMax):
            continue
        start = state.pos + len(opening)
        end = state.src.find(closing, start, state.posMax)
        while end >= 0 and end > start and state.src[end - 1] == "\\":
            end = state.src.find(closing, end + len(closing), state.posMax)
        if end < 0:
            return False
        if not silent:
            token = state.push("text", "", 0)
            token.content = state.src[state.pos:end + len(closing)]
        state.pos = end + len(closing)
        return True
    return False


_parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
_parser.inline.ruler.before("escape", "qq_math_literal", _math_literal)


def _inline(node: SyntaxTreeNode) -> str:
    if node.type in ("text", "code_inline", "html_inline"):
        return node.content
    if node.type in ("softbreak", "hardbreak"):
        return "\n"
    text = "".join(_inline(child) for child in node.children)
    if node.type == "link":
        url = node.attrs.get("href", "")
        return text if text == url or not url else f"{text}（{url}）" if text else url
    if node.type == "image":
        url = node.attrs.get("src", "")
        label = text or "图片"
        return f"{label}（{url}）" if url else label
    if node.type == "s":
        return f"（删除：{text}）"
    return text or node.content


def _table(node: SyntaxTreeNode) -> str:
    headers, rows = [], []
    for section in node.children:
        for row in section.children:
            cells = ["".join(_inline(child) for child in cell.children) for cell in row.children]
            if section.type == "thead":
                headers = cells
            else:
                rows.append(cells)
    if not rows:
        return " / ".join(headers)
    return "\n\n".join(
        f"【第 {index} 项】\n" + "\n".join(
            f"{headers[col] if col < len(headers) and headers[col] else f'第 {col + 1} 列'}：{value or '—'}"
            for col, value in enumerate(row))
        for index, row in enumerate(rows, 1))


def _block(node: SyntaxTreeNode) -> str:
    if node.is_root:
        return "\n\n".join(_block(child) for child in node.children)
    kind = node.type
    if kind in ("inline", "paragraph"):
        return _inline(node)
    if kind == "heading":
        return f"【{_inline(node)}】"
    if kind in ("fence", "code_block"):
        # Only remove the final structural newline, never strip code indentation.
        return node.content.removesuffix("\n")
    if kind in ("bullet_list", "ordered_list"):
        start = int(node.attrs.get("start", 1))
        items = []
        for index, item in enumerate(node.children, start):
            prefix = f"{index}. " if kind == "ordered_list" else "• "
            body = "\n".join(_block(child) for child in item.children)
            lines = body.split("\n")
            items.append(prefix + lines[0] + "".join("\n" + " " * len(prefix) + line for line in lines[1:]))
        return "\n".join(items)
    if kind == "blockquote":
        body = "\n\n".join(_block(child) for child in node.children)
        return "引用：\n" + "\n".join("  " + line for line in body.split("\n"))
    if kind == "table":
        return _table(node)
    if kind == "hr":
        return "────────"
    if node.children:
        return "\n\n".join(_block(child) for child in node.children)
    return node.content


def to_plain_text(markdown: str) -> str:
    result = _block(SyntaxTreeNode(_parser.parse(markdown)))
    # Reference definitions or a blank code fence may produce no display text.
    return result if result.strip() else markdown
