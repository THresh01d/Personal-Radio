export type TtsHealth = {
  loaded_version: string;
  installed_version: string;
  required_version: string;
  restart_required: boolean;
  dependency_mismatch: boolean;
  cooldown_seconds: number;
  checking: boolean;
  last_failure_code: string | null;
  last_failure_message: string;
  last_check: null | { ok: boolean; code: string; message: string; audio_bytes: number; elapsed_ms: number; checked_at: number };
};

