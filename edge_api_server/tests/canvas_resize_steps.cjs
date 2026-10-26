// Real mouse gestures shared by the spectrum and native TEM report browser tests.
const assert=require('node:assert/strict');
module.exports=async function resizeImage({page,editor,objectId,read,commit}){
  const select=()=>editor.getByLabel('페이지 개체 선택',{exact:true}).selectOption(objectId);
  const outline=()=>editor.locator('.canvas-object.selected').evaluate(n=>({w:parseFloat(n.style.width)/100,h:parseFloat(n.style.height)/100}));
  async function begin(){
    await select();const handle=editor.locator('.canvas-resize');await handle.scrollIntoViewIfNeeded();
    const stage=await editor.locator('#slide-stage').boundingBox(),box=await handle.boundingBox();assert(stage&&box);
    const x=box.x+box.width/2,y=box.y+box.height/2;await page.mouse.move(x,y);await page.mouse.down();return {stage,x,y};
  }
  const initial=await read();
  await commit(async()=>{const p=await begin();await page.mouse.move(p.x+p.stage.width*.04,p.y+p.stage.height*.01,{steps:4});await page.mouse.up();});
  const free=await read();assert(Math.abs(free.w/free.h-initial.w/initial.h)>.01,'Default image drag must change the aspect ratio');
  await commit(async()=>{const p=await begin();await page.keyboard.down('Shift');await page.mouse.move(p.x,p.y+p.stage.height*.03,{steps:4});await page.mouse.up();await page.keyboard.up('Shift');});
  const locked=await read();assert(locked.h>free.h);assert(Math.abs(locked.w/locked.h-free.w/free.h)<1e-6,'Shift+vertical drag must scale both axes');
  await commit(async()=>{
    const p=await begin();await page.mouse.move(p.x+p.stage.width*.02,p.y+p.stage.height*.003,{steps:4});const freeOutline=await outline();
    await page.keyboard.down('Shift');const ratioOutline=await outline();
    // CSS percentage serialization rounds; compare dimensions within 1e-6 of a page.
    assert(Math.abs(ratioOutline.h-ratioOutline.w*locked.h/locked.w)<1e-6,JSON.stringify({ratioOutline,locked}));
    await page.keyboard.up('Shift');const released=await outline();assert(Math.abs(released.w-freeOutline.w)<1e-9&&Math.abs(released.h-freeOutline.h)<1e-9);
    assert.deepEqual(await read(),locked,'Only release commits the drag');await page.mouse.up();
  });
  const final=await read();
  const p=await begin();await page.mouse.move(p.x-20,p.y-10,{steps:3});await page.keyboard.press('Escape');await page.mouse.up();assert.deepEqual(await read(),final);
  await commit(()=>editor.getByRole('button',{name:'개체 실행 취소',exact:true}).click());assert.deepEqual(await read(),locked);
  await commit(()=>editor.getByRole('button',{name:'개체 다시 실행',exact:true}).click());assert.deepEqual(await read(),final);
  return final;
};
