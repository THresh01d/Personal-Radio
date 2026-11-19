import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

// 仅合并静止、不透明、材质与顶点布局完全一致的部件，玻璃和原本的实例批次保持独立。
export function batchStaticMeshes(group) {
  const byLayout = new Map();
  const owners = new Map();
  group.updateMatrixWorld(true);
  const fromWorld = new THREE.Matrix4().copy(group.matrixWorld).invert();
  group.traverse(object => {
    if (!object.geometry) return;
    const g = object.geometry;
    owners.set(g, (owners.get(g) ?? 0) + 1);
    const m = object.material;
    if (!object.isMesh || object.isInstancedMesh || object.children.length || !object.visible
      || !m || Array.isArray(m) || m.transparent || m.isShaderMaterial
      || g.drawRange.start !== 0 || Number.isFinite(g.drawRange.count)) return;
    const attributes = Object.entries(g.attributes).sort(([a], [b]) => a.localeCompare(b));
    if (attributes.some(([, attribute]) => attribute.isInterleavedBufferAttribute)) return;
    const layout = attributes.map(([name, attribute]) =>
      [name, attribute.array.constructor.name, attribute.itemSize, attribute.normalized]);
    const key = JSON.stringify([m.id, !!g.index, object.castShadow, object.receiveShadow, object.renderOrder, layout]);
    if (!byLayout.has(key)) byLayout.set(key, []);
    byLayout.get(key).push(object);
  });
  for (const list of byLayout.values()) {
    if (list.length < 3) continue;
    const copies = list.map(part => part.geometry.clone().applyMatrix4(
      new THREE.Matrix4().multiplyMatrices(fromWorld, part.matrixWorld)));
    const merged = mergeGeometries(copies);
    for (const copy of copies) copy.dispose();
    if (!merged) continue;
    const batch = new THREE.Mesh(merged, list[0].material);
    batch.name = `冬季静态合批 × ${list.length}`;
    batch.castShadow = list[0].castShadow;
    batch.receiveShadow = list[0].receiveShadow;
    batch.renderOrder = list[0].renderOrder;
    group.add(batch);
    for (const part of list) {
      part.removeFromParent();
      const left = owners.get(part.geometry) - 1;
      owners.set(part.geometry, left);
      if (!left) part.geometry.dispose();
    }
  }
}

// 黄金比例顺序让每档预算均匀选到树冠各处。保留同一组 GPU attribute，切档只改绘制数量。
export function distributeBufferAttributes(attributes) {
  const count = attributes[0].count;
  if (count < 2) return;
  const coprime = (a, b) => {
    while (b) [a, b] = [b, a % b];
    return a === 1;
  };
  let step = Math.max(1, Math.round(count * .61803398875));
  while (!coprime(count, step)) step++;
  for (const attribute of attributes) {
    const { array, itemSize } = attribute;
    const original = array.slice();
    for (let i = 0, source = 0; i < count; i++, source = (source + step) % count) {
      for (let item = 0; item < itemSize; item++) array[i * itemSize + item] = original[source * itemSize + item];
    }
    attribute.needsUpdate = true;
  }
}
