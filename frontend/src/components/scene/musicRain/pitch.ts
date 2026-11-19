// 从短窗的 PCM 估计稳定音高。只听旋律常用的音域，降低鼓与低音的干扰。
export function createPitchDetector() {
  const tone = new Float32Array(1024);
  const correlations = new Float32Array(96);
  return function detectPitch(pcm: Float32Array, sampleRate: number): number | null {
    const stride = Math.max(1, Math.round(sampleRate / 12000));
    const rate = sampleRate / stride;
    const length = Math.min(Math.floor(pcm.length / stride), tone.length);
    const firstLag = Math.floor(rate / 1300);
    const lastLag = Math.ceil(rate / 240);
    if (firstLag < 2 || lastLag + 1 >= correlations.length || length <= lastLag * 3) return null;
    const upper = 1 - Math.exp(-2 * Math.PI * 1680 / rate);
    const lower = 1 - Math.exp(-2 * Math.PI * 240 / rate);
    let treble = 0, bass = 0, remainder = 0, energy = 0;
    for (let i = 0; i < length; i++) {
      const offset = i * stride;
      let sample = 0;
      for (let sampleIndex = offset; sampleIndex < offset + stride; sampleIndex++) sample += pcm[sampleIndex];
      sample /= stride;
      treble += (sample - treble) * upper;
      bass += (treble - bass) * lower;
      const high = treble - bass;
      remainder += (high - remainder) * lower;
      tone[i] = high - remainder;
      if (i >= lastLag) energy += tone[i] * tone[i];
    }
    if (energy / (length - lastLag) < .00003) return null;

    // 对比近一半窗口和它的延迟副本，优先寻找足够强的第一个周期。
    const start = Math.max(lastLag + 1, Math.floor(length * .40));
    let strongest = 0;
    for (let lag = firstLag - 1; lag <= lastLag + 1; lag++) {
      let dot = 0, recent = 0, delayed = 0;
      for (let i = start; i < length; i++) {
        const a = tone[i], b = tone[i - lag];
        dot += a * b; recent += a * a; delayed += b * b;
      }
      correlations[lag] = dot / (Math.sqrt(recent * delayed) || 1);
      if (lag >= firstLag && lag <= lastLag) strongest = Math.max(strongest, correlations[lag]);
    }
    const threshold = Math.max(.83, strongest * .94);
    for (let lag = firstLag; lag <= lastLag; lag++) {
      const left = correlations[lag - 1], center = correlations[lag], right = correlations[lag + 1];
      if (center < threshold || center <= left || center <= right) continue;
      const correction = .5 * (left - right) / (left - 2 * center + right);
      return rate / (lag + correction);
    }
    return null;
  };
}
