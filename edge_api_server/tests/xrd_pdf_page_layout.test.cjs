// Run the real report's orientation handler; this is not Supermium binary QA.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {JSDOM} = require('jsdom');

const report = execFileSync(process.env.RIST_TEST_PYTHON || 'python3', ['-c',
  'import plotly.graph_objects as go; from lim.xrd_plot import build_report_html; '
  + 'print(build_report_html(go.Figure(), sample_name="PDF layout", groups=[], group_map={}, warnings=[], '
  + 'origin=False, first_stem="sample", raw_line_indices=[], highlight_groups={}))'
], {encoding:'utf8', maxBuffer:8*1024*1024});
const script = [...report.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1])
  .find(s=>s.includes('function applyPrintPageStyle()'));
assert(script);

for (const mode of ['desktop', 'narrow', 'coarse']) {
  test(`${mode}: XRD orientation only changes graph/image width`, t=>{
    const dom = new JSDOM(report, {url:'http://localhost/xrd/report', runScripts:'outside-only'});
    t.after(()=>dom.window.close());
    const w=dom.window, d=w.document;
    w.matchMedia=query=>({matches:(mode==='narrow' && query.includes('max-width')) ||
      (mode==='coarse' && query.includes('pointer: coarse'))});
    w.eval(script);
    const option=d.getElementById('xrd-report-landscape-graph');
    function assertLayout(landscape) {
      const style=d.querySelector('[data-xrd-print-page-style]');
      assert(style);
      const css=style.textContent;
      assert(!css.includes('@page:first'));
      assert(css.includes('.xrd-report-page > .xrd-report-section'));
      assert(css.includes('page: xrd-report-portrait !important'));
      assert(css.includes('width: 190mm !important'));
      assert.equal(css.includes('width: 277mm !important'),landscape);
      assert.equal(d.body.classList.contains('xrd-report-graph-landscape'),landscape);
      assert.equal(d.querySelectorAll('[data-xrd-print-page-style]').length,1);
    }
    assertLayout(mode==='desktop');
    if(mode==='desktop') {
      option.checked=false; option.dispatchEvent(new w.Event('change')); assertLayout(false);
      option.checked=true; option.dispatchEvent(new w.Event('change')); assertLayout(true);
    } else {
      assert(option.disabled);
      assert(!option.checked);
    }
  });
}

for (const failNumberedImage of [false, true]) {
  test(`PDF snapshot ${failNumberedImage ? 'stops on failed' : 'includes full'} numbered graph`, async t=>{
    const dom=new JSDOM(report,{url:'http://localhost/xrd/report',runScripts:'outside-only'});
    t.after(()=>dom.window.close());
    const w=dom.window,d=w.document,gd=d.getElementById('xrd-plot');
    w.matchMedia=()=>({matches:false});
    w.alert=()=>{};
    w.console.warn=()=>{};
    const frame=d.createElement('div'); frame.className='xrd-numbered-peak-graph';
    const numbered=d.createElement('div'); numbered.id='xrd-numbered-peak-plot';
    frame.appendChild(numbered); d.getElementById('xrd-peak-info').appendChild(frame);
    gd.data=[]; gd.layout={};
    const imageCalls=[]; let captured=null;
    w.Plotly={
      newPlot:()=>Promise.resolve(), purge:()=>{}, relayout:()=>Promise.resolve(), Plots:{resize:()=>{}},
      toImage:(graph,options)=>{
        imageCalls.push({graph,options});
        if(graph===numbered && failNumberedImage) return Promise.reject(new Error('snapshot failed'));
        return Promise.resolve('data:image/png;base64,fixture');
      },
    };
    w.fetch=async(url,options)=>{captured=JSON.parse(options.body);return {ok:true,blob:async()=>new w.Blob(['PDF'])};};
    w.URL.createObjectURL=()=> 'blob:test-pdf'; w.URL.revokeObjectURL=()=>{};
    w.HTMLAnchorElement.prototype.click=()=>{};
    w.eval(script);
    const button=d.getElementById('xrd-report-pdf-export'); button.click();
    for(let i=0;i<100 && button.disabled;i++) await new Promise(r=>setTimeout(r,10));
    assert.equal(button.disabled,false);
    const call=imageCalls.find(c=>c.graph===numbered);
    assert(call);
    assert.equal(call.options.width,980); assert.equal(call.options.height,450);
    assert.equal(call.options.scale,2);
    if(failNumberedImage) {
      assert.equal(captured,null);
    } else {
      const snapshot=new JSDOM(captured.html); t.after(()=>snapshot.window.close());
      assert.equal(captured.landscape,true);
      assert(snapshot.window.document.querySelector('.has-print-numbered-plot .xrd-numbered-print-image[src]'));
      assert.equal(snapshot.window.document.querySelectorAll('script').length,0);
      assert(!frame.classList.contains('has-print-numbered-plot'));
      assert(!frame.querySelector('img').hasAttribute('src'));
    }
  });
}
