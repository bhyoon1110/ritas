// Display-only, second-order SNIP-style background clipping (decreasing window).
// Algorithm reference: https://root.cern.ch/doc/master/classTSpectrum.html
// This is an estimate for guide-line feet, NOT background subtraction or a
// quantitative fit. Never write these values into the original measurement.
const backgroundCache = new WeakMap();

export function estimateBackground(measurement) {
  const {x, y} = measurement;
  const cached = backgroundCache.get(measurement);
  // Project measurements are immutable; a newly loaded/replaced RAW invalidates
  // the cache. Zoom, stacking, phase visibility and exports reuse the same result.
  if (cached && cached.x === x && cached.y === y && cached.length === y.length)
    return cached.values;
  let values = y.slice();
  const count = y.length;
  if (count >= 3) {
    const span = x[count - 1] - x[0];
    // Use physical X distance, not a channel count: uneven sampling or a finer
    // acquisition step must not change the background's characteristic width.
    // For very short inputs retain at least two typical sample intervals.
    const halfWindow = Math.min(span / 2, Math.max(span * 0.02, 2 * span / (count - 1)));
    let next = new Array(count);
    // Bounded O(32*N) work and O(N) memory, including 250,000-point projects.
    for (let pass = 32; pass >= 1; pass--) {
      const width = halfWindow * pass / 32;
      let left = 0, right = 0;
      for (let i = 0; i < count; i++) {
        const lo = x[i] - width, hi = x[i] + width;
        // Do not invent measurements outside the acquired range.
        if (lo < x[0] || hi > x[count - 1]) {
          next[i] = values[i];
          continue;
        }
        while (left + 1 < count - 1 && x[left + 1] < lo) left++;
        while (right + 1 < count - 1 && x[right + 1] < hi) right++;
        const low = values[left] + (values[left + 1] - values[left])
          * (lo - x[left]) / (x[left + 1] - x[left]);
        const high = values[right] + (values[right + 1] - values[right])
          * (hi - x[right]) / (x[right + 1] - x[right]);
        next[i] = Math.min(values[i], low / 2 + high / 2);
      }
      const previous = values;
      values = next;
      next = previous;
    }
  }
  backgroundCache.set(measurement, {x, y, length: count, values});
  return values;
}
