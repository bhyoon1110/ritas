"""Shared numeric Y-axis geometry for the FTIR editor and its published viewer."""

FTIR_Y_BREAKS_JS = r"""
function ftirYBreaks(series, xRange) {
  var left = xRange ? Math.min(xRange[0], xRange[1]) : -Infinity;
  var right = xRange ? Math.max(xRange[0], xRange[1]) : Infinity;
  var intervals = [];
  series.forEach(function(trace) {
    var low = Infinity, high = -Infinity, px = null, py = null;
    function include(y) { low = Math.min(low, y); high = Math.max(high, y); }
    for (var i = 0; i < trace.x.length; i++) {
      var x = trace.x[i], y = trace.y[i];
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        if (!trace.connectgaps) px = null;
        continue;
      }
      if (x >= left && x <= right) include(y);
      // A line can cross the entire viewport with both endpoints outside it.
      if (px !== null && x !== px) {
        var a = Math.min(px, x), b = Math.max(px, x);
        if (a < left && left < b) include(py + (y-py)*((left-px)/(x-px)));
        if (a < right && right < b) include(py + (y-py)*((right-px)/(x-px)));
      }
      px = x; py = y;
    }
    if (low <= high) intervals.push({low:low, high:high, group:trace.group});
  });
  intervals.sort(function(a,b) { return a.low-b.low; });
  var bands = [];
  intervals.forEach(function(interval) {
    var last = bands[bands.length-1];
    if (last && interval.low <= last.high) last.high = Math.max(last.high, interval.high);
    else bands.push({low:interval.low, high:interval.high});
  });
  var breaks = [], offsets = {};
  if (!bands.length) return {breaks:breaks, offsets:offsets, range:null};
  var low = bands[0].low, high = bands[bands.length-1].high;
  var occupied = bands.reduce(function(sum,b) { return sum+(b.high-b.low); },0);
  var spacing = (occupied || (high-low)) * 0.12 / Math.max(1,bands.length-1);
  for (var i = 1; i < bands.length; i++) {
    var gap = bands[i].low-bands[i-1].high;
    if (spacing > 0 && gap > spacing*2) breaks.push({low:bands[i-1].high,high:bands[i].low,keep:spacing});
  }
  intervals.forEach(function(b) { offsets[b.group] = ftirMapY(b.low, breaks, false)-b.low; });
  return {breaks:breaks, offsets:offsets, range:[low,ftirMapY(high, breaks, false)]};
}

// Only unoccupied intervals change slope. Outside them this is a translation.
function ftirMapY(value, breaks, inverse) {
  var removed = 0;
  for (var i = 0; i < breaks.length; i++) {
    var b = breaks[i], width = b.high-b.low;
    var start = inverse ? b.low-removed : b.low, span = inverse ? b.keep : width;
    if (value <= start) break;
    if (value < start+span) {
      return (inverse ? b.low : b.low-removed) + (value-start)*(inverse ? width/b.keep : b.keep/width);
    }
    removed += width-b.keep;
  }
  return inverse ? value+removed : value-removed;
}

function ftirMoveObjects(layout, from, to) {
  if (JSON.stringify(from) === JSON.stringify(to)) return;
  function move(item, key) {
    if (typeof item[key] === "number") item[key] = ftirMapY(ftirMapY(item[key],from,true),to,false);
  }
  (layout.annotations || []).forEach(function(item) {
    if (!item.yref || item.yref === "y") move(item,"y");
    if (item.ayref === "y") move(item,"ay");
  });
  (layout.shapes || []).forEach(function(item) {
    if (!item.yref || item.yref === "y") { move(item,"y0"); move(item,"y1"); }
  });
}

function ftirFoldRange(plan) {
  var span = plan.range[1]-plan.range[0];
  var pad = Math.max(span*0.10, Math.abs(plan.range[0])*1e-12, 1e-12);
  return [plan.range[0]-pad, plan.range[1]+pad];
}

function ftirFoldTicks(axis, breaks, range) {
  var low = Math.min(range[0],range[1]), high = Math.max(range[0],range[1]);
  var scale = (high-low)/8;
  if (!(scale > 0) || !Number.isFinite(scale)) return;
  var power = Math.pow(10,Math.floor(Math.log(scale)/Math.LN10));
  var step = power * (scale/power <= 1 ? 1 : scale/power <= 2 ? 2 : scale/power <= 5 ? 5 : 10);
  if (axis.tickmode === "linear" && Number.isFinite(axis.dtick) && axis.dtick > 0) {
    step = axis.dtick * Math.max(1, Math.ceil((high-low)/axis.dtick/80));
  }
  var decimals = /^\.(\d)f$/.exec(axis.tickformat || "");
  var values = [], labels = [], start = low, removed = 0;
  function intact(end) {
    var from = Math.ceil((start+removed)/step);
    // Count iterations independently of the magnitude of the raw tick index.
    for (var j=0; j<=81; j++) {
      var actual = (from+j)*step, pos = actual-removed;
      if (pos > end) break;
      if (pos >= start) { values.push(pos); labels.push(decimals ? actual.toFixed(Number(decimals[1])) : String(Number(actual.toPrecision(12)))); }
    }
  }
  breaks.forEach(function(b) {
    var before = b.low-removed, after = before+b.keep, middle = (before+after)/2;
    intact(Math.min(high,before));
    if (middle >= low && middle <= high) { values.push(middle); labels.push("//"); }
    removed += b.high-b.low-b.keep;
    start = Math.max(low,after);
  });
  intact(high);
  axis.tickmode = "array"; axis.tickvals = values; axis.ticktext = labels;
}
"""
