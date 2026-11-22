// DOM 桩冒烟测试：在 Node 里执行页面脚本，验证真实计算结果能画出来。
// 沙箱下无头浏览器起不来（命名管道被禁），用最小 DOM 桩代替。
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const PAGE = path.join(__dirname, 'page.js');
const BASE = 'http://127.0.0.1:8000';

const calls = [];
function makeCtx() {
  return new Proxy({}, {
    get(t, p) {
      if (p in t) return t[p];
      if (p === 'measureText') return (s) => ({ width: String(s).length * 7 });
      return (...args) => { calls.push([p, args.length]); };
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

function makeEl(sel) {
  const ctx = makeCtx();
  const el = {
    sel, style: {}, dataset: {}, width: 0, height: 0,
    clientWidth: 900, clientHeight: 380,
    innerHTML: '', textContent: '', value: '', disabled: false,
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    appendChild() {}, remove() {}, focus() {},
    addEventListener(ev, fn) { (el._ev = el._ev || {})[ev] = fn; },
    querySelectorAll() { return []; },
    getContext() { return ctx; },
    getBoundingClientRect() { return { left: 0, top: 0, width: 900, height: 380 }; },
    scrollTop: 0, scrollHeight: 0,
  };
  return el;
}

const els = new Map();
function getEl(sel) {
  if (!els.has(sel)) els.set(sel, makeEl(sel));
  return els.get(sel);
}

global.document = {
  querySelector: getEl,
  getElementById: getEl,
  createElement: (tag) => makeEl(tag),
  createTextNode: (t) => ({ nodeValue: t, textContent: t }),
  querySelectorAll: () => [],
  body: makeEl('body'),
};
global.window = { devicePixelRatio: 1, addEventListener() {} };
global.location = { search: '' };
global.URLSearchParams = URLSearchParams;
global.URLParams = URLSearchParams;
// 保留真正的 URL 类（Node 的 fetch 依赖它），只补两个静态方法
try {
  URL.createObjectURL = () => 'blob:x';
  URL.revokeObjectURL = () => {};
} catch (e) { /* 只读时忽略 */ }
global.Blob = global.Blob || class { constructor() {} };
global.navigator = {};

const realFetch = global.fetch;
global.fetch = async (url, opts) => {
  const u = String(url);
  if (u.startsWith('/api/health')) {
    return { ok: true, status: 200, json: async () => ({ llm: true, ok: true }) };
  }
  return realFetch(u.startsWith('/') ? BASE + u : u, opts);
};

// 读取页面脚本并附加导出钩子（同作用域才能访问 state 与内部函数）
let src = fs.readFileSync(PAGE, 'utf-8');
src += `
;globalThis.__hook = {
  drawProfile, drawSection, renderResult, renderLegal,
  setResult: (v) => { state.result = v; },
  setSelectedX: (v) => { state.selectedX = v; },
  setForm: (f) => { Object.assign(state.form, f); },
  getState: () => state,
};`;

try {
  (0, eval)(src);
} catch (e) {
  console.error('页面脚本执行失败：', e.message);
  process.exit(1);
}

const h = globalThis.__hook;
if (!h) { console.error('未拿到钩子'); process.exit(1); }

async function solve(payload) {
  const res = await fetch(BASE + '/api/solve', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return res.json();
}

(async () => {
  // 等页面 init()（健康检查 / 预设 / 会话 / 首次计算）跑完，避免它覆盖测试结果
  await new Promise(r => setTimeout(r, 800));
  const results = [];

  const cases = [
    ['单段缓坡 M1', { overrides: { Q: 10, reaches: [{ L: 600, i: 0.001, b: 5, m: 0, n: 0.015 }],
      downstream: 'given_h', h0: 2.0 } }],
    ['陡转缓含水跃', { overrides: { Q: 10,
      reaches: [{ L: 100, i: 0.05, b: 5, m: 0, n: 0.015 },
                { L: 500, i: 0.001, b: 5, m: 0, n: 0.015 }],
      upstream: 'given_h', up_h: 0.30, downstream: 'given_h', h0: 1.00 } }],
  ];

  for (const [label, payload] of cases) {
    const j = await solve(payload);
    if (!j.ok) { results.push([label, 'FAIL', '接口返回 ok=false: ' + JSON.stringify(j.legal || {}).slice(0, 120)]); continue; }
    h.setResult(j.result);
    h.setSelectedX(150);
    let err = null, stage = '';
    calls.length = 0;
    try {
      stage = 'drawProfile'; h.drawProfile();
      stage = 'renderResult'; h.renderResult();
      stage = 'renderLegal'; h.renderLegal(j.legal);
      stage = 'drawSection'; await h.drawSection();
    } catch (e) { err = `${stage}: ${e.message}`; }
    if (err) { results.push([label, 'FAIL', err]); continue; }
    const drew = calls.filter(c => c[0] === 'lineTo').length;
    const texts = calls.filter(c => c[0] === 'fillText').length;
    const types = j.result.segments.map(s => s.curve_type).join('→');
    const props = els.get('#propsTable');
    results.push([label, 'OK',
      `${types}，lineTo ${drew} 次，文字 ${texts} 处，断面表${props && props.innerHTML ? '已填' : '空'}`]);
  }

  // 梯形断面：确认水面宽大于底宽（几何真的是梯形，不是矩形）
  {
    const j = await solve({ overrides: { Q: 12,
      reaches: [{ L: 800, i: 0.0008, b: 6, m: 1.5, n: 0.022 }],
      downstream: 'given_h', h0: 2.2 } });
    if (!j.ok) {
      results.push(['梯形断面几何', 'FAIL', '接口失败']);
    } else {
      h.setResult(j.result); h.setSelectedX(400);
      calls.length = 0;
      await h.drawSection();
      const sec = await (await fetch(BASE + '/api/section?x=400')).json();
      const b = j.result.reaches[0].b, m = j.result.reaches[0].m;
      const wantB = b + 2 * m * sec.h;
      const okB = Math.abs(sec.B - wantB) < 1e-6 && sec.B > b;
      const drew = calls.filter(c => c[0] === 'lineTo').length;
      results.push(['梯形断面几何', okB && drew > 8 ? 'OK' : 'FAIL',
        `b=${b} m=${m} h=${sec.h.toFixed(3)} → B=${sec.B.toFixed(3)}（应为 ${wantB.toFixed(3)}，>b 说明是梯形）, lineTo ${drew} 次`]);
    }
  }

  // 异常路径
  try {
    h.renderLegal({ code: 'G', label: '非法 · 参数越界', verdict: 'reject',
      reasons: ['糙率=0.5 超出上限 0.3'], hints: ['糙率 ≤ 0.3'] });
    h.renderLegal(null);
    h.setResult(null);
    h.drawProfile();
    results.push(['异常路径（G / 无结果）', 'OK', '不抛异常']);
  } catch (e) { results.push(['异常路径（G / 无结果）', 'FAIL', e.message]); }

  let ok = 0;
  const lines = [];
  for (const [label, st, msg] of results) {
    if (st === 'OK') ok++;
    lines.push(`${st === 'OK' ? '✅' : '❌'} ${label} —— ${msg}`);
  }
  lines.push('');
  lines.push(`通过 ${ok} / ${results.length}`);
  fs.writeFileSync(path.join(__dirname, 'dom-smoke.txt'), lines.join('\n') + '\n', 'utf-8');
  console.log(lines.join('\n'));
})();
