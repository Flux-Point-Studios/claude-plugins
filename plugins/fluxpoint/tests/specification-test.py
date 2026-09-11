import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / 'scripts/specification.py'
COMPILER = PLUGIN / 'scripts/compile-graph.py'


def packet():
    return {
        'version': 1, 'goal': 'Reject unchecked work before implementation',
        'asset': 'The existing graph compiler and harness',
        'scope': ['Spec enforcement'], 'outOfScope': ['Installing a theorem prover'],
        'decisions': [{
            'id': 'enforcement', 'status': 'defaulted', 'dependsOn': [],
            'record': {
                'question': 'How should the requirement boundary be enforced?',
                'options': [{'option': option, 'argued_by': 'model',
                             'strongest_objection': 'This option has maintenance and migration costs.'}
                            for option in ['manifest', 'prose']],
                'chosen': 'manifest',
                'rationale': 'The compiler already validates structured inputs before it emits code.',
                'overturned_prior': False, 'frozen_by': 'none', 'reversible': True,
                'evidence': ['Read scripts/compile-graph.py']},
            'costs': [{'option': option, 'cost': 'One maintained artifact to review',
                       'basis': 'estimate', 'evidence': ['Design estimate, not a timing measurement']}
                      for option in ['manifest', 'prose']]}],
        'requirements': [{'id': 'reject-missing', 'statement': 'An absent spec prevents compilation',
                          'decisions': ['enforcement'], 'checks': ['spec-tests'],
                          'counterexample': 'A mutating graph compiles with no spec'}],
        'checks': [{'id': 'spec-tests', 'kind': 'test',
                    'argv': ['{python}', '-c', 'print("checked")'],
                    'scope': 'Spec gate CLI behavior', 'assumptions': [], 'timeoutSeconds': 10}],
        'challenges': [{'claim': 'The model could mark every decision accepted',
                        'resolution': 'Defaulted is distinct from user-confirmed and grants no consent'}]}


def graph():
    return {'version': 1, 'name': 'spec-test', 'campaign': 'Implement a checked slice',
            'budget': {'maxNodes': 8}, 'nodes': [
                {'id': 'build', 'prompt': 'Implement the requirements.',
                 'contract': 'SliceV1', 'mutates': True},
                {'id': 'gate', 'after': 'build', 'prompt': 'Verify {{prev}}',
                 'contract': 'HarnessCheckV1', 'independent': True, 'verifies': 'build',
                 'haltWhen': 'exit != 0'}]}


class SpecificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.doc = packet()
        self.save()

    def save(self):
        (self.root / '.fluxpoint-spec.json').write_text(json.dumps(self.doc), encoding='utf-8')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              capture_output=True, text=True, encoding='utf-8',
                              env=dict(os.environ, PYTHONIOENCODING='utf-8'), timeout=20)

    def lock(self):
        self.save()
        r = self.run_cli('--lock')
        self.assertEqual(r.returncode, 0, r.stderr)

    def compile(self, ir=None):
        (self.root / 'WORK.md').write_text('```json graph-ir\n' + json.dumps(ir or graph()) + '\n```')
        return subprocess.run([sys.executable, str(COMPILER), 'WORK.md'], cwd=self.root,
                              capture_output=True, encoding='utf-8', timeout=20,
                              env=dict(os.environ, PYTHONIOENCODING='utf-8'))

    def test_lock_then_execute_actual_check(self):
        self.lock()
        r = self.run_cli('--run')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('checked', r.stdout)

    def test_check_requires_lock(self):
        r = self.run_cli('--check')
        self.assertEqual(r.returncode, 1)
        self.assertIn('lock', r.stderr)

    def test_spec_edit_invalidates_lock(self):
        self.lock()
        self.doc['goal'] = 'A different goal'
        self.save()
        self.assertIn('changed', self.run_cli('--check').stderr)

    def test_git_line_ending_conversion_preserves_lock(self):
        path = self.root / '.fluxpoint-spec.json'
        data = json.dumps(self.doc, indent=2).encode('utf-8')
        path.write_bytes(data.replace(b'\n', b'\r\n'))
        self.assertEqual(self.run_cli('--lock').returncode, 0)
        path.write_bytes(data)
        self.assertEqual(self.run_cli('--check').returncode, 0)

    def test_duplicate_json_keys_are_rejected(self):
        path = self.root / '.fluxpoint-spec.json'
        path.write_text(json.dumps(self.doc)[:-1] + ', "goal": "overwritten"}')
        self.assertEqual(self.run_cli('--lock').returncode, 1)

    def test_recursive_check_fails_without_spawning_forever(self):
        self.lock()
        r = subprocess.run([sys.executable, str(SCRIPT), '--run'], cwd=self.root,
                           env=dict(os.environ, FPL_SPEC_STACK=json.dumps([str(self.root.resolve())])),
                           capture_output=True, encoding='utf-8')
        self.assertEqual(r.returncode, 1)
        self.assertIn('recursive', r.stderr)

    def test_proof_rebaseline_invalidates_lock(self):
        baseline = self.root / '.fluxpoint-proof-baseline.json'
        baseline.write_text('{}')
        self.lock()
        baseline.write_text('{"counts": {}}')
        self.assertIn('baseline', self.run_cli('--check').stderr)

    def test_bad_decisions_and_mappings_cannot_lock(self):
        changes = [
            lambda d: d['decisions'].clear(),
            lambda d: d['decisions'][0].update(status='blocked'),
            lambda d: d['decisions'][0].update(status='approved'),
            lambda d: d['decisions'][0].update(dependsOn=['enforcement']),
            lambda d: d['decisions'][0].update(dependsOn=['absent']),
            lambda d: d['decisions'][0]['record'].update(evidence=[]),
            lambda d: d['decisions'][0]['costs'].pop(),
            lambda d: d['decisions'][0]['costs'][0].update(basis='measured', evidence=[]),
            lambda d: d['requirements'][0].update(checks=['absent']),
            lambda d: d['requirements'][0].update(decisions=['absent']),
            lambda d: d['requirements'].append(copy.deepcopy(d['requirements'][0])),
            lambda d: d['checks'][0].update(timeoutSeconds=True),
            lambda d: d['checks'][0].update(kind='formal-ish'),
            lambda d: d.update(unknown='typo'),
            lambda d: d['checks'][0].update(argz=['ignored']),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.doc = packet()
                change(self.doc)
                self.save()
                r = self.run_cli('--lock')
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_failed_missing_and_timed_out_checks_are_red(self):
        for argv, timeout in [(['{python}', '-c', 'raise SystemExit(7)'], 10),
                              (['fpl-nonexistent-checker'], 10),
                              (['{python}', '-c', 'import time; time.sleep(5)'], 1)]:
            with self.subTest(argv=argv):
                self.doc['checks'][0].update(argv=argv, timeoutSeconds=timeout)
                self.lock()
                self.assertEqual(self.run_cli('--run').returncode, 1)

    def test_formal_check_requires_and_runs_witness(self):
        self.doc['checks'][0]['kind'] = 'proof'
        self.save()
        self.assertIn('witness', self.run_cli('--lock').stderr)
        self.doc['checks'][0]['witness'] = ['{python}', '-c', 'raise SystemExit(6)']
        self.formal_obligation()
        self.arm_proof_guard()
        self.lock()
        r = self.run_cli('--run')
        self.assertEqual(r.returncode, 1)
        self.assertIn('witness exit 6', r.stdout)

    def arm_proof_guard(self):
        subprocess.run([sys.executable, str(PLUGIN / 'scripts/proof-guard.py'), '--baseline'],
                       cwd=self.root, check=True, capture_output=True)

    def test_formal_execution_requires_armed_escape_hatch_ratchet(self):
        self.formal_obligation()
        self.doc['checks'][0].update(kind='proof', witness=['{python}', '-c', 'print("reachable")'])
        self.lock()
        self.assertEqual(self.run_cli('--run').returncode, 1)
        self.arm_proof_guard()
        self.lock()
        self.assertEqual(self.run_cli('--run').returncode, 0)
        path = self.root / 'proof.dfy'
        path.write_text(path.read_text().replace('x := 1;', 'assume false; x := 1;'))
        self.assertEqual(self.run_cli('--run').returncode, 1)

    def formal_obligation(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / 'proof.dfy').write_text('method Positive() returns (x: int)\n ensures x > 0\n{ x := 1; }\n')
        subprocess.run(['git', 'add', 'proof.dfy'], cwd=self.root, check=True, capture_output=True)
        self.doc['checks'][0]['obligations'] = ['dafny:proof.dfy:Positive']

    def test_formal_check_must_name_obligations(self):
        self.doc['checks'][0].update(kind='proof', witness=['{python}', '-c', 'print("reachable")'])
        self.save()
        self.assertEqual(self.run_cli('--lock').returncode, 1)

    def test_obligation_drift_invalidates_lock_without_baseline(self):
        self.formal_obligation()
        self.doc['checks'][0].update(kind='proof', witness=['{python}', '-c', 'print("reachable")'])
        self.lock()
        path = self.root / 'proof.dfy'
        path.write_text(path.read_text().replace('x > 0', 'x >= 0'))
        r = self.run_cli('--check')
        self.assertEqual(r.returncode, 1)
        self.assertIn('obligation', r.stderr)

    def test_unknown_obligation_cannot_lock(self):
        self.doc['checks'][0]['obligations'] = ['dafny:absent.dfy:Missing']
        self.save()
        self.assertEqual(self.run_cli('--lock').returncode, 1)

    def test_mutating_graph_needs_packet(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        r = self.compile()
        self.assertEqual(r.returncode, 1)
        self.assertIn('spec', r.stderr)

    def test_compiled_context_and_summary_bind_locked_spec(self):
        self.lock()
        r = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr)
        digest = hashlib.sha256((self.root / '.fluxpoint-spec.json').read_bytes()).hexdigest()
        self.assertIn(digest, r.stdout)
        self.assertIn('reject-missing', r.stdout)
        self.assertIn('specification:', r.stdout)

    def test_read_only_graph_needs_no_packet(self):
        ir = graph()
        ir['nodes'] = [{'id': 'read', 'prompt': 'Read and report findings.', 'contract': 'FindingsV1'}]
        (self.root / '.fluxpoint-spec.json').unlink()
        self.assertEqual(self.compile(ir).returncode, 0)

    def test_legacy_absence_is_explicit_only_for_runner(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        r = self.run_cli('--run', '--if-present')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('legacy', r.stdout)
        (self.root / 'WORK.md').write_text('SPEC: .fluxpoint-spec.json\n')
        self.assertEqual(self.run_cli('--run', '--if-present').returncode, 1)

    def test_record_refuses_stale_spec_identity(self):
        self.lock()
        summary = {'outcome': 'COMPLETE', 'specification': {'sha256': 'wrong'}}
        (self.root / 'result.json').write_text(json.dumps(summary))
        r = subprocess.run([sys.executable, str(PLUGIN / 'scripts/record-run.py'),
                            '--run-id', 'spec-stale', '--result', 'result.json'],
                           cwd=self.root, capture_output=True, encoding='utf-8',
                           env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('spec', r.stderr)
        self.assertFalse((self.root / '.claude/fluxpoint/runs/spec-stale.json').exists())

    def test_record_cannot_omit_identity_when_packet_exists(self):
        self.lock()
        (self.root / 'result.json').write_text('{"outcome":"COMPLETE"}')
        r = subprocess.run([sys.executable, str(PLUGIN / 'scripts/record-run.py'),
                            '--run-id', 'spec-missing', '--result', 'result.json'],
                           cwd=self.root, capture_output=True, encoding='utf-8',
                           env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        self.assertEqual(r.returncode, 1)

    def test_check_cannot_change_the_spec_during_execution(self):
        self.doc['checks'][0]['argv'] = ['{python}', '-c',
            'from pathlib import Path; Path(".fluxpoint-spec.json").write_text("{}")']
        self.lock()
        self.assertEqual(self.run_cli('--run').returncode, 1)

    def test_missing_aiken_and_apalache_are_red(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else 'bash'
        for tool in ('aiken', 'apalache-mc'):
            with self.subTest(tool=tool):
                manifest = self.root / 'aiken.toml'
                if tool == 'aiken':
                    manifest.write_text('name = "test/project"')
                elif manifest.exists():
                    manifest.unlink()
                env = dict(os.environ, FPL_PLUGIN_ROOT=PLUGIN.as_posix(),
                           FPL_PY=Path(sys.executable).as_posix(), PYTHONIOENCODING='utf-8')
                if tool == 'apalache-mc':
                    env['FPL_APALACHE_ARGS'] = '--inv=Inv Spec.tla'
                command = 'function command() { if [ "$1" = -v ] && [ "$2" = "' + tool + '" ]; then return 1; fi; builtin command "$@"; }; export -f command; bash "$1" --full'
                r = subprocess.run([bash, '-c', command, 'probe', (PLUGIN / 'templates/harness.sh').as_posix()],
                                   cwd=self.root, env=env, capture_output=True, timeout=30)
                self.assertNotEqual(r.returncode, 0)

    def test_emitted_javascript_passes_packet_to_agents(self):
        self.lock()
        ir = graph()
        ir['treeGuard'] = False
        ir['nodes'].append({'id': 'review', 'after': 'gate', 'prompt': 'Review the requirements and {{prev}}.',
                            'contract': 'FindingsV1', 'verify': 'panel:3',
                            'verifyOver': 'findings', 'expectItems': 1})
        r = self.compile(ir)
        self.assertEqual(r.returncode, 0, r.stderr)
        prelude = '''
const prompts = [];
const args = {_base: {sha: 'abc', branch: 'main'}};
const log = () => {}, phase = () => {};
const budget = {remaining: () => 1000000};
const workflow = {};
const parallel = tasks => Promise.all(tasks.map(task => task()));
const agent = async (prompt) => {prompts.push(prompt); return {exit: 0, branch: 'main', findings: [{claim: 'review'}], refuted: false};};
'''
        script = prelude + '(async () => {\n' + r.stdout.replace('export const meta', 'const meta') + '''
})().then(result => {console.log(JSON.stringify({result, prompts}));});
'''
        (self.root / 'run.cjs').write_text(script, encoding='utf-8')
        result = subprocess.run(['node', 'run.cjs'], cwd=self.root, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed['result']['outcome'], 'COMPLETE')
        self.assertEqual(len(observed['prompts']), 6)
        self.assertTrue(all('reject-missing' in p for p in observed['prompts']))
        self.assertIn('sha256', observed['result']['specification'])

    def test_kani_without_cargo_cannot_pass_full_harness(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        (self.root / 'Cargo.toml').write_text('[package]\nname="test"\nversion="0.1.0"\n')
        (self.root / 'src').mkdir()
        (self.root / 'src/lib.rs').write_text('#[kani::proof]\nfn property() { assert!(true); }\n')
        env = dict(os.environ, FPL_PLUGIN_ROOT=PLUGIN.as_posix(),
                   FPL_PY=Path(sys.executable).as_posix(), PYTHONIOENCODING='utf-8')
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else 'bash'
        command = 'function command() { if [ "$1" = -v ] && [ "$2" = cargo ]; then return 1; fi; builtin command "$@"; }; export -f command; bash "$1" --full'
        r = subprocess.run([bash, '-c', command, 'probe', (PLUGIN / 'templates/harness.sh').as_posix()],
                           cwd=self.root, env=env, capture_output=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)

    def test_harness_requires_spec_when_work_declares_it(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        (self.root / 'WORK.md').write_text('SPEC: .fluxpoint-spec.json\n')
        env = dict(os.environ, FPL_PLUGIN_ROOT=PLUGIN.as_posix(),
                   FPL_PY=Path(sys.executable).as_posix(), PYTHONIOENCODING='utf-8')
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else 'bash'
        r = subprocess.run([bash, (PLUGIN / 'templates/harness.sh').as_posix(), '--full'],
                           cwd=self.root, env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('spec', r.stderr)

    def test_spec_marker_parsers_agree_without_installed_runner(self):
        (self.root / '.fluxpoint-spec.json').unlink()
        env = dict(os.environ, FPL_PLUGIN_ROOT='', CLAUDE_PLUGIN_ROOT='', PLUGIN_ROOT='',
                   FPL_PY=Path(sys.executable).as_posix(), PYTHONIOENCODING='utf-8')
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else 'bash'
        command = 'function find() { return 1; }; export -f find; bash "$1" --full'
        cases = [(f'SPEC:{gap}.fluxpoint-spec.json\n', 1) for gap in ('', ' ', '  ', '\t')]
        cases += [('SPEC: .fluxpoint-spec.json \t\n', 1),
                  ('SPEC:\t.fluxpoint-spec.json\r\n', 1),
                  ('SPEC:\n.fluxpoint-spec.json\n', 0),
                  ('SPEC: xfluxpoint-specxjson\n', 0)]
        for marker, expected in cases:
            with self.subTest(marker=marker):
                (self.root / 'WORK.md').write_bytes(marker.encode('utf-8'))
                runner = self.run_cli('--run', '--if-present')
                harness = subprocess.run([bash, '-c', command, 'probe', (PLUGIN / 'templates/harness.sh').as_posix()],
                                         cwd=self.root, env=env, capture_output=True, timeout=30)
                self.assertEqual(runner.returncode, expected, runner.stderr)
                self.assertEqual(harness.returncode, expected, harness.stderr)

    def test_unavailable_dafny_cannot_pass_full_harness(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / 'proof.dfy').write_text('method Positive() returns (x: int)\n ensures x > 0\n{ x := 0; }\n')
        subprocess.run(['git', 'add', 'proof.dfy'], cwd=self.root, check=True, capture_output=True)
        (self.root / '.fluxpoint-spec.json').unlink()
        env = dict(os.environ, FPL_PLUGIN_ROOT=PLUGIN.as_posix(),
                   FPL_PY=Path(sys.executable).as_posix(), PYTHONIOENCODING='utf-8')
        bash = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else 'bash'
        command = 'function command() { if [ "$1" = -v ] && [ "$2" = dafny ]; then return 1; fi; builtin command "$@"; }; export -f command; bash "$1" --full'
        r = subprocess.run([bash, '-c', command, 'probe', (PLUGIN / 'templates/harness.sh').as_posix()],
                           cwd=self.root, env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertNotEqual(r.returncode, 0)

    def test_decision_section_mention_does_not_waive_statement_drift(self):
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        proof = self.root / 'proof.dfy'
        proof.write_text('method Positive() returns (x: int)\n ensures x > 0\n{ x := 1; }\n')
        subprocess.run(['git', 'add', 'proof.dfy'], cwd=self.root, check=True, capture_output=True)
        guard = PLUGIN / 'scripts/spec-guard.py'
        subprocess.run([sys.executable, str(guard), '--baseline'], cwd=self.root, check=True, capture_output=True)
        proof.write_text(proof.read_text().replace('x > 0', 'x >= 0'))
        (self.root / 'WORK.md').write_text('## Decisions\nStill review dafny:proof.dfy:Positive.\n')
        r = subprocess.run([sys.executable, str(guard), '--check'], cwd=self.root, capture_output=True)
        self.assertEqual(r.returncode, 1)


if __name__ == '__main__':
    unittest.main()
