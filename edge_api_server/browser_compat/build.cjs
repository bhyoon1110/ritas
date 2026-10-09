// Reproducible OFFLINE runtime assets. Node/Babel are build-time tools only.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const babel = require('@babel/core');
const preset = require('@babel/preset-env');
const esbuild = require('esbuild');
const acorn = require('acorn');
const postcss = require('postcss');
const root = path.resolve(__dirname, '../app/static/chrome49');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const input = JSON.parse(execFileSync(process.env.RIST_BUILD_PYTHON || 'python3', [path.join(__dirname,'collect.py')], {encoding:'utf8', maxBuffer:32*1024*1024}));
fs.mkdirSync(root, {recursive:true});
const manifest = {version:1, target:'Chrome 49.0.2623.75', scripts:{}, styles:{}, pages:{}};
function compile(source) {
  const result = babel.transformSync(source, {
    configFile:false, babelrc:false, sourceType:'script', comments:true, compact:true,
    shouldPrintComment:comment=>/@license|@preserve|copyright|license information/i.test(comment),
    presets:[[preset,{targets:{chrome:'49'}, forceAllTransforms:true, modules:false, useBuiltIns:false}]],
  }).code;
  acorn.parse(result, {ecmaVersion:5, allowReserved:true});
  return result;
}
function emit(source, extension) {
  const file = digest(source) + extension;
  fs.writeFileSync(path.join(root,file), source);
  return file;
}
function cssFallback(source) {
  const css = postcss.parse(source);
  css.walkRules(rule => {
    const display = rule.nodes.find(n=>n.prop==='display' && n.value==='grid');
    const columns = rule.nodes.find(n=>n.prop==='grid-template-columns');
    if (display && columns) {
      display.value = 'flex';
      rule.append({prop:'flex-wrap',value:'wrap'});
    }
    if (columns) {
      const repeat = /^repeat\(\s*(\d+)\s*,/.exec(columns.value);
      const count = repeat ? Number(repeat[1]) : columns.value.includes('auto-fit') ? 2 : postcss.list.space(columns.value).length;
      const gap = rule.nodes.find(n=>n.prop==='gap');
      const spacing = gap ? postcss.list.space(gap.value).slice(-1)[0] : '12px';
      const width = count === 1 ? '100%' : `calc(${100/count}% - ${spacing})`;
      const child = postcss.rule({selector:rule.selectors.map(s=>s+' > *').join(',')});
      child.append({prop:'flex',value:`0 1 ${width}`},{prop:'min-width',value:'0'},{prop:'max-width',value:width});
      rule.parent.insertAfter(rule,child);
    }
  });
  css.walkDecls(decl => {
    const value = decl.value;
    if (decl.prop === 'display' && value === 'grid') decl.value = 'block';
    if (decl.prop === 'position' && value === 'sticky') decl.value = 'relative';
    if (decl.prop === 'grid-column' && /1\s*\/\s*-1/.test(value)) {
      decl.cloneBefore({prop:'flex-basis',value:'100%'});
      decl.cloneBefore({prop:'max-width',value:'100%'});
    }
    if (decl.prop === 'inset') {
      const v = postcss.list.space(value);
      ['top','right','bottom','left'].forEach((prop,i) => decl.cloneBefore({prop,value:v[i] || v[i % 2] || v[0]}));
      decl.remove();
    }
    if (/^(?:min|max)-(?:width|height)$|^(?:width|height)$/.test(decl.prop) && /^min\(/.test(value)) {
      const choices = postcss.list.comma(value.slice(4,-1));
      const fixed = choices.find(v => /^\d+(?:\.\d+)?px$/.test(v));
      const fluid = choices.find(v => v !== fixed && !/dvh|dvw/.test(v));
      if (fluid) decl.value = fluid;
      if (fixed && !decl.prop.startsWith('max-')) decl.cloneBefore({prop:'max-'+decl.prop.replace(/^min-/,''),value:fixed});
    }
    if (decl.prop === 'gap' && decl.parent.type === 'rule') {
      const grid = decl.parent.nodes.some(n => n.prop === 'display' && n.value === 'block');
      const gaps = postcss.list.space(value);
      const selector = decl.parent.selectors.map(s => s + ' > *').join(',');
      const extra = postcss.rule({selector});
      extra.append({prop:'margin-bottom',value:gaps[0]});
      if (!grid) extra.append({prop:'margin-right',value:gaps[1] || gaps[0]});
      decl.parent.parent.insertAfter(decl.parent,extra);
      decl.remove();
    }
  });
  return css.toString();
}
for (const [name,html] of Object.entries(input.pages)) {
  manifest.pages[name] = [];
  for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    const source = m[2].trim();
    if (!source || /\bsrc\s*=|application\/json/.test(m[1])) continue;
    // A separately escaped JSON assignment is data, not a program template.
    if (/^window\.RIST_VOC_CONFIG=\{[\s\S]*\};$/.test(source)) continue;
    const key = digest(source);
    if (!manifest.scripts[key]) manifest.scripts[key] = emit(compile(source), '.js');
    manifest.pages[name].push(key);
  }
  for (const m of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    manifest.styles[digest(m[1])] = cssFallback(m[1]);
  }
}
const bundle = esbuild.buildSync({entryPoints:[path.join(__dirname,'runtime.js')], bundle:true,
  platform:'browser', format:'iife', write:false, minify:false, target:'es2015', logLevel:'warning'}).outputFiles[0].text;
manifest.runtime = emit(compile(bundle), '.js');
const plotly = fs.readFileSync(input.plotly, 'utf8');
// Syntax is also checked for third-party Plotly; do not assume its version's support.
try { acorn.parse(plotly,{ecmaVersion:5,allowReserved:true}); manifest.plotly = null; }
catch (_) { manifest.plotly = emit(compile(plotly), '.js'); }
manifest.plotlySourceSha256 = digest(plotly);
manifest.css = emit(fs.readFileSync(path.join(__dirname,'fallback.css'),'utf8'), '.css');
fs.writeFileSync(path.join(root,'manifest.json'), JSON.stringify(manifest,null,2)+'\n');
const notices = ['Runtime third-party notices (bundled for offline Chrome 49 support).'];
notices.push('\n--- Plotly.js (bundled with plotly.py) ---\n'+plotly.slice(0,plotly.indexOf('*/')+2));
notices.push(fs.readFileSync(path.join(__dirname,'licenses/plotly.js.txt'),'utf8'));
for (const name of ['core-js','abortcontroller-polyfill','whatwg-fetch','formdata-polyfill','pepjs','@babel/core']) {
  const dir = path.dirname(require.resolve(name+'/package.json'));
  const license = ['LICENSE','LICENSE.txt','LICENSE.md','LICENSE-MIT'].find(f=>fs.existsSync(path.join(dir,f)));
  if (!license) throw new Error('Missing license for '+name);
  const pkg = require(name+'/package.json');
  notices.push(`\n--- ${name} ${pkg.version} ---\n`+fs.readFileSync(path.join(dir,license),'utf8'));
}
fs.writeFileSync(path.join(root,'THIRD_PARTY_NOTICES.txt'),notices.join('\n').trimEnd()+'\n');
const keep = new Set(['manifest.json',manifest.runtime,manifest.css,manifest.plotly,...Object.values(manifest.scripts)]);
// Remove only generated content-addressed assets, never arbitrary files.
for(const file of fs.readdirSync(root)) if (/^[a-f0-9]{64}\.(js|css)$/.test(file) && !keep.has(file)) fs.unlinkSync(path.join(root,file));
console.log(`Built ${Object.keys(manifest.scripts).length} scripts for ${Object.keys(input.pages).length} pages; ES5 syntax verified.`);
