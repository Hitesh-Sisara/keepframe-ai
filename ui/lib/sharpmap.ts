// Sharpness heat-map: the variance of the Laplacian (the same measure the worker scores with), per block of the
// proxy image. Computed in the browser from the image's pixels; the API exposes only the whole-frame number.

export interface SharpMap {
  cols: number;
  rows: number;
  /** 0..1 per cell, row-major, on an absolute scale so a soft frame shows little heat next to a sharp one. */
  cells: Float32Array;
  /** Fraction of cells that read as sharp (>= 0.5). */
  sharpShare: number;
}

/** Variance of the 4-neighbour Laplacian per `block`-px cell of an RGBA image. Log-scaled between `lo` and `hi`
 *  (variance on 0-255 grey), which spans "visibly soft" to "crisp" on a ~1000-px proxy. */
export function sharpMap(rgba: Uint8ClampedArray, w: number, h: number, block = 24, lo = 30, hi = 1200): SharpMap {
  const g = new Float32Array(w * h);
  for (let i = 0, p = 0; i < g.length; i++, p += 4) g[i] = 0.299 * rgba[p] + 0.587 * rgba[p + 1] + 0.114 * rgba[p + 2];
  const cols = Math.max(1, Math.floor(w / block));
  const rows = Math.max(1, Math.floor(h / block));
  const cells = new Float32Array(cols * rows);
  const lLo = Math.log(lo);
  const span = Math.log(hi) - lLo;
  let sharp = 0;
  for (let cy = 0; cy < rows; cy++)
    for (let cx = 0; cx < cols; cx++) {
      let s = 0;
      let s2 = 0;
      let n = 0;
      const y0 = Math.max(1, cy * block);
      const y1 = Math.min(h - 1, (cy + 1) * block);
      const x0 = Math.max(1, cx * block);
      const x1 = Math.min(w - 1, (cx + 1) * block);
      for (let y = y0; y < y1; y++)
        for (let x = x0; x < x1; x++) {
          const i = y * w + x;
          const v = g[i - 1] + g[i + 1] + g[i - w] + g[i + w] - 4 * g[i];
          s += v;
          s2 += v * v;
          n++;
        }
      const variance = n ? s2 / n - (s / n) ** 2 : 0;
      const t = variance <= lo ? 0 : Math.min(1, (Math.log(variance) - lLo) / span);
      cells[cy * cols + cx] = t;
      if (t >= 0.5) sharp++;
    }
  return { cols, rows, cells, sharpShare: sharp / (cols * rows) };
}

/** Heat colour for one cell: transparent when soft, then blue → cyan → yellow as detail rises (focus peaking). */
export function heatRGBA(t: number): [number, number, number, number] {
  if (t < 0.2) return [0, 0, 0, 0];
  const stops: [number, [number, number, number]][] = [
    [0.2, [40, 90, 255]],
    [0.55, [0, 220, 230]],
    [1, [255, 236, 60]],
  ];
  let k = 1;
  while (k < stops.length - 1 && t > stops[k][0]) k++;
  const [t0, c0] = stops[k - 1];
  const [t1, c1] = stops[k];
  const f = Math.max(0, Math.min(1, (t - t0) / (t1 - t0)));
  const c = c0.map((v, i) => Math.round(v + (c1[i] - v) * f)) as [number, number, number];
  return [c[0], c[1], c[2], Math.round(70 + 120 * t)];
}
