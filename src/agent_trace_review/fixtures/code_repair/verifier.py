"""Fixed evaluator, passed directly to isolated Python; never loaded from candidate files."""

import json
import sys
import unittest
import xml.etree.ElementTree as ET

namespace = {}
source = json.load(sys.stdin)["session.py"]
exec(compile(source, "session.py", "exec"), namespace)
is_valid = namespace["is_valid"]


class SessionExpiry(unittest.TestCase):
    def test_before_expiry(self):
        self.assertTrue(is_valid(100, 99))

    def test_at_expiry(self):
        self.assertFalse(is_valid(100, 100))

    def test_after_expiry(self):
        self.assertFalse(is_valid(100, 101))


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.cases.append((test, None, ""))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.cases.append((test, "failure", self._exc_info_to_string(err, test)))

    def addError(self, test, err):
        super().addError(test, err)
        self.cases.append((test, "error", self._exc_info_to_string(err, test)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.cases.append((test, "skipped", reason))


print("Fixed session-expiry verifier started", file=sys.stderr, flush=True)
result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(SessionExpiry)
)
root = ET.Element(
    "testsuite",
    name="session-expiry",
    tests=str(result.testsRun),
    failures=str(len(result.failures)),
    errors=str(len(result.errors)),
    skipped=str(len(result.skipped)),
)
for test, status, message in result.cases:
    case = ET.SubElement(root, "testcase", classname="SessionExpiry", name=test._testMethodName)
    if status:
        ET.SubElement(case, status).text = message
print(ET.tostring(root, encoding="unicode"), flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
