"""HTTP请求模型；限制与默认值集中维护，业务服务接收普通参数。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from typing import Annotated
from app import audio_features

DJLength = Literal["short", "normal", "auto"]
QueueSongId = Annotated[str, Field(min_length=1, max_length=128)]


class ChatRequest(BaseModel):
    message: str = Field(max_length=2000)
    session_id: str | None = None  # 前端 localStorage 保存，刷新后恢复上下文
    current_song_id: str | None = None  # “类似这首”绑定播放器中的真实歌曲
    current_batch_id: str | None = Field(default=None, min_length=1, max_length=128)
    current_queue_song_ids: list[QueueSongId] | None = Field(default=None, max_length=50)
    memory_owner_id: str | None = Field(default=None, min_length=1, max_length=128)
    dj_length: DJLength | None = None
    bind_dj_queue: bool = False


class ClearRequest(BaseModel):
    session_id: str


class LibraryRemovalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["playlist", "song"]
    target_id: str = Field(min_length=1, max_length=128)


class LibraryRemovalRequest(LibraryRemovalTarget):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdjustmentRequest(BaseModel):
    session_id: str
    batch_id: str
    operation: Literal["replace", "gentler", "energize", "other-artists", "refresh"]
    song_id: str | None = None
    dj_length: DJLength | None = None
    bind_dj_queue: bool = False


class DJQueueRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    song_ids: list[QueueSongId] = Field(min_length=1, max_length=50)


class FeedbackRequest(BaseModel):
    """推荐卡片上的 👍/👎 反馈。action 由 Literal 限定，非法值返回 422。"""
    session_id: str
    song_id: str
    action: Literal["like", "dislike"]
    artist: str | None = None  # 保留原反馈的署名元数据，不用于推断整位歌手的偏好


class TaskFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    song_id: str = Field(min_length=1, max_length=128)


class MemoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=128)
    memory_owner_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal['song', 'artist', 'genre', 'expression', 'reference']
    target: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=32)
    scene: str = Field(default='', max_length=20)
    reference: str = Field(default='', max_length=200)
    expected_id: str | None = Field(default=None, max_length=64)


class ReferenceFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=128)
    batch_id: str = Field(min_length=1, max_length=128)
    song_id: str = Field(min_length=1, max_length=128)
    value: Literal['like', 'unlike']


class ListeningContext(BaseModel):
    """来源仅是本机播放器声明，不能冒充经过服务端音频审计的听感事实。"""
    model_config = ConfigDict(extra='forbid')
    contract: Literal['playback-v2']
    playback_id: str = Field(min_length=1, max_length=128)
    start_reason: Literal['user', 'auto_next', 'repeat_one', 'user_replay']
    interrupted: bool
    played_seconds: FiniteFloat = Field(ge=0, le=86400)


class ListeningEventRequest(BaseModel):
    """前端播放器上报的原始行为；event_id 让网络重试保持幂等。"""
    event_id: str
    session_id: str
    song_id: str
    event_type: Literal["play", "skip", "finish", "replay"]
    batch_id: str | None = None
    position_seconds: FiniteFloat = Field(default=0, ge=0)
    duration_seconds: FiniteFloat | None = Field(default=None, ge=0)
    context: ListeningContext | None = None


class AudioFeaturesRequest(BaseModel):
    """前端 Web Audio 对一次真实播放的统计摘要；不包含原始音频。"""
    # 旧路径不能静默吞掉新协议编号，让客户端误以为获得了重复提交保护。
    model_config = ConfigDict(extra="forbid")
    song_id: str
    # HTTP 在保存前拦截 NaN/无穷大；内部调用仍由保存层独立校验。
    energy_mean: FiniteFloat
    energy_peak: FiniteFloat
    low_ratio: FiniteFloat
    brightness: FiniteFloat
    dynamic_range: FiniteFloat
    beat_density: FiniteFloat
    sample_seconds: FiniteFloat
    algorithm_version: str = audio_features.ALGORITHM_VERSION


class AutoQueueRequest(BaseModel):
    session_id: str | None = None
    song_id: str
    count: int = 3
    dj_length: DJLength | None = None
    bind_dj_queue: bool = False


class PendingAudioFeaturesRequest(BaseModel):
    song_ids: list[str]


class ObservationPrepareRequest(BaseModel):
    """新协议显式准备；不接受客户端时间、编号或自选算法。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    song_id: str = Field(min_length=1, max_length=128)
    measurement_mode: Literal["foreground", "background"]


class ObservationSummary(BaseModel):
    """拒绝数字字符串/布尔和非有限数，范围及关系交由基础层再次校验。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    energy_mean: Annotated[FiniteFloat, Field(strict=True)]
    energy_peak: Annotated[FiniteFloat, Field(strict=True)]
    low_ratio: Annotated[FiniteFloat, Field(strict=True)]
    brightness: Annotated[FiniteFloat, Field(strict=True)]
    dynamic_range: Annotated[FiniteFloat, Field(strict=True)]
    beat_density: Annotated[FiniteFloat, Field(strict=True)]
    sample_seconds: Annotated[FiniteFloat, Field(strict=True)]


class ObservationCancelRequest(BaseModel):
    """编号放在受限请求体中，避免租约标识进入访问日志路径。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    observation_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ObservationSubmitRequest(ObservationCancelRequest):
    """完整绑定与原样摘要；不能静默丢弃新协议字段或投回旧摘要路由。"""
    library_generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    song_id: str = Field(min_length=1, max_length=128)
    song_revision: str = Field(pattern=r"^[0-9a-f]{32}$")
    metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_algorithm: Literal["web-audio-v1"]
    capture_contract: Literal["media-clock-v1"]
    measurement_mode: Literal["foreground", "background"]
    capture_configuration: Literal["fft2048-s078-bands-v1", "fft2048-s072-bands-v1"]
    summary: ObservationSummary
    source_extent: Literal["unknown"]
    confidence: Literal["uncalibrated"]


class LLMSettingsRequest(BaseModel):
    provider: Literal["offline", "deepseek", "doubao", "openai_compatible", "ollama", "chatgpt"]
    model: str = Field("", max_length=200)
    base_url: str = Field("", max_length=2048)
    api_key: str | None = Field(None, max_length=4096)
    chatgpt_profile_id: str | None = Field(None, max_length=24)


class LLMConnectionRequest(LLMSettingsRequest):
    """测试连接只使用本次请求中的 Key，不会在测试成功前偷偷保存。"""
    check_capabilities: bool = False

