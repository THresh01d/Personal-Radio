import { onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";
import type { AudioFeatureSummary } from "../types/radio";
import { createMediaCaptureClock } from "./audioCaptureClock";

export type VisualMode = "quiet" | "atmosphere" | "immersive";
export type AudioAnalysisStatus = "idle" | "ready" | "active" | "no-signal" | "unavailable";

/**
 * 从正式播放器的 HTMLAudioElement 读取实时频谱。
 *
 * 这里不保存音频，也不创建第二条播放流。AudioContext 只负责把同一个音频元素
 * 接到分析器再送往扬声器；只上报一首歌的统计摘要，不上传原始频谱或音频。
 */
export function useAudioAnalysis(audio: HTMLAudioElement, visualMode: Ref<VisualMode>, onFallback?: () => void) {
  const low = ref(0);
  const mid = ref(0);
  const high = ref(0);
  const energy = ref(0);
  const beat = ref(0);
  // 给舞台保留细分频段；三段平均值继续驱动环境灯光。
  const spectrum = ref<number[]>(Array(48).fill(0));
  const status = ref<AudioAnalysisStatus>("idle");
  const message = ref("开始播放后读取真实频谱");
  let disabled = false;
  let disposed = false;
  let generation = 0;
  let startRevision = 0;

  let context: AudioContext | null = null;
  let source: MediaElementAudioSourceNode | null = null;
  let analyser: AnalyserNode | null = null;
  let bins: Uint8Array<ArrayBuffer> | null = null;
  let frame = 0;
  let lastSampleAt = 0;
  let movingEnergy = 0;
  let movingLow = 0;
  let visualReference = 0.32;
  let lastBeatAt = 0;
  let preparing: Promise<boolean> | null = null;
  let capture: {
    sampleSeconds: number;
    weightedEnergy: number;
    energyPeak: number;
    weightedLow: number;
    weightedMid: number;
    weightedHigh: number;
    beats: number;
    histogram: number[];
  } | null = null;
  const captureClock = createMediaCaptureClock();
  let boundAudio: HTMLAudioElement | null = null;
  const captureEvents = ["waiting", "seeking", "seeked", "pause", "ended", "ratechange", "emptied", "loadedmetadata"];
  let backgroundRunning = false;
  let cancelBackground: (() => void) | null = null;

  // 恢复上下文和后台媒体都必须有界；取消后迟到结果不能再接管声音链路。
  async function bounded<T>(task: Promise<T>, milliseconds: number, signal?: AbortSignal): Promise<T> {
    let timer = 0;
    let abort: () => void = () => {};
    const boundary = new Promise<never>((_, reject) => {
      abort = () => reject(new Error("音频分析已取消"));
      if (signal?.aborted) { abort(); return; }
      signal?.addEventListener("abort", abort, { once: true });
      timer = window.setTimeout(() => reject(new Error("音频分析等待超时")), milliseconds);
    });
    try { return await Promise.race([task, boundary]); }
    finally { window.clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }

  function closeGraph() {
    try { source?.disconnect(); } catch { /* 已断开的节点不阻止清理。 */ }
    try { analyser?.disconnect(); } catch { /* 同上。 */ }
    if (context) void context.close().catch(() => {});
    context = null; source = null; analyser = null; bins = null;
  }

  function disable(reason: string, replace = false) {
    disabled = true;
    ++generation;
    cancelBackgroundAnalysis();
    stop();
    closeGraph();
    status.value = "unavailable";
    message.value = reason;
    if (replace && !disposed) onFallback?.();
  }

  function useOrdinaryAudio(next: HTMLAudioElement, reason = "音源不支持频谱读取，已切换普通播放") {
    disable(reason);
    unbindCaptureEvents();
    audio = next;
  }

  function beginFeatureCapture() {
    capture = {
      sampleSeconds: 0,
      weightedEnergy: 0,
      energyPeak: 0,
      weightedLow: 0,
      weightedMid: 0,
      weightedHigh: 0,
      beats: 0,
      histogram: Array(20).fill(0),
    };
    captureClock.reset();
  }

  function percentile(histogram: number[], ratio: number) {
    const total = histogram.reduce((sum, count) => sum + count, 0);
    if (!total) return 0;
    const target = total * ratio;
    let seen = 0;
    for (let index = 0; index < histogram.length; index += 1) {
      seen += histogram[index] || 0;
      if (seen >= target) return (index + 0.5) / histogram.length;
    }
    return 1;
  }

  function summarizeCapture(result: typeof capture): AudioFeatureSummary | null {
    // 持续零信号不能生成“已分析”的假档案；不猜测它是静音还是平台限制。
    if (!result || result.sampleSeconds < 12 || result.energyPeak === 0) return null;
    const totalBands = result.weightedLow + result.weightedMid + result.weightedHigh;
    return {
      energy_mean: result.weightedEnergy / result.sampleSeconds,
      energy_peak: result.energyPeak,
      low_ratio: totalBands ? result.weightedLow / totalBands : 0,
      brightness: totalBands ? result.weightedHigh / totalBands : 0,
      dynamic_range: Math.max(0, percentile(result.histogram, 0.9) - percentile(result.histogram, 0.1)),
      beat_density: result.beats / result.sampleSeconds * 60,
      sample_seconds: result.sampleSeconds,
      algorithm_version: "web-audio-v1",
    };
  }

  function finishFeatureCapture(): AudioFeatureSummary | null {
    const result = capture;
    capture = null;
    captureClock.reset();
    return summarizeCapture(result);
  }

  function resetLevels() {
    low.value = 0;
    mid.value = 0;
    high.value = 0;
    energy.value = 0;
    beat.value = 0;
    spectrum.value = Array(48).fill(0);
    movingEnergy = 0;
    movingLow = 0;
    visualReference = 0.32;
    lastBeatAt = 0;
    status.value = disabled ? "unavailable" : context ? "ready" : "idle";
  }

  function averageBand(values: Uint8Array<ArrayBuffer>, fromHz: number, toHz: number, fullScale = 180) {
    if (!context) return 0;
    const nyquist = context.sampleRate / 2;
    const start = Math.max(0, Math.floor((fromHz / nyquist) * values.length));
    const end = Math.min(values.length, Math.ceil((toHz / nyquist) * values.length));
    if (end <= start) return 0;
    let total = 0;
    for (let index = start; index < end; index += 1) total += values[index] || 0;
    // 舞台保留原有增益。冰面检测用完整 0–255 范围，以区别高响度下的鼓点。
    return Math.min(1, total / (end - start) / fullScale);
  }

  // 雪夜按需读同一个分析器。这个可选的看波形操作失败也不影响扬声器或频谱。
  function readWaveform(buffer: Float32Array<ArrayBuffer>) {
    if (!analyser || !bins || context?.state !== "running" || audio.paused || audio.seeking
      || buffer.length !== analyser.fftSize || document.hidden) return null;
    try { analyser.getFloatTimeDomainData(buffer); }
    catch { return null; }
    return { sampleRate: context.sampleRate, bass: averageBand(bins, 28, 110, 255) };
  }

  function sample(timestamp: number) {
    frame = 0;
    if (!analyser || !bins || audio.paused || document.hidden || disposed) { captureClock.reset(); return; }
    if (context?.state !== "running") {
      disable("音频分析上下文已中断，已恢复普通播放", true);
      return;
    }
    frame = window.requestAnimationFrame(sample);
    // 30fps 足以驱动环境动画；没有必要让 Vue 每秒更新 60 次。
    if (timestamp - lastSampleAt < 33) return;
    lastSampleAt = timestamp;
    try { analyser.getByteFrequencyData(bins); }
    catch { disable("频谱读取失败，已恢复普通播放", true); return; }

    const nextLow = averageBand(bins, 20, 250);
    const nextMid = averageBand(bins, 250, 2_000);
    const nextHigh = averageBand(bins, 2_000, 10_000);
    const nextEnergy = Math.min(1, nextLow * 0.42 + nextMid * 0.38 + nextHigh * 0.2);
    movingEnergy = movingEnergy ? movingEnergy * 0.92 + nextEnergy * 0.08 : nextEnergy;
    movingLow = movingLow ? movingLow * 0.9 + nextLow * 0.1 : nextLow;

    // 只调显示强度，保留原始能量用于鼓点判断。慢速参考值避免歌曲间响度差
    // 让安静的歌几乎静止、响歌又把光线和频谱推到满格；零信号仍然是零。
    if (nextEnergy > 0.03) {
      visualReference += (nextEnergy - visualReference) * (nextEnergy > visualReference ? 0.025 : 0.008);
    }
    const visualGain = Math.max(0.72, Math.min(1.85, 0.42 / Math.max(0.12, visualReference)));

    // 对数分桶让低频和人声都占有可见空间；平滑只消除闪烁，不制造无声时的波形。
    const previous = spectrum.value;
    const nextSpectrum = Array.from({ length: 48 }, (_, index) => {
      const fromHz = 35 * (12_000 / 35) ** (index / 48);
      const toHz = 35 * (12_000 / 35) ** ((index + 1) / 48);
      const level = Math.min(1, averageBand(bins!, fromHz, toHz) * visualGain);
      const old = previous[index] || 0;
      return old + (level - old) * (level > old ? 0.46 : 0.22);
    });

    low.value = Math.min(1, nextLow * visualGain);
    mid.value = Math.min(1, nextMid * visualGain);
    high.value = Math.min(1, nextHigh * visualGain);
    energy.value = Math.min(1, nextEnergy * visualGain);
    spectrum.value = nextSpectrum;
    // 低频瞬态加最短间隔，比整体响度阈值更接近鼓点；它仍不是 BPM 检测。
    const lowTransient = nextLow > Math.max(0.14, movingLow * 1.16)
      && nextEnergy > Math.max(0.09, movingEnergy * 1.05)
      && timestamp - lastBeatAt > 220;
    if (lowTransient) lastBeatAt = timestamp;
    if (capture) {
      const delta = captureClock.observe(audio, timestamp, true);
      if (delta > 0) {
        capture.sampleSeconds += delta;
        capture.weightedEnergy += nextEnergy * delta;
        capture.energyPeak = Math.max(capture.energyPeak, nextEnergy);
        capture.weightedLow += nextLow * delta;
        capture.weightedMid += nextMid * delta;
        capture.weightedHigh += nextHigh * delta;
        capture.histogram[Math.min(19, Math.floor(nextEnergy * 20))] += 1;
        if (lowTransient) capture.beats += 1;
      }
    }
    if (visualMode.value === "quiet") {
      low.value = 0; mid.value = 0; high.value = 0; energy.value = 0; beat.value = 0;
      spectrum.value = Array(48).fill(0);
    } else {
      beat.value = lowTransient ? 1 : Math.max(0, beat.value - 0.12);
    }
    status.value = bins.some((value) => value > 0) ? "active" : "no-signal";
    message.value = status.value === "active" ? "正在读取真实音频频谱" : "暂未读到频谱：可能是静音段或音源限制";
  }

  async function prepare() {
    if (disabled || disposed) return false;
    if (preparing) return preparing;
    const revision = generation;
    preparing = (async () => {
      let nextContext: AudioContext | null = null;
      let nextSource: MediaElementAudioSourceNode | null = null;
      try {
        if (!context) {
          const AudioContextClass = window.AudioContext;
          if (!AudioContextClass) throw new Error("浏览器不支持 Web Audio");
          nextContext = new AudioContextClass();
          // 先恢复 Context，再创建 MediaElementSource。恢复失败时音频元素仍保持
          // 普通播放链路，不会因为一个悬停的 AudioContext 变成无声。
          await bounded(nextContext.resume(), 1500);
          if (disposed || revision !== generation) { void nextContext.close().catch(() => {}); return false; }
          if (nextContext.state !== "running") throw new Error("上下文未运行");
          // 分析器是旁路：节点创建或采样不能成为扬声器输出的串联依赖。
          nextSource = nextContext.createMediaElementSource(audio);
          nextSource.connect(nextContext.destination);
          const nextAnalyser = nextContext.createAnalyser();
          nextAnalyser.fftSize = 2048;
          nextAnalyser.smoothingTimeConstant = 0.78;
          nextSource.connect(nextAnalyser);
          context = nextContext;
          source = nextSource;
          analyser = nextAnalyser;
          bins = new Uint8Array(nextAnalyser.frequencyBinCount);
          // 后台页也可能被浏览器中断上下文；不能只等下一帧才发现输出已暂停。
          context.onstatechange = () => {
            if (!disabled && !disposed && !audio.paused && context?.state !== "running") void prepare();
          };
        } else if (context.state === "suspended") {
          await bounded(context.resume(), 1500);
        }
        if (disposed || revision !== generation) return false;
        if (context?.state !== "running") throw new Error("上下文未运行");
        status.value = "ready";
        message.value = audio.paused ? "播放暂停，恢复后读取真实频谱" : "等待真实频谱信号";
        return true;
      } catch {
        const attached = Boolean(nextSource || source);
        if (nextContext && nextContext !== context) void nextContext.close().catch(() => {});
        if (!disposed && revision === generation) disable("音频分析不可用，音乐使用普通播放", attached);
        return false;
      } finally {
        preparing = null;
      }
    })();
    return preparing;
  }

  function start() {
    const revision = ++startRevision;
    void prepare().then((ready) => {
      if (!ready || revision !== startRevision || audio.paused || frame || document.hidden || disposed) return;
      frame = window.requestAnimationFrame(sample);
    });
  }

  async function analyzeUrlInBackground(url: string, seconds = 15): Promise<AudioFeatureSummary | null> {
    if (backgroundRunning || !url || document.hidden || !audio.paused || disposed || disabled) return null;
    backgroundRunning = true;
    const request = new AbortController();
    // 在任何 await 之前注册取消，包括上下文恢复、metadata 和 play 等待。
    cancelBackground = () => request.abort();
    const deadline = window.setTimeout(() => request.abort(), Math.min(30, Math.max(12, seconds)) * 1000 + 10_000);
    const media = new Audio();
    media.crossOrigin = "anonymous";
    media.preload = "auto";
    let mediaSource: MediaElementAudioSourceNode | null = null;
    let localAnalyser: AnalyserNode | null = null;
    let silentGain: GainNode | null = null;
    let timer = 0;
    const backgroundClock = createMediaCaptureClock();
    const resetBackgroundClock = () => backgroundClock.reset();
    for (const event of captureEvents) media.addEventListener(event, resetBackgroundClock);
    const result = {
      sampleSeconds: 0, weightedEnergy: 0, energyPeak: 0,
      weightedLow: 0, weightedMid: 0, weightedHigh: 0,
      beats: 0, histogram: Array(20).fill(0),
    };
    try {
      if (!await bounded(prepare(), 2000, request.signal) || !context || request.signal.aborted || document.hidden || !audio.paused) return null;
      await bounded(new Promise<void>((resolve, reject) => {
        media.onloadedmetadata = () => resolve();
        media.onerror = () => reject(new Error("背景音频无法加载"));
        // 先注册事件再设置地址，缓存命中也不会漏掉加载完成。
        media.src = url;
      }), 8000, request.signal);
      if (Number.isFinite(media.duration) && media.duration > 45) {
        media.currentTime = Math.min(30, media.duration * 0.18);
      }
      mediaSource = context.createMediaElementSource(media);
      localAnalyser = context.createAnalyser();
      localAnalyser.fftSize = 2048;
      localAnalyser.smoothingTimeConstant = 0.72;
      silentGain = context.createGain();
      silentGain.gain.value = 0;
      mediaSource.connect(localAnalyser);
      localAnalyser.connect(silentGain);
      silentGain.connect(context.destination);
      const values = new Uint8Array(localAnalyser.frequencyBinCount);
      let previousLow = 0;
      let previousEnergy = 0;
      let lastBeat = 0;
      await bounded(media.play(), 5000, request.signal);
      await bounded(new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          resolve();
        };
        media.onended = finish;
        timer = window.setInterval(() => {
          if (document.hidden || !audio.paused || context?.state !== "running") { request.abort(); return; }
          if (!localAnalyser) return;
          const now = performance.now();
          const delta = backgroundClock.observe(media, now, true);
          if (delta <= 0) { previousLow = 0; previousEnergy = 0; lastBeat = now; return; }
          try { localAnalyser.getByteFrequencyData(values); }
          catch { request.abort(); return; }
          const nextLow = averageBand(values, 20, 250);
          const nextMid = averageBand(values, 250, 2_000);
          const nextHigh = averageBand(values, 2_000, 10_000);
          const nextEnergy = Math.min(1, nextLow * 0.42 + nextMid * 0.38 + nextHigh * 0.2);
          const transient = nextLow > Math.max(0.14, previousLow * 1.16)
            && nextEnergy > Math.max(0.09, previousEnergy * 1.05)
            && now - lastBeat > 220;
          if (transient) { result.beats += 1; lastBeat = now; }
          previousLow = previousLow ? previousLow * 0.9 + nextLow * 0.1 : nextLow;
          previousEnergy = previousEnergy ? previousEnergy * 0.92 + nextEnergy * 0.08 : nextEnergy;
          result.sampleSeconds += delta;
          result.weightedEnergy += nextEnergy * delta;
          result.energyPeak = Math.max(result.energyPeak, nextEnergy);
          result.weightedLow += nextLow * delta;
          result.weightedMid += nextMid * delta;
          result.weightedHigh += nextHigh * delta;
          result.histogram[Math.min(19, Math.floor(nextEnergy * 20))] += 1;
          if (result.sampleSeconds >= Math.min(30, Math.max(12, seconds))) finish();
        }, 100);
      }), Math.min(30, Math.max(12, seconds)) * 1000 + 5000, request.signal);
      if (request.signal.aborted) return null;
      return summarizeCapture(result);
    } catch {
      return null;
    } finally {
      window.clearInterval(timer);
      window.clearTimeout(deadline);
      media.onloadedmetadata = null; media.onerror = null; media.onended = null;
      for (const event of captureEvents) media.removeEventListener(event, resetBackgroundClock);
      media.pause();
      media.removeAttribute("src");
      media.load();
      // 清理时单个节点已经断开也不能阻止释放后台运行锁。
      for (const node of [mediaSource, localAnalyser, silentGain]) {
        try { node?.disconnect(); } catch { /* 继续清理其他节点。 */ }
      }
      cancelBackground = null;
      backgroundRunning = false;
    }
  }

  function cancelBackgroundAnalysis() {
    cancelBackground?.();
  }

  function stop() {
    ++startRevision;
    if (frame) window.cancelAnimationFrame(frame);
    frame = 0;
    lastSampleAt = 0;
    captureClock.reset();
    resetLevels();
    if (!disabled) message.value = audio.paused ? "播放暂停，恢复后读取真实频谱" : "等待真实频谱信号";
  }

  function handleVisibility() {
    if (document.hidden) { cancelBackgroundAnalysis(); stop(); }
    else if (!audio.paused) start();
  }

  function unbindCaptureEvents() {
    for (const event of captureEvents) boundAudio?.removeEventListener(event, captureClock.reset);
    boundAudio = null;
  }

  onMounted(() => {
    document.addEventListener("visibilitychange", handleVisibility);
    boundAudio = audio;
    for (const event of captureEvents) boundAudio.addEventListener(event, captureClock.reset);
  });
  watch(visualMode, (mode) => {
    localStorage.setItem("personal-radio-visual-mode", mode);
    if (!audio.paused) start();
  });
  onBeforeUnmount(() => {
    disposed = true;
    ++generation;
    cancelBackgroundAnalysis();
    document.removeEventListener("visibilitychange", handleVisibility);
    unbindCaptureEvents();
    stop();
    closeGraph();
  });

  return {
    low, mid, high, energy, beat, spectrum, status, message, useOrdinaryAudio,
    prepare, start, stop, beginFeatureCapture, finishFeatureCapture, readWaveform,
    analyzeUrlInBackground,
    cancelBackgroundAnalysis,
  };
}
