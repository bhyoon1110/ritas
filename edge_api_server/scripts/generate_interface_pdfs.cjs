#!/usr/bin/env node

"use strict";

const fs = require("fs");
const path = require("path");
const { marked } = require("marked");
const { chromium } = require("playwright");

const repositoryRoot = path.resolve(__dirname, "..", "..");
const chromeExecutable =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const documents = [
  {
    source: "EXPERIMENT_PC_EDGE_API.md",
    output: "documents/EXPERIMENT_PC_EDGE_API.pdf",
    runningTitle: "실험 PC - Edge 분석 서버 REST API 명세",
  },
  {
    source: "EDGE_SPRING_BOOT_API.md",
    output: "documents/EDGE_SPRING_BOOT_API.pdf",
    runningTitle: "RIST 보고서 DB 전송 큐 연동 명세",
  },
];

const style = `
  @page { size: A4; }
  * { box-sizing: border-box; }
  html {
    color: #172b4d;
    background: #ffffff;
    font-family: "Arial Unicode MS", "Apple SD Gothic Neo", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.62;
  }
  body { margin: 0; overflow-wrap: anywhere; }
  h1, h2, h3, h4 {
    color: #11284a;
    line-height: 1.3;
    break-after: avoid-page;
  }
  h1 {
    margin: 0 0 12mm;
    padding-bottom: 5mm;
    border-bottom: 2px solid #2876d2;
    font-size: 25pt;
    letter-spacing: -0.5px;
  }
  h2 {
    margin: 9mm 0 3mm;
    padding-bottom: 2mm;
    border-bottom: 1px solid #b8cae0;
    font-size: 17pt;
  }
  h3 { margin: 6mm 0 2mm; font-size: 13.5pt; }
  h4 { margin: 4mm 0 1.5mm; font-size: 11.5pt; }
  p { margin: 0 0 3mm; orphans: 3; widows: 3; }
  ul, ol { margin: 1.5mm 0 3.5mm; padding-left: 7mm; }
  li { margin: 0 0 1.1mm; }
  blockquote {
    margin: 0 0 7mm;
    padding: 4mm 5mm;
    border-left: 3px solid #2876d2;
    background: #f1f5fb;
    color: #40546e;
    break-inside: avoid-page;
  }
  blockquote p { margin: 0 0 1.2mm; }
  blockquote p:last-child { margin-bottom: 0; }
  table {
    width: 100%;
    margin: 3mm 0 5mm;
    border-collapse: collapse;
    font-size: 8.7pt;
  }
  thead { display: table-header-group; }
  tr { break-inside: avoid-page; }
  th, td {
    padding: 2.2mm 2.5mm;
    border: 1px solid #b8c9df;
    text-align: left;
    vertical-align: top;
  }
  th { background: #e7eff8; color: #16365c; font-weight: 700; }
  code {
    padding: 0.2mm 1mm;
    border-radius: 2px;
    background: #eef3f8;
    color: #17324f;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
    font-size: 0.9em;
    overflow-wrap: anywhere;
  }
  pre {
    margin: 3mm 0 5mm;
    padding: 4mm;
    border-radius: 4px;
    background: #111b2b;
    color: #f2f6fb;
    font-size: 8.2pt;
    line-height: 1.48;
    white-space: pre-wrap;
    word-break: break-word;
    break-inside: auto;
  }
  pre code { padding: 0; background: transparent; color: inherit; }
  a { color: #1769aa; text-decoration: none; }
  hr { margin: 7mm 0; border: 0; border-top: 1px solid #c7d4e4; }
  strong { color: #102f57; }
`;

const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );

async function renderDocument(browser, specification) {
  const sourcePath = path.join(repositoryRoot, specification.source);
  const outputPath = path.join(repositoryRoot, specification.output);
  const markdown = fs.readFileSync(sourcePath, "utf8");
  const content = marked.parse(markdown, { gfm: true, breaks: false });
  const page = await browser.newPage();
  await page.setContent(
    `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${escapeHtml(
      specification.runningTitle,
    )}</title><style>${style}</style></head><body><main>${content}</main></body></html>`,
    { waitUntil: "load" },
  );
  await page.emulateMedia({ media: "print" });
  await page.pdf({
    path: outputPath,
    format: "A4",
    printBackground: true,
    displayHeaderFooter: true,
    margin: { top: "17mm", right: "16mm", bottom: "17mm", left: "16mm" },
    headerTemplate: `<div style="box-sizing:border-box;width:100%;padding:0 16mm;color:#64748b;font:8px Arial,sans-serif;text-align:right;">${escapeHtml(
      specification.runningTitle,
    )}</div>`,
    footerTemplate:
      '<div style="box-sizing:border-box;width:100%;padding:0 16mm;color:#64748b;font:8px Arial,sans-serif;display:flex;justify-content:space-between;"><span>RIST 분석 보고서 자동화</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
    preferCSSPageSize: true,
  });
  await page.close();
  process.stdout.write(`${specification.output}\n`);
}

async function main() {
  if (!fs.existsSync(chromeExecutable)) {
    throw new Error(`Google Chrome executable not found: ${chromeExecutable}`);
  }
  const browser = await chromium.launch({
    executablePath: chromeExecutable,
    headless: true,
  });
  try {
    for (const specification of documents) {
      await renderDocument(browser, specification);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
