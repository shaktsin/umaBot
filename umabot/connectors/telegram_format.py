"""Convert LLM markdown responses to Telegram-compatible HTML and handle message splitting."""
from __future__ import annotations

import re
from typing import List

# Telegram sendMessage limit
MAX_MESSAGE_LENGTH = 4096

# Placeholder prefix for protecting code blocks during conversion
_PLACEHOLDER = "\x00CODEBLOCK"


def markdown_to_telegram_html(text: str) -> str:
    """Convert common markdown patterns to Telegram-compatible HTML.

    Supported: code blocks, inline code, bold, italic, strikethrough,
    links, headers, lists, blockquotes.  Plain text passes through
    unchanged (with HTML entities escaped).
    """
    if not text:
        return text

    # 1. Extract fenced code blocks and protect them from further processing
    code_blocks: list[str] = []

    def _replace_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = _escape_html(m.group(2))
        if lang:
            html = f'<pre><code class="language-{lang}">{code}</code></pre>'
        else:
            html = f"<pre>{code}</pre>"
        idx = len(code_blocks)
        code_blocks.append(html)
        return f"{_PLACEHOLDER}{idx}\x00"

    text = re.sub(r"```(\w*)\n(.*?)```", _replace_code_block, text, flags=re.DOTALL)

    # 2. Extract inline code
    inline_codes: list[str] = []

    def _replace_inline_code(m: re.Match) -> str:
        code = _escape_html(m.group(1))
        idx = len(inline_codes)
        inline_codes.append(f"<code>{code}</code>")
        return f"\x00INLINECODE{idx}\x00"

    text = re.sub(r"`([^`]+)`", _replace_inline_code, text)

    # 3. Escape HTML entities in remaining text
    text = _escape_html(text)

    # 4. Inline formatting
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic: *text* or _text_ (not inside words for underscore)
    text = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", text)
    # Strikethrough: ~~text~~
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # 5. Block-level formatting (line by line)
    lines = text.split("\n")
    result_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Headers: # text -> bold
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            result_lines.append(f"<b>{header_match.group(2)}</b>")
            i += 1
            continue

        # Blockquotes: > text
        if line.startswith("&gt; ") or line == "&gt;":
            quote_lines = []
            while i < len(lines) and (lines[i].startswith("&gt; ") or lines[i] == "&gt;"):
                content = lines[i][5:] if lines[i].startswith("&gt; ") else ""
                quote_lines.append(content)
                i += 1
            result_lines.append(f'<blockquote>{chr(10).join(quote_lines)}</blockquote>')
            continue

        # Unordered lists: - item or * item (but not **)
        list_match = re.match(r"^(\s*)[-*]\s+(.+)$", line)
        if list_match and not line.lstrip().startswith("**"):
            indent = list_match.group(1)
            result_lines.append(f"{indent}• {list_match.group(2)}")
            i += 1
            continue

        result_lines.append(line)
        i += 1

    text = "\n".join(result_lines)

    # 6. Restore placeholders
    for idx, html in enumerate(inline_codes):
        text = text.replace(f"\x00INLINECODE{idx}\x00", html)
    for idx, html in enumerate(code_blocks):
        text = text.replace(f"{_PLACEHOLDER}{idx}\x00", html)

    return text


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> List[str]:
    """Split a message into chunks respecting Telegram's character limit.

    Split priority: paragraph boundary > line boundary > sentence end > hard split.
    Handles unclosed HTML tags across chunk boundaries.
    """
    if not text or len(text) <= max_length:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Find best split point before max_length
        split_at = _find_split_point(remaining, max_length)
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip("\n")

        # Handle unclosed tags
        unclosed = _find_unclosed_tags(chunk)
        if unclosed:
            # Close tags at end of chunk (reverse order)
            for tag in reversed(unclosed):
                chunk += f"</{tag}>"
            # Re-open tags at start of next chunk
            prefix = "".join(f"<{tag}>" for tag in unclosed)
            remaining = prefix + remaining

        chunks.append(chunk)

    return chunks


def _escape_html(text: str) -> str:
    """Escape HTML entities."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _find_split_point(text: str, max_length: int) -> int:
    """Find the best position to split text at or before max_length."""
    window = text[:max_length]

    # Paragraph boundary (double newline)
    pos = window.rfind("\n\n")
    if pos > max_length // 4:
        return pos + 1

    # Line boundary
    pos = window.rfind("\n")
    if pos > max_length // 4:
        return pos + 1

    # Sentence end
    for pattern in (". ", "! ", "? "):
        pos = window.rfind(pattern)
        if pos > max_length // 4:
            return pos + 2

    # Hard split
    return max_length


_PAIRED_TAGS = ("pre", "code", "b", "i", "s", "blockquote")


def _find_unclosed_tags(html: str) -> list[str]:
    """Return list of tag names that are opened but not closed."""
    stack: list[str] = []
    for m in re.finditer(r"<(/?)(\w+)[^>]*>", html):
        is_close = m.group(1) == "/"
        tag = m.group(2).lower()
        if tag not in _PAIRED_TAGS:
            continue
        if is_close:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    return stack
