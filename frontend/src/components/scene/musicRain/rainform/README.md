# 音乐雨幕的 Rainform 渲染移植

Required Notice: Rainform / 数据成雨 © 2026 afterimage — https://rainform.pages.dev/

来源：[afterimage-lab/Rainform](https://github.com/afterimage-lab/Rainform)，2026-10-16 读取的 `main/src/main.js`。
本目录中 `rendering.js`、`shaders.js`、`config.js` 改编自原作；适用随附的 **PolyForm Noncommercial 1.0.0**，
并非将原作重新许可为 MIT。原始许可及通知全文保留在本目录。Three.js 仍适用其自身的 MIT 许可。

保留：确定性雨链布局、珍珠与雨丝液态金属着色器、低位薄雾、共享强度纹理的水面倒影、GPU 双缓冲波动高度场。
原作默认关闭的峰值瀑布体积层和飞溅粒子层没有引入。

修改：删除坐标轴、读数、雨量编辑器、调参面板、原站全局 DOM/存储操作及雨声音频。
交互相机由上层 `orbit.ts` 接回 Three.js OrbitControls，保留拖动/缩放/惯性并隔离播放器控件事件；
暂停或静谧时允许手动观察，窗口/档位变化保留视角，双击或复位按钮回到正面。
固定雨串容量，沿频率位置连续映射音乐强度；每帧原地修改高度、透明度、速度及强度纹理，不重建几何。
仅明确暂停时让现有雨滴落水且不重生，落完后隐藏雨串、保留水面；恢复原雨串位置后继续原播放算法。
该暂停分支不修改雾带、水面材质、倒影、GPU衰减参数、初始雨串布局或相机。
仅性能档位变化时限制主雨串工作集，主雨串的均匀分布保证低中高频仍全部可见。

- `config.js`：实例独立的原作材质参数和世界尺度。
- `shaders.js`：原作雨珠、连接雨丝、水面、薄雾和波动模拟着色器。
- `rendering.js`：原作几何和落水算法，以及音乐输入、尺寸与释放接口。
- `rendering.d.ts`：供应用侧 TypeScript 使用的窄接口。
- 上层 `signal.ts`、`scene.ts` 和 `useMusicRain.ts`：音频门控、平滑、三档视觉、懒加载及生命周期。

这是 Personal-Radio 的非商业集成，不是 Rainform 官方版本。商业使用需另外核对原作许可。

2026-11-17 圣诞场景改用用户提供的 `preview/christmas-snow-night/src` 原稿。
`winterVillage/` 保存程序化树枝、小屋、地形及纹理，`batch.js` 仅对静止不透明部件合批；
`christmas.js` 负责原版光照、预算和播放器频谱门控。未复制原稿的 Three 运行库、页面 UI、
模拟频谱或第二套 renderer；程序纹理统一不大于 512×512。原版月影改为同一 renderer 内
512×512 的一次缓存，播放和拖动时不重新绘制阴影。原雨幕与雪夜算法继续保留，圣诞退出后
恢复其色调映射、雾与可见性。
