/* =====================================================================
   AVIARY · voxel bird system  —  3D renderer (rounded)
   ---------------------------------------------------------------------
   3D cuboids grouped into parts. Each frame parts transform in 3D (wings
   flap about the shoulder fore-aft axis; head pitches to peck), faces are
   lit + depth-sorted (legs draw UNDER the body). Each cube is drawn as a
   ROUNDED unit: a rounded silhouette underneath + its rounded visible
   faces on top, so outer corners AND edges read as truly rounded. Beaks
   are rounded pyramids. Differentiation = template proportions + colors.
   ===================================================================== */
/* eslint-disable */
// Live Aviary voxel renderer + SPECIES library. This is the original Aviary build
// code dropped in whole and kept intact (we don't rewrite it); the ONLY change from
// the source file is this module wrapper (IIFE-on-window -> ES module export at the
// bottom) so the app can import it. Don't hand-edit the geometry/color math; extend
// the registry via the engine's registerSpecies().
export const AviaryVoxel = (function () {
  const AX = 12, AY = 6.2, AZ = 11;
  const proj = p => [ (p[0]-p[1])*AX, (p[0]+p[1])*AY - p[2]*AZ ];
  const sub = (a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
  const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  const norm=v=>{const l=Math.hypot(v[0],v[1],v[2])||1;return [v[0]/l,v[1]/l,v[2]/l];};
  const VIEW = norm([1,1,1]);
  const LIGHT = norm([0.3,-0.5,0.95]);
  const R = 2.9;   // corner radius (screen px) — scaled per-model by RS below
  let RS = 1;      // rounding scale = the model's voxel scale (keeps rounding proportional to the bird)

  // ---- color ----
  function hexToRgb(h){h=h.replace('#','');if(h.length===3)h=h.split('').map(x=>x+x).join('');return [0,2,4].map(i=>parseInt(h.slice(i,i+2),16));}
  function rgbToHex(r){return '#'+r.map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join('');}
  function mix(h,f){const [r,g,b]=hexToRgb(h);const m=f<0?0:255,a=Math.abs(f);return rgbToHex([r+(m-r)*a,g+(m-g)*a,b+(m-b)*a]);}
  function shade(base,lf){const lum=hexToRgb(base).reduce((a,b)=>a+b,0)/3;const amb=lum<60?0.12:0;return mix(base,amb+lf*0.30);}

  // ---- 3D rot ----
  const rotX=(p,a,cy,cz)=>{const s=Math.sin(a),c=Math.cos(a),y=p[1]-cy,z=p[2]-cz;return [p[0],cy+y*c-z*s,cz+y*s+z*c];};
  const rotY=(p,a,cx,cz)=>{const s=Math.sin(a),c=Math.cos(a),x=p[0]-cx,z=p[2]-cz;return [cx+x*c+z*s,p[1],cz-x*s+z*c];};
  function poseVert(p,part,pose,pv){
    if(!pose) return p; let q=p;
    if(part==='wingN'&&pose.flap) q=rotX(q,pose.flap,pv.wy,pv.wz);
    if(part==='wingF'&&pose.flap) q=rotX(q,-pose.flap,-pv.wy,pv.wz);
    if(part==='head'&&pose.peck)  q=rotY(q,pose.peck,pv.nx,pv.nz);
    if(pose.tilt) q=rotY(q,pose.tilt,pv.bx,pv.bz);
    return q;
  }

  // ---- geometry helpers ----
  const sub2=(a,b)=>[a[0]-b[0],a[1]-b[1]];
  function roundedPath(pts,r){
    const n=pts.length; if(n<3) return '';
    let d='';
    for(let i=0;i<n;i++){
      const p1=pts[i],p0=pts[(i-1+n)%n],p2=pts[(i+1)%n];
      const v1=sub2(p0,p1),v2=sub2(p2,p1);
      const l1=Math.hypot(v1[0],v1[1])||1,l2=Math.hypot(v2[0],v2[1])||1;
      const rr=Math.min(r,l1*0.5,l2*0.5);
      const a=[p1[0]+v1[0]/l1*rr,p1[1]+v1[1]/l1*rr], b=[p1[0]+v2[0]/l2*rr,p1[1]+v2[1]/l2*rr];
      d+=(i===0?`M${a[0].toFixed(2)} ${a[1].toFixed(2)}`:`L${a[0].toFixed(2)} ${a[1].toFixed(2)}`);
      d+=`Q${p1[0].toFixed(2)} ${p1[1].toFixed(2)} ${b[0].toFixed(2)} ${b[1].toFixed(2)}`;
    }
    return d+'Z';
  }
  function hull(pts){
    if(pts.length<3) return pts;
    const p=pts.slice().sort((a,b)=>a[0]-b[0]||a[1]-b[1]);
    const cr=(o,a,b)=>(a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0]);
    const lo=[];for(const q of p){while(lo.length>=2&&cr(lo[lo.length-2],lo[lo.length-1],q)<=0)lo.pop();lo.push(q);}
    const up=[];for(let i=p.length-1;i>=0;i--){const q=p[i];while(up.length>=2&&cr(up[up.length-2],up[up.length-1],q)<=0)up.pop();up.push(q);}
    lo.pop();up.pop();return lo.concat(up);
  }

  // ---- cube -> render unit (rounded silhouette + rounded faces) ----
  const BOXF=[[0,3,2,1],[4,5,6,7],[0,4,7,3],[1,2,6,5],[0,1,5,4],[3,7,6,2]];
  function cubeUnit(box,color,part,pose,pv){
    const [x0,y0,z0,x1,y1,z1]=box;
    const v3=[[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]].map(v=>poseVert(v,part,pose,pv));
    const ctr=poseVert([(x0+x1)/2,(y0+y1)/2,(z0+z1)/2],part,pose,pv);
    const v2=v3.map(proj);
    const vis=[];
    for(const f of BOXF){
      const a=v3[f[0]],b=v3[f[1]],c=v3[f[2]];
      let n=norm(cross(sub(b,a),sub(c,a)));
      const fc=[(v3[f[0]][0]+v3[f[2]][0])/2,(v3[f[0]][1]+v3[f[2]][1])/2,(v3[f[0]][2]+v3[f[2]][2])/2];
      if(dot(n,sub(fc,ctr))<0) n=[-n[0],-n[1],-n[2]];
      if(dot(n,VIEW)<=0.002) continue;
      vis.push({pts:f.map(i=>v2[i]),col:shade(color,dot(n,LIGHT))});
    }
    if(!vis.length) return null;
    const sil=hull([].concat(...vis.map(f=>f.pts)));
    let cx=0,cy=0; sil.forEach(p=>{cx+=p[0];cy+=p[1];}); cx/=sil.length; cy/=sil.length;
    // rounded silhouette underneath; each face inset toward centre + rounded
    // so EVERY edge (outer silhouette AND the seams between faces) reads round.
    let svg=`<path d="${roundedPath(sil,R*RS)}" fill="${shade(color,-0.14)}"/>`;
    for(const f of vis){
      let me=1e9; for(let i=0;i<f.pts.length;i++){const a=f.pts[i],b=f.pts[(i+1)%f.pts.length];me=Math.min(me,Math.hypot(a[0]-b[0],a[1]-b[1]));}
      const mg=Math.min(1.7*RS, me*0.27);
      const ins=f.pts.map(p=>{const dx=cx-p[0],dy=cy-p[1],l=Math.hypot(dx,dy)||1;return [p[0]+dx/l*mg,p[1]+dy/l*mg];});
      svg+=`<path d="${roundedPath(ins,R*RS)}" fill="${f.col}"/>`;
    }
    return {depth:dot(ctr,VIEW),svg};
  }

  // ---- pyramid beak -> unit (rounded triangular faces) ----
  function beakUnit(hx,len,hy,zc,bill,color,part,pose,pv,cy){
    cy=cy||0;
    if(bill==="flat") return cubeUnit([hx-0.1,-hy*1.7+cy,zc-hy*0.5,hx+len,hy*1.7+cy,zc+hy*0.5],color,part,pose,pv);
    const az=(bill==="hook")?zc-hy*0.5:zc;
    const base=[[hx,-hy+cy,zc-hy],[hx,hy+cy,zc-hy],[hx,hy+cy,zc+hy],[hx,-hy+cy,zc+hy]];
    const apex=[hx+len,cy,az];
    const tris=[[base[3],base[2],apex],[base[1],base[0],apex],[base[2],base[1],apex],[base[0],base[3],apex]];
    const ctr=poseVert([hx+len*0.4,0,zc],part,pose,pv);
    const vis=[];
    for(const t of tris){
      const T3=t.map(v=>poseVert(v,part,pose,pv));
      let n=norm(cross(sub(T3[1],T3[0]),sub(T3[2],T3[0])));
      const fc=[(T3[0][0]+T3[1][0]+T3[2][0])/3,(T3[0][1]+T3[1][1]+T3[2][1])/3,(T3[0][2]+T3[1][2]+T3[2][2])/3];
      if(dot(n,sub(fc,ctr))<0) n=[-n[0],-n[1],-n[2]];
      if(dot(n,VIEW)<=0.002) continue;
      vis.push({pts:T3.map(proj),col:shade(color,dot(n,LIGHT))});
    }
    if(!vis.length) return null;
    const sil=hull([].concat(...vis.map(f=>f.pts)));
    let svg=`<path d="${roundedPath(sil,R*0.7*RS)}" fill="${shade(color,-0.24)}"/>`;
    for(const f of vis) svg+=`<path d="${roundedPath(f.pts,R*0.7*RS)}" fill="${f.col}"/>`;
    return {depth:dot(poseVert([hx+len,0,zc],part,pose,pv),VIEW)+0.2,svg};
  }

  // ---- flat decal quad (eye) ----
  function decalUnit(q,color,part,pose,pv,rr,flat){
    const V=q.map(v=>poseVert(v,part,pose,pv));
    let n=norm(cross(sub(V[1],V[0]),sub(V[2],V[0])));
    if(dot(n,VIEW)<=0.002) return null;
    const fc=[(V[0][0]+V[2][0])/2,(V[0][1]+V[2][1])/2,(V[0][2]+V[2][2])/2];
    return {depth:dot(fc,VIEW)+0.45,svg:`<path d="${roundedPath(V.map(proj),rr==null?0.9:rr)}" fill="${flat?color:shade(color,dot(n,LIGHT))}"/>`};
  }

  /* --------------------------------------------------------------------
     TEMPLATES
     -------------------------------------------------------------------- */
  const T = {
    songbird:  {u:1.00, leg:1.4, lw:0.24, bl:2.7, bw:1.6,  bh:2.6, hs:1.6,  hwR:0.95, hcf:0.72, hsink:0.5,  nk:0,  bkl:1.2, bkh:0.38, tl:2.6, tu:0.5,  wh:2.0, wl:0.8,  up:0, bill:"cone"},
    dove:      {u:1.05, leg:1.1, lw:0.22, bl:3.2, bw:1.5,  bh:2.6, hs:1.4,  hwR:0.94, hcf:0.74, hsink:0.5,  nk:0,  bkl:0.9, bkh:0.26, tl:3.6, tu:0.2,  wh:2.0, wl:0.8,  up:0, bill:"cone"},
    corvid:    {u:1.22, leg:1.7, lw:0.28, bl:3.2, bw:1.7,  bh:2.8, hs:1.8,  hwR:0.95, hcf:0.72, hsink:0.5,  nk:0,  bkl:1.8, bkh:0.46, tl:3.2, tu:0.4,  wh:2.1, wl:0.82, up:0, bill:"cone"},
    raptor:    {u:1.30, leg:1.5, lw:0.3,  bl:2.6, bw:1.7,  bh:3.0, hs:1.7,  hwR:0.97, hcf:0.7,  hsink:0.5,  nk:0,  bkl:0.9, bkh:0.46, tl:2.6, tu:-0.2, wh:2.5, wl:0.82, up:1, bill:"hook"},
    hummingbird:{u:0.6,leg:0.5, lw:0.18, bl:2.2, bw:1.05, bh:1.8, hs:1.2,  hwR:0.97, hcf:0.74, hsink:0.46, nk:0,  bkl:2.8, bkh:0.16, tl:2.0, tu:0.1,  wh:1.8, wl:0.8,  up:0, bill:"needle"},
    waterfowl: {u:1.10, leg:0.8, lw:0.24, bl:3.6, bw:1.6,  bh:2.5, hs:1.4,  hwR:0.94, hcf:0.76, hsink:0.42, nk:1.4,bkl:1.5, bkh:0.46, tl:2.2, tu:0.8,  wh:2.1, wl:0.78, up:0, bill:"flat"},
    shorebird: {u:1.00, leg:3.6, lw:0.16, bl:2.5, bw:1.2,  bh:2.0, hs:1.3,  hwR:0.95, hcf:0.74, hsink:0.46, nk:0.6,bkl:2.8, bkh:0.16, tl:1.7, tu:0.4,  wh:1.7, wl:0.8,  up:0, bill:"needle"},
    wading:    {u:1.28, leg:5.0, lw:0.15, bl:2.6, bw:1.3,  bh:2.4, hs:1.25, hwR:0.97, hcf:0.7,  hsink:0.42, nk:4.0,bkl:2.6, bkh:0.28, tl:1.8, tu:0.3,  wh:1.9, wl:0.8,  up:0, bill:"needle"},
  };

  function geom(tpl){
    const t=T[tpl]||T.songbird, u=t.u, m=v=>v*u;
    const bw=m(t.bw), z0=m(t.leg), bl=m(t.bl), bh=m(t.bh);
    const bx0=0,bx1=bl,bz0=z0,bz1=z0+bh;
    const hs=m(t.hs), hw=hs*t.hwR/2;
    const hcx=bx0+bl*t.hcf, hx0=hcx-hs/2, hx1=hcx+hs/2;    // head centered OVER the body (not its front edge)
    let hz0=bz1-hs*0.12;                                   // head sits ON TOP, bottom connects at body top
    if(t.up) hz0=bz1-hs*t.hsink+m(0.25);
    if(t.nk) hz0=bz1+m(t.nk)-hs*0.25;
    const hz1=hz0+hs, hmz=hz0+hs*0.5;
    const wh=m(t.wh), wx0=bx0+bl*0.14, wx1=bx0+bl*t.wl, wz1=bz1-bh*0.04, wz0=wz1-wh;
    return {t,u,m,bw,bl,bh,bx0,bx1,bz0,bz1,hs,hw,hx0,hx1,hz0,hz1,hmz,wh,wx0,wx1,wz0,wz1,z0};
  }

  /* --------------------------------------------------------------------
     buildBird(spec) -> model
     -------------------------------------------------------------------- */
  function buildBird(spec){
    const g=geom(spec.template), t=g.t, c=spec.colors, pat=spec.pattern||"plain", m=g.m;
    const backC=c.back, wingC=c.wing||mix(backC,-0.15), throatC=c.throat||c.belly||mix(backC,0.3);
    const billC=c.bill||"#3a3840", legC=c.legs||"#5e4d36";
    const crownC=(pat==="capped"||pat==="hooded")?(c.cap||mix(backC,-0.22)):(c.face||backC);
    const faceC =(pat==="hooded")?(c.cap||mix(backC,-0.22)):(c.face||backC);

    const cubes=[], decals=[], beaks=[];
    const C=(box,col,part)=>cubes.push({box,col,part:part||'body'});

    // tail
    const tl=m(t.tl),tu=m(t.tu),tz0=g.bz0+g.bh*0.24+tu,tz1=tz0+g.bh*0.46;
    C([g.bx0-tl,-g.bw*0.8,tz0, g.bx0+0.3,g.bw*0.8,tz1], wingC,'tail');
    if(c.tailTip) C([g.bx0-tl,-g.bw*0.82,tz0, g.bx0-tl+m(0.5),g.bw*0.82,tz1], c.tailTip,'tail');

    // wings
    C([g.wx0,-g.bw-m(0.4),g.wz0, g.wx1,-g.bw+0.04,g.wz1], mix(wingC,-0.06),'wingF');
    C([g.wx0, g.bw-0.04,g.wz0, g.wx1, g.bw+m(0.4),g.wz1], wingC,'wingN');

    // body (back) + belly on lower-front + breast on upper-front face (both visible)
    C([g.bx0,-g.bw,g.bz0, g.bx1,g.bw,g.bz1], backC,'body');
    const bellyC=c.belly||mix(backC,0.28), fy=g.bw*0.64;
    const zMid=g.bz0+g.bh*0.36, zHi=g.bz0+g.bh*0.82;
    decals.push({part:'body',col:bellyC, rr:1.3,q:[[g.bx1,-fy,g.bz0+0.05],[g.bx1,-fy,zMid],[g.bx1,fy,zMid],[g.bx1,fy,g.bz0+0.05]]});
    decals.push({part:'body',col:throatC,rr:1.3,q:[[g.bx1,-fy,zMid],[g.bx1,-fy,zHi],[g.bx1,fy,zHi],[g.bx1,fy,zMid]]});

    // legs (skinny) + claw feet
    if(g.z0>0.35){
      const lw=m(t.lw), lx=g.bx0+g.bl*0.5-lw/2, claw=m(0.55), cw=m(0.12), ch=m(0.18);
      [-m(0.42), m(0.18)].forEach(ly=>{
        C([lx,ly,0, lx+lw,ly+lw,g.bz0+0.15], legC,'legs');
        // three toes: forward, fore-side, back
        C([lx+lw*0.4,ly-cw*0.3,0, lx+lw*0.4+claw,ly-cw*0.3+cw,ch], mix(legC,-0.06),'legs');
        C([lx+lw*0.4,ly+lw*0.6,0, lx+lw*0.4+claw*0.8,ly+lw*0.6+cw,ch], mix(legC,-0.06),'legs');
        C([lx-claw*0.55,ly+lw*0.1,0, lx+lw*0.4,ly+lw*0.1+cw,ch], mix(legC,-0.1),'legs');
      });
    }

    // neck (under the head)
    if(t.nk){
      const nw=m(t.nk>2?0.6:0.9), ncx=(g.hx0+g.hx1)/2;
      C([ncx-nw/2,-nw/2,g.bz1-g.bh*0.2, ncx+nw/2,nw/2,g.hz0+g.hs*0.3], faceC,'body');
    }

    // head  (nudged toward -Y so it reads centered: shifts up-and-right on screen)
    const yo=-g.bw*0.22, hcy=yo;
    const splitZ=(pat==="capped")?g.hz0+g.hs*0.5:g.hz1;
    C([g.hx0,-g.hw+yo,g.hz0, g.hx1,g.hw+yo,splitZ], faceC,'head');
    if(splitZ<g.hz1) C([g.hx0,-g.hw+yo,splitZ, g.hx1,g.hw+yo,g.hz1], crownC,'head');

    // beak (rounded pyramid)
    beaks.push({hx:g.hx1, len:m(t.bkl), hy:m(t.bkh), zc:g.hmz, bill:t.bill, col:billC, part:'head', cy:hcy});

    // eye decal (on the +Y cheek of the shifted head)
    const eS=g.hs*0.24, eX=g.hx0+g.hs*0.52, eZ=Math.max(g.bz1+g.hs*0.04, g.hz0+g.hs*0.46), eY=g.hw+yo;
    decals.push({part:'head',col:"#1b1922",rr:0.8,flat:true,q:[[eX,eY,eZ],[eX,eY,eZ+eS],[eX+eS,eY,eZ+eS],[eX+eS,eY,eZ]]});
    decals.push({part:'head',col:"#f3f2f6",rr:0.5,flat:true,q:[[eX+eS*0.48,eY,eZ+eS*0.52],[eX+eS*0.48,eY,eZ+eS*0.88],[eX+eS*0.86,eY,eZ+eS*0.88],[eX+eS*0.86,eY,eZ+eS*0.52]]});

    const pv={wy:g.bw,wz:g.wz1,nx:(g.hx0+g.hx1)/2,nz:g.hz0-g.hs*0.5,bx:g.bl*0.5,bz:(g.bz0+g.bz1)/2};

    // viewBox over extreme poses
    const poses=[null,{flap:1.2},{peck:1.0}];
    let minX=1e9,minY=1e9,maxX=-1e9,maxY=-1e9;
    RS=g.u;
    poses.forEach(po=>{collectUnits({cubes,decals,beaks,pv},po).forEach(u=>{
      (u.svg.match(/-?\d+\.?\d*[ ,]-?\d+\.?\d*/g)||[]).forEach(s=>{const[a,b]=s.split(/[ ,]/).map(Number);if(isFinite(a)&&isFinite(b)){minX=Math.min(minX,a);maxX=Math.max(maxX,a);minY=Math.min(minY,b);maxY=Math.max(maxY,b);}});
    });});
    RS=1;
    const pad=4, vb=`${(minX-pad).toFixed(1)} ${(minY-pad).toFixed(1)} ${(maxX-minX+pad*2).toFixed(1)} ${(maxY-minY+pad*2).toFixed(1)}`;
    const feet=proj([g.bx0+g.bl*0.5,0,0]);
    const bodyC=proj([g.bx0+g.bl*0.5,0,(g.bz0+g.bz1)/2]);
    const headC=proj([(g.hx0+g.hx1)/2,0,(g.hz0+g.hz1)/2]);
    const topC=proj([g.bx0+g.bl*0.5,0,g.bz1]);
    const topQuad=[proj([g.bx0,-g.bw,g.bz1]),proj([g.bx1,-g.bw,g.bz1]),proj([g.bx1,g.bw,g.bz1]),proj([g.bx0,g.bw,g.bz1])];
    const frontMid=proj([g.bx1,0,g.bz1]);
    const headBase=proj([(g.hx0+g.hx1)/2,0,g.hz0]);
    return {cubes,decals,beaks,pv,rs:g.u,anchors:{feet,bodyC,headC,topC,topQuad,frontMid,headBase},viewBox:vb,bounds:{minX,minY,maxX,maxY},w:maxX-minX,h:maxY-minY};
  }

  function collectUnits(model,pose){
    const out=[];
    model.cubes.forEach(cb=>{ if(pose&&pose.fly&&cb.part==='legs') return; const u=cubeUnit(cb.box,cb.col,cb.part,pose,model.pv); if(u)out.push(u); });
    model.beaks.forEach(b=>{ const u=beakUnit(b.hx,b.len,b.hy,b.zc,b.bill,b.col,b.part,pose,model.pv,b.cy); if(u)out.push(u); });
    model.decals.forEach(d=>{ const u=decalUnit(d.q,d.col,d.part,pose,model.pv,d.rr,d.flat); if(u)out.push(u); });
    out.sort((a,b)=>a.depth-b.depth);
    return out;
  }
  function renderBird(model,pose){ RS=model.rs||1; const o=collectUnits(model,pose).map(u=>u.svg).join(''); RS=1; return o; }
  // render a list of {box,col} cubes (scene props/ground) — same rounded style
  function renderCubes(cubes){
    const out=cubes.map(c=>cubeUnit(c.box,c.col,'s',null,null)).filter(Boolean);
    out.sort((a,b)=>a.depth-b.depth);
    const svg=out.map(u=>u.svg).join('');
    let mnx=1e9,mny=1e9,mxx=-1e9,mxy=-1e9;
    (svg.match(/-?\d+\.?\d*[ ,]-?\d+\.?\d*/g)||[]).forEach(s=>{const[a,b]=s.split(/[ ,]/).map(Number);if(isFinite(a)&&isFinite(b)){mnx=Math.min(mnx,a);mxx=Math.max(mxx,a);mny=Math.min(mny,b);mxy=Math.max(mxy,b);}});
    return {svg,minX:mnx,minY:mny,w:mxx-mnx,h:mxy-mny};
  }
  function birdSVG(spec){ const m=buildBird(spec); return {html:`<g class="b-root">${renderBird(m,null)}</g>`, model:m, ...m}; }

  /* ===================================================================== registry */
  const SPECIES = {
    house_finch:   {id:"house_finch",common:"House Finch",sci:"Haemorhous mexicanus",template:"songbird",group:"Songbird",pattern:"capped",
      colors:{back:"#9a7b63",belly:"#e7ddcd",wing:"#7c6450",throat:"#c0413a",cap:"#c0413a",face:"#a98a6f",bill:"#6d5c44",legs:"#5f4e3a"}},
    lesser_goldfinch:{id:"lesser_goldfinch",common:"Lesser Goldfinch",sci:"Spinus psaltria",template:"songbird",group:"Songbird",pattern:"capped",
      colors:{back:"#8f8b3c",belly:"#f0e150",wing:"#2c2c2c",throat:"#f0e150",cap:"#2b2b2b",face:"#cdc23e",bill:"#b8b0a0",legs:"#5a5240"}},
    chickadee:     {id:"chickadee",common:"Chestnut-backed Chickadee",sci:"Poecile rufescens",template:"songbird",group:"Songbird",pattern:"hooded",
      colors:{back:"#9a5a32",belly:"#efe7da",wing:"#8a6a52",throat:"#efe7da",cap:"#2f2a26",face:"#f3eee6",bill:"#2d2a28",legs:"#574c40"}},
    junco:         {id:"junco",common:"Dark-eyed Junco",sci:"Junco hyemalis",template:"songbird",group:"Songbird",pattern:"hooded",
      colors:{back:"#6f6e76",belly:"#efeae2",wing:"#5a5961",throat:"#efeae2",cap:"#3b3a44",face:"#3b3a44",bill:"#e9b9c2",legs:"#c98e8e"}},
    annas_hummingbird:{id:"annas_hummingbird",common:"Anna's Hummingbird",sci:"Calypte anna",template:"hummingbird",group:"Hummingbird",pattern:"hooded",
      colors:{back:"#3f9166",belly:"#cdd6c4",wing:"#3a4a44",throat:"#bd285f",cap:"#bd285f",face:"#bd285f",bill:"#222",legs:"#333"}},
    ca_towhee:     {id:"ca_towhee",common:"California Towhee",sci:"Melozone crissalis",template:"songbird",group:"Songbird",pattern:"plain",
      colors:{back:"#8a7560",belly:"#a08b73",wing:"#7b6651",throat:"#b08a52",face:"#8a7560",bill:"#7a6a52",legs:"#6b5a44"}},
    robin:         {id:"robin",common:"American Robin",sci:"Turdus migratorius",template:"songbird",group:"Songbird",pattern:"capped",
      colors:{back:"#5e5a52",belly:"#e7ddcb",wing:"#4f4c45",throat:"#c1632b",cap:"#2c2a28",face:"#3a3834",bill:"#e0a83a",legs:"#6b5236"}},
    oak_titmouse:  {id:"oak_titmouse",common:"Oak Titmouse",sci:"Baeolophus inornatus",template:"songbird",group:"Songbird",pattern:"plain",
      colors:{back:"#9a948a",belly:"#d8d2c7",wing:"#8a857b",throat:"#cfc9be",face:"#a39d93",bill:"#3a3632",legs:"#5e564c"}},
    spotted_towhee:{id:"spotted_towhee",common:"Spotted Towhee",sci:"Pipilo maculatus",template:"songbird",group:"Songbird",pattern:"hooded",
      colors:{back:"#2a2730",belly:"#efe7da",wing:"#8a3f24",throat:"#b14a2b",cap:"#211f27",face:"#211f27",bill:"#2a2830",legs:"#9a8266"}},
    wc_sparrow:    {id:"wc_sparrow",common:"White-crowned Sparrow",sci:"Zonotrichia leucophrys",template:"songbird",group:"Songbird",pattern:"capped",
      colors:{back:"#9a8b6e",belly:"#d9d3c6",wing:"#7e6f52",throat:"#b9b2a4",cap:"#e9e6df",face:"#7d7468",bill:"#e0a23a",legs:"#7a6248"}},
    cedar_waxwing: {id:"cedar_waxwing",common:"Cedar Waxwing",sci:"Bombycilla cedrorum",template:"songbird",group:"Songbird",pattern:"capped",
      colors:{back:"#b59a78",belly:"#e9d98a",wing:"#7d7e86",throat:"#cdb48c",cap:"#7a5f44",face:"#c9ad85",bill:"#2c2a2e",legs:"#3a3632",tailTip:"#e2b53a"}},
    scrub_jay:     {id:"scrub_jay",common:"California Scrub-Jay",sci:"Aphelocoma californica",template:"corvid",group:"Corvid",pattern:"hooded",
      colors:{back:"#8a96a6",belly:"#dad7cd",wing:"#2f6bb0",throat:"#e8e6df",cap:"#2f6bb0",face:"#3d74b8",bill:"#23232a",legs:"#33333a"}},
    crow:          {id:"crow",common:"American Crow",sci:"Corvus brachyrhynchos",template:"corvid",group:"Corvid",pattern:"plain",
      colors:{back:"#2f2d38",belly:"#35333e",wing:"#262430",throat:"#35333e",face:"#2f2d38",bill:"#1a1820",legs:"#1a1820"}},
    mourning_dove: {id:"mourning_dove",common:"Mourning Dove",sci:"Zenaida macroura",template:"dove",group:"Dove / Pigeon",pattern:"plain",
      colors:{back:"#a59481",belly:"#d8c3ab",wing:"#8d7c69",throat:"#c8a98c",face:"#b29e89",bill:"#2e2a2a",legs:"#c06a5a"}},
    coopers_hawk:  {id:"coopers_hawk",common:"Cooper's Hawk",sci:"Astur cooperii",template:"raptor",group:"Bird of prey",pattern:"capped",
      colors:{back:"#5d6675",belly:"#ece4d8",wing:"#4b525f",throat:"#d6a08c",cap:"#33363f",face:"#7a8290",bill:"#33363f",legs:"#e0b83a"}},
    mallard:       {id:"mallard",common:"Mallard",sci:"Anas platyrhynchos",template:"waterfowl",group:"Waterfowl",pattern:"hooded",
      colors:{back:"#8d8779",belly:"#cfc6b6",wing:"#5b6a52",throat:"#7a5a3a",cap:"#2f7d56",face:"#2f7d56",bill:"#d6b53a",legs:"#d68a3a"}},
    sandpiper:     {id:"sandpiper",common:"Sandpiper",sci:"Calidris",template:"shorebird",group:"Shorebird",pattern:"plain",
      colors:{back:"#9a8a72",belly:"#efe9dd",wing:"#7e6f58",throat:"#d8cdbb",face:"#a89880",bill:"#2c2a2a",legs:"#3a3836"}},
    heron:         {id:"heron",common:"Great Blue Heron",sci:"Ardea herodias",template:"wading",group:"Wading bird",pattern:"capped",
      colors:{back:"#7d8b9c",belly:"#aeb7c2",wing:"#5f6b7c",throat:"#cdd2d8",cap:"#2b2f38",face:"#cdd2d8",bill:"#d6c24a",legs:"#3a3a40"}},
  };
  const YARD_IDS = ["house_finch","lesser_goldfinch","chickadee","junco","annas_hummingbird",
    "ca_towhee","robin","oak_titmouse","spotted_towhee","wc_sparrow","cedar_waxwing",
    "scrub_jay","crow","mourning_dove","coopers_hawk"];

  return { buildBird, renderBird, renderCubes, birdSVG, SPECIES, YARD_IDS, T, mix, proj, AX, AY, AZ };
})();
