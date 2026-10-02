/* 3D view of the cut / fill map.
   Loaded as a module after app.js; it reads the app's globals (state, cellInfo, renderStory,
   pageSize, ensureImage, C, fmtZ, signed1) and exposes window.View3D = {show, hide, update, resize}.

   Frame: x = sheet x (ft), y = elevation above the site's mid elevation (ft, times the vertical
   exaggeration), z = sheet y (ft) - so the top of the sheet is away from the viewer.

   Layers (each can be switched off):
     ground      the existing grade as a solid block: elevation tints (or the sheet drawing),
                 earth-coloured skirts down to a base plane
     contours    iso-lines of the existing grade (dark) and of the proposed grade (orange)
     volumes     the material between the two grades: red where the existing ground is above
                 the proposed grade (cut), blue where it is below (fill)
     proposed    the proposed grade itself as a translucent surface
     sources     for the hovered / pinned cell: lines to the evidence points its heights came from */
import * as THREE from './vendor/three.module.min.js';

const $ = (id) => document.getElementById(id);
const hex6 = (h, fb) => (/^#([0-9a-f]{6})$/i.test((h || '').trim()) ? h.trim() : fb);
const rgb = (h) => { const n = parseInt(h.slice(1), 16); return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]; };
const CUT = hex6(C.cut, '#c62828'), FILL = hex6(C.fill, '#1565c0'), EX = hex6(C.ex, '#178a5e'), PR = hex6(C.pr, '#d9480f'), ACC = hex6(C.accent, '#2457c5');
// elevation tints, low to high (green valley floor to pale summit)
const RAMP = ['#2e7d32', '#7cb342', '#e6d64f', '#c98a3f', '#8d5a2b', '#f1e9df'].map(rgb);
const EARTH = 0x9b7b58, BASE = 0x6f563d;

const V = {
  ready: false, active: false, renderer: null, scene: null, camera: null, group: null, marks: null,
  objs: {}, matTint: null, matSheet: null, tex: null, texKey: null, texTimer: 0,
  built: null, hf: null, zmid: 0, zmin: 0, zmax: 0, ybase: 0, interval: 1, home: null, raf: 0,
  drag: null, mouse: { x: 0, y: 0 }, cell: null,
  opts: { vx: 3, ground: true, contours: true, volumes: true, proposed: false, sources: true, texture: 'tint' },
  orbit: { target: new THREE.Vector3(), dist: 1000, az: 0.55, el: 0.75 },
};

// ---------------------------------------------------------------- set-up
function init() {
  if (V.ready) return true;
  const box = $('v3d');
  try { V.renderer = new THREE.WebGLRenderer({ antialias: true }); }
  catch (e) { hint('3D needs WebGL, which this browser did not provide: ' + e.message); return false; }
  V.renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  V.renderer.setClearColor(0xe4e9ef);
  box.insertBefore(V.renderer.domElement, box.firstChild);
  V.scene = new THREE.Scene();
  V.camera = new THREE.PerspectiveCamera(45, 1, 0.5, 200000);
  V.scene.add(new THREE.HemisphereLight(0xffffff, 0x8f99a6, 1.0));
  const sun = new THREE.DirectionalLight(0xffffff, 1.0); sun.position.set(0.5, 1, 0.7); V.scene.add(sun);
  V.group = new THREE.Group(); V.scene.add(V.group);          // scaled in y by the exaggeration
  V.marks = new THREE.Group(); V.scene.add(V.marks);          // unscaled: y is multiplied by hand
  bindControls();
  bindOptions();
  V.ready = true;
  return true;
}
function hint(text) { const h = $('v3dHint'); h.hidden = !text; h.textContent = text || ''; }
function resize() {
  if (!V.ready) return;
  const st = $('stage'); const w = st.clientWidth, h = st.clientHeight;
  if (!w || !h) return;
  V.renderer.setSize(w, h);
  V.camera.aspect = w / h; V.camera.updateProjectionMatrix();
}

// ---------------------------------------------------------------- helpers
function ramp(t) {
  const n = RAMP.length - 1; const x = Math.max(0, Math.min(1, t)) * n; const i = Math.min(n - 1, Math.floor(x)); const f = x - i;
  const a = RAMP[i], b = RAMP[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
function pickInterval(range) { for (const iv of [0.5, 1, 2, 5, 10, 20, 50]) if (range / iv <= 40) return iv; return 100; }
function disposeObj(o) { o.traverse((c) => { if (c.geometry) c.geometry.dispose(); if (c.material && c.material.dispose && c.material !== V.matTint && c.material !== V.matSheet) c.material.dispose(); }); }
function clearScene() {
  for (const k of Object.keys(V.objs)) { const o = V.objs[k]; if (o) { V.group.remove(o); disposeObj(o); } }
  V.objs = {};
  clearMarks();
}
function clearMarks() { while (V.marks.children.length) { const c = V.marks.children.pop(); disposeObj(c); } }
function mesh(pos, idx, material, extra) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  if (extra) for (const [name, arr, n] of extra) geo.setAttribute(name, new THREE.BufferAttribute(arr, n));
  geo.setIndex(idx); geo.computeVertexNormals();
  return new THREE.Mesh(geo, material);
}
/** Marching squares over a downsampled height field; returns line-segment positions (y unscaled). */
function isoLines(H, ok, nx, ny, xOf, zOf, levels, lift) {
  const out = [];
  const E = [[0, 1], [1, 2], [2, 3], [3, 0]];                  // corner pairs of the four edges
  const TABLE = { 1: [3, 0], 2: [0, 1], 3: [3, 1], 4: [1, 2], 5: [3, 0, 1, 2], 6: [0, 2], 7: [3, 2], 8: [2, 3], 9: [0, 2], 10: [0, 1, 2, 3], 11: [1, 2], 12: [1, 3], 13: [0, 1], 14: [3, 0] };
  for (let b = 0; b < ny - 1; b++) {
    for (let a = 0; a < nx - 1; a++) {
      const v = [b * nx + a, b * nx + a + 1, (b + 1) * nx + a + 1, (b + 1) * nx + a];
      if (!(ok[v[0]] && ok[v[1]] && ok[v[2]] && ok[v[3]])) continue;
      const h = [H[v[0]], H[v[1]], H[v[2]], H[v[3]]];
      const lo = Math.min(h[0], h[1], h[2], h[3]), hi = Math.max(h[0], h[1], h[2], h[3]);
      const px = [xOf(a), xOf(a + 1), xOf(a + 1), xOf(a)], pz = [zOf(b), zOf(b), zOf(b + 1), zOf(b + 1)];
      for (const L of levels) {
        if (L < lo || L >= hi) continue;
        const idx = (h[0] > L ? 1 : 0) | (h[1] > L ? 2 : 0) | (h[2] > L ? 4 : 0) | (h[3] > L ? 8 : 0);
        const segs = TABLE[idx]; if (!segs) continue;
        for (let k = 0; k < segs.length; k += 2) {
          for (const e of [segs[k], segs[k + 1]]) {
            const [i0, i1] = E[e]; const t = (L - h[i0]) / (h[i1] - h[i0] || 1e-9);
            out.push(px[i0] + (px[i1] - px[i0]) * t, L - V.zmid + lift, pz[i0] + (pz[i1] - pz[i0]) * t);
          }
        }
      }
    }
  }
  return new Float32Array(out);
}

// ---------------------------------------------------------------- surfaces
/** Build every layer from the result's grid (downsampled to ~160k vertices per mesh). */
function build(r) {
  clearScene();
  const g = r.grid, s = r.ft_per_pt, dk = r.display_page;
  const sub = gridSub(r);      // an older result's grid is at finished grade: its section comes off here
  const { w: pageW, h: pageH } = pageSize(dk);
  const step = Math.max(1, Math.ceil(Math.sqrt((g.nx * g.ny) / 160000)));
  const nx = Math.floor((g.nx - 1) / step) + 1, ny = Math.floor((g.ny - 1) / step) + 1, N = nx * ny;
  let zmin = Infinity, zmax = -Infinity;
  for (let idx = 0; idx < g.eg.length; idx++) {
    const a = g.eg[idx], b = g.fg[idx] == null ? null : g.fg[idx] - sub;
    if (a != null) { if (a < zmin) zmin = a; if (a > zmax) zmax = a; }
    if (b != null) { if (b < zmin) zmin = b; if (b > zmax) zmax = b; }
  }
  if (!Number.isFinite(zmin)) return false;
  const range = Math.max(zmax - zmin, 1);
  V.zmin = zmin; V.zmax = zmax; V.zmid = (zmin + zmax) / 2; V.ybase = zmin - Math.max(8, 0.25 * range) - V.zmid;
  V.interval = pickInterval(range);
  const zmid = V.zmid;
  const xOf = (a) => (g.x0 + (a * step + 0.5) * g.cell_pt) * s, zOf = (b) => (g.y0 + (b * step + 0.5) * g.cell_pt) * s;
  // vertex arrays: existing (E), proposed (P), tints, uv
  const HE = new Float32Array(N), HP = new Float32Array(N), okE = new Uint8Array(N), okP = new Uint8Array(N);
  const posE = new Float32Array(N * 3), posP = new Float32Array(N * 3), tint = new Float32Array(N * 3), uv = new Float32Array(N * 2);
  for (let b = 0; b < ny; b++) {
    for (let a = 0; a < nx; a++) {
      const i = a * step, j = b * step, idx = j * g.nx + i, v = b * nx + a;
      const xpt = g.x0 + (i + 0.5) * g.cell_pt, ypt = g.y0 + (j + 0.5) * g.cell_pt;
      const x = xpt * s, z = ypt * s;
      posE[3 * v] = x; posE[3 * v + 2] = z; posP[3 * v] = x; posP[3 * v + 2] = z;
      uv[2 * v] = xpt / pageW; uv[2 * v + 1] = 1 - ypt / pageH;
      const eg = g.eg[idx], fg = g.fg[idx] == null ? null : g.fg[idx] - sub;
      if (eg != null) { HE[v] = eg; okE[v] = 1; posE[3 * v + 1] = eg - zmid; const c = ramp((eg - zmin) / range); tint[3 * v] = c[0]; tint[3 * v + 1] = c[1]; tint[3 * v + 2] = c[2]; }
      if (fg != null) { HP[v] = fg; okP[v] = 1; posP[3 * v + 1] = fg - zmid; }
    }
  }
  const quad = (v00, v10, v01, v11, arr) => arr.push(v00, v01, v10, v10, v01, v11);
  const idxE = [], idxP = [];
  const qE = new Uint8Array((nx - 1) * (ny - 1)), qP = new Uint8Array((nx - 1) * (ny - 1));
  for (let b = 0; b < ny - 1; b++) {
    for (let a = 0; a < nx - 1; a++) {
      const v00 = b * nx + a, v10 = v00 + 1, v01 = v00 + nx, v11 = v01 + 1, q = b * (nx - 1) + a;
      if (okE[v00] && okE[v10] && okE[v01] && okE[v11]) { quad(v00, v10, v01, v11, idxE); qE[q] = 1; }
      if (okP[v00] && okP[v10] && okP[v01] && okP[v11]) { quad(v00, v10, v01, v11, idxP); qP[q] = 1; }
    }
  }
  // --- ground: the existing grade, tints or the sheet drawing, with skirts and a base
  if (!V.matTint) V.matTint = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
  if (!V.matSheet) V.matSheet = new THREE.MeshLambertMaterial({ color: 0xffffff, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
  V.objs.ground = mesh(posE, idxE, V.opts.texture === 'sheet' ? V.matSheet : V.matTint, [['color', tint, 3], ['uv', uv, 2]]);
  V.group.add(V.objs.ground);
  {
    // skirts on every boundary edge of the ground, a base plane under it
    const wall = [], wIdx = [], base = [], bIdx = [];
    const yb = V.ybase;
    const addWall = (va, vb) => {
      const n = wall.length / 3;
      wall.push(posE[3 * va], posE[3 * va + 1], posE[3 * va + 2], posE[3 * vb], posE[3 * vb + 1], posE[3 * vb + 2],
                posE[3 * va], yb, posE[3 * va + 2], posE[3 * vb], yb, posE[3 * vb + 2]);
      wIdx.push(n, n + 2, n + 1, n + 1, n + 2, n + 3);
    };
    const qAt = (a, b) => (a < 0 || b < 0 || a >= nx - 1 || b >= ny - 1) ? 0 : qE[b * (nx - 1) + a];
    for (let b = 0; b < ny - 1; b++) {
      for (let a = 0; a < nx - 1; a++) {
        if (!qE[b * (nx - 1) + a]) continue;
        const v00 = b * nx + a, v10 = v00 + 1, v01 = v00 + nx, v11 = v01 + 1;
        if (!qAt(a, b - 1)) addWall(v00, v10);
        if (!qAt(a, b + 1)) addWall(v01, v11);
        if (!qAt(a - 1, b)) addWall(v00, v01);
        if (!qAt(a + 1, b)) addWall(v10, v11);
        const n = base.length / 3;
        base.push(posE[3 * v00], yb, posE[3 * v00 + 2], posE[3 * v10], yb, posE[3 * v10 + 2], posE[3 * v01], yb, posE[3 * v01 + 2], posE[3 * v11], yb, posE[3 * v11 + 2]);
        bIdx.push(n, n + 1, n + 2, n + 1, n + 3, n + 2);
      }
    }
    if (wIdx.length) { V.objs.skirt = mesh(new Float32Array(wall), wIdx, new THREE.MeshLambertMaterial({ color: EARTH, side: THREE.DoubleSide })); V.group.add(V.objs.skirt); }
    if (bIdx.length) { V.objs.base = mesh(new Float32Array(base), bIdx, new THREE.MeshLambertMaterial({ color: BASE, side: THREE.DoubleSide })); V.group.add(V.objs.base); }
  }
  // --- contour lines of both grades
  const levels = []; for (let L = Math.ceil(zmin / V.interval) * V.interval; L <= zmax; L += V.interval) levels.push(Math.round(L * 1000) / 1000);
  const lift = Math.max(0.05, range * 0.004);
  const cE = isoLines(HE, okE, nx, ny, xOf, zOf, levels, lift);
  if (cE.length) { const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(cE, 3)); V.objs.contoursE = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x2b2b2b, transparent: true, opacity: 0.55 })); V.group.add(V.objs.contoursE); }
  const cP = isoLines(HP, okP, nx, ny, xOf, zOf, levels, lift);
  if (cP.length) { const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(cP, 3)); V.objs.contoursP = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: PR, transparent: true, opacity: 0.9 })); V.group.add(V.objs.contoursP); }
  // --- volumes between the grades: cut (existing above proposed) red, fill blue, closed with walls
  {
    const sign = new Int8Array((nx - 1) * (ny - 1));
    for (let b = 0; b < ny - 1; b++) for (let a = 0; a < nx - 1; a++) {
      const q = b * (nx - 1) + a; if (!qE[q] || !qP[q]) continue;
      const v00 = b * nx + a, v10 = v00 + 1, v01 = v00 + nx, v11 = v01 + 1;
      const d = (HP[v00] - HE[v00] + HP[v10] - HE[v10] + HP[v01] - HE[v01] + HP[v11] - HE[v11]) / 4;
      sign[q] = d > 0.02 ? 1 : d < -0.02 ? -1 : 0;
    }
    for (const sgn of [-1, 1]) {
      const pos = [], idx = [];
      const top = (v) => Math.max(HE[v], HP[v]) - zmid, bot = (v) => Math.min(HE[v], HP[v]) - zmid;
      const sAt = (a, b) => (a < 0 || b < 0 || a >= nx - 1 || b >= ny - 1) ? 0 : sign[b * (nx - 1) + a];
      const put = (v, y) => { pos.push(posE[3 * v], y, posE[3 * v + 2]); return pos.length / 3 - 1; };
      const wallQ = (va, vb) => { const a0 = put(va, top(va)), b0 = put(vb, top(vb)), a1 = put(va, bot(va)), b1 = put(vb, bot(vb)); idx.push(a0, a1, b0, b0, a1, b1); };
      for (let b = 0; b < ny - 1; b++) {
        for (let a = 0; a < nx - 1; a++) {
          const q = b * (nx - 1) + a; if (sign[q] !== sgn) continue;
          const v00 = b * nx + a, v10 = v00 + 1, v01 = v00 + nx, v11 = v01 + 1;
          const t00 = put(v00, top(v00)), t10 = put(v10, top(v10)), t01 = put(v01, top(v01)), t11 = put(v11, top(v11));
          idx.push(t00, t01, t10, t10, t01, t11);
          const b00 = put(v00, bot(v00)), b10 = put(v10, bot(v10)), b01 = put(v01, bot(v01)), b11 = put(v11, bot(v11));
          idx.push(b00, b10, b01, b10, b11, b01);
          if (sAt(a, b - 1) !== sgn) wallQ(v00, v10);
          if (sAt(a, b + 1) !== sgn) wallQ(v01, v11);
          if (sAt(a - 1, b) !== sgn) wallQ(v00, v01);
          if (sAt(a + 1, b) !== sgn) wallQ(v10, v11);
        }
      }
      if (idx.length) {
        const m = mesh(new Float32Array(pos), idx, new THREE.MeshLambertMaterial({ color: sgn < 0 ? CUT : FILL, side: THREE.DoubleSide, transparent: true, opacity: 0.55, depthWrite: false }));
        m.renderOrder = 2;
        V.objs[sgn < 0 ? 'cut' : 'fill'] = m; V.group.add(m);
      }
    }
  }
  // --- the proposed grade as a coarse mesh of lines (a translucent surface here would sit on
  // the top of every fill volume and turn its blue to gray)
  {
    const every = Math.max(1, Math.round(40 / (g.cell_pt * s * step)));      // one line every ~40 ft
    const seg = [];
    const push = (v0, v1) => { if (okP[v0] && okP[v1]) seg.push(posP[3 * v0], posP[3 * v0 + 1] + lift, posP[3 * v0 + 2], posP[3 * v1], posP[3 * v1 + 1] + lift, posP[3 * v1 + 2]); };
    for (let b = 0; b < ny; b += every) for (let a = 0; a < nx - 1; a++) push(b * nx + a, b * nx + a + 1);
    for (let a = 0; a < nx; a += every) for (let b = 0; b < ny - 1; b++) push(b * nx + a, (b + 1) * nx + a);
    if (seg.length) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(seg), 3));
      V.objs.proposed = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: PR, transparent: true, opacity: 0.8 }));
      V.objs.proposed.renderOrder = 3; V.group.add(V.objs.proposed);
    }
  }
  // --- the limit, on the ground
  if (Array.isArray(r.region) && r.region.length >= 3) {
    const pts = [];
    const poly = r.region.slice(); if (poly.length && (poly[0][0] !== poly[poly.length - 1][0] || poly[0][1] !== poly[poly.length - 1][1])) poly.push(poly[0]);
    for (let k = 1; k < poly.length; k++) {
      const [ax, ay] = poly[k - 1], [bx, by] = poly[k]; const L = Math.hypot(bx - ax, by - ay); const n = Math.max(1, Math.ceil(L / (g.cell_pt * 2)));
      for (let q = 0; q <= n; q++) {
        const xpt = ax + (bx - ax) * q / n, ypt = ay + (by - ay) * q / n;
        const h = heightNear(xpt, ypt, g.eg, 6); if (h == null) continue;
        pts.push(new THREE.Vector3(xpt * s, h - zmid + lift * 2, ypt * s));
      }
    }
    if (pts.length >= 2) { V.objs.region = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: ACC })); V.group.add(V.objs.region); }
  }
  applyTexture(dk);
  // picking uses the upper of the two grades at full resolution
  const top = new Array(g.eg.length);
  for (let idx = 0; idx < g.eg.length; idx++) { const a = g.eg[idx], b = g.fg[idx] == null ? null : g.fg[idx] - sub; top[idx] = a == null ? b : b == null ? a : Math.max(a, b); }
  V.hf = { g, s, top };
  const cx = (g.x0 + g.nx * g.cell_pt / 2) * s, cz = (g.y0 + g.ny * g.cell_pt / 2) * s;
  const ext = Math.max(g.nx, g.ny) * g.cell_pt * s;
  V.home = { target: new THREE.Vector3(cx, 0, cz), dist: ext * 1.15, az: 0.55, el: 0.75 };
  V.built = r; V.cell = null;
  renderLegend();
  applyOpts(); resetView();
  return true;
}
function heightNear(xpt, ypt, arr, reach) {
  const g = V.hf ? V.hf.g : state.result.grid;
  const i0 = Math.floor((xpt - g.x0) / g.cell_pt), j0 = Math.floor((ypt - g.y0) / g.cell_pt);
  for (let rr = 0; rr <= reach; rr++) {
    for (let dj = -rr; dj <= rr; dj++) for (let di = -rr; di <= rr; di++) {
      if (Math.max(Math.abs(di), Math.abs(dj)) !== rr) continue;
      const i = i0 + di, j = j0 + dj; if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) continue;
      const h = arr[j * g.nx + i]; if (h != null) return h;
    }
  }
  return null;
}
/** The sheet image for the ground (fetched by the 2D view; retried until loaded). */
function applyTexture(dk) {
  clearTimeout(V.texTimer);
  const rec = state.images[dk];
  if (!rec || !rec.ready) {
    if (rec && rec.error) return;
    ensureImage(dk);
    V.texTimer = setTimeout(() => { if (V.built && V.built.display_page === dk) applyTexture(dk); }, 600);
    return;
  }
  if (V.texKey !== dk) { if (V.tex) V.tex.dispose(); V.tex = new THREE.Texture(rec.img); V.tex.colorSpace = THREE.SRGBColorSpace; V.tex.needsUpdate = true; V.texKey = dk; }
  V.matSheet.map = V.tex; V.matSheet.needsUpdate = true;
  applyOpts();
}
function renderLegend() {
  const box = $('v3dLegend'); if (!box) return;
  const stops = RAMP.map((c, i) => `rgb(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)}) ${Math.round(100 * i / (RAMP.length - 1))}%`).join(', ');
  box.innerHTML = '';
  const bar = document.createElement('span'); bar.className = 'v3dramp'; bar.style.background = `linear-gradient(90deg, ${stops})`;
  const lo = document.createElement('span'); lo.textContent = fmtZ(V.zmin);
  const hi = document.createElement('span'); hi.textContent = fmtZ(V.zmax) + ' ft';
  const iv = document.createElement('span'); iv.className = 'muted'; iv.textContent = ` · contours every ${V.interval} ft`;
  const cut = document.createElement('span'); cut.innerHTML = `<i class="sw" style="background:${CUT};opacity:.6"></i>cut`;
  const fill = document.createElement('span'); fill.innerHTML = `<i class="sw" style="background:${FILL};opacity:.6"></i>fill`;
  box.append(lo, bar, hi, iv, cut, fill);
}

// ---------------------------------------------------------------- camera
function applyCamera() {
  const o = V.orbit, c = V.camera;
  c.position.set(o.target.x + o.dist * Math.cos(o.el) * Math.sin(o.az), o.target.y + o.dist * Math.sin(o.el), o.target.z + o.dist * Math.cos(o.el) * Math.cos(o.az));
  c.lookAt(o.target);
  c.updateMatrixWorld();          // picking may run before the next frame renders
}
function resetView() {
  if (!V.home) return;
  V.orbit.target.copy(V.home.target); V.orbit.dist = V.home.dist; V.orbit.az = V.home.az; V.orbit.el = V.home.el;
  applyCamera();
}
function bindControls() {
  const cv = V.renderer.domElement;
  cv.addEventListener('contextmenu', (e) => e.preventDefault());
  cv.addEventListener('mousedown', (e) => {
    if (e.button !== 0 && e.button !== 2 && e.button !== 1) return;
    e.preventDefault();
    V.drag = { x: e.clientX, y: e.clientY, mode: (e.button === 0 && !e.shiftKey) ? 'turn' : 'pan', moved: false };
    cv.style.cursor = 'grabbing';
  });
  window.addEventListener('mousemove', (e) => {
    const d = V.drag; if (!d || !V.active) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y; d.x = e.clientX; d.y = e.clientY;
    if (Math.abs(dx) + Math.abs(dy) > 0) d.moved = true;
    const o = V.orbit;
    if (d.mode === 'turn') { o.az -= dx * 0.005; o.el = Math.max(0.05, Math.min(1.52, o.el + dy * 0.005)); }
    else {
      const right = new THREE.Vector3(), up = new THREE.Vector3(), fwd = new THREE.Vector3();
      V.camera.getWorldDirection(fwd); right.crossVectors(fwd, V.camera.up).normalize(); up.crossVectors(right, fwd).normalize();
      const k = o.dist * 0.0016;
      o.target.addScaledVector(right, -dx * k).addScaledVector(up, dy * k);
    }
    applyCamera();
  });
  window.addEventListener('mouseup', (e) => {
    const d = V.drag; if (!d) return;
    V.drag = null; cv.style.cursor = 'grab';
    if (!d.moved && e.button === 0 && e.target === cv) pin();
  });
  cv.addEventListener('wheel', (e) => {
    e.preventDefault();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;
    V.orbit.dist = Math.max(20, Math.min(400000, V.orbit.dist * Math.exp(dy * 0.0012)));
    applyCamera();
  }, { passive: false });
  cv.addEventListener('mousemove', (e) => {
    if (V.drag || !V.active || !V.hf) return;
    const rect = cv.getBoundingClientRect();
    V.mouse.x = e.clientX - rect.left; V.mouse.y = e.clientY - rect.top;
    hover(pickCell({ x: (V.mouse.x / rect.width) * 2 - 1, y: -(V.mouse.y / rect.height) * 2 + 1 }));
  });
  cv.addEventListener('mouseleave', () => { if (!V.drag) hover(null); });
  window.addEventListener('resize', () => { if (V.active) resize(); });
}

// ---------------------------------------------------------------- picking and provenance
function heightAt(xft, zft, arr) {
  const { g, s } = V.hf;
  const i = Math.floor((xft / s - g.x0) / g.cell_pt), j = Math.floor((zft / s - g.y0) / g.cell_pt);
  if (i < 0 || j < 0 || i >= g.nx || j >= g.ny) return null;
  const h = arr[j * g.nx + i]; return h == null ? null : h;
}
/** The grid cell under a screen point: the view ray is marched until it drops below the upper grade. */
function pickCell(ndc) {
  V.camera.updateMatrixWorld();
  const rc = new THREE.Raycaster(); rc.setFromCamera(ndc, V.camera);
  const o = rc.ray.origin, d = rc.ray.direction;
  const { g, s, top } = V.hf; const vx = V.opts.vx;
  const cellFt = g.cell_pt * s; const stepLen = Math.max(cellFt * 0.7, 0.5);
  const tmax = o.distanceTo(V.orbit.target) + Math.max(g.nx, g.ny) * cellFt * 1.5;
  let prevDiff = null, prevT = 0;
  for (let t = 0; t <= tmax; t += stepLen) {
    const px = o.x + d.x * t, py = o.y + d.y * t, pz = o.z + d.z * t;
    const h = heightAt(px, pz, top);
    if (h == null) { prevDiff = null; continue; }
    const diff = py - (h - V.zmid) * vx;
    if (prevDiff != null && prevDiff > 0 && diff <= 0) {
      let lo = prevT, hi = t;
      for (let k = 0; k < 10; k++) {
        const m = (lo + hi) / 2; const hh = heightAt(o.x + d.x * m, o.z + d.z * m, top);
        if (hh == null || (o.y + d.y * m) - (hh - V.zmid) * vx > 0) lo = m; else hi = m;
      }
      const m = (lo + hi) / 2; const fx = o.x + d.x * m, fz = o.z + d.z * m;
      const i = Math.floor((fx / s - g.x0) / g.cell_pt), j = Math.floor((fz / s - g.y0) / g.cell_pt);
      return cellInfo(i, j) ? { i, j } : null;
    }
    prevDiff = diff; prevT = t;
  }
  return null;
}
function hover(cell) {
  const same = (a, b) => (!a && !b) || (a && b && a.i === b.i && a.j === b.j);
  const tip = $('v3dTip');
  if (!same(cell, V.cell)) {
    V.cell = cell;
    state.hover = cell;
    if (!state.pinned) { renderStory(); showCell(cell); }
  }
  if (!cell) { tip.hidden = true; return; }
  const info = cellInfo(cell.i, cell.j); if (!info) { tip.hidden = true; return; }
  tip.hidden = false;
  tip.style.color = info.depth < 0 ? CUT : FILL;
  tip.textContent = `${signed1(info.depth)} ft ${info.depth < 0 ? 'cut' : 'fill'} · existing ${fmtZ(info.eg)} · ${info.section ? 'subgrade' : 'proposed'} ${fmtZ(info.fg)}`;
  tip.style.left = Math.min(V.mouse.x + 14, $('stage').clientWidth - tip.offsetWidth - 8) + 'px';
  tip.style.top = (V.mouse.y + 16) + 'px';
}
function pin() {
  const cell = V.cell; const p = state.pinned;
  state.pinned = (cell && !(p && p.i === cell.i && p.j === cell.j)) ? cell : null;
  renderStory(); showCell(state.pinned || cell);
}
/** Outline the cell, a bar between the two grades, and lines to the evidence points its heights came from. */
function showCell(cell) {
  clearMarks();
  if (!cell || !V.hf) return;
  const info = cellInfo(cell.i, cell.j); if (!info) return;
  const { g, s } = V.hf; const vx = V.opts.vx;
  const cx = info.cx * s, cz = info.cy * s, half = g.cell_pt * s / 2;
  const ye = (info.eg - V.zmid) * vx, yp = (info.fg - V.zmid) * vx, top = Math.max(ye, yp) + 0.3;
  const P = (x, y, z) => new THREE.Vector3(x, y, z);
  const line = (pts, color) => V.marks.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color })));
  line([P(cx - half, top, cz - half), P(cx + half, top, cz - half), P(cx + half, top, cz + half), P(cx - half, top, cz + half), P(cx - half, top, cz - half)], 0xffb300);
  line([P(cx, ye, cz), P(cx, yp, cz)], info.depth < 0 ? CUT : FILL);
  if (!V.opts.sources || !state.result._ev) return;
  const rad = Math.max(0.5, V.orbit.dist * 0.004);
  for (const [tri, y0, color] of [[info.egTri, ye, EX], [info.fgTri, yp, PR]]) {
    for (const id of tri || []) {
      if (id == null || id < 0) continue;
      const ev = state.result._ev.get(id); if (!ev) continue;
      const p = P(ev.x * s, (ev.z - V.zmid) * vx, ev.y * s);
      line([P(cx, y0, cz), p], color);
      const dot = new THREE.Mesh(new THREE.SphereGeometry(rad, 10, 8), new THREE.MeshBasicMaterial({ color }));
      dot.position.copy(p); V.marks.add(dot);
    }
  }
}

// ---------------------------------------------------------------- options
function bindOptions() {
  $('v3dVx').addEventListener('input', (e) => { V.opts.vx = parseFloat(e.target.value) || 1; $('v3dVxVal').textContent = String(V.opts.vx); applyOpts(); });
  for (const [id, key] of [['v3dGround', 'ground'], ['v3dContours', 'contours'], ['v3dVolumes', 'volumes'], ['v3dProposed', 'proposed'], ['v3dSources', 'sources']]) {
    const e = $(id); if (!e) continue; e.checked = V.opts[key];
    e.addEventListener('change', (ev) => { V.opts[key] = ev.target.checked; applyOpts(); });
  }
  $('v3dTexture').addEventListener('change', (e) => { V.opts.texture = e.target.value; applyOpts(); });
  $('v3dReset').addEventListener('click', resetView);
}
function applyOpts() {
  V.group.scale.set(1, V.opts.vx, 1);
  const o = V.objs;
  if (o.ground) { o.ground.visible = V.opts.ground; o.ground.material = V.opts.texture === 'sheet' && V.matSheet.map ? V.matSheet : V.matTint; }
  if (o.skirt) o.skirt.visible = V.opts.ground;
  if (o.base) o.base.visible = V.opts.ground;
  if (o.contoursE) o.contoursE.visible = V.opts.contours;
  if (o.contoursP) o.contoursP.visible = V.opts.contours;
  if (o.cut) o.cut.visible = V.opts.volumes;
  if (o.fill) o.fill.visible = V.opts.volumes;
  if (o.proposed) o.proposed.visible = V.opts.proposed;
  showCell(state.pinned || V.cell);
}

// ---------------------------------------------------------------- lifecycle
function loop() {
  if (!V.active) return;
  V.renderer.render(V.scene, V.camera);
  V.raf = requestAnimationFrame(loop);
}
function show() {
  $('v3d').hidden = false;
  if (!init()) return;
  resize();
  V.active = true;
  const r = state.result;
  if (!r || r.error || !r.grid) { hint('Compute first, then open the 3D view.'); clearScene(); V.built = null; loop(); return; }
  if (V.built !== r) {
    hint('Building the surfaces…');
    setTimeout(() => { if (!V.active) return; const ok = build(r); hint(ok ? '' : 'Nothing to show: the grid has no heights.'); }, 20);
  } else hint('');
  cancelAnimationFrame(V.raf); loop();
}
function hide() {
  V.active = false; cancelAnimationFrame(V.raf); clearTimeout(V.texTimer);
  $('v3d').hidden = true; $('v3dTip').hidden = true;
  if (V.cell) { V.cell = null; state.hover = null; if (!state.pinned) renderStory(); }
}
function update() {
  if (!V.active) return;
  const r = state.result;
  if (!r || r.error || !r.grid) { hint('Compute first, then open the 3D view.'); clearScene(); V.built = null; return; }
  hint('Building the surfaces…');
  setTimeout(() => { if (!V.active) return; const ok = build(r); hint(ok ? '' : 'Nothing to show: the grid has no heights.'); }, 20);
}

window.View3D = { show, hide, update, resize, _V: V, _pick: pickCell, _hover: hover };
