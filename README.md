# Merged Directory Walker

Walks multiple directory trees simultaneously and yields grouped tuples of matching
paths from each root. For every relative path that exists in at least one root you
get a `MergeResult` with an entry per root — `None` where the path is absent.

## Usage

```python
from merged_directory_walker import MergedDirectoryWalker

walker = MergedDirectoryWalker(["/srv/baseline", "/srv/overlay"])
for result in walker.walk():
    print(result.relative_path, result.kind, result.entries)
    # result.entries is a tuple aligned with the roots list; None marks a missing root
```

Exported names: `MergedDirectoryWalker`, `MergeResult`.

`MergeResult` fields:

- `relative_path` — path relative to each root, using `os.sep`.
- `kind` — `'file'` or `'dir'`.
- `entries` — tuple aligned with `roots`; `None` where the path is absent.

## Why this exists

When you merge trees (overlay filesystems, config layering, sync tooling) you
need to see, for each logical path, which roots contribute to it. Doing this
with `os.walk` per root and then joining the streams is fiddly and easy to get
wrong for absent entries. This library does the join for you.

The trade-off: it buffers per-directory entry lists in memory rather than
streaming one root at a time. For trees with millions of entries in a single
directory this is heavier than a streaming join, but it makes the grouping logic
straightforward and correct. Most merged-tree workloads do not have million-entry
flat directories.

## Ordering

Within each directory level, file entries are yielded before subdirectory
entries, and each group is sorted by name. Subdirectories are recursed into
immediately after their own entry is yielded (depth-first across directories,
breadth-first within a level). This makes output reproducible across platforms.

## Awkward edges

- **File vs directory conflict.** If `foo` is a file in root A and a directory
  in root B, the walker yields *two* `MergeResult` objects for `foo` — one with
  `kind='file'` and one with `kind='dir'`. It does not pick a winner; the caller
  decides how to handle the conflict.

- **Symbolic links are not followed.** A symlink to a directory is reported as
  `kind='dir'` but is not recursed into, to prevent accidental traversal
  outside the merged tree.

- **Missing or unreadable roots are treated as empty.** Every entry from such
  a root will be `None`. This is deliberate so a root that may not exist yet
  (e.g. a baseline) does not crash the walk.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

## Limitations

Values are coerced to floats, so very large integers lose precision. If you need
exact integer aggregates over a window, this is the wrong tool.

