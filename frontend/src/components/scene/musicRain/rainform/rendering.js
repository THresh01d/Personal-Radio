// Required Notice: Rainform / 数据成雨 © 2026 afterimage — https://rainform.pages.dev/
// 改编原作渲染核心；许可证、来源和修改范围见本目录 README.md。
import * as THREE from 'three';
import { createConfig } from './config.js';
import { createShaders } from './shaders.js';
import { createSnowLayer } from './snow.js';
import { createChristmasLayer } from './christmas.js';
export function createRainformCore(canvas) {
    const { WATER_LEVEL, RAIN_LAYOUT_SEED, RAIN_CLUSTER_SEED, AMBIENT_RAIN_SEED, DOWNPOUR_RAIN_SEED, RAIN_ROLE, WORLD, RAIN_DEPTH, AXIS_CONFIG, RIPPLE_FIELD, PALETTE, TUNING } = createConfig();
    const VISUAL_RAINFALL_REFERENCE = 10;
    const rainCeilingValue = 12.8;
    const rainCeilingY = WATER_LEVEL + AXIS_CONFIG.worldHeight;
    // 固定容量、均匀分布的雨串只建一次，频谱更新不会重新随机布局或重建几何。
    const activeRainfall = Array(25).fill(10);
    const rainfallMax = 10;
    const peakWaterfallRanges = [[0, 24]];
    const state = { selectedActive: false, pointerActive: false, selectedHour: 0, burst: 0 };
    const chainFocus = () => 0;
    const isPhoneLandscapeViewport = () => false;
    let pixelRatio = 1;
    let viewportHeight = 1;
    let particleBudget = 15360;
    const rainPlotBounds = new THREE.Vector4(0, 1, 0, 1);
    const rainPlotFeather = new THREE.Vector3(0.002, 0, 0.002);
    const rainViewport = new THREE.Vector2(1, 1);
    const rainPlotClip = { value: 0 };
    const shaders = createShaders();
    // 沿用原作默认黑底银雨主题，不读取原站本地设置或创建第二套音频。
    PALETTE.pearlBright.setHex(0xfafbff);
    PALETTE.pearlMid.setHex(0xc2cbdb);
    PALETTE.pearlDark.setHex(0x000000);
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: 'high-performance' });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x000000, 0.022);
    const rainFog = scene.fog;
    const rainToneMapping = renderer.toneMapping;
    const rainExposure = renderer.toneMappingExposure;
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 110);
    camera.position.set(0, 3.6, 16.6);
    camera.lookAt(0, 3.6, 0.26);
    const worldGroup = new THREE.Group();
    worldGroup.name = 'music-rainform';
    scene.add(worldGroup);
    const waterPlane = createWaterPlane();
    const poolFloor = createPoolFloor();
    const mistBand = createMistBand();
    waterPlane.material.uniforms.uRainLut.value = mistBand.lut;
    waterPlane.material.uniforms.uLutBounds.value.set(mistBand.lutXMin, mistBand.lutSpan);
    const rainChains = createRainChainSystem(2000, 800, 1400);
    const rippleSystem = createRippleFieldSystem(renderer);
    worldGroup.add(poolFloor, waterPlane, mistBand.mesh, rainChains.lines, rainChains.points);
    const data = rainChains.data;
    const base = Object.fromEntries(['top', 'length', 'alpha', 'speed'].map(key => [key, data[key].slice()]));
    const impactRandom = createSeededRandom(0x4c8f6e27);
    let rippleCredit = 0;
    let lastTime = 0;
    let disposed = false;
    let chainLimit = data.count;
    const pauseHeadY = data.headY.slice();
    let wasPaused = false;
    let weather = 'rain';
    let snow = null;
    let snowTime = 0;
    let christmas = null;
    const melodyLight = new Float32Array(256);
    // 原作撞击粒子层默认关闭，仍保留真实落水触发的 GPU 波动高度场。
    function queueRainImpact() { return 0; }
    function tryEmitImpactRipple(_system, ripples, x, z, strength, _storm, _role, elapsed) {
        if (rippleCredit < 1 || impactRandom() > 0.06 + strength * 0.16)
            return;
        rippleCredit--;
        addRipple(ripples, x, z, 0.13 + strength * 0.4, false, elapsed);
    }
    const impactPearls = null;
    return {
        camera,
        setWeather(requested) {
            const mode = requested === 'snow' || requested === 'christmas' ? requested : 'rain';
            if (disposed || mode === weather) return;
            const wasChristmas = weather === 'christmas';
            weather = mode;
            melodyLight.fill(0);
            if (mode === 'snow' && !snow) {
                snow = createSnowLayer(WORLD, mistBand.lut);
                snow.resize(viewportHeight, pixelRatio, particleBudget);
                worldGroup.add(snow.points);
            }
            if (snow) snow.points.visible = mode === 'snow';
            const isRain = mode === 'rain';
            poolFloor.visible = waterPlane.visible = mistBand.mesh.visible = isRain;
            rainChains.lines.visible = rainChains.points.visible = isRain;
            const isChristmas = mode === 'christmas';
            if (isChristmas && !christmas) {
                christmas = createChristmasLayer();
                christmas.resize(viewportHeight, pixelRatio, particleBudget);
                scene.add(christmas.root);
            }
            if (christmas) christmas.root.visible = isChristmas;
            worldGroup.visible = !isChristmas;
            if (isChristmas || wasChristmas) {
                scene.background = isChristmas ? christmas.background : null;
                scene.fog = isChristmas ? christmas.fog : rainFog;
                renderer.toneMapping = isChristmas ? THREE.ACESFilmicToneMapping : rainToneMapping;
                renderer.toneMappingExposure = isChristmas ? 1.22 : rainExposure;
                // 与原版同样只烘焙一次月影。拖动、播放和切回圣诞都不刷新阴影。
                renderer.shadowMap.enabled = isChristmas;
                renderer.shadowMap.type = THREE.PCFShadowMap;
                renderer.shadowMap.autoUpdate = !isChristmas;
                renderer.shadowMap.needsUpdate = isChristmas && !christmas.shadow.map;
                if (isChristmas) christmas.resetResponse();
            }
        },
        resize(width, height, ratio, budget) {
            pixelRatio = ratio;
            viewportHeight = height;
            particleBudget = budget;
            if (snow) snow.resize(height, ratio, budget);
            renderer.setPixelRatio(ratio);
            renderer.setSize(width, height, false);
            rainViewport.set(width * ratio, height * ratio);
            rainChains.points.material.uniforms.uPixelRatio.value = ratio;
            camera.aspect = width / height;
            camera.updateProjectionMatrix();
            // 竖屏仅压缩横向世界坐标，低中高频仍完整覆盖画面，不裁掉两端频段。
            worldGroup.scale.x = Math.min(1, camera.aspect / 1.65);
            if (christmas) christmas.resize(height, ratio, budget);
            chainLimit = budget < 6000 ? 2000 : budget < 11000 ? 2800 : data.count;
            const pearls = chainLimit < data.count ? data.pearlStart[chainLimit] : data.pearlCount;
            const lines = chainLimit < data.count ? data.lineStart[chainLimit] : data.lineTotal;
            rainChains.points.geometry.setDrawRange(0, pearls);
            rainChains.lines.geometry.setDrawRange(0, lines * 2);
        },
        render(frame, pitch = null) {
            if (disposed)
                return;
            const dt = Math.max(0, Math.min(0.1, frame.time - lastTime));
            lastTime = frame.time;
            state.burst = frame.beat;
            const pauseProgress = frame.pauseProgress ?? null;
            if (weather === 'christmas') {
                christmas.update(frame, camera, frame.deltaSeconds ?? dt);
                renderer.render(scene, camera);
                return;
            }
            if (weather === 'snow') {
                renderSnow(frame, dt, pitch);
                return;
            }
            const paused = pauseProgress !== null;
            if (paused && !wasPaused) pauseHeadY.set(data.headY);
            // 恢复原来的雨串位置，播放时继续走原算法，不重排雨串或改变材质。
            if (!paused && wasPaused) data.headY.set(pauseHeadY);
            wasPaused = paused;
            rainChains.points.visible = !paused || pauseProgress < 1;
            rainChains.lines.visible = rainChains.points.visible;
            rippleCredit = Math.min(12, rippleCredit + dt * 40);
            // 沿频率位置连续插值；高度、密度与下落速度共用真实频谱，不保留固定雨量山形。
            for (let h = 0; h <= 24; h++)
                activeRainfall[h] = sampleBand(h) * 10;
            for (let i = 0; i < chainLimit; i++) {
                if (paused) continue;
                const value = sampleBand(data.hour[i]);
                const strength = Math.max(0, value);
                const heightScale = (0.95 + Math.pow(strength, 0.92) * 5.8) / 6.75;
                const oldHeight = Math.max(0.001, data.top[i] - WATER_LEVEL);
                data.top[i] = WATER_LEVEL + (base.top[i] - WATER_LEVEL) * heightScale;
                data.headY[i] = WATER_LEVEL + (data.headY[i] - WATER_LEVEL) * (data.top[i] - WATER_LEVEL) / oldHeight;
                data.length[i] = base.length[i] * (data.role[i] === RAIN_ROLE.AMBIENT ? heightScale : Math.sqrt(heightScale));
                data.strength[i] = strength;
                data.presence[i] = strength > 0.008 ? 1 : 0;
                data.alpha[i] = base.alpha[i] * Math.pow(strength, 0.72);
                data.speed[i] = base.speed[i] * (0.48 + strength * 0.72 + frame.beat * 0.18);
                assignWaterfallEnvelope(data, i, data.hour[i]);
            }
            const packed = mistBand.lut.image.data;
            for (let j = 0; j < 256; j++) {
                const intensity = Math.pow(sampleBand(j / 255 * 24), 1.12);
                packed[j * 4] = packed[j * 4 + 1] = packed[j * 4 + 2] = intensity;
            }
            mistBand.lut.needsUpdate = true;
            mistBand.material.uniforms.uTime.value = frame.time;
            // 限定原算法的工作集，性能降档时仍保留均匀分布的主雨链。
            const savedCount = data.count, savedPearls = data.pearlCount, savedLines = data.lineTotal;
            data.count = chainLimit;
            data.pearlCount = chainLimit < savedCount ? data.pearlStart[chainLimit] : savedPearls;
            data.lineTotal = chainLimit < savedCount ? data.lineStart[chainLimit] : savedLines;
            updateRainChains(rainChains, frame.time, dt, pauseProgress);
            data.count = savedCount;
            data.pearlCount = savedPearls;
            data.lineTotal = savedLines;
            if (frame.reactive && frame.ripple)
                addRipple(rippleSystem, hourToX(frame.ripple.band / 47 * 24), 0.3, frame.ripple.strength, true, frame.time);
            if (dt > 0)
                updateRippleField(rippleSystem, renderer, frame.time);
            waterPlane.material.uniforms.uTime.value = frame.time;
            renderer.render(scene, camera);
            function sampleBand(hour) {
                const position = clamp(hour / 24 * 47, 0, 47);
                const left = Math.floor(position), right = Math.min(47, left + 1);
                return lerp(frame.bands[left], frame.bands[right], position - left);
            }
        },
        dispose() {
            if (disposed)
                return;
            disposed = true;
            christmas?.shadow.dispose();
            disposeObject3D(scene);
            disposeObject3D(rippleSystem.simScene);
            disposeObject3D(rippleSystem.dropScene);
            rippleSystem.rtA.dispose();
            rippleSystem.rtB.dispose();
            scene.clear();
            renderer.dispose();
        },
    };
    function renderSnow(frame, dt, pitch) {
        const active = frame.reactive && frame.pauseProgress == null;
        if (active) snowTime += dt;
        snow.update(snowTime);

        const packed = mistBand.lut.image.data;
        const hasPitch = active && Number.isFinite(pitch) && pitch > 0;
        const midi = hasPitch ? 69 + 12 * Math.log2(pitch / 440) : 0;
        const center = .15 + .70 * clamp((midi - 63) / 21, 0, 1);
        const attack = 1 - Math.exp(-dt * 24);
        const release = 1 - Math.exp(-dt * 7.5);
        for (let j = 0; j < 256; j++) {
            const x = j / 255;
            const distance = (x - center) / .085;
            const target = hasPitch ? Math.exp(-distance * distance) : 0;
            // 只提亮音高附近的一小段雪，旧音的局部亮度平滑回落。
            const previous = melodyLight[j];
            melodyLight[j] = active ? lerp(previous, target, target > previous ? attack : release) : 0;
            packed[j * 4 + 1] = melodyLight[j];
        }
        mistBand.lut.needsUpdate = true;
        renderer.render(scene, camera);
    }
    function createRainChainSystem(baseCount, ambientCount = 0, downpourCount = 0) {
        const count = baseCount + ambientCount + downpourCount;
        const chain = {
            count,
            baseCount,
            ambientCount,
            downpourCount,
            role: new Uint8Array(count),
            style: new Uint8Array(count),
            resetSeed: new Uint32Array(count),
            seedIndex: new Uint32Array(count),
            hour: new Float32Array(count),
            strength: new Float32Array(count),
            presence: new Float32Array(count),
            stormWeight: new Float32Array(count),
            waterfallTop: new Float32Array(count),
            waterfallBlendTop: new Float32Array(count),
            waterfallFeather: new Float32Array(count),
            baseX: new Float32Array(count),
            z: new Float32Array(count),
            pathX: new Float32Array(count),
            pathZ: new Float32Array(count),
            top: new Float32Array(count),
            length: new Float32Array(count),
            headY: new Float32Array(count),
            speed: new Float32Array(count),
            near: new Float32Array(count),
            curtainLayer: new Uint8Array(count),
            driftX: new Float32Array(count),
            driftZ: new Float32Array(count),
            phase: new Float32Array(count),
            windSpeed: new Float32Array(count),
            alpha: new Float32Array(count),
            pearlStart: new Uint32Array(count),
            strandPearlCount: new Uint8Array(count),
            lineStart: new Uint32Array(count),
            lineCount: new Uint8Array(count),
            respawnCycle: new Uint32Array(count)
        };
        const random = createSeededRandom(RAIN_LAYOUT_SEED);
        const hourSampler = createHourSampler(TUNING.rain.baseWeight, TUNING.rain.exponent, random, true);
        const ambientRandom = createSeededRandom(AMBIENT_RAIN_SEED);
        const ambientHourSampler = () => randomRange(0.04, 23.96, ambientRandom);
        const downpourRandom = createSeededRandom(DOWNPOUR_RAIN_SEED);
        const downpourHourSampler = createIntegratedDownpourHourSampler(downpourRandom);
        let pearlCount = 0;
        let lineCount = 0;
        for (let i = 0; i < baseCount; i += 1) {
            const hour = hourSampler();
            const strength = sampleRainStrength(hour);
            const visibility = rainVisibilityResponse(strength);
            const presence = sampleRainPresence(hour);
            const heightStrength = sampleRainHeightStrength(hour);
            const near = sampleNearness(random);
            const clusterHeight = lerp(0.88, 1.12, valueNoise(hour * 1.55, RAIN_CLUSTER_SEED ^ 0x51f2a93d));
            const strandScatter = randomRange(-0.32 - strength * 0.34, 0.38 + strength * 0.68, random);
            const top = Math.min(rainCeilingY, WATER_LEVEL + clamp(0.95
                + Math.pow(heightStrength, 0.92) * 5.8 * clusterHeight
                + strandScatter, 0.76, 8.8));
            const layerPick = random();
            const curtainLayer = layerPick < 0.5 ? 0 : layerPick < 0.85 ? 1 : 2;
            const columnHeight = Math.max(0.8, top - WATER_LEVEL);
            const baseLength = randomRange(0.78, 1.14, random)
                + strength * randomRange(0.62, 1.08, random)
                + near * randomRange(0.12, 0.32, random);
            const length = curtainLayer === 1
                ? Math.max(0.42, Math.min(baseLength * randomRange(0.64, 0.9, random), columnHeight * randomRange(0.24, 0.42, random)))
                : baseLength;
            const beads = Math.round(lerp(20, 34, Math.pow(strength, 0.55))
                + near * randomRange(0, 4, random)
                + (curtainLayer === 1 ? 1 : 0));
            chain.hour[i] = hour;
            assignWaterfallEnvelope(chain, i, hour);
            chain.role[i] = RAIN_ROLE.BASE;
            chain.resetSeed[i] = RAIN_LAYOUT_SEED;
            chain.seedIndex[i] = i;
            chain.strength[i] = strength;
            chain.presence[i] = presence;
            chain.near[i] = near;
            chain.top[i] = top;
            chain.length[i] = length;
            chain.curtainLayer[i] = curtainLayer;
            chain.pearlStart[i] = pearlCount;
            chain.strandPearlCount[i] = clamp(beads, 16, 40);
            chain.lineStart[i] = lineCount;
            chain.lineCount[i] = chain.strandPearlCount[i] - 1;
            chain.speed[i] = randomRange(1.5, 2.45, random)
                + strength * randomRange(1.15, 2.55, random)
                + near * 0.42;
            chain.driftX[i] = randomRange(0.004, 0.014, random) + near * randomRange(0.003, 0.009, random);
            chain.driftZ[i] = randomRange(0.003, 0.01, random) + near * randomRange(0.002, 0.006, random);
            chain.phase[i] = random() * Math.PI * 2;
            chain.windSpeed[i] = randomRange(0.22, 0.58, random);
            chain.alpha[i] = randomRange(0.54, 0.96, random)
                * (0.22 + visibility * 1.18)
                * (0.76 + near * 0.72)
                * (curtainLayer === 1 ? 1.12 : 1)
                * presence;
            resetChainPosition(chain, i, true);
            pearlCount += chain.strandPearlCount[i];
            lineCount += chain.lineCount[i];
        }
        for (let localIndex = 0; localIndex < ambientCount; localIndex += 1) {
            const i = baseCount + localIndex;
            const hour = ambientHourSampler();
            const strength = sampleRainStrength(hour);
            const visibility = rainVisibilityResponse(strength);
            const presence = sampleRainPresence(hour);
            const near = sampleNearness(ambientRandom);
            const beads = Math.round(randomRange(28, 38, ambientRandom));
            chain.hour[i] = hour;
            assignWaterfallEnvelope(chain, i, hour);
            chain.role[i] = RAIN_ROLE.AMBIENT;
            chain.resetSeed[i] = AMBIENT_RAIN_SEED;
            chain.seedIndex[i] = localIndex;
            chain.strength[i] = strength;
            chain.presence[i] = presence;
            chain.near[i] = near;
            chain.top[i] = rainCeilingY;
            chain.length[i] = rainCeilingY - WATER_LEVEL + randomRange(0.12, 0.3, ambientRandom);
            chain.curtainLayer[i] = 2;
            chain.pearlStart[i] = pearlCount;
            chain.strandPearlCount[i] = beads;
            chain.lineStart[i] = lineCount;
            chain.lineCount[i] = beads - 1;
            chain.speed[i] = 0;
            chain.driftX[i] = randomRange(0.002, 0.008, ambientRandom);
            chain.driftZ[i] = randomRange(0.002, 0.006, ambientRandom);
            chain.phase[i] = ambientRandom() * Math.PI * 2;
            chain.windSpeed[i] = randomRange(0.18, 0.46, ambientRandom);
            chain.alpha[i] = randomRange(0.08, 0.2, ambientRandom)
                * (0.08 + visibility * 0.92)
                * presence;
            resetChainPosition(chain, i, true);
            pearlCount += beads;
            lineCount += beads - 1;
        }
        for (let localIndex = 0; localIndex < downpourCount; localIndex += 1) {
            const i = baseCount + ambientCount + localIndex;
            const hour = downpourHourSampler();
            const strength = sampleRainStrength(hour);
            const presence = sampleRainPresence(hour);
            const heightStrength = sampleRainHeightStrength(hour);
            const near = sampleNearness(downpourRandom);
            const clusterHeight = lerp(0.9, 1.12, valueNoise(hour * 1.55, RAIN_CLUSTER_SEED ^ 0x51f2a93d));
            const dataHeight = clamp(0.95
                + Math.pow(heightStrength, 0.92) * 5.8 * clusterHeight
                + randomRange(-0.22, 0.62, downpourRandom), 1.2, 8.8);
            const fullHeight = downpourRandom() < 0.45;
            const lowerRatio = downpourRandom() < 0.7
                ? randomRange(0.08, 0.38, downpourRandom)
                : randomRange(0.38, 0.58, downpourRandom);
            const topHeight = fullHeight
                ? dataHeight + randomRange(-0.12, 0.48, downpourRandom)
                : Math.max(0.82, dataHeight * lowerRatio + randomRange(-0.08, 0.24, downpourRandom));
            const stylePick = downpourRandom();
            const style = stylePick < 0.7 ? 0 : stylePick < 0.94 ? 1 : 2;
            const beads = Math.round(style === 0
                ? randomRange(13, 19, downpourRandom)
                : style === 1 ? randomRange(15, 21, downpourRandom) : randomRange(16, 22, downpourRandom));
            const length = fullHeight
                ? randomRange(0.82, 1.5, downpourRandom) + strength * randomRange(0.52, 1.02, downpourRandom)
                : Math.min(randomRange(0.38, 1.04, downpourRandom), topHeight * randomRange(0.48, 0.86, downpourRandom));
            chain.hour[i] = hour;
            assignWaterfallEnvelope(chain, i, hour);
            chain.role[i] = RAIN_ROLE.DOWNPOUR;
            chain.style[i] = style;
            chain.resetSeed[i] = DOWNPOUR_RAIN_SEED;
            chain.seedIndex[i] = localIndex;
            chain.strength[i] = strength;
            chain.presence[i] = presence;
            chain.near[i] = near;
            chain.top[i] = Math.min(rainCeilingY, WATER_LEVEL + topHeight);
            chain.length[i] = Math.max(0.34, length);
            chain.curtainLayer[i] = 0;
            chain.pearlStart[i] = pearlCount;
            chain.strandPearlCount[i] = beads;
            chain.lineStart[i] = lineCount;
            chain.lineCount[i] = beads - 1;
            chain.speed[i] = randomRange(2.65, 4.35, downpourRandom) + strength * 0.72;
            chain.driftX[i] = 0;
            chain.driftZ[i] = 0;
            chain.phase[i] = downpourRandom() * Math.PI * 2;
            chain.windSpeed[i] = 0;
            chain.alpha[i] = randomRange(style === 0 ? 0.26 : 0.32, style === 2 ? 0.62 : 0.52, downpourRandom) * (0.88 + strength * 0.34) * presence;
            resetChainPosition(chain, i, true);
            pearlCount += beads;
            lineCount += beads - 1;
        }
        for (let i = 0; i < count; i += 1) {
            chain.waterfallBlendTop[i] = chain.waterfallTop[i]
                + Math.sin(chain.phase[i] * 2.17 + chain.hour[i] * 0.41) * 0.2;
        }
        const pearlPositions = new Float32Array(pearlCount * 3);
        const pearlColors = new Float32Array(pearlCount * 3);
        const pearlAlphas = new Float32Array(pearlCount);
        const pearlBaseAlpha = new Float32Array(pearlCount);
        const pearlSizes = new Float32Array(pearlCount);
        const pearlUpperScales = new Float32Array(pearlCount);
        const pearlUpperProgress = new Float32Array(pearlCount);
        const pearlUpperSeed = new Float32Array(pearlCount);
        const pearlTopRetention = new Float32Array(pearlCount);
        const pearlTopSize = new Float32Array(pearlCount);
        const pearlTopAlpha = new Float32Array(pearlCount);
        const pearlAspects = new Float32Array(pearlCount);
        const pearlHighlights = new Float32Array(pearlCount);
        const pearlChain = new Uint16Array(pearlCount);
        const pearlFraction = new Float32Array(pearlCount);
        const pearlOffsetY = new Float32Array(pearlCount);
        const pearlShimmer = new Float32Array(pearlCount);
        const pearlStorm = new Float32Array(pearlCount);
        const pearlWaterfallTop = new Float32Array(pearlCount);
        const pearlWaterfallFeather = new Float32Array(pearlCount);
        const linePositions = new Float32Array(lineCount * 2 * 3);
        const lineColors = new Float32Array(lineCount * 2 * 3);
        const lineBandPhases = new Float32Array(lineCount * 2);
        const lineAlphas = new Float32Array(lineCount * 2);
        const lineBaseAlpha = new Float32Array(lineCount);
        const linePearlA = new Uint32Array(lineCount);
        const linePearlB = new Uint32Array(lineCount);
        const lineChain = new Uint16Array(lineCount);
        const lineStorm = new Float32Array(lineCount * 2);
        for (let i = 0; i < baseCount; i += 1) {
            const start = chain.pearlStart[i];
            const beads = chain.strandPearlCount[i];
            const strength = chain.strength[i];
            const near = chain.near[i];
            const baseSize = randomRange(1.15, 1.7, random)
                + near * randomRange(0.3, 0.82, random)
                + strength * randomRange(0.1, 0.4, random);
            for (let bead = 0; bead < beads; bead += 1) {
                const pearl = start + bead;
                const index = pearl * 3;
                const fraction = beads <= 1 ? 0 : bead / (beads - 1);
                const type = random();
                pearlChain[pearl] = i;
                pearlFraction[pearl] = fraction;
                pearlOffsetY[pearl] = randomRange(-0.006, 0.006, random);
                pearlShimmer[pearl] = random() * Math.PI * 2;
                pearlUpperScales[pearl] = 1;
                pearlUpperSeed[pearl] = random();
                pearlTopRetention[pearl] = 0.3;
                pearlTopSize[pearl] = randomRange(0.35, 0.55, random);
                pearlTopAlpha[pearl] = randomRange(0.18, 0.4, random);
                pearlSizes[pearl] = baseSize * randomRange(0.92, 1.08, random);
                pearlAspects[pearl] = randomRange(1.04, 1.26, random);
                let weight;
                if (type < 0.14) {
                    pearlHighlights[pearl] = randomRange(0.18, 0.32, random);
                    weight = randomRange(0.95, 1.22, random);
                }
                else if (type < 0.46) {
                    pearlHighlights[pearl] = randomRange(0.06, 0.16, random);
                    weight = randomRange(0.56, 0.86, random);
                }
                else {
                    pearlHighlights[pearl] = randomRange(0.02, 0.08, random);
                    weight = randomRange(0.26, 0.52, random);
                }
                pearlBaseAlpha[pearl] = clamp(weight * (0.68 + near * 0.5 + strength * 0.4), 0, 1.1);
                pearlAlphas[pearl] = pearlBaseAlpha[pearl];
                writeRainColor(pearlColors, index, strength, randomRange(0.72, 1.12, random) * (0.82 + near * 0.3) * (0.78 + weight * 0.36), random);
            }
            for (let segment = 0; segment < beads - 1; segment += 1) {
                const line = chain.lineStart[i] + segment;
                const colorIndex = line * 6;
                linePearlA[line] = start + segment;
                linePearlB[line] = start + segment + 1;
                lineChain[line] = i;
                lineBaseAlpha[line] = randomRange(0.009, 0.026, random)
                    * (0.54 + strength * 0.44)
                    * (0.6 + near * 0.42);
                writeRainColor(lineColors, colorIndex, strength, randomRange(0.2, 0.36, random) * (0.78 + near * 0.24), random);
                lineColors[colorIndex + 3] = lineColors[colorIndex] * 1.12;
                lineColors[colorIndex + 4] = lineColors[colorIndex + 1] * 1.12;
                lineColors[colorIndex + 5] = lineColors[colorIndex + 2] * 1.12;
            }
        }
        for (let localIndex = 0; localIndex < ambientCount; localIndex += 1) {
            const i = baseCount + localIndex;
            const start = chain.pearlStart[i];
            const beads = chain.strandPearlCount[i];
            const strength = chain.strength[i];
            const near = chain.near[i];
            const baseSize = randomRange(0.58, 1.02, ambientRandom) + near * randomRange(0.08, 0.28, ambientRandom);
            for (let bead = 0; bead < beads; bead += 1) {
                const pearl = start + bead;
                const index = pearl * 3;
                const fraction = beads <= 1 ? 0 : bead / (beads - 1);
                pearlChain[pearl] = i;
                pearlFraction[pearl] = fraction;
                pearlOffsetY[pearl] = randomRange(-0.004, 0.004, ambientRandom);
                pearlShimmer[pearl] = ambientRandom() * Math.PI * 2;
                pearlBaseAlpha[pearl] = randomRange(0.22, 0.5, ambientRandom) * (0.88 + near * 0.22);
                pearlAlphas[pearl] = pearlBaseAlpha[pearl];
                pearlUpperScales[pearl] = 1;
                pearlUpperSeed[pearl] = ambientRandom();
                pearlTopRetention[pearl] = randomRange(0.72, 0.84, ambientRandom);
                pearlTopSize[pearl] = randomRange(0.58, 0.78, ambientRandom);
                pearlTopAlpha[pearl] = randomRange(0.55, 0.75, ambientRandom);
                pearlSizes[pearl] = baseSize * randomRange(0.54, 0.88, ambientRandom);
                pearlAspects[pearl] = randomRange(1.35, 2.0, ambientRandom);
                pearlHighlights[pearl] = randomRange(0.04, 0.14, ambientRandom);
                writeExtensionRainColor(pearlColors, index, strength, randomRange(0.34, 0.62, ambientRandom), RAIN_ROLE.AMBIENT, 0, ambientRandom);
            }
            for (let segment = 0; segment < beads - 1; segment += 1) {
                const line = chain.lineStart[i] + segment;
                const colorIndex = line * 6;
                linePearlA[line] = start + segment;
                linePearlB[line] = start + segment + 1;
                lineChain[line] = i;
                lineBaseAlpha[line] = randomRange(0.003, 0.009, ambientRandom) * (0.72 + strength * 0.24);
                writeExtensionRainColor(lineColors, colorIndex, strength, randomRange(0.1, 0.2, ambientRandom), RAIN_ROLE.AMBIENT, 0, ambientRandom);
                lineColors[colorIndex + 3] = lineColors[colorIndex] * 1.08;
                lineColors[colorIndex + 4] = lineColors[colorIndex + 1] * 1.08;
                lineColors[colorIndex + 5] = lineColors[colorIndex + 2] * 1.08;
            }
        }
        for (let localIndex = 0; localIndex < downpourCount; localIndex += 1) {
            const i = baseCount + ambientCount + localIndex;
            const start = chain.pearlStart[i];
            const beads = chain.strandPearlCount[i];
            const strength = chain.strength[i];
            const near = chain.near[i];
            const style = chain.style[i];
            const styleScale = style === 0
                ? randomRange(0.35, 0.5, downpourRandom)
                : style === 1 ? randomRange(0.5, 0.7, downpourRandom) : randomRange(0.75, 0.95, downpourRandom);
            const baseSize = (randomRange(1.05, 1.72, downpourRandom)
                + near * randomRange(0.28, 0.82, downpourRandom)
                + strength * randomRange(0.1, 0.38, downpourRandom)) * styleScale;
            for (let bead = 0; bead < beads; bead += 1) {
                const pearl = start + bead;
                const index = pearl * 3;
                const fraction = beads <= 1 ? 0 : bead / (beads - 1);
                const type = downpourRandom();
                pearlChain[pearl] = i;
                pearlFraction[pearl] = fraction;
                pearlOffsetY[pearl] = randomRange(-0.004, 0.004, downpourRandom);
                pearlShimmer[pearl] = downpourRandom() * Math.PI * 2;
                pearlBaseAlpha[pearl] = randomRange(0.2, style === 2 ? 0.64 : 0.5, downpourRandom)
                    * (0.88 + near * 0.24 + strength * 0.2);
                pearlAlphas[pearl] = pearlBaseAlpha[pearl];
                pearlUpperScales[pearl] = 1;
                pearlUpperSeed[pearl] = downpourRandom();
                pearlTopRetention[pearl] = randomRange(0.34, 0.48, downpourRandom);
                pearlTopSize[pearl] = randomRange(0.48, 0.7, downpourRandom);
                pearlTopAlpha[pearl] = randomRange(0.32, 0.58, downpourRandom);
                pearlSizes[pearl] = baseSize * (type < 0.7
                    ? randomRange(0.68, 0.94, downpourRandom)
                    : randomRange(0.88, 1.14, downpourRandom));
                pearlAspects[pearl] = style === 0
                    ? randomRange(1.28, 1.82, downpourRandom)
                    : randomRange(1.05, 1.48, downpourRandom);
                pearlHighlights[pearl] = style === 2
                    ? randomRange(0.3, 0.52, downpourRandom)
                    : randomRange(0.08, 0.24, downpourRandom);
                writeExtensionRainColor(pearlColors, index, strength, randomRange(0.38, style === 2 ? 0.86 : 0.68, downpourRandom), RAIN_ROLE.DOWNPOUR, style, downpourRandom);
            }
            for (let segment = 0; segment < beads - 1; segment += 1) {
                const line = chain.lineStart[i] + segment;
                const colorIndex = line * 6;
                linePearlA[line] = start + segment;
                linePearlB[line] = start + segment + 1;
                lineChain[line] = i;
                lineBaseAlpha[line] = randomRange(0.008, style === 2 ? 0.025 : 0.021, downpourRandom)
                    * (0.78 + strength * 0.34)
                    * (0.82 + near * 0.22);
                writeExtensionRainColor(lineColors, colorIndex, strength, randomRange(0.2, style === 2 ? 0.42 : 0.34, downpourRandom), RAIN_ROLE.DOWNPOUR, style, downpourRandom);
                lineColors[colorIndex + 3] = lineColors[colorIndex] * 1.1;
                lineColors[colorIndex + 4] = lineColors[colorIndex + 1] * 1.1;
                lineColors[colorIndex + 5] = lineColors[colorIndex + 2] * 1.1;
            }
        }
        let bridgeTrailCapacity = 0;
        for (let pearl = 0; pearl < pearlCount; pearl += 1) {
            const chainIndex = pearlChain[pearl];
            pearlStorm[pearl] = chain.stormWeight[chainIndex];
            pearlWaterfallTop[pearl] = chain.waterfallBlendTop[chainIndex];
            pearlWaterfallFeather[pearl] = chain.waterfallFeather[chainIndex];
            if (pearlStorm[pearl] > 0.08 && pearlUpperSeed[pearl] > 0.55) {
                bridgeTrailCapacity += 1;
            }
        }
        for (let segment = 0; segment < lineCount; segment += 1) {
            const stormWeight = chain.stormWeight[lineChain[segment]];
            const bandPhaseSeed = Math.sin((lineChain[segment] + 1) * 12.9898) * 43758.5453;
            const bandPhase = (bandPhaseSeed - Math.floor(bandPhaseSeed)) * Math.PI * 2;
            lineStorm[segment * 2] = stormWeight;
            lineStorm[segment * 2 + 1] = stormWeight;
            lineBandPhases[segment * 2] = bandPhase;
            lineBandPhases[segment * 2 + 1] = bandPhase;
        }
        const pearlGeometry = new THREE.BufferGeometry();
        pearlGeometry.setAttribute('position', new THREE.BufferAttribute(pearlPositions, 3).setUsage(THREE.DynamicDrawUsage));
        pearlGeometry.setAttribute('aColor', new THREE.BufferAttribute(pearlColors, 3));
        pearlGeometry.setAttribute('aAlpha', new THREE.BufferAttribute(pearlAlphas, 1).setUsage(THREE.DynamicDrawUsage));
        pearlGeometry.setAttribute('aSize', new THREE.BufferAttribute(pearlSizes, 1));
        pearlGeometry.setAttribute('aUpperScale', new THREE.BufferAttribute(pearlUpperScales, 1).setUsage(THREE.DynamicDrawUsage));
        pearlGeometry.setAttribute('aUpperProgress', new THREE.BufferAttribute(pearlUpperProgress, 1).setUsage(THREE.DynamicDrawUsage));
        pearlGeometry.setAttribute('aAspect', new THREE.BufferAttribute(pearlAspects, 1));
        pearlGeometry.setAttribute('aHighlight', new THREE.BufferAttribute(pearlHighlights, 1));
        pearlGeometry.setAttribute('aStorm', new THREE.BufferAttribute(pearlStorm, 1));
        pearlGeometry.setAttribute('aWaterfallTop', new THREE.BufferAttribute(pearlWaterfallTop, 1));
        pearlGeometry.setAttribute('aWaterfallFeather', new THREE.BufferAttribute(pearlWaterfallFeather, 1));
        pearlGeometry.setAttribute('aMorphSeed', new THREE.BufferAttribute(pearlUpperSeed, 1));
        const pearlMaterial = new THREE.ShaderMaterial({
            uniforms: {
                uTime: { value: 0 },
                uPixelRatio: { value: pixelRatio },
                uFogDensity: { value: scene.fog.density },
                uMetalDark: { value: new THREE.Color(TUNING.metalRain.darkColor) },
                uMetalMid: { value: new THREE.Color(TUNING.metalRain.midColor) },
                uMetalBright: { value: new THREE.Color(TUNING.metalRain.brightColor) },
                uMetalTint: { value: new THREE.Color(TUNING.metalRain.tintColor) },
                uPearlBandFrequency: { value: TUNING.metalRain.pearlBandFrequency },
                uPearlBandSpeed: { value: TUNING.metalRain.pearlBandSpeed },
                uPearlSpecularPower: { value: TUNING.metalRain.pearlSpecularPower },
                uPearlFresnelStrength: { value: TUNING.metalRain.pearlFresnelStrength },
                uViewport: { value: rainViewport },
                uPlotBounds: { value: rainPlotBounds },
                uPlotFeather: { value: rainPlotFeather },
                uPlotClip: rainPlotClip,
                uMobileCrisp: { value: isPhoneLandscapeViewport() ? 1 : 0 },
                uMinPointSize: { value: TUNING.pearls.minPointSize },
                uMaxPointSize: { value: TUNING.pearls.maxPointSize }
            },
            vertexShader: shaders.createRainChainSystem_vertexShader_0,
            fragmentShader: shaders.createRainChainSystem_fragmentShader_1,
            transparent: true,
            depthWrite: false,
            blending: THREE.NormalBlending
        });
        const lineGeometry = new THREE.BufferGeometry();
        lineGeometry.setAttribute('position', new THREE.BufferAttribute(linePositions, 3).setUsage(THREE.DynamicDrawUsage));
        lineGeometry.setAttribute('aColor', new THREE.BufferAttribute(lineColors, 3));
        lineGeometry.setAttribute('aAlpha', new THREE.BufferAttribute(lineAlphas, 1).setUsage(THREE.DynamicDrawUsage));
        lineGeometry.setAttribute('aStorm', new THREE.BufferAttribute(lineStorm, 1));
        lineGeometry.setAttribute('aBandPhase', new THREE.BufferAttribute(lineBandPhases, 1));
        const lineMaterial = new THREE.ShaderMaterial({
            uniforms: {
                uTime: { value: 0 },
                uFogDensity: { value: scene.fog.density },
                uMetalDark: { value: new THREE.Color(TUNING.metalRain.darkColor) },
                uMetalMid: { value: new THREE.Color(TUNING.metalRain.midColor) },
                uMetalBright: { value: new THREE.Color(TUNING.metalRain.brightColor) },
                uThreadBandDensity: { value: TUNING.metalRain.threadBandDensity },
                uThreadBandSpeed: { value: TUNING.metalRain.threadBandSpeed },
                uThreadMirrorStrength: { value: TUNING.metalRain.threadMirrorStrength },
                uViewport: { value: rainViewport },
                uPlotBounds: { value: rainPlotBounds },
                uPlotFeather: { value: rainPlotFeather },
                uPlotClip: rainPlotClip,
                uMobileCrisp: { value: isPhoneLandscapeViewport() ? 1 : 0 }
            },
            vertexShader: shaders.createRainChainSystem_vertexShader_2,
            fragmentShader: shaders.createRainChainSystem_fragmentShader_3,
            transparent: true,
            depthWrite: false,
            blending: THREE.NormalBlending
        });
        const points = new THREE.Points(pearlGeometry, pearlMaterial);
        points.renderOrder = 3;
        pearlMaterial.uniforms.uBeadScale = { value: 1.0 };
        pearlMaterial.uniforms.uBeadAlpha = { value: TUNING.pearls.alpha };
        const lines = new THREE.LineSegments(lineGeometry, lineMaterial);
        lines.renderOrder = 2;
        const data = {
            ...chain,
            pearlCount,
            lineTotal: lineCount,
            pearlPositions,
            pearlAlphas,
            pearlBaseAlpha,
            pearlUpperScales,
            pearlUpperProgress,
            pearlUpperSeed,
            pearlTopRetention,
            pearlTopSize,
            pearlTopAlpha,
            pearlChain,
            pearlFraction,
            pearlOffsetY,
            pearlShimmer,
            linePositions,
            lineAlphas,
            lineBaseAlpha,
            linePearlA,
            linePearlB,
            lineChain,
            activeBridgeTrailCount: bridgeTrailCapacity
        };
        return { points, lines, data };
    }
    function updateRainChains(system, elapsed, delta, pauseProgress) {
        const data = system.data;
        system.points.material.uniforms.uTime.value = elapsed;
        system.lines.material.uniforms.uTime.value = elapsed;
        for (let chain = 0; chain < data.count; chain += 1) {
            const isAmbientCurtain = data.role[chain] === RAIN_ROLE.AMBIENT;
            const previousTail = data.headY[chain] - data.length[chain];
            if (pauseProgress !== null) {
                // 仅暂停分支：现有雨珠和背景雨帘落到水下，不能在顶部重新生成。
                const start = pauseHeadY[chain];
                data.headY[chain] = start - (Math.max(0, start - WATER_LEVEL) + 0.08) * pauseProgress;
            }
            else if (isAmbientCurtain) {
                data.headY[chain] = data.top[chain] - 0.03;
            }
            else {
                data.headY[chain] -= data.speed[chain] * delta * 0.58 * (0.92 + state.burst * 0.08);
            }
            const tail = data.headY[chain] - data.length[chain];
            const phase = data.phase[chain];
            const wind = Math.sin(elapsed * data.windSpeed[chain] + phase);
            const counterWind = Math.cos(elapsed * data.windSpeed[chain] * 0.72 + phase * 0.7);
            const pathX = data.baseX[chain] + wind * data.driftX[chain];
            const pathZ = data.z[chain] + counterWind * data.driftZ[chain];
            data.pathX[chain] = pathX;
            data.pathZ[chain] = pathZ;
            if (!isAmbientCurtain
                && data.presence[chain] > 0.08
                && previousTail >= WATER_LEVEL
                && tail < WATER_LEVEL) {
                const stormWeight = queueRainImpact(impactPearls, pathX, pathZ, data.hour[chain], data.strength[chain], data.near[chain], data.role[chain], elapsed);
                tryEmitImpactRipple(impactPearls, rippleSystem, pathX, pathZ, data.strength[chain], stormWeight, data.role[chain], elapsed);
            }
            if (pauseProgress === null && delta > 0 && !isAmbientCurtain && tail < WATER_LEVEL) {
                resetChainPosition(data, chain, false);
            }
        }
        for (let pearl = 0; pearl < data.pearlCount; pearl += 1) {
            const chain = data.pearlChain[pearl];
            const fraction = data.pearlFraction[pearl];
            const positionIndex = pearl * 3;
            const rawY = data.headY[chain] - fraction * data.length[chain] + data.pearlOffsetY[pearl];
            const y = Math.min(rawY, rainCeilingY);
            const ceilingFade = 1 - smoothstep(rainCeilingY - 0.055, rainCeilingY, rawY);
            const columnHeight = Math.max(0.8, data.top[chain] - WATER_LEVEL);
            const heightRatio = (y - WATER_LEVEL) / columnHeight;
            const nearWater = 1 - clamp(heightRatio, 0, 1);
            const pathX = data.pathX[chain];
            const pathZ = data.pathZ[chain];
            const focus = chainFocus(data.hour[chain], data.baseX[chain], data.z[chain]);
            const shimmer = 0.9 + Math.sin(elapsed * 4.2 + data.pearlShimmer[pearl]) * 0.12;
            const upperProgress = smoothstep(0.55, 1, heightRatio);
            const retention = lerp(1, data.pearlTopRetention[pearl], Math.pow(upperProgress, 1.15));
            const densityVisibility = upperProgress <= 0
                ? 1
                : 1 - smoothstep(retention - 0.06, retention + 0.06, data.pearlUpperSeed[pearl]);
            const upperSizeScale = lerp(1, data.pearlTopSize[pearl], Math.pow(upperProgress, 1.05));
            const upperAlphaScale = lerp(1, data.pearlTopAlpha[pearl], Math.pow(upperProgress, 1.1))
                * densityVisibility;
            const role = data.role[chain];
            const stormWeight = data.stormWeight[chain];
            const transitionTop = data.waterfallBlendTop[chain];
            const transitionWidth = data.waterfallFeather[chain];
            const waterfallTransition = stormWeight * (1 - smoothstep(transitionTop - transitionWidth, transitionTop + transitionWidth, y));
            const morphRetention = clamp((data.pearlUpperSeed[pearl] - 0.55) / 0.27, 0, 1);
            const finalPearlScale = role === RAIN_ROLE.DOWNPOUR
                ? lerp(0.5, 0.82, morphRetention)
                : lerp(0.4, 0.68, morphRetention);
            const curtainFloorScale = lerp(1, finalPearlScale, waterfallTransition);
            const sizeWaterfallScale = lerp(1, role === RAIN_ROLE.DOWNPOUR
                ? lerp(0.96, 1.62, morphRetention)
                : lerp(0.78, 1.36, morphRetention), smoothstep(0.22, 1, waterfallTransition));
            const deepWaterfallFade = lerp(1, role === RAIN_ROLE.DOWNPOUR
                ? lerp(0.76, 0.98, morphRetention)
                : lerp(0.62, 0.86, morphRetention), smoothstep(0.48, 1, waterfallTransition));
            const rainFloorFade = role === RAIN_ROLE.DOWNPOUR
                ? smoothstep(WATER_LEVEL + 0.015, WATER_LEVEL + 0.1, y)
                : smoothstep(WATER_LEVEL + 0.02, WATER_LEVEL + 0.12, y);
            const lowerCurtain = data.curtainLayer[chain] === 1 ? 1 : 0;
            const bottomGlow = role === RAIN_ROLE.BASE
                ? 1
                    + smoothstep(0.02, 0.5, nearWater)
                        * (TUNING.floorGlow.base + data.strength[chain] * TUNING.floorGlow.baseByStrength)
                        * (1 - stormWeight)
                    + lowerCurtain
                        * (TUNING.floorGlow.lowerCurtain + data.strength[chain] * TUNING.floorGlow.lowerCurtainByStrength)
                        * (1 - stormWeight)
                : role === RAIN_ROLE.DOWNPOUR
                    ? 1 + smoothstep(0.02, 0.54, nearWater)
                        * (TUNING.floorGlow.downpour + data.strength[chain] * TUNING.floorGlow.downpourByStrength)
                        * (1 - stormWeight)
                    : 1 + smoothstep(0.02, 0.5, nearWater) * TUNING.floorGlow.ambient;
            data.pearlPositions[positionIndex] = pathX;
            data.pearlPositions[positionIndex + 1] = y;
            data.pearlPositions[positionIndex + 2] = pathZ;
            data.pearlUpperScales[pearl] = upperSizeScale * sizeWaterfallScale;
            data.pearlUpperProgress[pearl] = upperProgress;
            data.pearlAlphas[pearl] = clamp(data.pearlBaseAlpha[pearl]
                * data.alpha[chain]
                * shimmer
                * ceilingFade
                * upperAlphaScale
                * rainFloorFade
                * bottomGlow
                * curtainFloorScale
                * deepWaterfallFade
                * (1 + focus * 0.56 + state.burst * localHourFocus(data.hour[chain], state.selectedHour) * 0.38), 0, 0.96);
        }
        for (let segment = 0; segment < data.lineTotal; segment += 1) {
            const a = data.linePearlA[segment] * 3;
            const b = data.linePearlB[segment] * 3;
            const lineIndex = segment * 6;
            const alphaIndex = segment * 2;
            const chain = data.lineChain[segment];
            const role = data.role[chain];
            const segmentY = (data.pearlPositions[a + 1] + data.pearlPositions[b + 1]) * 0.5;
            const stormWeight = data.stormWeight[chain];
            const transitionTop = data.waterfallBlendTop[chain];
            const transitionWidth = data.waterfallFeather[chain];
            const waterfallTransition = stormWeight * (1 - smoothstep(transitionTop - transitionWidth, transitionTop + transitionWidth, segmentY));
            const bridgeBoost = 1 + 4 * waterfallTransition * (1 - waterfallTransition) * 0.88;
            const lineFloorScale = lerp(1, role === RAIN_ROLE.DOWNPOUR ? 0.12 : 0.2, waterfallTransition) * bridgeBoost;
            const focus = chainFocus(data.hour[chain], data.baseX[chain], data.z[chain]);
            const endpointVisibility = clamp((data.pearlAlphas[data.linePearlA[segment]] + data.pearlAlphas[data.linePearlB[segment]]) * 0.86, 0, 1);
            const lineRoleBoost = role === RAIN_ROLE.DOWNPOUR
                ? 2.45
                : role === RAIN_ROLE.AMBIENT ? 1.2 : 1;
            const alpha = data.lineBaseAlpha[segment]
                * endpointVisibility
                * (0.76 + focus * 0.52)
                * lineRoleBoost
                * lineFloorScale;
            data.linePositions[lineIndex] = data.pearlPositions[a];
            data.linePositions[lineIndex + 1] = Math.max(WATER_LEVEL + 0.006, data.pearlPositions[a + 1]);
            data.linePositions[lineIndex + 2] = data.pearlPositions[a + 2];
            data.linePositions[lineIndex + 3] = data.pearlPositions[b];
            data.linePositions[lineIndex + 4] = Math.max(WATER_LEVEL + 0.006, data.pearlPositions[b + 1]);
            data.linePositions[lineIndex + 5] = data.pearlPositions[b + 2];
            data.lineAlphas[alphaIndex] = alpha * 0.58;
            data.lineAlphas[alphaIndex + 1] = alpha;
        }
        system.points.geometry.attributes.position.needsUpdate = true;
        system.points.geometry.attributes.aAlpha.needsUpdate = true;
        system.points.geometry.attributes.aUpperScale.needsUpdate = true;
        system.points.geometry.attributes.aUpperProgress.needsUpdate = true;
        system.lines.geometry.attributes.position.needsUpdate = true;
        system.lines.geometry.attributes.aAlpha.needsUpdate = true;
    }
    function createWaterPlane() {
        const geometry = new THREE.PlaneGeometry(WORLD.width * 1.16, WORLD.depth * 2.3, 150, 92);
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, WATER_LEVEL, 1.25);
        const material = new THREE.ShaderMaterial({
            uniforms: {
                uTime: { value: 0 },
                uColorDeep: { value: new THREE.Color(TUNING.water.deepColor) },
                uColorSurface: { value: new THREE.Color(TUNING.water.surfaceColor) },
                uRoughness: { value: TUNING.water.roughness },
                uSpecularStrength: { value: TUNING.water.specularStrength },
                uRippleHighlight: { value: TUNING.water.rippleHighlight },
                uSurfaceOpacity: { value: TUNING.water.surfaceOpacity },
                uHeightField: { value: null },
                uFieldBounds: {
                    value: new THREE.Vector4(RIPPLE_FIELD.xMin, RIPPLE_FIELD.xMax, RIPPLE_FIELD.zMin, RIPPLE_FIELD.zMax)
                },
                uFieldTexel: { value: new THREE.Vector2(1 / RIPPLE_FIELD.resX, 1 / RIPPLE_FIELD.resZ) },
                uRippleGain: { value: TUNING.ripple.gain },
                uRippleDisplace: { value: TUNING.ripple.displace },
                uWavePrimary: { value: TUNING.water.wavePrimary },
                uWaveSecondary: { value: TUNING.water.waveSecondary },
                uRainLut: { value: null },
                uLutBounds: { value: new THREE.Vector2(hourToX(0), hourToX(24) - hourToX(0)) },
                uReflStrength: { value: TUNING.water.reflStrength },
                uReflFade: { value: TUNING.water.reflFade },
                uRearFadeNearZ: { value: TUNING.water.rearFadeNearZ },
                uRearFadeFarZ: { value: TUNING.water.rearFadeFarZ }
            },
            vertexShader: shaders.createWaterPlane_vertexShader_4,
            fragmentShader: shaders.createWaterPlane_fragmentShader_5,
            transparent: true,
            depthWrite: false,
            blending: THREE.NormalBlending
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.renderOrder = 0;
        return mesh;
    }
    function createPoolFloor() {
        const geometry = new THREE.PlaneGeometry(WORLD.width * 1.16, WORLD.depth * 2.3);
        geometry.rotateX(-Math.PI / 2);
        geometry.translate(0, WATER_LEVEL - 0.28, 1.25);
        const material = new THREE.ShaderMaterial({
            uniforms: {
                uColor: { value: new THREE.Color(TUNING.water.deepColor) },
                uRearFadeNearZ: { value: TUNING.water.rearFadeNearZ },
                uRearFadeFarZ: { value: TUNING.water.rearFadeFarZ }
            },
            vertexShader: shaders.createPoolFloor_vertexShader_6,
            fragmentShader: shaders.createPoolFloor_fragmentShader_7,
            transparent: true,
            depthWrite: false,
            side: THREE.DoubleSide,
            toneMapped: false
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.renderOrder = -1;
        return mesh;
    }
    function createMistBand() {
        const N = 256;
        const xMin = hourToX(0);
        const xMax = hourToX(24);
        const span = xMax - xMin;
        const acc = new Float32Array(N);
        const wsum = new Float32Array(N);
        for (let h = 0; h <= 24; h += 0.04) {
            const frac = clamp((hourToX(h) - xMin) / span, 0, 1);
            const s = sampleRainStrength(h);
            const center = frac * (N - 1);
            const radius = 7;
            const lo = Math.max(0, Math.floor(center - radius));
            const hi = Math.min(N - 1, Math.ceil(center + radius));
            for (let j = lo; j <= hi; j += 1) {
                const d = (j - center) / radius;
                const w = Math.exp(-d * d * 1.3);
                acc[j] += s * w;
                wsum[j] += w;
            }
        }
        const packed = new Float32Array(N * 4);
        for (let j = 0; j < N; j += 1) {
            const hour = j / (N - 1) * 24;
            const presence = sampleRainPresence(hour);
            const intensity = (wsum[j] > 0 ? Math.pow(acc[j] / wsum[j], 1.12) : 0) * presence;
            packed[j * 4] = intensity;
            packed[j * 4 + 1] = intensity;
            packed[j * 4 + 2] = intensity;
            packed[j * 4 + 3] = 1;
        }
        const lut = new THREE.DataTexture(packed, N, 1, THREE.RGBAFormat, THREE.FloatType);
        lut.minFilter = THREE.LinearFilter;
        lut.magFilter = THREE.LinearFilter;
        lut.wrapS = THREE.ClampToEdgeWrapping;
        lut.wrapT = THREE.ClampToEdgeWrapping;
        lut.needsUpdate = true;
        const height = TUNING.mist.height;
        const geometry = new THREE.PlaneGeometry(span * 1.01, height, 1, 1);
        geometry.translate((xMin + xMax) * 0.5, WATER_LEVEL + height * 0.5 - 0.42, 1.15);
        const material = new THREE.ShaderMaterial({
            uniforms: {
                uTime: { value: 0 },
                uLut: { value: lut },
                uColor: { value: new THREE.Color(TUNING.mist.color) },
                uOpacity: { value: TUNING.mist.opacity },
                uReflOpacity: { value: TUNING.mist.reflectionOpacity },
                uViewport: { value: rainViewport },
                uPlotBounds: { value: rainPlotBounds },
                uPlotFeather: { value: rainPlotFeather }
            },
            vertexShader: shaders.createMistBand_vertexShader_8,
            fragmentShader: shaders.createMistBand_fragmentShader_9,
            transparent: true,
            depthWrite: false,
            depthTest: true,
            blending: THREE.AdditiveBlending
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = 'rain-mist-band';
        mesh.renderOrder = 1.7;
        mesh.frustumCulled = false;
        return { mesh, material, lut, lutXMin: xMin, lutSpan: span };
    }
    function createRippleFieldSystem(renderer) {
        const { resX, resZ, xMin, xMax, zMin, zMax } = RIPPLE_FIELD;
        const maxDrops = 160;
        const rtOptions = {
            type: THREE.HalfFloatType,
            format: THREE.RGBAFormat,
            minFilter: THREE.LinearFilter,
            magFilter: THREE.LinearFilter,
            depthBuffer: false,
            stencilBuffer: false,
            wrapS: THREE.ClampToEdgeWrapping,
            wrapT: THREE.ClampToEdgeWrapping
        };
        let rtA = new THREE.WebGLRenderTarget(resX, resZ, rtOptions);
        let rtB = new THREE.WebGLRenderTarget(resX, resZ, rtOptions);
        const simCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
        const prevTarget = renderer.getRenderTarget();
        renderer.setRenderTarget(rtA);
        renderer.clear();
        renderer.setRenderTarget(rtB);
        renderer.clear();
        renderer.setRenderTarget(prevTarget);
        const simMaterial = new THREE.ShaderMaterial({
            uniforms: {
                uField: { value: null },
                uTexel: { value: new THREE.Vector2(1 / resX, 1 / resZ) },
                uDamping: { value: TUNING.ripple.damping }
            },
            vertexShader: shaders.createRippleFieldSystem_vertexShader_10,
            fragmentShader: shaders.createRippleFieldSystem_fragmentShader_11,
            depthTest: false,
            depthWrite: false
        });
        const simScene = new THREE.Scene();
        simScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), simMaterial));
        const dropPositions = new Float32Array(maxDrops * 3);
        const dropAmps = new Float32Array(maxDrops);
        const dropGeometry = new THREE.BufferGeometry();
        dropGeometry.setAttribute('position', new THREE.BufferAttribute(dropPositions, 3).setUsage(THREE.DynamicDrawUsage));
        dropGeometry.setAttribute('aAmp', new THREE.BufferAttribute(dropAmps, 1).setUsage(THREE.DynamicDrawUsage));
        dropGeometry.setDrawRange(0, 0);
        const dropMaterial = new THREE.ShaderMaterial({
            uniforms: {
                uDropPx: { value: Math.max(5, resX / 210) }
            },
            vertexShader: shaders.createRippleFieldSystem_vertexShader_12,
            fragmentShader: shaders.createRippleFieldSystem_fragmentShader_13,
            transparent: true,
            depthTest: false,
            depthWrite: false,
            blending: THREE.CustomBlending,
            blendEquation: THREE.AddEquation,
            blendSrc: THREE.OneFactor,
            blendDst: THREE.OneFactor
        });
        const dropScene = new THREE.Scene();
        dropScene.add(new THREE.Points(dropGeometry, dropMaterial));
        return {
            rtA,
            rtB,
            simCamera,
            simMaterial,
            simScene,
            dropScene,
            dropGeometry,
            dropMaterial,
            dropPositions,
            dropAmps,
            dropCount: 0,
            maxDrops,
            fieldBounds: { xMin, xMax, zMin, zMax },
            texture: rtA.texture
        };
    }
    function addRipple(system, x, z, intensity, selected, elapsed) {
        if (system.dropCount >= system.maxDrops)
            return;
        const b = system.fieldBounds;
        const u = (clamp(x, b.xMin, b.xMax) - b.xMin) / (b.xMax - b.xMin);
        const v = (clamp(z, b.zMin, b.zMax) - b.zMin) / (b.zMax - b.zMin);
        const i = system.dropCount;
        system.dropPositions[i * 3] = u * 2 - 1;
        system.dropPositions[i * 3 + 1] = v * 2 - 1;
        system.dropPositions[i * 3 + 2] = 0;
        system.dropAmps[i] = (selected ? TUNING.ripple.dropClick : TUNING.ripple.dropRain) * (0.6 + intensity);
        system.dropCount += 1;
    }
    function updateRippleField(system, renderer, elapsed) {
        const prevTarget = renderer.getRenderTarget();
        const prevAutoClear = renderer.autoClear;
        if (system.dropCount > 0) {
            system.dropGeometry.setDrawRange(0, system.dropCount);
            system.dropGeometry.attributes.position.needsUpdate = true;
            system.dropGeometry.attributes.aAmp.needsUpdate = true;
            renderer.autoClear = false;
            renderer.setRenderTarget(system.rtA);
            renderer.render(system.dropScene, system.simCamera);
            renderer.autoClear = prevAutoClear;
        }
        system.simMaterial.uniforms.uField.value = system.rtA.texture;
        renderer.setRenderTarget(system.rtB);
        renderer.render(system.simScene, system.simCamera);
        const tmp = system.rtA;
        system.rtA = system.rtB;
        system.rtB = tmp;
        system.texture = system.rtA.texture;
        renderer.setRenderTarget(prevTarget);
        renderer.autoClear = prevAutoClear;
        waterPlane.material.uniforms.uHeightField.value = system.texture;
        system.dropCount = 0;
    }
    function resetChainPosition(data, i, initial) {
        if (!initial) {
            data.respawnCycle[i] += 1;
        }
        const resetSeed = data.resetSeed?.[i] || RAIN_LAYOUT_SEED;
        const seedIndex = data.seedIndex?.[i] ?? i;
        const random = createIndexedRandom(resetSeed, seedIndex, data.respawnCycle[i]);
        const strength = data.strength[i];
        const near = data.near[i];
        const layer = data.curtainLayer[i];
        const columnHeight = Math.max(0.8, data.top[i] - WATER_LEVEL);
        const spread = lerp(0.18, 0.68, Math.pow(strength, 0.82));
        data.baseX[i] = clamp(hourToX(data.hour[i]) + randomRange(-spread, spread, random), hourToX(0) + 0.12, hourToX(24) - 0.12);
        data.z[i] = sampleDepth(near, random);
        if (data.role[i] === RAIN_ROLE.AMBIENT) {
            data.headY[i] = rainCeilingY - 0.03;
            return;
        }
        if (layer === 1) {
            const lowerCeiling = WATER_LEVEL + columnHeight * randomRange(0.3, 0.44, random);
            data.headY[i] = randomRange(WATER_LEVEL + data.length[i] * 0.72, lowerCeiling + data.length[i] * 0.4, random);
            return;
        }
        if (layer === 0) {
            data.headY[i] = randomRange(WATER_LEVEL + data.length[i] * 0.78, data.top[i] + data.length[i] * 0.44, random);
            return;
        }
        data.headY[i] = initial
            ? randomRange(data.top[i] * 0.72, data.top[i] + data.length[i], random)
            : data.top[i] + data.length[i] + randomRange(0.04, 1.1, random);
    }
    function createIntegratedDownpourHourSampler(random = Math.random) {
        if (!peakWaterfallRanges.length) {
            return createHourSampler(0, 1, random, true);
        }
        const samplers = peakWaterfallRanges.map(([startHour, endHour]) => (createWeightedHourRangeSampler(startHour, endHour, random)));
        const weights = peakWaterfallRanges.map(([startHour, endHour]) => {
            const samples = 24;
            let weight = 0;
            for (let index = 0; index < samples; index += 1) {
                const hour = lerp(startHour, endHour, (index + 0.5) / samples);
                weight += 0.02 + sampleStormWeight(hour) * (0.7 + sampleRelativeRainfall(hour));
            }
            return weight * Math.max(0.1, endHour - startHour);
        });
        const total = weights.reduce((sum, value) => sum + value, 0);
        if (!Number.isFinite(total) || total <= 0) {
            return () => randomRange(0.04, 23.96, random);
        }
        const cumulative = [];
        let cursor = 0;
        for (const weight of weights) {
            cursor += weight / total;
            cumulative.push(cursor);
        }
        return () => {
            const pick = random();
            const index = Math.max(0, cumulative.findIndex(value => pick <= value));
            return samplers[index]();
        };
    }
    function createWeightedHourRangeSampler(startHour, endHour, random) {
        const binCount = 64;
        const binWidth = (endHour - startHour) / binCount;
        const weights = Array.from({ length: binCount }, (_, bin) => {
            const hour = startHour + (bin + 0.5) * binWidth;
            const strength = sampleRelativeRainfall(hour);
            const dataWeight = Math.pow(Math.max(0.001, strength - 0.2), 1.42);
            return 0.001 + dataWeight * sampleRainCluster(hour);
        });
        const total = weights.reduce((sum, value) => sum + value, 0);
        if (!Number.isFinite(total) || total <= 0) {
            return () => randomRange(0.04, 23.96, random);
        }
        const cumulative = [];
        let cursor = 0;
        for (const weight of weights) {
            cursor += weight / total;
            cumulative.push(cursor);
        }
        return () => {
            const pick = random();
            const binIndex = Math.max(0, cumulative.findIndex((value) => pick <= value));
            return clamp(startHour + (binIndex + random()) * binWidth, startHour, endHour);
        };
    }
    function createHourSampler(baseWeight, exponent, random = Math.random, clustered = false, intensityGain = 2.35, spatiallyBalanced = false) {
        const binCount = clustered ? 96 : 24;
        const binWidth = 24 / binCount;
        const weights = Array.from({ length: binCount }, (_, bin) => {
            const hour = (bin + 0.5) * binWidth;
            const strength = sampleRelativeRainfall(hour);
            const presence = sampleRainPresence(hour);
            const clusterWeight = clustered ? sampleRainCluster(hour) : 1;
            const spatialWeight = spatiallyBalanced ? sampleHourSpatialWeight(hour) : 1;
            const effectiveExponent = 0.55 + Math.max(0, exponent) * 0.35;
            const densityResponse = Math.pow(strength, effectiveExponent);
            const lowRainResponse = baseWeight * Math.pow(strength, 0.85);
            return presence
                * (lowRainResponse + densityResponse * intensityGain * clusterWeight)
                * spatialWeight;
        });
        const total = weights.reduce((sum, value) => sum + value, 0);
        if (!Number.isFinite(total) || total <= 0) {
            return () => randomRange(0.04, 23.96, random);
        }
        const cumulative = [];
        let cursor = 0;
        for (const weight of weights) {
            cursor += weight / total;
            cumulative.push(cursor);
        }
        return () => {
            const pick = random();
            const binIndex = Math.max(0, cumulative.findIndex((value) => pick <= value));
            const hour = binIndex * binWidth;
            if (clustered) {
                return clamp(hour + random() * binWidth, 0.04, 23.96);
            }
            const strength = sampleRelativeRainfall(hour);
            const spread = lerp(0.54, 0.95, Math.pow(strength, 0.62));
            const centered = ((random() + random() + random()) / 3 - 0.5) * 2;
            return clamp(hour + 0.5 + centered * spread, 0.04, 23.96);
        };
    }
    function sampleHourSpatialWeight(hour) {
        const rightHalf = hour >= 12;
        const exponent = rightHalf ? 2.3 : 1.45;
        const edgeDistance = Math.abs(hour / 12 - 1);
        return clamp(exponent * Math.pow(edgeDistance, exponent - 1), 0.42, 2.3);
    }
    function sampleRainCluster(hour) {
        const broad = valueNoise(hour * 0.62, RAIN_CLUSTER_SEED);
        const detail = valueNoise(hour * 2.45, RAIN_CLUSTER_SEED ^ 0x7f4a7c15);
        return 0.68 + broad * 0.38 + detail * 0.34;
    }
    function sampleRainfall(hour) {
        const safeHour = clamp(hour, 0, 24);
        const left = Math.floor(safeHour);
        const right = Math.min(left + 1, activeRainfall.length - 1);
        const amount = safeHour - left;
        return lerp(activeRainfall[left], activeRainfall[right], amount);
    }
    function sampleRainPresence(hour) {
        const safeHour = clamp(hour, 0, 24);
        const left = Math.floor(safeHour);
        const right = Math.min(left + 1, activeRainfall.length - 1);
        const amount = safeHour - left;
        const leftWet = activeRainfall[left] > 0;
        const rightWet = activeRainfall[right] > 0;
        if (!leftWet && !rightWet)
            return 0;
        const featherHours = 0.68;
        let zeroAnchorGate = 1;
        if (!leftWet)
            zeroAnchorGate *= smoothstep(0, featherHours, amount);
        if (!rightWet)
            zeroAnchorGate *= 1 - smoothstep(1 - featherHours, 1, amount);
        return clamp(zeroAnchorGate, 0, 1);
    }
    function sampleVisualRainfall(hour) {
        return sampleRainfall(hour) * sampleRainPresence(hour);
    }
    function sampleRelativeRainfall(hour) {
        if (rainfallMax <= 0)
            return 0;
        return clamp(sampleVisualRainfall(hour) / rainfallMax, 0, 1);
    }
    function sampleRainStrength(hour) {
        return clamp(sampleVisualRainfall(hour) / VISUAL_RAINFALL_REFERENCE, 0, 2);
    }
    function rainVisibilityResponse(strength) {
        const normalRain = Math.pow(clamp(strength, 0, 1), 0.58);
        const extremeRain = clamp(strength - 1, 0, 1) * 0.2;
        return normalRain + extremeRain;
    }
    function sampleRainHeightStrength(hour) {
        return clamp(sampleVisualRainfall(hour) / Math.max(0.001, rainCeilingValue), 0, 1);
    }
    function sampleStormWeight(hour) {
        return smoothstep(5.8, 7.8, sampleVisualRainfall(hour));
    }
    function sampleWaterfallTopY(hour) {
        const strength = sampleRainHeightStrength(hour);
        const clusterHeight = lerp(0.91, 1.09, valueNoise(hour * 1.55, RAIN_CLUSTER_SEED ^ 0x51f2a93d));
        const fullColumnHeight = clamp(0.95 + Math.pow(strength, 0.92) * 5.8 * clusterHeight, 1.2, 8.8);
        const topNoise = lerp(0.92, 1.08, valueNoise(hour * 3.4, RAIN_CLUSTER_SEED ^ 0x16c5a2d9));
        return WATER_LEVEL + fullColumnHeight * 0.54 * topNoise;
    }
    function assignWaterfallEnvelope(chain, index, hour) {
        chain.stormWeight[index] = sampleStormWeight(hour);
        chain.waterfallTop[index] = sampleWaterfallTopY(hour);
        chain.waterfallFeather[index] = lerp(1.35, 2.05, valueNoise(hour * 2.7 + index * 0.013, RAIN_CLUSTER_SEED ^ 0x762ad4f1));
    }
    function sampleNearness(random = Math.random) {
        const pick = random();
        if (pick < 0.38)
            return randomRange(0.72, 1, random);
        if (pick < 0.68)
            return randomRange(0.38, 0.72, random);
        return randomRange(0, 0.38, random);
    }
    function sampleDepth(near, random = Math.random) {
        const t = (near - 0.5) * 2; // -1(最远) .. 1(最近)
        return RAIN_DEPTH.center
            + t * RAIN_DEPTH.thickness
            + randomRange(-RAIN_DEPTH.jitter, RAIN_DEPTH.jitter, random);
    }
    function writeRainColor(target, index, strength, brightness, random = Math.random) {
        const color = strength > 0.62 && random() < 0.46
            ? PALETTE.pearlBright
            : random() < 0.68 ? PALETTE.pearlMid : PALETTE.pearlDark;
        const gain = brightness * (0.76 + strength * 0.28);
        target[index] = color.r * gain;
        target[index + 1] = color.g * gain;
        target[index + 2] = color.b * gain;
    }
    function writeExtensionRainColor(target, index, strength, brightness, role, style, random = Math.random) {
        const brightChance = role === RAIN_ROLE.AMBIENT
            ? 0.01
            : style === 2 ? 0.72 : 0.018;
        const tone = random();
        const color = tone < brightChance
            ? PALETTE.pearlBright
            : tone < brightChance + 0.68 ? PALETTE.pearlMid : PALETTE.pearlDark;
        const gain = brightness * (0.76 + strength * 0.28);
        target[index] = color.r * gain;
        target[index + 1] = color.g * gain;
        target[index + 2] = color.b * gain;
    }
    function hourToX(hour) {
        return (hour / 24 - 0.5) * AXIS_CONFIG.plotWidth;
    }
    function localHourFocus(hour, targetHour) {
        const distance = Math.abs(hour - targetHour);
        return Math.max(0, 1 - distance / 2.25);
    }
    function createSeededRandom(seed) {
        let value = seed >>> 0;
        return () => {
            value += 0x6d2b79f5;
            let result = value;
            result = Math.imul(result ^ result >>> 15, result | 1);
            result ^= result + Math.imul(result ^ result >>> 7, result | 61);
            return ((result ^ result >>> 14) >>> 0) / 4294967296;
        };
    }
    function createIndexedRandom(seed, index, cycle) {
        const mixedSeed = seed
            ^ Math.imul(index + 1, 0x9e3779b1)
            ^ Math.imul(cycle + 1, 0x85ebca6b);
        return createSeededRandom(mixedSeed >>> 0);
    }
    function valueNoise(value, seed) {
        const left = Math.floor(value);
        const amount = smoothstep(0, 1, value - left);
        return lerp(hashNoise(left, seed), hashNoise(left + 1, seed), amount);
    }
    function hashNoise(index, seed) {
        let value = Math.imul(index ^ seed, 0x45d9f3b);
        value = Math.imul(value ^ value >>> 16, 0x45d9f3b);
        return ((value ^ value >>> 16) >>> 0) / 4294967295;
    }
    function randomRange(min, max, random = Math.random) {
        return min + random() * (max - min);
    }
    function lerp(a, b, t) {
        return a + (b - a) * t;
    }
    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }
    function smoothstep(edge0, edge1, value) {
        const t = clamp((value - edge0) / (edge1 - edge0), 0, 1);
        return t * t * (3 - 2 * t);
    }
    function disposeObject3D(object) {
        if (!object)
            return;
        const geometries = new Set();
        const materials = new Set();
        const textures = new Set();
        object.traverse((node) => {
            if (node.isInstancedMesh) node.dispose();
            if (node.geometry)
                geometries.add(node.geometry);
            const nodeMaterials = Array.isArray(node.material) ? node.material : [node.material];
            nodeMaterials.filter(Boolean).forEach(material => materials.add(material));
        });
        for (const material of materials) {
            for (const value of Object.values(material)) {
                if (value?.isTexture)
                    textures.add(value);
            }
            for (const uniform of Object.values(material.uniforms || {})) {
                if (uniform?.value?.isTexture)
                    textures.add(uniform.value);
            }
        }
        textures.forEach(texture => texture.dispose());
        geometries.forEach(geometry => geometry.dispose());
        materials.forEach(material => material.dispose());
    }
}
