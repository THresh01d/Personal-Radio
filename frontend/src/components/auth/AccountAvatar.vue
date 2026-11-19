<script setup lang="ts">
import { ref } from "vue";

defineProps<{ url?: string | null }>();
const failedUrl = ref<string | null>(null);
</script>

<template>
  <span class="account-avatar" aria-hidden="true">
    <img v-if="url && url !== failedUrl" :key="url" :src="url" alt="" referrerpolicy="no-referrer" @error="failedUrl = url" />
    <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
      <circle cx="12" cy="8" r="3" />
      <path d="M5 19a7 7 0 0 1 14 0" stroke-linecap="round" />
    </svg>
  </span>
</template>

<style scoped>
.account-avatar {
  display: grid;
  flex: none;
  width: 28px;
  height: 28px;
  place-items: center;
  overflow: hidden;
  border: 1px solid #d9e5f337;
  border-radius: 50%;
  background: linear-gradient(145deg, #2c3543, #10151e);
  color: #ccd5e3;
}
.account-avatar img { display: block; width: 100%; height: 100%; object-fit: cover; }
.account-avatar svg { width: 64%; height: 64%; }
</style>
