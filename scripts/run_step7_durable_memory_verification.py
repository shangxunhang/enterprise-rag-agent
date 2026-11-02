"""Run the 351 focused/impact manifest and write exact collection/execution receipts.

LANE351_TEST_PG_DSN must identify an isolated PostgreSQL test database. Tests
create unique schemas. This runner neither installs dependencies nor starts DBs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest


MANIFEST = (
    'test_step7_durable_memory_persistence.py',
    'test_step7_durable_memory_postgres.py',
    'test_multi_agent_v3_memory_runtime.py',
    'test_multi_agent_v3_context_runtime.py',
    'test_multi_agent_v3_storage_lifecycle.py',
    'test_multi_agent_v3_artifact_runtime.py',
    'test_multi_agent_v3_fact_snapshot_runtime.py',
    'test_multi_agent_v3_claim_verification_runtime.py',
    'test_multi_agent_v3_lifecycle_commit.py',
    'test_multi_agent_v3_workflow_recovery.py',
    'test_multi_agent_v3_verifier_mainline.py',
    'test_post_write_section_document_lifecycle.py',
    'test_p1_application_recovery.py',
)


class Receipt:
    def __init__(self):
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({'nodeid': report.nodeid, 'phase': report.when,
                             'outcome': report.outcome, 'duration': report.duration})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--basetemp', type=Path, required=True)
    parser.add_argument('tests', nargs='*')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep pytest capture and SQLite sort spills on the explicitly chosen test
    # volume; production storage policy is unaffected.
    scratch = str(args.output.parent.resolve())
    for name in ('TEMP', 'TMP', 'SQLITE_TMPDIR'):
        os.environ[name] = scratch
    tempfile.tempdir = scratch
    receipt = Receipt()
    tests = args.tests or ['backend/tests/'+name for name in MANIFEST]
    status = pytest.main([*tests, '-q', '--capture=sys', '-p', 'no:cacheprovider',
                         '--tb=short', '--basetemp', str(args.basetemp),
                         '--junitxml', str(args.output.with_suffix('.xml'))], plugins=[receipt])
    calls = [item for item in receipt.reports if item['phase'] == 'call']
    exact = (len(receipt.collected) == len(set(receipt.collected))
             and Counter(receipt.collected) == Counter(item['nodeid'] for item in calls))
    green = status == 0 and exact and all(item['outcome'] == 'passed' for item in receipt.reports)
    result = {'schema_version': 'lane351_verification_v1', 'selectors': tests,
              'collected_nodeids': receipt.collected, 'reports': receipt.reports,
              'pytest_exit_code': int(status), 'exact_collected_equals_executed': exact,
              'collected_count': len(receipt.collected), 'call_outcomes': dict(Counter(item['outcome'] for item in calls)),
              'acceptance_green_no_skips': green}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('collected_count','call_outcomes',
                      'exact_collected_equals_executed','acceptance_green_no_skips')}))
    return 0 if green else 1


if __name__ == '__main__':
    sys.exit(main())
