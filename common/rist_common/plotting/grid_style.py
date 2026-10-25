"""Shared grid preference policy for spectrum styling and stacking."""

GRID_STYLE_JS = r"""
  function applyGridPreferences(layout, stacked, savedYAxis) {
    var meta = layout.meta || {};
    var preferences = meta.ristGrid || {};
    var stack = meta.ristFtirStack || meta.ristRamanStack || {};
    if (stacked === undefined) stacked = !!stack.enabled;
    var saved = savedYAxis || stack.yAxisDisplay;
    ["x", "y"].forEach(function(key) {
      var selected = preferences[key];
      if (!selected || typeof selected.major !== "boolean" || typeof selected.minor !== "boolean") return;
      var axis = layout[key + "axis"] || (layout[key + "axis"] = {});
      var minor = axis.minor || (axis.minor = {});
      axis.showgrid = selected.major && !(key === "y" && stacked);
      minor.showgrid = selected.minor && !(key === "y" && stacked);
      if (!axis.gridcolor) axis.gridcolor = "#e5e7eb";
      if (!minor.gridcolor) minor.gridcolor = "#f4f4f4";
      if (key === "y" && stacked && saved) {
        saved.showgrid = {exists: true, value: selected.major};
        saved.minorShowgrid = {exists: true, value: selected.minor};
      }
    });
    return layout;
  }
"""
