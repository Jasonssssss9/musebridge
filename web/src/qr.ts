// Small QR encoder for the demo QR cards: byte mode, error correction level M,
// versions 1–6 (up to 106 bytes, plenty for an artwork URL). Written here so the
// LAN demo needs no extra package or online QR service.

// [data codewords per block, error-correction codewords per block, blocks], index = version
const SPEC: [number, number, number][] = [[0, 0, 0], [16, 10, 1], [28, 16, 1], [44, 26, 1], [32, 18, 2], [43, 24, 2], [27, 16, 4]];

function mul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}

function rsDivisor(degree: number): number[] {
  const r = new Array<number>(degree).fill(0);
  r[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      r[j] = mul(r[j], root);
      if (j + 1 < degree) r[j] ^= r[j + 1];
    }
    root = mul(root, 2);
  }
  return r;
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const r = divisor.map(() => 0);
  for (const b of data) {
    const factor = b ^ (r.shift() as number);
    r.push(0);
    divisor.forEach((c, i) => { r[i] ^= mul(c, factor); });
  }
  return r;
}

/** Returns the module grid (true = dark), without the quiet zone. Throws if the text is too long. */
export function qrMatrix(text: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(text));
  const version = SPEC.findIndex(([d, , n], v) => v > 0 && bytes.length <= d * n - 2);
  if (version < 0) throw new Error('Text is too long for this QR encoder (106 bytes at most).');
  const [perBlock, ecLen, blocks] = SPEC[version];
  const capacity = perBlock * blocks;

  // Mode 0100 (bytes), 8-bit length, data, terminator, then pad codewords.
  const bits: number[] = [];
  const push = (value: number, n: number) => { for (let i = n - 1; i >= 0; i--) bits.push((value >>> i) & 1); };
  push(4, 4);
  push(bytes.length, 8);
  bytes.forEach((b) => push(b, 8));
  push(0, Math.min(4, capacity * 8 - bits.length));
  while (bits.length % 8) bits.push(0);
  for (let pad = 0xec; bits.length < capacity * 8; pad ^= 0xec ^ 0x11) push(pad, 8);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));

  // Error correction per block, then interleave.
  const divisor = rsDivisor(ecLen);
  const dataBlocks: number[][] = [];
  const ecBlocks: number[][] = [];
  for (let b = 0; b < blocks; b++) {
    const d = data.slice(b * perBlock, (b + 1) * perBlock);
    dataBlocks.push(d);
    ecBlocks.push(rsRemainder(d, divisor));
  }
  const codewords: number[] = [];
  for (let i = 0; i < perBlock; i++) dataBlocks.forEach((d) => codewords.push(d[i]));
  for (let i = 0; i < ecLen; i++) ecBlocks.forEach((e) => codewords.push(e[i]));

  const size = 17 + 4 * version;
  const m = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const fn = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const set = (x: number, y: number, dark: boolean) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return;
    m[y][x] = dark;
    fn[y][x] = true;
  };

  // Timing patterns, finder patterns with separators, alignment pattern.
  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const d = Math.max(Math.abs(dx), Math.abs(dy));
      set(cx + dx, cy + dy, d !== 2 && d !== 4);
    }
  }
  if (version >= 2) {
    const c = size - 7;
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(c + dx, c + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }

  // Format information for level M with mask 0 (a fixed 15-bit value), in both copies.
  const format = 0x5412;
  const bit = (i: number) => ((format >>> i) & 1) === 1;
  for (let i = 0; i <= 5; i++) set(8, i, bit(i));
  set(8, 7, bit(6)); set(8, 8, bit(7)); set(7, 8, bit(8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
  set(8, size - 8, true);

  // Data in the zigzag order, with mask 0 applied: flip where (x + y) is even.
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (fn[y][x]) continue;
        const dark = i < codewords.length * 8 && ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
        i++;
        m[y][x] = dark !== ((x + y) % 2 === 0);
      }
    }
  }
  return m;
}

/** SVG path data for the grid, offset by a 4-module quiet zone. */
export function qrPath(matrix: boolean[][]): string {
  let d = '';
  matrix.forEach((row, y) => row.forEach((dark, x) => { if (dark) d += `M${x + 4} ${y + 4}h1v1h-1z`; }));
  return d;
}
