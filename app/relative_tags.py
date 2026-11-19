"""资料不足时的相对标签方向；规则分值只解释标签变化，不代表声学或听感。"""
from app.relative_strength import _identity, baseline_ids

RULE_VERSION = 'relative-profile-tags-v1'
# 情绪比场景更直接；不由流派、歌名、夜晚或伤感推断强度。
CALM_MOODS = {'平静': 2, '温柔': 1, '治愈': 1}
ACTIVE_MOODS = {'热烈': 2}
MIN_TAG_CHANGE = 1


def tag_direction(song):
    def labels(key):
        values = song.get(key)
        return set(v for v in values if isinstance(v, str)) if isinstance(values, (list, tuple)) else set()
    moods, scenes = labels('mood'), labels('scene')
    calm = sorted(moods & CALM_MOODS.keys())
    active = sorted(moods & ACTIVE_MOODS.keys())
    calm_scene, active_scene = sorted(scenes & {'睡前'}), sorted(scenes & {'运动'})
    if not (calm or active or calm_scene or active_scene):
        return None
    return {'points': sum(CALM_MOODS[t] for t in calm) + len(calm_scene)
            - sum(ACTIVE_MOODS[t] for t in active) - len(active_scene),
            'calm_labels': calm + calm_scene, 'active_labels': active + active_scene}


def prepare_tag_comparison(direction, snapshot, metadata):
    ids = baseline_ids(snapshot)
    records = snapshot.get('records', []) if ids else []
    bound = bool(ids and all(row['id'] in metadata and row.get('identity') == _identity(metadata[row['id']])
                             for row in records))
    profiles = [tag_direction(metadata[sid]) for sid in ids] if bound else []
    values = [p['points'] for p in profiles if p is not None]
    return {'basis': 'relative_profile_tags', 'direction': direction, 'tag_rule_version': RULE_VERSION,
            'baseline_total': len(ids), 'baseline_tag_observed': len(values),
            'baseline_tag_mean': sum(values) / len(values) if values else None,
            'baseline_mean': None, 'margin': MIN_TAG_CHANGE,
            'usable': bool(bound and values and direction in ('lower', 'higher')),
            'baseline_identity_valid': bound}


def prioritize_tag_candidates(candidates, comparison, metadata):
    if not comparison['usable']:
        return []
    result = []
    for raw in candidates:
        current = metadata.get(str(raw['id']))
        # 缓存候选与当前作品必须一致；标签刷新不能掩盖同ID换作品。
        if not current or _identity(current) != _identity(raw):
            continue
        if raw.get('playability') == 'unavailable' or float(raw.get('taste_score', .5)) < 0:
            continue
        acoustic = raw.get('relative_strength') or {}
        # 完整声学前批存在时，已知反向／变化不足的候选不能由标签反向救回。
        if acoustic.get('energy') is not None and not acoustic.get('promoted'):
            continue
        profile = tag_direction(current)
        if profile is None:
            continue
        # 还须有目标方向的正向锚点，不能仅靠去掉反向标签就称作方向尝试。
        if not profile['calm_labels' if comparison['direction'] == 'lower' else 'active_labels']:
            continue
        delta = profile['points'] - comparison['baseline_tag_mean']
        directed = delta if comparison['direction'] == 'lower' else -delta
        if directed < MIN_TAG_CHANGE - 1e-9:
            continue
        record = {'basis': 'relative_profile_tags', **profile, 'tag_delta': delta,
                  'directed_change': directed, 'matched': True, 'promoted': True,
                  'tag_rule_version': RULE_VERSION}
        text = '档案标签相对前批可用标签更偏' + ('舒缓' if comparison['direction'] == 'lower' else '有劲')
        result.append({**raw, 'mood': current.get('mood') or [], 'scene': current.get('scene') or [],
                       'relative_strength': record,
                       'score_explanation': [*(raw.get('score_explanation') or []), text + '；未经声学比较确认']})
    return sorted(result, key=lambda s: (
        bool((s.get('reference_style_priority') or {}).get('matched')),
        bool((s.get('rain_music_direction') or {}).get('matched')),
        s['relative_strength']['directed_change']), reverse=True)


def tag_summary(songs, comparison, target_count, *, available=0, batch_choice=False):
    records = [s.get('relative_strength') or {} for s in songs]
    complete = bool(comparison['usable'] and len(songs) == target_count and all(
        r.get('basis') == 'relative_profile_tags' and r.get('matched')
        and r.get('directed_change', 0) >= MIN_TAG_CHANGE - 1e-9 for r in records))
    result = {k:v for k,v in comparison.items() if not k.startswith('_')}
    result.update(queue_preserved=not complete, status='tag_inferred' if complete else 'tag_insufficient',
                  supported_available=available, selected=len(songs) if complete else 0,
                  supported=len(songs) if complete else 0, energy_observed=0,
                  selected_mean=None, batch_energy_moved=False, batch_tag_direction_moved=complete,
                  target_count=target_count,
                  selected_tag_mean=sum(r['points'] for r in records)/len(records) if complete else None)
    if complete:
        word = '更舒缓' if comparison['direction'] == 'lower' else '更有劲'
        result['message'] = (f'声学资料不足，这次先依据档案标签尝试选得{word}。'
            f'以上一批有方向标签的{comparison["baseline_tag_observed"]}/{comparison["baseline_total"]}首为参照，'
            '新一批的标签方向已有变化；未经声学比较确认，实际听感还请你判断。')
    else:
        choice = ('若只想普通换歌，请使用这一轮原有的“保持方向，换一批”按钮。' if batch_choice else
                  '若只想普通换歌，可以明确说“保持方向，换一批”。')
        result['message'] = (f'现有声学资料和档案标签还不足以在原条件及配额下选出{target_count}首有明确相对方向变化的新歌，'
                             '这次未完成相对调整，当前队列保持不变。' + choice)
    return result
