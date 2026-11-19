import * as THREE from 'three';
import {
  TAU, mulberry32, fbm, clamp, lerp, smoothstep,
  terrainHeight, terrainNormal, creekPoint, makeCylinderBetween, addBox,
} from './util.js';
import { createForegroundBough } from './tree.js';

function makeSky(materials) {
  const group = new THREE.Group();
  const geo = new THREE.SphereGeometry(58, 32, 20);
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      uHorizon: { value: new THREE.Color(0x101f30) },
      uZenith: { value: new THREE.Color(0x02060c) },
      uGlow: { value: new THREE.Color(0x243a52) },
    },
    vertexShader: /* glsl */ `
      varying vec3 vWorld;
      void main() {
        vec4 w = modelMatrix * vec4(position, 1.0);
        vWorld = w.xyz;
        gl_Position = projectionMatrix * viewMatrix * w;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 uHorizon;
      uniform vec3 uZenith;
      uniform vec3 uGlow;
      varying vec3 vWorld;
      void main() {
        vec3 dir = normalize(vWorld);
        float h = smoothstep(-0.15, 0.62, dir.y);
        vec3 col = mix(uHorizon, uZenith, h);
        col += uGlow * pow(1.0 - abs(dir.y), 8.0) * 0.32;
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  group.add(new THREE.Mesh(geo, mat));

  const rng = mulberry32(991);
  const count = 620;
  const positions = [];
  for (let i = 0; i < count; i++) {
    const a = rng() * TAU;
    const y = 0.08 + Math.pow(rng(), 0.65) * 0.9;
    const r = Math.sqrt(1 - y * y);
    const radius = 48 + rng() * 4;
    positions.push(Math.cos(a) * r * radius, y * radius, Math.sin(a) * r * radius);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  const starMat = new THREE.PointsMaterial({
    color: 0xb9cee7,
    size: 0.18,
    map: materials.textures.glow,
    transparent: true,
    opacity: 0.72,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    fog: false,
    sizeAttenuation: true,
  });
  const stars = new THREE.Points(starGeo, starMat);
  stars.name = 'winter-sky-stars';
  group.add(stars);

  // A very faint moon haze behind the trees, never a hard disk.
  const moon = new THREE.Sprite(new THREE.SpriteMaterial({
    map: materials.textures.glow,
    color: 0x9ab8dc,
    transparent: true,
    opacity: 0.14,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    fog: false,
  }));
  moon.position.set(-19, 21, -30);
  moon.scale.set(18, 18, 1);
  group.add(moon);
  return { group, stars };
}

function createTerrain(materials) {
  const size = 44;
  const seg = 180;
  const geo = new THREE.PlaneGeometry(size, size, seg, seg);
  geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  const colors = [];
  const base = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    const h = terrainHeight(x, z);
    pos.setY(i, h);
    const n = fbm(x * 0.55 + 4, z * 0.55 - 2, 4);
    const creek = smoothstep(4.2, 1.1, Math.hypot(x - creekPoint(0.35).x, z - creekPoint(0.35).z));
    const shade = 0.73 + n * 0.24 - creek * 0.06;
    base.setRGB(shade * 0.82, shade * 0.9, shade * 1.0);
    colors.push(base.r, base.g, base.b);
  }
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const mat = materials.snow.clone();
  mat.vertexColors = true;
  mat.color.set(0xffffff);
  mat.roughness = 0.95;
  mat.bumpScale = 0.055;
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = true;
  mesh.castShadow = false;
  return mesh;
}

function createCreek(materials) {
  const group = new THREE.Group();
  const segments = 92;
  const across = 6;
  const positions = [];
  const uvs = [];
  const indices = [];
  const centers = [];
  const tangents = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const c = creekPoint(t);
    const c2 = creekPoint(Math.min(1, t + 0.01));
    const c1 = creekPoint(Math.max(0, t - 0.01));
    const tan = new THREE.Vector2(c2.x - c1.x, c2.z - c1.z).normalize();
    const normal = new THREE.Vector2(-tan.y, tan.x);
    const width = 3.65 + Math.sin(t * 10.2) * 0.28 + Math.sin(t * 3.7) * 0.22;
    centers.push(c); tangents.push(tan);
    for (let j = 0; j <= across; j++) {
      const u = j / across;
      const off = (u - 0.5) * width;
      const x = c.x + normal.x * off;
      const z = c.z + normal.y * off;
      const y = -0.39 + Math.sin(u * Math.PI) * -0.012 + Math.sin(t * 16 + u * 5) * 0.006;      positions.push(x, y, z);
      uvs.push(u, t);
    }
  }
  for (let i = 0; i < segments; i++) {
    for (let j = 0; j < across; j++) {
      const a = i * (across + 1) + j;
      const b = a + 1;
      const c = a + across + 1;
      const d = c + 1;
      indices.push(a, c, b, b, c, d);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();

  const uniforms = {
    uTime: { value: 0 },
    uBulbResponse: { value: 0 },
    uReflectionTree: { value: new THREE.Vector3() },
    uMoonDir: { value: new THREE.Vector3(-0.45, 0.7, 0.4).normalize() },
    uFogColor: { value: new THREE.Color(0x0c1a28) },
    uFogDensity: { value: 0.036 },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    transparent: false,
    fog: false,
    vertexShader: /* glsl */ `
      varying vec3 vWorld;
      varying vec2 vUv;
      varying vec3 vNormal;
      void main() {
        vUv = uv;
        vec4 w = modelMatrix * vec4(position, 1.0);
        vWorld = w.xyz;
        vNormal = normalize(mat3(modelMatrix) * normal);
        gl_Position = projectionMatrix * viewMatrix * w;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform float uTime;
      uniform float uBulbResponse;
      uniform vec3 uReflectionTree;
      uniform vec3 uMoonDir;
      uniform vec3 uFogColor;
      uniform float uFogDensity;
      varying vec3 vWorld;
      varying vec2 vUv;
      varying vec3 vNormal;

      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float noise(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        f = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1,0)), f.x), mix(hash(i + vec2(0,1)), hash(i + vec2(1,1)), f.x), f.y);
      }
      float fbm(vec2 p) {
        float v = 0.0, a = 0.5;
        for (int i = 0; i < 5; i++) { v += noise(p) * a; p = p * 2.02 + 4.7; a *= 0.5; }
        return v;
      }
      void main() {
        vec3 viewDir = normalize(cameraPosition - vWorld);
        vec3 n = normalize(vNormal);
        vec2 p = vWorld.xz;
        float grain = fbm(p * 2.4 + vec2(uTime * 0.006, 0.0));
        float broad = fbm(p * 0.44 + 2.3);
        vec3 deep = vec3(0.032, 0.09, 0.14);
        vec3 shallow = vec3(0.085, 0.2, 0.28);
        vec3 col = mix(deep, shallow, smoothstep(0.18, 0.82, broad) * 0.8 + grain * 0.18);

        // Slow local ripple normal, enough to break up the reflections.
        vec2 rip = vec2(
          sin(p.x * 2.7 + p.y * 0.8 + uTime * 0.12),
          cos(p.y * 2.3 - p.x * 0.6 - uTime * 0.09)
        ) * 0.012;
        vec3 rn = normalize(n + vec3(rip.x, 0.0, rip.y));

        float fres = pow(1.0 - max(dot(rn, viewDir), 0.0), 1.35);
        vec3 skyRef = mix(vec3(0.06, 0.12, 0.17), vec3(0.34, 0.47, 0.6), fres);
        col = mix(col, skyRef, 0.4 + fres * 0.4);
        col += vec3(0.018, 0.032, 0.048);

        float film = smoothstep(0.53, 0.78, fbm(p * 1.15 + vec2(5.0, -2.0)));
        col = mix(col, vec3(0.27, 0.36, 0.42), film * 0.16);

        vec3 r = reflect(-uMoonDir, normalize(vec3(rip.x, 1.0, rip.y)));
        float spec = pow(max(dot(r, viewDir), 0.0), 72.0);
        col += vec3(0.72, 0.86, 1.05) * spec * 0.72;

        // Hairline cracks with a few branching strokes.
        float crackBase = fbm(p * 5.8 + vec2(2.1, -1.4));
        float branch = fbm(p * 12.0 + vec2(-2.0, 4.0));
        float crack = smoothstep(0.021, 0.0, abs(crackBase - 0.48));
        crack += smoothstep(0.008, 0.0, abs(branch - 0.54)) * 0.6;
        col += vec3(0.55, 0.69, 0.79) * crack * 0.16;

        // Frost creeps in from both banks.
        float edge = min(vUv.x, 1.0 - vUv.x);
        float frost = smoothstep(0.18, 0.0, edge);
        frost *= 0.65 + fbm(p * 5.0) * 0.35;
        col += vec3(0.022, 0.042, 0.062);
        col = mix(col, vec3(0.55, 0.65, 0.72), frost * 0.7);

        // Tiny glints on the ice.
        vec2 cell = floor(p * 26.0);
        float glint = step(0.993, hash(cell + floor(uTime * 0.33)));
        col += vec3(0.65, 0.78, 0.9) * glint * 0.5;
        // 沿反射方向找树冠，霜纹把银光切成短碎的亮斑。
        vec2 toTree = uReflectionTree.xz - p;
        vec2 ray = -viewDir.xz;
        float along = dot(toTree, ray) / max(dot(ray, ray), .001);
        vec2 miss = toTree - ray * along;
        float crownY = vWorld.y + along * viewDir.y - uReflectionTree.y;
        float crownWidth = mix(1.95, .45, smoothstep(0., 7., crownY));
        float crown = exp(-dot(miss, miss) / (crownWidth * crownWidth));
        crown *= smoothstep(.05, .65, along) * smoothstep(.15, .85, crownY)
          * (1. - smoothstep(5., 8., crownY));
        vec2 frostCell = p * vec2(4.5, 13.) + grain * .65;
        float pools = smoothstep(.55, .81, noise(p * 2.1 + 7.));
        float flecks = smoothstep(.45, .96, noise(frostCell));
        col += vec3(.46, .64, .85) * crown * pools * flecks * (.83 + uBulbResponse) * (.62 + fres * .38);

        float dist = length(cameraPosition - vWorld);
        float fog = 1.0 - exp(-uFogDensity * uFogDensity * dist * dist);
        col = mix(col, uFogColor, fog);
        gl_FragColor = vec4(col, 1.0);
      }
    `,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 1;
  group.add(mesh);

  // A continuous snow shelf softens both banks and keeps the ice from looking
  // like a painted strip laid on top of the ground.
  const bankPositions = [];
  const bankUVs = [];
  const bankIndices = [];
  const bankRows = 80;
  const bankAcross = 3;
  for (let side = -1; side <= 1; side += 2) {
    const start = bankPositions.length / 3;
    for (let i = 0; i <= bankRows; i++) {
      const t = i / bankRows;
      const c = creekPoint(t);
      const c2 = creekPoint(Math.min(1, t + 0.01));
      const c1 = creekPoint(Math.max(0, t - 0.01));
      const tan = new THREE.Vector2(c2.x - c1.x, c2.z - c1.z).normalize();
      const normal = new THREE.Vector2(-tan.y, tan.x);
      const width = 3.65 + Math.sin(t * 10.2) * 0.28 + Math.sin(t * 3.7) * 0.22;
      for (let j = 0; j < bankAcross; j++) {
        const inset = [0.47, 0.52, 0.63][j] * width;
        const x = c.x + normal.x * side * inset;
        const z = c.z + normal.y * side * inset;
        let y;
        if (j === 0) y = -0.375;
        else if (j === 1) y = Math.max(-0.27, terrainHeight(x, z) - 0.06);
        else y = terrainHeight(x, z) + 0.035 + Math.sin(t * 16 + side) * 0.02;
        bankPositions.push(x, y, z);
        bankUVs.push(t * 7, j * 0.28);
      }
    }
    for (let i = 0; i < bankRows; i++) {
      for (let j = 0; j < bankAcross - 1; j++) {
        const a = start + i * bankAcross + j;
        const b = a + 1;
        const c = a + bankAcross;
        const d = c + 1;
        if (side > 0) bankIndices.push(a, c, b, b, c, d);
        else bankIndices.push(a, b, c, b, d, c);
      }
    }
  }
  const bankGeo = new THREE.BufferGeometry();
  bankGeo.setAttribute('position', new THREE.Float32BufferAttribute(bankPositions, 3));
  bankGeo.setAttribute('uv', new THREE.Float32BufferAttribute(bankUVs, 2));
  bankGeo.setIndex(bankIndices);
  bankGeo.computeVertexNormals();
  const bank = new THREE.Mesh(bankGeo, materials.snow);
  bank.receiveShadow = true;
  bank.castShadow = true;
  group.add(bank);

  // Fine, branching cracks sit just above the ice and remain legible in a close view.
  const crackRng = mulberry32(9021);
  const crackPositions = [];
  for (let i = 0; i < 38; i++) {
    const t = 0.06 + crackRng() * 0.86;
    const c = creekPoint(t);
    const c2 = creekPoint(Math.min(1, t + 0.01));
    const c1 = creekPoint(Math.max(0, t - 0.01));
    const tan = new THREE.Vector2(c2.x - c1.x, c2.z - c1.z).normalize();
    const normal = new THREE.Vector2(-tan.y, tan.x);
    const width = 3.65 + Math.sin(t * 10.2) * 0.28;
    let u = 0.12 + crackRng() * 0.74;
    let x = c.x + normal.x * (u - 0.5) * width;
    let z = c.z + normal.y * (u - 0.5) * width;
    const dir = crackRng() * TAU;
    const steps = 2 + Math.floor(crackRng() * 4);
    for (let s = 0; s < steps; s++) {
      const len = 0.08 + crackRng() * 0.22;
      const nx = x + Math.cos(dir + (crackRng() - 0.5) * 0.8) * len;
      const nz = z + Math.sin(dir + (crackRng() - 0.5) * 0.8) * len;
      crackPositions.push(x, -0.356, z, nx, -0.356, nz);
      x = nx; z = nz;
      if (crackRng() > 0.6) {
        const bx = x + Math.cos(dir + 1.1) * 0.09;
        const bz = z + Math.sin(dir + 1.1) * 0.09;
        crackPositions.push(x, -0.355, z, bx, -0.355, bz);
      }
    }
  }
  const crackGeo = new THREE.BufferGeometry();
  crackGeo.setAttribute('position', new THREE.Float32BufferAttribute(crackPositions, 3));
  const cracks = new THREE.LineSegments(crackGeo, new THREE.LineBasicMaterial({
    color: 0xb9d6e8,
    transparent: true,
    opacity: 0.32,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  }));
  cracks.renderOrder = 2;
  group.add(cracks);

  // Broken cold reflections: short, splintered strokes rather than one long mirror.
  const rng = mulberry32(3129);
  const glowGeo = new THREE.PlaneGeometry(1, 1);
  const coldMat = new THREE.MeshBasicMaterial({
    map: materials.textures.glow,
    color: 0xbfe0ff,
    transparent: true,
    opacity: 0.19,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const cold = new THREE.InstancedMesh(glowGeo, coldMat, 58);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < 58; i++) {
    const t = 0.13 + rng() * 0.8;
    const c = creekPoint(t);
    dummy.position.set(c.x + (rng() - 0.5) * 2.2, -0.365, c.z + (rng() - 0.5) * 0.8);
    dummy.rotation.set(-Math.PI / 2, 0, rng() * Math.PI);
    dummy.scale.set(0.08 + rng() * 0.1, 0.25 + rng() * .53, 1);
    dummy.updateMatrix();
    cold.setMatrixAt(i, dummy.matrix);
  }
  cold.instanceMatrix.needsUpdate = true;
  cold.renderOrder = 2;
  group.add(cold);

  const warmMat = new THREE.MeshBasicMaterial({
    map: materials.textures.glow,
    color: 0xffb56d,
    transparent: true,
    opacity: 0.16,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const warm = new THREE.InstancedMesh(glowGeo, warmMat, 9);
  for (let i = 0; i < 9; i++) {
    const t = 0.48 + rng() * 0.28;
    const c = creekPoint(t);
    dummy.position.set(c.x + 0.5 + (rng() - 0.5) * 1.4, -0.362, c.z + (rng() - 0.5) * 0.7);
    dummy.rotation.set(-Math.PI / 2, 0, rng() * Math.PI);
    dummy.scale.set(0.12 + rng() * 0.13, 0.5 + rng() * 0.5, 1);
    dummy.updateMatrix();
    warm.setMatrixAt(i, dummy.matrix);
  }
  warm.instanceMatrix.needsUpdate = true;
  warm.renderOrder = 2;
  group.add(warm);

  // Frosted edges and snow tongues overlapping the ice.
  const edgeSnowGeo = new THREE.IcosahedronGeometry(1, 1);
  const edgeSnow = new THREE.InstancedMesh(edgeSnowGeo, materials.snowLump, 22);
  let idx = 0;
  for (let side = -1; side <= 1; side += 2) {
    for (let i = 0; i < 11; i++) {
      const t = (i + 0.25) / 11;
      const c = creekPoint(t);
      const c2 = creekPoint(Math.min(1, t + 0.01));
      const c1 = creekPoint(Math.max(0, t - 0.01));
      const tan = new THREE.Vector2(c2.x - c1.x, c2.z - c1.z).normalize();
      const normal = new THREE.Vector2(-tan.y, tan.x);
      const width = 3.65 + Math.sin(t * 10.2) * 0.28;
      const off = side * (width * 0.53 + 0.1 + rng() * 0.34);
      dummy.position.set(c.x + normal.x * off, -0.23 + rng() * 0.12, c.z + normal.y * off);
      dummy.rotation.set(rng() * 0.25, rng() * TAU, rng() * 0.25);
      dummy.scale.set(0.24 + rng() * 0.26, 0.04 + rng() * 0.06, 0.2 + rng() * 0.25);
      dummy.updateMatrix();
      edgeSnow.setMatrixAt(idx++, dummy.matrix);
      edgeSnow.setColorAt(idx - 1, new THREE.Color(0.82 + rng() * 0.12, 0.9, 1));
    }
  }
  edgeSnow.instanceMatrix.needsUpdate = true;
  edgeSnow.castShadow = true;
  edgeSnow.receiveShadow = true;
  group.add(edgeSnow);

  // Bare branches poking through the snowy bank.
  for (let i = 0; i < 9; i++) {
    const t = 0.15 + rng() * 0.7;
    const c = creekPoint(t);
    const side = rng() > 0.5 ? 1 : -1;
    const a = new THREE.Vector3(c.x + side * 2.1, -0.22, c.z);
    const b = new THREE.Vector3(a.x + (rng() - 0.5) * 0.5, 0.24 + rng() * 0.28, a.z + (rng() - 0.5) * 0.35);
    group.add(makeCylinderBetween(a, b, 0.018, materials.branch, 5, 0.005));
  }

  return { group, uniforms };
}

function createPath(materials, doorstep) {
  const group = new THREE.Group();
  const rng = mulberry32(4342);
  const pts = [
    new THREE.Vector3(1.2, 0, 4.9),
    new THREE.Vector3(0.2, 0, 3.2),
    new THREE.Vector3(-1.0, 0, 1.8),
    new THREE.Vector3(-0.7, 0, 0.5),
    new THREE.Vector3(0.8, 0, -0.3),
    new THREE.Vector3(2.2, 0, .24),
    doorstep.clone(),
  ];
  for (const p of pts) p.y = terrainHeight(p.x, p.z);
  const curve = new THREE.CatmullRomCurve3(pts, false, 'catmullrom', 0.32);
  const pathMat = new THREE.MeshStandardMaterial({
    color: 0xbfc6c5,
    bumpMap: materials.textures.stone.bump,
    bumpScale: 0.04,
    roughness: 0.9,
    metalness: 0.02,
  });
  const stoneGeo = new THREE.DodecahedronGeometry(1, 1);
  const stones = new THREE.InstancedMesh(stoneGeo, pathMat, 26);
  const caps = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump, 11);
  const dummy = new THREE.Object3D();
  const stoneColor = new THREE.Color();
  for (let i = 0; i < 26; i++) {
    const t = 0.03 + (i / 25) * 0.92;
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize();
    p.addScaledVector(side, (rng() - 0.5) * 0.34);
    p.y = terrainHeight(p.x, p.z);
    const sx = 0.46 + rng() * 0.24;
    const sz = 0.34 + rng() * 0.18;
    dummy.position.set(p.x, p.y + 0.0, p.z);
    dummy.rotation.set((rng() - 0.5) * 0.08, rng() * TAU, (rng() - 0.5) * 0.08);
    dummy.scale.set(sx, 0.055, sz);
    dummy.updateMatrix();
    stones.setMatrixAt(i, dummy.matrix);
    const shade = 0.74 + rng() * 0.18;
    stoneColor.setRGB(shade * 0.93, shade * 0.97, shade);
    stones.setColorAt(i, stoneColor);
  }
  stones.receiveShadow = true;
  stones.castShadow = true;
  stones.instanceMatrix.needsUpdate = true;
  if (stones.instanceColor) stones.instanceColor.needsUpdate = true;
  const firstTreadStone = dummy.position.clone();
  for (let i = 0; i < 11; i++) {
    const t = 0.06 + rng() * 0.88;
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize().multiplyScalar((rng() > 0.5 ? 1 : -1) * (0.25 + rng() * 0.2));
    p.add(side);
    p.y = terrainHeight(p.x, p.z);
    dummy.position.set(p.x, p.y + 0.035, p.z);
    dummy.rotation.set(0, rng() * TAU, 0);
    dummy.scale.set(0.12 + rng() * 0.16, 0.022, 0.1 + rng() * 0.14);
    dummy.updateMatrix();
    caps.setMatrixAt(i, dummy.matrix);
  }
  caps.receiveShadow = true;
  caps.instanceMatrix.needsUpdate = true;
  group.add(stones, caps);

  // Snowbanks at the path margins.
  const bankGeo = new THREE.IcosahedronGeometry(1, 1);
  const bank = new THREE.InstancedMesh(bankGeo, materials.snowLump, 76);
  for (let i = 0; i < 76; i++) {
    const t = rng();
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize();
    p.addScaledVector(side, (rng() > 0.5 ? 1 : -1) * (0.48 + rng() * 0.3));
    p.y = terrainHeight(p.x, p.z);
    dummy.position.set(p.x, p.y + 0.02, p.z);
    dummy.rotation.set(0, rng() * TAU, 0);
    dummy.scale.set(0.16 + rng() * 0.3, 0.05 + rng() * 0.09, 0.16 + rng() * 0.3);
    dummy.updateMatrix();
    bank.setMatrixAt(i, dummy.matrix);
    bank.setColorAt(i, new THREE.Color(0.82 + rng() * 0.13, 0.9, 1));
  }
  bank.receiveShadow = true;
  bank.instanceMatrix.needsUpdate = true;
  group.add(bank);

  // A few shallow boot prints lead up to the steps.
  const printGeo = new THREE.CylinderGeometry(1, 1, 0.02, 10);
  const printMat = new THREE.MeshStandardMaterial({
    color: 0x648096,
    roughness: 1,
    transparent: true,
    opacity: 0.46,
    depthWrite: false,
  });
  const prints = new THREE.InstancedMesh(printGeo, printMat, 18);
  for (let i = 0; i < 18; i++) {
    const t = 0.38 + (i / 17) * 0.58;
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize();
    p.addScaledVector(side, i % 2 === 0 ? 0.32 : -0.32);
    p.y = terrainHeight(p.x, p.z) + 0.03;
    dummy.position.copy(p);
    dummy.rotation.set(0, Math.atan2(tan.x, tan.z), 0);
    dummy.scale.set(0.085, 1, 0.18);
    dummy.updateMatrix();
    prints.setMatrixAt(i, dummy.matrix);
  }
  prints.instanceMatrix.needsUpdate = true;
  prints.renderOrder = 2;
  group.add(prints);

  return { group, firstTreadStone };
}

function createRocksAndRoots(materials) {
  const group = new THREE.Group();
  const rng = mulberry32(6712);
  const rockGeo = new THREE.IcosahedronGeometry(1, 1);
  const pos = rockGeo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const s = 0.82 + rng() * 0.36;
    pos.setXYZ(i, pos.getX(i) * s, pos.getY(i) * s * 0.7, pos.getZ(i) * s);
  }
  rockGeo.computeVertexNormals();

  const spots = [
    [-3.7, 5.15, 0.95], [-3.4, 2.9, 0.44], [5.6, 3.1, 0.5], [3.8, 4.3, 0.35],
    [-6.2, 2.6, 0.35], [8.1, 2.2, 0.55], [-8.3, -0.3, 0.48], [6.7, 8.3, 0.45],
    [-2.1, 8.5, 0.33], [2.6, 8.9, 0.4],
  ];
  for (const [x, z, s] of spots) {
    const y = terrainHeight(x, z);
    const rock = new THREE.Mesh(rockGeo, materials.stone);
    rock.position.set(x, y + s * 0.16, z);
    rock.scale.set(s, s * 0.65, s * 0.86);
    rock.rotation.set(rng() * 0.4, rng() * TAU, rng() * 0.25);
    rock.castShadow = true;
    rock.receiveShadow = true;
    group.add(rock);
    const snow = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump);
    snow.position.set(x, y + s * 0.45, z);
    snow.scale.set(s * 0.8, s * 0.14, s * 0.66);
    snow.rotation.y = rng() * TAU;
    snow.castShadow = true;
    group.add(snow);
  }

  // Foreground root tangles, with snow packed into the crotches.
  for (const [x, z, side] of [[-3.75, 5.2, 1], [5.4, 3.05, -1]]) {
    const y = terrainHeight(x, z);
    for (let i = 0; i < 7; i++) {
      const a = -0.65 + i * 0.22;
      const p0 = new THREE.Vector3(x + side * (rng() - 0.5) * 0.75, y + 0.16, z + (rng() - 0.5) * 0.55);
      const p1 = new THREE.Vector3(x + Math.cos(a) * 1.15 * side, y + 0.38 + rng() * 0.3, z + Math.sin(a) * 1.0);
      group.add(makeCylinderBetween(p0, p1, 0.17 + rng() * 0.09, materials.branch, 7, 0.045));
      const snow = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump);
      snow.position.copy(p1);
      snow.position.y += 0.02;
      snow.scale.set(0.24 + rng() * 0.24, 0.07, 0.15 + rng() * 0.17);
      snow.castShadow = true;
      group.add(snow);
    }
    // A half-buried rock anchors the tangle to the ground.
    const rock = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.stone);
    rock.position.set(x + side * 0.55, y + 0.22, z + 0.2);
    rock.scale.set(0.65, 0.32, 0.52);
    rock.rotation.set(0.2, rng() * TAU, 0.1);
    rock.castShadow = true;
    group.add(rock);
    const cap = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
    cap.position.set(x + side * 0.55, y + 0.46, z + 0.2);
    cap.scale.set(0.58, 0.12, 0.46);
    cap.castShadow = true;
    group.add(cap);
  }
  return group;
}

function createBackgroundForest(materials) {
  const group = new THREE.Group();
  const rng = mulberry32(8842);
  const trees = [];
  for (let i = 0; i < 68; i++) {
    const x = -24 + rng() * 48;
    const z = -7.4 - rng() * 22;
    if (x > 0.4 && x < 10.2 && z > -12) continue;
    if (x > -4.8 && x < 5.8 && z > -15.5) continue;
    if (Math.hypot(x + 2.6, z + 2.5) < 6.0) continue;
    const h = 4.8 + rng() * 8.2;
    trees.push({
      x, z, h,
      width: h * (0.33 + rng() * 0.16),
      rot: rng() * TAU,
      variant: i % 4,
    });
  }
  // Edge sentinels sit far enough back to be softened by the fog.
  for (const [x, z, h, rot, variant] of [
    [-12.8, -5.8, 10.8, 0.7, 1],
    [13.2, -6.6, 11.6, 2.3, 2],
    [-15.0, -11.5, 12.8, 1.1, 0],
    [16.0, -12.0, 13.4, 4.0, 3],
  ]) {
    trees.push({ x, z, h, width: h * 0.4, rot, variant });
  }

  const planeGeo = new THREE.PlaneGeometry(1, 1);
  planeGeo.translate(0, 0.5, 0);
  const buckets = [[], [], [], []];
  for (const t of trees) buckets[t.variant].push(t);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  buckets.forEach((bucket, variant) => {
    const mat = new THREE.MeshBasicMaterial({
      map: materials.textures.pineSilhouettes[variant],
      color: 0xffffff,
      transparent: true,
      alphaTest: 0.22,
      depthWrite: true,
      side: THREE.DoubleSide,
      fog: true,
    });
    const mesh = new THREE.InstancedMesh(planeGeo, mat, bucket.length * 3);
    let idx = 0;
    for (const tree of bucket) {
      const baseY = terrainHeight(tree.x, tree.z);
      const shade = 0.58 + rng() * 0.2;
      for (let p = 0; p < 3; p++) {
        dummy.position.set(tree.x, baseY, tree.z);
        dummy.rotation.set(0, tree.rot + (p * Math.PI) / 3, 0);
        dummy.scale.set(tree.width, tree.h, 1);
        dummy.updateMatrix();
        mesh.setMatrixAt(idx, dummy.matrix);
        color.setRGB(shade * 0.8, shade * 0.9, shade);
        mesh.setColorAt(idx, color);
        idx++;
      }
    }
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    mesh.frustumCulled = true;
    group.add(mesh);
  });
  return group;
}

function createFog(materials) {
  const group = new THREE.Group();
  const rng = mulberry32(3382);
  const specs = [
    [-7, 2.2, -7.4, 20, 5.2, 0.55],
    [7, 2.9, -9.5, 24, 5.8, 0.46],
    [0, 3.8, -13.5, 33, 8.6, 0.5],
    [-14, 4.2, -13, 26, 7.0, 0.38],
    [14, 3.0, -7.8, 19, 4.8, 0.34],
    [0, 1.5, -6.0, 26, 4.0, 0.42],
    [-3, 1.7, -1.8, 18, 2.8, 0.22],
    [10, 1.9, -3.5, 13, 3.0, 0.2],
    [3, 0.8, 1.2, 24, 2.4, 0.16],
  ];
  for (const [x, y, z, sx, sy, op] of specs) {
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: materials.textures.mist,
      color: 0x8ea7c0,
      transparent: true,
      opacity: op,
      depthWrite: false,
      blending: THREE.NormalBlending,
    }));
    sprite.position.set(x, y, z);
    sprite.scale.set(sx, sy, 1);
    sprite.userData.baseX = x;
    sprite.userData.baseOpacity = op;
    sprite.userData.phase = rng() * TAU;
    group.add(sprite);
  }
  return group;
}

function createSnowfall(materials) {
  const rng = mulberry32(1977);
  const count = 2700;
  const positions = new Float32Array(count * 3);
  const speed = new Float32Array(count);
  const size = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    positions[i * 3] = -15 + rng() * 30;
    positions[i * 3 + 1] = 0.2 + rng() * 12;
    positions[i * 3 + 2] = -10 + rng() * 23;
    speed[i] = 0.18 + rng() * 0.45;
    size[i] = 0.025 + rng() * 0.055;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
  geo.setAttribute('aFallSpeed', new THREE.BufferAttribute(speed, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uMap: { value: materials.textures.glow },
      uTime: { value: 0 },
      uScale: { value: 900 },
      uOpacity: { value: 0.62 },
      uTreble: { value: 0 },
    },
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending,
    // 近处雪片更显眼，随距离收回原尺寸，让远处保留细雪。
    vertexShader: /* glsl */ `
      attribute float aSize;
      attribute float aFallSpeed;
      uniform float uTime;
      uniform float uScale;
      uniform float uTreble;
      varying float vA;
      void main() {
        vec3 p = position;
        p.y = .2 + mod(p.y - .2 - uTime * aFallSpeed, 12.);
        p.x += sin(uTime * 0.11 + position.z * 0.35) * 0.7;
        p.z += cos(uTime * 0.08 + position.x * 0.25) * 0.45;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        float depth = max(1.0, -mv.z);
        float flakeScale = 1.0 + .32 * (1.0 - smoothstep(16.0, 34.0, depth));
        gl_PointSize = clamp(aSize * uScale * flakeScale / depth, 0.8, 9.0);
        vA = smoothstep(28.0, 1.0, -mv.z) * 0.8 + 0.2;
        float crystal = step(.84, fract(aFallSpeed * 37.));
        vA *= 1. + uTreble * crystal;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uMap;
      uniform float uOpacity;
      varying float vA;
      void main() {
        vec4 t = texture2D(uMap, gl_PointCoord);
        gl_FragColor = vec4(vec3(0.82, 0.9, 1.0), t.a * vA * uOpacity);
      }
    `,
  });
  const points = new THREE.Points(geo, mat);
  points.name = 'christmas-snowfall';
  points.frustumCulled = false;
  points.renderOrder = 6;
  return { points, positions, speed, uniforms: mat.uniforms, count };
}

function createGroundSparkles(materials) {
  const rng = mulberry32(4291);
  const count = 900;
  const positions = [];
  const sizes = [];
  const phases = [];
  for (let i = 0; i < count; i++) {
    const x = -12 + rng() * 24;
    const z = -6 + rng() * 17;
    const y = terrainHeight(x, z) + 0.035;
    positions.push(x, y, z);
    sizes.push(0.012 + rng() * 0.02);
    phases.push(rng());
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('aSize', new THREE.Float32BufferAttribute(sizes, 1));
  geo.setAttribute('aPhase', new THREE.Float32BufferAttribute(phases, 1));
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uGlow: { value: 1 },
      uScale: { value: 900 },
      uMap: { value: materials.textures.glow },
    },
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: /* glsl */ `
      attribute float aSize;
      attribute float aPhase;
      uniform float uTime;
      uniform float uScale;
      uniform float uGlow;
      varying float vA;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = clamp(aSize * uScale / max(1.0, -mv.z), 0.5, 4.0);
        float tw = pow(max(0.0, sin(uTime * 3.0 + aPhase * 20.0)), 10.0);
        vA = 0.08 + tw * 0.85 * uGlow;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uMap;
      varying float vA;
      void main() {
        vec4 t = texture2D(uMap, gl_PointCoord);
        gl_FragColor = vec4(vec3(0.85, 0.93, 1.0), t.a * vA);
      }
    `,
  });
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false;
  return { points, uniforms: mat.uniforms };
}

export function createEnvironment(materials, doorstep) {
  const group = new THREE.Group();
  const sky = makeSky(materials);
  group.add(sky.group);
  const terrain = createTerrain(materials);
  group.add(terrain);
  const creek = createCreek(materials);
  group.add(creek.group);
  const path = createPath(materials, doorstep);
  group.add(path.group);
  group.add(createRocksAndRoots(materials));
  group.add(createBackgroundForest(materials));
  const fog = createFog(materials);
  group.add(fog);
  const snowfall = createSnowfall(materials);
  group.add(snowfall.points);
  const sparkles = createGroundSparkles(materials);
  group.add(sparkles.points);

  // Near boughs frame the top corners. They are lit mostly by the moon.
  const boughLeft = createForegroundBough(materials, { seed: 913, length: 5.2 });
  boughLeft.position.set(-6.6, 8.35, 4.0);
  boughLeft.rotation.set(0.06, -0.1, 0.2);
  group.add(boughLeft);
  const boughRight = createForegroundBough(materials, { seed: 102, length: 3.3 });
  boughRight.position.set(8.35, 8.75, 3.55);
  boughRight.rotation.set(-0.03, Math.PI + 0.18, -0.12);
  group.add(boughRight);

  return { group, creek, snowfall, sparkles, fog, skyStars: sky.stars, firstTreadStone: path.firstTreadStone };
}
