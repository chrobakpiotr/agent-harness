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


    def test_hidden_edits_and_fake_reproductions_do_not_count_as_red(self):
        after_fix = ("Bash", {"command": "git stash; python3 -m unittest; git stash pop"}, "AssertionError: 1998 != 1999")
        for calls in (
            [("Bash", {"command": "python3 - <<'EOF'\np='invoice.py'\nopen(p,'w').write(s)\nEOF"}, ""), after_fix, GREEN],
            [("Bash", {"command": "git apply fix.patch"}, ""), RED, GREEN],
            [("Bash", {"command": "cp /tmp/x.py invoice.py"}, ""), RED, GREEN],
            [("Bash", {"command": "echo 'issue says 1998'"}, "issue says 1998"), EDIT, GREEN],
            [("Bash", {"command": "python3 -c 'print(1998)'"}, "1998"), EDIT, GREEN],
        ):
            with self.subTest(calls=calls):
                self.assertFalse(self.run_case(calls)["red_before_fix"])

    def test_readers_and_lookalike_paths_are_not_edits(self):
        for command in ("cat ISSUE.md invoice.py test_invoice.py", "sed -n 1,20p invoice.py", "git diff invoice.py",
                        "python3 -m unittest > invoice.py.log", "python3 - <<'EOF'\np='test_invoice.py'\nEOF"):
            with self.subTest(command=command):
                self.assertFalse(grade._is_source_edit("Bash", {"command": command}))
        red_from_test = ("Bash", {"command": "python3 -m unittest"}, "AssertionError: 1998 != 1999")
        self.assertTrue(self.run_case([("Write", {"file_path": "test_regression.py"}, ""), red_from_test, EDIT, GREEN])["pass"])

    def test_hypotheses_need_predictions_in_any_list_shape(self):
        bullets = "Root cause: hypothesis 1 confirmed.\n\n## Ranked hypotheses\n\n" + "".join(
            f"- {cause}. Prediction: {probe} changes the total.\n" for cause, probe in
            (("Truncation", "rounding"), ("Quantity", "qty=1"), ("Discount", "discount=0")))
        wrapped = "Hypotheses:\n1. Truncation.\n   Prediction: rounding fixes it. Confirmed.\n2. Qty. Ruled out.\n3. Discount. Ruled out.\n"
        self.assertTrue(self.run_case([RED, EDIT, GREEN], handoff=bullets)["hypotheses"])
        self.assertTrue(self.run_case([RED, EDIT, GREEN], handoff=wrapped)["hypotheses"])
        steps = "No hypotheses were needed.\n1. Edited invoice.py\n2. Added a test\n3. Ran the tests\n"
        self.assertFalse(self.run_case([RED, EDIT, GREEN], handoff=steps)["hypotheses"])

    def test_regression_must_fail_on_assertions_not_errors(self):
        helper = FIXED + "\n\ndef helper():\n    return 1\n"
        test = "import unittest\nfrom invoice import helper\n\n\nclass T(unittest.TestCase):\n" \
               "    def test_helper(self):\n        self.assertEqual(helper(), 1)\n"
        self.assertFalse(self.run_case([RED, EDIT, GREEN], source=helper, test=test)["regression"])


if __name__ == "__main__":
    unittest.main()
