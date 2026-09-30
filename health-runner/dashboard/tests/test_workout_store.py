"""Fabricated transactions against native temporary Git repositories only."""
import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
import workout_store as ws


def payload():
    return {'schema_version':1, 'session_id':'dashboard-10000000-0000-4000-8000-000000000001',
            'date':'2026-09-20', 'workout_type':'upper_body', 'status':'complete', 'duration_min':None, 'notes':'Example',
            'sets':[{'exercise':'example_press','equipment':'example_machine','set_number':1,'load_lb':0,
                     'load_basis':'total_stack','reps':10,'rir':None,'form_quality':'not_reported','notes':''}]}


def git(path, *args):
    return subprocess.check_output(['git','-C',str(path),*args],text=True,stderr=subprocess.DEVNULL).strip()


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

    def test_authorization_requires_identity_and_origin(self):
        config={'HEALTH_WORKOUT_ORIGIN':'/tmp/example.git','HEALTH_WORKOUT_ALLOWED_USER':'example-user',
                'HEALTH_WORKOUT_ALLOWED_ORIGIN':'https://example.test:8443'}
        headers={'Tailscale-User-Login':'example-user','Origin':'https://example.test:8443','X-Health-Action':'save-workout'}
        with patch.dict(os.environ,config):
            self.assertEqual(ws.authorize(headers,write=True),config)
            for key in headers:
                bad=dict(headers);bad.pop(key)
                with self.assertRaises(ws.SaveError):ws.authorize(bad,write=True)
            with patch.dict(os.environ,{'HEALTH_WORKOUT_ALLOWED_USER':''}):
                with self.assertRaises(ws.SaveError):ws.authorize(headers)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='dashboard-save-test-',dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();assert not str(self.root).startswith('/mnt/')
        self.origin=self.root/'origin.git';self.source=self.root/'seed';self.source.mkdir()
        git(self.root,'init','--bare','--quiet',str(self.origin))
        git(self.source,'init','--quiet','-b','main')
        git(self.source,'config','user.name','Synthetic test');git(self.source,'config','user.email','demo@example.invalid')
        git(self.source,'config','commit.gpgsign','false')
        (self.source/'data').mkdir()
        for name,fields in [('sessions',ws.SESSION_FIELDS),('sets',ws.SET_FIELDS)]:
            (self.source/f'data/{name}.csv').write_text(','.join(fields)+'\n')
        (self.source/'unrelated.txt').write_text('preserve\n')
        git(self.source,'add','.');git(self.source,'commit','--quiet','-m','Synthetic base')
        git(self.source,'remote','add','origin',str(self.origin));git(self.source,'push','--quiet','origin','main')
        self.before=git(self.origin,'rev-parse','main')
        # Disable signing only in this synthetic test process's child Git configuration.
        self.env=patch.dict(os.environ,{'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'commit.gpgsign','GIT_CONFIG_VALUE_0':'false'})
        self.env.start();self.addCleanup(self.env.stop)
        self.state=self.root/'state'

    def save(self,p=None,**kwargs):
        return ws.save_workout(p or payload(),self.origin,state_dir=self.state,refresh=lambda:True,**kwargs)

    def test_atomic_append_only_and_identical_retry(self):
        r=self.save();self.assertTrue(r['saved']);self.assertFalse(r['duplicate'])
        self.assertEqual(set(git(self.origin,'diff','--name-only',self.before,'main').splitlines()),{'data/sessions.csv','data/sets.csv'})
        self.assertEqual(git(self.origin,'show','main:unrelated.txt'),'preserve')
        second=self.save();self.assertTrue(second['duplicate']);self.assertEqual(second['commit'],r['commit'])
        p=payload();p['sets'][0]['reps']=11
        with self.assertRaises(ws.SaveError) as caught:self.save(p)
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(git(self.origin,'rev-parse','main'),r['commit'])

    def test_concurrent_main_change_retried_without_losing_either(self):
        def race(attempt):
            if attempt:return
            (self.source/'unrelated.txt').write_text('concurrent update\n')
            git(self.source,'add','unrelated.txt');git(self.source,'commit','--quiet','-m','Concurrent synthetic update')
            git(self.source,'push','--quiet','origin','main')
        result=self.save(before_push=race)
        self.assertTrue(result['saved'])
        self.assertEqual(git(self.origin,'show','main:unrelated.txt'),'concurrent update')

    def test_rejected_push_leaves_main_unchanged(self):
        hook=self.origin/'hooks/pre-receive';hook.write_text('#!/bin/sh\nexit 1\n');hook.chmod(0o700)
        with self.assertRaises(ws.SaveError):self.save()
        self.assertEqual(git(self.origin,'rev-parse','main'),self.before)

    def test_invalid_set_never_writes_and_refresh_failure_keeps_receipt(self):
        p=payload();p['sets'][0]['reps']=-1
        with self.assertRaises(ws.SaveError):self.save(p)
        self.assertEqual(git(self.origin,'rev-parse','main'),self.before)
        def unavailable():raise OSError('synthetic')
        result=ws.save_workout(payload(),self.origin,state_dir=self.state,refresh=unavailable)
        self.assertTrue(result['saved']);self.assertFalse(result['refresh_requested'])
