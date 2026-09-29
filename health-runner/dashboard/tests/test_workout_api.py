"""Legacy proxy-header routing cannot restore an alternate write boundary.

Actual canonical HTTP policy, framing, restart and receipt tests are in
tests/test_transport*.py and tests/test_portable_http.py.
"""
import unittest
from unittest.mock import patch

import context_service as svc
import workout_store as ws


class RetiredWorkoutApiTests(unittest.TestCase):
    def test_preview_startup_refuses_without_listening(self):
        with patch('socket.socket', side_effect=AssertionError('No old listener')):
            self.assertEqual(svc.main(['--serve-html', '/not-read/example.html']), 2)
        self.assertFalse(hasattr(svc, 'Handler'))

    def test_forged_user_origin_and_action_do_not_authorize_legacy_save(self):
        with self.assertRaises(ws.SaveError) as error:
            ws.authorize({'Tailscale-User-Login': 'synthetic-owner', 'Origin': 'https://example.invalid', 'X-Health-Action': 'save-workout'}, write=True)
        self.assertEqual(error.exception.status, 410)
