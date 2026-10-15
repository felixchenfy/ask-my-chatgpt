from __future__ import annotations

import re


def conservative_text_cleanup(markdown: str) -> str:
    lines = markdown.splitlines()
    cleaned: list[str] = []
    skip_press_download = False
    template_line = re.compile(
        r"^(share this news article on:?|share (this|on)|subscribe|sign up for|"
        r"related (stories|articles)|advertisement|"
        r"previous (item|image)|next (item|image)|caption:|credits:|close)$",
        re.I,
    )
    for line in lines:
        plain = re.sub(r"[*_#>`\[\]()]", "", line).strip()
        if re.match(r"^Related (Links|Topics|Articles|Stories)\b", plain, re.I):
            break
        if re.match(r"^Press Contact:\s*$", plain, re.I):
            skip_press_download = True
            continue
        if skip_press_download:
            if line.lstrip().startswith("![") and not line.lstrip().startswith("[!["):
                skip_press_download = False
            else:
                continue
        if template_line.search(plain):
            continue
        if re.match(r"^-\s+\[(X|Facebook|LinkedIn|Reddit|Print)\]", line, re.I):
            continue
        cleaned.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()
