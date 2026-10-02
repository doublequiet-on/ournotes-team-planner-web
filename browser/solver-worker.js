import {CpSat, setWorkerBridgeEnabled} from 'or-tools-wasm/cp-sat';
import protobuf from 'protobufjs';
import Long from 'long';

// Preserve int64 values both when encoding constraints and decoding solutions.
protobuf.util.Long = Long;
protobuf.configure();
setWorkerBridgeEnabled(false);
const encoder = new TextEncoder();
let schemas;

function finish(shared, value) {
  const control = new Int32Array(shared, 0, 2);
  const bytes = encoder.encode(JSON.stringify(value));
  if (bytes.byteLength > shared.byteLength - 8) {
    finish(shared, {error: '求解结果超过本次缓冲区，计算已停止。'});
    return;
  }
  new Uint8Array(shared, 8, bytes.byteLength).set(bytes);
  Atomics.store(control, 1, bytes.byteLength);
  Atomics.store(control, 0, 1);
  Atomics.notify(control, 0);
}

async function loadSchemas() {
  if (!schemas) schemas = (async () => {
    const raw = await CpSat.getSchemas();
    const root = protobuf.parse(raw.cp_model).root;
    return {model: root.lookupType('operations_research.sat.CpModelProto'),
            response: root.lookupType('operations_research.sat.CpSolverResponse')};
  })();
  return schemas;
}

self.onmessage = async event => {
  const {raw, shared} = event.data;
  try {
    const types = await loadSchemas();
    const request = JSON.parse(raw);
    const message = types.model.fromObject(request.model);
    const issue = types.model.verify(message);
    if (issue) throw new Error(issue);
    const encoded = types.model.encode(message).finish();
    const validation = await CpSat.validate(encoded);
    if (!validation.ok) throw new Error('CP-SAT 模型校验失败：' + validation.message);
    const bytes = await CpSat.solveRaw(encoded, await encodeParams(request.parameters));
    const response = types.response.toObject(types.response.decode(bytes), {longs: String, enums: Number, defaults: true});
    finish(shared, {status: response.status, solution: response.solution, solutionInfo: response.solutionInfo});
  } catch (error) {
    finish(shared, {error: '浏览器求解失败：' + error.message});
  }
};

let parameterType;
async function encodeParams(parameters) {
  if (!parameterType) {
    const raw = await CpSat.getSchemas();
    parameterType = protobuf.parse(raw.sat_parameters).root.lookupType('operations_research.sat.SatParameters');
  }
  const {numSearchWorkers, ...remaining} = parameters;
  const message = parameterType.fromObject({...remaining, numWorkers: numSearchWorkers});
  return parameterType.encode(message).finish();
}
