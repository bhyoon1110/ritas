// Keep Korean button captions unbroken in both native and Chrome49 CSS.
// Pixel layout/axis-double-click behavior is verified separately in Plotly.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const postcss = require('postcss');
const manifest = JSON.parse(fs.readFileSync(path.resolve(__dirname,'../app/static/chrome49/manifest.json'),'utf8'));

for (const kind of ['ftir','raman']) {
  const html = execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
    `from app.${kind}_web import build_${kind}_page; print(build_${kind}_page())`],{encoding:'utf8'});
  const styles = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map(m=>m[1]);
  const id = kind === 'ftir' ? 'peak-plot' : 'raman-plot';
  for (const mode of ['native','chrome49']) {
    const css = mode === 'native' ? styles : styles.map(source=>{
      const compiled = manifest.styles[createHash('sha256').update(source).digest('hex')];
      assert(compiled,'rebuild compatibility CSS');return compiled;
    });
    const ast = postcss.parse(css.join('\n'));
    function declarations(selector) {
      const result = {};
      ast.walkRules('#'+id+' '+selector,rule=>{
        // Base rules apply at every viewport; narrower rules may adjust size.
        if (rule.parent.type !== 'root') return;
        rule.walkDecls(decl=>{result[decl.prop]=decl.value;});
      });
      return result;
    }
    test(kind+'/'+mode+': toolbar captions stay on one line; whole controls can wrap without shrinking',()=>{
      const button=declarations('.rist-plot-control-row button');
      assert.equal(button['white-space'],'nowrap');assert.equal(button['word-break'],'normal');
      assert.equal(button['overflow-wrap'],'normal');assert.equal(button['flex-shrink'],'0');
      assert.equal(declarations('.rist-plot-control-row > *')['flex-shrink'],'0');
      const row=declarations('.rist-plot-control-row');assert.equal(row['flex-wrap'],'wrap');
      assert.equal(row['max-width'],'calc(100% - 60px)');
      const bulk=declarations('.rist-legend-bulk-controls');
      assert.equal(bulk['flex-wrap'],'wrap');assert.equal(bulk['max-width'],'100%');
    });
    test(kind+'/'+mode+': axis reset/apply/crop buttons keep readable compact single-line labels',()=>{
      const button=declarations('.rist-axis-control-actions button');
      assert.equal(button['white-space'],'nowrap');assert.equal(button['flex'],'0 0 auto');
      assert.equal(button['font-size'],'12px');assert.equal(button['padding'],'6px 8px');
      assert.equal(button['min-height'],'34px');
      assert.equal(declarations('.rist-axis-control-footer')['flex-wrap'],'wrap');
      assert.equal(declarations('.rist-axis-control-actions')['flex'],'0 0 auto');
      assert.equal(declarations('.rist-axis-control-view-actions')['flex'],'0 0 auto');
    });
    test(kind+'/'+mode+': legend and shape dialogs fit inside narrow graphs and keep captions intact',()=>{
      for (const panel of ['.rist-legend-edit-panel','.rist-shape-editor-panel']) {
        assert.equal(declarations(panel).right,'8px');
        assert.equal(declarations(panel+' button')['white-space'],'nowrap');
        assert.equal(declarations(panel+' button')['flex-shrink'],'0');
      }
      for (const selector of ['.rist-legend-edit-actions','.rist-shape-editor-actions']) {
        assert.equal(declarations(selector)['flex-wrap'],'wrap');
      }
    });
    test(kind+'/'+mode+': page, template and library actions have single-line captions and wrapping groups',()=>{
      function pageDecl(selector) {
        const result={};
        ast.walkRules(rule=>{
          if (rule.parent.type==='root' && rule.selectors.includes(selector)) {
            rule.walkDecls(d=>{result[d.prop]=d.value;});
          }
        });
        return result;
      }
      assert.equal(pageDecl('button')['white-space'],'nowrap');
      assert.equal(pageDecl('.'+kind+'-report-option-button')['white-space'],'nowrap');
      assert.equal(pageDecl('.'+kind+'-library-dialog-button')['flex-shrink'],'0');
      const footer='.'+kind+'-library-dialog-'+(kind==='ftir'?'footer':'actions');
      assert.equal(pageDecl(footer)['flex-wrap'],'wrap');
      assert.equal(pageDecl('.'+kind+'-report-picker-item')['text-overflow'],'ellipsis');
      let mobileWrap=false;
      ast.walkRules('.'+kind+'-library-band',rule=>{
        if (rule.parent.type==='atrule') rule.walkDecls('flex-wrap',d=>{if(d.value==='wrap')mobileWrap=true;});
      });
      assert.ok(mobileWrap,'mobile library controls must wrap, not push the library list outside the viewport');
    });
  }
}

test('report editor and read-only viewer keep all action captions on one line',()=>{
  const editor=fs.readFileSync(path.resolve(__dirname,'../app/report_editor.html'),'utf8');
  const viewer=execFileSync(process.env.RIST_TEST_PYTHON || 'python3',['-c',
    'from app.report.spectrum_viewer import _STYLE; print(_STYLE)'],{encoding:'utf8'});
  for(const source of [editor.match(/<style>([\s\S]*?)<\/style>/)[1],viewer]) {
    const ast=postcss.parse(source);let nowrap=false,wrap=false;
    ast.walkRules(rule=>{
      if(rule.parent.type!=='root')return;
      if(rule.selectors.includes('button'))rule.walkDecls('white-space',d=>{if(d.value==='nowrap')nowrap=true;});
      rule.walkDecls('flex-wrap',d=>{if(d.value==='wrap')wrap=true;});
    });
    assert.ok(nowrap);assert.ok(wrap);
  }
});

test('long user-defined ratio and option labels stay single-line with full-text tooltips',()=>{
  for(const kind of ['ftir','raman']) {
    const source=fs.readFileSync(path.resolve(__dirname,'../app/'+kind+'_web.py'),'utf8');
    assert.match(source,/item\.textContent = value;\s*item\.title = value;/);
    if(kind==='raman') {
      assert.match(source,/edit\.style\.cssText = "[^"\n]*white-space:nowrap[^"\n]*text-overflow:ellipsis/);
      assert.match(source,/edit\.title = edit\.textContent;/);
    }
  }
});
