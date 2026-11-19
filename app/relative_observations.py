"""相对调整专用只读视图：不建表、不融合、不改旧声学摘要或普通推荐。"""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from app.audio_observations import BINDING_FIELDS, canonical_payload
from app.relative_strength import _identity

MAX_SONGS = 512
RECENT_PER_CONFIGURATION = 8


def read_observations(song_ids, *, cutoff=None, db_path=None):
    from app import library
    cutoff = time.time() if cutoff is None else cutoff
    ids = list(dict.fromkeys(str(sid) for sid in song_ids if sid is not None))
    bundle = {'songs': {}, 'observations': {}, 'cutoff': cutoff, 'read_error': None,
              'limited': len(ids) > MAX_SONGS}
    ids = ids[:MAX_SONGS]
    if not ids:
        return bundle
    path = Path(db_path or library.DB_PATH).resolve()
    marks = ','.join('?' for _ in ids)
    try:
        # mode=ro及query_only不允许诊断读取顺手初始化或迁移正式声学表。
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            for row in conn.execute(f'SELECT song_id,title,artist,album,duration_seconds FROM songs WHERE song_id IN ({marks})', ids):
                bundle['songs'][str(row['song_id'])] = {'id': str(row['song_id']), **dict(row)}
            rows = conn.execute(f'''SELECT * FROM (
                SELECT o.*, b.revision AS current_revision, b.metadata_sha256 AS binding_metadata,
                       g.generation AS current_generation,
                       ROW_NUMBER() OVER (PARTITION BY o.song_id,o.measurement_mode,o.capture_configuration
                         ORDER BY o.received_at DESC,o.observation_id DESC) AS position
                FROM audio_observations o
                JOIN audio_observation_songs b ON b.song_id=o.song_id
                JOIN audio_observation_state g ON g.singleton=1
                WHERE o.song_id IN ({marks}) AND o.received_at<=?
            ) WHERE position<=?''', (*ids, cutoff, RECENT_PER_CONFIGURATION)).fetchall()
    except sqlite3.Error as error:
        bundle['read_error'] = ('observations_missing' if 'no such table' in str(error) or 'no such column' in str(error)
                                else 'observations_unavailable')
        return bundle
    for row in rows:
        sid = str(row['song_id'])
        metadata = bundle['songs'].get(sid)
        if not metadata:
            continue
        values = [metadata.get(k) or (0 if k == 'duration_seconds' else '')
                  for k in ('title', 'artist', 'album', 'duration_seconds')]
        if any(not isinstance(v, str) for v in values[:3]) or type(values[3]) is not int or values[3] < 0:
            continue
        expected = hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if (row['library_generation'] != row['current_generation']
                or row['song_revision'] != row['current_revision']
                or row['metadata_sha256'] != row['binding_metadata']
                or row['metadata_sha256'] != expected):
            continue
        try:
            payload = json.loads(row['payload_json'])
            observation_id = payload.pop('observation_id')
            if observation_id != row['observation_id'] or any(payload[key] != row[key] for key in BINDING_FIELDS):
                continue
            encoded, fingerprint = canonical_payload(observation_id, payload)
            if fingerprint != row['payload_sha256'] or encoded != row['payload_json']:
                continue
            received = row['received_at']
            if not isinstance(received, (int, float)) or not math.isfinite(received) or received < 0:
                continue
            key = tuple(row[k] for k in ('library_generation', 'feature_algorithm', 'capture_contract',
                                         'measurement_mode', 'capture_configuration'))
            record = {'energy': payload['summary']['energy_mean'],
                      'sample_seconds': payload['summary']['sample_seconds'], 'received_at': received,
                      'observation_id': observation_id, 'work_identity': _identity(metadata)}
            per_song = bundle['observations'].setdefault(sid, {})
            if key not in per_song or (received, observation_id) > (per_song[key]['received_at'], per_song[key]['observation_id']):
                per_song[key] = record
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            # 单份坏包保持未知；同组内仍可用有界窗口中的其他有效观察。
            continue
    return bundle
