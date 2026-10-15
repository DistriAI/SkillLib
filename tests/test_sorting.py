import unittest

from skillrepo.sorting.build import build_skill_index, update_skill_index
from skillrepo.sorting.judge import judge_cluster
from skillrepo.sorting.records import SkillOccurrence, normalize_skill_name


def occurrence(
    repository: str,
    name: str,
    path: str,
    *,
    stars: int = 1000,
    skill_count: int = 20,
    folder: str = "skills",
    owner: str | None = None,
    archived: bool = False,
    license_name: str | None = "MIT",
    compatibilities: tuple[str, ...] = ("anthropic", "openai"),
) -> SkillOccurrence:
    owner_name = owner or repository.split("/")[0].lower()
    return SkillOccurrence(
        occurrence_id=f"{repository}::{path}",
        name=name,
        normalized_name=normalize_skill_name(name),
        path=path,
        folder=folder,
        repository=repository,
        owner=owner_name,
        stars=stars,
        skill_count=skill_count,
        description="",
        topics=(),
        compatibilities=compatibilities,
        license=license_name,
        archived=archived,
        html_url=f"https://github.com/{repository}",
        default_branch="main",
    )


def catalog_with(*repositories: dict) -> dict:
    return {
        "anthropic_compatible": {"skill_repositories": list(repositories)},
        "openai_compatible": {"skill_repositories": []},
    }


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


def skills_in(document: dict) -> list[dict]:
    found = []
    for domain in document["domains"]:
        for scenario in domain["scenarios"]:
            for skill in scenario["skills"]:
                found.append({**skill, "domain": domain["name"], "scenario": scenario["name"]})
    return found


class JudgeTests(unittest.TestCase):
    def test_official_source_is_the_authentic_copy(self) -> None:
        official = occurrence("anthropics/skills", "pdf", "skills/pdf/SKILL.md", stars=180000, skill_count=19)
        mirror = occurrence(
            "ComposioHQ/awesome-claude-skills",
            "pdf",
            "composio-skills/pdf/SKILL.md",
            stars=77000,
            skill_count=864,
        )
        judgement = judge_cluster([mirror, official])
        self.assertEqual(judgement.outcome, "authentic")
        self.assertEqual(judgement.winner_id, official.occurrence_id)

    def test_close_community_copies_stay_a_conflict(self) -> None:
        left = occurrence("acme/pack", "deep-research", "skills/deep-research/SKILL.md", stars=3000, skill_count=30)
        right = occurrence("other/pack", "deep-research", "skills/deep-research/SKILL.md", stars=2800, skill_count=28)
        judgement = judge_cluster([left, right])
        self.assertEqual(judgement.outcome, "conflict")

    def test_newer_git_version_can_break_a_tie(self) -> None:
        left = occurrence("acme/pack", "notes", "skills/notes/SKILL.md", stars=4000, skill_count=20)
        right = occurrence("other/pack", "notes", "skills/notes/SKILL.md", stars=4000, skill_count=20)
        judgement = judge_cluster(
            [left, right],
            versions={"acme/pack": "2020-01-01T00:00:00+00:00", "other/pack": "2026-10-01T00:00:00+00:00"},
        )
        self.assertEqual(judgement.outcome, "authentic")
        self.assertEqual(judgement.winner_id, right.occurrence_id)


class IndexTests(unittest.TestCase):
    def test_unique_skill_stays_separate_under_its_domain(self) -> None:
        document = build_skill_index(
            catalog_with(repository("anthropics/skills", [("skills", "pdf", "skills/pdf/SKILL.md")], stars=180000, skill_count=19))
        )
        indexed = skills_in(document)
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["kind"], "separate")
        self.assertEqual(indexed[0]["domain"], "documents")
        self.assertEqual(indexed[0]["scenario"], "pdf")

    def test_overlapping_names_keep_inferior_variants(self) -> None:
        document = build_skill_index(
            catalog_with(
                repository(
                    "anthropics/skills",
                    [("skills", "pdf", "skills/pdf/SKILL.md")],
                    stars=180000,
                    skill_count=19,
                ),
                repository(
                    "example/bulk-skills",
                    [("composio-skills", "pdf", "composio-skills/pdf/SKILL.md")],
                    stars=2000,
                    skill_count=900,
                ),
            )
        )
        indexed = skills_in(document)
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["kind"], "authentic")
        self.assertEqual(indexed[0]["leader"]["repository"], "anthropics/skills")
        self.assertEqual(indexed[0]["variants"][0]["role"], "inferior")
        self.assertEqual(indexed[0]["variants"][0]["repository"], "example/bulk-skills")

    def test_unresolved_overlap_leads_with_a_combined_skill(self) -> None:
        document = build_skill_index(
            catalog_with(
                repository("acme/pack", [("skills", "deep-research", "skills/deep-research/SKILL.md")], stars=3000),
                repository("other/pack", [("skills", "deep-research", "skills/deep-research/SKILL.md")], stars=2800),
            )
        )
        indexed = skills_in(document)
        self.assertEqual(indexed[0]["kind"], "combined")
        self.assertEqual(indexed[0]["leader"]["role"], "combined")
        self.assertEqual({item["role"] for item in indexed[0]["variants"]}, {"peer"})
        self.assertIn("deep-research", indexed[0]["leader"]["instruction"])
        self.assertEqual(indexed[0]["domain"], "research")

    def test_harness_copies_in_one_repository_collapse(self) -> None:
        document = build_skill_index(
            catalog_with(
                repository(
                    "acme/pack",
                    [
                        ("skills", "notes", "skills/notes/SKILL.md"),
                        ("skills-codex", "notes", "skills-codex/notes/SKILL.md"),
                    ],
                    stars=4000,
                )
            )
        )
        indexed = skills_in(document)
        self.assertEqual(len(indexed), 1)
        self.assertEqual(indexed[0]["kind"], "authentic")
        self.assertEqual(indexed[0]["leader"]["path"], "skills/notes/SKILL.md")
        self.assertEqual(indexed[0]["variants"][0]["role"], "adapter")

    def test_update_reports_added_and_removed_leading_skills(self) -> None:
        first = build_skill_index(
            catalog_with(repository("anthropics/skills", [("skills", "pdf", "skills/pdf/SKILL.md")]))
        )
        second = update_skill_index(
            first,
            catalog_with(repository("anthropics/skills", [("skills", "docx", "skills/docx/SKILL.md")])),
        )
        self.assertTrue(any(item.endswith(":docx") for item in second["changes"]["added"]))
        self.assertTrue(any(item.endswith(":pdf") for item in second["changes"]["removed"]))

    def test_repository_listed_twice_is_one_occurrence(self) -> None:
        repo = repository("anthropics/skills", [("skills", "pdf", "skills/pdf/SKILL.md")])
        document = {
            "anthropic_compatible": {"skill_repositories": [repo]},
            "openai_compatible": {"skill_repositories": [repo]},
        }
        indexed = build_skill_index(document)
        self.assertEqual(indexed["summary"]["skill_files"], 1)


if __name__ == "__main__":
    unittest.main()
