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
            if not {".git", ".venv", "build"} & set(md.relative_to(ROOT).parts)
            for target in LINK.findall(md.read_text(encoding="utf-8"))
            if "://" not in target and not (md.parent / target).exists()
        ]
        self.assertEqual(broken, [])

    def test_readme_python_examples_run(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", readme, re.DOTALL)
        self.assertTrue(blocks)
        for block in blocks:
            exec(compile(block, "README.md", "exec"), {})  # noqa: S102 - repository README, trusted


if __name__ == "__main__":
    unittest.main()
