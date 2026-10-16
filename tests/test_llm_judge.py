import json
import tempfile
import unittest
from pathlib import Path

from skillrepo.sorting.build import build_skill_index
from skillrepo.sorting.llm_judge import judge_contents
from skillrepo.sorting.records import SkillOccurrence, normalize_skill_name
from skillrepo.sorting.retrieve import SkillBody, SkillRetriever
from skillrepo.sorting.download import download_shortlist, skill_cache_path
from skillrepo.sorting.screen import SCREEN_PATH, load_screened_clusters, save_skill_screen, screen_catalog, screen_cluster


def occurrence(repository: str, name: str, path: str, **extra) -> SkillOccurrence:
    return SkillOccurrence(
        occurrence_id=f"{repository}::{path}",
        name=name,
        normalized_name=normalize_skill_name(name),
        path=path,
        folder=extra.get("folder", "skills"),
        repository=repository,
        owner=repository.split("/")[0].lower(),
        stars=extra.get("stars", 2000),
        skill_count=extra.get("skill_count", 20),
        description=extra.get("description", ""),
        topics=(),
        compatibilities=("anthropic",),
        license="MIT",
        archived=False,
        html_url=f"https://github.com/{repository}",
        default_branch="main",
    )


def catalog_with(*repositories: dict) -> dict:
    return {"anthropic_compatible": {"skill_repositories": list(repositories)}, "openai_compatible": {"skill_repositories": []}}


def repository(full_name: str, skills: list[tuple[str, str, str]], **extra) -> dict:
    domains: dict[str, list[dict]] = {}
    for folder, name, path in skills:
        domains.setdefault(folder, []).append({"name": name, "path": path})
    return {
        "full_name": full_name,
        "stars": extra.get("stars", 5000),
        "description": extra.get("description", ""),
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "topics": [],
        "license": "MIT",
        "archived": False,
        "compatibilities": ["anthropic"],
        "skill_count": extra.get("skill_count", len(skills)),
        "domains": [{"name": folder, "skills": items} for folder, items in domains.items()],
    }


class BodyReader:
    def __init__(self, texts: dict[str, str]):
        self.texts = texts
        self.counts = {"submodule": 0, "raw": len(texts), "missing": 0}

    def read(self, occurrence: SkillOccurrence) -> SkillBody:
        return SkillBody(occurrence.occurrence_id, self.texts.get(occurrence.occurrence_id, ""), "raw")


class ScreenTests(unittest.TestCase):
    def test_fixture_copy_is_not_sent_to_the_model(self) -> None:
        cluster = screen_cluster(
            [
                occurrence("anthropics/skills", "pdf", "skills/pdf/SKILL.md", stars=180000, skill_count=19),
                occurrence("acme/pack", "pdf", "skills/pdf/SKILL.md", stars=3000),
                occurrence("other/pack", "pdf", "tests/pdf/SKILL.md", stars=2500),
            ]
        )
        self.assertTrue(cluster.needs_llm)
        self.assertEqual(
            {copy.occurrence.repository for copy in cluster.content_copies},
            {"anthropics/skills", "acme/pack"},
        )

    def test_saved_screen_reloads_the_shortlist(self) -> None:
        catalog = catalog_with(
            repository("anthropics/skills", [("skills", "pdf", "skills/pdf/SKILL.md")], stars=180000, skill_count=19),
            repository("acme/pack", [("skills", "pdf", "skills/pdf/SKILL.md")], stars=3000),
            repository("solo/pack", [("skills", "notes", "skills/notes/SKILL.md")], stars=2000),
        )
        document = screen_catalog(catalog)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill_screen.json"
            save_skill_screen(document, path)
            loaded = load_screened_clusters(path)
        by_name = {cluster.name: cluster for cluster in loaded}
        self.assertEqual(document["summary"]["content_clusters"], 1)
        self.assertEqual(document["summary"]["downloads"], 2)
        self.assertEqual(
            {item["repository"] for item in document["downloads"]},
            {"anthropics/skills", "acme/pack"},
        )
        self.assertTrue(by_name["pdf"].needs_llm)
        self.assertFalse(by_name["notes"].needs_llm)
        self.assertEqual(
            {copy.occurrence.repository for copy in by_name["pdf"].content_copies},
            {"anthropics/skills", "acme/pack"},
        )

    def test_harness_copies_in_one_repository_skip_the_model(self) -> None:
        cluster = screen_cluster(
            [
                occurrence("acme/pack", "notes", "skills/notes/SKILL.md"),
                occurrence("acme/pack", "notes", "skills-codex/notes/SKILL.md"),
            ]
        )
        self.assertFalse(cluster.needs_llm)


class DownloadTests(unittest.TestCase):
    def test_download_skips_files_already_stored(self) -> None:
        catalog = catalog_with(
            repository("acme/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
            repository("other/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
        )
        document = screen_catalog(catalog)
        calls: list[str] = []

        def fetch(url: str, timeout: float) -> bytes:
            calls.append(url)
            return b"# notes\nKeep a log.\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_skill_screen(document, root / SCREEN_PATH)
            present = skill_cache_path(root, "acme/pack", "skills/notes/SKILL.md")
            present.parent.mkdir(parents=True)
            present.write_text("# notes\nalready here\n")
            first = download_shortlist(root, fetch=fetch, workers=2)
            second = download_shortlist(root, fetch=fetch, workers=2)
        self.assertEqual(first["downloaded"], 1)
        self.assertEqual(first["cached"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(second["cached"], 2)
        self.assertEqual(len(calls), 1)

    def test_retry_fetches_only_the_failed_file(self) -> None:
        catalog = catalog_with(
            repository("acme/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
            repository("other/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
        )
        document = screen_catalog(catalog)
        calls: list[str] = []
        fail = True

        def fetch(url: str, timeout: float) -> bytes:
            calls.append(url)
            if fail and "other/pack" in url:
                raise TimeoutError("timed out")
            return b"# notes\nKeep a log.\n"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_skill_screen(document, root / SCREEN_PATH)
            present = skill_cache_path(root, "acme/pack", "skills/notes/SKILL.md")
            present.parent.mkdir(parents=True)
            present.write_text("# notes\nalready here\n")
            first = download_shortlist(root, fetch=fetch, workers=1)
            fail = False
            calls.clear()
            second = download_shortlist(root, fetch=fetch, workers=1)
            kept = present.read_text()
        self.assertEqual(first["failed"], 1)
        self.assertEqual(len(first["failures"]), 1)
        self.assertTrue(all("other/pack" in url for url in calls))
        self.assertEqual(second["downloaded"], 1)
        self.assertEqual(second["cached"], 1)
        self.assertEqual(kept, "# notes\nalready here\n")


class ContentJudgeTests(unittest.TestCase):
    def test_identical_text_does_not_call_the_model(self) -> None:
        copies = [
            _screened(occurrence("anthropics/skills", "pdf", "skills/pdf/SKILL.md", stars=180000, skill_count=19), 0.8),
            _screened(occurrence("acme/pack", "pdf", "skills/pdf/SKILL.md"), 0.4),
        ]

        def complete(messages, max_tokens=100):
            raise AssertionError("identical skill text should not be sent to the model")

        decision = judge_contents("pdf", [(copies[0], "# pdf\nExtract text."), (copies[1], "# pdf\nExtract text.")], complete=complete)
        self.assertEqual(decision["judge"], "identical-content")
        self.assertEqual(decision["winner_occurrence_id"], copies[0].occurrence.occurrence_id)

    def test_model_can_reject_the_metadata_leader(self) -> None:
        official = occurrence("anthropics/skills", "pdf", "skills/pdf/SKILL.md", stars=180000, skill_count=19)
        community = occurrence("acme/pack", "pdf", "skills/pdf/SKILL.md", stars=3000)
        texts = {
            official.occurrence_id: "---\nname: pdf\n---\n# pdf\nSee the other repository.",
            community.occurrence_id: "---\nname: pdf\n---\n# pdf\nExtract text with pdftotext, then return the pages.",
        }
        winner = community.occurrence_id

        class Client:
            def complete(self, messages, max_tokens=100):
                return json.dumps(
                    {
                        "decision": "authentic",
                        "winner_occurrence_id": winner,
                        "domain": "documents",
                        "scenario": "pdf-text",
                        "rationale": "The community copy is the procedure. The official file only points elsewhere.",
                        "combined_instruction": "",
                        "notes": [],
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            document = build_skill_index(
                catalog_with(
                    repository("anthropics/skills", [("skills", "pdf", "skills/pdf/SKILL.md")], stars=180000, skill_count=19),
                    repository("acme/pack", [("skills", "pdf", "skills/pdf/SKILL.md")], stars=3000),
                ),
                mode="llm",
                root=Path(tmp),
                client=Client(),
                retrieve=BodyReader(texts),
                classify=lambda items: {},
            )
        skill = document["domains"][0]["scenarios"][0]["skills"][0]
        self.assertEqual(skill["kind"], "authentic")
        self.assertEqual(skill["judge"], "llm")
        self.assertEqual(skill["leader"]["repository"], "acme/pack")
        self.assertEqual(document["domains"][0]["name"], "documents")
        self.assertEqual(document["domains"][0]["scenarios"][0]["name"], "pdf-text")

    def test_model_writes_the_combined_skill(self) -> None:
        left = occurrence("acme/pack", "notes", "skills/notes/SKILL.md", stars=4000)
        right = occurrence("other/pack", "notes", "skills/notes/SKILL.md", stars=3900)
        texts = {
            left.occurrence_id: "# notes\nSave notes as Markdown.",
            right.occurrence_id: "# notes\nSave notes as HTML only.",
        }

        class Client:
            def complete(self, messages, max_tokens=100):
                return json.dumps(
                    {
                        "decision": "combine",
                        "winner_occurrence_id": None,
                        "domain": "documents",
                        "scenario": "note-taking",
                        "rationale": "The copies disagree on the file format.",
                        "combined_instruction": "---\nname: notes\n---\n# notes\n\n## Conflicts\n- acme/pack saves Markdown. other/pack saves HTML.\n",
                        "notes": [],
                    }
                )

        with tempfile.TemporaryDirectory() as tmp:
            document = build_skill_index(
                catalog_with(
                    repository("acme/pack", [("skills", "notes", "skills/notes/SKILL.md")], stars=4000),
                    repository("other/pack", [("skills", "notes", "skills/notes/SKILL.md")], stars=3900),
                ),
                mode="llm",
                root=Path(tmp),
                client=Client(),
                retrieve=BodyReader(texts),
                classify=lambda items: {},
            )
        skill = document["domains"][0]["scenarios"][0]["skills"][0]
        self.assertEqual(skill["kind"], "combined")
        self.assertIn("Conflicts", skill["leader"]["instruction"])
        self.assertEqual({item["role"] for item in skill["variants"]}, {"peer"})

    def test_unreadable_model_reply_uses_metadata(self) -> None:
        left = occurrence("acme/pack", "notes", "skills/notes/SKILL.md")
        right = occurrence("other/pack", "notes", "skills/notes/SKILL.md")

        class Client:
            def complete(self, messages, max_tokens=100):
                return "not json"

        with tempfile.TemporaryDirectory() as tmp:
            document = build_skill_index(
                catalog_with(
                    repository("acme/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
                    repository("other/pack", [("skills", "notes", "skills/notes/SKILL.md")]),
                ),
                mode="llm",
                root=Path(tmp),
                client=Client(),
                retrieve=BodyReader({left.occurrence_id: "# one", right.occurrence_id: "# two"}),
                classify=lambda items: {"notes": ("documents", "notes")},
            )
        skill = next(
            item
            for domain in document["domains"]
            for scenario in domain["scenarios"]
            for item in scenario["skills"]
        )
        self.assertEqual(skill["judge"], "heuristic-fallback")
        self.assertEqual(document["domains"][0]["name"], "documents")

    def test_downloaded_skill_file_is_read_without_cloning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = skill_cache_path(root, "acme/pack", "skills/notes/SKILL.md")
            path.parent.mkdir(parents=True)
            path.write_text("# notes\nKeep a log.\n")
            body = SkillRetriever(root).read(occurrence("acme/pack", "notes", "skills/notes/SKILL.md"))
        self.assertEqual(body.source, "cache")
        self.assertIn("Keep a log", body.text)

    def test_checkout_is_read_before_the_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "cache" / "repos" / "acme" / "pack" / "skills" / "notes" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("# notes\nKeep a log.\n")
            body = SkillRetriever(root).read(occurrence("acme/pack", "notes", "skills/notes/SKILL.md"))
        self.assertEqual(body.source, "submodule")
        self.assertIn("Keep a log", body.text)


def _screened(occurrence: SkillOccurrence, score: float):
    from skillrepo.sorting.screen import ScreenedCopy

    return ScreenedCopy(occurrence, score, ["shipped skill"], True)


if __name__ == "__main__":
    unittest.main()
