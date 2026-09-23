"""R41–R44: declared JSON fields versus frozen bytes, never proof of a conclusion."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

from . import frozen_refs
from .errors import NotFoundError, ValidationError

CHECKER_VERSION = 'field-check/1'
NUMERIC_OPS = {'approx', 'lt', 'le', 'gt', 'ge'}


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def finite_number(value):
    return is_number(value) and (isinstance(value, int) or math.isfinite(value))


def _literal(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def declare(db, root, kind, checks, *, current=()):
    if not isinstance(checks, list):
        raise ValidationError('checks: not_a_list')
    if checks and kind not in {'claim', 'observation'}:
        raise ValidationError('checks: kind_not_checkable')
    existing = {_literal(item) for item in current}
    for index, spec in enumerate(checks):
        def fail(code, error=ValidationError):
            raise error(f'checks[{index}]: {code}')
        try:
            unchanged = _literal(spec) in existing
        except (ValueError, TypeError):
            unchanged = False
        if unchanged:
            continue
        if not isinstance(spec, dict) or set(spec) - {'ref','path','op','value','tolerance'}:
            fail('unknown_field')
        ref = spec.get('ref')
        if not frozen_refs.is_frozen(ref):
            fail('not_frozen')
        resolution = frozen_refs.resolve(db, root, ref)
        if resolution['outcome'] != 'resolved':
            fail(resolution['reason'], NotFoundError if resolution['outcome']=='not_found' else ValidationError)
        obj = resolution['object']
        file = obj is not None and obj['kind']=='file' and not resolution.get('subpath')
        if obj and resolution['kind']=='object_subpath':
            file = (Path(root)/'.research/objects'/obj['version']/resolution['subpath']).is_file()
        if not file:
            fail('not_a_file')
        path = spec.get('path')
        if not isinstance(path, str) or (path and not path.startswith('/')) or re.search(r'~(?![01])', path):
            fail('bad_pointer')
        op = spec.get('op')
        if not isinstance(op, str) or op not in NUMERIC_OPS | {'eq'}:
            fail('bad_op')
        value = spec.get('value')
        scalar = value is None or isinstance(value, (str, bool)) or finite_number(value)
        if 'value' not in spec or not scalar or (op in NUMERIC_OPS and not finite_number(value)):
            fail('invalid_value')
        if op == 'approx':
            if 'tolerance' not in spec:
                fail('tolerance_required')
            if not finite_number(spec['tolerance']):
                fail('invalid_tolerance')
            if spec['tolerance'] < 0:
                fail('negative_tolerance')
        elif 'tolerance' in spec:
            fail('tolerance_not_allowed')
    return checks


def pointer(document, text):
    if text == '':
        return True, document
    value = document
    for raw in text[1:].split('/'):
        token = raw.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):
            if token not in value:
                return False, None
            value = value[token]
        elif isinstance(value, list):
            if re.fullmatch(r'0|[1-9][0-9]*', token) is None:
                return False, None
            index = int(token)
            if index >= len(value):
                return False, None
            value = value[index]
        else:
            return False, None
    return True, value


def _kind(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if is_number(value):
        return 'number'
    if isinstance(value, str):
        return 'string'
    return 'container'


def _compare(body, spec):
    try:
        document = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return 'not_checkable', 'unsupported_format', None
    present, observed = pointer(document, spec['path'])
    if not present:
        return 'not_checkable', 'missing_value', None
    text = json.dumps(observed, allow_nan=True, ensure_ascii=False, sort_keys=True)[:200]
    if _kind(observed) == 'container':
        return 'not_checkable', 'not_a_scalar', text
    op, value = spec['op'], spec['value']
    if op in NUMERIC_OPS or (op == 'eq' and is_number(value)):
        if is_number(observed) and not finite_number(observed):
            return 'not_checkable', 'invalid_number', text
        if op in NUMERIC_OPS and not is_number(observed):
            return 'not_checkable', 'invalid_number', text
    if op == 'eq':
        if _kind(observed) != _kind(value):
            return 'inconsistent', 'type_mismatch', text
        holds = observed == value
    else:
        holds = {
            'approx': lambda: abs(observed - value) <= spec['tolerance'],
            'lt': lambda: observed < value, 'le': lambda: observed <= value,
            'gt': lambda: observed > value, 'ge': lambda: observed >= value,
        }[op]()
    return ('consistent', None, text) if holds else ('inconsistent', 'value_mismatch', text)


def evaluate(db, root, target_ref, checks, *, sequence, created_at):
    """Evaluate once in the caller's write transaction; cache only within this call."""
    import hashlib
    opened, contents, rows = {}, {}, []
    for index, spec in enumerate(checks):
        resolution = frozen_refs.resolve(db, root, spec['ref'])
        obj = resolution['object']
        version = obj['version'] if obj else None
        reason = resolution['reason']
        digest = text = None
        result = 'not_checkable'
        if resolution['outcome'] == 'resolved':
            if version not in opened:
                checked, _ = frozen_refs.open(db, root, spec['ref'])
                opened[version] = checked['reason']
            reason = opened[version]
            if reason is None:
                key = (version, resolution.get('subpath', ''))
                if key not in contents:
                    path = Path(root)/'.research/objects'/version
                    if key[1]:
                        path = path / key[1]
                    try:
                        body = path.read_bytes()
                        contents[key] = (body, hashlib.sha256(body).hexdigest(), None)
                    except OSError as exc:
                        contents[key] = (None, None, 'object_missing' if isinstance(exc, FileNotFoundError) else 'object_corrupted')
                body, digest, reason = contents[key]
                if reason is None:
                    result, reason, text = _compare(body, spec)
        row = dict(target_ref=target_ref, check_index=index, spec=_literal(spec),
                   input_ref=spec['ref'], object_version=version, input_sha256=digest,
                   observed_text=text, tolerance_rule='abs' if spec['op']=='approx' else None,
                   checker_version=CHECKER_VERSION, result=result, reason=reason,
                   created_at=created_at, source_sequence=sequence)
        db.execute(f"INSERT INTO field_checks ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        rows.append({**row, 'target': row['target_ref'], 'sequence': row['source_sequence'], 'spec': spec})
        del rows[-1]['target_ref'], rows[-1]['source_sequence']
    return rows


def read(db, target_ref):
    """Return stored facts only; never reopen objects or evaluate on a read."""
    rows = []
    for raw in db.execute('SELECT * FROM field_checks WHERE target_ref=? ORDER BY check_index', (target_ref,)):
        row = dict(raw)
        row['target'] = row.pop('target_ref')
        row['sequence'] = row.pop('source_sequence')
        row['spec'] = json.loads(row['spec'])
        rows.append(row)
    return rows
