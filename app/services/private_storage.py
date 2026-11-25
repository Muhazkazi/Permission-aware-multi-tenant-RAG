"""Open a verified private tenant directory for the lifetime of an upload."""

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def tenant_directory(root: Path, namespace: str) -> Iterator[int | None]:
    if (
        not root.is_absolute()
        or root == Path(root.anchor)
        or ".." in root.parts
        or not re.fullmatch("[0-9a-f]{64}", namespace)
    ):
        raise ValueError("Unsafe storage directory.")
    target = root / namespace
    if os.name == "nt":
        # Trusted parent ACLs are a prerequisite on Windows. Check all existing
        # ancestors, including junctions, before creating anything below them.
        current = Path(root.anchor)
        for part in target.parts[1:]:
            current /= part
            current.mkdir(mode=0o700, exist_ok=True)
            info = current.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Unsafe storage directory.")
        yield None
        return

    flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY
    fd = os.open(root.anchor, flags)
    try:
        for index, part in enumerate(target.parts[1:], start=1):
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if index >= len(root.parts) - 1:
                if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError(
                        "Storage requires a private service-owned directory."
                    )
            elif info.st_uid not in (0, os.geteuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise ValueError("Unsafe storage ancestor.")
        yield fd
    finally:
        os.close(fd)
