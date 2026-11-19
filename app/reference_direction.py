"""只从用户明确给出的音乐词提取方向，并核对具体参考版本。"""
import re

from app import library
from app.recommendation_state import MOODS, _genre_values, genre_text_signals, parse_intent
from app.retrieval import artist_identity_keys
from app.scene import unnegated_terms

_REGIONAL_POP = ('华语流行', '日本流行', '韩国流行')
_TRACK_NAME = r'(?P<artist>[^《》，。！？!?；;]{1,48}?)?《(?P<title>[^《》]+)》'
NAMED_REFERENCE = re.compile(r'(?:类似|接近|像)' + _TRACK_NAME)
SAVE_REFERENCE = re.compile(
    r'(?:请)?(?:记住[，,：:\s]*)?以后[，,\s]*(?:我)?(?:拿|用|以|把)\s*'
    + _TRACK_NAME + r'(?:当|作|作为|为)(?:参考曲|参考)(?:的?时(?:候)?)?[，,：:\s]*(?P<direction>.+)'
)
USE_PREVIOUS = re.compile(r'(?:就)?(?:按|沿用)(?:刚才|上面|这个|刚才这个)(?:的)?方向(?:找|选|挑|推荐)?(?:歌)?')


def describe(direction):
    return '、'.join([*direction.get('moods', []), *([direction['style']] if direction.get('style')
                    else direction.get('genres', [])), *('少一点' + item for item in direction.get('exclude', []))])


def parse(text):
    """情绪与风格是搜索线索；评价旧歌的那一段由调用方先拆开。"""
    text = re.sub(r'《[^》]*》', '', text)
    positive, excluded = genre_text_signals(text)
    regional = next((style for style in _REGIONAL_POP if style in positive), '')
    positive_moods = {term for _, term in unnegated_terms(text, MOODS)}
    moods = [label for label in MOODS if label in positive_moods]
    return {'genres': _genre_values(text, None), 'moods': moods,
            'style': regional, 'exclude': excluded}


def resolve_named(title, artist='', preferred_id=None):
    title = title.strip()
    artist = (artist or '').strip().removesuffix('的').strip()
    hits = [song for song in library.find_songs_by_title(title) if song['title'].casefold() == title.casefold()
            and (not artist or bool(artist_identity_keys(artist) & artist_identity_keys(song.get('artist'))))]
    if len(hits) == 1:
        return hits[0]
    if preferred_id:
        hit = next((song for song in hits if str(song['id']) == str(preferred_id)), None)
        if hit:
            return hit
    raise ValueError('请用完整歌名和歌手指定参考曲，或先在播放器选中对应版本。')


def selection(text, *, reference_id=None):
    parsed, provided = parse_intent(text, {'query': text})
    if reference_id:
        parsed['reference_song_id'] = reference_id
        provided['reference_song_id'] = True
    return {key: parsed.get(key) for key, present in provided.items() if present}


def matches_style(song, direction):
    from app.platform_metadata import style_details_for_song
    style = direction.get('style')
    return not style or f'流行/{style}' in style_details_for_song(song)


def matches_labels(song, direction):
    return (matches_style(song, direction)
            and all(not direction.get(key) or bool(set(song.get(field) or ()) & set(direction[key]))
                    for key, field in (('moods', 'mood'), ('genres', 'genre')))
            and not set(direction['exclude']).intersection(song.get('genre') or ()))
