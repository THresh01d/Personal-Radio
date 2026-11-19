import { onBeforeUnmount, onMounted, ref } from "vue";
import { getSetupStatus, pauseNcmMetadata, checkLoginQr, createLoginQr, dismissNcmSync, getLatestNcmSync, getLoginStatus, getNcmPlaylists, getNcmSyncStatus, logoutNcm, retryNcmSync, syncNcmPlaylists } from "../services/api";
import type { LoginStatus, MusicAccountProfile, NcmPlaylist, NcmSyncResult, NcmSyncScope } from "../types/radio";

export function useNcmAuth(notify: (message: string) => void) {
  const profile = ref<MusicAccountProfile | null>(null);
  const qrOpen = ref(false);
  const qrImage = ref("");
  const qrStatus = ref("");
  const syncing = ref(false);
  const syncOpen = ref(false);
  const playlists = ref<NcmPlaylist[]>([]);
  const playlistsLoading = ref(false);
  const playlistsError = ref("");
  const syncScope = ref<NcmSyncScope>("liked");
  const selectedPlaylistIds = ref<string[]>([]);
  const syncTask = ref<NcmSyncResult | null>(null);
  const tagCoverage = ref<import("../types/radio").TagCoverage | null>(null);
  const syncProgressError = ref("");
  const syncProgressReading = ref(false);
  let progressFailures = 0;
  let progressRevision = 0;
  let accountRevision = 0;
  let disposed = false;
  let qrTimer: number | undefined;
  let syncTimer: number | undefined;

  function setProfile(status: Pick<LoginStatus, "nickname" | "avatar_url"> | null) {
    ++accountRevision;
    profile.value = status ? {
      nickname: status.nickname || "网易云用户",
      avatarUrl: status.avatar_url || null,
    } : null;
  }

  async function refreshLoginStatus() {
    const revision = accountRevision;
    try {
      const status = await getLoginStatus();
      if (disposed || revision !== accountRevision) return;
      setProfile(status.logged_in ? status : null);
      if (status.storage_warning) notify(status.storage_warning);
    } catch {
      if (!disposed && revision === accountRevision) setProfile(null);
    }
  }

  async function openQr() {
    window.clearInterval(qrTimer);
    qrOpen.value = true;
    qrImage.value = "";
    qrStatus.value = "正在向网易云获取二维码…";
    try {
      const data = await createLoginQr();
      if (!data.key || !data.qrimg) throw new Error(data.detail || "二维码获取失败");
      qrImage.value = data.qrimg;
      qrStatus.value = "请使用网易云音乐扫码";
      qrTimer = window.setInterval(async () => {
        try {
          const result = await checkLoginQr(data.key!);
          if (result.status === "scanned") qrStatus.value = "已扫码，请在手机上确认";
          if (result.status === "confirmed") {
            window.clearInterval(qrTimer);
            setProfile(result);
            qrStatus.value = "登录成功，欢迎回来";
            window.setTimeout(() => {
              qrOpen.value = false;
              void openSync();
            }, 650);
            notify("网易云已登录，接下来选择要留在本地的歌单");
          }
          if (result.status === "expired") {
            window.clearInterval(qrTimer);
            qrStatus.value = "二维码已过期，点击刷新";
          }
        } catch (error) {
          // 安全存储失败不能永远停在“等待确认”；保留二维码和下次轮询恢复能力。
          qrStatus.value = error instanceof Error ? error.message : "登录检查暂时失败，正在重试";
        }
      }, 2000);
    } catch (error) {
      qrStatus.value = error instanceof Error ? error.message : "二维码获取失败，点击刷新";
    }
  }

  function closeQr() {
    window.clearInterval(qrTimer);
    qrOpen.value = false;
  }

  async function logout() {
    try {
      await logoutNcm();
      setProfile(null);
      notify("网易云登录已退出");
    } catch (error) {
      notify(error instanceof Error ? error.message : "退出登录失败");
    }
  }

  async function loadPlaylists() {
    playlistsLoading.value = true;
    playlistsError.value = "";
    try {
      playlists.value = await getNcmPlaylists();
      if (!selectedPlaylistIds.value.length) {
        const liked = playlists.value.find((playlist) => playlist.is_liked);
        selectedPlaylistIds.value = liked ? [liked.id] : [];
      }
    } catch (error) {
      playlistsError.value = error instanceof Error ? error.message : "歌单读取失败";
    } finally {
      playlistsLoading.value = false;
    }
  }

  async function openSync() {
    syncOpen.value = true;
    if (syncing.value && syncTask.value?.task_id) {
      void reconnectSync();
    } else if (!syncing.value) {
      syncTask.value = await getLatestNcmSync().catch(() => null);
      if (["pending", "running"].includes(syncTask.value?.status || "") && syncTask.value?.task_id) {
        syncing.value = true;
        void watchSync(syncTask.value.task_id);
      }
    }
    await Promise.all([loadPlaylists(), refreshCoverage()]);
  }

  async function openSyncForPlaylist(playlistId: string) {
    // 曲库抽屉里的“同步/更新”应直接定位目标，不让用户在数百张歌单中再找一次。
    syncScope.value = "selected";
    selectedPlaylistIds.value = [playlistId];
    await openSync();
  }

  async function refreshCoverage() {
    try { tagCoverage.value = (await getSetupStatus()).library.tag_coverage || null; }
    catch { /* 曲库统计暂不可用不妨碍账号与同步恢复。 */ }
  }

  async function completeMetadata() {
    await syncPlaylist("metadata");
  }

  async function pauseMetadata() {
    if (!syncTask.value?.task_id) return;
    try { await pauseNcmMetadata(syncTask.value.task_id); notify("正在暂停；已完成的百科会保留"); }
    catch (error) { notify(error instanceof Error ? error.message : "暂停失败"); }
  }

  function closeSync() {
    if (syncing.value && (syncTask.value?.phase === "metadata" || syncProgressError.value)) { syncOpen.value = false; return; }
    if (!syncing.value) {
      window.clearTimeout(syncTimer);
      const task = syncTask.value;
      if (task?.task_id && ["partial_failed", "failed", "interrupted"].includes(task.status || "")) {
        void dismissNcmSync(task.task_id).catch(() => undefined);
      }
      syncTask.value = null;
      syncOpen.value = false;
    }
  }

  async function reconnectSync() {
    const taskId = syncTask.value?.task_id;
    if (!taskId || disposed) return;
    progressFailures = 0;
    await watchSync(taskId);
  }

  async function watchSync(taskId: string) {
    if (disposed) return;
    window.clearTimeout(syncTimer);
    const revision = ++progressRevision;
    syncProgressReading.value = true;
    try {
      const task = await getNcmSyncStatus(taskId);
      if (disposed || revision !== progressRevision) return;
      syncTask.value = task;
      syncProgressError.value = "";
      progressFailures = 0;
      const terminal = ["completed", "partial_failed", "failed", "interrupted"].includes(task.status || "");
      if (!terminal) {
        syncTimer = window.setTimeout(() => { void watchSync(taskId); }, 700);
        return;
      }
      syncing.value = false;
      await refreshCoverage();
      if (disposed || revision !== progressRevision) return;
      if (task.status === "completed") {
        notify(`曲库与百科任务已完成：新增 ${task.added || 0} 首，更新 ${task.updated || 0} 首`);
      } else {
        notify(task.error || "同步未完整完成，可以重试");
      }
    } catch {
      if (disposed || revision !== progressRevision) return;
      // 读状态失败不能替后台宣布任务失败，更不能自动重发同步写请求。
      syncProgressError.value = "暂时无法读取进度，后台任务可能仍在运行。恢复连接后可重新读取进度。";
      progressFailures++;
      if (progressFailures < 3) {
        syncTimer = window.setTimeout(() => { void watchSync(taskId); }, 1000 * progressFailures);
      }
    } finally {
      if (!disposed && revision === progressRevision) syncProgressReading.value = false;
    }
  }

  async function syncPlaylist(scopeOverride?: NcmSyncScope) {
    if (syncing.value || disposed) return;
    ++progressRevision;
    syncProgressError.value = "";
    progressFailures = 0;
    syncing.value = true;
    syncTask.value = null;
    try {
      const data = await syncNcmPlaylists(scopeOverride || syncScope.value, selectedPlaylistIds.value);
      if (!data.ok || !data.task_id) throw new Error(data.error || "同步任务创建失败");
      syncTask.value = data;
      await watchSync(data.task_id);
    } catch (error) {
      syncing.value = false;
      notify(error instanceof Error ? error.message : "同步失败，请稍后重试");
    }
  }

  async function retrySync() {
    if (syncing.value || disposed) return;
    ++progressRevision;
    syncProgressError.value = "";
    progressFailures = 0;
    const taskId = syncTask.value?.task_id;
    if (!taskId) return;
    syncing.value = true;
    try {
      const data = await retryNcmSync(taskId);
      if (!data.ok || !data.task_id) throw new Error(data.error || "同步重试创建失败");
      syncTask.value = data;
      await watchSync(data.task_id);
    } catch (error) {
      syncing.value = false;
      notify(error instanceof Error ? error.message : "同步重试失败");
    }
  }

  onMounted(() => { void refreshLoginStatus(); });
  onBeforeUnmount(() => {
    disposed = true;
    ++progressRevision;
    window.clearInterval(qrTimer);
    window.clearTimeout(syncTimer);
  });

  return {
    profile,
    qrOpen,
    qrImage,
    qrStatus,
    syncing,
    syncOpen,
    playlists,
    playlistsLoading,
    playlistsError,
    syncScope,
    syncTask,
    tagCoverage,
    syncProgressError,
    syncProgressReading,
    reconnectSync,
    completeMetadata,
    pauseMetadata,
    selectedPlaylistIds,
    openQr,
    closeQr,
    openSync,
    openSyncForPlaylist,
    closeSync,
    loadPlaylists,
    logout,
    syncPlaylist,
    retrySync,
  };
}

