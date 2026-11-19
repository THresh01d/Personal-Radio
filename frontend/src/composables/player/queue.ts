import { ref, type Ref } from "vue";
import type { QueueSource, Song } from "../../types/radio";
import type { PlaybackMode } from "./types";

type QueueOptions = {
  playbackMode: Ref<PlaybackMode>;
  beforeChange: () => void;
  beforeRemoveCurrent: () => void;
};

// 队列只维护可见顺序和手动边界，不持有媒体元素或发起推荐请求。
export function createPlaybackQueue({ playbackMode, beforeChange, beforeRemoveCurrent }: QueueOptions) {
  const currentSong = ref<Song | null>(null);
  const playbackQueue = ref<Song[]>([]);
  const queueSource = ref<QueueSource>("recommendation");
  const queueIndex = ref(-1);

  function prepareQueue(songs: Song[], source: QueueSource = "recommendation") {
    beforeChange();
    queueSource.value = source;
    if (!currentSong.value && songs[0]) {
      currentSong.value = songs[0];
      assignQueue(songs);
      queueIndex.value = 0;
      return;
    }
    assignQueue(songs);
  }

  function shuffleUpcoming(queue: Song[]) {
    // 只洗牌未播放部分，实际顺序直接呈现在队列中；不会随机反复抽中同一首。
    const result = [...queue];
    const first = result.findIndex((song) => song.id === currentSong.value?.id) + 1;
    for (let index = result.length - 1; index > first; index -= 1) {
      const target = first + Math.floor(Math.random() * (index - first + 1));
      [result[index], result[target]] = [result[target]!, result[index]!];
    }
    return result;
  }

  function assignQueue(queue: Song[]) {
    const sameQueue = queue === playbackQueue.value;
    const fixed = queueSource.value === "recommendation" && queue.some(song => song.round_queue_order_fixed);
    playbackQueue.value = playbackMode.value === "shuffle" && !sameQueue && !fixed ? shuffleUpcoming(queue) : queue;
    queueIndex.value = playbackQueue.value.findIndex((song) => song.id === currentSong.value?.id);
  }

  function changedManualQueue(queue: Song[]) {
    // 队列编辑优先于在途 AI 续播，但不能重载正在播放的媒体或重置事件计数。
    beforeChange();
    playbackQueue.value = queue;
    queueSource.value = "manual";
    queueIndex.value = queue.findIndex((song) => song.id === currentSong.value?.id);
  }

  function playNext(song: Song) {
    if (song.id === currentSong.value?.id) return false;
    const queue = playbackQueue.value.filter((item) => item.id !== song.id);
    const index = queue.findIndex((item) => item.id === currentSong.value?.id);
    queue.splice(index + 1, 0, song);
    changedManualQueue(queue);
    if (!currentSong.value) { currentSong.value = song; queueIndex.value = 0; }
    return true;
  }

  function reorderQueue(songId: string, step: -1 | 1) {
    const queue = [...playbackQueue.value];
    const index = queue.findIndex((song) => song.id === songId);
    const target = index + step;
    if (index < 0 || target < 0 || target >= queue.length) return false;
    [queue[index], queue[target]] = [queue[target]!, queue[index]!];
    changedManualQueue(queue);
    return true;
  }

  function removeFromQueue(songId: string) {
    const index = playbackQueue.value.findIndex((song) => song.id === songId);
    if (index < 0) return false;
    const queue = playbackQueue.value.filter((song) => song.id !== songId);
    if (songId === currentSong.value?.id) {
      // 用户移除当前歌不代表要立刻播放别的歌；清理旧请求后只选中邻近歌曲。
      beforeRemoveCurrent();
      currentSong.value = queue[Math.min(index, queue.length - 1)] || null;
    }
    changedManualQueue(queue);
    return true;
  }

  function appendToQueue(song: Song, source: QueueSource = "manual") {
    beforeChange();
    const added = !playbackQueue.value.some((item) => item.id === song.id);
    if (added) {
      playbackQueue.value = [...playbackQueue.value, song];
    }
    // 用户主动改过队列后，播完必须尊重这条手动边界，不再自动接推荐。
    queueSource.value = source;
    if (!currentSong.value) {
      currentSong.value = song;
      queueIndex.value = playbackQueue.value.findIndex((item) => item.id === song.id);
    }
    return added;
  }

  function appendPlaylist(songs: Song[]) {
    const seen = new Set(playbackQueue.value.map(song => song.id));
    const added = songs.filter(song => !seen.has(song.id) && seen.add(song.id));
    if (!added.length) return 0;
    if (!currentSong.value) currentSong.value = added[0]!;
    changedManualQueue([...playbackQueue.value, ...added]);
    return added.length;
  }
  return { currentSong, playbackQueue, queueSource, queueIndex, prepareQueue, shuffleUpcoming, assignQueue,
    playNext, reorderQueue, removeFromQueue, appendToQueue, appendPlaylist };
}
