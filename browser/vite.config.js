import {defineConfig} from 'vite';

// Ship only the CP-SAT runtimes. The upstream loader also references six
// unrelated solver families, which would otherwise add over 100 MB.
function cpSatOnly() {
  return {
    name: 'ournotes-cp-sat-only', enforce: 'pre',
    transform(code, id) {
      if (!id.replaceAll('\\', '/').endsWith('/browser/runtime_loader.js')) return;
      const start = code.indexOf('const runtimeAssets = {');
      const end = code.indexOf('async function loadFactory', start);
      if (start < 0 || end < 0) throw new Error('OR-Tools runtime loader changed');
      const assets = `const runtimeAssets = {cp_sat_runtime: {
        jspi: {jsUrl: new URL('../wasm/cp_sat_runtime.js', import.meta.url).href,
               wasmUrl: new URL('../wasm/cp_sat_runtime.wasm', import.meta.url).href},
        asyncify: {jsUrl: new URL('../wasm/cp_sat_runtime_asyncify.js', import.meta.url).href,
                   wasmUrl: new URL('../wasm/cp_sat_runtime_asyncify.wasm', import.meta.url).href}
      }};\n`;
      return code.slice(0, start) + assets + code.slice(end);
    },
  };
}

export default defineConfig({
  base: './',
  plugins: [cpSatOnly()],
  worker: {format: 'es', plugins: () => [cpSatOnly()]},
  build: {target: 'es2022', assetsInlineLimit: 0},
});
