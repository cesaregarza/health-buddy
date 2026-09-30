"""Pure workout validation and legacy CSV helpers; remote saving is retired."""
from __future__ import annotations

import csv
import io
import math
import os
import re
from datetime import date, datetime
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



def save_workout(payload, origin, *, state_dir=None, refresh=None, as_of=None, before_push=None):
    """Refuse the old remote writer before inspecting payload, paths or hooks."""
    raise SaveError('Remote workout saving is retired; use canonical workspace operations.', 410)


def authorize(headers, *, write=False):
    """Caller-provided proxy headers cannot revive the retired boundary."""
    raise SaveError('Legacy proxy authorization is retired; use canonical authentication.', 410)
