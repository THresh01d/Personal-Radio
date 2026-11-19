import { ref } from "vue";
import { getLyrics } from "../../services/api";
import type { LyricLine } from "../../types/radio";

// 歌词按歌曲修订绑定，暂停/恢复同曲不会误丢弃仍然有效的结果。
export function createLyrics(getSongRevision: () => number) {
  const lyricLines = ref<LyricLine[]>([]);
  const lyricsInstrumental = ref(false);

  async function loadLyrics(songId: string, token: number) {
    lyricLines.value = [];
    lyricsInstrumental.value = false;
    try {
      const data = await getLyrics(songId);
      if (token !== getSongRevision()) return;
      lyricLines.value = Array.isArray(data.lines) ? data.lines : [];
      lyricsInstrumental.value = Boolean(data.instrumental);
    } catch {
      // 歌词是播放增强能力；平台无歌词或网络异常时静默回到完整声纹舞台。
      if (token === getSongRevision()) {
        lyricLines.value = [];
        lyricsInstrumental.value = false;
      }
    }
  }
  return { lyricLines, lyricsInstrumental, loadLyrics };
}
