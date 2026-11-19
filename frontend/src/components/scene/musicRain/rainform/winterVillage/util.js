import * as THREE from 'three';

export const TAU = Math.PI * 2;

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const smoothstep = (a, b, x) => {
  const t = clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};

export function hash2(x, y) {
  let n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453123;
  return n - Math.floor(n);
}

export function valueNoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  const a = hash2(xi, yi), b = hash2(xi + 1, yi);
  const c = hash2(xi, yi + 1), d = hash2(xi + 1, yi + 1);
  return lerp(lerp(a, b, u), lerp(c, d, u), v);
}

export function fbm(x, y, octaves = 4) {
  let value = 0, amp = 0.5, freq = 1, total = 0;
  for (let i = 0; i < octaves; i++) {
    value += valueNoise(x * freq, y * freq) * amp;
    total += amp;
    amp *= 0.5;
    freq *= 2.03;
  }
  return value / total;
}

// A quiet, deterministic terrain. The creek carves a soft diagonal channel in
// the foreground, while the cabin and tree pads are gently levelled.
export function creekPoint(t) {
  return {
    x: -7.8 + t * 4.6,
    z: 8.8 - t * 8.2 + Math.sin(t * 3.1 + 0.3) * 0.55,
  };
}

const creekSamples = Array.from({ length: 64 }, (_, i) => creekPoint(i / 63));

export function creekDistance(x, z) {
  let best = 99;
  for (let i = 0; i < creekSamples.length - 1; i++) {
    const a = creekSamples[i], b = creekSamples[i + 1];
    const vx = b.x - a.x, vz = b.z - a.z;
    const wx = x - a.x, wz = z - a.z;
    const len = vx * vx + vz * vz;
    const t = clamp((wx * vx + wz * vz) / len, 0, 1);
    const dx = x - (a.x + vx * t);
    const dz = z - (a.z + vz * t);
    const d = Math.hypot(dx, dz);
    if (d < best) best = d;
  }
  return best;
}

export function terrainHeight(x, z) {
  let h = 0.05;
  h += (fbm(x * 0.13 + 3.4, z * 0.13 - 1.7, 3) - 0.5) * 0.52;
  h += (fbm(x * 0.055 - 4.1, z * 0.055 + 2.4, 3) - 0.5) * 0.72;
  h += Math.sin(x * 0.19 + z * 0.1) * 0.055;
  h += smoothstep(8, 20, Math.abs(x)) * 0.42;
  h += smoothstep(-3, -15, z) * 0.38;

  const cd = creekDistance(x, z);
  const channel = 1 - smoothstep(0.9, 2.9, cd);
  h -= channel * 1.05;
  // Raised, rounded banks on both sides of the ice.
  h += Math.exp(-Math.pow((cd - 3.5) / 1.05, 2)) * 0.22;

  // Level pads: the tree sits in a small snowy clearing, the cabin on a low shelf.
  const treeD = Math.hypot(x + 2.55, z + 2.45);
  h = lerp(h, 0.24, (1 - smoothstep(2.0, 4.1, treeD)) * 0.78);

  const cabinD = Math.max(Math.abs(x - 4.95) / 5.0, Math.abs(z + 3.6) / 4.5);
  h = lerp(h, 0.17, (1 - smoothstep(0.72, 1.18, cabinD)) * 0.86);

  return h;
}

export function terrainNormal(x, z, eps = 0.08) {
  const hL = terrainHeight(x - eps, z), hR = terrainHeight(x + eps, z);
  const hD = terrainHeight(x, z - eps), hU = terrainHeight(x, z + eps);
  return new THREE.Vector3(hL - hR, 2 * eps, hD - hU).normalize();
}

export function makeCylinderBetween(a, b, radius, material, segments = 6, radiusTop = null) {
  const dir = new THREE.Vector3().subVectors(b, a);
  const len = dir.length();
  const geo = new THREE.CylinderGeometry(radiusTop ?? radius, radius, len, segments, 1);
  const mesh = new THREE.Mesh(geo, material);
  mesh.position.copy(a).addScaledVector(dir, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
  return mesh;
}

export function addBox(parent, w, h, d, x, y, z, material, opts = {}) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.position.set(x, y, z);
  if (opts.rotation) mesh.rotation.set(...opts.rotation);
  mesh.castShadow = opts.castShadow ?? true;
  mesh.receiveShadow = opts.receiveShadow ?? true;
  parent.add(mesh);
  return mesh;
}

export function setShadow(mesh, cast = true, receive = true) {
  mesh.castShadow = cast;
  mesh.receiveShadow = receive;
  return mesh;
}

export function disposeObject(object) {
  object.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const mats = Array.isArray(child.material) ? child.material : [child.material];
      mats.forEach((m) => m.dispose?.());
    }
  });
}
