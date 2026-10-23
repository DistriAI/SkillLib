"""The host contract: one prompt, one schema, one tool result."""

import json
import tempfile
import unittest
from pathlib import Path

from skilllib.integrate import (
    FIND,
    LOAD,
    VISIT,
    TOOL_NAMES,
    call_tool,
    catalog_ready,
    clear_finder_cache,
    resident_prompt,
    task_instruction,
    tool_specs,
)
from skilllib.sorting.promptpack import DOMAIN_SENTENCES, LOAD_CAP


def _library(root: Path) -> None:
    skill = root / "cache" / "skills" / "demo" / "gsea" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: gsea\n---\n# GSEA\nRun the enrichment procedure.\n", encoding="utf-8")
    card = {
        "id": "demo::gsea",
        "name": "gsea",
        "aliases": ["gsea"],
        "kind": "authentic",
        "description": "Run GSEA on a ranked gene list.",
        "positive_scope": ["ranked gene list"],
        "negative_scope": [],
        "inputs": [],
        "outputs": [],
        "domains": ["science"],
        "preconditions": [],
        "provenance": {"conflict_note": "Prefer the ranked-list method."},
        "body": {"scripts_checked_out": False},
        "sources": [{
            "repository": "demo/gsea",
            "cache_path": "cache/skills/demo/gsea/SKILL.md",
            "present": True,
        }],
        "links": {"requires": [], "alternative": [], "follows": []},
        "applicability": {"gap": ""},
    }
    gap = {
        **card,
        "id": "demo::pdf",
        "name": "pdf",
        "description": "Work with PDF files.",
        "sources": [],
        "provenance": {"conflict_note": ""},
        "applicability": {"gap": "Run visit for demo/pdf before using bundled scripts."},
    }
    catalog = root / "catalog"
    catalog.mkdir()
    (catalog / "routing_cards.jsonl").write_text(
        json.dumps(card) + "\n" + json.dumps(gap) + "\n",
        encoding="utf-8",
    )
    (catalog / "task_families.json").write_text("{}\n", encoding="utf-8")


class IntegrateTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_finder_cache()

    def tearDown(self) -> None:
        clear_finder_cache()

    def test_specs_use_the_canonical_names(self) -> None:
        specs = tool_specs()
        self.assertEqual([spec["name"] for spec in specs], list(TOOL_NAMES))
        find = specs[0]["parameters"]
        self.assertEqual(find["required"], ["task"])
        self.assertIn(LOAD, specs[0]["description"])
        self.assertIn(VISIT, specs[1]["description"])

    def test_resident_prompt_names_the_tools(self) -> None:
        text = resident_prompt()
        self.assertTrue(text.startswith("## Skill library\n"))
        for name, sentence in DOMAIN_SENTENCES:
            self.assertIn(f"- {name}: {sentence}", text)
        self.assertIn(FIND, text)
        self.assertIn(LOAD, text)
        self.assertIn(VISIT, text)
        self.assertIn(f"at most {LOAD_CAP}", text)
        self.assertNotIn("find_skills", text)
        self.assertNotIn("Working set:", text)

    def test_task_instruction_matches_the_load_cap(self) -> None:
        text = task_instruction()
        self.assertIn(FIND, text)
        self.assertIn(LOAD, text)
        self.assertIn(str(LOAD_CAP), text)

    def test_call_tool_find_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(catalog_ready(root))
            missing = call_tool(FIND, {"task": "rank genes"}, root=root)
            self.assertFalse(missing["success"])
            self.assertIn("catalog", missing["error"])
            blank = call_tool(FIND, {"task": "  "}, root=root)
            self.assertFalse(blank["success"])
            _library(root)
            self.assertTrue(catalog_ready(root))
            bad = call_tool(FIND, {"task": "rank genes", "constraints": {"python": True}}, root=root)
            self.assertIn("constraints", bad["error"])
            found = call_tool(FIND, {"task": "Run GSEA on a ranked gene list"}, root=root)
            self.assertTrue(found["success"])
            self.assertEqual(found["cards"][0]["id"], "demo::gsea")
            loaded = call_tool(LOAD, {"skill_id": "demo::gsea"}, root=root)
            self.assertIn("enrichment procedure", loaded["text"])
            gap = call_tool(LOAD, {"skill_id": "demo::pdf"}, root=root)
            self.assertEqual(gap["text"], "")
            self.assertIn("demo/pdf", gap["gap"])
            self.assertIn(VISIT, gap["next"])
            unknown = call_tool(LOAD, {"skill_id": "missing::skill"}, root=root)
            self.assertIn("Unknown skill id", unknown["error"])
            visit = call_tool(VISIT, {"repository": "not a repository"}, root=root)
            self.assertFalse(visit["success"])


if __name__ == "__main__":
    unittest.main()
