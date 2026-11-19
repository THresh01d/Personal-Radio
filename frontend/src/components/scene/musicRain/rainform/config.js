// Required Notice: Rainform / 数据成雨 © 2026 afterimage — https://rainform.pages.dev/
// 改编原作渲染核心；许可证、来源和修改范围见本目录 README.md。
import * as THREE from 'three';
export function createConfig() {
    const WATER_LEVEL = -0.14;
    const RAIN_LAYOUT_SEED = 0x6d2b79f5;
    const RAIN_CLUSTER_SEED = 0x2f6e2b1d;
    const AMBIENT_RAIN_SEED = 0x53a91d7b;
    const DOWNPOUR_RAIN_SEED = 0x31c7af59;
    const RAIN_ROLE = {
        BASE: 0,
        AMBIENT: 1,
        DOWNPOUR: 2
    };
    const WORLD = {
        width: 22.5,
        depth: 10.4,
        frontZ: 4.9,
        backZ: -4.2
    };
    const RAIN_DEPTH = { center: 0.2, thickness: 0.85, jitter: 0.22 };
    const AXIS_CONFIG = {
        worldHeight: 6.6,
        plotWidth: 17,
        z: RAIN_DEPTH.center + RAIN_DEPTH.thickness + 0.32,
        fadeDuration: 0.2,
        dragThreshold: 5
    };
    const RIPPLE_FIELD = {
        resX: 768,
        resZ: 384,
        xMin: -WORLD.width * 0.55,
        xMax: WORLD.width * 0.55,
        zMin: -WORLD.depth * 0.6,
        zMax: WORLD.frontZ * 1.5
    };
    const PALETTE = {
        fog: 0x000000,
        pearlBright: new THREE.Color(0xd2d8de),
        pearlMid: new THREE.Color(0x8492a2),
        pearlDark: new THREE.Color(0x424e5c),
        waterBright: new THREE.Color(0xa2b0bc),
        waterMid: new THREE.Color(0x5a6878),
        waterDark: new THREE.Color(0x283040)
    };
    const TUNING = {
        mist: {
            color: 0xabb6bf, // 雾色。调亮会偏银白发光；调暗会更冷、更隐入背景。
            opacity: 0.28, // 雾整体不透明度。调大会水线更亮更奶雾；调小会减少底部光带。
            height: 3.5, // 雾带高度。调大雾往上铺得更高；调小雾更贴近水面。
            reflectionOpacity: 0 // 水线倒影强度。调大会多一层竖向反光；调小会更干净克制。
        },
        pearls: {
            maxPointSize: 1.9, // 近景雨珠最大像素。调大会珠子更圆更显眼；调小会变成更细的雨丝。
            minPointSize: 25.3, // 远景雨珠最小像素。调大会远处仍有颗粒；调小会让远景更暗更轻。
            alpha: 3 // 雨珠整体透明度。调大会雨幕更实；调小会更稀薄、更像雾。
        },
        rain: {
            baseWeight: 0.62, // 低谷权重。只放大已有的小雨，不再给所有非零时段增加固定雨量。
            exponent: 0.05 // 强度曲线偏移。调大会雨更集中在峰值；调小会让分布更均匀。
        },
        metalRain: {
            darkColor: 0x000000,
            midColor: 0xc2cbdb,
            brightColor: 0xfafbff,
            tintColor: 0x0091ff,
            pearlBandFrequency: 5.5,
            pearlBandSpeed: -2.55,
            pearlSpecularPower: 30,
            pearlFresnelStrength: 1.56,
            threadBandDensity: 0.081,
            threadBandSpeed: -0.9,
            threadMirrorStrength: 0.93,
            bodyBandDensity: 5.4,
            bodyBandSpeed: -1.1,
            bodyMirrorStrength: 0.78,
            filamentBandDensity: 2.35,
            filamentBandSpeed: 0.84,
            filamentMirrorStrength: 0.58,
            highlightMirrorStrength: 0.9
        },
        floorGlow: {
            base: -0.77, // 普通雨基础提亮。调大会水线附近更亮；调小会压暗底部光带。
            baseByStrength: 0.48, // 普通雨随雨强增加的提亮。调大会暴雨段底部更亮；调小会减少峰值发白。
            lowerCurtain: 0.13, // 低层雨帘额外提亮。调大会底部短雨帘更明显；调小会让它们更隐。
            lowerCurtainByStrength: -0.89, // 低层雨帘随雨强增加的提亮。调大会峰值下沿更亮；调小会更暗。
            downpour: -1.43, // 瀑布雨近水基础提亮。调大会瀑布底部更白；调小会保留暗部。
            downpourByStrength: 0.67, // 瀑布雨随雨强增加的提亮。调大会峰值底部更强；调小会更柔。
            ambient: -0.06 // 背景雨近水提亮。调大会背景水线更有雾光；调小会更干净。
        },
        water: {
            deepColor: 0x000000, // 深水底色。压到接近雾色，水面读作近黑镜面。
            surfaceColor: 0x2b3240, // 表层色，用于近端渐变。
            roughness: 1, // 磨砂程度。0 接近镜面，1 更柔和、更漫反射。
            specularStrength: 2.5, // 镜面高光强度。
            rippleHighlight: 3, // 波峰反光强度。
            surfaceOpacity: 5, // 水面基础浓度；过高会产生磨砂白雾感。
            wavePrimary: 0, // 大波顶点位移幅度。
            waveSecondary: 0, // 小波顶点位移幅度。
            reflStrength: 0.5, // 雨柱倒影亮度。调大水面镜像更亮(竖条更明显)；调小更隐入黑底。
            reflFade: 2.15, // 倒影长度(世界单位)。调大倒影拖得更远；调小更紧贴水线。
            rearFadeNearZ: -0.7, // 后缘渐隐起点。
            rearFadeFarZ: -4.2 // 后缘完全透明的位置，与背景边界一致。
        },
        ripple: {
            gain: 0, // 涟漪亮度增益。调大会环纹更亮更明显；调小会更隐。
            damping: 0.9999, // 波衰减。调大会波传播更久；调小会更快消失。
            dropRain: 1.51, // 普通雨滴涟漪强度。调大会每滴雨更容易出圈；调小会更平静。
            dropClick: 0.95, // 点击涟漪强度。
            displace: 0.012 // 涟漪顶点位移幅度，太大会在掠射角下产生条纹。
        }
    };
    return { WATER_LEVEL, RAIN_LAYOUT_SEED, RAIN_CLUSTER_SEED, AMBIENT_RAIN_SEED, DOWNPOUR_RAIN_SEED, RAIN_ROLE, WORLD, RAIN_DEPTH, AXIS_CONFIG, RIPPLE_FIELD, PALETTE, TUNING };
}
