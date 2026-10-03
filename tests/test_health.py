import base64
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import health

NOW = dt.datetime(2026, 10, 3, 12, tzinfo=health.UTC)


def run(identifier=1, event='schedule', age=1, conclusion='success', status='completed', branch='main'):
    return {'id': identifier, 'event': event, 'head_branch': branch, 'status': status,
            'conclusion': conclusion, 'created_at': health.iso(NOW - dt.timedelta(hours=age)),
            'updated_at': health.iso(NOW - dt.timedelta(hours=age)),
            'html_url': f'https://github.com/example/package/actions/runs/{identifier}'}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.repository = {'full_name': 'example/package', 'default_branch': 'main',
                           'created_at': health.iso(NOW - dt.timedelta(days=100))}
        self.workflow = {'state': 'active'}

    def assess(self, recent=None, checks=None):
        good = run()
        return health.assess(self.workflow, self.repository, recent if recent is not None else [good],
                             checks if checks is not None else [(good, NOW - dt.timedelta(hours=1))], NOW)

    def test_recent_upstream_check_and_publish_are_healthy(self):
        status = self.assess()
        self.assertEqual(status['status'], 'healthy')
        self.assertEqual(status['last_successful_upstream_check'], '2026-10-03T11:00:00Z')
        self.assertEqual(status['reasons'], [])

    def test_push_build_does_not_replace_a_missing_upstream_check(self):
        status = self.assess([run(event='push')], [])
        self.assertEqual(status['status'], 'unhealthy')
        self.assertIsNone(status['last_successful_upstream_check'])

    def test_repeated_publish_rerun_does_not_refresh_old_check_timestamp(self):
        status = self.assess([run(age=0)], [(run(age=0), NOW - dt.timedelta(hours=49))])
        self.assertEqual(status['status'], 'unhealthy')
        self.assertIn('older than 48', status['reasons'][0])

    def test_publish_failure_alerts_despite_fresh_upstream_check(self):
        status = self.assess([run(2, event='push', age=0, conclusion='failure'), run()])
        self.assertEqual(status['status'], 'unhealthy')
        self.assertTrue(any('ended with failure' in reason for reason in status['reasons']))

    def test_failed_upstream_check_is_not_hidden_by_later_successful_push(self):
        failed = run(2, age=1, conclusion='failure')
        good = run(1, age=24)
        status = self.assess([run(3, event='push', age=0), failed, good],
                             [(failed, None), (good, NOW - dt.timedelta(hours=24))])
        self.assertEqual(status['status'], 'unhealthy')
        self.assertTrue(any('version check' in reason for reason in status['reasons']))

    def test_disabled_workflow_alerts_even_with_recent_success(self):
        self.workflow['state'] = 'disabled_inactivity'
        self.assertIn('disabled_inactivity', self.assess()['reasons'][0])

    def test_stuck_run_alerts_but_recent_in_progress_run_does_not(self):
        stuck = run(2, age=3, status='queued', conclusion=None)
        good = run(1, age=24)
        checks = [(good, NOW - dt.timedelta(hours=24))]
        self.assertEqual(self.assess([stuck, good], checks)['status'], 'unhealthy')
        stuck['created_at'] = health.iso(NOW - dt.timedelta(minutes=10))
        self.assertEqual(self.assess([stuck, good], checks)['status'], 'healthy')

    def test_pull_request_success_does_not_mask_main_failure(self):
        failed = run(2, event='push', age=1, conclusion='failure')
        pull_request = run(3, event='pull_request', age=0)
        self.assertEqual(self.assess([pull_request, failed])['status'], 'unhealthy')

    def test_new_repository_has_initialization_grace_period(self):
        self.repository['created_at'] = health.iso(NOW - dt.timedelta(hours=2))
        self.assertEqual(self.assess([], [])['status'], 'initializing')

    def test_upstream_step_time_is_used_instead_of_later_build_time(self):
        jobs = [{'steps': [
            {'name': health.CHECK_STEP, 'conclusion': 'success', 'completed_at': '2026-10-03T10:00:00Z'},
            {'name': health.LEGACY_STEP, 'conclusion': 'success', 'completed_at': '2026-10-03T11:00:00Z'},
        ]}]
        self.assertEqual(health.check_time(jobs), health.timestamp('2026-10-03T10:00:00Z'))

    def test_legacy_scheduled_runs_are_recognized(self):
        jobs = [{'steps': [{'name': health.LEGACY_STEP, 'conclusion': 'success',
                            'completed_at': '2026-10-03T10:00:00Z'}]}]
        self.assertEqual(health.check_time(jobs), health.timestamp('2026-10-03T10:00:00Z'))


class FakeGitHub:
    def __init__(self):
        self.issue = None
        self.writes = []
        self.branch_exists = False
        self.content = None

    def call(self, path='', method='GET', body=None):
        if method != 'GET':
            self.writes.append((path, method, body))
            if path == 'issues':
                self.issue = {'number': 42, 'state': 'open', **body}
            elif path == 'issues/42':
                self.issue.update(body)
            elif path == 'git/refs':
                self.branch_exists = True
            elif path == 'contents/health.json':
                self.content = {'sha': 'status-sha', 'content': body['content']}
            return {}
        if path.startswith('issues?'):
            return [self.issue] if self.issue else []
        if path == '':
            return {'default_branch': 'main'}
        if path == 'git/ref/heads/main':
            return {'object': {'sha': 'main-sha'}}
        if path == f'git/ref/heads/{health.BRANCH}':
            if not self.branch_exists:
                raise health.NotFound(path)
            return {'object': {'sha': 'status-sha'}}
        if path.startswith('contents/health.json?'):
            if self.content is None:
                raise health.NotFound(path)
            return self.content
        raise AssertionError(f'Unexpected API request: {path}')


class PublicationTests(unittest.TestCase):
    def status(self, state):
        repository = {'full_name': 'example/package', 'default_branch': 'main',
                      'created_at': health.iso(NOW - dt.timedelta(days=100))}
        result = health.assess({'state': 'active'}, repository, [run()], [(run(), NOW)], NOW)
        result['status'] = state
        result['reasons'] = ['Simulated failure'] if state == 'unhealthy' else []
        return result

    def test_one_issue_is_created_updated_closed_and_reopened(self):
        api = FakeGitHub()
        health.sync_issue(api, self.status('unhealthy'))
        self.assertEqual(api.issue['state'], 'open')
        self.assertIn(health.MARKER, api.issue['body'])
        health.sync_issue(api, self.status('unhealthy'))
        health.sync_issue(api, self.status('healthy'))
        self.assertEqual(api.issue['state'], 'closed')
        health.sync_issue(api, self.status('unhealthy'))
        self.assertEqual(api.issue['state'], 'open')
        self.assertEqual(sum(method == 'POST' for _, method, _ in api.writes), 1)

    def test_initialization_does_not_close_existing_alert(self):
        api = FakeGitHub()
        health.sync_issue(api, self.status('unhealthy'))
        before = len(api.writes)
        health.sync_issue(api, self.status('initializing'))
        self.assertEqual(len(api.writes), before)
        self.assertEqual(api.issue['state'], 'open')

    def test_healthy_status_without_incident_does_not_create_issue(self):
        api = FakeGitHub()
        health.sync_issue(api, self.status('healthy'))
        self.assertEqual(api.writes, [])

    def test_heartbeat_updates_separate_branch_and_preserves_file_sha(self):
        api = FakeGitHub()
        status = self.status('healthy')
        health.publish_status(api, status)
        health.publish_status(api, status)
        commits = [body for path, _, body in api.writes if path == 'contents/health.json']
        self.assertEqual(len(commits), 2)
        self.assertTrue(all(body['branch'] == health.BRANCH for body in commits))
        self.assertEqual(commits[1]['sha'], 'status-sha')
        self.assertEqual(json.loads(base64.b64decode(commits[0]['content'])), status)


if __name__ == '__main__':
    unittest.main()
