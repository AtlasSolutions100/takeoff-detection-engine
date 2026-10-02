// THE 3D VIEW of a flooring takeoff (CET, 2026-09-09).
//
// The rooms the operator picked, stood up: each room's outline becomes a
// floor slab in the material of its floor type (carpet, tile, wood, LVP -
// procedural textures drawn on a canvas, so nothing is fetched and the
// field office renders it offline), tinted with the type's colour, with
// its deductions cut out as holes; its walls rise to the ceiling height
// read in the room (or the panel default, or 9 ft) as translucent slabs
// so the inside stays visible; a label floats over each room. Orbit,
// dolly and pan with the mouse.
//
// PRESENTATION ONLY. Every number in the panel is computed by app.js; this
// file reads room polygons and areas and draws them. It changes nothing.
//
// three.js r147 and its OrbitControls are vendored in webui/vendor/ (MIT,
// THREE-LICENSE beside them) and loaded on first open, not at page load:
// 600 KB nobody pays for until they press the button.
"use strict";
(function () {
  const VENDOR = ["vendor/three.min.js", "vendor/OrbitControls.js"];
  let loading = null;
  function loadVendor() {
    if (window.THREE && THREE.OrbitControls) return Promise.resolve();
    if (loading) return loading;
    loading = VENDOR.reduce((p, src) => p.then(() => new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => rej(new Error("could not load " + src));
      document.head.appendChild(s);
    })), Promise.resolve());
    return loading;
  }

  // ---- procedural floor textures: one 256 px tile each, in FEET ----------
  // Each returns {canvas, sizeFt}: the canvas covers sizeFt x sizeFt of floor.
  function hexRgb(h) { const n = parseInt(String(h).slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function tint(ctx, w, h, hex, alpha) {
    const [r, g, b] = hexRgb(hex);
    ctx.fillStyle = `rgba(${r},${g},${b},${alpha})`; ctx.fillRect(0, 0, w, h);
  }
  function noise(ctx, w, h, amount, seed) {
    const img = ctx.getImageData(0, 0, w, h); const d = img.data;
    let s = seed || 1;
    for (let i = 0; i < d.length; i += 4) {
      s = (s * 16807) % 2147483647;
      const n = ((s / 2147483647) - 0.5) * amount;
      d[i] += n; d[i + 1] += n; d[i + 2] += n;
    }
    ctx.putImageData(img, 0, 0);
  }
  const TEX = {
    carpet(hex) {   // 2 x 2 ft of fibre
      const c = document.createElement("canvas"); c.width = c.height = 256; const x = c.getContext("2d");
      x.fillStyle = "#b9b4a8"; x.fillRect(0, 0, 256, 256);
      tint(x, 256, 256, hex, 0.8); noise(x, 256, 256, 46, 7);
      return { canvas: c, sizeFt: 2 };
    },
    tile(hex) {     // 2 x 2 ft: four 1 ft tiles with grout
      const c = document.createElement("canvas"); c.width = c.height = 256; const x = c.getContext("2d");
      x.fillStyle = "#8f8a82"; x.fillRect(0, 0, 256, 256);              // grout
      x.fillStyle = "#e6e2da";
      for (let i = 0; i < 2; i++) for (let j = 0; j < 2; j++) x.fillRect(i * 128 + 3, j * 128 + 3, 122, 122);
      tint(x, 256, 256, hex, 0.55); noise(x, 256, 256, 14, 3);
      return { canvas: c, sizeFt: 2 };
    },
    wood(hex) {     // 4 x 4 ft of 5 in planks, staggered, with grain
      const c = document.createElement("canvas"); c.width = c.height = 256; const x = c.getContext("2d");
      x.fillStyle = "#b0855a"; x.fillRect(0, 0, 256, 256);
      const pw = 256 / 4 * (5 / 12);                  // 5 in planks, 4 ft canvas
      let row = 0;
      for (let y = 0; y < 256; y += pw, row++) {
        const off = (row % 3) * 85;
        for (let sx = -256 + off; sx < 256; sx += 128) {
          x.fillStyle = `hsl(28, 45%, ${38 + ((row * 7 + sx) % 5) * 3}%)`;
          x.fillRect(sx, y, 126, pw - 1.5);
          x.strokeStyle = "rgba(60,30,10,.25)"; x.lineWidth = 1;
          for (let g = 3; g < pw - 3; g += 4) { x.beginPath(); x.moveTo(sx, y + g); x.lineTo(sx + 126, y + g + 1.5); x.stroke(); }
        }
      }
      tint(x, 256, 256, hex, 0.45); noise(x, 256, 256, 12, 5);
      return { canvas: c, sizeFt: 4 };
    },
    lvp(hex) {      // 4 x 4 ft of 7 in planks, long stagger
      const c = document.createElement("canvas"); c.width = c.height = 256; const x = c.getContext("2d");
      x.fillStyle = "#9c8a76"; x.fillRect(0, 0, 256, 256);
      const pw = 256 / 4 * (7 / 12);
      let row = 0;
      for (let y = 0; y < 256; y += pw, row++) {
        const off = (row % 2) * 128;
        for (let sx = -256 + off; sx < 256; sx += 256) {
          x.fillStyle = `hsl(30, 22%, ${52 + (row % 3) * 4}%)`;
          x.fillRect(sx, y, 254, pw - 1.5);
        }
      }
      tint(x, 256, 256, hex, 0.5); noise(x, 256, 256, 10, 11);
      return { canvas: c, sizeFt: 4 };
    },
    plain(hex) {
      const c = document.createElement("canvas"); c.width = c.height = 128; const x = c.getContext("2d");
      x.fillStyle = "#cfcbc2"; x.fillRect(0, 0, 128, 128);
      tint(x, 128, 128, hex, 0.85); noise(x, 128, 128, 8, 2);
      return { canvas: c, sizeFt: 2 };
    },
  };
  function texKind(name) {
    const n = String(name || "").toLowerCase();
    if (/carpet|rug/.test(n)) return "carpet";
    if (/tile|ceramic|porcelain|stone|slate|marble/.test(n)) return "tile";
    if (/hardwood|wood|oak|maple|hickory|walnut|bamboo/.test(n)) return "wood";
    if (/lvp|lvt|vinyl|laminate|plank/.test(n)) return "lvp";
    return "plain";
  }

  // ---- the view ---------------------------------------------------------
  let view = null;
  function shell() {
    let el = document.getElementById("fl3d");
    if (el) return el;
    el = document.createElement("div");
    el.id = "fl3d";
    el.innerHTML = `
      <div class="fl3d-bar">
        <span class="fl3d-title">3D — the rooms as picked</span>
        <span class="fl3d-note">drag to orbit · wheel to zoom · right-drag to pan</span>
        <label><input type="checkbox" id="fl3dTex" checked> textures</label>
        <label><input type="checkbox" id="fl3dWalls" checked> walls</label>
        <label>wall height <input type="number" id="fl3dH" step="0.5" min="1" max="30"> ft for rooms with none</label>
        <span id="fl3dNote" class="fl3d-note"></span>
        <button id="fl3dClose" class="mini">close</button>
      </div>
      <div id="fl3dStage"></div>`;
    document.body.appendChild(el);
    return el;
  }

  function build(fl, opts) {
    const stage = document.getElementById("fl3dStage");
    stage.innerHTML = "";
    const W = stage.clientWidth, H = stage.clientHeight;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(W, H);
    stage.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(opts.dark ? 0x0d1013 : 0xcfcbc2);
    const camera = new THREE.PerspectiveCamera(50, W / H, 0.1, 5000);
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 0.5);
    sun.position.set(30, 60, 20);
    scene.add(sun);

    const fpp = opts.fpp;                              // ft per pt
    const rooms = (fl.rooms || []).filter(r => r.pts && r.pts.length >= 3);
    if (!rooms.length || !fpp) return { renderer, scene, camera, controls, note: fpp ? "no rooms picked" : "no scale set" };
    // world: x = pt x * fpp, z = pt y * fpp, y up. Centre on the rooms.
    let bx0 = Infinity, bz0 = Infinity, bx1 = -Infinity, bz1 = -Infinity;
    for (const r of rooms) for (const p of r.pts) { bx0 = Math.min(bx0, p[0] * fpp); bx1 = Math.max(bx1, p[0] * fpp); bz0 = Math.min(bz0, p[1] * fpp); bz1 = Math.max(bz1, p[1] * fpp); }
    const cx = (bx0 + bx1) / 2, cz = (bz0 + bz1) / 2;
    const toWorld = (p) => [p[0] * fpp - cx, p[1] * fpp - cz];

    // the table under the layout: a drafting grid in feet
    const span = Math.max(bx1 - bx0, bz1 - bz0) * 1.6 + 20;
    const grid = new THREE.GridHelper(span, Math.round(span), opts.dark ? 0x4a5257 : 0x8f8a82, opts.dark ? 0x2b3033 : 0xb5ae9f);
    grid.position.y = -0.05;
    scene.add(grid);

    const texCache = {};
    const matFor = (type) => {
      const hex = (type && type.color) || "#9aa3b2";
      const kind = opts.textures ? texKind(type && type.name) : "plain";
      const key = kind + hex + (opts.textures ? "t" : "f");
      if (!texCache[key]) {
        const t = TEX[kind](hex);
        const tex = new THREE.CanvasTexture(t.canvas);
        tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
        tex.repeat.set(1 / t.sizeFt, 1 / t.sizeFt);
        tex.anisotropy = 4;
        texCache[key] = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.9, metalness: 0.0,
          color: opts.textures ? 0xffffff : new THREE.Color(hex) });
      }
      return texCache[key];
    };
    const wallMat = new THREE.MeshStandardMaterial({ color: opts.dark ? 0xd8d3c7 : 0xffffff, transparent: true, opacity: 0.32, roughness: 1, side: THREE.DoubleSide, depthWrite: false });
    const labelSprite = (text, color) => {
      const c = document.createElement("canvas"); c.width = 512; c.height = 128; const x = c.getContext("2d");
      x.fillStyle = "rgba(20,22,24,.75)"; x.fillRect(0, 0, 512, 128);
      x.fillStyle = color; x.fillRect(0, 0, 14, 128);
      x.font = "600 44px IBM Plex Sans, Segoe UI, sans-serif"; x.fillStyle = "#ecece8"; x.textBaseline = "middle";
      x.fillText(text.slice(0, 22), 32, 64);
      const tex = new THREE.CanvasTexture(c);
      // screen-sized: a near room and a far one get the same label
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, sizeAttenuation: false }));
      sp.scale.set(0.2, 0.05, 1);
      return sp;
    };

    let nWalls = 0;
    for (const r of rooms) {
      const type = Number.isInteger(r.type) ? fl.types[r.type] : null;
      const shape = new THREE.Shape(r.pts.map(p => { const w = toWorld(p); return new THREE.Vector2(w[0], -w[1]); }));
      const holes = [];
      for (const o of (r.objects || [])) if (o.deduct && o.pts && o.pts.length >= 3) holes.push(o.pts);
      for (const d of (r.deductions || [])) if (d.pts && d.pts.length >= 3) holes.push(d.pts);
      for (const hp of holes) shape.holes.push(new THREE.Path(hp.map(p => { const w = toWorld(p); return new THREE.Vector2(w[0], -w[1]); })));
      const geo = new THREE.ShapeGeometry(shape, 4);
      const floor = new THREE.Mesh(geo, matFor(type));
      floor.rotation.x = -Math.PI / 2;               // shape's y -> world -z
      scene.add(floor);
      // walls: one thin slab per outline edge, to the room's ceiling
      const hFt = (r.ceiling_ft > 0 ? r.ceiling_ft : (fl.default_ceiling_ft > 0 ? fl.default_ceiling_ft : opts.wallH)) || 9;
      if (opts.walls) {
        for (let i = 0; i < r.pts.length; i++) {
          const a = toWorld(r.pts[i]), b = toWorld(r.pts[(i + 1) % r.pts.length]);
          const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
          if (len < 0.2) continue;
          const wall = new THREE.Mesh(new THREE.BoxGeometry(len, hFt, 0.25), wallMat);
          wall.position.set((a[0] + b[0]) / 2, hFt / 2, (a[1] + b[1]) / 2);
          wall.rotation.y = -Math.atan2(b[1] - a[1], b[0] - a[0]);
          scene.add(wall); nWalls++;
        }
      }
      // the name, over the middle of the room
      let mx = 0, mz = 0;
      for (const p of r.pts) { const w = toWorld(p); mx += w[0]; mz += w[1]; }
      const sp = labelSprite(r.name || "room", (type && type.color) || "#9aa3b2");
      sp.position.set(mx / r.pts.length, hFt + 1.2, mz / r.pts.length);
      scene.add(sp);
    }
    // fit: the layout's bounding sphere fills the view from a 3/4 angle
    const ext = Math.hypot(bx1 - bx0, bz1 - bz0) / 2 + 6;
    const dist = ext / Math.sin(camera.fov / 2 * Math.PI / 180) * 0.85;
    camera.position.set(dist * 0.42, dist * 0.78, dist * 0.5);
    controls.target.set(0, 3, 0);
    controls.update();
    const note = `${rooms.length} room${rooms.length === 1 ? "" : "s"} · ${nWalls} wall slabs · floor textures are drawn by name of the floor type (carpet, tile, wood, LVP); the colour is the panel's`;
    return { renderer, scene, camera, controls, note };
  }

  function open(fl, opts) {
    const el = shell();
    el.hidden = false;
    document.getElementById("fl3dH").value = opts.wallH || 9;
    document.getElementById("fl3dTex").checked = opts.textures !== false;
    document.getElementById("fl3dWalls").checked = opts.walls !== false;
    loadVendor().then(() => {
      if (view) { view.dispose(); view = null; }
      const o = { fpp: opts.fpp, dark: opts.dark, wallH: +document.getElementById("fl3dH").value || 9,
                  textures: document.getElementById("fl3dTex").checked, walls: document.getElementById("fl3dWalls").checked };
      const v = build(fl, o);
      document.getElementById("fl3dNote").textContent = v.note || "";
      let alive = true;
      const tick = () => { if (!alive) return; v.controls && v.controls.update(); v.renderer.render(v.scene, v.camera); requestAnimationFrame(tick); };
      tick();
      const onResize = () => {
        const st = document.getElementById("fl3dStage");
        v.renderer.setSize(st.clientWidth, st.clientHeight);
        v.camera.aspect = st.clientWidth / st.clientHeight; v.camera.updateProjectionMatrix();
      };
      window.addEventListener("resize", onResize);
      view = { dispose() { alive = false; window.removeEventListener("resize", onResize); v.renderer.dispose(); } };
      const rebuild = () => open(fl, Object.assign({}, opts, {
        wallH: +document.getElementById("fl3dH").value || 9,
        textures: document.getElementById("fl3dTex").checked, walls: document.getElementById("fl3dWalls").checked }));
      document.getElementById("fl3dTex").onchange = rebuild;
      document.getElementById("fl3dWalls").onchange = rebuild;
      document.getElementById("fl3dH").onchange = rebuild;
      document.getElementById("fl3dClose").onclick = close;
    }).catch((e) => {
      document.getElementById("fl3dNote").textContent = "the 3D library did not load: " + e.message;
      document.getElementById("fl3dClose").onclick = close;
    });
  }
  function close() {
    const el = document.getElementById("fl3d");
    if (el) el.hidden = true;
    if (view) { view.dispose(); view = null; }
  }
  window.fl3dOpen = open;
  window.fl3dClose = close;
  window.fl3dTexKind = texKind;
})();
