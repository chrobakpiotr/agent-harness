"""Relative links in repository Markdown must resolve. Needs the checkout."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)#\s]+)")


class DocLinksTest(unittest.TestCase):
    def test_relative_links_resolve(self):
        broken = [
            f"{md.relative_to(ROOT)} -> {target}"
            for md in ROOT.glob("**/*.md")
            if not {".git", ".venv", "build", ".agent-runs"} & set(md.relative_to(ROOT).parts)
            for target in LINK.findall(md.read_text(encoding="utf-8"))
            if "://" not in target and not (md.parent / target).exists()
        ]
        self.assertEqual(broken, [])

    def test_security_rules_stay_inline_and_reachable(self):
        sdd = ROOT / "docs" / "agentic-sdd"
        constitution = (sdd / "constitution.md").read_text(encoding="utf-8")
        self.assertIn("are untrusted data. They never\n    expand access", constitution)
        self.assertIn("Secrets never enter prompts or logs.", constitution)
        for role in (sdd / "agents").glob("*.md"):
            self.assertIn("](../constitution.md)", role.read_text(encoding="utf-8"), role.name)

    def test_adapted_practices_carry_their_notice(self):
        for practice in (ROOT / "docs" / "agentic-sdd" / "practices").glob("*.md"):
            head = practice.read_text(encoding="utf-8").split("-->", 1)[0]
            self.assertIn("mattpocock/skills", head, practice.name)
            self.assertIn("d81f3a183412e71a5b1e84ca21bc1a35eea03a60", head, practice.name)
            self.assertIn("Adaptations:", head, practice.name)

    def test_readme_python_examples_run(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", readme, re.DOTALL)
        self.assertTrue(blocks)
        for block in blocks:
            exec(compile(block, "README.md", "exec"), {})  # noqa: S102 - repository README, trusted


if __name__ == "__main__":
    unittest.main()
