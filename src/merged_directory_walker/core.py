"""Core implementation of the merged directory walker.

Design decisions:
- Per-directory entries are sorted by name before recursion. This mirrors os.walk's
  topdown=True deterministic ordering and makes the output reproducible across runs
  and platforms. The alternative (filesystem iteration order) is OS- and
  inode-dependent and therefore non-deterministic.
- Files and directories at the same path are never mixed: a path that is a
  directory in root A and a regular file in root B is a genuine conflict. We yield
  the directory group and the file group separately so the caller can decide how to
  handle the conflict rather than silently picking one.
- Files and directories are recursed independently. Files at the current level
  are all yielded before descending into any subdirectory. This gives a breadth-
  first ordering *within* each directory level, while the overall walk is still
  depth-first across directories. The benefit: a caller processing a merged tree
  sees all siblings before any children, which matches how merged-file consumers
  (overlay fs, sync tools) typically want to operate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass(frozen=True)
class MergeResult:
    """A single grouped entry produced by :class:`MergedDirectoryWalker`.

    ``relative_path`` is the path relative to each root, using ``os.sep``.
    ``kind`` is either ``'file'`` or ``'dir'`` and is determined by the type of
    the entry in the first root that contains it. When roots disagree on the
    type (file vs dir at the same relative path), the conflicting groups are
    yielded separately so the caller can detect the conflict.
    ``entries`` is a list aligned with the walker's ``roots`` argument; ``None``
    marks a root where this path does not exist.
    """

    relative_path: str
    kind: str
    entries: Tuple[Optional[str], ...]


class MergedDirectoryWalker:
    """Walk multiple directory trees in lockstep, yielding grouped paths.

    For each relative path that exists in at least one root, yields a
    :class:`MergeResult` whose ``entries`` list is aligned with ``roots``. Roots
    where the path is absent contribute ``None``.

    Roots that do not exist or are not directories are silently treated as empty
    (all entries None). This keeps the walker usable when a root is optional —
    for example a baseline directory that may not yet exist.

    Symbolic links are not followed. A symlink is treated as a leaf entry of
    type determined by ``os.path.islink`` / ``os.path.isdir`` on the link itself
    (a symlink to a directory is reported as ``kind='dir'`` but is not recursed
    into). This prevents accidental traversal outside the merged tree.
    """

    def __init__(self, roots: List[str]) -> None:
        if not isinstance(roots, (list, tuple)):
            raise TypeError("roots must be a list or tuple of paths")
        # Copy and normalise so callers can mutate their list freely and so that
        # trailing-slash differences between roots do not produce phantom-empty
        # sub-paths.
        self._roots: Tuple[str, ...] = tuple(os.path.normpath(r) for r in roots)

    @property
    def roots(self) -> Tuple[str, ...]:
        """The normalised root paths, in the order given to the constructor."""
        return self._roots

    def walk(self) -> Iterator[MergeResult]:
        """Yield :class:`MergeResult` objects for the merged tree.

        Yields are ordered deterministically: within each directory, file entries
        come before subdirectory entries, and each group is sorted by name.
        """
        if not self._roots:
            return
        yield from self._walk_dir("")

    def _walk_dir(self, rel: str) -> Iterator[MergeResult]:
        # Gather the absolute path for each root, or None if the root is missing
        # or the subdirectory does not exist in that root.
        abs_paths: List[Optional[str]] = []
        for root in self._roots:
            base = root if not rel else os.path.join(root, rel)
            if base is None or not os.path.isdir(base) or os.path.islink(base):
                # Non-existent root or a symlink leaf (we do not recurse links).
                abs_paths.append(None)
                continue
            abs_paths.append(base)

        files: Dict[str, List[Optional[str]]] = {}
        dirs: Dict[str, List[Optional[str]]] = {}

        for idx, ap in enumerate(abs_paths):
            if ap is None:
                continue
            try:
                with os.scandir(ap) as it:
                    for entry in it:
                        _classify(entry, idx, self._roots, files, dirs)
            except OSError:
                # Permission errors or races: treat this root's branch as empty.
                # We do not raise — a partial tree is still useful to walk.
                continue

        for name in sorted(files.keys()):
            entries = tuple(files[name])
            child_rel = name if not rel else os.path.join(rel, name)
            yield MergeResult(child_rel, "file", entries)

        for name in sorted(dirs.keys()):
            entries = tuple(dirs[name])
            child_rel = name if not rel else os.path.join(rel, name)
            yield MergeResult(child_rel, "dir", entries)
            # Only recurse into real directories (not symlinks-to-dirs). The
            # _walk_dir method re-checks islink on the resolved path.
            yield from self._walk_dir(child_rel)


def _classify(
    entry: os.DirEntry,
    idx: int,
    roots: Tuple[str, ...],
    files: Dict[str, List[Optional[str]]],
    dirs: Dict[str, List[Optional[str]]],
) -> None:
    name = entry.name
    # Build the absolute path for this entry lazily; only needed when we record
    # a hit, to keep the common absent-entry path cheap.
    abs_path = os.path.join(roots[idx], name) if False else entry.path

    is_link = entry.is_symlink()
    # is_dir() follows symlinks by default; we want to classify the link target
    # type so that a symlink-to-dir is reported as a dir (but not recursed).
    try:
        is_dir = entry.is_dir()
    except OSError:
        # Broken symlink or permission issue — treat as a file leaf so the path
        # is still surfaced to the caller.
        is_dir = False

    if is_dir and not is_link:
        bucket = dirs
    else:
        # Files, broken symlinks, and symlinks-to-dir (which we treat as dir
        # leaves but do not recurse into) are handled here. Symlinks-to-dir go
        # into the dirs bucket so the caller sees kind='dir' consistent with the
        # link target, but _walk_dir will refuse to recurse because of the
        # islink check.
        bucket = dirs if (is_dir and is_link) else files

    if name not in bucket:
        # Initialise the per-name slot with None for every root, then fill in.
        bucket[name] = [None] * len(roots)
    bucket[name][idx] = abs_path
