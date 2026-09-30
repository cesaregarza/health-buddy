"""HTTP save boundaries; no real data or external calls."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import context_service as svc
from test_workout_store import payload


class WorkoutApiTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'HEALTH_WORKOUT_ORIGIN':'/tmp/example.git','HEALTH_WORKOUT_ALLOWED_USER':'example',
                                      'HEALTH_WORKOUT_ALLOWED_ORIGIN':'https://example.test:8443'})
        self.env.start();self.addCleanup(self.env.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),svc.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.headers={'Tailscale-User-Login':'example','Origin':'https://example.test:8443',
                      'X-Health-Action':'save-workout','Content-Type':'application/json'}

    def call(self,path,body=None,headers=None):
        request=urllib.request.Request(self.base+path,data=body,headers=headers if headers is not None else self.headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as r:return r.status,json.loads(r.read())
        except urllib.error.HTTPError as r:return r.code,json.loads(r.read())

    def test_status_and_owner_origin_content_type_gates(self):
        self.assertEqual(self.call('/api/workouts/status')[0],200)
        self.assertEqual(self.call('/api/workouts/status',headers={})[0],403)
        raw=json.dumps(payload()).encode()
        with patch.object(svc.workouts,'save_workout') as save:
            for headers in ({},{**self.headers,'Origin':'https://hostile.invalid'},
                            {**self.headers,'X-Health-Action':''}):
                self.assertEqual(self.call('/api/workouts',raw,headers)[0],403)
            self.assertEqual(self.call('/api/workouts',raw,{**self.headers,'Content-Type':'text/plain'})[0],415)
            self.assertEqual(self.call('/api/workouts',b'x'*65537)[0],413)
            self.assertEqual(self.call('/api/workouts',b'{broken')[0],400)
            save.assert_not_called()

    def test_success_returns_durable_receipt(self):
        receipt={'saved':True,'session_id':payload()['session_id'],'commit':'a'*40,'duplicate':False,'refresh_requested':True}
        with patch.object(svc.workouts,'save_workout',return_value=receipt) as save:
            self.assertEqual(self.call('/api/workouts',json.dumps(payload()).encode()),(200,receipt))
            save.assert_called_once()
