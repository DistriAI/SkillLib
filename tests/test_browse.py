import tempfile
import unittest
from pathlib import Path

from skilllib.browse import write_browse_site


class BrowseTests(unittest.TestCase):
    def test_pages_link_a_skill_back_to_its_repositories(self) -> None:
        index = {
            "domains": [
                {
                    "name": "documents",
                    "scenarios": [
                        {
                            "name": "pdf",
                            "skills": [
                                {
                                    "name": "pdf",
                                    "kind": "combined",
                                    "rationale": "The copies disagree.",
                                    "leader": {"role": "combined", "instruction": "Use either copy."},
                                    "variants": [
                                        {
                                            "role": "peer",
                                            "repository": "anthropics/skills",
                                            "path": "skills/pdf/SKILL.md",
                                            "html_url": "https://github.com/anthropics/skills",
                                        },
                                        {
                                            "role": "peer",
                                            "repository": "openai/skills",
                                            "path": "skills/.curated/pdf/SKILL.md",
                                            "html_url": "https://github.com/openai/skills",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            counts = write_browse_site(index, destination)
            root = (destination / "index.html").read_text(encoding="utf-8")
            skill = (destination / "documents" / "pdf" / "pdf.html").read_text(encoding="utf-8")
        self.assertEqual(counts["domains"], 1)
        self.assertIn("documents/index.html", root)
        self.assertIn("https://github.com/anthropics/skills", skill)
        self.assertIn("https://github.com/openai/skills", skill)
        self.assertIn('href="../../index.html"', skill)


if __name__ == "__main__":
    unittest.main()
