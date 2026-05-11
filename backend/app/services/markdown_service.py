import re

import markdown as md

_IMAGE_BRACKETED_RE = re.compile(
    r"\[\!\[([^\]]*)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)\](?!\s*\()"
)
_IMAGE_SPACED_RE = re.compile(
    r"\!\[([^\]]*)\]\s*\(([^)\s]+(?:\s+\"[^\"]*\")?)\)"
)

def normalize_markdown(markdown_text: str) -> str:
    if not markdown_text:
        return ""
    text = str(markdown_text)
    text = _IMAGE_BRACKETED_RE.sub(r"![\1](\2)", text)
    text = _IMAGE_SPACED_RE.sub(r"![\1](\2)", text)
    return text

def markdown_to_html(markdown_text: str) -> str:
   
    markdown_text = normalize_markdown(markdown_text)
    return md.markdown(
        markdown_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"],
        output_format="html5",
    )
