"""相对调整的声学比较与说明出口；标签尝试单独标注，不冒充全曲听感。"""
import hashlib
import json
import math

from app.audio_features import ALGORITHM_VERSION, MIN_SAMPLE_SECONDS

# 只是现有0～1摘要标尺上的保守方向间隔，不代表响度百分比或听感阈值。
ENERGY_MARGIN = 0.05
MAX_BASELINE = 8


def direction_from_message(message):
    from app.recommendation_state import task_energy_adjustment
    if not task_energy_adjustment(message):
        return None
    return 'higher' if any(word in message for word in ('燃', '躁', '有劲', '高能量')) else 'lower'


def _identity(song):
    values = [str(song.get(key) or '') for key in ('id', 'title', 'artist', 'album')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()


def _energy(features):
    """只接纳已实现的标尺、足量有限摘要；旧记录没有覆盖或全曲置信度保证。"""
    if not isinstance(features, dict) or features.get('algorithm_version') != ALGORITHM_VERSION:
        return None
    energy, seconds = features.get('energy_mean'), features.get('sample_seconds')
    if any(isinstance(value, bool) or not isinstance(value, (float, int)) for value in (energy, seconds)):
        return None
    if not math.isfinite(energy) or not math.isfinite(seconds):
        return None
    if not 0 <= energy <= 1 or not MIN_SAMPLE_SECONDS <= seconds <= 86400:
        return None
    return float(energy)


def capture_baseline(songs):
    """保存实际返回时的少量摘要，后续采样更新不能回写这次比较的前批。"""
    records = []
    for song in songs[:MAX_BASELINE]:
        features = song.get('audio_features') or {}
        energy = _energy(features)
        records.append({'id': str(song['id']), 'identity': _identity(song),
                        'energy': energy,
                        'algorithm_version': ALGORITHM_VERSION if energy is not None else None})
    return {'version': 1, 'records': records}


def baseline_ids(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
        return []
    records = snapshot.get('records')
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_BASELINE:
        return []
    if not all(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'] for row in records):
        return []
    ids = [row['id'] for row in records]
    return ids if len(set(ids)) == len(ids) else []


def prepare_comparison(direction, snapshot, metadata):
    """缺前批、部分未知、身份改变都不猜整批均值；不回填历史比较依据。"""
    ids = baseline_ids(snapshot)
    values = []
    for record in snapshot.get('records', []) if ids else []:
        song = metadata.get(record['id'])
        value = record.get('energy')
        valid = (song and record.get('identity') == _identity(song)
                 and record.get('algorithm_version') == ALGORITHM_VERSION
                 and isinstance(value, (float, int)) and not isinstance(value, bool)
                 and math.isfinite(value) and 0 <= value <= 1)
        if valid:
            values.append(float(value))
    usable = bool(ids and len(values) == len(ids) and direction in ('lower', 'higher'))
    return {'direction': direction, 'basis': 'previous_batch_fragment_energy',
            'baseline_total': len(ids), 'baseline_observed': len(values),
            'baseline_mean': sum(values) / len(values) if usable else None,
            'algorithm_version': ALGORITHM_VERSION, 'margin': ENERGY_MARGIN,
            'usable': usable}


def prioritize_candidates(candidates, comparison, *, observations=None):
    """保留曲风／雨天方向优先组与原分数，只在组内先选有相对方向依据的歌。"""
    if not comparison['usable']:
        return candidates
    result = []
    baseline = comparison['baseline_mean']
    lower = comparison['direction'] == 'lower'
    for raw in candidates:
        if observations is None:
            value = _energy(raw.get('audio_features'))
        else:
            record = observations.get(str(raw['id']), {}).get(comparison.get('_group_key'))
            # 候选可能来自较早的检索缓存；同ID换作品后不能套用新作品的观察。
            value = record['energy'] if record and record.get('work_identity') == _identity(raw) else None
        delta = None if value is None else value - baseline
        matched = delta is not None and (delta <= -ENERGY_MARGIN + 1e-9 if lower else delta >= ENERGY_MARGIN - 1e-9)
        protected = raw.get('playability') == 'unavailable' or float(raw.get('taste_score', .5)) < 0
        record = {'energy': value, 'delta': delta, 'matched': matched,
                  'promoted': bool(matched and not protected), 'algorithm_version': ALGORITHM_VERSION}
        item = {**raw, 'relative_strength': record}
        if matched:
            word = '低' if lower else '高'
            item['score_explanation'] = [*(raw.get('score_explanation') or []),
                f'已有同版本片段平均能量比前批均值{word}；不代表全曲听感']
        result.append(item)
    return sorted(result, key=lambda song: (
        bool((song.get('reference_style_priority') or {}).get('matched')),
        bool((song.get('rain_music_direction') or {}).get('matched')),
        song['relative_strength']['promoted']), reverse=True)


def selection_summary(songs, comparison, target_count):
    """数量及覆盖只统计最终选择；未知、反向、同值不能冒充调整成功。"""
    lower = comparison['direction'] == 'lower'
    word, listening = ('低', '更舒缓') if lower else ('高', '更有劲')
    records = [song.get('relative_strength') or {} for song in songs]
    observed = sum(record.get('energy') is not None for record in records)
    supported = sum(record.get('matched') is True for record in records)
    complete = bool(songs and observed == len(songs) and comparison['usable'])
    mean = sum(record['energy'] for record in records) / len(songs) if complete else None
    moved = bool(complete and (mean <= comparison['baseline_mean'] - ENERGY_MARGIN + 1e-9 if lower
                              else mean >= comparison['baseline_mean'] + ENERGY_MARGIN - 1e-9))
    if not comparison['usable']:
        message = ('上一批缺少完整、有效且同版本的片段能量资料，本轮未作相对能量优先；'
                   + ('暂不能确认比上一批更安静。' if lower else '暂不能确认比上一批更有劲。'))
        status = 'baseline_unknown'
    elif not songs:
        message = '保留原条件并排除上一批后，当前没有可返回的新候选，未完成相对调整。'
        status = 'empty'
    else:
        status = 'supported' if supported == len(songs) else 'partial' if supported else 'unsupported'
        message = f'本轮{supported}/{len(songs)}首有同版本片段能量比前批均值更{word}的依据'
        if supported < len(songs):
            message += f'，其余{len(songs) - supported}首按原条件补充，相对方向未确认'
        message += f'；片段能量不等于全曲听感，暂不能保证{listening}。'
    if len(songs) < target_count and songs:
        message += f' 保留原条件和前批排除后，本轮只返回{len(songs)}首。'
    return {**comparison, 'status': status, 'energy_observed': observed,
            'supported': supported, 'selected': len(songs), 'target_count': target_count,
            'selected_mean': mean, 'batch_energy_moved': moved, 'message': message}


def prepare_observed_comparison(direction, snapshot, bundle):
    """每次调整重新取有效观察；比较中途不再引入截止时刻之后的记录。"""
    ids = baseline_ids(snapshot)
    records = snapshot.get('records', []) if ids else []
    by_song = bundle['observations']
    choices = []
    for row in records:
        metadata = bundle['songs'].get(row['id'])
        choices.append(set(by_song.get(row['id'], {})) if metadata and row.get('identity') == _identity(metadata) else set())
    common = set.intersection(*choices) if choices else set()
    # 不同测量模式／配置不拼均值；有完整前台组时固定优先采用前台。
    key = min(common, key=lambda k: (k[3] != 'foreground', k)) if common else None
    group_counts = [sum(group in c for c in choices) for group in set().union(*choices)] if choices else []
    values = [by_song[sid][key]['energy'] for sid in ids] if key else []
    return {'direction': direction, 'basis': 'existing_bound_observations',
            'baseline_total': len(ids), 'baseline_observed': len(ids) if key else max(group_counts, default=0),
            'baseline_available_any': sum(bool(c) for c in choices),
            'baseline_mean': sum(values) / len(values) if values else None,
            'algorithm_version': key[1] if key else None, 'capture_contract': key[2] if key else None,
            'measurement_mode': key[3] if key else None, 'capture_configuration': key[4] if key else None,
            'margin': ENERGY_MARGIN, 'usable': bool(key and direction in ('lower', 'higher')),
            'snapshot_cutoff': bundle['cutoff'], 'read_error': bundle['read_error'],
            'bounded_read': bundle['limited'], '_group_key': key}


def observed_summary(songs, comparison, target_count, *, available=0, batch_choice=False):
    """仅完整且有方向依据的新批可提交；不足时保留队列，不退成普通换歌。"""
    if comparison.get('basis') == 'relative_profile_tags':
        from app.relative_tags import tag_summary
        return tag_summary(songs, comparison, target_count, available=available, batch_choice=batch_choice)
    result = selection_summary(songs, comparison, target_count)
    result = {k: v for k, v in result.items() if not k.startswith('_')}
    blocked = not comparison['usable'] or len(songs) < target_count or result['supported'] != len(songs)
    result['queue_preserved'] = blocked
    result['supported_available'] = available
    if blocked:
        if not comparison['usable']:
            result['status'] = 'baseline_unknown'
            reason = ('现有声学观察暂时无法读取' if comparison.get('read_error') == 'observations_unavailable' else
                      '上一批缺少完整、作品绑定有效且同协议／同配置的片段能量资料')
        else:
            result['status'] = 'candidate_insufficient'
            reason = f'当前候选中有可比片段资料、支持目标方向且满足配额的新歌不足{target_count}首'
        choice = ('若只想普通换歌，请使用这一轮原有的“保持方向，换一批”按钮。' if batch_choice else
                  '若只想普通换歌，可以明确说“保持方向，换一批”。')
        result.update(selected=0, supported=0, energy_observed=0, selected_mean=None, batch_energy_moved=False,
                      message=reason + '，这次未完成相对调整，当前队列保持不变。' + choice)
    return result


def task_refresh_request(message):
    """明确的普通换歌选择独立执行，不把肯定词自动解释为接受降级。"""
    import re
    return bool(re.fullmatch(r'\s*(?:(?:那就|那|好|好的)[，,\s]*)?'
        r'(?:保持(?:原|当前)?方向[，,\s]*|只|仅|普通)?换一(?:批|组)(?:歌|歌曲)?(?:吧|就好|就行)?[。！!\s]*', message or ''))
