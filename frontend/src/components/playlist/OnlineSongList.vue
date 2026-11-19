<script setup lang="ts">
import { ref } from "vue";
import type { Song } from "../../types/radio";
import SongItem from "./SongItem.vue";

defineProps<{ songs: Song[]; currentSongId?: string; isPlaying: boolean }>();
defineEmits<{ play: [song: Song, queue: Song[]]; enqueue: [song: Song]; next: [song: Song] }>();
const expanded = ref("");
</script>

<template>
  <ol v-if="songs.length" class="playlist online-song-list">
    <SongItem v-for="(song, index) in songs" :key="song.id" :song="song" :index="index"
      :current="currentSongId === song.id" :playing="isPlaying" :expanded="expanded === song.id"
      allow-enqueue allow-next @toggle="expanded = expanded === song.id ? '' : song.id"
      @play="$emit('play', song, songs)" @enqueue="$emit('enqueue', song)" @next="$emit('next', song)" />
  </ol>
</template>
