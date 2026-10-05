"""The diagnosing eval grader passes a reference run and fails each missing discipline. Needs the checkout."""

import importlib.util
import json
import pathlib
import shutil
import tempfile
import unittest

EVAL = pathlib.Path(__file__).resolve().parents[1] / "evals" / "diagnosing"
spec = importlib.util.spec_from_file_location("grade", EVAL / "grade.py")
grade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grade)

FIXED = (EVAL / "fixture" / "invoice.py").read_text().replace("int(price * 100)", "round(price * 100)")
REGRESSION = "import unittest\nfrom invoice import total_cents\n\n\nclass T(unittest.TestCase):\n" \
             "    def test_cents(self):\n        self.assertEqual(total_cents([(19.99, 1)]), 1999)\n"
HANDOFF = "Hypotheses, ranked:\n" + "".join(f"{n}. If {cause} is the cause, then {probe} changes the result.\n"
                  for n, cause, probe in ((1, "float truncation", "rounding"), (2, "qty", "qty=1"),
                                          (3, "discount", "discount=0")))
RED = ("Bash", {"command": "python3 -c 'from invoice import *; print(total_cents([(19.99, 1)]))'"}, "1998")
EDIT = ("Edit", {"file_path": "invoice.py"}, "ok")
GREEN = ("Bash", {"command": "python3 -m unittest"}, "OK")


class GraderTest(unittest.TestCase):
    def run_case(self, calls, source=FIXED, test=REGRESSION, handoff=HANDOFF):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(EVAL / "fixture", tmp / "ws")
        (tmp / "ws" / "invoice.py").write_text(source)
        (tmp / "ws" / "test_regression.py").write_text(test)
        (tmp / "ws" / "HANDOFF.md").write_text(handoff)
        lines = []
        for i, (name, args, out) in enumerate(calls):
            lines.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": name, "input": args}]}})
            lines.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": out}]}]}})
        (tmp / "t.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
        return grade.grade(tmp / "ws", tmp / "t.jsonl")

    def test_reference_run_passes(self):
        self.assertTrue(self.run_case([RED, EDIT, GREEN])["pass"])

    def test_each_missing_discipline_fails(self):
        cases = {
            "red_before_fix": [self.run_case([EDIT, GREEN]), self.run_case([EDIT, RED, GREEN])],
            "hypotheses": [self.run_case([RED, EDIT, GREEN], handoff="Fixed the rounding.\n")],
            "regression": [self.run_case([RED, EDIT, GREEN], test=REGRESSION.replace("19.99, 1)]), 1999", "2.0, 1)]), 200"))],
            "fix": [self.run_case([RED, EDIT, GREEN], source=(EVAL / "fixture" / "invoice.py").read_text())],
        }
        for check, results in cases.items():
            for result in results:
                with self.subTest(check=check):
                    self.assertFalse(result[check])
                    self.assertFalse(result["pass"])


if __name__ == "__main__":
    unittest.main()
