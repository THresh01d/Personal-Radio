import * as THREE from 'three';

export function createSnowLayer(world, bands) {
    const count = 3600;
    const width = world.width * 2;
    const height = 13;
    const position = new Float32Array(count * 3);
    const flake = new Float32Array(count * 4);
    let seed = 592913;
    const random = () => {
        seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
        return seed / 4294967296;
    };
    for (let i = 0; i < count; i++) {
        // 深度与横向位置交错，降档只缩短同一批属性，仍留有远雪与少量近雪。
        const near = i % 31 === 0;
        const middle = i % 7 < 3;
        const x = (Math.floor(i / 2) * .61803398875 % 1 + random() * .003) % 1 * width * .5;
        const y = random() * height;
        const z = near ? 6 + random() * 6.8 : middle ? -7 + random() * 12.5 : -23 + random() * 15;
        const speed = near ? .75 + random() * .52 : middle ? .53 + random() * .47 : .35 + random() * .39;
        const variation = Math.pow(random(), 1.9);
        const size = near ? .077 + variation * .16 : middle ? .026 + variation * .078 : .019 + variation * .050;
        position.set([i % 2 ? x : -x, y, z], i * 3);
        flake.set([speed, size, random() * Math.PI * 2, random()], i * 4);
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(position, 3));
    geometry.setAttribute('aFlake', new THREE.BufferAttribute(flake, 4));
    const material = new THREE.ShaderMaterial({
        transparent: true,
        depthWrite: false,
        uniforms: {
            uTime: { value: 0 },
            uHeight: { value: 1 },
            uVolume: { value: new THREE.Vector2(width, height) },
            uBands: { value: bands },
        },
        vertexShader: `
            attribute vec4 aFlake;
            uniform float uTime, uHeight;
            uniform vec2 uVolume;
            uniform sampler2D uBands;
            varying float vAlpha, vFocus, vTilt, vShape;

            void main() {
                float falling = mod(position.y - uTime * aFlake.x, uVolume.y);
                float drift = sin(uTime * .61 + aFlake.z + falling * .43) * (.15 + aFlake.y * 2.2);
                float wind = uTime * (.42 + aFlake.x * .54) + drift;
                float x = mod(position.x + wind + uVolume.x * .5, uVolume.x) - uVolume.x * .5;
                float z = position.z + sin(uTime * .31 + aFlake.z) * aFlake.y * 2.5;
                vec4 view = modelViewMatrix * vec4(x, falling - 3.4, z, 1.0);
                gl_Position = projectionMatrix * view;

                float depth = max(-view.z, .1);
                float aperture = 1.0 - smoothstep(4.5, 11.5, depth);
                float rawSize = uHeight * projectionMatrix[1][1] * .5 * aFlake.y / depth;
                gl_PointSize = clamp(rawSize * (1.0 + aperture * .54), 1.2, 39.0);
                vFocus = (1.0 - aperture) * smoothstep(2.4, 5.0, gl_PointSize);
                vTilt = aFlake.z + .32 * uTime;
                vShape = aFlake.w;

                float fadeY = smoothstep(.0, .65, falling) * (1.0 - smoothstep(12.25, 13.0, falling));
                float fadeX = 1.0 - smoothstep(.41, .5, abs(x) / uVolume.x);
                float depthFade = smoothstep(.9, 4.7, depth) * (1.0 - .56 * smoothstep(14.0, 41.0, depth));
                float music = texture2D(uBands, vec2(clamp(x / (uVolume.x * .53) + .5, 0.0, 1.0), .5)).g;
                float softness = 1.0 - aperture * .50;
                // 镜头附近的大片雪较淡。音高只抬高中间一层的局部亮度。
                vAlpha = (.24 + aFlake.w * .39 + music * .18 * (1.0 - aperture))
                    * depthFade * fadeX * fadeY * softness;
            }
        `,
        fragmentShader: `
            varying float vAlpha, vFocus, vTilt, vShape;

            void main() {
                vec2 p = gl_PointCoord * 2.0 - 1.0;
                vec2 axis = vec2(cos(vTilt), sin(vTilt));
                vec2 flake = vec2(dot(p, axis), dot(p, vec2(-axis.y, axis.x)));
                flake.y *= 1.12 + vShape * .48;
                float radius = length(flake);
                float airy = exp(-radius * radius * 3.3) * (1.0 - smoothstep(.43, 1.0, radius));
                float grain = length(flake + vec2(flake.y * .16, -.09)) + flake.x * flake.y * .24;
                float focused = 1.0 - smoothstep(.16, .67, grain);
                float alpha = mix(airy, focused, vFocus * .8) * vAlpha;
                if (alpha < .008) discard;
                vec3 color = mix(vec3(.68, .74, .80), vec3(.86, .89, .92), vShape);
                gl_FragColor = vec4(color, alpha);
            }
        `,
    });
    const points = new THREE.Points(geometry, material);
    points.name = 'snow-night-particles';
    points.frustumCulled = false;
    return {
        points,
        resize(height, ratio, budget) {
            material.uniforms.uHeight.value = height * ratio;
            geometry.setDrawRange(0, budget < 6000 ? 1400 : budget < 11000 ? 2400 : count);
        },
        update(time) { material.uniforms.uTime.value = time; },
    };
}
