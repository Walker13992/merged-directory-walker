import os
import shutil
import tempfile
import unittest

from merged_directory_walker import MergedDirectoryWalker, MergeResult


class _TempTrees:
    """Helper that builds a set of root directories from a spec dict."""

    def __init__(self, specs):
        # specs: dict of root_label -> dict of rel_path -> 'file'|'dir'
        self.tmp = tempfile.mkdtemp(prefix="mdw_test_")
        self.roots = []
        for label in sorted(specs.keys()):
            root = os.path.join(self.tmp, label)
            os.makedirs(root, exist_ok=True)
            self.roots.append(root)
            for rel, kind in specs[label].items():
                full = os.path.join(root, rel)
                if kind == "dir":
                    os.makedirs(full, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "w") as f:
                        f.write("")

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMergedDirectoryWalker(unittest.TestCase):
    def setUp(self):
        self._trees = []

    def tearDown(self):
        for t in self._trees:
            t.close()

    def _make(self, specs):
        t = _TempTrees(specs)
        self._trees.append(t)
        return t

    def _walk_all(self, walker):
        return list(walker.walk())

    def test_empty_roots_list_yields_nothing(self):
        walker = MergedDirectoryWalker([])
        self.assertEqual(self._walk_all(walker), [])

    def test_single_root_single_file(self):
        t = self._make({"a": {"file.txt": "file"}})
        walker = MergedDirectoryWalker([t.roots[0]])
        results = self._walk_all(walker)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.relative_path, "file.txt")
        self.assertEqual(r.kind, "file")
        self.assertEqual(len(r.entries), 1)
        self.assertTrue(r.entries[0].endswith(os.path.join("a", "file.txt")))

    def test_single_root_dir_recurses(self):
        t = self._make({"a": {"d": "dir", "d/inner.txt": "file"}})
        walker = MergedDirectoryWalker([t.roots[0]])
        results = self._walk_all(walker)
        paths = [(r.relative_path, r.kind) for r in results]
        self.assertIn(("d", "dir"), paths)
        self.assertIn((os.path.join("d", "inner.txt"), "file"), paths)
        # Directory entry comes before its contents within the same level.
        d_idx = paths.index(("d", "dir"))
        inner_idx = paths.index((os.path.join("d", "inner.txt"), "file"))
        self.assertLess(d_idx, inner_idx)

    def test_two_roots_disjoint_files(self):
        t = self._make({
            "a": {"only_a.txt": "file"},
            "b": {"only_b.txt": "file"},
        })
        walker = MergedDirectoryWalker([t.roots[0], t.roots[1]])
        results = self._walk_all(walker)
        by_path = {r.relative_path: r for r in results}
        self.assertIn("only_a.txt", by_path)
        self.assertIn("only_b.txt", by_path)
        a = by_path["only_a.txt"]
        b = by_path["only_b.txt"]
        self.assertIsNotNone(a.entries[0])
        self.assertIsNone(a.entries[1])
        self.assertIsNone(b.entries[0])
        self.assertIsNotNone(b.entries[1])

    def test_two_roots_shared_file_both_present(self):
        t = self._make({
            "a": {"shared.txt": "file"},
            "b": {"shared.txt": "file"},
        })
        walker = MergedDirectoryWalker([t.roots[0], t.roots[1]])
        results = self._walk_all(walker)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.relative_path, "shared.txt")
        self.assertEqual(r.kind, "file")
        self.assertIsNotNone(r.entries[0])
        self.assertIsNotNone(r.entries[1])

    def test_missing_root_is_treated_as_empty(self):
        t = self._make({"a": {"x.txt": "file"}})
        missing = os.path.join(t.tmp, "does_not_exist")
        walker = MergedDirectoryWalker([t.roots[0], missing])
        results = self._walk_all(walker)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.relative_path, "x.txt")
        self.assertIsNotNone(r.entries[0])
        self.assertIsNone(r.entries[1])

    def test_file_in_one_root_dir_in_other_yields_separate_groups(self):
        t = self._make({
            "a": {"conflict": "file"},
            "b": {"conflict": "dir", "conflict/child.txt": "file"},
        })
        walker = MergedDirectoryWalker([t.roots[0], t.roots[1]])
        results = self._walk_all(walker)
        kinds = {(r.relative_path, r.kind) for r in results}
        # Both a file group and a dir group are emitted for the same path.
        self.assertIn(("conflict", "file"), kinds)
        self.assertIn(("conflict", "dir"), kinds)
        # The dir group is recursed into.
        self.assertIn((os.path.join("conflict", "child.txt"), "file"), kinds)

    def test_files_before_dirs_within_level(self):
        t = self._make({
            "a": {
                "z_dir": "dir",
                "z_dir/inner.txt": "file",
                "a_file.txt": "file",
                "m_file.txt": "file",
            },
        })
        walker = MergedDirectoryWalker([t.roots[0]])
        results = self._walk_all(walker)
        top = [r for r in results if os.path.dirname(r.relative_path) == ""]
        names = [r.relative_path for r in top]
        # Files first, then dirs; each group sorted.
        self.assertEqual(names, ["a_file.txt", "m_file.txt", "z_dir"])

    def test_nested_directories_three_roots(self):
        t = self._make({
            "a": {"d": "dir", "d/e": "dir", "d/e/a_only.txt": "file"},
            "b": {"d": "dir", "d/e": "dir", "d/e/shared.txt": "file"},
            "c": {"d": "dir", "d/e": "dir", "d/e/c_only.txt": "file"},
        })
        walker = MergedDirectoryWalker(list(t.roots))
        results = self._walk_all(walker)
        by_path = {r.relative_path: r for r in results}
        deep = os.path.join("d", "e", "shared.txt")
        self.assertIn(deep, by_path)
        r = by_path[deep]
        self.assertIsNone(r.entries[0])
        self.assertIsNotNone(r.entries[1])
        self.assertIsNone(r.entries[2])
        # All three roots have the d/e directory.
        de = by_path[os.path.join("d", "e")]
        self.assertEqual(r.kind, "file")
        self.assertEqual(len(de.entries), 3)
        self.assertTrue(all(e is not None for e in de.entries))

    def test_results_are_merge_result_instances(self):
        t = self._make({"a": {"x.txt": "file"}})
        walker = MergedDirectoryWalker([t.roots[0]])
        results = self._walk_all(walker)
        self.assertIsInstance(results[0], MergeResult)
        self.assertEqual(results[0].entries.__class__.__name__, "tuple")

    def test_roots_property_returns_normalised_tuple(self):
        t = self._make({"a": {}})
        walker = MergedDirectoryWalker([t.roots[0] + os.sep])
        self.assertEqual(walker.roots, (t.roots[0],))
        self.assertIsInstance(walker.roots, tuple)

    def test_roots_must_be_list_or_tuple(self):
        with self.assertRaises(TypeError):
            MergedDirectoryWalker("not/a/list")

    def test_symlink_to_dir_is_not_recursed(self):
        t = self._make({
            "a": {"real": "dir", "real/inner.txt": "file", "link": "dir"},
        })
        # Replace the 'link' dir entry with an actual symlink to 'real'.
        link_path = os.path.join(t.roots[0], "link")
        os.rmdir(link_path)
        os.symlink(os.path.join(t.roots[0], "real"), link_path)
        walker = MergedDirectoryWalker([t.roots[0]])
        results = self._walk_all(walker)
        paths = {r.relative_path for r in results}
        self.assertIn("link", paths)
        # We must NOT have recursed into the symlink — link/inner.txt absent.
        self.assertNotIn(os.path.join("link", "inner.txt"), paths)
        self.assertIn(os.path.join("real", "inner.txt"), paths)

    def test_deterministic_order_across_runs(self):
        t = self._make({
            "a": {
                "b_dir": "dir",
                "b_dir/c.txt": "file",
                "b_dir/a.txt": "file",
                "z.txt": "file",
                "a.txt": "file",
            },
        })
        walker = MergedDirectoryWalker([t.roots[0]])
        first = [r.relative_path for r in walker.walk()]
        second = [r.relative_path for r in walker.walk()]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
