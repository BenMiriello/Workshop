/* =====================================================================
   AVIARY · live yard scene (engine)
   Isometric voxel diorama: a grass plot + bushes + feeder on a flat blue
   field. Birds (yard species) fly in at random intervals, land, peck/hop
   at their own intervals, then lift off. Tap a bird to identify it.
   ---------------------------------------------------------------------
   The original Aviary scene/behavior code, dropped in whole and kept intact
   (we integrate it, we don't rewrite it). Surgery vs. the source is confined
   to wiring it into the app: (1) IIFE -> the
   createAviary(opts) factory below, (2) the three scene elements come in via
   opts instead of getElementById, (3) the demo controls/side-panel DOM is now
   optional (guarded), (4) no random demo spawner auto-starts -- the scene is
   driven by setPresent() from real detections, (5) models build lazily so any
   registered species can spawn, (6) public API adds setNameTags/registerSpecies/
   destroy. The bird geometry + motion code is untouched.
   ===================================================================== */
import { AviaryVoxel } from "./aviary-voxel.js";

// createAviary({ stage, scenery, field, countchip? }) -> public API.
// stage/scenery/field are the three scene layers (see the original Aviary.html
// markup the engine expects). countchip is optional (the "N in the yard" overlay).
export function createAviary(opts) {
  const V = AviaryVoxel;
  const { buildBird, renderBird, renderCubes, SPECIES, YARD_IDS } = V;

  // ---- scene constants ----
  const GW = 20, GH = 20;           // square lawn (bigger)
  const TW = 17, TH = 8.5, ZH = 14; // iso tile half-width/height, altitude scale
  const SC = 0.36;                  // bird display scale (smaller on a bigger yard)
  const MAX = 12;
  let OX = 0, OY = 0, sceneW = 0, sceneH = 0;

  const stage = opts.stage;
  const scenery = opts.scenery;
  const field = opts.field;
  // Optional demo-chrome elements. In the dashboard element these don't exist,
  // so every reference to them is guarded; $ returns null harmlessly.
  const $ = (id) => (opts.elements && opts.elements[id]) || null;

  const w2s = (gx, gy, gz = 0) => [OX + (gx - gy) * TW, OY + (gx + gy) * TH - gz * ZH];

  // ---- models (built lazily, cached) ----
  // The original prebuilt only the ~15 demo yard species; here any registered
  // species (the full North-America set) can appear, so models build on first use.
  const MODELS = {};
  function model(id){ return MODELS[id] || (SPECIES[id] ? (MODELS[id] = buildBird(SPECIES[id])) : null); }
  YARD_IDS.forEach(id => { model(id); });
  // Merge generated species profiles into the registry. Each entry is the same
  // shape as SPECIES (template/group/pattern/colors). The generated set keys by
  // id but omits the `id` FIELD; presence reconciliation, panel counts, and
  // selection all read `b.sp.id`, so stamp it from the key — otherwise every
  // generated species lands with id===undefined and setPresent churns it in/out
  // each poll (want[id] never matches have[undefined]). Safe to call repeatedly.
  function registerSpecies(map){ if(!map) return; for(const id in map){ const p=map[id]; SPECIES[id] = (p && p.id!=null) ? p : Object.assign({}, p, {id}); } }
  function feetRel(m){ return [m.anchors.feet[0] - m.bounds.minX, m.anchors.feet[1] - m.bounds.minY]; }

  // species weighting: common songbirds spawn more; hawk rare
  const WEIGHT = { house_finch:6, lesser_goldfinch:5, chickadee:5, junco:5, annas_hummingbird:3,
    ca_towhee:3, robin:4, oak_titmouse:3, spotted_towhee:2, wc_sparrow:3, cedar_waxwing:2,
    scrub_jay:3, crow:2, mourning_dove:3, coopers_hawk:0.6 };
  const FLOCK = { house_finch:3, lesser_goldfinch:3, junco:2, cedar_waxwing:3, crow:2, mourning_dove:2 };
  function pickSpecies(){
    const pool=[]; YARD_IDS.forEach(id=>{ const n=Math.round((WEIGHT[id]||1)*10); for(let i=0;i<n;i++)pool.push(id); });
    return pool[(Math.random()*pool.length)|0];
  }

  /* ---------------- scenery (lawn + props) ---------------- */
  function rpoly(pts, r){ // rounded polygon path in screen coords
    const n=pts.length; let d='';
    for(let i=0;i<n;i++){const p1=pts[i],p0=pts[(i-1+n)%n],p2=pts[(i+1)%n];
      const v1=[p0[0]-p1[0],p0[1]-p1[1]],v2=[p2[0]-p1[0],p2[1]-p1[1]];
      const l1=Math.hypot(...v1)||1,l2=Math.hypot(...v2)||1,rr=Math.min(r,l1/2,l2/2);
      const a=[p1[0]+v1[0]/l1*rr,p1[1]+v1[1]/l1*rr],b=[p1[0]+v2[0]/l2*rr,p1[1]+v2[1]/l2*rr];
      d+=(i?`L${a[0].toFixed(1)} ${a[1].toFixed(1)}`:`M${a[0].toFixed(1)} ${a[1].toFixed(1)}`)+`Q${p1[0].toFixed(1)} ${p1[1].toFixed(1)} ${b[0].toFixed(1)} ${b[1].toFixed(1)}`;}
    return d+'Z';
  }
  // props placed in the field as positioned voxel models (z-sorted with birds)
  // props hug the BACK + SIDE edges (the two top edges), never the front corner
  const PROPS = [
    { gx:3.0, gy:2.5, r:2.2, cubes: treeRound() },
    { gx:15.5, gy:2.8, r:2.2, cubes: treeTall() },
    { gx:1.8, gy:8.5, r:1.4, cubes: bush(1.05) },
    { gx:18.4, gy:9.5, r:1.4, cubes: bush(0.95) },
    { gx:9.5, gy:1.6, r:1.3, cubes: bush(0.85) },
    { gx:18.5, gy:5.5, r:1.4, cubes: feeder() },
    { gx:2.2, gy:12.5, r:1.5, cubes: bath() },
  ];
  // 3D voxel clumps (lobes stacked in WORLD space, not screen-layered)
  function bush(s){ const d='#4d8442',g='#5f9f52',l='#74b562'; const S=v=>v*s*1.7;
    return [ {box:[S(0),S(0),0, S(2.2),S(2.2),S(1.7)],col:d},
             {box:[S(-0.5),S(0.7),S(0.5), S(1.1),S(2.1),S(2.4)],col:g},
             {box:[S(1.1),S(-0.4),S(0.6), S(2.7),S(1.6),S(2.5)],col:g},
             {box:[S(0.5),S(0.7),S(2.1), S(2.0),S(2.1),S(3.2)],col:l} ]; }
  function treeRound(){ const t='#7a5a3c',d='#3f7a3a',g='#519349',l='#69aa57'; const S=v=>v*1.85;
    return [ {box:[S(1.0),S(1.0),0, S(1.7),S(1.7),S(3.4)],col:t},
             {box:[S(-0.3),S(-0.3),S(3.0), S(3.0),S(3.0),S(4.8)],col:d},
             {box:[S(0.2),S(0.3),S(4.6), S(2.6),S(2.5),S(6.0)],col:g},
             {box:[S(0.9),S(0.8),S(5.8), S(2.0),S(1.9),S(6.8)],col:l} ]; }
  function treeTall(){ const t='#6f5436',d='#3a6f3e',g='#4d8a48',l='#63a455'; const S=v=>v*1.8;
    return [ {box:[S(0.9),S(0.9),0, S(1.4),S(1.4),S(2.8)],col:t},
             {box:[S(-0.3),S(-0.3),S(2.4), S(2.6),S(2.6),S(3.9)],col:d},
             {box:[S(0.1),S(0.1),S(3.7), S(2.1),S(2.1),S(5.1)],col:g},
             {box:[S(0.45),S(0.45),S(4.9), S(1.6),S(1.6),S(6.2)],col:g},
             {box:[S(0.8),S(0.8),S(6.0), S(1.25),S(1.25),S(7.0)],col:l} ]; }
  function feeder(){ const post='#6f5436',wood='#9a7a52',seed='#d8be7e',roof='#9c5a3c'; const S=v=>v*1.9;
    return [ {box:[S(1.1),S(1.1),0, S(1.6),S(1.6),S(3.5)],col:post},
             {box:[S(0.1),S(0.1),S(3.4), S(2.6),S(2.6),S(3.9)],col:wood},
             {box:[S(0.7),S(0.7),S(3.9), S(2.0),S(2.0),S(4.9)],col:seed},
             {box:[S(0.35),S(0.35),S(4.9), S(2.35),S(2.35),S(5.3)],col:roof},
             {box:[S(0.7),S(0.7),S(5.3), S(2.0),S(2.0),S(5.7)],col:roof} ]; }
  function bath(){ const st='#9a9aa6',sd='#82828f',wt='#7fb6d6'; const S=v=>v*1.85;
    return [ {box:[S(1.0),S(1.0),0, S(1.7),S(1.7),S(2.0)],col:sd},
             {box:[S(0),S(0),S(1.9), S(2.7),S(2.7),S(2.6)],col:st},
             {box:[S(0.35),S(0.35),S(2.5), S(2.35),S(2.35),S(2.78)],col:wt} ]; }
  const PROP_SPOTS = PROPS.map(p=>({gx:p.gx,gy:p.gy,r:p.r||1.3}));
  // a ground spot is clear if it's away from every prop and every other bird
  function clear(gx,gy,self){
    for(const s of PROP_SPOTS){ if(Math.hypot(gx-s.gx,gy-s.gy)<s.r) return false; }
    for(const b of birds){ if(b===self||b.state==='out') continue; const bx=b.state==='in'?b.to.gx:b.gx, by=b.state==='in'?b.to.gy:b.gy; if(Math.hypot(gx-bx,gy-by)<1.15) return false; }
    return true;
  }
  function freeSpot(){ for(let i=0;i<50;i++){const gx=1.5+Math.random()*(GW-3),gy=1.5+Math.random()*(GH-3); if(clear(gx,gy,null)) return [gx,gy]; } return [GW/2+(Math.random()-0.5)*2, GH/2+(Math.random()-0.5)*2]; }

  function layout(){
    sceneW = stage.clientWidth; sceneH = stage.clientHeight;
    OX = sceneW/2 - ((GW-GH)/2)*TW;
    OY = sceneH*0.60 - ((GW+GH)/2)*TH;
    // lawn
    const c=[w2s(0,0),w2s(GW,0),w2s(GW,GH),w2s(0,GH)];
    const TKN=14; // soil thickness
    const front=[c[1],c[2],c[3]];
    let g=`<path d="${rpoly(c,18)}" fill="#7fb267"/>`;
    // thickness on the two front edges (GW edge and GH edge)
    g+=`<path d="${rpoly([c[1],c[2],[c[2][0],c[2][1]+TKN],[c[1][0],c[1][1]+TKN]],6)}" fill="#5e8c46"/>`;
    g+=`<path d="${rpoly([c[2],c[3],[c[3][0],c[3][1]+TKN],[c[2][0],c[2][1]+TKN]],6)}" fill="#699a52"/>`;
    // subtle mow stripes along gx
    for(let i=1;i<GW;i++){const a=w2s(i,0),b=w2s(i,GH);g+=`<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="rgba(255,255,255,.05)" stroke-width="2"/>`;}
    g=`<path d="${rpoly(c,18)}" fill="#86bd6a"/>`+g.slice(g.indexOf('<path',5)); // top lighter
    scenery.innerHTML = g;
    // props
    PROPS.forEach(p=>{ if(!p.el){ const rd=renderCubes(p.cubes); p.rend=rd;
        const b0=p.cubes[0].box, bl=V.proj([(b0[0]+b0[3])/2,(b0[1]+b0[4])/2,0]);
        p.baseRel=[bl[0]-rd.minX, bl[1]-rd.minY];   // trunk/base centre, relative to viewBox origin
        p.el=document.createElement('div'); p.el.className='bird'; p.el.style.cursor='default';
        p.el.innerHTML=`<svg width="${rd.w*SC}" height="${rd.h*SC}" viewBox="${rd.minX} ${rd.minY} ${rd.w} ${rd.h}" style="position:absolute">${rd.svg}</svg>`;
        field.appendChild(p.el); }
      const s=w2s(p.gx,p.gy,0);
      p.el.style.transform=`translate(${s[0]-p.baseRel[0]*SC}px,${s[1]-p.baseRel[1]*SC}px)`;
      p.el.style.zIndex=Math.round(s[1]);
    });
    birds.forEach(placeBird);
  }

  /* ---------------- birds ---------------- */
  const birds = [];
  let bidc = 0;
  function spawn(speciesId){
    const id = speciesId || pickSpecies();
    const m0 = model(id); if(!m0) return;   // unknown species id -> nothing to draw
    // Detection-driven scene: exactly one bird per species (no flocking), so the
    // yard mirrors "which species are present", never a count. Demo SIM can flock.
    const flock = SIM ? ((Math.random()<0.5 && FLOCK[id]) ? 1+((Math.random()*FLOCK[id])|0) : 1) : 1;
    for(let k=0;k<flock && birds.length<MAX;k++){
      const m=m0, fr=feetRel(m);
      const edge=Math.random();
      const spot=freeSpot(); const tx=spot[0], ty=spot[1];
      let fx,fy; // off-lawn entry
      if(edge<0.4){fx=-2.5; fy=ty;} else if(edge<0.8){fx=GW+2.5; fy=ty;} else {fx=tx; fy=-2.5;}
      const b={ id:'b'+(bidc++), sp:SPECIES[id], model:m, fr,
        gx:fx,gy:fy,gz:7+Math.random()*2, face:1, state:'in', t0:performance.now(),
        dur:1700+Math.random()*900, from:{gx:fx,gy:fy,gz:7+Math.random()*2},
        to:{gx:tx,gy:ty,gz:0}, action:'idle', actEnd:0, dwellEnd:0,
        el:document.createElement('div') };
      b.el.className='bird'; b.el.dataset.id=b.id;
      b.el.innerHTML=`<svg style="position:absolute"></svg>`;
      b.svg=b.el.querySelector('svg');
      b.svg.setAttribute('viewBox', m.viewBox);
      b.svg.setAttribute('width', m.w*SC); b.svg.setAttribute('height', m.h*SC);
      b.el.style.transformOrigin = `${b.fr[0]*SC}px ${b.fr[1]*SC}px`;
      b.shadow=document.createElement('div'); b.shadow.className='shadow';
      b.el.addEventListener('click',e=>{e.stopPropagation();select(b);});
      field.appendChild(b.shadow); field.appendChild(b.el);
      birds.push(b);
    }
  }
  const ease=t=>1-Math.pow(1-t,2.2);
  function placeBird(b){
    const s=w2s(b.gx,b.gy,b.gz), g=w2s(b.gx,b.gy,0);
    b.el.style.transform=`translate(${s[0]-b.fr[0]*SC}px,${s[1]-b.fr[1]*SC}px) scaleX(${b.face})`;
    b.el.style.zIndex=Math.round(g[1]+200);
    const sk=Math.max(0.25,1-b.gz*0.09);
    b.shadow.style.width=(38*SC*sk)+'px'; b.shadow.style.height=(19*SC*sk)+'px';
    b.shadow.style.transform=`translate(${g[0]}px,${g[1]}px) translate(-50%,-50%)`;
    b.shadow.style.opacity=Math.max(0,0.36-b.gz*0.03);
    b.shadow.style.zIndex=Math.round(g[1]+190);
  }
  function setFace(b,dx){ if(Math.abs(dx)>0.001) b.face = dx>0?1:-1; }

  function update(b,now){
    let pose=null;
    if(b.state==='in'||b.state==='out'){
      const p=Math.min(1,(now-b.t0)/b.dur), e=ease(p);
      b.gx=b.from.gx+(b.to.gx-b.from.gx)*e;
      b.gy=b.from.gy+(b.to.gy-b.from.gy)*e;
      b.gz=b.from.gz+(b.to.gz-b.from.gz)*e;
      if(b.state==='in') setFace(b,(b.to.gx-b.to.gy)-(b.from.gx-b.from.gy));  // face travel on the way in; keep facing on the way out
      pose={flap:0.5+Math.sin(now/1000*9+b.id.length)*0.5, fly:true};
      if(p>=1){
        if(b.state==='in'){ b.state='ground'; b.action='idle'; b.actEnd=now+600; b.dwellEnd=now+(7000+Math.random()*11000)/SPEED; }
        else { remove(b); return; }
      }
    } else { // ground
      // Detection-driven: a grounded bird stays as long as its species is present;
      // only setPresent() drops it (15 min after the last detection). The random
      // dwell timer is demo-only, so the yard doesn't churn birds in and out.
      if(SIM && now>b.dwellEnd){ leave(b,now); }
      else {
        if(now>b.actEnd) nextAction(b,now);
        if(b.action==='peck'){ const ph=((now-b.actStart)%620)/620; pose={peck:Math.sin(ph*Math.PI)*1.05}; }
        else if(b.action==='hop'){ const p=Math.min(1,(now-b.actStart)/b.actDur);
          b.gx=b.hopFrom.gx+(b.hopTo.gx-b.hopFrom.gx)*p; b.gy=b.hopFrom.gy+(b.hopTo.gy-b.hopFrom.gy)*p;
          b.gz=Math.sin(p*Math.PI)*0.8; pose={flap:Math.sin(p*Math.PI)*0.5};
          if(p>=1)b.gz=0; }
        else { b.gz=0; } // idle
      }
    }
    placeBird(b);
    b.svg.innerHTML = renderBird(b.model,pose);
  }
  function nextAction(b,now){
    const r=Math.random();
    b.actStart=now;
    if(r<0.42){ b.action='peck'; b.actDur=1200+Math.random()*1400; b.actEnd=now+b.actDur; }
    else if(r<0.7){ // hop to a nearby CLEAR spot, else just idle
      let ht=null;
      for(let i=0;i<6;i++){ const nx=clamp(b.gx+(Math.random()-0.5)*3.2,1.3,GW-1.3), ny=clamp(b.gy+(Math.random()-0.5)*3.2,1.3,GH-1.3); if(clear(nx,ny,b)){ ht={gx:nx,gy:ny}; break; } }
      if(!ht){ b.action='idle'; b.actEnd=now+900+Math.random()*1200; return; }
      b.action='hop'; b.actDur=460; b.actEnd=now+b.actDur+200; b.hopFrom={gx:b.gx,gy:b.gy}; b.hopTo=ht;
      setFace(b,(b.hopTo.gx-b.hopTo.gy)-(b.gx-b.gy)); }
    else if(r<0.85){ b.action='turn'; b.face*=-1; b.actDur=300; b.actEnd=now+1000; }
    else { b.action='idle'; b.actEnd=now+800+Math.random()*1800; }
  }
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  function leave(b,now){
    b.state='out'; b.t0=now; b.dur=1500+Math.random()*700;
    b.from={gx:b.gx,gy:b.gy,gz:0};
    // exit in the direction the bird is FACING (so it flies out facing forward).
    // face>0 = facing +X (screen down-right) -> leave the +X edge; face<0 -> leave the +Y edge.
    if(b.face>0) b.to={gx:GW+3, gy:clamp(b.gy+(Math.random()-0.3),0,GH), gz:5+Math.random()*2};
    else         b.to={gx:clamp(b.gx+(Math.random()-0.3),0,GW), gy:GH+3, gz:5+Math.random()*2};
    if(selected===b) closeCallout();
  }
  function remove(b){ b.dead=true; b.el.remove(); b.shadow.remove(); if(b.tag){b.tag.remove();b.tag=null;} const i=birds.indexOf(b); if(i>=0)birds.splice(i,1); if(selected===b){selected=null;closeCallout();} }

  /* ---------------- selection / callout ---------------- */
  let selected=null, callout=null;
  function select(b){
    selected=b; birds.forEach(x=>x.el.classList.toggle('sel',x===b));
    if(!callout){ callout=document.createElement('div'); callout.className='callout'; field.appendChild(callout); }
    callout.innerHTML=`<div class="ctop"><div class="cname"><b>${b.sp.common}</b><i>${b.sp.sci}</i></div>
      <button class="cx" onclick="event.stopPropagation()">×</button></div>
      <div class="cmeta"><span class="cchip grp">${b.sp.group}</span><span class="cchip now">● heard now</span></div>`;
    callout.querySelector('.cx').addEventListener('click',e=>{e.stopPropagation();closeCallout();});
    positionCallout();
  }
  function positionCallout(){
    if(!callout||!selected) return;
    const b=selected, s=w2s(b.gx,b.gy,b.gz);
    callout.style.left=s[0]+'px';
    callout.style.top=(s[1]-b.fr[1]*SC-6)+'px';
  }
  function closeCallout(){ if(callout){callout.remove();callout=null;} birds.forEach(x=>x.el.classList.remove('sel')); selected=null; }
  stage.addEventListener('click',closeCallout);

  /* ---------------- panel ---------------- */
  let showTags=false;
  function tagFor(b){
    if(!showTags){ if(b.tag){b.tag.remove();b.tag=null;} return; }
    if(!b.tag){ b.tag=document.createElement('div'); b.tag.className='nametag'; field.appendChild(b.tag); }
    const s=w2s(b.gx,b.gy,b.gz);
    b.tag.textContent=b.sp.common;
    b.tag.style.left=s[0]+'px'; b.tag.style.top=(s[1]-b.fr[1]*SC-2)+'px';
    b.tag.style.zIndex=99999;
  }
  let lastPanel=0;
  function panel(now){
    if(now-lastPanel<400) return; lastPanel=now;
    const ground=birds.filter(b=>b.state==='ground');
    const counts={};
    ground.forEach(b=>{ counts[b.sp.id]=(counts[b.sp.id]||0)+1; });
    const ids=Object.keys(counts);
    // The dashboard element keeps only the in-stage count chip (no side panel).
    // pcount/presentlist exist only in the standalone demo shell, hence guarded.
    const pc=$('pcount'); if(pc) pc.textContent = ground.length?`${ground.length} bird${ground.length>1?'s':''}`:'';
    const cc=$('countchip') || opts.countchip;
    if(cc) cc.innerHTML = `<b>${ground.length}</b> in the yard · <b>${ids.length}</b> species`;
    if(opts.onState) opts.onState({ count: ground.length, species: ids.length });
    const list=$('presentlist');
    if(!list) return;
    if(!ids.length){ list.innerHTML=`<div class="emptyp">Quiet right now — watch for the next arrival.</div>`; return; }
    list.innerHTML = ids.map(id=>{ const sp=SPECIES[id], m=model(id);
      const sv=`<svg width="${m.w*0.42}" height="${m.h*0.42}" viewBox="${m.viewBox}">${renderBird(m,null)}</svg>`;
      return `<div class="prow" data-sp="${id}"><div class="pchip">${sv}</div>
        <div class="nm"><b>${sp.common}</b><span>${sp.group}</span></div>
        <span class="st">${counts[id]>1?counts[id]+'×':'now'}</span></div>`;
    }).join('');
    list.querySelectorAll('.prow').forEach(row=>row.addEventListener('click',()=>{
      const b=birds.find(x=>x.state==='ground'&&x.sp.id===row.dataset.sp); if(b)select(b);
    }));
  }

  /* ---------------- clock + loop ---------------- */
  // SIM defaults OFF: production drives the scene from real detections via
  // setPresent(); the random demo spawner only runs if simulate(true) is called.
  let SPEED=1, playing=true, simMin=9*60+41, lastSpawn=0, spawnGap=2500, SIM=false;
  function fmtClock(){ let h=Math.floor(simMin/60)%24,mm=Math.floor(simMin%60);const ap=h<12?'AM':'PM';h=h%12||12;
    return `${h}:${String(mm).padStart(2,'0')} ${ap}`; }
  let last=performance.now();
  let raf=0, stopped=false;
  function loop(now){
    const dt=Math.min(64,now-last); last=now;
    if(playing){
      simMin += dt/1000*0.5*SPEED;
      const ck=$('clk'); if(ck) ck.innerHTML=`${fmtClock()}<br><small>rolling 15 min</small>`;
      if(SIM && now-lastSpawn > spawnGap/SPEED){ lastSpawn=now; spawnGap=2600+Math.random()*4200; if(birds.length<MAX) spawn(); }
    }
    birds.slice().forEach(b=>{ if(playing||b.state!=='ground') update(b,now); if(!b.dead) tagFor(b); });
    positionCallout();
    panel(now);
    if(!stopped) raf=requestAnimationFrame(loop);
  }

  /* ---------------- controls (optional demo chrome) ---------------- */
  // These exist only in the standalone shell; in the dashboard element the host
  // owns the (single) name-tags toggle and calls setNameTags(). All guarded.
  $('speedseg')?.addEventListener('click',e=>{ const b=e.target.closest('button'); if(!b)return;
    [...e.currentTarget.children].forEach(x=>x.classList.toggle('on',x===b)); SPEED=+b.dataset.s; });
  $('playbtn')?.addEventListener('click',e=>{ playing=!playing; e.currentTarget.textContent=playing?'❚❚':'▶'; last=performance.now(); });
  $('tgtags')?.addEventListener('click',e=>{ showTags=!showTags; e.currentTarget.classList.toggle('on',showTags); });
  $('summon')?.addEventListener('click',()=>{ if(birds.length<MAX) spawn(); });

  const onResize=()=>{ clearTimeout(onResize._t); onResize._t=setTimeout(layout,150); };
  window.addEventListener('resize',onResize);

  // ---- go ---- (no demo auto-spawn; the scene fills from setPresent())
  // Clear the host nodes first so a remount (StrictMode double-invoke, HMR) can
  // never inherit orphaned bird/shadow DOM from a prior engine instance.
  field.innerHTML=''; scenery.innerHTML='';
  layout();
  raf=requestAnimationFrame(loop);

  /* =====================================================================
     PUBLIC API  —  this is the seam for real integration.
     The standalone build runs a random simulation (SIM=true). To drive the
     scene from real Yard-Mic detections instead:
        Aviary.simulate(false)                  // stop the random spawner
        Aviary.setPresent(['house_finch',...])  // call whenever the rolling
                                                // 15-min set of heard species changes
     setPresent() reconciles the scene: it flies in species newly heard and
     flies out species that have gone quiet. Bird ids must exist in
     AviaryVoxel.SPECIES (add new species there, same shape).
     ===================================================================== */
  function setPresent(ids){
    const want={}; (ids||[]).forEach(id=>{ if(SPECIES[id]) want[id]=(want[id]||0)+1; });
    const have={}; birds.forEach(b=>{ if(b.state!=='out') have[b.sp.id]=(have[b.sp.id]||0)+1; });
    Object.keys(want).forEach(id=>{ for(let i=(have[id]||0); i<want[id] && birds.length<MAX; i++) spawn(id); });
    birds.forEach(b=>{ if(b.state==='ground' && !want[b.sp.id]) leave(b,performance.now()); });
    // Self-heal: drop any DOM the engine no longer owns. Under React StrictMode the
    // mount effect double-invokes, and a discarded first engine can leave orphaned
    // bird/shadow nodes in the shared field that no live bird tracks (so they never
    // animate or leave). Sweep them so the rendered field always matches birds[].
    const own=new Set(); birds.forEach(b=>{ own.add(b.el); own.add(b.shadow); });
    field.querySelectorAll('.bird,.shadow').forEach(n=>{ if(!own.has(n)) n.remove(); });
  }
  function destroy(){
    stopped=true; if(raf) cancelAnimationFrame(raf);
    window.removeEventListener('resize',onResize); clearTimeout(onResize._t);
    birds.slice().forEach(remove); closeCallout();
    field.innerHTML=''; scenery.innerHTML='';
  }
  return {
    setPresent,
    heard:(id)=>{ if(SPECIES[id] && birds.length<MAX) spawn(id); },  // a single detection -> one bird flies in
    simulate:(on)=>{ SIM=!!on; },
    setNameTags:(on)=>{ showTags=!!on; },     // the dashboard element's one control
    registerSpecies,                          // merge generated NA color profiles
    species:()=>Object.keys(SPECIES),
    relayout:layout,
    destroy,
  };
}
