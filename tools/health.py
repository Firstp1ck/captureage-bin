#!/usr/bin/env python3
"""Check upstream-update freshness and AUR publishing, optionally recording alerts."""
import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, urlencode

UTC = dt.timezone.utc
BRANCH = 'automation-health'
MARKER = '<!-- captureage-automation-health -->'
ISSUE_TITLE = 'CaptureAge automation needs attention'
CHECK_STEP = 'Check upstream CaptureAge version'
LEGACY_STEP = 'Update and build in Arch Linux'
EVENTS = ('schedule', 'workflow_dispatch', 'push')
FAILURES = {'failure', 'timed_out', 'action_required', 'startup_failure'}


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')


class NotFound(Exception):
    pass


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository, flags=re.ASCII):
            raise ValueError('Expected OWNER/REPOSITORY')
        self.repository = repository

    def call(self, path='', method='GET', body=None):
        route = f'repos/{self.repository}' + (f'/{path}' if path else '')
        command = ['gh', 'api', route, '--method', method]
        data = None
        if body is not None:
            command += ['--input', '-']
            data = json.dumps(body)
        result = subprocess.run(command, input=data, text=True, capture_output=True)
        if result.returncode:
            if '(HTTP 404)' in result.stderr:
                raise NotFound(route)
            raise RuntimeError(f'GitHub API failed: {result.stderr.strip()}')
        return json.loads(result.stdout) if result.stdout.strip() else None


def runs(api, branch, **filters):
    query = urlencode({'branch': branch, 'per_page': 100, 'exclude_pull_requests': 'true', **filters})
    return api.call(f'actions/workflows/update.yml/runs?{query}')['workflow_runs']


def check_time(jobs):
    # A push build does not check upstream; callers only pass scheduled/manual runs.
    for name in (CHECK_STEP, LEGACY_STEP):
        times = [timestamp(step['completed_at']) for job in jobs for step in job.get('steps', [])
                 if step['name'] == name and step.get('conclusion') == 'success' and step.get('completed_at')]
        if times:
            return max(times)
    return None


def assess(workflow, repository, recent, checks, now, stale_hours=48):
    recent = [run for run in recent if run.get('event') in EVENTS
              and run.get('head_branch') == repository['default_branch']]
    completed = sorted((run for run in recent if run['status'] == 'completed'),
                       key=lambda run: timestamp(run['updated_at']), reverse=True)
    good_checks = [(checked, run) for run, checked in checks if checked is not None]
    last_check, check_run = max(good_checks, key=lambda item: item[0]) if good_checks else (None, None)
    publications = [run for run in completed if run.get('conclusion') == 'success']
    reasons = []
    if workflow['state'] != 'active':
        reasons.append(f"Update workflow is {workflow['state']}.")
    if last_check is None:
        if now - timestamp(repository['created_at']) > dt.timedelta(hours=stale_hours):
            reasons.append('No successful upstream version check has been recorded.')
    elif now - last_check > dt.timedelta(hours=stale_hours):
        reasons.append(f'Last successful upstream check is older than {stale_hours:g} hours.')
    if completed and completed[0].get('conclusion') in FAILURES:
        reasons.append(f"Latest update/publish run ended with {completed[0]['conclusion']}.")
    if checks:
        latest_attempt, checked = max(checks, key=lambda item: timestamp(item[0]['created_at']))
        if checked is None and latest_attempt.get('conclusion') in FAILURES:
            reasons.append('Latest scheduled/manual run did not complete its upstream version check.')
    if recent:
        latest = max(recent, key=lambda run: timestamp(run['created_at']))
        if latest['status'] != 'completed' and now - timestamp(latest['created_at']) > dt.timedelta(hours=2):
            reasons.append('An update/publish run has been queued or running for more than two hours.')
    state = 'unhealthy' if reasons else ('healthy' if last_check else 'initializing')
    return {
        'repository': repository['full_name'], 'checked_at': iso(now), 'status': state,
        'stale_after_hours': stale_hours, 'workflow_state': workflow['state'],
        'last_successful_upstream_check': iso(last_check) if last_check else None,
        'upstream_check_run_url': check_run['html_url'] if check_run else None,
        'latest_completed_run_url': completed[0]['html_url'] if completed else None,
        'last_successful_publish': publications[0]['updated_at'] if publications else None,
        'last_successful_publish_run_url': publications[0]['html_url'] if publications else None,
        'reasons': reasons,
    }


def collect(api, now, stale_hours=48):
    repository = api.call()
    workflow = api.call('actions/workflows/update.yml')
    branch = repository['default_branch']
    recent = runs(api, branch)
    check_runs = []
    for event in ('schedule', 'workflow_dispatch'):
        check_runs.extend(runs(api, branch, event=event, per_page=10))
    checks = []
    for run in check_runs:
        jobs = api.call(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100")['jobs']
        checks.append((run, check_time(jobs)))
    # Include older successful publications even if 100 recent failures crowd them out.
    recent.extend(runs(api, branch, status='success', per_page=1))
    return assess(workflow, repository, recent, checks, now, stale_hours)


def report(status):
    lines = [MARKER, '## CaptureAge automation health', '', f"Status: **{status['status']}**",
             f"Evaluated: {status['checked_at']}", '',
             f"Last successful upstream check: {status['last_successful_upstream_check'] or 'not yet recorded'}",
             f"Last successful publishing run: {status['last_successful_publish'] or 'not yet recorded'}", '']
    lines.extend(f'- {reason}' for reason in status['reasons'])
    for label, key in (('Upstream check', 'upstream_check_run_url'),
                       ('Latest completed run', 'latest_completed_run_url'),
                       ('Last successful publishing run', 'last_successful_publish_run_url')):
        if status[key]:
            lines.append(f'- [{label}]({status[key]})')
    if status['status'] == 'unhealthy':
        lines += ['', 'Inspect the linked run, fix the failure, and rerun **Update and publish CaptureAge**.',
                  'If the workflow is disabled, re-enable it in the Actions tab and run it manually.',
                  'The monitor closes this issue automatically when the checks recover.']
    else:
        lines += ['', 'The monitor will alert again if update checks or publishing become unhealthy.']
    return '\n'.join(lines) + '\n'


def sync_issue(api, status):
    issue = None
    for page in range(1, 11):
        items = api.call(f'issues?state=all&sort=created&direction=desc&per_page=100&page={page}')
        issue = next((item for item in items if 'pull_request' not in item
                      and MARKER in (item.get('body') or '') and item['title'] == ISSUE_TITLE), None)
        if issue or len(items) < 100:
            break
    if status['status'] == 'unhealthy':
        body = {'title': ISSUE_TITLE, 'body': report(status)}
        if issue:
            body['state'] = 'open'
            api.call(f"issues/{issue['number']}", 'PATCH', body)
        else:
            api.call('issues', 'POST', body)
    elif status['status'] == 'healthy' and issue and issue['state'] == 'open':
        api.call(f"issues/{issue['number']}", 'PATCH',
                 {'body': report(status), 'state': 'closed', 'state_reason': 'completed'})


def publish_status(api, status):
    try:
        api.call(f'git/ref/heads/{BRANCH}')
    except NotFound:
        default = api.call()['default_branch']
        head = api.call(f'git/ref/heads/{quote(default, safe="")}')['object']['sha']
        api.call('git/refs', 'POST', {'ref': f'refs/heads/{BRANCH}', 'sha': head})
    route = 'contents/health.json'
    try:
        existing = api.call(f'{route}?ref={BRANCH}')
    except NotFound:
        existing = None
    content = (json.dumps(status, indent=2) + '\n').encode()
    body = {'message': 'Record CaptureAge automation health', 'branch': BRANCH,
            'content': base64.b64encode(content).decode()}
    if existing:
        body['sha'] = existing['sha']
    api.call(route, 'PUT', body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--stale-hours', type=float, default=48)
    parser.add_argument('--publish', action='store_true', help='Update the status branch and alert issue')
    args = parser.parse_args()
    if not args.repo or args.stale_hours <= 0:
        parser.error('Provide --repo OWNER/REPOSITORY and a positive freshness threshold')
    api = GitHub(args.repo)
    status = collect(api, dt.datetime.now(UTC), args.stale_hours)
    print(json.dumps(status, indent=2))
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(summary).open('a') as output:
            output.write(report(status))
    if args.publish:
        publish_status(api, status)
        sync_issue(api, status)
    return 1 if status['status'] == 'unhealthy' else 0


if __name__ == '__main__':
    raise SystemExit(main())
