/**
 * IMRI — charts.
 *
 * Three small SVG builders: bars, a donut and a sparkline. No library, no
 * canvas, no layout thrash.
 *
 * Rules that keep these honest:
 *   · colours come from `--chart-1…6` tokens, so both themes are correct with
 *     no JavaScript branching;
 *   · bars grow once on mount (600 ms) and never re-run, so hovering or
 *     re-rendering a row does not replay the animation;
 *   · hover feedback is CSS opacity only — no re-computation, no tooltip JS;
 *   · every chart carries `role="img"` and a sentence describing its content,
 *     and each mark has a native `<title>` for the pointer tooltip.
 */

import { el, svgEl, cx } from './ui.js';
import { fmtNum, t } from './i18n.js';

/** The categorical palette, in order. */
export const CHART_COLORS = Object.freeze([
  'var(--chart-1)',
  'var(--chart-2)',
  'var(--chart-3)',
  'var(--chart-4)',
  'var(--chart-5)',
  'var(--chart-6)',
]);

/**
 * Colour for a series index, wrapping around the palette.
 * @param {number} index
 * @returns {string}
 */
export function colorAt(index) {
  return CHART_COLORS[((index % CHART_COLORS.length) + CHART_COLORS.length) % CHART_COLORS.length];
}

/**
 * Round a raw grid interval up to a number a human would choose.
 *
 * The axis is built from a step, not from a maximum: four steps of 10 give the
 * labels 0, 10, 20, 30, 40, which is what a librarian expects to read. Rounding
 * the maximum instead produced 0, 13, 25, 38, 50 — technically correct, visibly
 * wrong.
 *
 * @param {number} raw The ideal interval, `max / gridCount`.
 * @returns {number} One of 1, 2, 2.5, 5, 10 × a power of ten.
 */
function niceStep(raw) {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const normalised = raw / magnitude;
  let step;
  if (normalised <= 1) step = 1;
  else if (normalised <= 2) step = 2;
  else if (normalised <= 2.5) step = 2.5;
  else if (normalised <= 5) step = 5;
  else step = 10;
  return step * magnitude;
}

/**
 * A vertical bar chart.
 *
 * @param {Object} options
 * @param {Array<{label: string, value: number, color?: string}>} options.data
 * @param {number} [options.height] Pixel height of the drawing area.
 * @param {boolean} [options.animate] Grow on mount; default true.
 * @param {(value: number) => string} [options.format] Axis label formatter.
 * @param {string} [options.ariaLabel] Sentence read by a screen reader.
 * @param {string} [options.emptyText]
 * @param {number} [options.maxLabels] Thin out the x-axis when crowded.
 * @returns {HTMLElement}
 */
export function barChart(options) {
  const opts = options || {};
  const data = (opts.data || []).map((item) => ({
    label: String(item.label === undefined || item.label === null ? '' : item.label),
    value: Number(item.value) || 0,
    color: item.color || null,
  }));
  const height = Number(opts.height) || 180;
  const maxLabels = Number(opts.maxLabels) || 12;

  const wrap = el('div', { class: 'chart-block' });

  if (!data.length || data.every((item) => item.value === 0)) {
    wrap.appendChild(el('div', { class: 'chart__empty', text: opts.emptyText || t('empty.generic.title') }));
    return wrap;
  }

  const width = 640;
  const padLeft = 44;
  const padRight = 8;
  const padTop = 10;
  const padBottom = 26;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  // Counts are whole things: never label an axis "0, 1.25, 2.5".
  const gridCount = 4;
  const allIntegers = data.every((item) => Number.isInteger(item.value));
  let stepValue = niceStep(Math.max(...data.map((item) => item.value)) / gridCount);
  if (allIntegers) stepValue = Math.max(1, Math.round(stepValue));
  const maxValue = stepValue * gridCount;
  const axisFormat = opts.format || ((value) => fmtNum(value, {
    decimals: Number.isInteger(stepValue) ? 0 : (stepValue < 1 ? 2 : 1),
  }));

  const svg = svgEl('svg', {
    class: cx('chart', opts.animate !== false && 'chart--animate'),
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: 'xMidYMid meet',
    role: 'img',
    'aria-label': opts.ariaLabel || '',
  });

  // Grid: four horizontal rules plus their labels. Drawn first so bars sit on top.
  for (let i = 0; i <= gridCount; i += 1) {
    const value = stepValue * i;
    const y = padTop + plotH - (plotH * i) / gridCount;
    svg.appendChild(svgEl('line', {
      class: 'chart__grid',
      x1: padLeft,
      x2: width - padRight,
      y1: y,
      y2: y,
    }));
    svg.appendChild(svgEl('text', {
      class: 'chart__axis',
      x: padLeft - 8,
      y: y + 3.5,
      'text-anchor': 'end',
    }, axisFormat(value)));
  }

  const band = plotW / data.length;
  const barWidth = Math.max(6, Math.min(30, band * 0.62));
  const labelStep = Math.ceil(data.length / maxLabels);

  data.forEach((item, index) => {
    const barHeight = maxValue === 0 ? 0 : Math.max(1, (item.value / maxValue) * plotH);
    const x = padLeft + band * index + (band - barWidth) / 2;
    const y = padTop + plotH - barHeight;

    const rect = svgEl('rect', {
      class: 'chart__bar',
      x,
      y,
      width: barWidth,
      height: barHeight,
      rx: Math.min(8, barWidth / 2),
      fill: item.color || colorAt(index),
      style: `animation-delay:${Math.min(index * 24, 240)}ms`,
    });
    rect.appendChild(svgEl('title', {}, `${item.label}: ${axisFormat(item.value)}`));
    svg.appendChild(rect);

    if (index % labelStep === 0) {
      svg.appendChild(svgEl('text', {
        class: 'chart__axis',
        x: x + barWidth / 2,
        y: height - 8,
        'text-anchor': 'middle',
      }, item.label));
    }
  });

  wrap.appendChild(svg);
  return wrap;
}

/**
 * A donut chart.
 *
 * Segments are drawn with `stroke-dasharray` on a circle rotated -90°, so the
 * first slice starts at twelve o'clock. The ring fades and scales in once; the
 * dash geometry is never touched again.
 *
 * @param {Object} options
 * @param {Array<{label: string, value: number, color?: string,
 *                valueLabel?: string}>} options.data
 * @param {number} [options.size] Square size in pixels.
 * @param {number} [options.thickness] Ring thickness.
 * @param {string} [options.centerValue] Big number in the middle.
 * @param {string} [options.centerLabel] Caption under the number.
 * @param {boolean} [options.legend] Render a legend below; default true.
 * @param {string} [options.ariaLabel]
 * @param {string} [options.emptyText]
 * @returns {HTMLElement}
 */
export function donut(options) {
  const opts = options || {};
  const data = (opts.data || [])
    .map((item, index) => ({
      label: String(item.label === undefined || item.label === null ? '' : item.label),
      value: Math.max(0, Number(item.value) || 0),
      color: item.color || colorAt(index),
      // Callers that want the legend to carry more than a raw count (a share,
      // a currency) supply their own string; the default stays the number.
      valueLabel: item.valueLabel || null,
    }))
    .filter((item) => item.value > 0);

  const wrap = el('div', { class: 'chart-block' });
  const total = data.reduce((sum, item) => sum + item.value, 0);

  if (!total) {
    wrap.appendChild(el('div', { class: 'chart__empty', text: opts.emptyText || t('empty.generic.title') }));
    return wrap;
  }

  const size = Number(opts.size) || 180;
  const thickness = Number(opts.thickness) || 22;
  const radius = (size - thickness) / 2;
  const center = size / 2;
  const circumference = 2 * Math.PI * radius;

  const svg = svgEl('svg', {
    class: cx('chart', opts.animate !== false && 'chart--animate'),
    viewBox: `0 0 ${size} ${size}`,
    width: size,
    height: size,
    role: 'img',
    'aria-label': opts.ariaLabel || '',
  });

  const ring = svgEl('g', { class: 'chart__donut', transform: `rotate(-90 ${center} ${center})` });

  // Track: a faint full ring so a single-slice donut still reads as a ring.
  ring.appendChild(svgEl('circle', {
    cx: center,
    cy: center,
    r: radius,
    fill: 'none',
    stroke: 'var(--border)',
    'stroke-width': thickness,
  }));

  let offset = 0;
  data.forEach((item) => {
    const length = (item.value / total) * circumference;
    const arc = svgEl('circle', {
      class: 'chart__arc',
      cx: center,
      cy: center,
      r: radius,
      fill: 'none',
      stroke: item.color,
      'stroke-width': thickness,
      'stroke-dasharray': `${Math.max(length - 1.5, 0.5)} ${circumference}`,
      'stroke-dashoffset': String(-offset),
      'stroke-linecap': 'butt',
    });
    arc.appendChild(svgEl('title', {}, `${item.label}: ${fmtNum(item.value)}`));
    ring.appendChild(arc);
    offset += length;
  });
  svg.appendChild(ring);

  if (opts.centerValue !== undefined && opts.centerValue !== null) {
    svg.appendChild(svgEl('text', {
      class: 'chart__value',
      x: center,
      y: center + (opts.centerLabel ? -2 : 6),
      'text-anchor': 'middle',
    }, String(opts.centerValue)));
  }
  if (opts.centerLabel) {
    svg.appendChild(svgEl('text', {
      class: 'chart__caption',
      x: center,
      y: center + 16,
      'text-anchor': 'middle',
    }, opts.centerLabel));
  }

  wrap.appendChild(svg);

  if (opts.legend !== false) {
    wrap.appendChild(legend(data.map((item) => ({
      label: item.label,
      color: item.color,
      value: item.valueLabel || fmtNum(item.value),
    }))));
  }
  return wrap;
}

/**
 * A sparkline: a thin line with a soft area fill and a dot on the last point.
 *
 * @param {Object} options
 * @param {number[]} options.values
 * @param {number} [options.width]
 * @param {number} [options.height]
 * @param {string} [options.color] Any CSS colour, usually a token.
 * @param {boolean} [options.area] Fill under the line; default true.
 * @param {string} [options.ariaLabel]
 * @returns {HTMLElement}
 */
export function sparkline(options) {
  const opts = options || {};
  const values = (opts.values || []).map((value) => Number(value) || 0);
  const width = Number(opts.width) || 120;
  const height = Number(opts.height) || 40;
  const color = opts.color || 'var(--primary)';

  const svg = svgEl('svg', {
    class: 'spark',
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: 'none',
    role: 'img',
    'aria-label': opts.ariaLabel || '',
  });

  if (values.length < 2) {
    svg.appendChild(svgEl('line', {
      x1: 0,
      x2: width,
      y1: height / 2,
      y2: height / 2,
      stroke: 'var(--border)',
      'stroke-width': 1,
    }));
    return svg;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 3;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - pad - ((value - min) / span) * (height - pad * 2);
    return [x, y];
  });

  const line = points
    .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(' ');

  if (opts.area !== false) {
    svg.appendChild(svgEl('path', {
      d: `${line} L${width} ${height} L0 ${height} Z`,
      fill: color,
      opacity: '0.1',
      stroke: 'none',
    }));
  }

  svg.appendChild(svgEl('path', {
    d: line,
    fill: 'none',
    stroke: color,
    'stroke-width': '1.75',
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
  }));

  const last = points[points.length - 1];
  svg.appendChild(svgEl('circle', {
    cx: last[0].toFixed(1),
    cy: last[1].toFixed(1),
    r: '2.4',
    fill: color,
  }));

  return svg;
}

/**
 * A legend row for a donut or a stacked bar.
 *
 * @param {Array<{label: string, color: string, value?: string}>} items
 * @returns {HTMLElement}
 */
export function legend(items) {
  const node = el('div', { class: 'chart__legend' });
  for (const item of items || []) {
    node.appendChild(el('span', { class: 'chart__key' },
      el('span', { class: 'chart__swatch', style: { background: item.color } }),
      el('span', { text: item.value ? `${item.label} · ${item.value}` : item.label })));
  }
  return node;
}

/** Default export grouping the builders. */
export default { CHART_COLORS, colorAt, barChart, donut, sparkline, legend };
