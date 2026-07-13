// 端到端测试 - 同步版本
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('/workspace/output/index.html', 'utf8');
// 找主业务 script (最长的那个不含 src 的)
const scripts = [...html.matchAll(/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/g)];
const code = scripts.map(m => m[1]).sort((a, b) => b.length - a.length)[0];

const data = JSON.parse(fs.readFileSync('/workspace/edge-solution/data/prices.json', 'utf8'));

const sandbox = {
  console,
  document: {
    getElementById: () => ({ classList:{add:()=>{},remove:()=>{}}, addEventListener:()=>{}, querySelectorAll:()=>[], querySelector:()=>null, dataset:{}, innerHTML:'', textContent:'', style:{}, title:'', removeAttribute:()=>{}, getContext:()=>({createLinearGradient:()=>({addColorStop:()=>{}}),clearRect:()=>{},beginPath:()=>{},arc:()=>{},fill:()=>{},moveTo:()=>{},lineTo:()=>{},stroke:()=>{},closePath:()=>{},fillRect:()=>{}}) }),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => ({ classList:{add:()=>{},remove:()=>{}}, addEventListener:()=>{}, querySelectorAll:()=>[], dataset:{}, innerHTML:'', textContent:'', style:{}, title:'', removeAttribute:()=>{}, getContext:()=>({createLinearGradient:()=>({addColorStop:()=>{}})}) }),
  },
  window: { addEventListener: () => {}, PRICE_API_URL: 'data://mock', __EMBEDDED_PRICES: data },
  localStorage: { _s:{}, getItem(k){return this._s[k]||null;}, setItem(k,v){this._s[k]=v;}, removeItem(k){delete this._s[k];} },
  fetch: async () => ({ ok: true, json: async () => data, text: async () => JSON.stringify(data) }),
  performance: { now: ()=>Date.now() },
  requestAnimationFrame: (cb)=>0,
  IntersectionObserver: class { observe(){} unobserve(){} disconnect(){} },
  setInterval: () => 0,
  setTimeout: (cb,ms)=>0,
  Chart: class { constructor(){} destroy(){} },
};
vm.createContext(sandbox);

// 同步测试代码: 跳过 fetch, 直接调用 applyPriceData
const testCode = code + `
// 使用嵌入模式 (window.__EMBEDDED_PRICES)
applyPriceData(window.__EMBEDDED_PRICES, 'embedded');
DATA_SOURCE = 'embedded';
DATA_UPDATED_AT = window.__EMBEDDED_PRICES.updatedAt;

// 跑一次预测
computeForecast('pork');
computeForecast('veggie');
computeForecast('gas92');

globalThis.__test = {
  DATA_SOURCE,
  porkCurrent: PRICE_DB.pork.current,
  porkHistoryLen: PRICE_DB.pork.history.length,
  porkHistoryFirst3: PRICE_DB.pork.history.slice(0,3),
  porkHistoryLast3: PRICE_DB.pork.history.slice(-3),
  porkMom: PRICE_DB.pork.mom,
  porkYoy: PRICE_DB.pork.yoy,
  porkPredict: PRICE_DB.pork.predict,
  porkConfidence: PRICE_DB.pork.confidence,
  porkChangeRate: PRICE_DB.pork.changeRate,
  veggieCurrent: PRICE_DB.veggie.current,
  veggiePredict: PRICE_DB.veggie.predict,
  veggieConfidence: PRICE_DB.veggie.confidence,
  elecCurrent: PRICE_DB.elec.current,
  gas92Current: PRICE_DB.gas92.current,
  catCount: CATEGORIES.length,
  dbCount: Object.keys(PRICE_DB).length,
  hasDistricts: typeof window.__EMBEDDED_PRICES.districts !== 'undefined',
  districts: window.__EMBEDDED_PRICES.districts,
};
`;

vm.runInContext(testCode, sandbox);

if(sandbox.__test){
  console.log('✅ 真实数据接入成功');
  console.log(JSON.stringify(sandbox.__test, null, 2));
  if(sandbox.__test.porkHistoryLen >= 14){
    console.log('✅ history 长度足够(>=14) 用于预测');
  }
} else {
  console.log('❌ 失败');
}
