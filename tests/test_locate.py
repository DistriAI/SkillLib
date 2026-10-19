import json
import tempfile
import unittest
from pathlib import Path

from skillrepo.sorting.enrich import enrich_cards, is_thin
from skillrepo.sorting.families import build_task_families, heuristic_namer, write_family_pages
from skillrepo.sorting.finder import Finder
from skillrepo.sorting.promptpack import render_prompt


def card(skill_id, name, **overrides):
    base = {
        "schema_version": "1",
        "id": skill_id,
        "name": name,
        "aliases": [name],
        "kind": "separate",
        "description": "",
        "positive_scope": [],
        "negative_scope": [],
        "inputs": [],
        "outputs": [],
        "domains": ["science"],
        "scenario": name,
        "preconditions": [],
        "side_effects": ["read-only"],
        "provenance": {"conflict_note": "", "peer_ids": []},
        "body": {"cache_path": f"cache/skills/acme/skills/{name}/SKILL.md", "present": True, "sha256": name, "scripts_checked_out": False},
        "sources": [
            {
                "role": "leader",
                "repository": "acme/skills",
                "path": f"skills/{name}/SKILL.md",
                "cache_path": f"cache/skills/acme/skills/{name}/SKILL.md",
                "present": True,
            }
        ],
    }
    base.update(overrides)
    return base


class EnrichTests(unittest.TestCase):
    def test_thin_means_a_body_missing_scope_or_io(self) -> None:
        self.assertFalse(is_thin(card("a", "a", body={"present": False})))
        full = card("a", "a", description="Do a thing.", negative_scope=["other"], inputs=["x"])
        self.assertFalse(is_thin(full))
        self.assertTrue(is_thin(card("a", "a", description="Do a thing.", negative_scope=["other"])))

    def test_fill_keeps_existing_text_and_reuses_a_saved_call(self) -> None:
        class Client:
            def __init__(self):
                self.calls = 0

            def complete(self, messages, max_tokens=800):
                self.calls += 1
                return json.dumps(
                    {
                        "description": "Invented description",
                        "positive_scope": ["ranked gene list"],
                        "negative_scope": ["raw counts"],
                        "inputs": ["gene list"],
                        "outputs": ["plot"],
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "cache/skills/acme/skills/gsea/SKILL.md"
            text_path.parent.mkdir(parents=True)
            text_path.write_text("---\nname: gsea\ndescription: Run GSEA.\n---\n", encoding="utf-8")
            target = card(
                "acme::gsea",
                "gsea",
                description="Run GSEA.",
                body={"cache_path": "cache/skills/acme/skills/gsea/SKILL.md", "present": True, "sha256": "abc", "scripts_checked_out": False},
            )
            client = Client()
            summary = enrich_cards([target], root, client, max_cards=5)
            self.assertEqual(target["description"], "Run GSEA.")
            self.assertEqual(target["negative_scope"], ["raw counts"])
            self.assertEqual(summary["enrich"]["filled"], 1)
            target["negative_scope"] = []
            target["inputs"] = []
            target["outputs"] = []
            target["positive_scope"] = []
            enrich_cards([target], root, client, max_cards=5)
            self.assertEqual(client.calls, 1)
            self.assertEqual(target["negative_scope"], ["raw counts"])


class FamilyTests(unittest.TestCase):
    def test_product_skills_skip_families(self) -> None:
        cards = [
            card("g1", "gmail-automation", domains=["agent-tooling"], scenario="gmail-automation"),
            card("g2", "gmail-send", domains=["agent-tooling"], scenario="gmail-send"),
            card("m1", "gsea", domains=["science"], scenario="gene-set-enrichment", description="Ranked genes."),
        ]
        document = build_task_families(cards, name_family=heuristic_namer)
        products = {row["product"]: row["skill_ids"] for row in document["exact"]}
        self.assertEqual(products["gmail"], ["g1", "g2"])
        science = document["domains"][0]
        self.assertEqual(science["name"], "science")
        self.assertEqual(science["families"][0]["skill_ids"], ["m1"])
        with tempfile.TemporaryDirectory() as tmp:
            page = write_family_pages(document, Path(tmp))
            self.assertIn("science", page.read_text(encoding="utf-8"))


class FinderTests(unittest.TestCase):
    def test_exact_product_and_negative_scope(self) -> None:
        gsea = card(
            "combined::gsea",
            "gsea",
            description="Test a ranked gene list with GSEA.",
            positive_scope=["Running GSEA on a ranked gene list"],
            negative_scope=["Differential expression on raw expression matrices"],
            kind="combined",
        )
        gmail = card(
            "combined::gmail-automation",
            "gmail-automation",
            domains=["agent-tooling"],
            scenario="gmail-automation",
            description="Automate Gmail send and reply.",
        )
        families = {
            "exact": [{"product": "gmail", "skill_ids": [gmail["id"]]}],
            "domains": [
                {
                    "name": "science",
                    "families": [
                        {
                            "name": "gene-sets",
                            "definition": "Gene set enrichment on ranked lists.",
                            "skill_ids": [gsea["id"]],
                        }
                    ],
                }
            ],
        }
        finder = Finder([gsea, gmail], families)
        ranked = finder.find("Run GSEA on a ranked gene list", domain="science")
        self.assertEqual(ranked[0]["name"], "gsea")
        self.assertEqual(ranked[0]["family"], "gene-sets")
        rejected = finder.find("Run differential expression on a raw expression matrix", domain="science")
        self.assertNotIn("gsea", [item["name"] for item in rejected])
        mailed = finder.find("Automate Gmail to send a reply")
        self.assertEqual(mailed[0]["name"], "gmail-automation")
        self.assertEqual(finder.find("Fold an origami crane"), [])

    def test_load_reports_missing_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "cache/skills/acme/skills/gsea/SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("run gsea\n", encoding="utf-8")
            target = card("acme::gsea", "gsea")
            loaded = Finder([target]).load(root, "acme::gsea")
        self.assertIn("run gsea", loaded["text"])
        self.assertFalse(loaded["scripts_checked_out"])


class PromptTests(unittest.TestCase):
    def test_working_set_uses_the_description(self) -> None:
        target = card("combined::gsea", "gsea", description="Run GSEA on a ranked gene list.")
        text = render_prompt({target["id"]: target}, [target["id"]])
        self.assertIn("science:", text)
        self.assertIn("find_skills", text)
        self.assertIn("load_skill", text)
        self.assertIn("- gsea: Run GSEA on a ranked gene list.", text)
        self.assertIn("at most 3", text)


if __name__ == "__main__":
    unittest.main()
