export type LibraryRemovalTarget = { kind: "playlist" | "song"; target_id: string; name: string };
export type LibraryRemovalPreview = {
  kind: "playlist" | "song"; target_ids: string[]; title: string; revision: string;
  affected_song_count: number; removed_song_count: number; kept_shared_count: number;
  source_playlist_count: number; removed_song_ids: string[];
};
export type RemovedLibraryItem = {
  kind: "playlist" | "song"; target_id: string; name: string; artist?: string;
  removed_at: string; operation_revision?: string;
};
