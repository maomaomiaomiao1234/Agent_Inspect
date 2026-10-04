"""Review-only reproductions. No real model requests or Docker containers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'examples/smolagents')]

import httpx
from agent_server import AgentTarget, create_app
from agent_trace_review.assessment_contracts import AssessmentSuite, SuiteGenerationInput, TargetDefinition
from agent_trace_review.assessment_store import AssessmentStore
from agent_trace_review.assessments import AssessmentManager, prepare_assessment, run_assessment
from agent_trace_review.storage import Store
from agent_trace_review.suite_generation import generate_suite
import agent_trace_review.target_client as tc


def execute(suite, transport):
    with tempfile.TemporaryDirectory(prefix='agent-inspect-review-') as directory:
        db = AssessmentStore(Store(directory))
        target = TargetDefinition(id='review', endpoint='http://testserver', demo=True)
        job, repo = prepare_assessment(db, target, suite)
        return run_assessment(db, job['id'], target, suite, repo, transport=transport)


def history_check(history):
    raw = generate_suite(SuiteGenerationInput(cases=5)).model_dump()
    raw['cases'] = [raw['cases'][4]]
    raw['cases'][0]['turns'][1]['history'] = history
    target = AgentTarget()
    try:
        job = execute(AssessmentSuite.model_validate(raw), httpx.ASGITransport(app=create_app(target)))
        return {k: job['results'][0][k] for k in ('outcome', 'execution_state', 'error')}
    finally:
        target.close()


def budget_check():
    raw = generate_suite(SuiteGenerationInput(cases=1)).model_dump()
    raw['budgets'][0]['max_output_tokens'] = 5
    raw['cases'][0]['turns'] *= 3
    correct = next(r['value'] for r in raw['cases'][0]['profile']['rules'] if r['id'] == 'correct')
    calls = []
    def respond(request):
        calls.append(json.loads(request.content)['budget']['max_output_tokens'])
        response = {'protocol': 'agent-review/target-v1', 'output': {'answer': correct}}
        if len(calls) > 1:
            response['usage'] = {'tokens': {'input': 1, 'output': 6, 'total': 7}}
        return httpx.Response(200, json=response)
    job = execute(AssessmentSuite.model_validate(raw), httpx.MockTransport(respond))
    row = job['results'][0]
    return {'requested_budgets': calls, 'reported_output_tokens_lower_bound': 12,
            **{k: row[k] for k in ('outcome', 'execution_state', 'usage')}}


def missing_protocol_check():
    suite = generate_suite(SuiteGenerationInput(cases=1))
    correct = next(r.value for r in suite.cases[0].profile.rules if r.id == 'correct')
    job = execute(suite, httpx.MockTransport(lambda _: httpx.Response(200, json={'output': {'answer': correct}})))
    return {k: job['results'][0][k] for k in ('outcome', 'execution_state', 'error')}


def crash_child(directory):
    directory = Path(directory)
    image = 'sha256:' + 'a' * 64
    def docker(*args):
        if args[:2] == ('image', 'inspect'):
            return json.dumps([{'Id': image, 'Config': {}}])
        if args[0] == 'run':
            (directory / 'simulated-container-running').write_text(args[args.index('--name') + 1])
            return 'fake-container'
        if args[0] == 'inspect':
            return json.dumps([{'NetworkSettings': {'Ports': {'9000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '54321'}]}}}])
        if args[:2] == ('rm', '-f'):
            (directory / 'simulated-container-running').unlink(missing_ok=True)
        return ''
    tc._docker = docker
    tc.TargetClient.health = lambda *a, **kw: {'status': 'ready'}
    target = TargetDefinition(id='crash-review', deployment={'image': image, 'port': 9000}, health_path='/health')
    db = AssessmentStore(Store(directory / 'data'))
    with tc.deployed_target(target) as (_, deployment):
        job = db.create_job({'deployment': deployment})
        db.claim(job['id'])
        (directory / 'job-id').write_text(job['id'])
        os._exit(23)


def crash_check():
    with tempfile.TemporaryDirectory(prefix='agent-inspect-crash-review-') as directory:
        proc = subprocess.run([sys.executable, __file__, '--crash-child', directory], capture_output=True)
        assert proc.returncode == 23, proc.stderr.decode()
        directory = Path(directory)
        manager = AssessmentManager(Store(directory / 'data'), {})
        try:
            job = manager.db.job((directory / 'job-id').read_text())
            return {'simulation': True, 'child_exit': proc.returncode, 'job_state': job['state'],
                    'container_still_running': (directory / 'simulated-container-running').exists(),
                    'persisted_deployment_fields': sorted(job['deployment'])}
        finally:
            manager.close()


if len(sys.argv) > 1 and sys.argv[1] == '--crash-child':
    crash_child(sys.argv[2])
else:
    result = {'smolagents_full_history': history_check('full'),
              'smolagents_current_history_control': history_check('current'),
              'partial_usage_budget': budget_check(),
              'missing_protocol': missing_protocol_check(),
              'crash_recovery': crash_check()}
    print(json.dumps(result, indent=2, ensure_ascii=False))
