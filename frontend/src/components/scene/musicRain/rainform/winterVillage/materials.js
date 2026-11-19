import * as THREE from 'three';
import {
  makeWoodTextures,
  makeBarkTexture,
  makeStoneTextures,
  makeSnowBumpTexture,
  makeSnowColorTexture,
  makeGlowTexture,
  makeMistTexture,
  makeShadowTexture,
  makeWindowTexture,
  makeRingTexture,
  makeShingleTexture,
  makePineSilhouetteTextures,
} from './textures.js';

export function createMaterials() {
  const wood = makeWoodTextures();
  const stone = makeStoneTextures();
  const shingle = makeShingleTexture();
  const bark = makeBarkTexture();
  const snowBump = makeSnowBumpTexture();
  const snowColor = makeSnowColorTexture();
  const glow = makeGlowTexture();
  const mist = makeMistTexture();
  const shadow = makeShadowTexture();
  const windowTex = makeWindowTexture();
  const ring = makeRingTexture();
  const pineSilhouettes = makePineSilhouetteTextures();

  const snow = new THREE.MeshLambertMaterial({
    color: 0xe9f2fa,
    map: snowColor,
    bumpMap: snowBump,
    bumpScale: 0.13,
    emissive: 0x07111b,
    emissiveIntensity: 0.7,
  });

  const snowLump = new THREE.MeshLambertMaterial({
    color: 0xe4eef9,
    map: snowColor,
    bumpMap: snowBump,
    bumpScale: 0.09,
    emissive: 0x08131f,
    emissiveIntensity: 0.58,
    flatShading: true,
  });

  const wall = new THREE.MeshStandardMaterial({
    map: wood.map,
    bumpMap: wood.bump,
    bumpScale: 0.045,
    color: 0x9b8068,
    roughness: 0.88,
    metalness: 0.0,
  });

  const timber = new THREE.MeshStandardMaterial({
    map: wood.map,
    bumpMap: wood.bump,
    bumpScale: 0.055,
    color: 0x4a3526,
    roughness: 0.82,
    metalness: 0.0,
  });

  const darkWood = new THREE.MeshStandardMaterial({
    map: wood.map,
    bumpMap: wood.bump,
    bumpScale: 0.05,
    color: 0x34251c,
    roughness: 0.72,
    metalness: 0.02,
  });

  const barkMat = new THREE.MeshStandardMaterial({
    map: bark,
    bumpMap: bark,
    bumpScale: 0.06,
    color: 0x8a7565,
    roughness: 0.96,
    metalness: 0.0,
  });

  const stoneMat = new THREE.MeshStandardMaterial({
    map: stone.map,
    bumpMap: stone.bump,
    bumpScale: 0.075,
    color: 0xaeb2b0,
    roughness: 0.97,
    metalness: 0.0,
  });

  const stoneDark = new THREE.MeshStandardMaterial({
    map: stone.map,
    bumpMap: stone.bump,
    bumpScale: 0.08,
    color: 0x737877,
    roughness: 0.98,
    metalness: 0.0,
  });

  const roofMat = new THREE.MeshStandardMaterial({
    map: shingle,
    bumpMap: shingle,
    bumpScale: 0.05,
    color: 0x90979a,
    roughness: 0.9,
    metalness: 0.02,
  });

  const shingleMat = new THREE.MeshStandardMaterial({
    map: shingle,
    bumpMap: shingle,
    bumpScale: 0.05,
    color: 0x8a9092,
    roughness: 0.88,
    metalness: 0.03,
  });

  const needleMat = new THREE.MeshStandardMaterial({
    color: 0x183230,
    roughness: 0.92,
    metalness: 0,
    side: THREE.DoubleSide,
    flatShading: true,
  });

  const branchMat = new THREE.MeshStandardMaterial({
    map: bark,
    color: 0x4b382b,
    roughness: 0.94,
    metalness: 0,
  });

  const metal = new THREE.MeshStandardMaterial({
    color: 0x242a2e,
    roughness: 0.32,
    metalness: 0.86,
  });

  const brass = new THREE.MeshStandardMaterial({
    color: 0x9a7142,
    roughness: 0.3,
    metalness: 0.88,
  });

  const glass = new THREE.MeshPhysicalMaterial({
    color: 0xb5d5e4,
    roughness: 0.06,
    metalness: 0.0,
    transparent: true,
    opacity: 0.24,
    transmission: 0.0,
    side: THREE.DoubleSide,
    depthWrite: false,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
  });

  const warmGlass = new THREE.MeshBasicMaterial({
    map: windowTex,
    color: 0xffd7a0,
    toneMapped: true,
  });

  const leather = new THREE.MeshStandardMaterial({
    color: 0x2a1a14,
    roughness: 0.62,
    metalness: 0.02,
  });

  const candleWax = new THREE.MeshStandardMaterial({
    color: 0xe6d9bd,
    roughness: 0.72,
    metalness: 0,
    emissive: 0x4a2d0e,
    emissiveIntensity: 0.4,
  });

  const flame = new THREE.MeshBasicMaterial({
    color: 0xffd08a,
    transparent: true,
    opacity: 0.94,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  const ringMat = new THREE.MeshStandardMaterial({
    map: ring,
    color: 0xa37a50,
    roughness: 0.9,
    metalness: 0,
  });

  const rope = new THREE.MeshStandardMaterial({ color: 0x6f5c43, roughness: 0.9 });
  const redBerry = new THREE.MeshStandardMaterial({ color: 0x6f1616, roughness: 0.48, metalness: 0.05 });
  const wreathGreen = new THREE.MeshStandardMaterial({ color: 0x17332b, roughness: 0.9 });
  const fabric = new THREE.MeshStandardMaterial({ color: 0x6b3b27, roughness: 0.96, side: THREE.DoubleSide });

  return {
    textures: { wood, stone, shingle, bark, snowBump, snowColor, glow, mist, shadow, windowTex, ring, pineSilhouettes },
    snow,
    snowLump,
    wall,
    timber,
    darkWood,
    bark: barkMat,
    stone: stoneMat,
    stoneDark,
    roof: roofMat,
    shingle: shingleMat,
    needle: needleMat,
    branch: branchMat,
    metal,
    brass,
    glass,
    warmGlass,
    leather,
    candleWax,
    flame,
    ring: ringMat,
    rope,
    redBerry,
    wreathGreen,
    fabric,
  };
}
