// Отдельный поток для нейросети дорисовки (LaMa): расчёт идёт секунды, главный поток не должен замирать.
// Принимает картинку 512×512 (0..1) и маску (1 — дорисовать), возвращает картинку 0..255.
const { parentPort, workerData } = require('worker_threads');
const fs = require('fs');
const { pathToFileURL } = require('url');

(async () => {
  const ort = await import(pathToFileURL(workerData.ortPath).href);
  ort.env.wasm.numThreads = workerData.threads;
  const session = await ort.InferenceSession.create(fs.readFileSync(workerData.modelPath), { executionProviders: ['wasm'] });
  parentPort.postMessage({ type: 'loaded' });
  const S = workerData.size;
  const result = await session.run({
    image: new ort.Tensor('float32', workerData.image, [1, 3, S, S]),
    mask: new ort.Tensor('float32', workerData.mask, [1, 1, S, S]),
  });
  const out = result[session.outputNames[0]].data;
  parentPort.postMessage({ type: 'done', data: out }, [out.buffer]);
})().catch((e) => parentPort.postMessage({ type: 'error', message: e?.message || String(e) }));
