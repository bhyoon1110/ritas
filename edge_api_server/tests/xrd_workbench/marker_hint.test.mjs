import test from 'node:test';
import assert from 'node:assert/strict';
import {createState,buildFigure,phaseMarkerHint} from '../../app/xrd_workbench/web/viewer/model.mjs';
const source={id:'hint',samples:[{id:'a',name:'A',measurement:{x:[10,20,30],y:[1,9,2],axis_kind:'two_theta',axis_unit:'deg',intensity_unit:'cps'},
  phases:[{id:'p',name:'Phase A'}],observed_peaks:[{no:1,x:20}],
  assignments:[{no:1,x:20,matches:[{phase:'Phase A',hkl:'100'}]}],reference_peaks:[]}]};
function hint(change,stateChange=()=>{}){const p=structuredClone(source),s=createState(p);s.markers=true;change(p.samples[0],p);stateChange(s);return phaseMarkerHint(p,s,buildFigure(p,s));}
test('phase marker notice distinguishes no measurement, no phase list, observations and assignments',()=>{
  assert.match(hint(()=>{},s=>{s.visible=[];}),/표시할 측정 그래프가 없습니다/);
  assert.match(hint(s=>{s.phases=[];}),/결정상 목록이 없습니다/);
  assert.match(hint(s=>{s.observed_peaks=[];}),/관측 피크표/);
  assert.match(hint(s=>{s.assignments=[];}),/배정 정보가 없습니다/);
  assert.match(hint(s=>{s.assignments[0].matches[0].phase='Mismatch';}),/이름·피크 번호·위치/);
  assert.match(hint(()=>{},s=>{s.hiddenPhases=['p'];}),/모두 숨김/);
});
test('notice accounts for reversed zoom, Y cropping and partially missing samples',()=>{
  assert.match(hint(()=>{},s=>{s.xRange=[25,15];}),/마커 1개 표시/);
  assert.match(hint(()=>{},s=>{s.yRange=[0,1];}),/확대 범위 밖/);
  const p=structuredClone(source);p.samples.push({...structuredClone(p.samples[0]),id:'b',name:'B',observed_peaks:[]});
  const s=createState(p);s.markers=true;
  const message=phaseMarkerHint(p,s,buildFigure(p,s));
  assert.match(message,/마커 1개 표시/);assert.match(message,/B: 관측 피크표/);
});
