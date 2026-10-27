// Match the shared tools injected into XRD report.html by the authenticated route.
const fs=require('node:fs'),path=require('node:path');
module.exports=function(window){
  window.eval(fs.readFileSync(path.join(__dirname,'../../common/rist_common/plotting/file_progress.js'),'utf8'));
  for(const name of ['report_editor_icons.js','report_page_sidebar.js','report_cell_editor.js','report_canvas_editor.js'])
    window.eval(fs.readFileSync(path.join(__dirname,'../app',name),'utf8'));
};
