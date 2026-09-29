"""Synthetic Fast mode ranking, persistence and HTTP contracts; no external calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request

import training_fast as fast
import context_service as svc

REVISION = 'a' * 40
DAY = '2026-09-28'


def snapshot(count=4):
    return {'meta': {'origin_full_sha': REVISION}, 'bp': 'PRIVATE_READINGS',
        'training_detail': {'prescriptions': [{'date': DAY, 'recommendation': {
            'date': DAY, 'template': {'label': 'Example workout',
                'minimum_version': 'Keep the main push and pull patterns.',
                'exercises': [{'exercise': name, 'equipment': 'example equipment', 'work': '2 sets x 10',
                    'load': 'PRIVATE_LOAD', 'notes': 'PRIVATE_NOTE',
                    'progression_result': {'evidence_sets': ['PRIVATE_HISTORY']}}
                    for name in ['Chest press', 'Another chest press', 'Leg press', 'Row'][:count]]}}}]}}


def score_response(request):
    scores = [4, 3, 1, 2]
    return {'model': 'jev-test', 'answers': {key: {'type': 'score', 'score': scores[int(key[7:])],
        'probabilities': {str(i): float(i == scores[int(key[7:])]) for i in range(5)}}
        for key in request['questions']}}


def choice_response(request, winner, probability=1):
    key, spec = next(iter(request['questions'].items()))
    options = list(spec['criteria'])
    return {'model': 'jev-test', 'answers': {key: {'type': 'choice', 'choice': winner,
        'probabilities': {option: probability if option == winner else (1 - probability) / (len(options) - 1)
                          for option in options}}}}


def plan(count=4):
    return fast.select_plan(snapshot(count), DAY, REVISION, 'jev-test')


class RankingTests(unittest.TestCase):
    def test_scores_then_contextual_choices_then_threshold(self):
        p = plan()
        before = fast.initial(p)
        first_request = fast.question(p, before)
        self.assertEqual(len(first_request['questions']), 4)
        state = fast.advance(p, before, score_response(first_request))
        self.assertEqual(before['ranked'], [])
        self.assertEqual([x['id'] for x in state['ranked']], ['e0'])
        request = fast.question(p, state)
        self.assertEqual(set(request['questions']['next']['criteria']), {'e1', 'e2', 'e3'})
        self.assertEqual([x['id'] for x in request['state']['locked_in']], ['e0'])
        # A lower independent score wins next because it complements the prefix.
        state = fast.advance(p, state, choice_response(request, 'e2', .8))
        self.assertEqual([x['id'] for x in state['ranked']], ['e0', 'e2'])
        request = fast.question(p, state)
        self.assertEqual(set(request['questions']['next']['criteria']), {'e1', 'e3'})
        self.assertEqual([x['id'] for x in request['state']['locked_in']], ['e0', 'e2'])
        state = fast.advance(p, state, choice_response(request, 'e3', .9))
        self.assertEqual([x['id'] for x in state['ranked']], ['e0', 'e2', 'e3', 'e1'])
        self.assertEqual(state['phase'], 'threshold')
        self.assertIsNone(state['must_count'])
        request = fast.question(p, state)
        self.assertEqual(request['questions']['must']['criteria']['keep_2']['keep'], ['e0', 'e2'])
        state = fast.advance(p, state, choice_response(request, 'keep_2', .8))
        self.assertEqual((state['phase'], state['must_count']), ('complete', 2))
        self.assertFalse(state['threshold_uncertain'])

    def test_one_or_two_exercises_skip_unnecessary_choice(self):
        for count in (1, 2):
            p = plan(count)
            state = fast.initial(p)
            state = fast.advance(p, state, score_response(fast.question(p, state)))
            self.assertEqual((state['phase'], len(state['ranked'])), ('threshold', count))

    def test_cutoff_supports_none_all_unknown_and_close_answers(self):
        p = plan(2)
        base = fast.advance(p, fast.initial(p), score_response(fast.question(p, fast.initial(p))))
        for winner, probability, count, uncertain in [('keep_0', .9, 0, False),
                ('keep_2', .9, 2, False), ('undetermined', .9, None, True), ('keep_1', .4, 1, True)]:
            with self.subTest(winner=winner):
                result = fast.advance(p, base, choice_response(fast.question(p, base), winner, probability))
                self.assertEqual((result['must_count'], result['threshold_uncertain']), (count, uncertain))

    def test_live_api_rounding_uses_reported_score(self):
        p = plan(1)
        state = fast.initial(p)
        answer = {'model':'jev-test','answers': {'score_e0': {'type':'score','score':.99,
            'probabilities': {'0':.14,'1':.75,'2':.08,'3':.02,'4':.01}}}}
        result = fast.advance(p, state, answer)
        self.assertEqual(result['ranked'][0]['score'], .99)
        self.assertEqual(fast._distribution({'probabilities': {'a':.33,'b':.33,'c':.33}}, ['a','b','c']), {'a':.33,'b':.33,'c':.33})

    def test_ties_use_stable_original_order(self):
        p = plan()
        base = fast.initial(p)
        response = score_response(fast.question(p, base))
        response['answers']['score_e1'] = deepcopy(response['answers']['score_e0'])
        state = fast.advance(p, base, response)
        self.assertEqual(state['ranked'][0]['id'], 'e0')
        request = fast.question(p, state)
        answer = choice_response(request, 'e2', .5)
        answer['answers']['next']['probabilities'] = {'e1': .5, 'e2': .5, 'e3': 0}
        result = fast.advance(p, state, answer)
        self.assertEqual(result['ranked'][1]['id'], 'e1')

    def test_outbound_context_omits_private_history_and_readings(self):
        p = plan()
        text = json.dumps(fast.question(p, fast.initial(p)))
        self.assertNotIn('PRIVATE_', text)
        self.assertNotIn(REVISION, text)
        self.assertIn('Keep the main push and pull', text)
        self.assertIn('2 sets x 10', text)

    def test_plan_bound_to_full_prescription_revision_and_model(self):
        original = plan()['plan_id']
        changed = snapshot()
        changed['training_detail']['prescriptions'][0]['recommendation']['template']['exercises'][0]['load'] = 'changed'
        self.assertNotEqual(fast.select_plan(changed, DAY, REVISION, 'jev-test')['plan_id'], original)
        self.assertNotEqual(fast.select_plan(snapshot(), DAY, REVISION, 'jev-new')['plan_id'], original)
        for day, revision in [('bad-date', REVISION), (DAY, 'b' * 40), (DAY, 'example')]:
            with self.assertRaises(fast.FastError):
                fast.select_plan(snapshot(), day, revision, 'jev-test')

    def test_incomplete_invalid_and_nonfinite_answers_are_rejected(self):
        p = plan()
        base = fast.initial(p)
        valid = score_response(fast.question(p, base))
        variants = [{}, {'answers': {}}, deepcopy(valid), deepcopy(valid), deepcopy(valid), deepcopy(valid)]
        variants[2]['answers']['score_e0']['probabilities']['4'] = float('nan')
        variants[3]['answers']['score_e0']['probabilities']['4'] = 0
        variants[4]['answers']['score_e0']['score'] = 1
        variants[5]['answers']['score_e0']['probabilities']['unexpected'] = 0
        for answer in variants:
            with self.assertRaises(fast.FastError):
                fast.advance(p, base, answer)
        self.assertEqual(base, fast.initial(p))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.store = fast.Store(Path(self.temp.name) / 'state')
        self.p = plan()

    def test_resume_duplicate_and_completed_requests_do_not_call_again(self):
        ask = Mock(side_effect=score_response)
        self.assertEqual(self.store.get(self.p)['step'], 0)
        first = self.store.step(self.p, 0, ask)
        self.assertEqual(self.store.step(self.p, 0, ask), first)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(fast.Store(self.store.root).get(self.p), first)
        self.assertEqual(self.store._path(self.p).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(fast.FastError):
            self.store.step(self.p, 10, ask)
        self.assertEqual(ask.call_count, 1)

    def test_completed_cache_does_not_reask(self):
        p = plan(2)
        scored = self.store.step(p, 0, score_response)
        done = self.store.step(p, scored['step'], lambda request: choice_response(request, 'keep_1'))
        ask = Mock()
        self.assertEqual(self.store.step(p, done['step'], ask), done)
        ask.assert_not_called()

    def test_invalid_answer_or_changed_plan_never_commits(self):
        with self.assertRaises(fast.FastError):
            self.store.step(self.p, 0, lambda _: {})
        self.assertEqual(self.store.get(self.p)['step'], 0)
        check = Mock(side_effect=[None, fast.FastError('stale', 409)])
        with self.assertRaises(fast.FastError):
            self.store.step(self.p, 0, score_response, check)
        self.assertEqual(self.store.get(self.p)['step'], 0)

    def test_concurrent_step_cannot_duplicate_jev_call(self):
        other = Mock()
        def ask(request):
            with self.assertRaises(fast.FastError) as error:
                self.store.step(self.p, 0, other)
            self.assertEqual(error.exception.status, 409)
            return score_response(request)
        self.store.step(self.p, 0, ask)
        other.assert_not_called()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'HEALTH_WORKOUT_ALLOWED_USER': 'owner',
            'HEALTH_WORKOUT_ALLOWED_ORIGIN': 'https://dashboard.test'})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name, value in [('data', Mock(return_value=snapshot())),
                            ('FAST_STORE', fast.Store(self.temp.name)),
                            ('ask_training_jev', Mock(side_effect=score_response))]:
            patcher = patch.object(svc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), svc.Handler)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, body=None, headers=None, query=None):
        supplied = {'Tailscale-User-Login': 'owner', 'Origin': 'https://dashboard.test',
                    'X-Health-Action': 'rank-training', 'Content-Type': 'application/json'}
        supplied.update(headers or {})
        path = '/api/training/fast' + (query or '')
        request = urllib.request.Request(self.base + path, headers=supplied,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_read_is_free_and_advance_is_idempotent(self):
        status, result = self.call(query=f'?date={DAY}&revision={REVISION}')
        self.assertEqual((status, result['phase']), (200, 'not_started'))
        svc.ask_training_jev.assert_not_called()
        body = {'date': DAY, 'revision': REVISION, 'step': 0}
        status, result = self.call(body)
        self.assertEqual((status, result['step']), (200, 1))
        self.assertEqual(self.call(body), (200, result))
        self.assertEqual(svc.ask_training_jev.call_count, 1)

    def test_auth_origin_action_validation_and_stale_rejected_before_calls(self):
        body = {'date': DAY, 'revision': REVISION, 'step': 0}
        for headers, status in [({'Tailscale-User-Login': 'other'}, 403),
                ({'Origin': 'https://evil.test'}, 403), ({'X-Health-Action': 'save-workout'}, 403),
                ({'Content-Type': 'text/plain'}, 415)]:
            self.assertEqual(self.call(body, headers)[0], status)
        for payload, status in [({**body, 'revision': 'b' * 40}, 409),
                ({**body, 'exercises': []}, 400), ({**body, 'step': True}, 400),
                ([], 400), ({**body, 'date': 'x' * 3000}, 413)]:
            self.assertEqual(self.call(payload)[0], status)
        svc.ask_training_jev.assert_not_called()



class TransportTests(unittest.TestCase):
    def test_transport_uses_logged_cli_without_shell_or_secret_arguments(self):
        response = {'answers': {}}
        with patch.object(svc, 'api_key', return_value='test-only-secret'), patch.object(svc.subprocess, 'run') as run:
            run.return_value = Mock(returncode=0, stdout=json.dumps(response))
            self.assertEqual(svc.ask_training_jev(fast.question(plan(), fast.initial(plan()))), response)
            args, kwargs = run.call_args
            self.assertNotIn('test-only-secret', str(args))
            self.assertEqual(kwargs['env']['TYPESAFE_API_KEY'], 'test-only-secret')
            self.assertEqual(kwargs['timeout'], 30)
            self.assertFalse(kwargs.get('shell', False))
