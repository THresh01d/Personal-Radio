// 仅服务生产前端与合成数据；不导入应用后端、不打开数据库、不访问音乐平台。
import { createServer } from "node:http";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve, sep, extname } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = resolve(root, process.argv[2] || ".cache/depth-carousel-preview");
if (!output.startsWith(resolve(root, ".cache") + sep)) throw new Error("预览记录只允许放入新的 .cache 子目录");
const distArg = process.argv.find(arg => arg.startsWith("--dist="))?.slice("--dist=".length);
const dist = resolve(root, distArg || "static/dist");
if (distArg && !dist.startsWith(resolve(root, ".cache") + sep)) throw new Error("隔离构建只允许从 .cache 读取");
mkdirSync(output, { recursive: false });
const musicFixture = process.argv.includes("--music");
const desktopFixture = process.argv.includes("--christmas");
const snowFixture = process.argv.includes("--snow");
const observationFixture = process.argv.includes("--observations");
const observationEvents = [], observationLeases = new Map();
let observationSequence = 0;
const fixtureSeconds = musicFixture ? 48 : 20;
const sampleRate = musicFixture ? 24000 : 16000;
const names = ["雨落之后", "晚风留声", "月面来信", "凌晨慢行", "微光航线", "街角回声", "夜色未眠"];
const songs = names.map((title, i) => ({ id: `preview-${i + 1}`, title, artist: "合成预览 · AFTER HOURS", album: "封面交互示例",
  duration_seconds: fixtureSeconds, cover_url: `/preview/cover/${i + 1}.svg`, genre: ["纯音乐"], mood: ["平静"], reason: "仅演示切歌界面" }));
const seed = JSON.stringify({ sessionId: "depth-carousel-preview", rounds: [{ answer: "合成轮播预览", songs }] });
const bootstrap = `<script>if(!sessionStorage.getItem('depth-preview-seeded')){
localStorage.setItem('personal-radio-session','depth-carousel-preview');localStorage.setItem('personal-radio-rounds',${JSON.stringify(seed)});
${snowFixture ? "if(!localStorage.getItem('personal-radio-stage-weather'))localStorage.setItem('personal-radio-stage-weather','snow');" : ""}
for(const key of ['announce-on','acoustic-backfill','smart-continue'])localStorage.setItem('personal-radio-'+key,'off');
localStorage.setItem('personal-radio-playback-mode','sequence');sessionStorage.setItem('depth-preview-seeded','yes');}</script>`;
const desktopBridge = desktopFixture ? `<script>
window.personalRadioDesktop={checkForUpdates:async()=>{},getDesktopVersion:async()=>'合成预览'};
</script>` : '';
const banner = `<aside style="position:fixed;z-index:99;bottom:104px;left:50%;transform:translateX(-50%);max-width:90%;white-space:nowrap;padding:6px 12px;border:1px solid #9bcebf44;border-radius:20px;background:#04151be6;color:#a8c5bd;font:10px sans-serif">${musicFixture ? "合成测试音 · 每8秒低频／中频／高频／混合切换" : "交互预览 · 合成封面与静音测试音源"}</aside>`;
const graphicsProbe = musicFixture && process.argv.includes("--graphics-probe") ? `<script>document.addEventListener('DOMContentLoaded',()=>{
let extension; const controls=document.createElement('div');
controls.style.cssText='position:fixed;z-index:99;right:12px;top:100px;display:flex;gap:6px';
for(const [label,run] of [['模拟图形中断',()=>{const canvas=document.querySelector('.music-rain-canvas');extension=canvas?.getContext('webgl2')?.getExtension('WEBGL_lose_context');extension?.loseContext();}],['恢复图形',()=>extension?.restoreContext()]]){
const button=document.createElement('button');button.textContent=label;button.style.cssText='padding:6px;border:1px solid #668a8366;background:#07171d;color:#a8c5bd;border-radius:8px;font-size:10px';button.addEventListener('click',run);controls.append(button);}
document.body.append(controls);});</script>` : '';
// 仅合成媒体验证使用可切换调度；受控结果必须与原生RAF分开记录。
const observationProbe = observationFixture ? `<script>
localStorage.setItem('personal-radio-visual-mode','quiet');
const originalSource=AudioContext.prototype.createMediaElementSource;
AudioContext.prototype.createMediaElementSource=function(media){
const node=originalSource.call(this,media),connect=node.connect.bind(node),destination=this.destination,mute=this.createGain();
mute.gain.value=0;mute.connect(destination);
node.connect=function(target,...rest){return connect(target===destination?mute:target,...rest)};return node};
const nativeRAF=window.requestAnimationFrame.bind(window),nativeCancel=window.cancelAnimationFrame.bind(window);
let controlled=false,sequence=1000000;const pendingFrames=new Map();
function schedule(ticket){const run=()=>{pendingFrames.delete(ticket.key);ticket.callback(performance.now())};
ticket.mode=controlled;ticket.handle=controlled?setTimeout(run,34):nativeRAF(run)}
function stop(ticket){if(ticket.mode)clearTimeout(ticket.handle);else nativeCancel(ticket.handle)}
window.requestAnimationFrame=callback=>{const ticket={key:++sequence,callback};pendingFrames.set(ticket.key,ticket);schedule(ticket);return ticket.key};
window.cancelAnimationFrame=key=>{const ticket=pendingFrames.get(key);if(ticket){stop(ticket);pendingFrames.delete(key)}};
document.addEventListener('DOMContentLoaded',()=>{
const controls=document.createElement('aside');controls.style.cssText='position:fixed;z-index:100;top:90px;left:20px;max-width:460px;background:#111e;color:#ddd;padding:12px;border-radius:12px;font:11px monospace';
const button=document.createElement('button');button.textContent='采样调度：原生';
button.onclick=()=>{controlled=!controlled;for(const ticket of pendingFrames.values()){stop(ticket);schedule(ticket)}button.textContent=controlled?'采样调度：受控':'采样调度：原生'};
const state=document.createElement('pre');state.setAttribute('aria-label','观察探针');controls.append(button,state);document.body.append(controls);
const refresh=()=>fetch('/preview/observation-probe').then(r=>r.json()).then(data=>{state.textContent=JSON.stringify(data,null,2)});refresh();setInterval(refresh,1000);
});</script>` : '';
// 读取当前构建入口，后续重建后刷新预览也不会指向已被Vite清理的旧资源。
const html = () => readFileSync(resolve(dist, "index.html"), "utf8").replace("<head>", `<head>${bootstrap}${desktopBridge}${graphicsProbe}${observationProbe}`).replace("</body>", `${banner}</body>`);
const audio = Buffer.alloc(44 + sampleRate * fixtureSeconds * 2);
audio.write("RIFF"); audio.writeUInt32LE(audio.length - 8, 4); audio.write("WAVEfmt ", 8); audio.writeUInt32LE(16, 16);
audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22); audio.writeUInt32LE(sampleRate, 24); audio.writeUInt32LE(sampleRate * 2, 28);
audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34); audio.write("data", 36); audio.writeUInt32LE(audio.length - 44, 40);
if (musicFixture) {
  // 仅在内存合成有限测试音，走原生媒体与既有分析器；不读取/保存用户歌曲。
  for (let index = 0; index < sampleRate * fixtureSeconds; index++) {
    const t = index / sampleRate;
    const section = Math.floor(t / 8) % 4;
    const frequencies = section === 0 ? [90, 140, 190] : section === 1 ? [600, 850, 1200]
      : section === 2 ? [3600, 4500, 5700] : [140, 750, 4200];
    const phase = t % 0.6;
    const beat = Math.sin(2 * Math.PI * 62 * t) * Math.exp(-phase * 22) * 0.16;
    const edge = Math.min(1, (t % 8) * 12, (8 - t % 8) * 12);
    const tone = frequencies.reduce((sum, hz) => sum + Math.sin(2 * Math.PI * hz * t), 0) * 0.055;
    audio.writeInt16LE(Math.round(Math.max(-1, Math.min(1, (tone + beat) * edge)) * 32767), 44 + index * 2);
  }
}

function art(number) {
  const palettes = [["#0b2730", "#6b9997", "#d4aa77"], ["#3b1d31", "#ce8d64", "#f2c987"], ["#16263e", "#7795ac", "#c8d4ca"], ["#112a35", "#64957c", "#b0ce9c"]];
  const [dark, light, accent] = palettes[(number - 1) % palettes.length];
  const scene = number === 1 ? `<circle cx="290" cy="152" r="60" fill="${accent}" opacity=".8"/><path d="M0 310 115 202 200 280 310 210 420 300V420H0Z" fill="${dark}"/><path d="M0 346Q180 290 420 358" fill="none" stroke="${accent}" opacity=".4"/>`
    : number === 2 ? `<circle cx="210" cy="216" r="112" fill="${accent}"/><path d="M0 294 102 210 217 330 313 252 420 312V420H0Z" fill="${dark}" opacity=".8"/><path d="M0 342 161 287 299 373 420 330V420H0Z" fill="${dark}"/>`
    : number === 3 ? `<circle cx="210" cy="213" r="95" fill="${accent}"/><circle cx="238" cy="188" r="88" fill="${dark}"/><ellipse cx="210" cy="213" rx="170" ry="44" fill="none" stroke="${light}" transform="rotate(-32 210 213)"/><circle cx="334" cy="100" r="4" fill="${accent}"/>`
    : `<path d="M0 300V200H44V274H70V160H118V294H152V224H200V130H250V254H280V187H324V286H355V216H400V304H420V420H0Z" fill="${dark}"/><path d="M220 158V235M88 185V258M307 214V260M372 247V282" stroke="${accent}" stroke-width="5"/><path d="M0 360Q200 320 420 350" fill="none" stroke="${accent}" opacity=".4"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 420"><defs><linearGradient id="bg" x2=".7" y2="1"><stop stop-color="${dark}"/><stop offset="1" stop-color="${light}"/></linearGradient><filter id="noise"><feTurbulence baseFrequency=".7" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncA type="linear" slope=".09"/></feComponentTransfer><feBlend in="SourceGraphic" mode="soft-light"/></filter></defs><g filter="url(#noise)"><path fill="url(#bg)" d="M0 0h420v420H0z"/>${scene}<text x="29" y="44" font-family="monospace" font-size="11" letter-spacing="4" fill="#eee9d7">AFTER HOURS / 0${number}</text><text x="29" y="381" font-family="sans-serif" font-size="25" letter-spacing="5" fill="#eee9d7">${names[number - 1]}</text></g></svg>`;
}

const server = createServer(async (req, res) => {
  const address = server.address();
  const origin = `http://127.0.0.1:${address.port}`;
  // 合成预览也限制本机 Host/Origin，不借本轮视觉工作开放日常服务。
  if (req.headers.host !== `127.0.0.1:${address.port}` || (req.headers.origin && req.headers.origin !== origin)) {
    res.writeHead(403); res.end(); return;
  }
  const path = new URL(req.url, origin).pathname;
  const send = (value, type = "application/json") => { res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" }); res.end(type === "application/json" ? JSON.stringify(value) : value); };
  if (path === "/api/local-access") { res.setHeader("Set-Cookie", "preview-access=yes; HttpOnly; SameSite=Strict; Path=/"); send({ ok: true }); return; }
  if (path.startsWith("/api/") && !req.headers.cookie?.includes("preview-access=yes")) { res.writeHead(403); res.end(); return; }
  if (observationFixture && path === "/preview/observation-probe") return send({ events: observationEvents });
  if (observationFixture && req.method === "POST" && path.startsWith("/api/audio-observations")) {
    let text = "";
    for await (const chunk of req) { text += chunk; if (text.length > 65536) { res.writeHead(413); res.end(); return; } }
    let body;
    try { body = JSON.parse(text); } catch { res.writeHead(422); res.end(); return; }
    const record = event => { observationEvents.push({ ...event, at: Date.now() }); writeFileSync(resolve(output, "observation-events.json"), JSON.stringify(observationEvents, null, 2)); };
    if (path === "/api/audio-observations/prepare") {
      if (!songs.some(song => song.id === body.song_id)) { res.writeHead(404); res.end(); return; }
      const lease = { observation_id: (++observationSequence).toString(16).padStart(32, "0"),
        binding: { song_id: body.song_id, library_generation: "b".repeat(32), song_revision: "c".repeat(32),
          metadata_sha256: "d".repeat(64), feature_algorithm: "web-audio-v1", capture_contract: "media-clock-v1",
          measurement_mode: "foreground", capture_configuration: "fft2048-s078-bands-v1" }, expires_at: Date.now() / 1000 + 1200 };
      observationLeases.set(lease.observation_id, lease); record({ type: "prepare", song_id: body.song_id });
      await new Promise(resolve => setTimeout(resolve, 1500));
      record({ type: "binding", song_id: body.song_id }); return send(lease);
    }
    if (!observationLeases.has(body.observation_id)) { res.writeHead(409); res.end(); return; }
    const lease = observationLeases.get(body.observation_id);
    if (path.endsWith("/cancel")) {
      observationLeases.delete(body.observation_id); record({ type: "cancel", song_id: lease.binding.song_id }); return send({ cancelled: true });
    }
    record({ type: "submit", song_id: body.song_id, packet: body }); return send({ status: "accepted", observation_id: body.observation_id });
  }
  if (path === "/") return send(html(), "text/html; charset=utf-8");
  if (/^\/preview\/cover\/[1-7]\.svg$/.test(path)) return send(art(Number(path.at(-5))), "image/svg+xml; charset=utf-8");
  if (path === "/preview/silence.wav") {
    const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || "");
    if (range) {
      const start = Number(range[1]), end = Math.min(Number(range[2] || audio.length - 1), audio.length - 1);
      if (start > end) { res.writeHead(416); res.end(); return; }
      res.writeHead(206, { "Content-Type": "audio/wav", "Content-Range": `bytes ${start}-${end}/${audio.length}`, "Content-Length": end - start + 1, "Accept-Ranges": "bytes" });
      res.end(audio.subarray(start, end + 1)); return;
    }
    res.writeHead(200, { "Content-Type": "audio/wav", "Content-Length": audio.length, "Accept-Ranges": "bytes" }); res.end(audio); return;
  }
  if (path === "/api/setup/status") return send({ library: { song_count: songs.length, ready: true }, netease: { credential_present: false }, llm: { provider: "offline" } });
  if (path === "/api/ncm/status") return send({ logged_in: false });
  if (desktopFixture && path === "/api/qq/status") return send({ logged_in: false, available: false });
  if (desktopFixture && path === "/api/hotkeys") return send({ supported: false, active: [] });
  if (desktopFixture && path === "/favicon.ico") { res.writeHead(204); res.end(); return; }
  if (path === "/api/ncm/sync/latest") return send(null);
  if (path === "/api/library/playlists" || path === "/api/ncm/playlists") return send({ playlists: [] });
  if (path === "/api/library/songs") return send({ songs, total: songs.length, offset: 0, limit: 50 });
  if (/^\/api\/sessions\/[^/]+\/messages$/.test(path)) return send({ messages: [] });
  if (path.startsWith("/api/ncm/lyrics/")) return send(path.endsWith("preview-3")
    ? { success: true, instrumental: false, lines: [{ time: 0, text: "合成歌词 · 只验证布局" }, { time: 8, text: "合成下一行 · 点击展开" }, { time: 15, text: "合成结束行 · 非真实歌词" }] }
    : { success: true, instrumental: true, lines: [] });
  if (path.startsWith("/api/ncm/playurl/")) return send({ success: true, url: "/preview/silence.wav" });
  if (path.startsWith("/api/ncm/song-cover/")) return send({ cover_url: songs.find(s => path.endsWith(s.id))?.cover_url || "" });
  if (desktopFixture && path === "/api/audio-observations/prepare" && req.method === "POST") { req.resume(); return send(null); }
  if (path === "/api/audio-features/pending") return send({ song_ids: [] });
  if (path === "/api/listening-events" || path === "/api/audio-features") { req.resume(); return send({ ok: true }); }
  if (path.startsWith("/dist/assets/")) {
    const file = resolve(dist, path.slice("/dist/".length));
    if (file.startsWith(dist + sep)) {
      try { return send(readFileSync(file), ({ ".js": "text/javascript", ".css": "text/css", ".png": "image/png" })[extname(file)] || "application/octet-stream"); } catch { /* 缺失资源走明确404。 */ }
    }
  }
  res.writeHead(404); res.end();
});
server.listen(0, "127.0.0.1", () => { console.log(`Depth carousel synthetic preview: http://127.0.0.1:${server.address().port}`); });
