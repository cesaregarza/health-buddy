"""Validate user-entered workouts and append them atomically to private Git main."""
from __future__ import annotations

import csv
import fcntl
import io
import math
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SESSION_FIELDS = ['session_id', 'date', 'workout_type', 'status', 'duration_min', 'bodyweight_lb', 'notes']
SET_FIELDS = ['session_id', 'session_date', 'exercise', 'equipment', 'set_number', 'set_count', 'load_lb', 'load_basis', 'reps', 'rir', 'form_quality', 'status', 'notes']
MAX_BODY = 65536
MAX_SETS = 80


class SaveError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, field, limit=100, required=True):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise SaveError(f'Invalid {field}.')
    value = value.strip()
    if required and not value:
        raise SaveError(f'{field} is required.')
    if limit == 100 and ('\n' in value or '\t' in value):
        raise SaveError(f'Invalid {field}.')
    return value


def _number(value, field, maximum, *, integer=False, minimum=0, optional=True):
    if value is None and optional:
        return ''
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SaveError(f'{field} must be a finite number.')
    if not minimum <= value <= maximum or (integer and value != int(value)):
        raise SaveError(f'{field} is outside the allowed range.')
    return str(int(value)) if value == int(value) else str(value)


def normalize(payload, as_of=None):
    fields = {'schema_version', 'session_id', 'date', 'workout_type', 'status', 'duration_min', 'notes', 'sets', 'base_revision'}
    if not isinstance(payload, dict) or set(payload) - fields or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
        raise SaveError('Unsupported workout payload.')
    sid = payload.get('session_id')
    if not isinstance(sid, str) or not re.fullmatch(r'dashboard-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', sid):
        raise SaveError('Invalid workout identifier.')
    day = payload.get('date')
    try:
        parsed = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise SaveError('Use a valid workout date.') from None
    if day != parsed.isoformat() or parsed > (as_of or datetime.now(ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))).date()):
        raise SaveError('Workout date cannot be in the future.')
    kind = payload.get('workout_type')
    if kind not in ('upper_body', 'lower_body', 'cardio', 'racquet', 'shuffle', 'strength'):
        raise SaveError('Invalid workout type.')
    status = payload.get('status')
    if status not in ('complete', 'partial'):
        raise SaveError('Choose complete or partial.')
    revision = payload.get('base_revision')
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision)):
        raise SaveError('Invalid source revision.')
    duration = _number(payload.get('duration_min'), 'Duration', 1440)
    notes = _text(payload.get('notes', ''), 'Notes', 2000, False)
    sets = payload.get('sets')
    if not isinstance(sets, list) or len(sets) > MAX_SETS:
        raise SaveError(f'At most {MAX_SETS} sets can be saved.')
    if not sets and (kind in ('upper_body', 'lower_body', 'strength') or not duration):
        raise SaveError('Enter completed sets, or a recorded cardio duration.')
    output, keys = [], set()
    for item in sets:
        allowed = {'exercise', 'equipment', 'set_number', 'load_lb', 'load_basis', 'reps', 'rir', 'form_quality', 'notes'}
        if not isinstance(item, dict) or set(item) - allowed:
            raise SaveError('Invalid set fields.')
        exercise = _text(item.get('exercise'), 'Exercise')
        equipment = _text(item.get('equipment'), 'Equipment')
        ordinal = _number(item.get('set_number'), 'Set number', 1000, minimum=1, integer=True, optional=False)
        key = (exercise, equipment, ordinal)
        if key in keys:
            raise SaveError('Duplicate exercise, machine and set number.')
        keys.add(key)
        basis = item.get('load_basis')
        if basis not in ('per_hand', 'total_stack', 'machine_stack', 'total', 'bodyweight', 'not_reported'):
            raise SaveError('Choose a valid load basis.')
        form = item.get('form_quality', 'not_reported')
        if form not in ('not_reported', 'clean', 'controlled', 'breakdown'):
            raise SaveError('Choose a valid form report.')
        output.append(dict(zip(SET_FIELDS, [sid, day, exercise, equipment, ordinal, '',
            _number(item.get('load_lb'), 'Load', 2000), basis,
            _number(item.get('reps'), 'Reps', 1000, integer=True, minimum=1, optional=False),
            _number(item.get('rir'), 'RIR', 10, integer=True), form, 'completed',
            _text(item.get('notes', ''), 'Set notes', 2000, False)])))
    output.sort(key=lambda r: (r['exercise'], r['equipment'], int(r['set_number'])))
    return dict(zip(SESSION_FIELDS, [sid, day, kind, status, duration, '', notes])), output


def _git(repo, *args, check=True):
    command = ['git', '-C', str(repo), *args]
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if check and result.returncode:
        raise SaveError('The central log could not be updated. Your draft is safe; retry shortly.', 503)
    return result


def _csv(text, fields):
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != fields:
        raise SaveError('The central log format changed. Refresh before saving.', 409)
    rows = list(reader)
    if any(set(row) != set(fields) or any(v is None for v in row.values()) for row in rows):
        raise SaveError('The central log needs a format review.', 409)
    return rows


def _matches(session, sets, existing_sessions, existing_sets):
    found = [r for r in existing_sessions if r['session_id'] == session['session_id']]
    rows = [r for r in existing_sets if r['session_id'] == session['session_id']]
    if not found and not rows:
        return False
    rows.sort(key=lambda r: (r['exercise'], r['equipment'], int(r['set_number'])))
    if found == [session] and rows == sets:
        return True
    raise SaveError('This workout ID is already saved with different values. Existing records were preserved.', 409)


def _append(path, fields, rows):
    if not rows:
        return
    # Existing bytes remain untouched apart from adding a required trailing newline.
    with path.open('rb') as handle:
        raw = handle.read()
    with path.open('a', newline='', encoding='utf-8') as handle:
        if raw and not raw.endswith(b'\n'):
            handle.write('\n')
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writerows(rows)


def request_refresh():
    return subprocess.run(['systemctl', '--user', 'restart', '--no-block', 'health-dashboard.service'],
                          capture_output=True, timeout=10).returncode == 0


def save_workout(payload, origin, *, state_dir=None, refresh=request_refresh, as_of=None, before_push=None):
    session, sets = normalize(payload, as_of)
    origin = Path(origin).resolve()
    state = Path(state_dir or Path.home() / '.local/state/health-dashboard').resolve()
    if str(origin).startswith('/mnt/') or str(state).startswith('/mnt/'):
        raise SaveError('Workout storage must use native Linux paths.', 503)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'save.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for attempt in range(3):
            with tempfile.TemporaryDirectory(prefix='workout-', dir=state) as temporary:
                repo = Path(temporary).resolve()
                if str(repo).startswith('/mnt/'):
                    raise SaveError('Invalid temporary storage.', 503)
                _git(repo, 'init', '--quiet')
                _git(repo, 'remote', 'add', 'origin', str(origin))
                _git(repo, 'fetch', '--quiet', '--depth=1', 'origin', 'main')
                # A sparse index avoids checking out unrelated private assets.
                _git(repo, 'config', 'core.sparseCheckout', 'true')
                (repo / '.git/info/sparse-checkout').write_text('/data/sessions.csv\n/data/sets.csv\n')
                _git(repo, 'checkout', '--quiet', '-b', 'main', 'FETCH_HEAD')
                files = [(repo / 'data/sessions.csv', SESSION_FIELDS), (repo / 'data/sets.csv', SET_FIELDS)]
                existing = [_csv(p.read_text(), fields) for p, fields in files]
                duplicate = _matches(session, sets, *existing)
                if duplicate:
                    commit = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
                else:
                    _append(files[0][0], SESSION_FIELDS, [session])
                    _append(files[1][0], SET_FIELDS, sets)
                    _git(repo, 'add', '--', 'data/sessions.csv', 'data/sets.csv')
                    changed = set(_git(repo, 'diff', '--cached', '--name-only').stdout.splitlines())
                    if not changed <= {'data/sessions.csv', 'data/sets.csv'}:
                        raise SaveError('Unexpected files in workout update.', 503)
                    _git(repo, '-c', 'user.name=Health dashboard', '-c', 'user.email=health-dashboard@localhost',
                         'commit', '--quiet', '-m', 'Record dashboard workout ' + session['session_id'])
                    commit = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
                    if before_push:
                        before_push(attempt)
                    pushed = _git(repo, 'push', '--quiet', 'origin', 'HEAD:main', check=False)
                    if pushed.returncode:
                        # Re-read authoritative content even if the push succeeded but its response was lost.
                        _git(repo, 'fetch', '--quiet', 'origin', 'main')
                        remote = [_csv(_git(repo, 'show', 'FETCH_HEAD:' + name).stdout, fields)
                                  for name, fields in [('data/sessions.csv', SESSION_FIELDS), ('data/sets.csv', SET_FIELDS)]]
                        if _matches(session, sets, *remote):
                            commit = _git(repo, 'rev-parse', 'FETCH_HEAD').stdout.strip()
                        elif attempt < 2:
                            continue
                        else:
                            raise SaveError('The log changed during save. Your draft is safe; retry.', 409)
                try:
                    refreshed = bool(refresh())
                except (OSError, subprocess.SubprocessError):
                    refreshed = False
                return {'saved': True, 'session_id': session['session_id'], 'commit': commit,
                        'duplicate': duplicate, 'refresh_requested': refreshed}
    raise SaveError('Workout could not be saved. Retry shortly.', 503)


def settings():
    return {name: os.environ.get(name, '').strip() for name in
            ('HEALTH_WORKOUT_ORIGIN', 'HEALTH_WORKOUT_ALLOWED_USER', 'HEALTH_WORKOUT_ALLOWED_ORIGIN')}


def authorize(headers, *, write=False):
    config = settings()
    if not all(config.values()):
        raise SaveError('Workout saving is not configured yet. Your draft stays on this device.', 503)
    if headers.get('Tailscale-User-Login') != config['HEALTH_WORKOUT_ALLOWED_USER']:
        raise SaveError('Open this dashboard from your signed-in Tailscale device to save.', 403)
    if write and (headers.get('Origin') != config['HEALTH_WORKOUT_ALLOWED_ORIGIN']
                  or headers.get('X-Health-Action') != 'save-workout'):
        raise SaveError('Workout saves must originate from this dashboard.', 403)
    return config
