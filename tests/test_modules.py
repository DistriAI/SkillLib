import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from skilllib.catalog import Catalog
from skilllib.cli import main
from skilllib.content import FileListing
from skilllib.index import update_index
from skilllib.models import RepoMeta
from skilllib.search import add_repository, search_repositories
from skilllib.sorting.build import INDEX_PATH, build_skill_index, save_skill_index
from skilllib.sorting.cards import build_routing_cards, load_routing_cards, write_routing_cards
from skilllib.sorting.families import FAMILIES_PATH, assign_to_families, build_task_families, heuristic_namer, write_task_families
from skilllib.sorting.links import link_cards


def meta(full_name: str, description: str, stars: int = 2000) -> RepoMeta:
    _owner, name = full_name.split("/")
    return RepoMeta(
        full_name=full_name,
        name=name,
        stars=stars,
        description=description,
        html_url=f"https://github.com/{full_name}",
        default_branch="main",
        license="MIT",
    )


def skill_paths(count: int = 5) -> list[str]:
    return [f"skills/area-{index}/SKILL.md" for index in range(count)]


class ListingFetcher:
    def __init__(self, readmes: dict[str, str], listed: dict[str, list[str]]):
        self.readmes_by_name = readmes
        self.listed = listed

    def readmes(self, metas, log=None):
        return {item.full_name: self.readmes_by_name.get(item.full_name, "") for item in metas}

    def listings(self, metas, log=None):
        found = {}
        for item in metas:
            paths = self.listed.get(item.full_name)
            if paths is not None:
                found[item.full_name] = FileListing(paths, len(paths), "test")
        return found

    def frontmatter_state(self, item, paths):
        return "ok"


class MergeTests(unittest.TestCase):
    def test_merge_keeps_repositories_from_the_earlier_search(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-merge-"))
        fetcher = ListingFetcher(
            {"anthropics/skills": "Skills for Claude.", "acme/team-skills": "Skills for Claude."},
            {"anthropics/skills": skill_paths(), "acme/team-skills": skill_paths()},
        )

        class First:
            def search(self, queries):
                return [meta("anthropics/skills", "Public repository for Agent Skills", stars=180000)]

        class Second:
            def search(self, queries):
                return [meta("acme/team-skills", "Claude skills for a team", stars=1500)]

        search_repositories(1000, root=root, client=First(), fetcher=fetcher, log=lambda _message: None)
        catalog = search_repositories(
            500,
            root=root,
            client=Second(),
            fetcher=fetcher,
            log=lambda _message: None,
            merge=True,
        )
        names = [item.full_name for item in catalog.skill_repositories()]
        self.assertEqual(names, ["anthropics/skills", "acme/team-skills"])
        self.assertEqual(catalog.min_stars, 500)
        report = json.loads((root / "catalog" / "search_report.json").read_text(encoding="utf-8"))
        self.assertTrue(report["merged"])
        self.assertEqual(report["added"], ["acme/team-skills"])

    def test_add_merges_one_repository_and_keeps_the_star_cutoff(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-add-"))
        fetcher = ListingFetcher(
            {"anthropics/skills": "Skills for Claude.", "acme/team-skills": "Skills for Claude.", "acme/empty": ""},
            {
                "anthropics/skills": skill_paths(),
                "acme/team-skills": skill_paths(),
                "acme/empty": [],
            },
        )

        class Client:
            def __init__(self, results):
                self.results = results

            def search(self, queries):
                return self.results

            def repository(self, full_name):
                return meta(full_name, "Skills for Claude.", stars=40)

        search_repositories(
            1000,
            root=root,
            client=Client([meta("anthropics/skills", "Public repository for Agent Skills", stars=180000)]),
            fetcher=fetcher,
            log=lambda _message: None,
        )
        catalog = add_repository("acme/team-skills", root=root, client=Client([]), fetcher=fetcher, log=lambda _message: None)
        names = [item.full_name for item in catalog.skill_repositories()]
        self.assertEqual(names, ["anthropics/skills", "acme/team-skills"])
        self.assertEqual(Catalog.load(root / "catalog" / "skill_repositories.json").min_stars, 1000)
        with self.assertRaises(LookupError):
            add_repository("acme/empty", root=root, client=Client([]), fetcher=fetcher, log=lambda _message: None)
        self.assertIsNone(Catalog.load(root / "catalog" / "skill_repositories.json").get("acme/empty"))


def repository(full_name: str, skills: list[tuple[str, str]]) -> dict:
    return {
        "full_name": full_name,
        "stars": 5000,
        "description": "",
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "topics": [],
        "license": "MIT",
        "archived": False,
        "compatibilities": ["anthropic"],
        "skill_count": len(skills),
        "domains": [{"name": "skills", "skills": [{"name": name, "path": path} for name, path in skills]}],
    }


def write_catalog(root: Path, repositories: list[dict]) -> dict:
    document = {
        "min_stars": 1000,
        "min_skills": 5,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "queries": [],
        "anthropic_compatible": {"collections": [], "skill_repositories": repositories},
        "openai_compatible": {"collections": [], "skill_repositories": []},
    }
    path = root / "catalog" / "skill_repositories.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def finish_products(root: Path, document: dict) -> None:
    index = build_skill_index(document, mode="heuristic")
    save_skill_index(index, root / INDEX_PATH)
    cards, report = build_routing_cards(index, root)
    write_routing_cards(cards, report, root)
    write_task_families(build_task_families(cards, name_family=heuristic_namer), root)


class UpdateTests(unittest.TestCase):
    def test_seed_is_idle_and_a_new_skill_is_added_beside_the_old_one(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-update-"))
        original = repository("acme/pack", [("notes", "skills/notes/SKILL.md")])
        document = write_catalog(root, [original])
        text = root / "cache" / "skills" / "acme" / "pack" / "skills" / "notes" / "SKILL.md"
        text.parent.mkdir(parents=True)
        text.write_text("---\nname: notes\ndescription: Take notes.\n---\n", encoding="utf-8")
        finish_products(root, document)
        quiet = lambda _message: None
        seeded = update_index(root, judge="heuristic", progress=quiet)
        self.assertEqual(seeded["action"], "seeded")
        self.assertTrue((root / "catalog" / "index_state.json").is_file())
        idle = update_index(root, judge="heuristic", progress=quiet)
        self.assertEqual(idle["action"], "idle")

        text.write_text("---\nname: notes\ndescription: Take notes carefully.\n---\n", encoding="utf-8")
        changed = update_index(root, judge="heuristic", progress=quiet)
        self.assertEqual(changed["action"], "updated")
        self.assertEqual(changed["changed"], 1)
        self.assertEqual(changed["added"], 0)

        write_catalog(root, [original, repository("acme/more", [("ledger", "skills/ledger/SKILL.md")])])
        added = update_index(root, judge="heuristic", progress=quiet)
        self.assertEqual(added["action"], "updated")
        self.assertEqual(added["added"], 1)
        names = {card["name"] for card in load_routing_cards(root / "catalog" / "routing_cards.jsonl")}
        self.assertEqual(names, {"notes", "ledger"})
        again = update_index(root, judge="heuristic", progress=quiet)
        self.assertEqual(again["action"], "idle")


class FamilyAssignTests(unittest.TestCase):
    def test_a_new_skill_joins_a_family_without_renaming_it(self) -> None:
        cards = [
            {"id": "gene", "name": "gene-set", "domains": ["science"], "scenario": "gene-set"},
            {"id": "plot", "name": "gene-set-plot", "domains": ["science"], "scenario": "gene-set-plot"},
        ]
        document = build_task_families(cards, name_family=heuristic_namer)
        before = {
            family["name"]: list(family["skill_ids"])
            for domain in document["domains"]
            for family in domain["families"]
        }
        extra = {"id": "report", "name": "gene-set-report", "domains": ["science"], "scenario": "gene-set-report"}
        updated, assigned = assign_to_families(document, cards + [extra])
        self.assertEqual(assigned, 1)
        after = {
            family["name"]: list(family["skill_ids"])
            for domain in updated["domains"]
            for family in domain["families"]
        }
        self.assertEqual(set(before), set(after) - {"general"} if "general" in after and "general" not in before else set(before))
        self.assertIn("report", after["gene-set"])
        self.assertEqual(after["gene-set-plot"], before["gene-set-plot"])

    def test_overflow_joins_general_and_leaves_the_other_families(self) -> None:
        families = []
        cards = []
        for index in range(30):
            skill_id = f"id-{index}"
            families.append(
                {
                    "id": f"design/fam-{index:02d}",
                    "name": f"fam-{index:02d}",
                    "definition": f"Bucket {index:02d} only.",
                    "exemplar_tasks": ["a", "b"],
                    "discriminator": "d",
                    "judge": "heuristic",
                    "skill_ids": [skill_id],
                }
            )
            cards.append(
                {
                    "id": skill_id,
                    "name": f"skill-{index}",
                    "domains": ["design"],
                    "scenario": f"bucket-{index:02d}",
                }
            )
        cards.append({"id": "origami", "name": "origami", "domains": ["design"], "scenario": "paper-folding"})
        document = {"exact": [], "domains": [{"name": "design", "families": families}]}
        updated, assigned = assign_to_families(document, cards)
        self.assertEqual(assigned, 1)
        names = [family["name"] for family in updated["domains"][0]["families"]]
        self.assertEqual(names[:30], [f"fam-{index:02d}" for index in range(30)])
        general = next(family for family in updated["domains"][0]["families"] if family["name"] == "general")
        self.assertEqual(general["skill_ids"], ["origami"])
        self.assertEqual(updated["domains"][0]["families"][0]["skill_ids"], ["id-0"])


class LinkRescanTests(unittest.TestCase):
    def test_only_a_file_that_mentions_the_new_name_is_parsed_again(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-link-"))
        web = root / "cache" / "skills" / "acme" / "skills" / "web" / "SKILL.md"
        other = root / "cache" / "skills" / "acme" / "skills" / "other" / "SKILL.md"
        report = root / "cache" / "skills" / "acme" / "skills" / "report-writing" / "SKILL.md"
        for path, text in (
            (web, "Use the `report-writing` skill before filing.\n"),
            (other, "Count inventory.\n"),
            (report, "Write the report.\n"),
        ):
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
        first = link_cards([_card("web"), _card("other")], root, progress=lambda _message: None)
        self.assertEqual(first["links"]["rescanned"], 0)
        self.assertEqual(_card_links(root, "web")["requires"], [])
        second = link_cards(
            [_card("web"), _card("other"), _card("report-writing")],
            root,
            progress=lambda _message: None,
            rescan_names={"report-writing"},
        )
        self.assertEqual(second["links"]["rescanned"], 1)
        self.assertEqual(_card_links(root, "web")["requires"], ["report-writing"])
        self.assertEqual(_card_links(root, "other")["requires"], [])


def _card(name: str) -> dict:
    relative = f"cache/skills/acme/skills/{name}/SKILL.md"
    return {
        "schema_version": "1",
        "id": name,
        "name": name,
        "kind": "separate",
        "description": "",
        "positive_scope": [],
        "negative_scope": [],
        "inputs": [],
        "outputs": [],
        "domains": ["general"],
        "scenario": name,
        "preconditions": [],
        "side_effects": ["read-only"],
        "provenance": {},
        "body": {"cache_path": relative, "present": True, "sha256": name, "scripts_checked_out": False},
        "sources": [
            {
                "role": "leader",
                "repository": "acme/skills",
                "path": f"skills/{name}/SKILL.md",
                "cache_path": relative,
                "present": True,
            }
        ],
    }


def _card_links(root: Path, name: str) -> dict:
    cards = load_routing_cards(root / "catalog" / "routing_cards.jsonl")
    found = next(card for card in cards if card["name"] == name)
    return found["links"]


class HelpTests(unittest.TestCase):
    def test_help_lists_the_four_modules(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as caught:
                main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        text = buffer.getvalue()
        self.assertIn("add", text)
        self.assertIn("update", text)
        self.assertIn("modules:", text)
        search_help = io.StringIO()
        with redirect_stdout(search_help):
            with self.assertRaises(SystemExit) as search_exit:
                main(["search", "--help"])
        self.assertEqual(search_exit.exception.code, 0)
        self.assertIn("--merge", search_help.getvalue())


class InterfaceTests(unittest.TestCase):
    def test_find_and_prompt_read_the_cards_update_wrote(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-agent-"))
        document = write_catalog(root, [repository("acme/pack", [("notes", "skills/notes/SKILL.md")])])
        finish_products(root, document)
        update_index(root, judge="heuristic", progress=lambda _message: None)
        from skilllib import find_skills, render_prompt

        found = find_skills("take notes", root=root)
        self.assertTrue(found["cards"])
        self.assertIn("notes", found["cards"][0]["name"])
        prompt = render_prompt(root, working_set=[found["cards"][0]["id"]])
        self.assertIn("find_skills", prompt)
        self.assertIn("notes", prompt)
        self.assertTrue((root / FAMILIES_PATH).is_file())


if __name__ == "__main__":
    unittest.main()
