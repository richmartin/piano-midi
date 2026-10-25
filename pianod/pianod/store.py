"""State on the data partition, written so a power cut can never leave a half-written file.

Each write goes to a temporary file in the same directory, is fsync'd, renamed over the old file,
and then the directory itself is fsync'd so the rename is on the card too. A crash at any point
leaves either the old version or the new one. (Same scheme as hotpi's store.py.)
"""

from __future__ import annotations

import json
import os
import tempfile


def write_json(path: str, data) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_bytes(path: str, data: bytes) -> None:
    """Atomic write for binary files (a downloaded MIDI file, a saved recording)."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Unreadable or corrupt: keep a copy for the post-mortem and carry on with the default.
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        return default
