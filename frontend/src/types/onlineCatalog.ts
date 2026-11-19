import type { MusicPlatform, Song } from "./radio";

export const platformNames = { netease: "网易云音乐", qq: "QQ 音乐" } as const;
export const searchKinds = { song: "单曲", artist: "歌手", album: "专辑" } as const;
export type SearchKind = keyof typeof searchKinds;
export type OnlineSong = Song & { platform: MusicPlatform; kind: "song" };
export type OnlineCollection = {
  platform: MusicPlatform;
  kind: "playlist" | "artist" | "album";
  id: string;
  title: string;
  artist: string;
  cover_url?: string | null;
};
export type OnlineSearchPage = { items: (OnlineSong | OnlineCollection)[]; has_more: boolean };
export type OnlineTracksPage = { songs: Song[]; has_more: boolean };
