import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from skilllib.catalog import Catalog
from skilllib.classify import (
    classify,
    extract_repository_links,
    group_domains,
    has_agent_skill_frontmatter,
    is_specification_repo,
    needs_inspection,
    worth_file_listing,
)
from skilllib.content import FileListing
from skilllib.models import RepoMeta
from skilllib.search import search_repositories
from skilllib.visit import prefetch_repositories, visit_repository


def meta(full_name: str, description: str, stars: int = 2000, topics: list[str] | None = None) -> RepoMeta:
    owner, name = full_name.split("/")
    return RepoMeta(
        full_name=full_name,
        name=name,
        stars=stars,
        description=description,
        html_url=f"https://github.com/{full_name}",
        default_branch="main",
        topics=topics or [],
    )


class ClassifyTests(unittest.TestCase):
    def test_frontmatter_requires_name_and_description(self) -> None:
        text = "---\nname: pdf\ndescription: Handle PDFs\n---\n# PDF\n"
        self.assertTrue(has_agent_skill_frontmatter(text))
        self.assertFalse(has_agent_skill_frontmatter("---\nname: pdf\n---\n"))

    def test_groups_skills_under_domains(self) -> None:
        domains = group_domains(
            [
                "skills/pdf/SKILL.md",
                "skills/docx/SKILL.md",
                "skills/creative/algorithmic-art/SKILL.md",
            ]
        )
        by_name = {domain.name: [skill.name for skill in domain.skills] for domain in domains}
        self.assertEqual(by_name["creative"], ["algorithmic-art"])
        self.assertEqual(by_name["skills"], ["docx", "pdf"])

    def test_skill_repository_is_kept_when_it_ships_many_skills(self) -> None:
        repository = meta("anthropics/skills", "Public repository for Agent Skills", topics=["agent-skills"])
        paths = [f"skills/skill-{index}/SKILL.md" for index in range(6)]
        decision = classify(repository, "These skills run in Claude.", paths, True)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.kind, "skill_repository")
        self.assertEqual(decision.compatibilities, ["anthropic"])
        self.assertEqual(sum(len(domain.skills) for domain in decision.domains), 6)

    def test_methodology_pack_is_content_not_a_mechanism(self) -> None:
        repository = meta(
            "obra/superpowers",
            "An agentic skills framework and software development methodology.",
        )
        paths = [f"skills/step-{index}/SKILL.md" for index in range(6)]
        readme = "Works with Claude Code and Codex."
        decision = classify(repository, readme, paths, True)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.compatibilities, ["anthropic", "openai"])

    def test_topic_alone_does_not_select_a_product(self) -> None:
        repository = meta(
            "paperclipai/paperclip",
            "The open-source app everyone uses to manage agents",
            topics=["agent-skills"],
        )
        self.assertFalse(needs_inspection(repository))
        self.assertFalse(worth_file_listing(repository))

    def test_mechanism_repos_are_rejected_even_with_skill_files(self) -> None:
        repository = meta(
            "yusufkaraaslan/Skill_Seekers",
            "Convert documentation websites, GitHub repositories, and PDFs into Claude AI skills",
        )
        paths = [f"skills/skill-{index}/SKILL.md" for index in range(8)]
        decision = classify(repository, "A converter.", paths, True)
        self.assertFalse(decision.accepted)
        self.assertIn("mechanism", decision.reason)

    def test_skill_loader_is_not_listed(self) -> None:
        repository = meta("numman-ali/openskills", "Universal skills loader for AI coding agents")
        self.assertFalse(worth_file_listing(repository))

    def test_library_that_mentions_the_spec_is_still_content(self) -> None:
        repository = meta(
            "google-labs-code/stitch-skills",
            "A library of Agent Skills. Each skill follows the Agent Skills specification.",
        )
        self.assertFalse(is_specification_repo(repository))
        self.assertTrue(worth_file_listing(repository))

    def test_specification_is_rejected(self) -> None:
        repository = meta("agentskills/agentskills", "Specification and documentation for Agent Skills")
        self.assertTrue(is_specification_repo(repository))
        self.assertFalse(needs_inspection(repository))
        paths = [f"examples/skill-{index}/SKILL.md" for index in range(6)]
        decision = classify(repository, "An open format for skills.", paths, True)
        self.assertFalse(decision.accepted)
        self.assertIn("specification", decision.reason)

    def test_incidental_product_skills_are_rejected(self) -> None:
        repository = meta("acme/agent-app", "Desktop app for coding agents")
        paths = [f".claude/skills/skill-{index}/SKILL.md" for index in range(3)]
        decision = classify(repository, "Install the app.", paths, True)
        self.assertFalse(decision.accepted)

    def test_collection_indexes_repositories(self) -> None:
        repository = meta("VoltAgent/awesome-agent-skills", "A curated collection of agent skills.")
        readme = "\n".join(
            [
                "# Awesome Agent Skills",
                "A collection of official Agent Skills.",
                "https://github.com/anthropics/skills/tree/main/skills/pdf",
                "https://github.com/openai/skills",
                "https://github.com/addyosmani/agent-skills",
                "https://github.com/obra/superpowers",
                "https://github.com/alirezarezvani/claude-skills",
                "https://github.com/user-attachments/assets/abc",
                "Not skills: https://github.com/serpapi/serpapi-cli",
            ]
        )
        links = extract_repository_links(readme, repository.full_name)
        self.assertEqual(
            links,
            [
                "anthropics/skills",
                "openai/skills",
                "addyosmani/agent-skills",
                "obra/superpowers",
                "alirezarezvani/claude-skills",
            ],
        )
        decision = classify(repository, readme, [], False)
        self.assertEqual(decision.kind, "collection")
        self.assertEqual(decision.child_repositories, links)


class SearchTests(unittest.TestCase):
    def test_search_builds_a_hierarchy_and_drops_unrelated_repos(self) -> None:
        skill = meta("anthropics/skills", "Public repository for Agent Skills", stars=180000)
        collection = meta("VoltAgent/awesome-agent-skills", "Curated agent skills.", stars=30000)
        spec = meta("agentskills/agentskills", "Specification and documentation for Agent Skills", stars=20000)
        product = meta("paperclipai/paperclip", "The open-source app everyone uses to manage agents", stars=90000)
        small = meta("acme/skills", "Claude skills", stars=10)
        readme = "\n".join(
            [
                "# Awesome Agent Skills",
                "A collection of official Agent Skills for Claude and Codex.",
                "https://github.com/anthropics/skills",
                "https://github.com/openai/skills",
                "https://github.com/addyosmani/agent-skills",
                "https://github.com/obra/superpowers",
                "https://github.com/alirezarezvani/claude-skills",
            ]
        )

        class FakeClient:
            def search(self, queries):
                self.queries = queries
                return [product, skill, collection, spec, small]

        class FakeFetcher:
            def readmes(self, metas, log=None):
                return {
                    "anthropics/skills": "Skills for Claude.",
                    "VoltAgent/awesome-agent-skills": readme,
                    "paperclipai/paperclip": "An app for running agents at work.",
                }

            def listings(self, metas, log=None):
                names = {item.full_name for item in metas}
                self.listed = names
                listings = {}
                if "anthropics/skills" in names:
                    listings["anthropics/skills"] = FileListing(
                        [f"skills/area-{index}/SKILL.md" for index in range(5)],
                        20,
                        "test",
                    )
                if "VoltAgent/awesome-agent-skills" in names:
                    listings["VoltAgent/awesome-agent-skills"] = FileListing([], 4, "test")
                return listings

            def frontmatter_state(self, item, paths):
                return "ok"

        root = Path(tempfile.mkdtemp(prefix="skilllib-search-"))
        catalog = search_repositories(
            1000,
            root=root,
            client=FakeClient(),
            fetcher=FakeFetcher(),
            log=lambda _message: None,
        )
        self.assertEqual([item.full_name for item in catalog.skill_repositories()], ["anthropics/skills"])
        self.assertEqual([item.full_name for item in catalog.collections()], ["VoltAgent/awesome-agent-skills"])
        document = json.loads((root / "catalog" / "skill_repositories.json").read_text(encoding="utf-8"))
        anthropic = document["anthropic_compatible"]
        self.assertEqual(anthropic["skill_repositories"][0]["domains"][0]["name"], "skills")
        indexed = anthropic["collections"][0]["repositories"]
        qualified = next(item for item in indexed if item["full_name"] == "anthropics/skills")
        self.assertTrue(qualified["qualified"])
        self.assertEqual(qualified["stars"], 180000)
        self.assertFalse(any(item["full_name"] == "agentskills/agentskills" for item in indexed))
        reloaded = Catalog.load(root / "catalog" / "skill_repositories.json")
        self.assertEqual(reloaded.get("anthropics/skills").skill_count, 5)


class VisitTests(unittest.TestCase):
    def test_first_visit_clones_and_later_visits_use_the_cache(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-visit-"))
        calls = []

        def run(command, cwd, check, **kwargs):
            calls.append(command)
            dest = Path(command[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            (dest / "README.md").write_text("cached\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        first = visit_repository("anthropics/skills", root, run=run)
        second = visit_repository("anthropics/skills", root, run=run)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["git", "clone"])
        self.assertNotIn("submodule", calls[0])
        self.assertTrue((first / "README.md").is_file())
        self.assertFalse((root / ".gitmodules").exists())

    def test_visit_reports_git_stderr(self) -> None:
        def run(command, cwd, check, **kwargs):
            raise subprocess.CalledProcessError(128, command, stderr="fatal: repository not found")

        with self.assertRaises(RuntimeError) as caught:
            visit_repository("acme/missing", Path(tempfile.mkdtemp(prefix="skilllib-visit-")), run=run)
        message = str(caught.exception)
        self.assertIn("128", message)
        self.assertIn("repository not found", message)

    def test_invalid_repository_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            visit_repository("not a repo", Path("."))

    def test_a_restored_tree_without_git_is_reused(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-snapshot-"))
        dest = root / "cache" / "repos" / "acme" / "skills"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("local\n", encoding="utf-8")
        calls = []

        def run(command, cwd, check, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        found = visit_repository("acme/skills", root, run=run)
        self.assertEqual(found, dest)
        self.assertEqual(calls, [])

    def test_ssh_clone_uses_a_key_and_does_not_prompt(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-ssh-"))
        seen = {}

        def run(command, cwd, check, **kwargs):
            seen["command"] = command
            seen["env"] = kwargs.get("env") or {}
            dest = Path(command[-1])
            dest.mkdir(parents=True)
            (dest / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            (dest / "README.md").write_text("cached\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        visit_repository("acme/skills", root, protocol="ssh", run=run)
        self.assertEqual(seen["command"][-2], "git@github.com:acme/skills.git")
        self.assertIn("BatchMode=yes", seen["env"].get("GIT_SSH_COMMAND", ""))

    def test_prefetch_clones_missing_repositories_and_retries_failures(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="skilllib-prefetch-"))
        catalog = {
            "min_stars": 1,
            "anthropic_compatible": {
                "skill_repositories": [
                    {
                        "full_name": "acme/skills",
                        "default_branch": "main",
                        "license": "MIT",
                        "html_url": "https://github.com/acme/skills",
                    },
                    {
                        "full_name": "acme/other",
                        "default_branch": "master",
                        "license": None,
                        "html_url": "https://github.com/acme/other",
                    },
                ],
                "collections": [],
            },
        }
        (root / "catalog").mkdir()
        (root / "catalog" / "skill_repositories.json").write_text(json.dumps(catalog), encoding="utf-8")
        calls = []

        def run(command, cwd, check, **kwargs):
            calls.append(command)
            if command[-2].endswith("acme/other.git"):
                raise subprocess.CalledProcessError(128, command, stderr="fatal: denied")
            dest = Path(command[-1])
            dest.mkdir(parents=True)
            (dest / ".git").mkdir()
            (dest / "README.md").write_text("cached\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0)

        status = prefetch_repositories(root, workers=2, run=run)
        self.assertEqual(status["cloned"], 1)
        self.assertEqual(status["failed"], 1)
        self.assertTrue((root / "cache" / "repos" / "manifest.json").is_file())
        again = prefetch_repositories(root, workers=2, run=run, only_failed=True)
        self.assertEqual(again["cached"], 1)
        self.assertEqual(again["failed"], 1)
        self.assertEqual(sum(1 for command in calls if command[-2].endswith("acme/skills.git")), 1)
        self.assertEqual(sum(1 for command in calls if command[-2].endswith("acme/other.git")), 2)


if __name__ == "__main__":
    unittest.main()
