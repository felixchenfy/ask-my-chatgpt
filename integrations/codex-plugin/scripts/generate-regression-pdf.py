"""Build a deterministic 10,000-codepoint PDF fixture without contacting ChatGPT."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics, ttfonts
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "output" / "pdf"
    output.mkdir(parents=True, exist_ok=True)
    # Bundled ReportLab writes non-BMP CMap values as invalid five-digit hex.
    # Fix only this generator's process; PDF mappings must contain UTF-16BE.
    original_cmap = ttfonts.makeToUnicodeCMap

    def unicode_cmap(fontname, subset):
        cmap = original_cmap(fontname, subset)
        for index, point in enumerate(subset):
            if point > 0xFFFF:
                cmap = cmap.replace(
                    f"<{index:02X}> <{point:04X}>",
                    f"<{index:02X}> <{chr(point).encode('utf-16-be').hex().upper()}>",
                )
        return cmap

    ttfonts.makeToUnicodeCMap = unicode_cmap
    pdfmetrics.registerFont(TTFont("TestUnicode", "C:/Windows/Fonts/seguisym.ttf"))
    lines = []
    for index in range(1, 201):
        # Every line is distinct; an emoji exercises non-BMP codepoint counting.
        line = f"Line {index:03d}: A reply must belong to its own message."
        if index in (10, 100, 200):
            line = f"Line {index:03d}: Unicode test 😀 preserves the full text."
        lines.append(line.ljust(50, "."))
    assert all(len(line) == 50 for line in lines)
    expected = "\n".join(lines)
    target = output / "bridge-unicode-10000-regression.pdf"
    document = canvas.Canvas(str(target), pagesize=(595.28, 841.89))
    document.setTitle("Bridge Unicode 10,000-character regression")
    for start in range(0, 200, 40):
        document.setFont("TestUnicode", 11)
        for offset, line in enumerate(lines[start : start + 40]):
            document.drawString(48, 789 - offset * 18, line)
        document.showPage()
    document.save()
    extracted = "\n".join(page.extract_text().strip() for page in PdfReader(target).pages)
    assert extracted == expected, "PDF text extraction must match the source exactly"
    (output / "bridge-unicode-10000-expected.txt").write_text(expected, encoding="utf-8")
    manifest = {
        "attachment_path": str(target),
        "expected_response_characters": 10000,
        "expected_response_lines": 200,
        "expected_response_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
    }
    (output / "bridge-unicode-10000-expectations.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
