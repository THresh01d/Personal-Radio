import * as THREE from 'three';
import { createMaterials } from './winterVillage/materials.js';
import { createEnvironment } from './winterVillage/environment.js';
import { createFirTree } from './winterVillage/tree.js';
import { createCabin } from './winterVillage/cabin.js';
import { batchStaticMeshes, distributeBufferAttributes } from './winterVillage/batch.js';
import { terrainHeight } from './winterVillage/util.js';

// 保留原版模型与取景坐标；降档只减少同一缓冲区的可见实例和点。
const quality = [
  { points: [1040, 100, 500, 80, 150], detail: .35 },
  { points: [8500, 650, 1120, 240, 180], detail: .54 },
  { points: [20000, 650, 1920, 500, 240], detail: .76 },
  { points: [Infinity, Infinity, Infinity, Infinity, Infinity], detail: 1 },
];
export const christmasQuality = budget => quality[budget < 4000 ? 0 : budget < 6000 ? 1 : budget < 11000 ? 2 : 3];
export const christmasPulse = age => Math.max(0, 1 - Math.max(0, age) / .30) ** 2;

export function createChristmasLayer() {
  const root = new THREE.Group();
  root.name = 'christmas-night';
  const background = new THREE.Color(0x040a11);
  const fog = new THREE.FogExp2(0x0c1a28, .036);
  const moon = new THREE.DirectionalLight(0xc5daf4, 2.85);
  moon.position.set(-9.5, 16.5, 9.5);
  moon.target.position.set(0, 2.8, -2.8);
  moon.castShadow = true;
  moon.shadow.mapSize.set(512, 512);
  Object.assign(moon.shadow.camera, { near: .5, far: 42, left: -16, right: 16, top: 17, bottom: -13 });
  moon.shadow.bias = -.00035;
  moon.shadow.normalBias = .035;
  moon.shadow.radius = 2;
  const rim = new THREE.DirectionalLight(0x6f9fce, .7);
  rim.position.set(13, 7, -16);
  rim.target.position.set(0, 3, 0);
  root.add(new THREE.HemisphereLight(0x8faed2, 0x101a26, .78),
    new THREE.AmbientLight(0x203349, .34), moon, moon.target, rim, rim.target);

  const materials = createMaterials();
  const tree = createFirTree(materials, materials.textures.glow);
  tree.group.position.set(-2.55, terrainHeight(-2.55, -2.45) + .01, -2.45);
  const cabin = createCabin(materials, materials.textures.glow);
  cabin.group.name = 'winter-cabin';
  cabin.group.position.set(4.95, terrainHeight(4.95, -3.8) - .02, -3.8);
  cabin.group.rotation.y = -.18;
  const doorstep = cabin.group.localToWorld(new THREE.Vector3(-.4, 0, 5.63));
  const environment = createEnvironment(materials, doorstep);
  environment.creek.uniforms.uReflectionTree.value.copy(tree.group.position);
  for (const model of [tree, cabin, environment]) batchStaticMeshes(model.group);
  root.add(tree.group, cabin.group, environment.group);

  function practical(color, intensity, distance, x, y, z) {
    const light = new THREE.PointLight(color, intensity, distance, 2);
    light.position.set(x, y, z);
    root.add(light);
    return light;
  }
  practical(0x9bc9ff, 9, 13, -2.55, 4.2, -2.35);
  practical(0x7caee8, 5, 8, -2.55, 1.3, -1.9);
  const porch = practical(0xffa25c, 26, 11, 4.15, 1.55, -.4);
  const spot = new THREE.SpotLight(0xffb069, 34, 12, Math.PI / 4.1, .7, 2);
  spot.position.set(4.2, 3.1, -1.05);
  // 从门前第一块踏石向台阶收一点，保留门廊的主光区。
  spot.target.position.copy(environment.firstTreadStone).lerp(new THREE.Vector3(4.25, .05, .05), .3);
  root.add(spot, spot.target);

  const plane = new THREE.PlaneGeometry(1, 1);
  function groundLight(x, z, sx, sz, opacity, color = 0, spill = false) {
    const mesh = new THREE.Mesh(plane, new THREE.MeshBasicMaterial({
      map: spill ? materials.textures.glow : materials.textures.shadow,
      color, transparent: true, opacity, depthWrite: false,
      blending: spill ? THREE.AdditiveBlending : THREE.NormalBlending,
    }));
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set(x, terrainHeight(x, z) + (spill ? .035 : .028), z);
    mesh.scale.set(sx, sz, 1);
    mesh.renderOrder = spill ? 2 : 1;
    root.add(mesh);
  }
  groundLight(-2.55, -2.45, 6.8, 6.2, .55);
  groundLight(-3.75, 5.2, 4.4, 3.8, .32);
  groundLight(5.4, 3.05, 4.4, 3.6, .28);
  groundLight(4.15, .4, 7.2, 5.8, .105, 0xff9f50, true);
  groundLight(-2.55, -2.45, 8.4, 7.2, .05, 0x86bdf5, true);

  const { snowfall, sparkles, creek } = environment;
  const points = [tree.corePoints, tree.glowPoints, snowfall.points, sparkles.points, environment.skyStars];
  const pointCounts = points.map(point => {
    const attributes = point.geometry.attributes;
    distributeBufferAttributes(Object.values(attributes));
    return attributes.position.count;
  });
  const instanceCounts = tree.detailMeshes.map(mesh => {
    mesh.computeBoundingSphere();
    distributeBufferAttributes([mesh.instanceMatrix, mesh.instanceColor].filter(Boolean));
    return mesh.count;
  });
  const scaled = points.map(point => point.material.uniforms?.uScale).filter(Boolean);
  const doorLights = [porch, spot, ...cabin.windowLights];
  const doorBases = doorLights.map(light => light.intensity);
  const glassBase = materials.warmGlass.color.clone();
  const starBase = tree.starMat.emissiveIntensity;
  let viewportScale = 1, tier;
  let age = .30, previousBeat = 0, time = 0, low = 0, mid = 0, high = 0;
  const response = { time: 0, pulse: 0, silver: 0, warm: 0, crystal: 0 };

  function applyResponse() {
    response.time = time;
    response.pulse = christmasPulse(age);
    response.silver = low * .065 + response.pulse * .24;
    response.warm = mid * .13;
    response.crystal = high * .32;
    tree.corePoints.material.uniforms.uResponse.value = response.silver;
    tree.glowPoints.material.uniforms.uResponse.value = response.silver;
    creek.uniforms.uBulbResponse.value = response.silver;
    snowfall.uniforms.uTime.value = time;
    snowfall.uniforms.uTreble.value = response.crystal;
    sparkles.uniforms.uGlow.value = 1 + response.crystal;
    for (let i = 0; i < doorLights.length; i++) doorLights[i].intensity = doorBases[i] * (1 + response.warm);
    materials.warmGlass.color.copy(glassBase).multiplyScalar(1 + response.warm * .65);
    tree.starMat.emissiveIntensity = starBase * (1 + response.warm * .7);
  }
  function resetResponse() {
    age = .30;
    previousBeat = low = mid = high = 0;
    applyResponse();
  }
  return {
    root, background, fog, shadow: moon.shadow, response,
    resize(height, ratio, budget) {
      viewportScale = height * ratio;
      const next = christmasQuality(budget);
      if (next === tier) return;
      tier = next;
      points.forEach((point, i) => point.geometry.setDrawRange(0, Math.min(pointCounts[i], tier.points[i])));
      tree.detailMeshes.forEach((mesh, i) => { mesh.count = Math.ceil(instanceCounts[i] * tier.detail); });
    },
    resetResponse,
    update(frame, camera, seconds) {
      const scale = viewportScale / (2 * Math.tan(camera.fov * Math.PI / 360));
      for (const uniform of scaled) uniform.value = scale;
      if (!frame.reactive) {
        if (previousBeat || low || mid || high || age < .30) resetResponse();
        return;
      }
      const elapsed = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
      const dt = Math.min(.10, elapsed);
      time += dt;
      const beat = frame.beat || 0;
      age = beat > .65 && previousBeat <= .65 ? 0 : Math.min(.30, age + elapsed);
      previousBeat = beat;
      const blend = 1 - Math.exp(-dt * 7);
      low += (frame.low - low) * blend;
      mid += (frame.mid - mid) * blend;
      high += (frame.high - high) * blend;
      applyResponse();
    },
  };
}
