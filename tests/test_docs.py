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


if __name__ == "__main__":
    unittest.main()
