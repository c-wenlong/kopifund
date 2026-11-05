"""Write a file in one step, readable by its owner only.

Every file Kopifund writes beside the record (the cache, the rules, the
profile, the key store, the phone link's devices, the copilot's threads) goes
through write_private, so a reader or a crash sees the old file or the new one
and never half of either, and no file is ever briefly readable by others.
"""
import os
import tempfile
from pathlib import Path


def write_private(path, data):
    """Replace `path` with `data` (bytes, or a str written as UTF-8), mode 600.

    The bytes go to a temp file of this call's own in the same folder (mkstemp
    makes it mode 600, and two writers never share one), are flushed to disk,
    then os.replace swaps it in. A file at `path` linked to another name is
    replaced, never written through. On any failure the temp file is removed
    and the old file stays. The folder is synced last so the swap itself
    survives a power cut; a folder that cannot be synced is not an error, the
    file is already in place."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    # the name is cut so the temp file's name always fits, whatever `path` is
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name[:64]}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError:
        pass
    return path
