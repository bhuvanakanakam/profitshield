"""Fail closed when a task pull request does not target development.

CI passes the PR base via an environment variable, never shell interpolation.
This script does not change repository settings, branches or PR metadata.
"""
import os
import sys


def check_base(base):
    if base != 'development':
        raise ValueError('Task pull requests must target development; correct the PR base before review.')


def main():
    try:
        check_base(os.environ.get('PROFITSHIELD_PR_BASE'))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print('PR base is development.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
