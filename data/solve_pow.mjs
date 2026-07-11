import { readFileSync } from "fs";

const wasmBytes = readFileSync(new URL("./sha3_wasm_bg.wasm", import.meta.url));
const wasmModule = await WebAssembly.instantiate(wasmBytes, {});

const { memory, wasm_solve, __wbindgen_add_to_stack_pointer, __wbindgen_export_0 } =
  wasmModule.instance.exports;

function encodeString(s) {
  const bytes = new TextEncoder().encode(s);
  const ptr = __wbindgen_export_0(bytes.length, 1);
  new Uint8Array(memory.buffer, ptr, bytes.length).set(bytes);
  return { ptr, len: bytes.length };
}

function solve(challenge, salt, difficulty) {
  const prefix = `${salt}_${difficulty}_`;
  const sp = __wbindgen_add_to_stack_pointer(-16);
  const ch = encodeString(challenge);
  const pf = encodeString(prefix);
  wasm_solve(sp, ch.ptr, ch.len, pf.ptr, pf.len, difficulty);
  const view = new DataView(memory.buffer);
  const status = view.getInt32(sp + 0, true);
  const answer = view.getFloat64(sp + 8, true);
  __wbindgen_add_to_stack_pointer(16);
  if (status === 0) return null;
  return answer;
}

// Read challenge from stdin
const input = readFileSync(0, "utf8").trim();
const { challenge, salt, difficulty } = JSON.parse(input);

const answer = solve(challenge, salt, difficulty);
process.stdout.write(JSON.stringify({ answer }));
