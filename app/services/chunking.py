"""Deterministic overlapping windows measured in Unicode characters."""


def validate_windows(size: int, overlap: int) -> None:
    if size < 1 or overlap < 0 or 4 * overlap > 3 * size:
        raise ValueError("CHUNK_OVERLAP must be between zero and 75% of CHUNK_SIZE.")


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    validate_windows(size, overlap)
    step = size - overlap
    windows = 1 + (max(0, len(text) - size) + step - 1) // step
    if windows > 4096:
        raise ValueError("Document requires too many chunks.")
    # Each repeated boundary contributes exactly overlap extra code points.
    # Four bytes per code point conservatively bounds all UTF-8 encodings.
    if 4 * (len(text) + (windows - 1) * overlap) > 8 * 1024 * 1024:
        raise ValueError("Generated chunks exceed the output budget.")
    chunks = []
    for index in range(windows):
        start = index * step
        chunk = text[start : start + size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks
