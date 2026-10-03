import {version as browserVersion} from './package.json';

let pyodide, cancel, started = 0, lastPersist = 0, lastProgress = {};
const decoder = new TextDecoder();
let ready;
let persistStats = {snapshots: 0, snapshot_bytes: 0, snapshot_copy_ms: 0};

self.browser_cancelled = () => cancel ? Atomics.load(cancel, 0) !== 0 : false;
self.browser_progress = raw => {
  lastProgress = JSON.parse(raw);
  self.postMessage({type: 'progress', changes: lastProgress});
};
self.browser_persist = force => {
  const now = performance.now();
  if (!force && now - lastPersist < 500) return;
  lastPersist = now;
  const bytes = pyodide.FS.readFile('/state/search-v1.sqlite3');
  persistStats.snapshots += 1;
  persistStats.snapshot_bytes += bytes.byteLength;
  persistStats.snapshot_copy_ms += performance.now() - now;
  self.postMessage({type: 'persist', bytes: bytes.buffer}, [bytes.buffer]);
};
self.browser_solve = (raw, count) => {
  if (!Number.isSafeInteger(count) || count < 0) throw new Error('求解模型变量计数无效。');
  const shared = new SharedArrayBuffer(Math.max(1048576, count * 32 + 4096));
  const control = new Int32Array(shared, 0, 2);
  self.postMessage({type: 'solve', raw, shared});
  let lastHeartbeat = 0;
  while (!Atomics.load(control, 0)) {
    if (self.browser_cancelled()) return '{"cancelled":true}';
    Atomics.wait(control, 0, 0, 250);
    if (performance.now() - lastHeartbeat >= 1000) {
      lastHeartbeat = performance.now();
      self.postMessage({type: 'progress', changes: {...lastProgress,
        elapsed_seconds: Math.round((performance.now() - started) / 100) / 10}});
    }
  }
  return decoder.decode(new Uint8Array(shared, 8, Atomics.load(control, 1)).slice());
};

async function initialize({baseURL, stored}) {
  self.postMessage({type: 'loading', text: '正在下载并准备计算组件，首次打开请稍候…'});
  const indexURL = baseURL + 'vendor/pyodide/';
  const {loadPyodide} = await import(/* @vite-ignore */ indexURL + 'pyodide.mjs');
  pyodide = await loadPyodide({indexURL});
  self.postMessage({type: 'loading', text: '正在核对游戏数据与公式…'});
  const response = await fetch(baseURL + 'planner-runtime.zip?v=' + encodeURIComponent(browserVersion));
  if (!response.ok) throw new Error('计算资料加载失败，请刷新网页。');
  const bytes = new Uint8Array(await response.arrayBuffer());
  pyodide.unpackArchive(bytes, 'zip', {extractDir: '/planner'});
  pyodide.FS.mkdirTree('/state');
  if (stored) pyodide.FS.writeFile('/state/search-v1.sqlite3', new Uint8Array(stored));
  pyodide.runPython("import sys; sys.path.insert(0, '/planner'); import browser_runtime");
  self.postMessage({type: 'loading', text: ''});
}

self.onmessage = async event => {
  const message = event.data;
  if (message.type === 'initialize') {
    ready = initialize(message);
    try {await ready; self.postMessage({type: 'reply', id: message.id, value: true});}
    catch (error) {self.postMessage({type: 'reply', id: message.id, error: error.message});}
    return;
  }
  try {
    await ready;
    if (message.method === 'restore-state') {
      pyodide.runPython('browser_runtime.close_cache()');
      try {pyodide.FS.unlink('/state/search-v1.sqlite3');} catch {}
      if (message.stored) pyodide.FS.writeFile('/state/search-v1.sqlite3', new Uint8Array(message.stored));
      const cacheReset = pyodide.runPython('browser_runtime.restore_cache()');
      self.postMessage({type: 'reply', id: message.id, value: {cacheReset}});
      return;
    }
    cancel = message.cancel ? new Int32Array(message.cancel) : null;
    if (message.method === 'optimize') {
      started = performance.now(); lastProgress = {};
      persistStats = {snapshots: 0, snapshot_bytes: 0, snapshot_copy_ms: 0};
    }
    pyodide.globals.set('_browser_method', message.method);
    pyodide.globals.set('_browser_body', JSON.stringify(message.body || {}));
    pyodide.globals.set('_browser_job_id', message.jobId || null);
    const raw = pyodide.runPython('browser_runtime.invoke(_browser_method, _browser_body, _browser_job_id)');
    const value = JSON.parse(raw);
    if (value.result) value.result.search.snapshot_diagnostics = {...persistStats};
    self.postMessage({type: 'reply', id: message.id, value});
  } catch (error) {
    self.postMessage({type: 'reply', id: message.id, error: error.message});
  }
};
