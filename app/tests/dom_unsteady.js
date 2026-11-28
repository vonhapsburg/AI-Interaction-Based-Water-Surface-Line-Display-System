/**
 * 非恒定页脚本的运行时冒烟测试：
 * 在 Node 里用最小 DOM/Canvas 桩执行 unsteady.html 的主脚本，
 * 然后用一份合成结果依次调用四个绘图函数，任何 ReferenceError 都会被抓到。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const htmlPath = path.join(__dirname, "..", "web", "unsteady.html");
const html = fs.readFileSync(htmlPath, "utf8");
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const main = scripts[scripts.length - 1];

function makeCtx() {
  return new Proxy({}, {
    get(t, p) {
      if (p === "canvas") return { width: 900, height: 300 };
      if (p === "measureText") return () => ({ width: 20 });
      if (p === "getImageData") return () => ({ data: [] });
      if (p in t) return t[p];
      return () => {};
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

function makeEl(id) {
  const el = {
    id: id || "",
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    dataset: {},
    addEventListener() {},
    removeEventListener() {},
    appendChild() {},
    removeChild() {},
    parentElement: null,
    closest() { return makeEl(); },
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    getContext() { return makeCtx(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 300 }; },
    scrollTo() {},
    focus() {},
    width: 900, height: 300, clientWidth: 900, clientHeight: 300,
    value: "", textContent: "", innerHTML: "",
  };
  el.parentElement = el;
  return el;
}

const documentStub = {
  querySelector(sel) { return makeEl(sel); },
  querySelectorAll() { return []; },
  getElementById() { return makeEl(); },
  createElement() { return makeEl(); },
  addEventListener() {},
  body: makeEl("body"),
  head: makeEl("head"),
};

const sandbox = {
  document: documentStub,
  window: { addEventListener() {}, devicePixelRatio: 1, innerWidth: 1280, innerHeight: 900 },
  console,
  performance: { now: () => 0 },
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: (fn) => { try { fn(); } catch (e) { throw e; } return 0; },
  requestAnimationFrame: (fn) => { fn(0); return 0; },
  fetch: async () => ({ json: async () => ({}), text: async () => "{}", ok: true, status: 200 }),
  localStorage: { getItem: () => null, setItem: () => {} },
  URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let failed = 0;
function check(name, fn) {
  try {
    fn();
    console.log("✓ " + name);
  } catch (e) {
    failed++;
    console.log("✗ " + name + "  —— " + e.name + ": " + e.message);
  }
}

check("脚本可以执行（加载期不抛异常）", () => {
  vm.runInContext(main, sandbox, { filename: "unsteady.js" });
});

// 合成一份结果
const Nx = 11;
const x = Array.from({ length: Nx }, (_, i) => i * 100);
const h0 = 1.124, amp = 0.3;
const f1 = x.map((_, i) => h0 + amp * Math.sin(Math.PI * i / (Nx - 1)));
const f2 = x.map((_, i) => h0 + amp * 0.6 * Math.sin(Math.PI * i / (Nx - 1)));
const R = {
  model: "diffusion",
  x,
  t: [0, 900, 1800],
  t_snap_hours: [0, 0.25, 0.5],
  h_snap: [f1, f2],
  stations: [0, 0.25, 0.5, 0.75, 1].map((f, k) => ({
    x: f * 1000,
    Q: [10, 20, 15],
    h: [h0, h0 + 0.2, h0 + 0.1],
    peak_Q: 20 - k,
    peak_t: 900 + k * 60,
  })),
  diagnostics: {
    dt: 1.2, steps: 1200, dx: 100, max_courant: 0.4, mass_err: 0.0001,
    c_k_h0: 2.6, c_k_peak: 3.2, c_k_measured: 3.0,
    boundary_peak_err: 0.001, h_dn_used: 1.124, h_dn_is_normal: true,
    newton_iters: 0, newton_failed: 0, theta: 0.7, psi: 0.5, nx: Nx,
    auto_coarsened: false,
  },
  meta: {
    L: 1000, b: 5, m: 0, n: 0.015, i0: 0.001, Q0: 10, Qpeak: 30,
    t_peak: 3600, t_base: 7200, h_n: h0, h_dn: 1.124, c_k: 2.6,
    model: "diffusion", b: 5,
  },
};

check("drawFamily（含包络带）", () => { sandbox.drawFamily(R, 0); });
check("drawHydro", () => { sandbox.drawHydro(R); });
check("drawLoop（含引线标签）", () => { sandbox.drawLoop(R); });
check("drawDiag", () => { sandbox.drawDiag(R); });
check("drawGate（堰分支）", () => {
  const Rw = JSON.parse(JSON.stringify(R));
  Rw.structures = [{type: "weir", x: 600, b_w: 5, C: 0.35, z_s: 0.5,
    t: [0, 900, 1800], Q: [8, 12, 10], hu: [3.6, 3.8, 3.7],
    hd: [1.4, 1.5, 1.45], a: [0, 0, 0],
    H0_hist: [3.1, 3.3, 3.2], sig_hist: [1, 1, 1],
    reg_hist: ["free", "free", "free"], regime: "free", sigma: 1, H0: 3.2}];
  Rw.gate = {on: false};
  sandbox.drawGate(Rw);
});
check("drawWeirRate", () => {
  const Rw = JSON.parse(JSON.stringify(R));
  Rw.structures = [{type: "weir", x: 600, b_w: 5, C: 0.35, z_s: 0.5,
    Q: [8, 12, 10], H0_hist: [3.1, 3.3, 3.2], sig_hist: [1, 1, 1],
    reg_hist: ["free", "free", "free"]}];
  sandbox.drawWeirRate(Rw);
});
check("drawWeirZoom", () => {
  const Rw = JSON.parse(JSON.stringify(R));
  Rw.structures = [{type: "weir", x: 500, b_w: 5, C: 0.35, z_s: 0.5,
    H0: 3.2, regime: "free"}];
  sandbox.drawWeirZoom(Rw);
});
check("drawGate（含闸门）", () => {
  sandbox.drawGate(Object.assign({}, R, { gate: {
    on: true, x: 500, b_g: 5, z_s: 0, mu: 0.62,
    t: [0, 1800, 3600], a: [1, 0.8, 0.5],
    Q: [10, 8, 6], hu: [2.0, 2.6, 3.4], hd: [1.4, 1.5, 1.6],
  }}));
});
check("drawCharChart（缓流）", () => {
  sandbox.drawCharChart({
    regime: "subcritical", L: 1000, t_end: 1300, theory_up: 650,
    points: [{ x: 0, arrival: 600 }, { x: 500, arrival: 300 }, { x: 1000, arrival: 0 }],
  });
});
check("drawCharChart（急流）", () => {
  sandbox.drawCharChart({ regime: "supercritical", L: 1000, Fr: 2.2, c_minus_v: -2.7, points: [] });
});
check("drawBackwater", () => {
  sandbox.drawBackwater({ x, h: f1, hn: h0 });
});
check("switchSchedMode", () => { sandbox.switchSchedMode("opt"); });
check("setPlanSummary", () => {
  sandbox.setPlanSummary({name:"方案A", a_end:0.6, peak:21.0, cut:0.25,
                          hmax:5.6, feasible:true}, {max_stage: 6.0, source: "test"});
});
check("renderReply（【】标红与注意块）", () => {
  const el = makeEl();
  sandbox.renderReply(el, "结论是【缓流可上溯】。\n注意：急流时不行。");
  if (!/class="hl"/.test(el.innerHTML)) throw new Error("【】未标红");
  if (!/class="warn"/.test(el.innerHTML)) throw new Error("注意块未渲染");
});
check("escHtml 转义", () => {
  const out = sandbox.escHtml('<b>&"');
  if (out.indexOf("<b>") >= 0) throw new Error("未转义");
});

console.log(failed ? `\n失败 ${failed} 项` : "\n全部通过");
process.exit(failed ? 1 : 0);
