"""Find and load read skilllib.json before downloading a skill."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skilllib.sorting.download import materialize_skill_file
from skilllib.sorting.links import CheckoutIndex


class ConfiguredCacheTests(unittest.TestCase):
    def test_a_file_in_the_configured_cache_is_not_downloaded(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-root-"))
        store = Path(tempfile.mkdtemp(prefix="skilllib-store-"))
        skill = store / "skills" / "acme" / "skills" / "skills" / "gsea" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("name: gsea\n", encoding="utf-8")
        (root / "skilllib.json").write_text(json.dumps({"cache": str(store)}), encoding="utf-8")

        def fetch(*_args, **_kwargs):
            raise AssertionError("download was attempted")

        found = materialize_skill_file(root, "acme/skills", "skills/gsea/SKILL.md", fetch=fetch)
        self.assertEqual(found, skill)
        self.assertFalse((root / "cache").exists())

    def test_a_relative_cache_path_is_resolved_from_the_checkout(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-rel-"))
        skill = root / "elsewhere" / "repos" / "acme" / "skills" / "skills" / "gsea" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("name: gsea\n", encoding="utf-8")
        (root / "skilllib.json").write_text(json.dumps({"cache": "elsewhere"}), encoding="utf-8")
        found = materialize_skill_file(
            root,
            "acme/skills",
            "skills/gsea/SKILL.md",
            fetch=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download was attempted")),
        )
        self.assertEqual(found, skill.resolve())

    def test_a_bundled_file_in_the_configured_checkout_counts_as_present(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-bundle-"))
        store = Path(tempfile.mkdtemp(prefix="skilllib-bundle-store-"))
        script = store / "repos" / "acme" / "skills" / "skills" / "gsea" / "scripts" / "run.py"
        script.parent.mkdir(parents=True)
        script.write_text("print(1)\n", encoding="utf-8")
        (root / "skilllib.json").write_text(json.dumps({"cache": str(store)}), encoding="utf-8")
        index = CheckoutIndex(root)
        self.assertTrue(index.has("acme/skills", "skills/gsea/SKILL.md", "scripts/run.py"))

    def test_without_a_config_file_the_download_still_runs(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-plain-"))
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return b"name: gsea\n"

        found = materialize_skill_file(root, "acme/skills", "skills/gsea/SKILL.md", fetch=fetch)
        self.assertEqual(len(calls), 1)
        self.assertTrue(found is not None and found.is_file())
        self.assertTrue(str(found).startswith(str(root / "cache" / "skills")))


if __name__ == "__main__":
    unittest.main()
