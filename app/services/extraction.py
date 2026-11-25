"""Validate and extract supported plaintext and PDF content."""

import os
import subprocess
import sys
from pathlib import Path

from app.services.pdf_worker import MAX_TEXT_CHARS


class InvalidDocument(ValueError):
    """File content cannot be accepted for processing."""


def extract_text(data: bytes, media_type: str) -> str:
    if media_type == "application/pdf":
        if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
            raise InvalidDocument
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py"))],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={
                    k: os.environ[k]
                    for k in ("SystemRoot", "WINDIR")
                    if k in os.environ
                },
                timeout=15,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise InvalidDocument from None
        data = result.stdout
    elif media_type != "text/plain":
        raise InvalidDocument
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidDocument from None
    if media_type == "text/plain" and text.lstrip().startswith("%PDF-"):
        raise InvalidDocument
    if (
        len(text) > MAX_TEXT_CHARS
        or not any(char.isalnum() for char in text)
        or any(
            (ord(char) < 32 and char not in "\t\r\n\f") or 127 <= ord(char) <= 159
            for char in text
        )
    ):
        raise InvalidDocument
    return text
