import type { AudioFeatureSummary } from "./radio";

export type AudioObservationBinding = {
  library_generation: string;
  song_id: string;
  song_revision: string;
  metadata_sha256: string;
  feature_algorithm: "web-audio-v1";
  capture_contract: "media-clock-v1";
  measurement_mode: "foreground";
  capture_configuration: "fft2048-s078-bands-v1";
};

export type AudioObservationLease = {
  observation_id: string;
  binding: AudioObservationBinding;
  expires_at: number;
};

export type AudioObservationPacket = AudioObservationBinding & {
  observation_id: string;
  summary: Omit<AudioFeatureSummary, "algorithm_version">;
  source_extent: "unknown";
  confidence: "uncalibrated";
};
