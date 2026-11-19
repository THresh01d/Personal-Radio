import { getTtsUrl, renewLocalAccess } from "../../services/api";
import type { DjVoice } from "./djVoice";

// 网络语音与本机语音共用取消入口，切歌、关闭和跳过都不能留下迟到的人声。
export function createVoicePlayback(audio: HTMLAudioElement, notify: (message: string) => void) {
  let revision = 0;
  let cancel: (() => void) | null = null;
  let paused = false;
  let pauseActive: (() => void) | null = null;
  let resumeActive: (() => void) | null = null;

  // 暂停期间冻结超时预算，不能将用户暂停误判为网络/语音故障。
  function clock(expired: () => void) {
    let timer = 0, remaining = 0, started = 0;
    const freeze = () => {
      if (timer) remaining = Math.max(1, remaining - (Date.now() - started));
      window.clearTimeout(timer); timer = 0;
    };
    const run = () => {
      if (paused || timer || remaining <= 0) return;
      started = Date.now(); timer = window.setTimeout(() => { timer = 0; expired(); }, remaining);
    };
    return { arm(ms: number) { freeze(); remaining = ms; run(); }, freeze, run,
      clear() { window.clearTimeout(timer); timer = remaining = 0; } };
  }
  function pause() { paused = true; pauseActive?.(); }
  function resume() { if (!paused) return; paused = false; resumeActive?.(); }
  const synthesis = (() => {
    try {
      const engine = window.speechSynthesis;
      engine?.getVoices();
      return engine;
    } catch { return undefined; }
  })();

  function stop() {
    ++revision;
    paused = false;
    cancel?.();
    pauseActive = resumeActive = null;
    cancel = null;
    audio.pause();
    audio.removeAttribute("src");
  }

  function networkVoice(text: string, onStart: () => void, voice: DjVoice) {
    return new Promise<void>((resolve, reject) => {
      let settled = false;
      const request = new AbortController();
      const timer = clock(() => finish(new Error("DJ 语音获取或播放超时")));
      const finish = (error?: Error) => {
        if (settled) return;
        settled = true;
        request.abort();
        timer.clear();
        audio.removeEventListener("playing", started);
        audio.removeEventListener("ended", ended);
        audio.removeEventListener("error", failed);
        cancel = pauseActive = resumeActive = null;
        audio.pause();
        audio.removeAttribute("src");
        if (error) reject(error); else resolve();
      };
      let began = false;
      const started = () => {
        if (paused) { audio.pause(); return; }
        if (audio.paused) return;
        if (!began) { began = true; onStart(); timer.arm(60_000); }
      };
      const ended = () => { if (audio.ended) finish(); };
      const failed = () => { if (audio.error) finish(new Error("网络语音暂时不可用")); };
      cancel = () => finish(new DOMException("已取消播报", "AbortError"));
      let playAttempt = 0;
      const play = () => {
        const attempt = ++playAttempt;
        void audio.play().catch((error) => {
          // pause 会拒绝尚未完成的 play；旧尝试不能结束已恢复的人声。
          if (attempt !== playAttempt || settled) return;
          finish(error);
        });
      };
      pauseActive = () => { ++playAttempt; timer.freeze(); audio.pause(); };
      resumeActive = () => {
        timer.run();
        if (audio.src) play();
      };
      audio.addEventListener("playing", started);
      audio.addEventListener("ended", ended);
      audio.addEventListener("error", failed);
      // 后端允许八秒合成；另留传输时间，避免前端五秒先放弃正常生成的语音。
      timer.arm(15_000);
      // 原生媒体拿不到 HTTP 状态；发声前通过同源握手刷新本机 Cookie，
      // 避免后端热重启后仍拿旧凭证请求 TTS。取消后不允许迟到握手重新开口。
      void renewLocalAccess(request.signal).then(() => {
        if (settled) return;
        try {
          audio.src = getTtsUrl(text, voice);
          if (!paused) play();
        } catch (error) { finish(error instanceof Error ? error : new Error("语音播放失败")); }
      }).catch((error) => { if (!settled) finish(error instanceof Error ? error : new Error("本机语音连接失败")); });
    });
  }

  function localVoice(text: string, onStart: () => void) {
    const voice = synthesis?.getVoices().find((item) => item.localService && /^zh(?:-|_)/i.test(item.lang));
    if (!synthesis || !voice || !window.SpeechSynthesisUtterance) {
      return Promise.reject(new Error("DJ 语音不可用，且未找到本机中文语音；请检查网络或安装系统中文语音"));
    }
    return new Promise<void>((resolve, reject) => {
      const utterance = new window.SpeechSynthesisUtterance(text);
      let settled = false;
      const timer = clock(() => finish(new Error("本机 DJ 语音未能启动或播放超时")));
      let queued = false;
      const finish = (error?: Error) => {
        if (settled) return;
        settled = true;
        timer.clear();
        utterance.onstart = utterance.onend = utterance.onerror = null;
        cancel = pauseActive = resumeActive = null;
        if (error) {
          try { synthesis.cancel(); } catch { /* 取消接口异常也必须结束本次等待。 */ }
        }
        if (error) reject(error); else resolve();
      };
      utterance.voice = voice;
      utterance.lang = voice.lang;
      utterance.volume = audio.volume;
      utterance.onstart = () => {
        onStart();
        timer.arm(60_000);
      };
      utterance.onend = () => finish();
      utterance.onerror = () => finish(new Error("本机 DJ 语音播放失败，请检查系统音量和浏览器声音权限"));
      cancel = () => finish(new DOMException("已取消播报", "AbortError"));
      const start = () => {
        if (settled) return;
        try {
          synthesis.resume();
          if (!queued) { queued = true; synthesis.speak(utterance); }
        } catch { finish(new Error("本机 DJ 语音未能启动")); }
      };
      pauseActive = () => { timer.freeze(); try { synthesis.pause(); } catch { finish(new Error("本机 DJ 语音无法暂停")); } };
      resumeActive = () => { timer.run(); start(); };
      timer.arm(5_000);
      if (!paused) start();
    });
  }

  async function speak(text: string, onStart: () => void, voice: DjVoice) {
    stop();
    const attempt = revision;
    let started = false;
    const markStarted = () => {
      if (started) return;
      started = true;
      onStart();
    };
    try {
      await networkVoice(text.slice(0, 300), markStarted, voice);
    } catch (error) {
      if (attempt !== revision || (error instanceof DOMException && error.name === "AbortError")) return;
      // 已经听到的报幕不换声线从头重念；只有完全未出声才使用本机中文语音。
      if (started) throw error;
      notify("网络语音未能播放，尝试本机中文语音…");
      try { await localVoice(text.slice(0, 300), markStarted); }
      catch (fallbackError) { if (attempt === revision) throw fallbackError; }
    }
  }

  return { speak, stop, pause, resume };
}
