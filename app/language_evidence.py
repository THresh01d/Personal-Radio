"""演唱语种证据：标题文字和模型推断不能充当硬条件的事实来源。"""
import re

LANGUAGE_ALIASES = {
    '中文': '中文', '国语': '中文', '普通话': '中文', 'chinese': '中文', 'mandarin': '中文',
    '英文': '英文', '英语': '英文', 'english': '英文',
    '日文': '日文', '日语': '日文', 'japanese': '日文',
    '粤语': '粤语', 'cantonese': '粤语', '韩语': '韩语', '韩文': '韩语', 'korean': '韩语',
    '俄语': '俄语', '俄文': '俄语', 'russian': '俄语',
    '法语': '法语', 'french': '法语', '德语': '德语', 'german': '德语',
    '西班牙语': '西班牙语', 'spanish': '西班牙语', '葡萄牙语': '葡萄牙语', 'portuguese': '葡萄牙语',
    '意大利语': '意大利语', 'italian': '意大利语', '多语': '多语', '多语言': '多语',
    '其他': '其他',
}


def normalize_language(value):
    """只规范明确语种名称；多语不强取第一种，未知也不从字符推断。"""
    if isinstance(value, (list, tuple)):
        parts = value
    elif isinstance(value, str):
        parts = re.split(r'[/、,，;；]+', value)
    else:
        return None
    if not parts or any(not isinstance(v, str) for v in parts):
        return None
    values = [LANGUAGE_ALIASES.get(v.strip().casefold()) for v in parts]
    if any(v is None for v in values):
        return None
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else '多语'


def reported_language(song):
    """只接受服务端已绑定作品且处于ready状态的平台字段，不信任自报来源标记。"""
    metadata = song.get('platform_metadata')
    if not isinstance(metadata, dict) or metadata.get('status') != 'ready':
        return None
    return normalize_language(metadata.get('language'))


def language_fields(song):
    """有效字段与旧推断分开；保留原值供解释，不改数据库或冒称已核实。"""
    reported = reported_language(song)
    inferred = normalize_language(song.get('language_inferred', song.get('language')))
    return {'language': reported, 'language_source': 'netease_wiki' if reported else None,
            'language_status': 'platform_reported' if reported else 'unknown',
            'language_inferred': inferred}
