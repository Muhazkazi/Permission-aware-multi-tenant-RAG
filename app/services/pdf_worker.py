"""Bounded PDF extraction subprocess. Input and output use binary pipes."""

import sys
from io import BytesIO

MAX_TEXT_CHARS = 1_000_000


def main() -> None:
    if sys.platform.startswith("linux"):
        import resource

        memory = 384 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(sys.stdin.buffer.read()), strict=True)
    if reader.is_encrypted or len(reader.pages) > 200:
        raise ValueError
    pages = []
    total = 0
    for page in reader.pages:
        text = page.extract_text()
        total += len(text) + 1
        if total > MAX_TEXT_CHARS:
            raise ValueError
        pages.append(text)
    sys.stdout.buffer.write("\n".join(pages).encode("utf-8"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
