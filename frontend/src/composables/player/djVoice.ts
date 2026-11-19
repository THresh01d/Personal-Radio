export const DJ_VOICES = [
  { id: "zh-CN-XiaoxiaoNeural", label: "晓晓" },
  { id: "zh-CN-XiaoyiNeural", label: "晓伊" },
  { id: "zh-CN-YunxiNeural", label: "云希" },
  { id: "zh-CN-YunyangNeural", label: "云扬" },
  { id: "zh-CN-YunjianNeural", label: "云健" },
  { id: "zh-CN-YunxiaNeural", label: "云夏" },
] as const;

export type DjVoice = (typeof DJ_VOICES)[number]["id"];
export const DJ_VOICE_KEY = "personal-radio-dj-voice";

export function restoreDjVoice(saved: string | null): DjVoice {
  return DJ_VOICES.find(({ id }) => id === saved)?.id ?? DJ_VOICES[0].id;
}
