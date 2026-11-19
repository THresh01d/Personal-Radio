<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AccountAvatar from "../auth/AccountAvatar.vue";
import PlayerGlass from "../player/PlayerGlass.vue";
import type { MusicAccount, MusicPlatform } from "../../types/radio";

const props = defineProps<{
  accounts: readonly MusicAccount[];
  syncing: boolean;
  activePanel?: string | null;
  desktop?: boolean;
}>();

defineEmits<{
  home: [];
  connect: [platform: MusicPlatform];
  sync: [];
  qqPlaylists: [];
  disconnect: [platform: MusicPlatform];
  clear: [];
  taste: [];
  settings: [];
  desktopSettings: [];
  library: [];
  dj: [];
}>();

const menuOpen = ref(false);
const profileMenu = ref<HTMLElement | null>(null);
const accountTrigger = ref<HTMLButtonElement | null>(null);
const platformNames: Record<MusicPlatform, string> = { netease: "网易云音乐", qq: "QQ 音乐" };
const neteaseAccount = computed(() => props.accounts.find(account => account.platform === "netease"));
const qqAccount = computed(() => props.accounts.find(account => account.platform === "qq"));
const primaryProfile = computed(() => (
  neteaseAccount.value?.profile || props.accounts.find(account => account.profile)?.profile
));

function closeMenu() {
  menuOpen.value = false;
}

function closeMenuAndFocus() {
  closeMenu();
  accountTrigger.value?.focus();
}

function closeMenuFromKeyboard(event: KeyboardEvent) {
  if (!menuOpen.value) return;
  event.stopPropagation();
  closeMenuAndFocus();
}

function onDocumentPointer(event: PointerEvent) {
  if (!profileMenu.value?.contains(event.target as Node)) closeMenu();
}

onMounted(() => document.addEventListener("pointerdown", onDocumentPointer));
onBeforeUnmount(() => document.removeEventListener("pointerdown", onDocumentPointer));
</script>

<template>
  <header class="topbar">
    <button class="brand" type="button" @click="$emit('home')"><span class="brand-dot"></span>私人电台</button>
    <div class="topbar-actions">
      <template v-if="!desktop">
        <nav class="listening-entry" aria-label="听歌方式">
          <button type="button" :aria-pressed="activePanel === 'playlist'" @click="$emit('library')">自己选歌</button>
          <button type="button" :aria-pressed="activePanel === 'chat'" @click="$emit('dj')">DJ 推荐</button>
        </nav>
        <button class="memory-button" type="button" @click="$emit('taste')"><span>◉</span>DJ 记忆</button>
        <button class="ai-settings-button" type="button" @click="$emit('settings')"><span>⌁</span>AI 设置</button>
      </template>
      <div ref="profileMenu" class="profile-menu" @keydown.esc="closeMenuFromKeyboard">
        <button ref="accountTrigger" class="profile-trigger" type="button" :aria-expanded="menuOpen"
          :aria-label="primaryProfile ? `音乐账号 · ${primaryProfile.nickname}` : '音乐账号'"
          :title="primaryProfile?.nickname || '音乐账号'" @click="menuOpen = !menuOpen">
          <AccountAvatar :url="primaryProfile?.avatarUrl" />
          <span class="profile-name">{{ primaryProfile?.nickname || "音乐账号" }}</span>
          <svg class="profile-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
        </button>
        <Transition name="menu-pop">
          <section v-if="menuOpen" class="profile-popover radio-panel" aria-label="音乐账号">
            <PlayerGlass :corner-radius="18" :refract="false" />
            <h2>音乐账号</h2>
            <ul class="profile-accounts">
              <li v-for="account in accounts" :key="account.platform" class="profile-account">
                <AccountAvatar :url="account.profile?.avatarUrl" />
                <div class="profile-account-details">
                  <strong>{{ platformNames[account.platform] }}</strong>
                  <span :title="account.profile?.nickname">{{ account.profile?.nickname || "未连接" }}</span>
                </div>
                <button v-if="account.profile" type="button" :aria-label="`退出${platformNames[account.platform]}`"
                  @click="closeMenuAndFocus(); $emit('disconnect', account.platform)">退出</button>
                <button v-else type="button" :aria-label="`连接${platformNames[account.platform]}`"
                  @click="closeMenuAndFocus(); $emit('connect', account.platform)">扫码登录</button>
              </li>
            </ul>
            <div class="profile-actions">
              <button v-if="neteaseAccount?.profile" type="button" :disabled="syncing"
                @click="closeMenuAndFocus(); $emit('sync')">{{ syncing ? "正在同步…" : "同步网易云歌单" }}</button>
              <button v-if="qqAccount?.profile" type="button" @click="closeMenuAndFocus(); $emit('qqPlaylists')">QQ 音乐歌单</button>
              <button class="danger" type="button" @click="closeMenuAndFocus(); $emit('clear')">清空今夜</button>
            </div>
          </section>
        </Transition>
      </div>
    </div>
    <button v-if="desktop" class="desktop-settings-button" type="button" aria-label="总设置" title="总设置" @click="$emit('desktopSettings')">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.55V21h-4v-.08A1.7 1.7 0 0 0 8.94 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.57 15 1.7 1.7 0 0 0 3 14H3v-4h.08A1.7 1.7 0 0 0 4.6 8.94a1.7 1.7 0 0 0-.34-1.88L4.2 7l2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.57 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15.06 4.6a1.7 1.7 0 0 0 1.88-.34L17 4.2 19.83 7l-.06.06A1.7 1.7 0 0 0 19.43 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z" /></svg>
    </button>
  </header>
</template>

<style scoped>
.profile-menu { position: relative; }

.profile-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 170px;
  min-height: 36px;
  padding: 3px 9px 3px 4px;
  border: 1px solid #dce6f237;
  border-radius: 999px;
  background: linear-gradient(160deg, #b5cae51a, #10141ad6 62%);
  box-shadow: inset 0 1px 0 #ffffff18, 0 3px 14px #0005;
  color: #edf0f5;
  font-size: 12px;
  font-weight: 600;
}

.profile-trigger:hover, .profile-trigger[aria-expanded="true"] {
  border-color: #dce6f261;
  background: linear-gradient(160deg, #bbd1ee28, #151b24e6 62%);
}

.profile-name, .profile-account-details :is(strong, span) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-chevron {
  flex: none;
  width: 13px;
  color: #b2bac7;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: transform .18s;
}

.profile-trigger[aria-expanded="true"] .profile-chevron { transform: rotate(180deg); }

.profile-popover {
  position: absolute;
  z-index: 9;
  inset: calc(100% + 8px) 0 auto auto;
  width: min(280px, calc(100vw - 28px));
  max-height: min(360px, calc(100dvh - 120px));
  overflow: auto;
  isolation: isolate;
  padding: 13px;
  border-radius: 18px;
  background: #10131bf0;
  box-shadow: 0 16px 38px #0008;
  color: #edf0f5;
  text-shadow: none;
}
.profile-popover > .player-glass { background: linear-gradient(155deg, #222b39a6, #0b0f1766); }

.profile-popover h2 { margin: 0 0 10px; font-size: 14px; font-weight: 650; }
.profile-accounts { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.profile-account {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--panel-fill);
}
.profile-account-details { display: grid; flex: 1; min-width: 0; gap: 3px; }
.profile-account-details strong { font-size: 12px; font-weight: 600; }
.profile-account-details span { color: #aeb8c6; font-size: 11px; }

.profile-popover button {
  min-height: 30px;
  padding: 5px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--panel-fill);
  color: #edf0f5;
  font-size: 12px;
  white-space: nowrap;
}
.profile-popover button:hover:not(:disabled) { background: var(--panel-fill-active); }
.profile-popover button:disabled { opacity: .5; }
.profile-popover .danger { color: #e9b5aa; }
.profile-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.profile-menu button:focus-visible { outline: 2px solid #ccd7e5; outline-offset: 2px; }

@media (max-width: 700px) {
  .profile-menu { margin-inline-start: auto; }
}
</style>
