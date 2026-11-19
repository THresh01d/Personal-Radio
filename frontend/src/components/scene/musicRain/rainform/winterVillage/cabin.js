import * as THREE from 'three';
import { TAU, mulberry32, addBox, setShadow, makeCylinderBetween } from './util.js';

function beamBetween(parent, a, b, width, depth, material) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(len, width, depth), material);
  mesh.position.set((a.x + b.x) / 2, (a.y + b.y) / 2, a.z);
  mesh.rotation.z = Math.atan2(dy, dx);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

function makeLantern(materials, glowTexture) {
  const group = new THREE.Group();
  const { metal, glass, candleWax, flame } = materials;
  addBox(group, 0.32, 0.035, 0.32, 0, -0.19, 0, metal);
  addBox(group, 0.32, 0.035, 0.32, 0, 0.2, 0, metal);
  for (const [x, z] of [[-0.15, -0.15], [-0.15, 0.15], [0.15, -0.15], [0.15, 0.15]]) {
    addBox(group, 0.022, 0.39, 0.022, x, 0, z, metal);
  }
  const glassBox = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.31, 0.26), glass);
  glassBox.position.y = 0.0;
  glassBox.renderOrder = 2;
  group.add(glassBox);
  const candle = new THREE.Mesh(new THREE.CylinderGeometry(0.047, 0.055, 0.14, 8), candleWax);
  candle.position.y = -0.095;
  group.add(candle);
  const flameMesh = new THREE.Mesh(new THREE.ConeGeometry(0.03, 0.095, 7), flame);
  flameMesh.position.y = -0.008;
  group.add(flameMesh);
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture,
    color: 0xffb968,
    transparent: true,
    opacity: 0.6,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  }));
  glow.scale.set(1.15, 1.15, 1);
  group.add(glow);
  const light = new THREE.PointLight(0xffb464, 12.0, 6.0, 2);
  light.position.y = 0.03;
  group.add(light);
  // Pagoda cap and suspension ring.
  const roof = new THREE.Mesh(new THREE.ConeGeometry(0.25, 0.15, 4), metal);
  roof.position.y = 0.29;
  roof.rotation.y = Math.PI / 4;
  group.add(roof);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.012, 5, 12), metal);
  ring.position.y = 0.39;
  ring.rotation.x = Math.PI / 2;
  group.add(ring);
  group.userData.glow = glow;
  group.userData.light = light;
  return group;
}

function makeWindow(materials, width, height) {
  const group = new THREE.Group();
  const { warmGlass, timber, metal, snowLump } = materials;
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(width, height), warmGlass);
  glass.position.z = 0.008;
  group.add(glass);

  // Deep outer frame.
  const fw = 0.11;
  addBox(group, fw, height + fw * 2, 0.14, -width / 2 - fw / 2, 0, 0.055, timber);
  addBox(group, fw, height + fw * 2, 0.14, width / 2 + fw / 2, 0, 0.055, timber);
  addBox(group, width + fw * 2, fw, 0.14, 0, height / 2 + fw / 2, 0.055, timber);
  addBox(group, width + fw * 2, fw, 0.14, 0, -height / 2 - fw / 2, 0.055, timber);
  // Inner sash.
  addBox(group, 0.045, height, 0.075, 0, 0, 0.075, timber);
  addBox(group, width, 0.045, 0.075, 0, 0, 0.075, timber);
  // Sill.
  addBox(group, width + 0.3, 0.1, 0.31, 0, -height / 2 - 0.15, 0.13, timber);
  addBox(group, width + 0.16, 0.032, 0.2, 0, -height / 2 - 0.085, 0.16, snowLump, { castShadow: false });
  // A thin, broken line of snow on the sill.
  for (let i = 0; i < 3; i++) {
    const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), snowLump);
    mesh.position.set(-width / 2 + (i + 0.5) * (width / 3.2), -height / 2 - 0.065, 0.18 + (i % 2) * 0.04);
    mesh.scale.set(0.14 + (i % 3) * 0.03, 0.025, 0.08);
    mesh.castShadow = true;
    group.add(mesh);
  }
  // Curtain shadows behind the pane.
  const curtainMat = new THREE.MeshBasicMaterial({ map: materials.textures.windowTex, opacity: 0.25, transparent: true, depthWrite: false });
  for (const side of [-1, 1]) {
    const curtain = new THREE.Mesh(new THREE.PlaneGeometry(width * 0.28, height), curtainMat);
    curtain.position.set(side * width * 0.33, 0, 0.012);
    group.add(curtain);
  }
  // A guard bar across the lower half.
  addBox(group, width + 0.05, 0.025, 0.02, 0, -height * 0.2, 0.115, metal);
  return group;
}

function makeBoot(materials, mirrored = false) {
  const group = new THREE.Group();
  const { leather, metal } = materials;
  const soleMat = new THREE.MeshStandardMaterial({ color: 0x15100d, roughness: 0.72, metalness: 0.04 });

  // The side profile is extruded across the width, which reads as a real boot
  // from the front and from the side far more clearly than stacked cylinders.
  const shape = new THREE.Shape();
  shape.moveTo(-0.1, 0.0);
  shape.lineTo(0.25, 0.0);
  shape.quadraticCurveTo(0.35, 0.01, 0.36, 0.09);
  shape.quadraticCurveTo(0.35, 0.17, 0.25, 0.19);
  shape.quadraticCurveTo(0.14, 0.21, 0.1, 0.28);
  shape.lineTo(0.07, 0.42);
  shape.quadraticCurveTo(0.0, 0.46, -0.08, 0.43);
  shape.lineTo(-0.105, 0.19);
  shape.quadraticCurveTo(-0.12, 0.03, -0.1, 0.0);
  const bootGeo = new THREE.ExtrudeGeometry(shape, {
    depth: 0.18,
    bevelEnabled: true,
    bevelSegments: 2,
    steps: 1,
    bevelSize: 0.012,
    bevelThickness: 0.012,
  });
  bootGeo.translate(0, 0.022, -0.09);
  bootGeo.rotateY(-Math.PI / 2);
  const boot = new THREE.Mesh(bootGeo, leather);
  boot.castShadow = true;
  boot.receiveShadow = true;
  group.add(boot);

  // Thin sole and a heel block.
  addBox(group, 0.205, 0.028, 0.47, 0, 0.014, 0.12, soleMat);
  addBox(group, 0.19, 0.055, 0.11, 0, 0.042, -0.035, soleMat);
  const cuff = new THREE.Mesh(new THREE.TorusGeometry(0.105, 0.016, 5, 14), leather);
  cuff.rotation.x = Math.PI / 2;
  cuff.position.set(0, 0.45, -0.005);
  group.add(cuff);
  for (let i = 0; i < 3; i++) {
    const lace = new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.005, 4, 8, Math.PI), metal);
    lace.rotation.x = Math.PI / 2;
    lace.rotation.z = Math.PI / 2;
    lace.position.set(0, 0.28 + i * 0.045, 0.085);
    group.add(lace);
  }
  // A little melting snow around the sole.
  const snowRing = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
  snowRing.scale.set(0.17, 0.018, 0.23);
  snowRing.position.set(0, 0.008, 0.11);
  group.add(snowRing);
  if (mirrored) group.scale.x = -1;
  return group;
}

function addWreath(parent, materials, x, y, z) {
  const group = new THREE.Group();
  group.position.set(x, y, z);
  const { wreathGreen, redBerry, rope } = materials;
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.052, 8, 24), wreathGreen);
  group.add(ring);
  for (let i = 0; i < 18; i++) {
    const a = (i / 18) * TAU;
    const sprig = new THREE.Mesh(new THREE.ConeGeometry(0.038, 0.16, 4), wreathGreen);
    sprig.position.set(Math.cos(a) * 0.23, Math.sin(a) * 0.23, 0.01);
    sprig.rotation.z = -a + Math.PI / 2;
    sprig.rotation.x = 0.5;
    group.add(sprig);
  }
  for (let i = 0; i < 6; i++) {
    const a = i * 1.7;
    const berry = new THREE.Mesh(new THREE.SphereGeometry(0.025, 6, 5), redBerry);
    berry.position.set(Math.cos(a) * 0.2, Math.sin(a) * 0.2, 0.06);
    group.add(berry);
  }
  const bow = new THREE.Mesh(new THREE.TorusGeometry(0.075, 0.018, 5, 10, Math.PI * 1.4), rope);
  bow.position.set(0, -0.08, 0.08);
  bow.rotation.z = -0.6;
  group.add(bow);
  parent.add(group);
}

function cabinClamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

function snowEdge(z) {
  return cabinClamp(1.85 + Math.sin(z * 2.35 + 1.25) * 0.36 + Math.sin(z * 5.1 - 0.4) * 0.12, 0.95, 2.55);
}

function isChimneyPatch(x, z) {
  return Math.abs(x - 1.56) < 0.68 && Math.abs(z + 0.62) < 0.58;
}

function makeRoofSnowSheet(materials, length, depth, side) {
  const NX = 12;
  const NZ = 36;
  const positions = [];
  const uvs = [];
  const indices = [];
  for (let iz = 0; iz <= NZ; iz++) {
    const z = -depth / 2 + 0.1 + (iz / NZ) * (depth - 0.2);
    const edge = snowEdge(z);
    for (let ix = 0; ix <= NX; ix++) {
      const f = ix / NX;
      const x = 0.04 + f * (edge - 0.04);
      const bump = 0.018 * Math.sin(x * 9.2 + z * 3.1) + 0.014 * Math.sin(z * 8.0 - x * 4.0);
      positions.push(x, 0.148 + bump, z);
      uvs.push(f * 2.2, (z / depth + 0.5) * 2.4);
    }
  }
  for (let iz = 0; iz < NZ; iz++) {
    for (let ix = 0; ix < NX; ix++) {
      const a = iz * (NX + 1) + ix;
      const b = a + 1;
      const c = a + NX + 1;
      const d = c + 1;
      const cx = (positions[a * 3] + positions[b * 3] + positions[c * 3] + positions[d * 3]) / 4;
      const cz = (positions[a * 3 + 2] + positions[b * 3 + 2] + positions[c * 3 + 2] + positions[d * 3 + 2]) / 4;
      if (side === 'right' && isChimneyPatch(cx, cz)) continue;
      indices.push(a, c, b, b, c, d);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, materials.snow);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

const roofAngle = Math.atan2(2.1, 3.8);
const ridgeHeight = 6.2;
const deckCenter = -0.01;
const deckThickness = 0.13;

function makeEaveDrifts(material, lip, surface, spans) {
  const positions = [];
  const indices = [];
  const uvs = [];
  for (const [start, end, depth, thickness] of spans) {
    const segments = Math.ceil((end - start) / .065);
    const offset = positions.length / 3;
    for (let i = 0; i <= segments; i++) {
      const t = i / segments;
      const z = start + (end - start) * t;
      const taper = Math.min(1, t * 6, (1 - t) * 6);
      const edge = lip + taper * (.05 + .024 * Math.sin(z * 10 + .4));
      const width = depth * (.86 + .14 * Math.sin(z * 5.2 + 1.2)) * taper;
      const height = thickness * (.78 + .22 * Math.cos(z * 9.7)) * taper;
      positions.push(
        edge - width, surface - .012, z,
        edge - width * .78, surface + height * .54, z,
        edge - width * .40, surface + height, z,
        edge - width * .12, surface + height * .62, z,
        edge, surface - .022 * taper, z,
      );
      for (let j = 0; j < 5; j++) uvs.push(j / 4, z * .6);
      if (!i) continue;
      for (let j = 0; j < 4; j++) {
        const a = offset + (i - 1) * 5 + j;
        indices.push(a, a + 5, a + 1, a + 1, a + 5, a + 6);
      }
      const a = offset + (i - 1) * 5;
      indices.push(a + 4, a + 9, a, a, a + 9, a + 5);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function makeRoofSide(materials, side) {
  const roofRandom = mulberry32(7512);
  const group = new THREE.Group();
  // 左坡转半圈再倾斜，使瓦片和积雪同样朝上。
  if (side === 'left') group.rotateY(Math.PI);
  group.rotateZ(-roofAngle);
  group.position.y = ridgeHeight;
  const length = 4.82;
  const depth = 5.55;
  // 两片有厚度的板在脊线上搭接，保留原来的檐口外沿。
  const overlap = 0.05;
  const base = addBox(group, length + overlap, deckThickness, depth,
    (length - overlap) / 2, deckCenter, 0, materials.roof, { castShadow: true });
  base.receiveShadow = true;

  const rows = 13;
  const cols = 10;
  const shingleGeo = new THREE.BoxGeometry(0.37, 0.065, 0.56);
  const shingles = new THREE.InstancedMesh(shingleGeo, materials.shingle, rows * cols);
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  let idx = 0;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = 0.25 + r * 0.34 + (c % 2) * 0.06;
      const z = -depth / 2 + 0.3 + c * 0.53 + (r % 2) * 0.13;
      dummy.position.set(x, 0.085 + (r % 3) * 0.003, z);
      dummy.rotation.set(0, (roofRandom() - 0.5) * 0.02, 0);
      dummy.scale.set(1, 1, 1);
      dummy.updateMatrix();
      shingles.setMatrixAt(idx, dummy.matrix);
      const shade = 0.66 + roofRandom() * 0.22;
      color.setRGB(shade, shade * 1.01, shade * 1.03);
      shingles.setColorAt(idx, color);
      idx++;
    }
  }
  shingles.castShadow = true;
  shingles.receiveShadow = true;
  shingles.instanceMatrix.needsUpdate = true;
  if (shingles.instanceColor) shingles.instanceColor.needsUpdate = true;
  group.add(shingles);

  // A thin, continuous sheet of snow with a torn lower edge. It leaves most of
  // the shingle courses readable and opens a darker melt ring by the chimney.
  group.add(makeRoofSnowSheet(materials, length, depth, side));

  // Snow sits mostly near the ridge and on the windward edge. The patch around
  // the chimney is deliberately left darker and mostly clear.
  const snowGeo = new THREE.IcosahedronGeometry(1, 2);
  const snowCount = 36;
  const snow = new THREE.InstancedMesh(snowGeo, materials.snowLump, snowCount);
  for (let i = 0; i < snowCount; i++) {
    const z = -depth / 2 + 0.15 + roofRandom() * (depth - 0.3);
    const edge = snowEdge(z);
    const x = i < 16 ? 0.12 + roofRandom() * 0.8 : Math.max(0.35, edge - 0.32 - roofRandom() * 0.35);
    let zz = z;
    if (side === 'right' && isChimneyPatch(x, zz)) {
      zz += zz < -0.62 ? -0.62 : 0.62;
      zz = cabinClamp(zz, -depth / 2 + 0.2, depth / 2 - 0.2);
    }
    dummy.position.set(x, 0.155 + roofRandom() * 0.03, zz);
    dummy.rotation.set(0, roofRandom() * TAU, 0);
    const s = 0.12 + roofRandom() * 0.2;
    dummy.scale.set(s, 0.035 + roofRandom() * 0.025, 0.15 + roofRandom() * 0.22);
    dummy.updateMatrix();
    snow.setMatrixAt(i, dummy.matrix);
    snow.setColorAt(i, new THREE.Color(0.85 + roofRandom() * 0.1, 0.92, 1));
  }
  snow.castShadow = true;
  snow.receiveShadow = true;
  snow.instanceMatrix.needsUpdate = true;
  group.add(snow);

  // 挑檐上的雪沿分成几段，靠烟囱较薄，缺口露出瓦和木边。
  const drifts = side === 'right'
    ? [[-2.57, -1.13, .39, .097], [-.95, -.25, .24, .054], [.02, 1.01, .31, .11], [1.16, 2.69, .48, .13]]
    : [[-2.53, -.91, .42, .12], [-.76, .39, .34, .086], [.62, 1.54, .40, .107], [1.72, 2.66, .28, .075]];
  group.add(makeEaveDrifts(materials.snow, length - .035, .087, drifts));

  // Fascia board at the eave.
  addBox(group, 0.12, 0.27, depth, length - 0.05, -0.15, 0, materials.timber);
  return group;
}

export function createCabin(materials, glowTexture) {
  const rng = mulberry32(5738);
  const group = new THREE.Group();
  group.name = 'warm-cabin';
  const windowLights = [];
  const smokeSprites = [];

  // Stone base and body.
  addBox(group, 6.9, 0.55, 4.9, 0, 0.275, 0, materials.stone);
  addBox(group, 6.5, 3.2, 4.6, 0, 2.15, 0, materials.wall);
  addBox(group, 6.58, 0.18, 4.68, 0, 0.64, 0, materials.timber);

  // 山墙顶沿用屋面坡度并嵌进屋面板，贯通屋身以闭合前后山墙和两侧檐下。
  const wallHalfWidth = 3.25;
  const wallTop = 3.75;
  const wallFace = 2.305;
  const peak = ridgeHeight + (deckCenter - deckThickness / 2) / Math.cos(roofAngle) + 0.04 - wallTop;
  const shoulder = peak - wallHalfWidth * Math.tan(roofAngle);
  const gableShape = new THREE.Shape();
  gableShape.moveTo(-wallHalfWidth, -0.015);
  gableShape.lineTo(wallHalfWidth, -0.015);
  gableShape.lineTo(wallHalfWidth, shoulder);
  gableShape.lineTo(0, peak);
  gableShape.lineTo(-wallHalfWidth, shoulder);
  gableShape.closePath();
  const gable = new THREE.Mesh(
    new THREE.ExtrudeGeometry(gableShape, { depth: 2 * wallFace, bevelEnabled: false }),
    materials.wall,
  );
  gable.position.set(0, wallTop, -wallFace);
  gable.castShadow = true;
  gable.receiveShadow = true;
  group.add(gable);
  for (const z of [wallFace, -wallFace]) {
    // Gable trim.
    const trimY = 3.75;
    const trim = new THREE.Mesh(new THREE.BoxGeometry(6.65, 0.14, 0.12), materials.timber);
    trim.position.set(0, trimY + 0.02, z + (z > 0 ? 0.012 : -0.012));
    group.add(trim);
  }

  // Board-and-batten seams on the front and visible right wall.
  for (let i = 0; i < 9; i++) {
    const x = -3.0 + i * 0.75;
    if (Math.abs(x + 2.15) < 0.66) continue; // window
    if (Math.abs(x + 0.42) < 0.62) continue; // door
    if (Math.abs(x - 1.42) < 0.6) continue; // small front window
    addBox(group, 0.055, 3.05, 0.035, x, 2.16, 2.318, materials.timber, { castShadow: false });
  }
  for (let i = 0; i < 6; i++) {
    const z = -1.95 + i * 0.78;
    if (Math.abs(z + 0.2) < 0.66) continue;
    addBox(group, 0.035, 3.05, 0.055, 3.267, 2.16, z, materials.timber, { castShadow: false });
  }

  // Structural frame.
  for (const x of [-3.23, 3.23]) {
    for (const z of [-2.28, 2.28]) {
      addBox(group, 0.19, 3.35, 0.19, x, 2.12, z, materials.timber);
    }
  }
  addBox(group, 6.65, 0.19, 0.16, 0, 3.66, 2.31, materials.timber);
  addBox(group, 6.65, 0.17, 0.16, 0, 1.0, 2.31, materials.timber);
  addBox(group, 0.16, 3.3, 4.62, 3.24, 2.12, 0, materials.timber);
  addBox(group, 0.16, 3.3, 4.62, -3.24, 2.12, 0, materials.timber);
  beamBetween(group, new THREE.Vector3(-3.0, 3.45, 2.4), new THREE.Vector3(-2.5, 2.75, 2.4), 0.13, 0.1, materials.timber);
  beamBetween(group, new THREE.Vector3(3.0, 3.45, 2.4), new THREE.Vector3(2.5, 2.75, 2.4), 0.13, 0.1, materials.timber);
  // Gable kingpost and diagonal truss.
  addBox(group, 0.15, 2.28, 0.14, 0, 4.91, 2.36, materials.timber);
  beamBetween(group, new THREE.Vector3(-2.9, 3.87, 2.4), new THREE.Vector3(-0.06, 5.85, 2.4), 0.12, 0.1, materials.timber);
  beamBetween(group, new THREE.Vector3(2.9, 3.87, 2.4), new THREE.Vector3(0.06, 5.85, 2.4), 0.12, 0.1, materials.timber);

  // Main roof slopes.
  group.add(makeRoofSide(materials, 'right'), makeRoofSide(materials, 'left'));

  // Ridge snow, fat and irregular.
  const ridgeSnow = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump, 20);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < 20; i++) {
    dummy.position.set((rng() - 0.5) * 0.14, 6.26 + rng() * 0.04, -2.72 + i * 0.286);
    dummy.rotation.set(rng() * 0.1, rng() * TAU, rng() * 0.1);
    dummy.scale.set(0.18 + rng() * 0.12, 0.075 + rng() * 0.04, 0.18 + rng() * 0.11);
    dummy.updateMatrix();
    ridgeSnow.setMatrixAt(i, dummy.matrix);
    ridgeSnow.setColorAt(i, new THREE.Color(0.88 + rng() * 0.08, 0.94, 1));
  }
  ridgeSnow.castShadow = true;
  ridgeSnow.receiveShadow = true;
  ridgeSnow.instanceMatrix.needsUpdate = true;
  group.add(ridgeSnow);

  // Chimney.
  addBox(group, 0.78, 2.25, 0.78, 1.36, 5.92, -0.62, materials.stone);
  addBox(group, 0.94, 0.12, 0.94, 1.36, 7.08, -0.62, materials.stoneDark);
  addBox(group, 0.78, 0.12, 0.78, 1.36, 7.16, -0.62, materials.stone);
  addBox(group, 0.5, 0.08, 0.5, 1.36, 7.23, -0.62, materials.metal);
  for (let i = 0; i < 4; i++) {
    const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
    s.position.set(1.36 + (rng() - 0.5) * 0.54, 7.19 + rng() * 0.05, -0.62 + (rng() - 0.5) * 0.54);
    s.scale.set(0.17 + rng() * 0.12, 0.05 + rng() * 0.03, 0.17 + rng() * 0.1);
    s.castShadow = true;
    group.add(s);
  }

  // Porch floor and roof.
  addBox(group, 5.9, 0.16, 1.88, 0, 0.74, 3.22, materials.timber);
  for (let i = 0; i < 8; i++) {
    addBox(group, 0.03, 0.012, 1.82, -2.55 + i * 0.73, 0.828, 3.22, materials.darkWood, { castShadow: false });
  }
  const porchRoof = addBox(group, 6.0, 0.13, 2.3, 0, 3.33, 3.15, materials.roof);
  porchRoof.rotation.x = 0.19;
  const porchBlanket = addBox(group, 5.86, 0.055, 2.14, 0, 3.405, 3.16, materials.snow);
  porchBlanket.rotation.x = 0.19;
  porchBlanket.castShadow = true;
  porchBlanket.receiveShadow = true;
  const porchEdge = makeEaveDrifts(materials.snow, 1.05, .029,
    [[-2.89, -1.54, .27, .064], [-1.36, -.16, .21, .046], [.03, 1.61, .32, .071], [1.74, 2.84, .26, .065]]);
  porchEdge.rotation.y = -Math.PI / 2;
  porchBlanket.add(porchEdge);

  for (const x of [-2.66, 2.66]) {
    addBox(group, 0.17, 2.48, 0.17, x, 1.98, 4.04, materials.timber);
    const cap = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump);
    cap.position.set(x, 3.26, 4.04);
    cap.scale.set(0.15, 0.07, 0.15);
    cap.castShadow = true;
    group.add(cap);
    beamBetween(group, new THREE.Vector3(x, 2.84, 4.04), new THREE.Vector3(x + (x > 0 ? -0.65 : 0.65), 3.18, 3.03), 0.1, 0.09, materials.timber);
  }
  addBox(group, 5.75, 0.14, 0.18, 0, 3.12, 4.04, materials.timber);

  // Railing; leave the center open for the steps.
  function railSegment(x0, x1, z) {
    const mid = (x0 + x1) / 2;
    addBox(group, x1 - x0, 0.09, 0.11, mid, 1.55, z, materials.timber);
    addBox(group, x1 - x0, 0.07, 0.08, mid, 0.96, z, materials.timber);
    const count = Math.max(2, Math.round((x1 - x0) / 0.26));
    for (let i = 0; i <= count; i++) {
      const x = x0 + (i / count) * (x1 - x0);
      addBox(group, 0.045, 0.57, 0.045, x, 1.26, z, materials.darkWood, { castShadow: false });
    }
    addBox(group, x1 - x0, 0.035, 0.145, mid, 1.616, z, materials.snowLump, { castShadow: false });
    for (let i = 0; i < 3; i++) {
      const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
      s.position.set(x0 + (i + 0.55) * (x1 - x0) / 3, 1.63, z + (rng() - 0.5) * 0.05);
      s.scale.set(0.12 + rng() * 0.07, 0.025, 0.055 + rng() * 0.02);
      group.add(s);
    }
  }
  railSegment(-2.62, -1.28, 4.04);
  railSegment(0.48, 2.62, 4.04);
  // Side rail, visible from the camera's three-quarter angle.
  addBox(group, 0.11, 0.09, 1.45, 2.66, 1.55, 3.31, materials.timber);
  addBox(group, 0.08, 0.07, 1.45, 2.66, 0.96, 3.31, materials.timber);
  for (let i = 0; i < 6; i++) {
    addBox(group, 0.045, 0.57, 0.045, 2.66, 1.26, 2.66 + i * 0.26, materials.darkWood, { castShadow: false });
  }
  for (const x of [-2.62, -1.28, 0.48, 2.62]) {
    addBox(group, 0.15, 1.0, 0.15, x, 1.08, 4.04, materials.timber);
    const cap = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump);
    cap.position.set(x, 1.61, 4.04);
    cap.scale.set(0.13, 0.07, 0.13);
    group.add(cap);
  }

  // Steps. The lowest tread meets the snow, and each upper tread overlaps the one below.
  const stepMat = materials.timber;
  for (let i = 0; i < 4; i++) {
    const y = 0.69 - i * 0.145;
    const z = 4.02 + i * 0.29;
    const w = 1.8 + i * 0.04;
    addBox(group, w, 0.12, 0.34, -0.4, y, z, stepMat);
    addBox(group, w - 0.06, 0.13, 0.3, -0.4, y - 0.12, z + 0.02, materials.stoneDark);
    // Snow pushed to either side of the tread.
    for (const side of [-1, 1]) {
      const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
      s.position.set(-0.4 + side * (w / 2 - 0.09), y + 0.07, z + (rng() - 0.5) * 0.18);
      s.scale.set(0.15 + rng() * 0.08, 0.035, 0.12 + rng() * 0.05);
      group.add(s);
    }
  }

  // Door, frame, panels, handle and wreath.
  const doorX = -0.42;
  addBox(group, 1.12, 2.3, 0.12, doorX, 1.98, 2.325, materials.darkWood);
  addBox(group, 0.12, 2.42, 0.19, doorX - 0.61, 1.99, 2.3, materials.timber);
  addBox(group, 0.12, 2.42, 0.19, doorX + 0.61, 1.99, 2.3, materials.timber);
  addBox(group, 1.34, 0.12, 0.19, doorX, 3.16, 2.3, materials.timber);
  addBox(group, 0.95, 0.08, 0.08, doorX, 0.87, 2.38, materials.snowLump, { castShadow: false });
  for (const y of [2.42, 1.55]) {
    addBox(group, 0.84, 0.72, 0.035, doorX, y, 2.397, materials.wall, { castShadow: false });
    addBox(group, 0.68, 0.56, 0.04, doorX, y, 2.408, materials.darkWood, { castShadow: false });
  }
  const handle = new THREE.Mesh(new THREE.SphereGeometry(0.045, 8, 6), materials.brass);
  handle.position.set(doorX + 0.38, 1.62, 2.45);
  group.add(handle);
  const keyhole = new THREE.Mesh(new THREE.BoxGeometry(0.022, 0.075, 0.01), materials.metal);
  keyhole.position.set(doorX + 0.38, 1.49, 2.45);
  group.add(keyhole);
  addWreath(group, materials, doorX, 2.33, 2.47);

  // Front and side windows.
  const frontWindow = makeWindow(materials, 1.13, 1.14);
  frontWindow.position.set(-2.18, 2.37, 2.33);
  group.add(frontWindow);
  const frontLight = new THREE.PointLight(0xffb15e, 13.0, 4.8, 2);
  frontLight.position.set(-2.18, 2.35, 2.95);
  group.add(frontLight);
  windowLights.push(frontLight);

  const porchWindow = makeWindow(materials, 0.88, 0.98);
  porchWindow.position.set(1.42, 2.42, 2.33);
  group.add(porchWindow);
  const porchWindowLight = new THREE.PointLight(0xffb05c, 10.0, 4.0, 2);
  porchWindowLight.position.set(1.42, 2.38, 2.9);
  group.add(porchWindowLight);
  windowLights.push(porchWindowLight);

  const sideWindow = makeWindow(materials, 1.05, 1.18);
  sideWindow.position.set(3.27, 2.44, -0.18);
  sideWindow.rotation.y = Math.PI / 2;
  group.add(sideWindow);
  const sideLight = new THREE.PointLight(0xffae5a, 11.0, 4.4, 2);
  sideLight.position.set(3.9, 2.4, -0.18);
  group.add(sideLight);
  windowLights.push(sideLight);

  // Rear and left windows keep the cabin believable after the camera orbits.
  const backWindow = makeWindow(materials, 0.98, 1.08);
  backWindow.position.set(-1.55, 2.35, -2.33);
  backWindow.rotation.y = Math.PI;
  group.add(backWindow);
  const backLight = new THREE.PointLight(0xffac58, 10.0, 4.2, 2);
  backLight.position.set(-1.55, 2.3, -2.95);
  group.add(backLight);
  windowLights.push(backLight);

  const leftWindow = makeWindow(materials, 0.9, 1.05);
  leftWindow.position.set(-3.27, 2.4, -0.2);
  leftWindow.rotation.y = -Math.PI / 2;
  group.add(leftWindow);
  const leftLight = new THREE.PointLight(0xffae5a, 9.0, 4.0, 2);
  leftLight.position.set(-3.9, 2.35, -0.2);
  group.add(leftLight);
  windowLights.push(leftLight);

  // Warm light spilled onto the porch and the first snowy treads.
  const spill = new THREE.Mesh(
    new THREE.PlaneGeometry(6.2, 3.2),
    new THREE.MeshBasicMaterial({
      map: materials.textures.glow,
      color: 0xff9d4d,
      transparent: true,
      opacity: 0.28,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    })
  );
  spill.rotation.x = -Math.PI / 2;
  spill.position.set(0.2, 0.86, 3.0);
  spill.renderOrder = 2;
  group.add(spill);

  // Lantern hanging from the porch beam.
  const lantern = makeLantern(materials, glowTexture);
  lantern.position.set(0.75, 2.73, 3.55);
  group.add(lantern);
  addBox(group, 0.04, 0.32, 0.04, 0.75, 3.0, 3.55, materials.metal);
  windowLights.push(lantern.userData.light);
  const lanternGlow = lantern.userData.glow;
  const porchLight = lantern.userData.light;

  // Boots by the door.
  const boot1 = makeBoot(materials, false);
  boot1.position.set(0.23, 0.83, 2.98);
  boot1.rotation.y = 0.24;
  group.add(boot1);
  const boot2 = makeBoot(materials, true);
  boot2.position.set(0.56, 0.83, 2.93);
  boot2.rotation.y = -0.12;
  group.add(boot2);

  // Split firewood against the wall; the pile rests on the porch boards.
  const logSide = new THREE.MeshStandardMaterial({ map: materials.textures.bark, color: 0x78614d, roughness: 0.95 });
  for (let i = 0; i < 10; i++) {
    const row = i < 6 ? 0 : 1;
    const col = i < 6 ? i : i - 6;
    const log = new THREE.Mesh(
      new THREE.CylinderGeometry(0.085 + rng() * 0.03, 0.09 + rng() * 0.03, 0.78, 9),
      [logSide, materials.ring, materials.ring]
    );
    log.rotation.x = Math.PI / 2;
    log.rotation.y = (rng() - 0.5) * 0.1;
    log.position.set(-2.1 + col * 0.2, 0.86 + row * 0.17, 2.72 + (row ? 0.08 : 0));
    log.castShadow = true;
    log.receiveShadow = true;
    group.add(log);
  }
  for (let i = 0; i < 5; i++) {
    const s = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 1), materials.snowLump);
    s.position.set(-2.15 + rng() * 1.0, 1.04 + (i > 2 ? 0.12 : 0), 2.6 + rng() * 0.35);
    s.scale.set(0.09 + rng() * 0.09, 0.025, 0.06 + rng() * 0.05);
    group.add(s);
  }

  // Doormat.
  const matMaterial = new THREE.MeshStandardMaterial({ color: 0x3b2a1c, roughness: 0.98, metalness: 0 });
  addBox(group, 0.78, 0.025, 0.4, -0.4, 0.84, 3.35, matMaterial, { castShadow: false });

  // Snow drift against the foundation.
  const drift = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 2), materials.snowLump, 34);
  for (let i = 0; i < 34; i++) {
    const t = i / 33;
    const x = -3.4 + t * 6.8;
    const front = i % 3 !== 0;
    dummy.position.set(x, 0.16 + rng() * 0.1, front ? 2.55 + rng() * 0.25 : -2.5 - rng() * 0.15);
    dummy.rotation.set(0, rng() * TAU, 0);
    dummy.scale.set(0.35 + rng() * 0.38, 0.1 + rng() * 0.12, 0.2 + rng() * 0.3);
    dummy.updateMatrix();
    drift.setMatrixAt(i, dummy.matrix);
    drift.setColorAt(i, new THREE.Color(0.82 + rng() * 0.1, 0.9, 1));
  }
  drift.castShadow = true;
  drift.receiveShadow = true;
  drift.instanceMatrix.needsUpdate = true;
  group.add(drift);

  // Smoke — opacity is driven in the main loop.
  const smokeMat = new THREE.SpriteMaterial({
    map: materials.textures.mist,
    color: 0x7d8fa4,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
  });
  for (let i = 0; i < 9; i++) {
    const sprite = new THREE.Sprite(smokeMat.clone());
    sprite.position.set(1.36, 7.25 + i * 0.38, -0.62);
    sprite.scale.set(0.8 + i * 0.15, 0.55 + i * 0.11, 1);
    sprite.userData.baseY = 7.25 + i * 0.38;
    sprite.userData.phase = i * 0.75;
    sprite.userData.baseX = 1.36;
    group.add(sprite);
    smokeSprites.push(sprite);
  }

  // Icicles hang from the porch eave and the windward roof edge.
  const icicleMat = new THREE.MeshPhysicalMaterial({
    color: 0xcfe6f4,
    roughness: 0.1,
    metalness: 0,
    transparent: true,
    opacity: 0.55,
    clearcoat: 1,
    clearcoatRoughness: 0.05,
    emissive: 0x172733,
    emissiveIntensity: 0.4,
  });
  for (let i = 0; i < 20; i++) {
    const x = -2.78 + (i / 19) * 5.55 + (rng() - 0.5) * 0.13;
    const len = 0.12 + rng() * 0.4;
    const ic = new THREE.Mesh(new THREE.ConeGeometry(0.018 + rng() * 0.015, len, 5), icicleMat);
    ic.position.set(x, 3.3 - len / 2, 4.23 + (rng() - 0.5) * 0.08);
    ic.rotation.x = Math.PI + (rng() - 0.5) * 0.12;
    group.add(ic);
  }
  for (let i = 0; i < 24; i++) {
    const z = -2.65 + (i / 23) * 5.3 + (rng() - 0.5) * 0.12;
    const len = 0.1 + rng() * 0.34;
    for (const side of [-1, 1]) {
      const ic = new THREE.Mesh(new THREE.ConeGeometry(0.015 + rng() * 0.012, len, 5), icicleMat);
      ic.position.set(side * 4.02, 3.87 - len / 2, z);
      ic.rotation.x = Math.PI + (rng() - 0.5) * 0.16;
      group.add(ic);
    }
  }

  // Soft contact shadow beneath the whole building.
  const contact = new THREE.Mesh(
    new THREE.PlaneGeometry(11.5, 8.5),
    new THREE.MeshBasicMaterial({
      map: materials.textures.shadow,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
      color: 0x000000,
    })
  );
  contact.rotation.x = -Math.PI / 2;
  contact.position.set(0, 0.025, 0.5);
  contact.renderOrder = 1;
  group.add(contact);

  group.traverse((o) => {
    if (o.isMesh) {
      setShadow(o, o.castShadow, true);
    }
  });

  return {
    group,
    windowLights,
    porchLight,
    lanternGlow,
    smokeSprites,
    foundationTop: 0.55,
    porchFloor: 0.83,
  };
}
