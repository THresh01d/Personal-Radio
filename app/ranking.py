"""推荐核心 2.0 的可解释混合排序。

六个通道使用路线图约定的固定权重。没有证据的通道使用 0.5 中性分，保证旧歌、
尚未完成声学分析的歌不会被误伤；中性分不进入面向用户的解释，避免把“未知”
包装成“匹配”。可播放性不是偏好分，而是独立保护因子：只有平台明确报告不可用
时才降权，未知状态不扣分也不宣称可播放。
"""

from app.audio_features import acoustic_similarity
from app.language_evidence import reported_language
from app.platform_metadata import STYLE_DETAIL_KEYS, style_details_for_song

RANKING_VERSION = "hybrid-v2"
RANKING_WEIGHTS = {
    "semantic": 0.35,
    "tag": 0.20,
    "acoustic": 0.15,
    "taste": 0.15,
    "transition": 0.10,
    "freshness": 0.05,
}
NEUTRAL_SCORE = 0.5
DIRECT_STYLE_SOURCES = frozenset({'user_confirmed', 'netease_wiki'})
# 这些标签仍覆盖多种唱法、编曲和情绪，不能仅凭地域和流行大类保证相似。
REGIONAL_POP_DETAILS = frozenset({'流行/华语流行', '流行/日本流行', '流行/韩国流行'})

ENERGY_TARGETS = {
    "low": 0.22,
    "medium-low": 0.38,
    "medium": 0.55,
    "medium-high": 0.70,
    "high": 0.84,
}


def acoustic_match(song, filters):
    """按期待能量匹配实播特征；没有能量意图时不假造声学分。"""
    target_name = getattr(filters, "energy", None)
    if target_name not in ENERGY_TARGETS:
        return None, False
    features = song.get("audio_features") or {}
    if features.get("energy_mean") is None:
        return None, False
    target = ENERGY_TARGETS[target_name]
    energy = max(0.0, min(1.0, float(features["energy_mean"])))
    max_distance = max(target, 1 - target)
    return max(0.0, 1 - abs(energy - target) / max_distance), True


def _normalized(values):
    return {str(value).casefold() for value in (values or []) if str(value).strip()}


def reference_semantic_enabled(filters):
    """标签获准使用不连带启用旧向量；未传独立决策的旧调用者保持原门槛。"""
    supported = filters.reference_semantic_genres
    if supported is None:
        supported = filters.reference_genres
    return bool(filters.genres or supported)


def _reference_specificity(filters):
    """细分与宽泛标签分开：流行大类本身不足以获得参考相似优先。"""
    genres = _normalized(filters.reference_genres)
    direct = filters.reference_genre_source in DIRECT_STYLE_SOURCES
    values = filters.reference_style_details or ()
    values = values if isinstance(values, (tuple, list)) else ()
    details = tuple(dict.fromkeys(value for value in values
        if isinstance(value, str) and value in STYLE_DETAIL_KEYS
        and value.split('/', 1)[0].casefold() in genres)) if direct else ()
    mode = 'detail' if details else 'broad_only' if direct and genres == {'流行'} else 'coarse'
    return details, mode


def tag_match_details(song, filters):
    """计算活跃标签维度的命中比例，并返回可解释的真实命中项。"""
    dimensions = []
    details = []
    song_genres = _normalized(song.get("genre"))
    song_moods = _normalized(song.get("mood"))
    song_scenes = _normalized(song.get("scene"))

    requested_genres = _normalized(filters.genres or filters.reference_genres)
    if requested_genres:
        reference_details, specificity = _reference_specificity(filters)
        if not filters.genres and reference_details:
            hits = set(reference_details).intersection(style_details_for_song(song))
            # 同一大类但不同子类不能靠大类重合拿满分；原权重与其他维度保持。
            dimensions.append(len(hits) / len(reference_details))
            if hits:
                details.append('参考细分曲风重合（平台标签）：' + ' · '.join(sorted(hits)))
        elif not filters.genres and specificity == 'broad_only':
            dimensions.append(0.0)
        else:
            score, label = _genre_match(song_genres, requested_genres, filters)
            dimensions.append(score)
            if label:
                details.append(label)

    requested_moods = _normalized(filters.moods)
    if requested_moods:
        hits = requested_moods & song_moods
        dimensions.append(bool(hits))
        if hits:
            details.append("情绪命中：" + " · ".join(sorted(hits)))

    requested_scenes = _normalized(getattr(filters, "scenes", ()))
    if requested_scenes:
        hits = requested_scenes & song_scenes
        dimensions.append(bool(hits))
        if hits:
            details.append("场景命中：" + " · ".join(sorted(hits)))

    if filters.lang:
        hit = reported_language(song) == filters.lang
        dimensions.append(hit)
        if hit:
            details.append(f"语种命中：{filters.lang}")

    if filters.want_instrumental:
        hit = bool(song.get("is_instrumental"))
        dimensions.append(hit)
        if hit:
            details.append("类型命中：纯音乐")

    score = sum(dimensions) / len(dimensions) if dimensions else 0.0
    return score, details


def _genre_match(song_genres, requested_genres, filters):
    """保留旧大类计分；新的细分分支只在平台给出可用子标签时启用。"""
    hits = requested_genres & song_genres
    score = bool(hits) if filters.genres else len(hits) / len(requested_genres)
    if hits:
        source_label = {'user_confirmed': '用户确认', 'netease_wiki': '网易云百科'}.get(
            filters.reference_genre_source, '标签推断')
        label = "风格命中：" if filters.genres else f"参考软风格重合（{source_label}）："
        return score, label + " · ".join(sorted(hits))
    return score, ''


def taste_score_from_factor(factor):
    """把旧版乘数映射到有界有符号通道；明确反感强、行为推断弱。"""
    value = max(0.0, float(factor or 1.0))
    if value <= 0.15:
        return -2.0
    if value <= 0.3:
        return -1.0
    if value < 1.0:
        # 行为降权 0.70 -> 0，明显弱于用户主动表态但足以打破接近的语义分。
        return max(-1.0, min(NEUTRAL_SCORE, (value - 0.7) * (5 / 3)))
    if value >= 1.0:
        return min(1.0, NEUTRAL_SCORE + (value - 1.0) * 2.0)
    return NEUTRAL_SCORE


def _playability(song):
    """只相信明确的平台事实；unknown 不降权，unavailable 才进入保护因子。"""
    status = str(song.get("playability") or "unknown")
    if status == "unavailable":
        return status, 0.25
    if status == "playable":
        return status, 1.0
    return "unknown", 1.0


def _weighted_score(components):
    return sum(components[name] * weight for name, weight in RANKING_WEIGHTS.items())


def _set_taste_component(song, factor):
    """替换一个候选的个人偏好通道，并从六路分数重新计算总分。"""
    updated = dict(song)
    components = dict(updated["score_components"])
    taste_score = taste_score_from_factor(factor)
    components["taste"] = taste_score
    hybrid = _weighted_score(components)
    playability_factor = float(updated.get("playability_factor", 1.0))
    updated.update({
        "taste_factor": round(float(factor), 4),
        "taste_score": round(taste_score, 4),
        "score_components": {key: round(value, 4) for key, value in components.items()},
        "hybrid_score": round(hybrid, 4),
        "final_score": round(hybrid * playability_factor, 4),
    })
    explanation = [
        item for item in updated.get("score_explanation") or []
        if not item.startswith("个人偏好")
    ]
    if abs(float(factor) - 1.0) > 1e-9:
        explanation.append(
            f"个人偏好 {taste_score:.2f} × {RANKING_WEIGHTS['taste']:.0%}"
            f"（原始系数 ×{float(factor):.2f}）"
        )
    updated["score_explanation"] = explanation
    return updated


def apply_taste_factors(candidates, factor_getter):
    """为已经过统一排序的候选注入个人偏好通道并重新排序。"""
    return sorted(
        (_set_taste_component(song, factor_getter(song)) for song in candidates),
        key=lambda song: song["final_score"],
        reverse=True,
    )


def prioritize_reference_candidates(candidates, filters, reference_song_id):
    """只在原候选内优先采用双方都有人工/平台曲风重合的作品，分数保持不变。"""
    reference_details, specificity = _reference_specificity(filters)
    enabled = bool(filters.reference_genres and filters.reference_genre_source in DIRECT_STYLE_SOURCES
                   and specificity != 'broad_only')
    reference_genres = _normalized(filters.reference_genres)
    result = []
    for raw in candidates:
        overlap = (set(reference_details).intersection(style_details_for_song(raw)) if reference_details
                   else reference_genres.intersection(_normalized(raw.get('genre'))))
        # 明确不可播放或主动反感仍保留原保护，不能靠曲风分组翻回首位；不是硬删除。
        protected = (raw.get('playability') == 'unavailable' or float(raw.get('taste_score', NEUTRAL_SCORE)) < 0)
        matched = bool(enabled and raw.get('genre_source') in DIRECT_STYLE_SOURCES and overlap and not protected)
        item = {**raw, 'reference_style_priority': {
            'reference_song_id': str(reference_song_id), 'genres': list(filters.reference_genres),
            'source': filters.reference_genre_source, 'enabled': enabled, 'matched': matched,
            'style_details': list(reference_details), 'specificity': specificity,
            'match_basis': 'platform_detail' if reference_details else 'coarse' if enabled else 'none'}}
        if matched:
            item['score_explanation'] = [*(raw.get('score_explanation') or []),
                ('参考细分曲风优先：双方平台子标签有重合，组内沿用原综合分排序' if reference_details
                 else '参考曲风优先：双方人工或平台标签有重合，组内沿用原综合分排序')]
        result.append(item)
    # 输入已按含品味的原综合分排列；稳定分组保留组内排序和补充候选的原顺序。
    return sorted(result, key=lambda song: song['reference_style_priority']['matched'], reverse=True)


def reference_priority_summary(songs, target_count):
    """汇总真正返回的作品，避免把候选标签数量说成最终选歌数量。"""
    if not songs:
        return None
    records = [s.get('reference_style_priority') for s in songs]
    if not all(isinstance(r, dict) for r in records):
        return None
    first = records[0]
    context = ('reference_song_id', 'genres', 'source', 'enabled', 'style_details', 'specificity')
    if not first.get('reference_song_id') or any(any(r.get(k) != first.get(k) for k in context) for r in records):
        return None  # 混合旧批次/不同参考方向时不编造统一来源结论。
    matched = sum(r.get('matched') is True for r in records)
    fallback = len(songs) - matched
    if first.get('specificity') == 'broad_only':
        message = '参考歌曲目前只有“流行”这类宽泛标签，无法据此确认相似方向；本轮沿用其余依据挑选，可能包含不同听感的歌曲。'
    elif not first.get('enabled'):
        message = '参考歌曲缺少本轮可直接采用的人工或平台曲风标签，沿用原推荐方式；风格接近程度尚不能确认。'
    elif fallback:
        kind = '同细分曲风' if first.get('style_details') else '同风格'
        message = f'当前候选中可优先采用的{kind}标签作品不足，另补充了{fallback}首按原方式挑选的歌，可能包含不同曲风。'
    elif len(songs) < target_count:
        if all(r.get('mood_matches') for r in records):
            message = f'只有{len(songs)}首同时有曲风和情绪线索支持，这轮先听这些。'
        else:
            message = f'符合本轮限制的候选数量不足，本轮只返回{len(songs)}首有曲风标签支持的歌。'
    else:
        message = ''
    return {**{k: first.get(k) for k in context}, 'matched': matched, 'fallback': fallback,
            'target_count': target_count, 'message': message}


def narrow_regional_pop_reference(candidates, filters, positive_ids):
    """曲风与情绪分别提供线索；资料不足的作品不拿来凑数。"""
    reference_moods = set(filters.reference_moods)
    supported = []
    for song in candidates:
        if song.get('playability') == 'unavailable' or song['taste_score'] < 0:
            continue
        explicit_match = str(song['id']) in positive_ids
        record = song['reference_style_priority']
        moods = set((song.get('platform_metadata') or {}).get('mood') or ())
        overlap = reference_moods & moods
        # 平台现有分类可以明确包含说唱；被平台覆盖的旧档案只作为冲突线索降序。
        if not explicit_match and (not record['matched'] or len(overlap) < min(2, len(reference_moods))
                                   or '说唱' in (song.get('genre') or ())):
            continue
        ratio = len(overlap) / len(reference_moods | moods)
        legacy_conflict = song.get('genre_source') != 'user_confirmed' and '说唱' in (song.get('genre_original') or ())
        record = {**record, 'mood_matches': sorted(overlap)}
        supported.append(((not explicit_match, legacy_conflict, -ratio), {**song, 'reference_style_priority': record}))
    # 曲风和情绪共同支持时，保留原综合分作为同等证据的排序依据。
    return [song for _, song in sorted(supported, key=lambda item: item[0])]


def rank_candidates(candidates, filters, recent_ids=None, reference_features=None):
    """为所有召回路径附加同一组六路分数，再按最终分统一排序。"""
    recent = {str(song_id) for song_id in (recent_ids or [])}
    ranked = []
    for raw in candidates:
        song = dict(raw)
        raw_semantic = song.get("score")
        semantic = (
            NEUTRAL_SCORE if raw_semantic is None
            else max(0.0, min(1.0, float(raw_semantic)))
        )
        # 单曲新来源只放宽标签使用；旧文本与声学组合仍独立检查原跨作品门槛。
        # 指代句的查询字面始终不能冒充参考文本，避免再次损失已认可的声学邻曲。
        semantic_usable = not reference_features or (
            song.get('semantic_source') == 'reference_embedding'
            and reference_semantic_enabled(filters))
        if not semantic_usable:
            semantic = NEUTRAL_SCORE
        tag_score, tag_details = tag_match_details(song, filters)
        freshness = 0.0 if str(song.get("id")) in recent else 1.0
        acoustic_score, acoustic_observed = acoustic_match(song, filters)
        transition_score = (
            acoustic_similarity(reference_features, song.get("audio_features") or {})
            if reference_features else None
        )
        # 地域流行参考只让片段比较占一个通道；已有明确的能量方向可单独计入。
        transition_component = transition_score
        if reference_features and acoustic_score is None:
            acoustic_score = transition_score
            acoustic_observed = transition_score is not None
            if filters.reference_moods:
                transition_component = None

        taste_factor = float(song.get("taste_factor") or 1.0)
        taste_score = taste_score_from_factor(taste_factor)
        components = {
            "semantic": semantic,
            "tag": tag_score,
            "acoustic": acoustic_score if acoustic_score is not None else NEUTRAL_SCORE,
            "taste": taste_score,
            "transition": transition_component if transition_component is not None else NEUTRAL_SCORE,
            "freshness": freshness,
        }
        hybrid = _weighted_score(components)
        playability_status, playability_factor = _playability(song)

        routes = list(song.get("recall_routes") or [])
        if not routes:
            routes.append("semantic" if raw_semantic is not None else "exact")
        if tag_score > 0 and "tag" not in routes:
            routes.append("tag")

        explanation = []
        if raw_semantic is not None and semantic_usable:
            label = "参考歌曲文本档案相似" if song.get("semantic_source") == "reference_embedding" else "语义匹配"
            explanation.append(
                f"{label} {semantic:.2f} × {RANKING_WEIGHTS['semantic']:.0%}"
            )
        if tag_details:
            explanation.extend(tag_details)
            explanation.append(
                f"标签匹配 {tag_score:.2f} × {RANKING_WEIGHTS['tag']:.0%}"
            )
        if acoustic_observed and acoustic_score is not None:
            label = "与参考歌曲声学接近" if reference_features else "实播能量匹配"
            explanation.append(
                f"{label} {acoustic_score:.2f} × {RANKING_WEIGHTS['acoustic']:.0%}"
            )
        if transition_component is not None:
            explanation.append(
                f"播放衔接 {transition_score:.2f} × {RANKING_WEIGHTS['transition']:.0%}"
            )
        if freshness == 0:
            explanation.append("最近推荐过，新鲜度降为 0")
        if playability_status == "unavailable":
            explanation.append("平台已明确不可播放，保护性降权")

        song.update({
            "ranking_version": RANKING_VERSION,
            "ranking_weights": dict(RANKING_WEIGHTS),
            "semantic_score": round(semantic, 4),
            "tag_score": round(tag_score, 4),
            "acoustic_score": round(acoustic_score, 4) if acoustic_score is not None else None,
            "acoustic_observed": acoustic_observed,
            "taste_factor": round(taste_factor, 4),
            "taste_score": round(taste_score, 4),
            "transition_score": round(transition_score, 4) if transition_score is not None else None,
            "freshness_score": freshness,
            "playability": playability_status,
            "playability_factor": playability_factor,
            "score_components": {key: round(value, 4) for key, value in components.items()},
            "hybrid_score": round(hybrid, 4),
            "final_score": round(hybrid * playability_factor, 4),
            "recall_routes": routes,
            "score_explanation": explanation,
        })
        ranked.append(song)
    return sorted(ranked, key=lambda item: item["final_score"], reverse=True)
