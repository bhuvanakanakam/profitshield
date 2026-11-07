"""M0 workflow policy regressions; no app imports, data or network access."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.github' / 'scripts' / 'check_pr_base.py'
spec = importlib.util.spec_from_file_location('development_workflow_policy', SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class DevelopmentWorkflowTests(unittest.TestCase):
    def test_development_is_the_integration_target(self):
        policy.check_base('development')

    def test_main_and_other_branches_are_rejected(self):
        for base in ['main', 'master', 'release', 'feature/task', 'Development', 'refs/heads/development']:
            with self.subTest(base=base), self.assertRaises(ValueError):
                policy.check_base(base)

    def test_missing_or_ambiguous_context_fails_closed(self):
        for base in [None, '', ' development', 'development ', 'development\nmain', ['development']]:
            with self.subTest(base=base), self.assertRaises(ValueError):
                policy.check_base(base)

    def test_untrusted_base_names_cannot_be_executed_or_reflected(self):
        malicious = 'development; echo UNTRUSTED_BASE_PAYLOAD'
        result = subprocess.run([sys.executable, str(SCRIPT)], env={'PROFITSHIELD_PR_BASE': malicious},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('UNTRUSTED_BASE_PAYLOAD', result.stdout + result.stderr)

    def test_cli_success_and_failure_statuses(self):
        for env, status in [({'PROFITSHIELD_PR_BASE': 'development'}, 0),
                            ({'PROFITSHIELD_PR_BASE': 'main'}, 1), ({}, 1)]:
            with self.subTest(env=env):
                result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                                        text=True, timeout=5)
                self.assertEqual(result.returncode, status)


if __name__ == '__main__':
    unittest.main()
