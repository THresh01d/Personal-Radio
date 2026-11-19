import * as THREE from 'three';
import { TAU, mulberry32, lerp, clamp, makeCylinderBetween } from './util.js';

function makeNeedleSprigGeometry() {
  const positions = [];
  const indices = [];
  const rng = mulberry32(1402);
  let vi = 0;
  const planes = 3;
  for (let p = 0; p < planes; p++) {
    const rot = p * Math.PI / 3.1;
    const cos = Math.cos(rot), sin = Math.sin(rot);
    for (let i = 0; i < 18; i++) {
      const t = 0.06 + (i / 17) * 0.9;
      const side = i % 2 === 0 ? -1 : 1;
      const spread = 0.62 + rng() * 0.45;
      const len = 0.055 + rng() * 0.065;
      const width = 0.0045 + rng() * 0.0035;
      const baseY = t * 0.22;
      const bx = 0, by = baseY;
      const tipX = side * Math.sin(spread) * len;
      const tipY = baseY + Math.cos(spread) * len * 0.72;
      const nx = Math.cos(spread) * width * side;
      const ny = -Math.sin(spread) * width;
      const p1 = [bx - nx, by - ny, 0], p2 = [bx + nx, by + ny, 0], p3 = [tipX, tipY, 0];
      for (const v of [p1, p2, p3]) {
        positions.push(v[0] * cos - v[2] * sin, v[1], v[0] * sin + v[2] * cos);
      }
      indices.push(vi, vi + 1, vi + 2);
      vi += 3;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  g.setIndex(indices);
  g.computeVertexNormals();
  return g;
}

function makeGlowPoints(positions, sizes, phases, colors, glowTexture, uScale, opacity = 1) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('aSize', new THREE.Float32BufferAttribute(sizes, 1));
  geo.setAttribute('aPhase', new THREE.Float32BufferAttribute(phases, 1));
  geo.setAttribute('aColor', new THREE.Float32BufferAttribute(colors, 3));
  const uniforms = {
    uTime: { value: 0 },
    uGlow: { value: 1 },
    uScale: { value: uScale },
    uMap: { value: glowTexture },
    uOpacity: { value: opacity },
    uResponse: { value: 0 },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexColors: false,
    vertexShader: /* glsl */ `
      attribute float aSize;
      attribute float aPhase;
      attribute vec3 aColor;
      uniform float uTime;
      uniform float uGlow;
      uniform float uScale;
      uniform float uResponse;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        // 灯珠略抬离枝面，保留屋檐等前景对灯串的正常遮挡。
        mv.z += .08;
        float twinkle = 0.76 + 0.24 * sin(uTime * 2.1 + aPhase * 6.2831);
        float pulse = 1.0 + uGlow * 0.18;
        float sz = aSize * pulse * (0.86 + 0.32 * sin(uTime * 0.7 + aPhase * 3.0));
        gl_PointSize = clamp(sz * 1.12 * uScale / max(1.0, -mv.z), 0.8, 11.0);
        vColor = aColor * (0.88 + twinkle * 0.34);
        vAlpha = 0.55 + twinkle * 0.45;
        float branchTip = smoothstep(.8, 1.6, length(position.xz)) * step(.69, aPhase);
        vAlpha *= 1. + branchTip * uResponse;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uMap;
      uniform float uOpacity;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        float d = length(gl_PointCoord - vec2(0.5));
        if (d > 0.5) discard;
        float core = smoothstep(0.5, 0.05, d);
        float halo = smoothstep(0.5, 0.12, d);
        vec3 col = vColor * (0.54 + core * 1.05);
        gl_FragColor = vec4(col * 1.48, (core * 0.82 + halo * 0.22) * vAlpha * uOpacity);
      }
    `,
  });
  const points = new THREE.Points(geo, mat);
  points.name = opacity < .5 ? 'winter-tree-halos' : 'winter-tree-lights';
  points.frustumCulled = false;
  return { points, uniforms };
}

export function createFirTree(materials, glowTexture, options = {}) {
  const group = new THREE.Group();
  group.name = 'silver-christmas-tree';
  const rng = mulberry32(options.seed ?? 2812);
  const height = options.height ?? 8.9;
  const baseRadius = options.radius ?? 3.35;
  const tiers = options.tiers ?? 17;

  const branchMatrices = [];
  const snowMatrices = [];
  const snowColors = [];
  const ribbonPositions = [];
  const ribbonUVs = [];
  const ribbonIndices = [];
  const needleMatrices = [];
  const needleColors = [];
  const lightPositions = [];
  const lightSizes = [];
  const lightPhases = [];
  const lightColors = [];
  const glowPositions = [];
  const glowSizes = [];
  const glowPhases = [];
  const glowColors = [];
  const tipLights = [];

  const dummy = new THREE.Object3D();
  const up = new THREE.Vector3(0, 1, 0);
  const xAxis = new THREE.Vector3(1, 0, 0);
  const needleSprig = makeNeedleSprigGeometry();

  function addBranchSegment(a, b, radius) {
    const dir = new THREE.Vector3().subVectors(b, a);
    const len = dir.length();
    if (len < 0.0001) return;
    dummy.position.copy(a).addScaledVector(dir, 0.5);
    dummy.quaternion.setFromUnitVectors(up, dir.clone().normalize());
    dummy.scale.set(radius, len, radius);
    dummy.updateMatrix();
    branchMatrices.push(dummy.matrix.clone());
  }

  function addSnowAt(point, tangent, scale = 1) {
    const q = new THREE.Quaternion().setFromUnitVectors(xAxis, tangent.clone().normalize());
    dummy.position.copy(point);
    dummy.quaternion.copy(q);
    dummy.scale.set((0.064 + rng() * 0.075) * scale, (0.02 + rng() * 0.021) * scale, (0.038 + rng() * 0.044) * scale);
    dummy.updateMatrix();
    snowMatrices.push(dummy.matrix.clone());
    const tint = 0.86 + rng() * 0.12;
    snowColors.push(new THREE.Color(tint * 0.86, tint * 0.93, tint));
  }

  // A continuous crown of snow lying on the upper half of the branch. This is
  // what keeps the close-up from reading as a pile of separate white marbles.
  function addSnowRibbon(points, thickness, isTwig) {
    const cross = 6;
    const start = ribbonPositions.length / 3;
    const offsets = [-1, -0.62, -0.2, 0.2, 0.62, 1];
    const crowns = [0.008, 0.021, 0.033, 0.034, 0.022, 0.007];
    for (let i = 0; i < points.length; i++) {
      const t = i / (points.length - 1);
      const p = points[i];
      const a = samplePath(points, Math.max(0, t - 0.03));
      const b = samplePath(points, Math.min(1, t + 0.03));
      const tangent = new THREE.Vector3().subVectors(b, a).normalize();
      const side = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();
      const irregular = 0.82 + 0.28 * Math.sin(t * 33 + thickness * 91) + rng() * 0.1;
      const baseFade = 0.35 + 0.65 * Math.min(1, t / 0.12);
      const width = clamp(
        thickness * (isTwig ? 0.9 : 1.55) * (1 - t * 0.74) * irregular * baseFade + 0.005,
        0.006,
        isTwig ? 0.04 : 0.095
      );
      for (let j = 0; j < cross; j++) {
        const off = offsets[j] * width * (1 + (rng() - 0.5) * 0.18);
        const crown = crowns[j] + (rng() - 0.5) * 0.004;
        ribbonPositions.push(
          p.x + side.x * off,
          p.y + crown + 0.01 * Math.sin(t * 21 + j),
          p.z + side.z * off
        );
        ribbonUVs.push(j / (cross - 1), t * 9.0);
      }
    }
    for (let i = 0; i < points.length - 1; i++) {
      for (let j = 0; j < cross - 1; j++) {
        const a = start + i * cross + j;
        const b = a + 1;
        const c = a + cross;
        const d = c + 1;
        ribbonIndices.push(a, c, b, b, c, d);
      }
    }
  }

  function addNeedleSprig(point, direction, scale = 1) {
    dummy.position.copy(point);
    dummy.quaternion.setFromUnitVectors(up, direction.clone().normalize());
    dummy.rotateY(rng() * TAU);
    const s = (0.56 + rng() * 0.42) * scale;
    dummy.scale.set(s, s, s);
    dummy.updateMatrix();
    needleMatrices.push(dummy.matrix.clone());
    const shade = 0.75 + rng() * 0.4;
    needleColors.push(new THREE.Color(0.055 * shade, 0.13 * shade, 0.12 * shade));
  }

  function addLight(point, size = 1) {
    const px = point.x + (rng() - 0.5) * 0.13;
    const pz = point.z + (rng() - 0.5) * 0.13;
    const py = point.y + 0.025 + rng() * 0.07;
    const warmShift = rng() > 0.9 ? 0.08 : 0;
    const brightness = 0.82 + rng() * 0.18;
    lightPositions.push(px, py, pz);
    lightSizes.push((0.009 + rng() * 0.015) * size);
    lightPhases.push(rng());
    lightColors.push((0.84 + warmShift) * brightness, (0.92 + warmShift * 0.5) * brightness, 1.0 * brightness);
  }

  function addGlowLight(point, size) {
    glowPositions.push(point.x, point.y + 0.02, point.z);
    glowSizes.push(size);
    glowPhases.push(rng());
    glowColors.push(0.66, 0.8, 1.0);
  }

  function processBranch(points, thickness, opts = {}) {
    const isTwig = opts.twig ?? false;
    const tangent = new THREE.Vector3();
    for (let i = 0; i < points.length - 1; i++) {
      const p = points[i], n = points[i + 1];
      const t = (i + 1) / (points.length - 1);
      const radius = thickness * Math.pow(1 - t, 0.72) + 0.006;
      addBranchSegment(p, n, radius);
    }

    if (!isTwig || rng() > 0.24) addSnowRibbon(points, thickness, isTwig);
    const snowCount = isTwig ? 4 : 11;
    for (let i = 0; i < snowCount; i++) {
      const t = 0.12 + (i / Math.max(1, snowCount - 1)) * 0.86;
      const p = samplePath(points, t);
      const ahead = samplePath(points, Math.min(1, t + 0.035));
      tangent.subVectors(ahead, p).normalize();
      p.y += 0.042 + rng() * 0.02;
      addSnowAt(p, tangent, isTwig ? 0.4 : 0.42 + rng() * 0.12);
    }

    const needleCount = isTwig ? 4 : 11;
    for (let i = 0; i < needleCount; i++) {
      const t = 0.3 + rng() * 0.68;
      const p = samplePath(points, t);
      const ahead = samplePath(points, Math.min(1, t + 0.07));
      tangent.subVectors(ahead, p).normalize();
      const side = new THREE.Vector3(-tangent.z, 0, tangent.x).normalize();
      const dir = tangent.clone().multiplyScalar(0.6).addScaledVector(side, (rng() - 0.5) * 1.2);
      dir.y -= 0.28 + rng() * 0.35;
      p.y -= 0.02;
      addNeedleSprig(p, dir, isTwig ? 0.8 : 1.0);
    }

    const lightCount = isTwig ? 26 : 54;
    for (let i = 0; i < lightCount; i++) {
      const bias = Math.pow(rng(), 0.4);
      const t = 0.08 + bias * 0.92;
      const p = samplePath(points, t);
      // Concentrate small lights near the branch rim.
      const side = new THREE.Vector3(-p.z, 0, p.x).normalize().multiplyScalar((rng() - 0.5) * 0.22);
      p.add(side);
      addLight(p, isTwig ? 0.74 : 1);
    }
    const tip = points[points.length - 1].clone();
    for (let i = 0; i < 5; i++) {
      addLight(tip.clone().add(new THREE.Vector3((rng() - 0.5) * 0.1, 0.02, (rng() - 0.5) * 0.1)), 1.02);
    }
    tipLights.push(tip);
    if (opts.glow !== false) addGlowLight(tip, isTwig ? 0.16 : 0.25);
  }

  function samplePath(points, t) {
    if (points.length === 2) return points[0].clone().lerp(points[1], t);
    const f = t * (points.length - 1);
    const i = Math.min(points.length - 2, Math.floor(f));
    const local = f - i;
    return points[i].clone().lerp(points[i + 1], local);
  }

  // Trunk and visible root flare.
  const trunkHeight = height - 0.7;
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.055, 0.31, trunkHeight, 9, 7),
    materials.bark
  );
  trunk.position.y = trunkHeight * 0.5;
  trunk.castShadow = true;
  trunk.receiveShadow = true;
  group.add(trunk);

  // Main whorls, each independently jittered so the silhouette is never a perfect cone.
  for (let tier = 0; tier < tiers; tier++) {
    const t = tier / (tiers - 1);
    const tierY = 1.02 + t * (height - 1.7) + (rng() - 0.5) * 0.1;
    const maxRadius = baseRadius * Math.pow(1 - t, 0.92) + 0.13;
    const count = Math.round(lerp(15, 5, t));
    const baseAngle = tier * 2.399 + rng() * 0.4;
    for (let j = 0; j < count; j++) {
      const angle = baseAngle + (j / count) * TAU + (rng() - 0.5) * 0.2;
      const radius = maxRadius * (0.72 + rng() * 0.4);
      const droop = 0.12 + radius * 0.16 + rng() * 0.06;
      const p0 = new THREE.Vector3(Math.cos(angle) * 0.09, tierY + (rng() - 0.5) * 0.08, Math.sin(angle) * 0.09);
      const p1 = new THREE.Vector3(Math.cos(angle) * radius * 0.55, tierY - 0.05 + rng() * 0.04, Math.sin(angle) * radius * 0.55);
      const p2 = new THREE.Vector3(Math.cos(angle) * radius, tierY - droop, Math.sin(angle) * radius);
      const curve = new THREE.QuadraticBezierCurve3(p0, p1, p2);
      const points = curve.getPoints(8);
      processBranch(points, 0.052 + (1 - t) * 0.026);

      // Secondary and tertiary twigs make the gaps feel airy up close.
      const twigCount = t > 0.82 ? 1 : 3;
      for (let k = 0; k < twigCount; k++) {
        const at = 0.34 + rng() * 0.48;
        const base = samplePath(points, at);
        const outward = new THREE.Vector3(Math.cos(angle), 0, Math.sin(angle));
        const tangent = new THREE.Vector3().subVectors(samplePath(points, Math.min(1, at + 0.06)), base).normalize();
        const side = new THREE.Vector3(-tangent.z, 0, tangent.x).multiplyScalar(k === 0 ? 1 : -1);
        const dir = outward.multiplyScalar(0.3).add(side.multiplyScalar(0.72)).normalize();
        const twigLen = (0.38 + rng() * 0.38) * (1 - t * 0.55);
        const tp0 = base.clone();
        const tp1 = base.clone().addScaledVector(dir, twigLen * 0.55);
        tp1.y += 0.03 + rng() * 0.04;
        const tp2 = base.clone().addScaledVector(dir, twigLen);
        tp2.y -= 0.04 + rng() * 0.05;
        const twigCurve = new THREE.QuadraticBezierCurve3(tp0, tp1, tp2);
        processBranch(twigCurve.getPoints(4), 0.017, { twig: true });
        if (k < 2) {
          const subBase = samplePath(twigCurve.getPoints(4), 0.55);
          const subDir = new THREE.Vector3(
            (rng() - 0.5) * 1.2,
            0.08 + rng() * 0.2,
            (rng() - 0.5) * 1.2
          ).normalize();
          const subEnd = subBase.clone().addScaledVector(subDir, 0.28 + rng() * 0.25);
          subEnd.y -= 0.06;
          const subCurve = new THREE.QuadraticBezierCurve3(
            subBase,
            subBase.clone().lerp(subEnd, 0.55).add(new THREE.Vector3(0, 0.04, 0)),
            subEnd
          );
          processBranch(subCurve.getPoints(3), 0.009, { twig: true, glow: false });
        }
      }
    }
  }

  // The leader at the crown.
  for (let i = 0; i < 5; i++) {
    const y = height - 1.1 + i * 0.22;
    const r = 0.2 * (1 - i / 6);
    for (let j = 0; j < 4; j++) {
      const a = i * 2.4 + j * TAU / 4;
      const pts = [
        new THREE.Vector3(0, y, 0),
        new THREE.Vector3(Math.cos(a) * r * 0.5, y + 0.09, Math.sin(a) * r * 0.5),
        new THREE.Vector3(Math.cos(a) * r, y + 0.2, Math.sin(a) * r),
      ];
      processBranch(pts, 0.018, { twig: true, glow: false });
    }
  }

  // Branch instances.
  const branchGeo = new THREE.CylinderGeometry(0.68, 1, 1, 6, 1);
  const branches = new THREE.InstancedMesh(branchGeo, materials.branch, branchMatrices.length);
  branchMatrices.forEach((m, i) => branches.setMatrixAt(i, m));
  branches.castShadow = true;
  branches.receiveShadow = true;
  branches.instanceMatrix.needsUpdate = true;
  group.add(branches);

  // Snow pillows on the upper side of every branch.
  const snowGeo = new THREE.IcosahedronGeometry(1, 1);
  const snow = new THREE.InstancedMesh(snowGeo, materials.snowLump, snowMatrices.length);
  snowMatrices.forEach((m, i) => {
    snow.setMatrixAt(i, m);
    snow.setColorAt(i, snowColors[i]);
  });
  snow.castShadow = true;
  snow.receiveShadow = true;
  snow.instanceMatrix.needsUpdate = true;
  if (snow.instanceColor) snow.instanceColor.needsUpdate = true;
  group.add(snow);

  // One merged ribbon mesh gives the branches a continuous snow load.
  const ribbonGeo = new THREE.BufferGeometry();
  ribbonGeo.setAttribute('position', new THREE.Float32BufferAttribute(ribbonPositions, 3));
  ribbonGeo.setAttribute('uv', new THREE.Float32BufferAttribute(ribbonUVs, 2));
  ribbonGeo.setIndex(ribbonIndices);
  ribbonGeo.computeVertexNormals();
  const ribbonMat = materials.snow.clone();
  ribbonMat.side = THREE.DoubleSide;
  ribbonMat.bumpScale = 0.028;
  const snowRibbon = new THREE.Mesh(ribbonGeo, ribbonMat);
  snowRibbon.castShadow = true;
  snowRibbon.receiveShadow = true;
  group.add(snowRibbon);

  // Dark blue-green needle fans on the underside.
  const needles = new THREE.InstancedMesh(needleSprig, materials.needle, needleMatrices.length);
  needleMatrices.forEach((m, i) => {
    needles.setMatrixAt(i, m);
    needles.setColorAt(i, needleColors[i]);
  });
  needles.instanceMatrix.needsUpdate = true;
  if (needles.instanceColor) needles.instanceColor.needsUpdate = true;
  needles.castShadow = false;
  group.add(needles);

  // Thousands of small, cool bulbs exactly along the sampled branch paths.
  const lightObj = makeGlowPoints(
    lightPositions, lightSizes, lightPhases, lightColors,
    glowTexture, 1385
  );
  lightObj.points.renderOrder = 4;
  group.add(lightObj.points);

  const glowObj = makeGlowPoints(
    glowPositions, glowSizes.map(s => s * 2.6), glowPhases, glowColors,
    glowTexture, 1385, 0.24
  );
  glowObj.points.renderOrder = 3;
  group.add(glowObj.points);

  // Roots gripping the snow mound.
  const rootMat = materials.branch;
  for (let i = 0; i < 9; i++) {
    const a = i / 9 * TAU + rng() * 0.35;
    const r0 = 0.18 + rng() * 0.1;
    const r1 = 0.52 + rng() * 0.45;
    const p0 = new THREE.Vector3(Math.cos(a) * r0, 0.28, Math.sin(a) * r0);
    const p1 = new THREE.Vector3(Math.cos(a) * (r0 + r1) * 0.55, 0.12 + rng() * 0.06, Math.sin(a) * (r0 + r1) * 0.55);
    const p2 = new THREE.Vector3(Math.cos(a) * (r0 + r1), 0.01 + rng() * 0.03, Math.sin(a) * (r0 + r1));
    group.add(makeCylinderBetween(p0, p1, 0.095, rootMat, 6, 0.06));
    group.add(makeCylinderBetween(p1, p2, 0.06, rootMat, 6, 0.012));
  }
  const baseSnowCount = 24;
  const baseSnowGeo = new THREE.IcosahedronGeometry(1, 2);
  const baseSnow = new THREE.InstancedMesh(baseSnowGeo, materials.snowLump, baseSnowCount);
  for (let i = 0; i < baseSnowCount; i++) {
    const a = rng() * TAU;
    const rad = 0.3 + rng() * 0.85;
    dummy.position.set(Math.cos(a) * rad, 0.02 + rng() * 0.11, Math.sin(a) * rad);
    dummy.quaternion.setFromEuler(new THREE.Euler(rng() * 0.2, rng() * TAU, rng() * 0.2));
    dummy.scale.set(0.28 + rng() * 0.4, 0.08 + rng() * 0.1, 0.28 + rng() * 0.38);
    dummy.updateMatrix();
    baseSnow.setMatrixAt(i, dummy.matrix);
    baseSnow.setColorAt(i, new THREE.Color(0.84 + rng() * 0.12, 0.9 + rng() * 0.08, 1.0));
  }
  baseSnow.castShadow = true;
  baseSnow.receiveShadow = true;
  baseSnow.instanceMatrix.needsUpdate = true;
  group.add(baseSnow);

  // Crystal star, made from two crossed faceted stars so it holds up in profile.
  const starGroup = new THREE.Group();
  starGroup.position.y = height + 0.38;
  const outline = [];
  for (let i = 0; i < 12; i++) {
    const a = -Math.PI / 2 + i / 12 * TAU;
    const r = i % 2 === 0 ? 0.48 : 0.13;
    outline.push([Math.cos(a) * r, Math.sin(a) * r]);
  }
  // 星芯隆起形成独立晶面，晶棱接光时仍能辨认切面。
  const vertices = [];
  for (let i = 0; i < outline.length; i++) {
    const a = outline[i], b = outline[(i + 1) % outline.length];
    vertices.push(0, 0, .12, ...a, .035, ...b, .035);
    vertices.push(0, 0, -.12, ...b, -.035, ...a, -.035);
    vertices.push(...a, .035, ...a, -.035, ...b, -.035, ...a, .035, ...b, -.035, ...b, .035);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  starGeo.computeVertexNormals();
  const starMat = new THREE.MeshPhysicalMaterial({
    color: 0x9fc2e3,
    emissive: 0x6faae9,
    emissiveIntensity: .62,
    metalness: 0.08,
    roughness: 0.25,
    clearcoat: 1,
    clearcoatRoughness: 0.06,
  });
  const star = new THREE.Mesh(starGeo, starMat);
  const star2 = star.clone();
  star2.rotation.y = Math.PI / 2;
  star2.scale.setScalar(0.82);
  const edgeMat = new THREE.LineBasicMaterial({
    color: 0xeaf6ff,
    transparent: true,
    opacity: 0.69,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(starGeo, 24), edgeMat);
  edge.scale.setScalar(1.02);
  const auraMat = new THREE.MeshBasicMaterial({
    color: 0xbcdfff,
    transparent: true,
    opacity: 0.12,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const aura = new THREE.Mesh(starGeo, auraMat);
  aura.scale.setScalar(1.035);
  starGroup.add(star, star2, aura, edge);
  const starGlow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture,
    color: 0xc6e4ff,
    transparent: true,
    opacity: 0.49,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    depthTest: false,
  }));
  starGlow.scale.set(2.6, 2.6, 1);
  starGlow.renderOrder = 8;
  starGroup.add(starGlow);

  const flareMat = new THREE.SpriteMaterial({
    map: glowTexture,
    color: 0xdaf0ff,
    transparent: true,
    opacity: 0.25,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    depthTest: false,
  });
  const flareH = new THREE.Sprite(flareMat);
  flareH.scale.set(6.4, 0.42, 1);
  flareH.renderOrder = 8;
  const flareV = new THREE.Sprite(flareMat.clone());
  flareV.material.opacity = 0.17;
  flareV.scale.set(0.42, 4.8, 1);
  flareV.renderOrder = 8;
  starGroup.add(flareH, flareV);
  const starLight = new THREE.PointLight(0xbcdcff, 7.0, 6.5, 2);
  starLight.position.set(0, -0.05, 0);
  starGroup.add(starLight);
  group.add(starGroup);

  return {
    group,
    uniforms: { core: lightObj.uniforms, glow: glowObj.uniforms },
    starMat,
    starGlow,
    starLight,
    starGroup,
    starAuraMat: auraMat,
    starFlares: [flareH, flareV],
    corePoints: lightObj.points,
    glowPoints: glowObj.points,
    detailMeshes: [snow, needles],
    tipLights,
    height,
  };
}

// Larger, separately built boughs frame the lens in the near foreground.
export function createForegroundBough(materials, options = {}) {
  const rng = mulberry32(options.seed ?? 811);
  const group = new THREE.Group();
  const length = options.length ?? 4.2;
  const start = new THREE.Vector3(0, 0, 0);
  const end = new THREE.Vector3(length, -0.4 - rng() * 0.4, (rng() - 0.5) * 1.2);
  const mid = new THREE.Vector3(length * 0.48, 0.22 + rng() * 0.2, end.z * 0.35);
  const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
  const pts = curve.getPoints(12);

  // Wooden spine.
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    const r = 0.12 * Math.pow(1 - i / (pts.length - 1), 0.65) + 0.012;
    group.add(makeCylinderBetween(a, b, r, materials.branch, 7, r * 0.82));
  }

  // Continuous snow load along the main bough.
  const boughSnowPositions = [];
  const boughSnowUVs = [];
  const boughSnowIndices = [];
  const boughOffsets = [-1, -0.35, 0.35, 1];
  const boughCrown = [0.006, 0.035, 0.036, 0.005];
  for (let i = 0; i < pts.length; i++) {
    const t = i / (pts.length - 1);
    const p = pts[i];
    const tan = curve.getTangent(t).normalize();
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize();
    const w = 0.1 * (1 - t * 0.7) + 0.02;
    for (let j = 0; j < 4; j++) {
      boughSnowPositions.push(
        p.x + side.x * boughOffsets[j] * w,
        p.y + boughCrown[j] + 0.045,
        p.z + side.z * boughOffsets[j] * w
      );
      boughSnowUVs.push(j / 3, t * 6);
    }
  }
  for (let i = 0; i < pts.length - 1; i++) {
    for (let j = 0; j < 3; j++) {
      const a = i * 4 + j;
      const b = a + 1;
      const c = a + 4;
      const d = c + 1;
      boughSnowIndices.push(a, c, b, b, c, d);
    }
  }
  const boughSnowGeo = new THREE.BufferGeometry();
  boughSnowGeo.setAttribute('position', new THREE.Float32BufferAttribute(boughSnowPositions, 3));
  boughSnowGeo.setAttribute('uv', new THREE.Float32BufferAttribute(boughSnowUVs, 2));
  boughSnowGeo.setIndex(boughSnowIndices);
  boughSnowGeo.computeVertexNormals();
  const boughSnowMat = materials.snow.clone();
  boughSnowMat.side = THREE.DoubleSide;
  const boughSnow = new THREE.Mesh(boughSnowGeo, boughSnowMat);
  boughSnow.castShadow = true;
  group.add(boughSnow);

  const snowGeo = new THREE.IcosahedronGeometry(1, 2);
  const snowCount = 28;
  const snow = new THREE.InstancedMesh(snowGeo, materials.snowLump, snowCount);
  const dummy = new THREE.Object3D();
  const xAxis = new THREE.Vector3(1, 0, 0);
  for (let i = 0; i < snowCount; i++) {
    const t = (i + 1) / (snowCount + 1);
    const p = curve.getPoint(t);
    const tan = curve.getTangent(t).normalize();
    const side = new THREE.Vector3(-tan.z, 0, tan.x).normalize();
    p.addScaledVector(side, (rng() - 0.5) * 0.13);
    p.y += 0.07 + rng() * 0.06;
    dummy.position.copy(p);
    dummy.quaternion.setFromUnitVectors(xAxis, tan);
    dummy.scale.set(0.14 + rng() * 0.15, 0.045 + rng() * 0.04, 0.08 + rng() * 0.08);
    dummy.updateMatrix();
    snow.setMatrixAt(i, dummy.matrix);
    snow.setColorAt(i, new THREE.Color(0.86 + rng() * 0.1, 0.92, 1));
  }
  snow.castShadow = true;
  snow.receiveShadow = true;
  snow.instanceMatrix.needsUpdate = true;
  group.add(snow);

  // Side twigs and needle sprays.
  const twigCount = 13;
  const twigSnowMatrices = [];
  const sprigGeo = makeNeedleSprigGeometry();
  const sprigs = new THREE.InstancedMesh(sprigGeo, materials.needle, twigCount * 8);
  let idx = 0;
  for (let i = 0; i < twigCount; i++) {
    const t = 0.12 + (i / twigCount) * 0.83;
    const base = curve.getPoint(t);
    const tan = curve.getTangent(t);
    const side = new THREE.Vector3(-tan.z, 0, tan.x).multiplyScalar(i % 2 ? 1 : -1);
    const dir = side.multiplyScalar(0.8).add(new THREE.Vector3(0, 0.32, 0)).normalize();
    const twigLen = 0.55 + rng() * 0.6;
    const a = base.clone();
    const b = base.clone().addScaledVector(dir, twigLen);
    b.y -= 0.12;
    group.add(makeCylinderBetween(a, b, 0.016, materials.branch, 5, 0.005));
    for (let j = 0; j < 8; j++) {
      const t2 = 0.18 + j * 0.1;
      const p = a.clone().lerp(b, t2);
      const d = new THREE.Vector3(
        (rng() - 0.5) * 1.1,
        0.12 + rng() * 0.35,
        (rng() - 0.5) * 1.1
      ).normalize();
      dummy.position.copy(p);
      dummy.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d);
      dummy.rotateY(rng() * TAU);
      const s = 1.05 + rng() * 0.65;
      dummy.scale.set(s, s, s);
      dummy.updateMatrix();
      sprigs.setMatrixAt(idx, dummy.matrix);
      sprigs.setColorAt(idx, new THREE.Color(0.045, 0.12, 0.11));
      idx++;
    }
    for (let j = 0; j < 3; j++) {
      const t2 = 0.25 + j * 0.25;
      const p = a.clone().lerp(b, t2);
      const d = b.clone().sub(a).normalize();
      dummy.position.copy(p);
      dummy.position.y += 0.03;
      dummy.quaternion.setFromUnitVectors(xAxis, d);
      dummy.scale.set(0.1 + rng() * 0.12, 0.028 + rng() * 0.02, 0.06 + rng() * 0.05);
      dummy.updateMatrix();
      twigSnowMatrices.push(dummy.matrix.clone());
    }
  }
  sprigs.instanceMatrix.needsUpdate = true;
  if (sprigs.instanceColor) sprigs.instanceColor.needsUpdate = true;
  group.add(sprigs);

  const twigSnow = new THREE.InstancedMesh(snowGeo, materials.snowLump, twigSnowMatrices.length);
  twigSnowMatrices.forEach((m, i) => {
    twigSnow.setMatrixAt(i, m);
    twigSnow.setColorAt(i, new THREE.Color(0.86, 0.92, 1));
  });
  twigSnow.instanceMatrix.needsUpdate = true;
  twigSnow.castShadow = true;
  group.add(twigSnow);

  return group;
}
