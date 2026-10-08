const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../ui/web/home.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(api) {
  const classes=new Set(), timers=new Map(); let next=0;
  const document={body:{classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n)}},activeElement:null};
  const context={window:api?{pywebview:{api:{set_home_quiet:api}}}:{}, document,
    setTimeout:(fn,delay)=>{timers.set(++next,{fn,delay});return next;},clearTimeout:id=>timers.delete(id),cancelAnimationFrame(){},devicePixelRatio:1};
  vm.runInNewContext(source,context);
  const home=Object.assign(Object.create(context.window.TrubaHome.prototype),{disposed:false,pinned:false,busy:false,time:0,
    root:{contains:()=>false},dialog:{open:false,close(){}},wave:{color:[1,2,3]},
    controller:{abort(){}},resize:{disconnect(){}}});
  return {home,context,classes,timers};
}
let passed=0;
async function check(name,test){await test();passed++;console.log('OK '+name);}
(async()=>{
  await check('quiet hides native frame and reveal restores it once per state',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return{ok:true};});
    for(let i=0;i<100;i++) f.home.reveal(); await flush();
    assert.deepEqual(calls,[false]);
    f.home.hide(true); await flush(); assert(f.classes.has('home-quiet'));
    f.home.reveal(); await flush(); assert.deepEqual(calls,[false,true,false]);
  });
  await check('late native bridge receives current mode',async()=>{
    const calls=[],f=fixture();f.home.hide(true);
    f.context.window.pywebview={api:{set_home_quiet:async quiet=>{calls.push(quiet);return{ok:true};}}};
    f.home.hide(true); await flush(); assert.deepEqual(calls,[true]);
  });
  await check('latest request wins while native operation is in flight',async()=>{
    const calls=[],pending=[],f=fixture(quiet=>{calls.push(quiet);return new Promise(resolve=>pending.push(resolve));});
    f.home.reveal();pending.shift()({ok:true});await flush();
    f.home.hide(true);f.home.reveal();pending.shift()({ok:true});await flush();
    assert.deepEqual(calls,[false,true,false]);pending.shift()({ok:true});await flush();
  });
  await check('leaving home restores frame after a pending hide',async()=>{
    const calls=[],pending=[],f=fixture(quiet=>{calls.push(quiet);return new Promise(resolve=>pending.push(resolve));});
    f.home.hide(true);f.home.dispose();pending.shift()({ok:true});await flush();
    assert.deepEqual(calls,[true,false]);pending.shift()({ok:true});await flush();
  });
  await check('hovered native buttons defer hiding and reveal cancels retry',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return quiet?{ok:true,deferred:true}:{ok:true};});
    f.home.hide(true); await flush();
    assert([...f.timers.values()].some(t=>t.delay===250));
    f.home.reveal(); await flush();
    assert(![...f.timers.values()].some(t=>t.delay===250));assert.deepEqual(calls,[true,false]);
  });
  await check('pinned controls and typing keep frame reachable',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return{ok:true};});
    f.home.pinned=true;f.home.hide(true);await flush();assert.deepEqual(calls,[]);
    f.home.pinned=false;f.context.document.activeElement={tagName:'INPUT'};f.home.root.contains=()=>true;
    f.home.hide();await flush();assert.deepEqual(calls,[]);
  });
  await check('browser without native bridge keeps existing autohide',async()=>{
    const f=fixture();f.home.hide(true);assert(f.classes.has('home-quiet'));
    f.home.reveal();assert(!f.classes.has('home-quiet'));
  });
  await check('stationary pointer keeps controls visible beyond idle timeout',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return{ok:true};});
    f.home.pointerMove({screenX:300,screenY:400});await flush();
    f.home.hide();await flush();
    assert(!f.classes.has('home-quiet'));assert.deepEqual(calls,[false]);
  });
  await check('layout-generated pointer movement cannot undo quiet mode',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return{ok:true};});
    f.home.pointerMove({screenX:300,screenY:400});await flush();
    f.home.pointerInside=false;f.home.hide(true);await flush();
    for(let i=0;i<100;i++) f.home.pointerMove({screenX:300,screenY:400});await flush();
    assert(f.classes.has('home-quiet'));assert.deepEqual(calls,[false,true]);
    f.home.pointerMove({screenX:301,screenY:400});await flush();
    assert(!f.classes.has('home-quiet'));assert.deepEqual(calls,[false,true,false]);
  });
  await check('brief exit and return cancels the pending hide',async()=>{
    const calls=[],f=fixture(async quiet=>{calls.push(quiet);return{ok:true};});
    f.home.pointerMove({screenX:300,screenY:400});await flush();
    f.home.pointerLeave();assert([...f.timers.values()].some(t=>t.delay===200));
    f.home.pointerMove({screenX:301,screenY:400});await flush();
    assert(![...f.timers.values()].some(t=>t.delay===200));
    f.home.hide();assert(!f.classes.has('home-quiet'));assert.deepEqual(calls,[false]);
  });
  await check('resize refreshes native eligibility while a request is in flight',async()=>{
    const calls=[],pending=[],f=fixture(quiet=>{calls.push(quiet);return new Promise(resolve=>pending.push(resolve));});
    f.home.hide(true);
    f.home.canvas={width:1000,height:640,getBoundingClientRect:()=>({width:1000,height:640})};
    f.home.fitWave=()=>{};f.home.size();
    pending.shift()({ok:true});await flush();
    assert.deepEqual(calls,[true,true]);pending.shift()({ok:true});await flush();
    f.home.size();await flush();assert.deepEqual(calls,[true,true]);
  });
  await check('small window keeps the wave above controls without changing its shape',async()=>{
    const Wave=require('../ui/web/home_wave.js');
    for(const [width,height,titleTop] of [[1000,608,288],[1292,762,470],[1920,1080,750]]) {
      const f=fixture();f.home.wave=new Wave([107,138,253]);
      f.home.canvas={getBoundingClientRect:()=>({width,height,top:0})};
      f.home.root.querySelector=()=>({getBoundingClientRect:()=>({top:titleTop})});
      f.home.fitWave();
      for(let i=0;i<120;i++) f.home.wave.advance(1/60,{activity:'speaking',output_level:1},[107,138,253]);
      const g=f.home.wave.geometry(50,width,height);
      for(const band of f.home.wave.bands) for(const point of band.points) {
        assert(f.home.wave.point(point,band,g)[1]<titleTop-24);
      }
      assert(g.cy<=height*.4 && g.cy>=height*.3);
      if(height===1080) assert.equal(g.cy,height*.4);
    }
  });
  console.log(JSON.stringify({passed}));
})().catch(error=>{console.error(error);process.exitCode=1;});
