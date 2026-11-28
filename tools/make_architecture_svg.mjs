#!/usr/bin/env node
/**
 * Phase 3: Zero-Dependency GitHub README Animated Architecture SVG + Mermaid Generator
 * Outputs pure CSS + SMIL (<animateMotion>) SVG (zero <script> tags so GitHub README renders animation).
 *
 * Usage:
 *   node tools/make_architecture_svg.mjs <input.json> [outDir]
 */
import fs from "node:fs";
import path from "node:path";

const NODE_COLORS = {
  start: { fill: "#064E3B", stroke: "#10B981" },
  end: { fill: "#064E3B", stroke: "#10B981" },
  process: { fill: "#172554", stroke: "#3B82F6" },
  decision: { fill: "#1E1B4B", stroke: "#6366F1" },
  data: { fill: "#451A03", stroke: "#F59E0B" },
  error: { fill: "#450A0A", stroke: "#EF4444" },
  output: { fill: "#042F2E", stroke: "#14B8A6" }
};

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function wrapLabel(text, maxChars = 20) {
  const words = String(text || "").split(/\s+/);
  const lines = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > maxChars && cur) {
      lines.push(cur);
      cur = w;
    } else {
      cur = (cur + " " + w).trim();
    }
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 2);
}

function layoutSpec(spec) {
  const nodes = spec.nodes || [];
  const edges = spec.edges || [];
  const layerMap = new Map();
  nodes.forEach((n, idx) => {
    const l = typeof n.layer === "number" ? n.layer : idx;
    if (!layerMap.has(l)) layerMap.set(l, []);
    layerMap.get(l).push(n);
  });

  const sortedLayers = Array.from(layerMap.keys()).sort((a, b) => a - b);
  const colGap = 215;
  const rowGap = 112;
  const padX = 115;
  const headerH = 66;
  const padY = 66;

  const maxRows = Math.max(1, ...sortedLayers.map((l) => layerMap.get(l).length));
  const width = Math.max(820, padX * 2 + Math.max(0, sortedLayers.length - 1) * colGap);
  const bodyH = Math.max(240, padY * 2 + (maxRows - 1) * rowGap + 44);
  const height = headerH + bodyH + 42;

  const posMap = new Map();
  sortedLayers.forEach((layerKey, colIdx) => {
    const colNodes = layerMap.get(layerKey);
    const colHeight = (colNodes.length - 1) * rowGap;
    const startY = headerH + bodyH / 2 - colHeight / 2;
    colNodes.forEach((node, rowIdx) => {
      const w = node.type === "decision" ? 132 : 160;
      const h = node.type === "decision" ? 68 : 56;
      posMap.set(node.id, { ...node, x: padX + colIdx * colGap, y: startY + rowIdx * rowGap, w, h, colIdx });
    });
  });

  const laidEdges = edges
    .map((e, i) => {
      const a = posMap.get(e.from);
      const b = posMap.get(e.to);
      if (!a || !b) return null;
      let d = "";
      let lx = (a.x + b.x) / 2;
      let ly = (a.y + b.y) / 2 - 8;

      if (e.kind === "feedback" || b.colIdx < a.colIdx) {
        const sx = a.x;
        const sy = a.y + a.h / 2;
        const ex = b.x;
        const ey = b.y + b.h / 2;
        const dropY = Math.max(sy, ey) + 46;
        d = `M ${sx} ${sy} L ${sx} ${dropY - 12} Q ${sx} ${dropY} ${sx - 12} ${dropY} L ${ex + 12} ${dropY} Q ${ex} ${dropY} ${ex} ${dropY - 12} L ${ex} ${ey}`;
        lx = (sx + ex) / 2;
        ly = dropY + 14;
      } else if (a.colIdx === b.colIdx) {
        const dir = b.y > a.y ? 1 : -1;
        d = `M ${a.x} ${a.y + dir * (a.h / 2)} L ${b.x} ${b.y - dir * (b.h / 2)}`;
        lx = a.x + 56;
        ly = (a.y + b.y) / 2;
      } else {
        const sx = a.x + a.w / 2;
        const sy = a.y;
        const ex = b.x - b.w / 2;
        const ey = b.y;
        const mx = (sx + ex) / 2;
        if (Math.abs(sy - ey) < 4) {
          d = `M ${sx} ${sy} L ${ex} ${ey}`;
        } else {
          const r = Math.min(14, Math.abs(ey - sy) / 2, Math.abs(ex - sx) / 4);
          const dir = ey > sy ? 1 : -1;
          d = `M ${sx} ${sy} L ${mx - r} ${sy} Q ${mx} ${sy} ${mx} ${sy + dir * r} L ${mx} ${ey - dir * r} Q ${mx} ${ey} ${mx + r} ${ey} L ${ex} ${ey}`;
        }
        lx = mx;
        ly = Math.min(sy, ey) - 8;
      }
      return { ...e, idx: i, d, lx, ly };
    })
    .filter(Boolean);

  return { width, height, nodes: Array.from(posMap.values()), edges: laidEdges };
}

function buildSvg(spec) {
  const L = layoutSpec(spec);
  const edgeSvg = L.edges
    .map((e) => {
      const flowColor = e.kind === "error" ? "#EF4444" : e.kind === "feedback" ? "#F59E0B" : "#38BDF8";
      const dur = (2.2 + (e.idx % 3) * 0.35).toFixed(2);
      const begin = (e.idx * 0.25).toFixed(2);
      return `
    <g>
      <path d="${e.d}" fill="none" stroke="#30363D" stroke-width="2.6" stroke-linecap="round" marker-end="url(#arrow)"/>
      <path id="edge-${e.idx}" class="flow" d="${e.d}" fill="none" stroke="${flowColor}" stroke-width="2" stroke-dasharray="7 7" stroke-linecap="round"/>
      <circle r="3.6" fill="${flowColor}">
        <animateMotion dur="${dur}s" begin="${begin}s" repeatCount="indefinite" path="${e.d}"/>
      </circle>
      ${e.label ? `<text x="${e.lx}" y="${e.ly}" class="elbl">${esc(e.label)}</text>` : ""}
    </g>`;
    })
    .join("");

  const nodeSvg = L.nodes
    .map((n, i) => {
      const pal = NODE_COLORS[n.type] || NODE_COLORS.process;
      const hw = n.w / 2;
      const hh = n.h / 2;
      const delay = (i * 0.55).toFixed(2);
      let shape = `<rect x="${n.x - hw}" y="${n.y - hh}" width="${n.w}" height="${n.h}" rx="10" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="2" class="nshape" style="animation-delay:${delay}s"/>`;
      if (n.type === "start" || n.type === "end") {
        shape = `<rect x="${n.x - hw}" y="${n.y - hh}" width="${n.w}" height="${n.h}" rx="${hh}" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="2" class="nshape" style="animation-delay:${delay}s"/>`;
      } else if (n.type === "decision") {
        const pts = `${n.x},${n.y - hh} ${n.x + hw},${n.y} ${n.x},${n.y + hh} ${n.x - hw},${n.y}`;
        shape = `<polygon points="${pts}" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="2" class="nshape" style="animation-delay:${delay}s"/>`;
      } else if (n.type === "data" || n.type === "output") {
        shape = `<rect x="${n.x - hw}" y="${n.y - hh}" width="${n.w}" height="${n.h}" rx="22" fill="${pal.fill}" stroke="${pal.stroke}" stroke-width="2" class="nshape" style="animation-delay:${delay}s"/>`;
      }

      const lines = wrapLabel(n.label, n.type === "decision" ? 14 : 21);
      const ty = lines.length === 2 ? n.y - 6 : n.y + 1;
      const tspans = lines
        .map((ln, idx) => `<tspan x="${n.x}" dy="${idx === 0 ? 0 : 13}">${esc(ln)}</tspan>`)
        .join("");

      return `
    <g>
      ${shape}
      <text x="${n.x}" y="${ty}" class="nlbl">${tspans}</text>
      ${n.tech ? `<text x="${n.x}" y="${n.y + (lines.length === 2 ? 20 : 17)}" class="ntech">${esc(n.tech)}</text>` : ""}
    </g>`;
    })
    .join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${L.width}" height="${L.height}" viewBox="0 0 ${L.width} ${L.height}" role="img" aria-label="${esc(spec.title || "Architecture Diagram")}">
  <style>
    .bg { fill: #0d1117; stroke: #30363d; stroke-width: 1.5; }
    .title { font: 700 16px 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; fill: #f8fafc; }
    .sub { font: 500 11.5px 'Fira Code', ui-monospace, monospace; fill: #94a3b8; }
    .nlbl { font: 700 11.5px 'Inter', -apple-system, sans-serif; fill: #f8fafc; text-anchor: middle; }
    .ntech { font: 500 9.5px 'Fira Code', ui-monospace, monospace; fill: #cbd5e1; text-anchor: middle; }
    .elbl { font: 500 10px 'Fira Code', ui-monospace, monospace; fill: #94a3b8; text-anchor: middle; }
    .flow { animation: dash 1.5s linear infinite; }
    .nshape { animation: glow 4.5s ease-in-out infinite; }
    @keyframes dash { from { stroke-dashoffset: 28; } to { stroke-dashoffset: 0; } }
    @keyframes glow { 0%, 100% { filter: drop-shadow(0 0 0px rgba(239,68,68,0)); } 50% { filter: drop-shadow(0 0 8px rgba(239,68,68,0.55)); } }
    @media (prefers-reduced-motion: reduce) { .flow, .nshape { animation: none; } }
  </style>
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1.5 L 8.5 5 L 0 8.5 z" fill="#64748B"/>
    </marker>
    <linearGradient id="accentBar" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#DC2626"/>
      <stop offset="50%" stop-color="#EF4444"/>
      <stop offset="100%" stop-color="#38BDF8"/>
    </linearGradient>
  </defs>
  <rect x="1" y="1" width="${L.width - 2}" height="${L.height - 2}" rx="14" class="bg"/>
  <rect x="1" y="1" width="${L.width - 2}" height="4" rx="2" fill="url(#accentBar)"/>
  <text x="24" y="32" class="title">${esc(spec.title || "System Architecture")}</text>
  <text x="24" y="50" class="sub">${esc(spec.subtitle || "")}</text>
  ${edgeSvg}
  ${nodeSvg}
  <text x="24" y="${L.height - 14}" class="sub">Generated from architecture.json · Pure CSS/SMIL SVG (GitHub README compatible)</text>
</svg>
`;
}

function buildMermaid(spec) {
  const lines = ["flowchart LR"];
  for (const n of spec.nodes || []) {
    const lbl = String(n.label).replace(/"/g, "'");
    if (n.type === "decision") lines.push(`  ${n.id}{"${lbl}"}`);
    else if (n.type === "start" || n.type === "end") lines.push(`  ${n.id}(["${lbl}"])`);
    else if (n.type === "data" || n.type === "output") lines.push(`  ${n.id}[("${lbl}")]`);
    else lines.push(`  ${n.id}["${lbl}"]`);
  }
  for (const e of spec.edges || []) {
    const arrow = e.kind === "feedback" || e.kind === "error" ? "-.->" : "-->";
    const lbl = e.label ? `|${String(e.label).replace(/\|/g, "/")}|` : "";
    lines.push(`  ${e.from} ${arrow}${lbl} ${e.to}`);
  }
  lines.push("  classDef start fill:#064E3B,stroke:#10B981,color:#F8FAFC;");
  lines.push("  classDef process fill:#172554,stroke:#3B82F6,color:#F8FAFC;");
  lines.push("  classDef decision fill:#1E1B4B,stroke:#6366F1,color:#F8FAFC;");
  lines.push("  classDef data fill:#451A03,stroke:#F59E0B,color:#F8FAFC;");
  lines.push("  classDef error fill:#450A0A,stroke:#EF4444,color:#F8FAFC;");
  lines.push("  classDef output fill:#042F2E,stroke:#14B8A6,color:#F8FAFC;");
  for (const n of spec.nodes || []) {
    const cls = n.type === "end" ? "start" : n.type || "process";
    lines.push(`  class ${n.id} ${cls};`);
  }
  return lines.join("\n") + "\n";
}

function main() {
  const inputPath = process.argv[2];
  const outDir = process.argv[3] || "docs";
  if (!inputPath) {
    console.error("Usage: node tools/make_architecture_svg.mjs <architecture.json> [outDir]");
    process.exit(1);
  }
  const spec = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  fs.mkdirSync(outDir, { recursive: true });
  const svgPath = path.join(outDir, "architecture.svg");
  const mmdPath = path.join(outDir, "architecture.mmd");
  fs.writeFileSync(svgPath, buildSvg(spec), "utf8");
  fs.writeFileSync(mmdPath, buildMermaid(spec), "utf8");
  console.log(`Generated ${svgPath} and ${mmdPath}`);
}

main();
