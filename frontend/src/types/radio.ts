export type Song = {
  id: string;
  platform?: MusicPlatform;
  online_catalog?: boolean;
  title: string;
  artist: string;
  album?: string;
  duration_seconds?: number;
  reason?: string;
  dj_intro?: string;
  round_intro?: string;
  round_program_version?: string;
  round_segments?: DJSegment[];
  round_queue_order_fixed?: boolean;
  round_opening_priority?: boolean;
  round_opening_independent?: boolean;
  round_copy_status?: string;
  round_opening_status?: string;
  genre?: string[];
  genre_source?: "user_confirmed" | "netease_wiki";
  mood?: string[];
  scene?: string[];
  language?: string | null;
  is_instrumental?: boolean;
  semantic_score?: number;
  tag_score?: number;
  taste_score?: number;
  transition_score?: number | null;
  freshness_score?: number;
  taste_factor?: number;
  final_score?: number;
  ranking_version?: string;
  ranking_weights?: Record<string, number>;
  score_components?: Record<string, number>;
  playability?: "playable" | "unavailable" | "unknown";
  recall_routes?: string[];
  score_explanation?: string[];
  batch_id?: string;
  reference_song_id?: string;
  reference_title?: string;
  source?: "manual_library" | "recommendation";
  cover_url?: string;
  acoustic_score?: number | null;
  audio_features?: (AudioFeatureSummary & {
    observation_count?: number;
    updated_at?: string;
  }) | null;
};

export type ReviewScope = "query_positive" | "global_genre";
export type SongReview = {
  review_id: string; revision: number; status: "pending" | "confirmed" | "revoked";
  payload: { song_id: string; metadata_sha256: string; scope: ReviewScope; genres: string[] | null; query_key: string | null };
  stale?: boolean; recorded_at?: string;
};
export type SongReviewSnapshot = {
  song: { song_id: string; title: string; artist: string; album: string };
  metadata_sha256: string; original_genres: string[]; effective_genres: string[];
  platform_metadata?: { raw_genres?: string[]; genre?: string[]; raw_moods?: string[]; bpm?: number | null; status?: string } | null;
  active_review_id: string | null; conflict: boolean; reviews: SongReview[];
  total: number; has_more: boolean; genre_options: string[];
  context: { batch_id: string; query: string } | null; notice: string;
};

export type ChatMessage = {
  id?: number;
  role: "user" | "assistant";
  content: string;
  notices?: string[];
  diagnostic?: DiagnosticRecord & { notices?: string[] };
};

export type DiagnosticRecord = {
  request_id: string;
  category: string;
  status: number | null;
  elapsed_ms: number;
  stages: Record<string, { calls: number; elapsed_ms: number }>;
  running_stages: string[];
  codes: string[];
};

export type RequestDiagnostics = {
  capacity: number;
  active_capacity: number;
  recent: DiagnosticRecord[];
  active: DiagnosticRecord[];
  scope: string;
};

export type ChatResponse = {
  answer: string;
  songs: Song[];
  session_id?: string;
  message_id?: number;
  batch_id?: string | null;
  dj_program?: DJProgram | null;
  stats?: {
    queue_preserved?: boolean;
    replacement?: { batch_id: string; song_id: string };
    picks?: number;
    channel?: string;
    ranking_version?: string;
    intent?: Record<string, unknown>;
    diagnostics?: (DiagnosticRecord & { notices?: string[] }) | null;
  };
};

export type RecommendationRound = {
  answer: string;
  songs: Song[];
  batch_id?: string;
  dj_program?: DJProgram | null;
  playback_song_ids?: string[];
};

export type AdjustmentOperation = "replace" | "gentler" | "energize" | "other-artists" | "refresh";
export type DJLength = "short" | "normal";
export type DJSegment = {
  id: string;
  before_song_id: string;
  after_song_id?: string | null;
  position: number;
  standalone?: boolean;
  text: string;
  sources: Array<{ id: string; text: string; url: string; entity_url: string; source: string }>;
};
export type DJProgram = {
  batch_id: string;
  confirmation_only?: boolean;
  queue_order_required?: boolean;
  playback_song_ids?: string[];
  message_id?: number;
  notices?: string[];
  status: "pending" | "ready" | "fallback";
  text: string;
  mode: "sourced" | "metadata";
  max_seconds: number;
  sources: Array<{ id: string; text: string; url: string; entity_url: string; source: string }>;
  program_version?: string;
  segments?: DJSegment[];
  copy_status?: string;
  copy_source?: string;
  copy_length?: DJLength;
  copy_reasons?: string[];
  opening_priority?: boolean;
  opening_independent?: boolean;
  opening_status?: string;
};

export type PlaylistEntry = {
  song: Song;
  heading: string;
};

export type PlaylistView = "queue" | "playlists" | "library" | "history" | "recommended" | "search";
export type QueueSource = "recommendation" | "manual";

export type LocalLibraryPlaylist = {
  id: string;
  name: string;
  track_count: number;
  remote_track_count: number;
  remote_updated_at: number;
  cover_url?: string | null;
  is_liked: boolean;
  synced_at: string | null;
};

export type LocalLibrarySongs = {
  songs: Song[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
  playlist_id?: string | null;
  query: string;
  order: "playlist" | "title" | "random";
  library_stats?: {
    unique_songs: number;
    linked_songs: number;
    unassigned_songs: number;
  } | null;
};

export type MusicAccountProfile = {
  nickname: string;
  avatarUrl: string | null;
};

export type MusicPlatform = "netease" | "qq";

export type QqPlaylist = { id: string; name: string; track_count: number | null; cover_url: string | null };

export type MusicAccount = {
  platform: MusicPlatform;
  profile: MusicAccountProfile | null;
};

export type LoginStatus = {
  logged_in: boolean;
  nickname?: string | null;
  avatar_url?: string | null;
  credential_storage?: "keyring" | "legacy-file";
  storage_warning?: string | null;
};

export type NcmPlaylist = {
  id: string;
  name: string;
  track_count: number;
  is_liked: boolean;
  cover_url?: string | null;
  remote_updated_at: number;
  creator_id?: string | null;
  owned_by_user: boolean;
  subscribed: boolean;
};

export type TagCoverage = { total: number; genre_count: number; unknown_genre_count: number; platform_ready: number; platform_empty: number; platform_failed: number; platform_pending: number; platform_eligible_count?: number; platform_unlinked_count?: number; platform_genre_count?: number; platform_without_genre_count?: number };
export type NcmSyncScope = "liked" | "selected" | "all" | "metadata";

export type NcmSyncResult = {
  ok?: boolean;
  task_id?: string;
  status?: "pending" | "running" | "completed" | "partial_failed" | "failed" | "interrupted";
  phase?: string;
  processed?: number;
  total?: number;
  added?: number;
  updated?: number;
  metadata_total?: number;
  metadata_processed?: number;
  metadata_failed?: number;
  tag_coverage?: TagCoverage;
  skipped_playlists?: number;
  completed_playlists?: number;
  failed_playlists?: number;
  failed_playlist_ids?: string[];
  recoverable?: boolean;
  error?: string;
  scope?: NcmSyncScope;
};

export type LLMProviderId = "offline" | "deepseek" | "doubao" | "openai_compatible" | "ollama" | "chatgpt";

export type LLMProviderOption = {
  id: LLMProviderId;
  default_model: string;
  default_base_url: string;
  needs_api_key: boolean;
  has_saved_key?: boolean;
};

export type LLMSettings = {
  provider: LLMProviderId;
  model: string;
  base_url: string;
  mode: "offline" | "cloud" | "local_model";
  needs_api_key: boolean;
  has_api_key: boolean;
  chatgpt_profile_id?: string | null;
  updated_at?: string | null;
  providers: LLMProviderOption[];
};

export type ChatGPTStatus = {
  account_id: string | null;
  profiles: { id: string; label: string; connected: boolean }[];
  status: "idle" | "pending" | "exchanging" | "connected" | "expired" | "cancelled" | "failed";
  message: string;
};

export type SetupStatus = {
  library: {
    song_count: number;
    embedding_count: number;
    profile_count: number;
    tag_coverage?: TagCoverage;
    ready: boolean;
  };
  netease: {
    credential_present: boolean;
    nickname?: string | null;
  };
  llm: LLMSettings;
};

export type FeedbackAction = "like" | "dislike" | "not_now" | "reference_like" | "reference_unlike";

export type ListeningEventType = "play" | "skip" | "finish" | "replay";
export type ListeningContext = {
  contract: "playback-v2";
  playback_id: string;
  start_reason: "user" | "auto_next" | "repeat_one" | "user_replay";
  interrupted: boolean;
  played_seconds: number;
};

export type AudioFeatureSummary = {
  energy_mean: number;
  energy_peak: number;
  low_ratio: number;
  brightness: number;
  dynamic_range: number;
  beat_density: number;
  sample_seconds: number;
  algorithm_version: "web-audio-v1";
};

export type LyricLine = {
  time: number;
  text: string;
  translation?: string;
};

export type TasteSong = {
  id: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  genre: string[];
  mood: string[];
  reason: string;
  factor: number;
  confidence: number;
  source: "explicit_feedback" | "listening_behavior" | "task_feedback";
  task_id?: string;
  scope: "long_term" | "session" | "task";
  metadata_available: boolean;
  evidence?: {
    early_skips: number;
    finishes: number;
    replays: number;
  };
};

export type TasteArtist = {
  artist: string;
  reason: string;
  factor: number;
  confidence: number;
  source: "explicit_feedback";
  scope: "long_term";
};

export type TasteProfile = {
  session_id: string;
  scene_memories?: SceneMemory[];
  preferences?: MemoryPreference[];
  task_feedback?: { task_id: string | null; query: string; songs: TasteSong[] };
  explicit: {
    liked_songs: TasteSong[];
    disliked_songs: TasteSong[];
    boosted_artists: TasteArtist[];
    demoted_artists: TasteArtist[];
  };
  behavior: {
    quality?: { legacy_events: number; loop_events: number; interrupted_events: number; insufficient_events: number };
    boosted_songs: TasteSong[];
    demoted_songs: TasteSong[];
    evidence: Record<string, {
      early_skips: number;
      finishes: number;
      replays: number;
    }>;
  };
  summary: {
    plays: number;
    skips: number;
    finishes: number;
    replays: number;
  };
  boundaries: Record<"long_term" | "session" | "context", {
    label: string;
    description: string;
  }>;
  acoustic: {
    analyzed_songs: number;
    total_songs: number;
    coverage_percent: number;
    sample_hours: number;
    recent_songs: Array<{
      id: string;
      title: string;
      artist: string;
      energy_mean: number;
      low_ratio: number;
      brightness: number;
      beat_density: number;
      sample_seconds: number;
      observation_count: number;
      updated_at: string;
    }>;
  };
};

export type SceneMemory = {
  id: string;
  scene: string;
  song_id: string;
  song: { id: string; title: string; artist: string; album: string };
  source_text: string;
  created_at: number;
  updated_at: number;
  version: number;
  effective: boolean;
  reason: string;
};

export type MemoryDraft = { kind: 'song' | 'artist' | 'genre' | 'expression' | 'reference'; target: string; value: string; scene: string; reference: string; expected_id?: string };
export type MemoryKind = MemoryDraft['kind'] | 'reference_direction';
export type MemoryPreference = {
  id: string; kind: MemoryKind; scene: string; value: string; value_label: string;
  label: string; target_key: string; source_text: string; reason: string;
  effective: boolean; version: number;
  target: { id?: string; title?: string; artist?: string; album?: string; label?: string;
    direction?: { moods: string[]; genres: string[]; style: string; exclude: string[] };
    reference?: { id: string; title: string; artist: string; album: string } };
};
