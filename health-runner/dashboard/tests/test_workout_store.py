"""Pure workout/CSV invariants; retired remote writer never invokes Git."""
from datetime import date
import unittest
from unittest.mock import patch

import workout_store as ws


def payload():
    return {'schema_version':1, 'session_id':'dashboard-10000000-0000-4000-8000-000000000001',
            'date':'2026-09-20', 'workout_type':'upper_body', 'status':'complete', 'duration_min':None, 'notes':'Example',
            'sets':[{'exercise':'example_press','equipment':'example_machine','set_number':1,'load_lb':0,
                     'load_basis':'total_stack','reps':10,'rir':None,'form_quality':'not_reported','notes':''}]}


class ValidationTests(unittest.TestCase):
    def test_unknowns_and_zero_survive(self):
        session, sets=ws.normalize(payload(),date(2026,9,28))
        self.assertEqual(session['duration_min'],'')
        self.assertEqual(sets[0]['load_lb'],'0')
        self.assertEqual(sets[0]['rir'],'')
        self.assertEqual(sets[0]['form_quality'],'not_reported')

    def test_invalid_payloads_rejected_before_storage(self):
        for update in ({'date':'2099-01-01'},{'duration_min':float('nan')},{'duration_min':True},
                       {'session_id':'../escape'},{'sets':[]},{'unrecognized':1},{'schema_version':True}):
            with self.subTest(update=update), self.assertRaises(ws.SaveError):
                ws.normalize({**payload(),**update},date(2026,9,28))
        for key,value in [('reps',0),('reps',True),('rir',11),('load_lb',float('inf')),('form_quality','assumed'),('load_basis','arbitrary')]:
            p=payload();p['sets'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ws.SaveError):ws.normalize(p)
        p=payload();p['sets']*=2
        with self.assertRaisesRegex(ws.SaveError,'Duplicate'):ws.normalize(p)


    def test_natural_key_and_exact_csv_headers(self):
        session, sets = ws.normalize(payload(), date(2026, 9, 28))
        self.assertFalse(ws._matches(session, sets, [], []))
        self.assertTrue(ws._matches(session, sets, [session], list(sets)))
        changed = [{**sets[0], 'reps': '11'}]
        with self.assertRaises(ws.SaveError) as error:
            ws._matches(session, changed, [session], sets)
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(ws.SaveError):
            ws._csv('wrong,header\n', ws.SESSION_FIELDS)

    def test_remote_writer_and_proxy_headers_refuse_without_side_effects(self):
        class UnreadableOrigin:
            def __fspath__(self):
                raise AssertionError('No source path may be inspected')

        with patch('subprocess.run', side_effect=AssertionError('No Git or refresh')):
            with self.assertRaises(ws.SaveError) as error:
                ws.save_workout(payload(), UnreadableOrigin(), before_push=lambda: self.fail('No push'))
            self.assertEqual(error.exception.status, 410)
            with self.assertRaises(ws.SaveError):
                ws.authorize({'Tailscale-User-Login': 'synthetic-owner'}, write=True)
