/**
 * Lightweight canvas charts (no CDN). Supports doughnut and bar for report page.
 */
(function () {
  "use strict";

  var PALETTE = ["#c45c26", "#2f5f8a", "#2f6b4f", "#8a6d3b", "#6b4f7a", "#3d5a4c"];

  function parseData(canvas) {
    var labels = [];
    var values = [];
    try {
      labels = JSON.parse(canvas.getAttribute("data-labels") || "[]");
      values = JSON.parse(canvas.getAttribute("data-values") || "[]");
    } catch (err) {
      labels = [];
      values = [];
    }
    return { labels: labels, values: values, type: canvas.getAttribute("data-type") || "bar" };
  }

  function drawBar(ctx, labels, values) {
    var w = ctx.canvas.width;
    var h = ctx.canvas.height;
    var pad = 28;
    var max = Math.max.apply(null, values.concat([1]));
    var barW = (w - pad * 2) / Math.max(labels.length, 1);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "12px IBM Plex Sans, sans-serif";
    ctx.textAlign = "center";
    labels.forEach(function (label, i) {
      var v = values[i] || 0;
      var bh = ((h - pad * 2) * v) / max;
      var x = pad + i * barW + barW * 0.15;
      var y = h - pad - bh;
      ctx.fillStyle = PALETTE[i % PALETTE.length];
      ctx.fillRect(x, y, barW * 0.7, bh);
      ctx.fillStyle = "#5d6a63";
      ctx.fillText(String(v), x + barW * 0.35, y - 6);
      ctx.fillText(String(label).slice(0, 12), x + barW * 0.35, h - 8);
    });
  }

  function drawDoughnut(ctx, labels, values) {
    var w = ctx.canvas.width;
    var h = ctx.canvas.height;
    var cx = w / 2;
    var cy = h / 2 - 10;
    var radius = Math.min(w, h) * 0.32;
    var total = values.reduce(function (a, b) { return a + b; }, 0) || 1;
    var start = -Math.PI / 2;
    ctx.clearRect(0, 0, w, h);
    values.forEach(function (v, i) {
      var angle = (v / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, radius, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = PALETTE[i % PALETTE.length];
      ctx.fill();
      start += angle;
    });
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.55, 0, Math.PI * 2);
    ctx.fillStyle = "#fffdf8";
    ctx.fill();

    ctx.font = "12px IBM Plex Sans, sans-serif";
    ctx.textAlign = "left";
    var legendY = h - 18 - labels.length * 14;
    labels.forEach(function (label, i) {
      var y = Math.max(8, legendY) + i * 14;
      ctx.fillStyle = PALETTE[i % PALETTE.length];
      ctx.fillRect(12, y, 10, 10);
      ctx.fillStyle = "#5d6a63";
      ctx.fillText(label + " (" + (values[i] || 0) + ")", 28, y + 9);
    });
  }

  function paint(canvas) {
    var data = parseData(canvas);
    var parent = canvas.parentElement;
    var width = parent ? parent.clientWidth : 320;
    var height = parent ? parent.clientHeight : 240;
    canvas.width = Math.max(width, 200);
    canvas.height = Math.max(height, 180);
    var ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (data.type === "doughnut") {
      drawDoughnut(ctx, data.labels, data.values);
    } else {
      drawBar(ctx, data.labels, data.values);
    }
  }

  function init() {
    document.querySelectorAll("canvas[data-labels]").forEach(paint);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  window.addEventListener("resize", init);
})();
