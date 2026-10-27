// Keep in sync with core/sample_colors.py; parity is checked by regression tests.
export const sampleColors = [
  '#ff3535', '#3535ff', '#15966b', '#9d51c9', '#d98515', '#00a1ad',
  '#333333', '#8c564b', '#cf267d', '#687c15', '#163f76', '#8e2424',
  '#ff11ff', '#11aa11', '#1188ff', '#aa88bb', '#662255', '#1199dd',
  '#224411', '#113399', '#778877', '#dd7755', '#dd22bb', '#ff1166',
];

export function sampleColor(index) {
  if (index < sampleColors.length) return sampleColors[index];
  // An index-based sequence keeps existing samples stable when more are added.
  const step = index - sampleColors.length;
  const hue = (210 + step * 137.50776405003785) % 360;
  const saturation = [0.65, 0.80, 0.70][step % 3];
  const lightness = [0.38, 0.46, 0.30][step % 3];
  const amplitude = saturation * Math.min(lightness, 1 - lightness);
  let rgb = [0, 8, 4].map(offset => {
    const k = (offset + hue / 30) % 12;
    const value = lightness - amplitude * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(255 * value);
  });
  const luminance = () => rgb.map(v => v / 255)
    .map(v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4))
    .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  while (luminance() > 0.30) rgb = rgb.map(v => Math.floor(v * 0.92));
  return '#' + rgb.map(v => v.toString(16).padStart(2, '0')).join('');
}

export function automaticSampleColors(project) {
  return Object.fromEntries(project.samples.map((sample, index) => [sample.id, sampleColor(index)]));
}
