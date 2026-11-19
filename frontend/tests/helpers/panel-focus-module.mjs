import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
const require = createRequire(import.meta.url);
const ts = require("typescript");
const compiled = ts.transpileModule(readFileSync(new URL("../../src/composables/usePanelFocus.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

// 组件与单元回归执行同一生产逻辑，仅文档/节点边界由宿主提供。
export function panelFocusModule(vue, document, getComputedStyle = () => ({ visibility: "visible" })) {
  const exports = {};
  runInNewContext(compiled, { exports, document, getComputedStyle, require: id => {
    if (id === "vue") return vue;
    throw new Error(`Unexpected focus dependency ${id}`);
  } });
  return exports;
}
