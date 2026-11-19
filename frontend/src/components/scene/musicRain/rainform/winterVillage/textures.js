import * as THREE from 'three';
import { mulberry32, fbm, clamp } from './util.js';

function canvas2d(w, h) {
  // 纹理只在 2D 离屏画布上生成，电台舞台仍只有一个可见 WebGL 画布。
  const c = typeof OffscreenCanvas === 'function' ? new OffscreenCanvas(w, h) : document.createElement('canvas');
  if (c.width !== w) c.width = w;
  if (c.height !== h) c.height = h;
  return { c, ctx: c.getContext('2d') };
}

function finish(canvas, { repeat = [1, 1], color = true } = {}) {
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat[0], repeat[1]);
  tex.anisotropy = 4;
  if (color) tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export function makeWoodTextures() {
  const rng = mulberry32(9201);
  const size = 512;
  const { c, ctx } = canvas2d(size, size);
  const { c: bc, ctx: bctx } = canvas2d(size, size);
  ctx.fillStyle = '#3d2e24';
  ctx.fillRect(0, 0, size, size);
  bctx.fillStyle = '#8f8f8f';
  bctx.fillRect(0, 0, size, size);

  const plankW = 64;
  for (let p = 0; p < 8; p++) {
    const shade = 38 + Math.floor(rng() * 24);
    ctx.fillStyle = `rgb(${shade + 22},${shade + 10},${shade - 3})`;
    ctx.fillRect(p * plankW, 0, plankW, size);
    bctx.fillStyle = `rgb(${120 + Math.floor(rng() * 25)},${120},${120})`;
    bctx.fillRect(p * plankW + 2, 0, plankW - 4, size);

    // Long, slightly wandering grain.
    for (let g = 0; g < 16; g++) {
      const x0 = p * plankW + 5 + rng() * (plankW - 10);
      ctx.beginPath();
      ctx.strokeStyle = `rgba(${12 + rng() * 24},${8 + rng() * 16},${5 + rng() * 12},${0.18 + rng() * 0.25})`;
      ctx.lineWidth = 0.5 + rng() * 1.4;
      ctx.moveTo(x0, 0);
      for (let y = 0; y <= size; y += 16) {
        const xx = x0 + Math.sin(y * 0.018 + g * 2.1 + p) * (1.2 + rng() * 2.5);
        ctx.lineTo(xx, y);
      }
      ctx.stroke();
      bctx.strokeStyle = `rgba(80,80,80,${0.18 + rng() * 0.18})`;
      bctx.lineWidth = 1;
      bctx.beginPath();
      bctx.moveTo(x0, 0);
      for (let y = 0; y <= size; y += 20) bctx.lineTo(x0 + Math.sin(y * 0.02 + p) * 2, y);
      bctx.stroke();
    }

    // Knots.
    if (rng() > 0.45) {
      const kx = p * plankW + 12 + rng() * (plankW - 24);
      const ky = 30 + rng() * (size - 60);
      for (let k = 0; k < 5; k++) {
        ctx.beginPath();
        ctx.ellipse(kx, ky, 7 - k * 1.1, 12 - k * 2, 0.08, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(24,15,10,${0.16 + k * 0.1})`;
        ctx.lineWidth = 1.1;
        ctx.stroke();
      }
    }

    // Deep seam between boards.
    ctx.fillStyle = 'rgba(8,7,6,0.72)';
    ctx.fillRect(p * plankW, 0, 2, size);
    ctx.fillStyle = 'rgba(150,118,88,0.16)';
    ctx.fillRect(p * plankW + 2, 0, 2, size);
    bctx.fillStyle = '#3f3f3f';
    bctx.fillRect(p * plankW, 0, 3, size);
  }
  // A little dust and frost.
  for (let i = 0; i < 900; i++) {
    ctx.fillStyle = `rgba(210,197,174,${rng() * 0.1})`;
    ctx.fillRect(rng() * size, rng() * size, 1 + rng() * 2, 1);
  }
  return {
    map: finish(c, { repeat: [2.2, 1.4] }),
    bump: finish(bc, { repeat: [2.2, 1.4], color: false }),
  };
}

export function makeBarkTexture() {
  const rng = mulberry32(7103);
  const { c, ctx } = canvas2d(256, 512);
  ctx.fillStyle = '#231b17';
  ctx.fillRect(0, 0, 256, 512);
  for (let i = 0; i < 90; i++) {
    const x = rng() * 256;
    const y = rng() * 512;
    const h = 20 + rng() * 120;
    ctx.strokeStyle = `rgba(${55 + rng() * 38},${39 + rng() * 24},${27 + rng() * 18},${0.2 + rng() * 0.5})`;
    ctx.lineWidth = 1 + rng() * 3;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.bezierCurveTo(x + rng() * 6 - 3, y + h * 0.35, x + rng() * 8 - 4, y + h * 0.75, x + rng() * 3 - 1.5, y + h);
    ctx.stroke();
  }
  for (let i = 0; i < 500; i++) {
    ctx.fillStyle = `rgba(190,176,155,${rng() * 0.09})`;
    ctx.fillRect(rng() * 256, rng() * 512, 1, 1 + rng() * 4);
  }
  return finish(c, { repeat: [1, 3] });
}

export function makeStoneTextures() {
  const rng = mulberry32(3390);
  const size = 512;
  const { c, ctx } = canvas2d(size, size);
  const { c: bc, ctx: bctx } = canvas2d(size, size);
  ctx.fillStyle = '#2d3233';
  ctx.fillRect(0, 0, size, size);
  bctx.fillStyle = '#505050';
  bctx.fillRect(0, 0, size, size);
  const rows = 5;
  for (let row = 0; row < rows; row++) {
    const y0 = row * (size / rows);
    const h = size / rows;
    let x = -rng() * 40;
    while (x < size) {
      const w = 65 + rng() * 85;
      const inset = 4 + rng() * 3;
      const pts = [
        [x + inset + rng() * 5, y0 + inset + rng() * 3],
        [x + w - inset - rng() * 5, y0 + inset + rng() * 5],
        [x + w - inset, y0 + h - inset - rng() * 4],
        [x + inset + rng() * 4, y0 + h - inset],
      ];
      const grey = 86 + Math.floor(rng() * 35);
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.closePath();
      const grd = ctx.createLinearGradient(x, y0, x + w, y0 + h);
      grd.addColorStop(0, `rgb(${grey + 22},${grey + 25},${grey + 22})`);
      grd.addColorStop(0.55, `rgb(${grey},${grey + 3},${grey})`);
      grd.addColorStop(1, `rgb(${grey - 22},${grey - 18},${grey - 18})`);
      ctx.fillStyle = grd;
      ctx.fill();
      ctx.strokeStyle = 'rgba(18,20,20,0.65)';
      ctx.lineWidth = 3;
      ctx.stroke();
      // Speckled stone surface.
      for (let i = 0; i < 55; i++) {
        ctx.fillStyle = `rgba(${rng() > 0.5 ? 220 : 20},${rng() > 0.5 ? 218 : 22},${rng() > 0.5 ? 205 : 20},${rng() * 0.13})`;
        ctx.beginPath();
        ctx.arc(x + inset + rng() * (w - inset * 2), y0 + inset + rng() * (h - inset * 2), rng() * 2.4, 0, Math.PI * 2);
        ctx.fill();
      }
      bctx.fillStyle = `rgb(${140 + rng() * 40},${140},${140})`;
      bctx.beginPath();
      bctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < 4; i++) bctx.lineTo(pts[i][0], pts[i][1]);
      bctx.closePath();
      bctx.fill();
      x += w;
    }
  }
  return { map: finish(c, { repeat: [2, 1.3] }), bump: finish(bc, { repeat: [2, 1.3], color: false }) };
}

export function makeSnowBumpTexture() {
  const size = 512;
  const { c, ctx } = canvas2d(size, size);
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const n = fbm(x * 0.021, y * 0.021, 5) * 0.75 + fbm(x * 0.11, y * 0.11, 3) * 0.25;
      const v = Math.floor(clamp(110 + n * 110, 0, 255));
      const i = (y * size + x) * 4;
      img.data[i] = v; img.data[i + 1] = v; img.data[i + 2] = v; img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return finish(c, { repeat: [5, 5], color: false });
}

export function makeSnowColorTexture() {
  const rng = mulberry32(2207);
  const size = 512;
  const { c, ctx } = canvas2d(size, size);
  const img = ctx.createImageData(size, size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const n = fbm(x * 0.017 + 8, y * 0.017 - 3, 5);
      const micro = fbm(x * 0.12, y * 0.12, 3);
      const v = 198 + n * 42 + micro * 12;
      const i = (y * size + x) * 4;
      img.data[i] = v * 0.92;
      img.data[i + 1] = v * 0.97;
      img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  for (let i = 0; i < 1200; i++) {
    ctx.fillStyle = `rgba(255,255,255,${0.12 + rng() * 0.28})`;
    const s = rng() > 0.94 ? 1.5 : 0.7;
    ctx.fillRect(rng() * size, rng() * size, s, s);
  }
  return finish(c, { repeat: [5, 5] });
}

export function makeGlowTexture() {
  const { c, ctx } = canvas2d(128, 128);
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.08, 'rgba(255,255,255,0.98)');
  g.addColorStop(0.24, 'rgba(255,255,255,0.42)');
  g.addColorStop(0.55, 'rgba(255,255,255,0.08)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return finish(c);
}

export function makeMistTexture() {
  const rng = mulberry32(5531);
  const { c, ctx } = canvas2d(512, 256);
  ctx.clearRect(0, 0, 512, 256);
  for (let i = 0; i < 56; i++) {
    const x = 50 + rng() * 412;
    const y = 80 + rng() * 120;
    const rx = 40 + rng() * 95;
    const ry = 15 + rng() * 38;
    const g = ctx.createRadialGradient(x, y, 0, x, y, rx);
    g.addColorStop(0, `rgba(255,255,255,${0.16 + rng() * 0.2})`);
    g.addColorStop(0.6, `rgba(255,255,255,${0.07 + rng() * 0.1})`);
    g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(1, ry / rx);
    ctx.translate(-x, -y);
    ctx.fillStyle = g;
    ctx.fillRect(x - rx, y - rx, rx * 2, rx * 2);
    ctx.restore();
  }
  return finish(c);
}

export function makeShadowTexture() {
  const { c, ctx } = canvas2d(256, 256);
  const g = ctx.createRadialGradient(128, 128, 10, 128, 128, 124);
  g.addColorStop(0, 'rgba(0,0,0,0.62)');
  g.addColorStop(0.45, 'rgba(0,0,0,0.34)');
  g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  return finish(c);
}

export function makeWindowTexture() {
  const rng = mulberry32(1002);
  const { c, ctx } = canvas2d(256, 256);
  const g = ctx.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, '#ffd49a');
  g.addColorStop(0.38, '#f6a85f');
  g.addColorStop(0.72, '#c9773f');
  g.addColorStop(1, '#7e4028');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 256, 256);
  // Curtain edges, a table lamp and a few household silhouettes.
  ctx.fillStyle = 'rgba(55,28,20,0.68)';
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(46, 0); ctx.bezierCurveTo(23, 70, 36, 160, 12, 256); ctx.lineTo(0, 256); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(256, 0); ctx.lineTo(212, 0); ctx.bezierCurveTo(234, 80, 219, 170, 248, 256); ctx.lineTo(256, 256); ctx.closePath(); ctx.fill();
  ctx.fillStyle = 'rgba(54,29,24,0.48)';
  ctx.fillRect(158, 168, 46, 9);
  ctx.fillRect(173, 150, 15, 22);
  ctx.beginPath(); ctx.arc(180, 147, 8, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,236,190,0.8)'; ctx.fill();
  ctx.fillStyle = 'rgba(77,43,28,0.55)';
  ctx.beginPath(); ctx.ellipse(71, 205, 24, 49, 0.05, 0, Math.PI * 2); ctx.fill();
  for (let i = 0; i < 120; i++) {
    ctx.fillStyle = `rgba(255,235,193,${rng() * 0.06})`;
    ctx.fillRect(rng() * 256, rng() * 256, 1, 1);
  }
  return finish(c);
}

export function makeRingTexture() {
  const { c, ctx } = canvas2d(128, 128);
  ctx.fillStyle = '#b88c5c';
  ctx.beginPath(); ctx.arc(64, 64, 62, 0, Math.PI * 2); ctx.fill();
  for (let i = 0; i < 12; i++) {
    ctx.beginPath();
    ctx.arc(64 + (i % 3) * 2, 64 + (i % 2) * 2, 8 + i * 4.4, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(72,43,23,${0.22 + (i % 4) * 0.08})`;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  ctx.strokeStyle = 'rgba(48,29,16,0.85)';
  ctx.lineWidth = 5;
  ctx.beginPath(); ctx.arc(64, 64, 59, 0, Math.PI * 2); ctx.stroke();
  return finish(c);
}

export function makeShingleTexture() {
  const rng = mulberry32(8442);
  const { c, ctx } = canvas2d(256, 256);
  ctx.fillStyle = '#373b3d';
  ctx.fillRect(0, 0, 256, 256);
  for (let y = 0; y < 256; y += 32) {
    const offset = (y / 32) % 2 ? 22 : 0;
    for (let x = -30; x < 280; x += 44) {
      const shade = 46 + Math.floor(rng() * 20);
      ctx.fillStyle = `rgb(${shade},${shade + 3},${shade + 4})`;
      ctx.fillRect(x + offset, y + 2, 42, 29);
      ctx.fillStyle = 'rgba(20,20,20,0.2)';
      ctx.fillRect(x + offset, y + 29, 42, 3);
    }
  }
  for (let i = 0; i < 600; i++) {
    ctx.fillStyle = `rgba(190,198,200,${rng() * 0.06})`;
    ctx.fillRect(rng() * 256, rng() * 256, 1, 1);
  }
  return finish(c, { repeat: [3, 2] });
}

// Four cut-out pine silhouettes. At forest distance these carry more believable
// branch rhythm than a cone ever could, while the near boughs remain full 3D.
export function makePineSilhouetteTextures() {
  const textures = [];
  for (let variant = 0; variant < 4; variant++) {
    const rng = mulberry32(6201 + variant * 733);
    const W = 256, H = 512;
    const { c, ctx } = canvas2d(W, H);
    ctx.clearRect(0, 0, W, H);
    const cx = W * 0.5 + (rng() - 0.5) * 18;
    const baseY = H - 10;
    const topY = 16 + rng() * 36;

    // Trunk.
    const trunkGrad = ctx.createLinearGradient(cx - 8, 0, cx + 8, 0);
    trunkGrad.addColorStop(0, 'rgba(13,24,27,0.6)');
    trunkGrad.addColorStop(0.5, 'rgba(33,42,41,0.95)');
    trunkGrad.addColorStop(1, 'rgba(8,16,19,0.72)');
    ctx.strokeStyle = trunkGrad;
    ctx.lineWidth = 12 - variant * 0.8;
    ctx.beginPath();
    ctx.moveTo(cx, baseY);
    ctx.quadraticCurveTo(cx + 2, H * 0.55, cx + 1, topY);
    ctx.stroke();

    const tiers = 19;
    for (let tier = 0; tier < tiers; tier++) {
      const f = tier / (tiers - 1);
      const y = H - 60 - f * (H - 120);
      const width = (W * 0.46) * Math.pow(1 - f, 0.78) + 9;
      const count = Math.max(2, Math.round(5 - f * 3));
      for (let side = -1; side <= 1; side += 2) {
        for (let k = 0; k < count; k++) {
          const spread = (0.25 + k * 0.2) * (0.55 + rng() * 0.65);
          const len = width * (0.45 + rng() * 0.64);
          const startY = y + (rng() - 0.5) * 12;
          const endX = cx + side * len;
          const endY = startY + len * (0.12 + rng() * 0.2) + 6;
          const midX = cx + side * len * 0.45;
          const midY = startY - len * 0.04 + spread * 14;
          // Shadow core of the branch.
          ctx.strokeStyle = `rgba(${9 + rng() * 9},${20 + rng() * 10},${22 + rng() * 9},0.9)`;
          ctx.lineWidth = 2.2 + (1 - f) * 2.8;
          ctx.beginPath();
          ctx.moveTo(cx, startY);
          ctx.quadraticCurveTo(midX, midY, endX, endY);
          ctx.stroke();
          // Ragged needle clusters.
          for (let t = 0.25; t < 1; t += 0.095) {
            const x = cx + (endX - cx) * t + (rng() - 0.5) * 4;
            const yy = startY + (endY - startY) * t + (rng() - 0.5) * 4;
            const nlen = 5 + rng() * 13;
            const ang = side * (0.5 + rng() * 0.8) + Math.PI * 0.5;
            ctx.strokeStyle = `rgba(12,26,27,${0.42 + rng() * 0.38})`;
            ctx.lineWidth = 1 + rng() * 1.2;
            ctx.beginPath();
            ctx.moveTo(x, yy);
            ctx.lineTo(x + Math.cos(ang) * nlen * side, yy + Math.sin(ang) * nlen * 0.45 + 2);
            ctx.stroke();
          }
          // Snow settled along the upper edge of the branch.
          ctx.strokeStyle = `rgba(${112 + rng() * 28},${134 + rng() * 24},${154 + rng() * 21},${0.18 + rng() * 0.22})`;
          ctx.lineWidth = 1.1 + (1 - f) * 1.1;
          ctx.beginPath();
          ctx.moveTo(cx + side * 3, startY - 2.5);
          ctx.quadraticCurveTo(midX, midY - 4, endX - side * 2, endY - 1.8);
          ctx.stroke();
        }
      }
    }
    // Snow on the crown.
    ctx.strokeStyle = 'rgba(170,188,204,0.5)';
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(cx - 3, H * 0.4);
    ctx.quadraticCurveTo(cx + 2, topY + 14, cx, topY);
    ctx.stroke();
    // Soft blue rim at the lower silhouette, as if rim-lit by the moon.
    ctx.strokeStyle = 'rgba(89,114,139,0.12)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx + 5, baseY);
    ctx.lineTo(cx + 4, topY + 30);
    ctx.stroke();
    textures.push(finish(c));
  }
  return textures;
}

