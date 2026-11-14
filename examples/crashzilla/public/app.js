import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { simulate, CURVES } from "/physics.js";

const $ = (id) => document.getElementById(id);
const ORGAN_NAMES = {
  brain: "Brain", skull_face: "Skull / face", cervical_spine: "Cervical spine", ribs: "Ribs / sternum", lungs: "Lungs",
  heart_aorta: "Heart / aorta", liver: "Liver", spleen: "Spleen", kidneys: "Kidneys", bowel: "Bowel", pelvis: "Pelvis",
  femur: "Femur / hip", lower_leg: "Tibia / ankle", arms: "Arms / clavicle",
};

// ---------------------------------------------------------------- scene
const canvas = $("three");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(1);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x040507);

const camera = new THREE.PerspectiveCamera(34, 1, 0.05, 50);
camera.position.set(0.95, 0.95, -3.05);
const controls = new OrbitControls(camera, canvas);
controls.target.set(0.25, 0.28, 0);
controls.enableDamping = true;
controls.autoRotate = false;
const labels = new CSS2DRenderer();
labels.domElement.style.cssText = "position:absolute;inset:0;pointer-events:none";
canvas.parentElement.appendChild(labels.domElement);

scene.add(new THREE.AmbientLight(0x6d7a8c, 0.6));
const key = new THREE.DirectionalLight(0xbfd8ff, 1.4); key.position.set(2, 3, 2); scene.add(key);
const rim = new THREE.PointLight(0xff2d3d, 2, 3); rim.position.set(-1, 1.2, -1); scene.add(rim);
const grid = new THREE.GridHelper(8, 64, 0x1a2230, 0x0e131b); grid.position.y = -0.37; scene.add(grid);

const composer = new EffectComposer(renderer);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.7, 0.45, 0.22);
composer.addPass(bloom);
composer.addPass(new OutputPass());

function resize() {
  const r = canvas.parentElement.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  composer.setSize(r.width, r.height); bloom.resolution.set(r.width / 2, r.height / 2);
  labels.setSize(r.width, r.height);
  camera.aspect = r.width / r.height; camera.updateProjectionMatrix();
}
addEventListener("resize", resize);

// ---------------------------------------------------------------- materials
const boneMat = new THREE.MeshStandardMaterial({ color: 0xdfe8f0, emissive: 0x8fa3b8, emissiveIntensity: 0.35, roughness: 0.5, transparent: true, opacity: 0.8 });
const organMat = (hex) => new THREE.MeshStandardMaterial({ color: hex, emissive: hex, emissiveIntensity: 0.25, roughness: 0.35, transparent: true, opacity: 0.85 });

// ---------------------------------------------------------------- helpers
const organs = {};       // id -> [meshes]
const anchors = {};      // id -> Object3D for labels
function tag(id, mesh) { (organs[id] ??= []).push(mesh); if (!anchors[id]) anchors[id] = mesh; return mesh; }
function boneBetween(a, b, r, mat = boneMat) {
  const d = new THREE.Vector3().subVectors(b, a);
  const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 0.9, d.length(), 10), mat);
  m.position.copy(a).addScaledVector(d, 0.5);
  m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
  return m;
}
function ellipsoid(rx, ry, rz, mat) { const m = new THREE.Mesh(new THREE.SphereGeometry(1, 28, 20), mat); m.scale.set(rx, ry, rz); return m; }
const V = (x, y, z) => new THREE.Vector3(x, y, z);


// ---------------------------------------------------------------- anatomical organ shapes
// simple value-noise used to sculpt the brain's folds
function hash3(x, y, z) { const h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453; return h - Math.floor(h); }
function noise3(x, y, z) {
  const xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z), xf = x - xi, yf = y - yi, zf = z - zi;
  const f = (t) => t * t * (3 - 2 * t), u = f(xf), v = f(yf), w = f(zf);
  const L = (a, b, t) => a + (b - a) * t;
  const c = (dx, dy, dz) => hash3(xi + dx, yi + dy, zi + dz);
  return L(L(L(c(0,0,0), c(1,0,0), u), L(c(0,1,0), c(1,1,0), u), v), L(L(c(0,0,1), c(1,0,1), u), L(c(0,1,1), c(1,1,1), u), v), w);
}
function brainGeometry() {
  const g = new THREE.SphereGeometry(1, 96, 64), p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const fold = Math.abs(noise3(v.x * 5, v.y * 5, v.z * 5) - 0.5) * 2;          // ridged noise = gyri and sulci
    const fissure = Math.exp(-Math.pow(v.z / 0.07, 2)) * 0.18 * (v.y > -0.3 ? 1 : 0); // split between hemispheres
    const r = 1 - 0.09 * (1 - fold) - fissure;
    v.multiplyScalar(r); v.y *= v.y < -0.35 ? 0.7 : 1;                            // flatter base
    p.setXYZ(i, v.x * 1.15, v.y * 0.85, v.z);
  }
  g.computeVertexNormals(); return g;
}
function lungGeometry(side) {
  const pts = []; for (let i = 0; i <= 20; i++) { const t = i / 20; pts.push(new THREE.Vector2(Math.sin(t * Math.PI) ** 0.7 * (0.6 + 0.4 * t), (t - 0.5) * 2)); }
  const g = new THREE.LatheGeometry(pts, 40), p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i); if (v.z * side < 0) v.z *= 0.45; v.x *= 1.1; p.setXYZ(i, v.x, v.y, v.z); } // flat inner face
  g.computeVertexNormals(); return g;
}
function heartGeometry() {
  const g = new THREE.SphereGeometry(1, 48, 32), p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i); const k = v.y < 0 ? 1 - Math.abs(v.y) * 0.55 : 1; v.x *= k; v.z *= k * 0.85; v.y *= 1.1; p.setXYZ(i, v.x, v.y, v.z); }
  g.computeVertexNormals(); g.rotateZ(-0.5); return g;
}
function liverGeometry() {
  const g = new THREE.SphereGeometry(1, 48, 32), p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i); const taper = 0.45 + 0.55 * (v.z + 1) / 2; v.y *= taper * (v.y < 0 ? 0.55 : 1); v.x *= 0.9; p.setXYZ(i, v.x, v.y, v.z); }
  g.computeVertexNormals(); return g;
}
function kidneyGeometry() { const g = new THREE.TorusGeometry(0.6, 0.42, 16, 32, Math.PI * 1.35); g.rotateZ(Math.PI * 0.82); g.scale(0.7, 1, 0.55); return g; }
function intestineGeometry() {
  const pts = []; let x = 0, y = 0.5, dir = 1;
  for (let row = 0; row < 7; row++) { for (let i = 0; i <= 8; i++) pts.push(new THREE.Vector3(x + dir * (i / 8 - 0.5) * 1.6, y + Math.sin(i * 1.3) * 0.06, Math.cos(i * 0.9 + row) * 0.25)); dir *= -1; y -= 0.16; }
  return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 260, 0.075, 8);
}
function organ(geo, sx, sy, sz, mat) { const m = new THREE.Mesh(geo, mat); m.scale.set(sx, sy, sz); return m; }

// ---------------------------------------------------------------- the car interior (Tesla-style cabin)
const car = new THREE.Group(); scene.add(car);
const leather = new THREE.MeshStandardMaterial({ color: 0x15181d, roughness: 0.55, metalness: 0.1 });
const trim = new THREE.MeshStandardMaterial({ color: 0x0c0e12, roughness: 0.8 });
const glassEdge = new THREE.LineBasicMaterial({ color: 0x2e3a4a, transparent: true, opacity: 0.8 });
const rbox = (w, h, d, r, mat) => new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 4, r), mat);
const withEdges = (m, op = 0.35) => { m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry, 30), new THREE.LineBasicMaterial({ color: 0x3a4658, transparent: true, opacity: op }))); return m; };
// floor
const floor = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 1.4), new THREE.MeshStandardMaterial({ color: 0x07090c, roughness: 0.95 }));
floor.rotation.x = -Math.PI / 2; floor.position.set(0.35, -0.36, 0); car.add(floor);
// driver seat: cushion, back, headrest, side bolsters
const seat = new THREE.Group(); car.add(seat);
const cushion = withEdges(rbox(0.52, 0.12, 0.5, 0.04, leather)); cushion.position.set(-0.02, -0.03, 0); cushion.rotation.z = 0.08; seat.add(cushion);
const back = withEdges(rbox(0.11, 0.66, 0.5, 0.04, leather)); back.position.set(-0.3, 0.33, 0); back.rotation.z = 0.24; seat.add(back);
const headrest = withEdges(rbox(0.1, 0.2, 0.28, 0.04, leather)); headrest.position.set(-0.43, 0.78, 0); headrest.rotation.z = 0.24; seat.add(headrest);
for (const s of [-1, 1]) { const b = withEdges(rbox(0.12, 0.42, 0.06, 0.025, leather)); b.position.set(-0.24, 0.3, 0.24 * s); b.rotation.z = 0.24; seat.add(b); }
const seatBase = rbox(0.4, 0.22, 0.36, 0.02, trim); seatBase.position.set(0.0, -0.2, 0); seat.add(seatBase);
// dashboard (wide, low, Model 3 style) + center screen
const dash = withEdges(rbox(0.42, 0.2, 1.7, 0.06, trim), 0.25); dash.position.set(0.95, 0.36, 0.35); car.add(dash);
const dashTop = withEdges(rbox(0.3, 0.04, 1.7, 0.02, leather), 0.25); dashTop.position.set(0.9, 0.48, 0.35); car.add(dashTop);
const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.38, 0.24), new THREE.MeshBasicMaterial({ color: 0x0d2a3a }));
screen.position.set(0.74, 0.62, 0.42); screen.rotation.y = -Math.PI / 2 - 0.47; screen.rotation.x = 0; car.add(screen);
const screenGlow = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(0.38, 0.24)), new THREE.LineBasicMaterial({ color: 0x5fd8ff }));
screenGlow.position.copy(screen.position); screenGlow.rotation.copy(screen.rotation); car.add(screenGlow);
// steering column + wheel (yoke-ish round wheel), centered in front of the driver's chest
const WHEEL = V(0.2, 0.47, 0);
const colMat = new THREE.MeshStandardMaterial({ color: 0x1b2028, roughness: 0.5, metalness: 0.4 });
const column = boneBetween(WHEEL.clone().add(V(0.02, -0.01, 0)), V(0.8, 0.28, 0), 0.028, colMat); car.add(column);
const wheel = new THREE.Group(); wheel.position.copy(WHEEL); wheel.rotation.z = -0.42; car.add(wheel);
const rimMat = new THREE.MeshStandardMaterial({ color: 0x1c2027, roughness: 0.42, metalness: 0.15 });
const trimMat = new THREE.MeshStandardMaterial({ color: 0x8e99a8, roughness: 0.25, metalness: 0.9, emissive: 0x1b2633, emissiveIntensity: 0.6 });
const glowMat = new THREE.MeshBasicMaterial({ color: 0x5fd8ff, transparent: true, opacity: 0.55 });
const wheelRim = new THREE.Mesh(new THREE.TorusGeometry(0.175, 0.024, 20, 96), rimMat); wheelRim.rotation.y = Math.PI / 2; wheel.add(wheelRim);
const rimGlow = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.0018, 6, 128), glowMat); rimGlow.rotation.y = Math.PI / 2; wheel.add(rimGlow);
const hub = new THREE.Mesh(new RoundedBoxGeometry(0.05, 0.1, 0.13, 4, 0.02), rimMat); wheel.add(hub);
const hubLogo = new THREE.Mesh(new THREE.BoxGeometry(0.004, 0.03, 0.03), trimMat); hubLogo.position.x = -0.026; wheel.add(hubLogo);
for (const sd of [-1, 1]) {
  const sp = new THREE.Mesh(new RoundedBoxGeometry(0.03, 0.035, 0.11, 3, 0.012), rimMat); sp.position.set(0, -0.005, sd * 0.11); wheel.add(sp);
  const btn = new THREE.Mesh(new THREE.CylinderGeometry(0.011, 0.011, 0.006, 20), trimMat); btn.rotation.z = Math.PI / 2; btn.position.set(-0.017, -0.005, sd * 0.1); wheel.add(btn);
}
const lowSpoke = new THREE.Mesh(new RoundedBoxGeometry(0.03, 0.09, 0.03, 3, 0.012), rimMat); lowSpoke.position.set(0, -0.11, 0); wheel.add(lowSpoke);
// pedals
for (const z of [0.05, 0.16]) { const p = rbox(0.03, 0.12, 0.07, 0.01, colMat); p.position.set(0.72, -0.26, z); p.rotation.z = -0.5; car.add(p); }
// door panel + window line on the driver's left (-Z)
const door = withEdges(rbox(1.1, 0.5, 0.05, 0.02, new THREE.MeshStandardMaterial({ color: 0x0c0e12, transparent: true, opacity: 0.12, depthWrite: false })), 0.35); door.position.set(0.35, 0.05, -0.42); car.add(door);
const armrest = rbox(0.4, 0.05, 0.08, 0.02, new THREE.MeshStandardMaterial({ color: 0x15181d, transparent: true, opacity: 0.2, depthWrite: false })); armrest.position.set(0.15, 0.22, -0.38); car.add(armrest);
// A-pillar, B-pillar, roof rail, windshield (glass edges)
const glassLine = (pts) => { const g = new THREE.BufferGeometry().setFromPoints(pts); return new THREE.Line(g, glassEdge); };
car.add(glassLine([V(0.95, 0.5, -0.42), V(0.45, 1.05, -0.4), V(-0.35, 1.08, -0.4), V(-0.55, 0.5, -0.42)]));
car.add(glassLine([V(0.95, 0.5, 0.95), V(0.45, 1.05, 0.9), V(-0.35, 1.08, 0.9)]));
car.add(glassLine([V(0.45, 1.05, -0.4), V(0.45, 1.05, 0.9)]));
car.add(glassLine([V(0.95, 0.5, -0.42), V(0.95, 0.5, 0.95)]));
const bPillar = boneBetween(V(-0.42, 0.25, -0.43), V(-0.38, 1.06, -0.41), 0.03, trim); car.add(bPillar);
// center console
const consoleBox = withEdges(rbox(0.7, 0.2, 0.22, 0.03, trim), 0.25); consoleBox.position.set(0.2, -0.12, 0.38); car.add(consoleBox);
// airbag (deploys from the wheel hub)
const bagGeo = (() => {
  const g = new THREE.SphereGeometry(1, 64, 48), p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const seam = Math.exp(-Math.pow(v.x / 0.08, 2)) * 0.05;                       // stitched seam around the edge
    const wr = (noise3(v.x * 4, v.y * 4, v.z * 4) - 0.5) * 0.09;                  // fabric wrinkles
    v.multiplyScalar(1 + wr - seam);
    v.x = v.x * 0.5 - 0.5;                                                         // flat cushion, grows toward the driver from its back face
    p.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals(); return g;
})();
const bagMat = new THREE.MeshStandardMaterial({ color: 0xf1f3f6, roughness: 0.95, metalness: 0, transparent: true, opacity: 0.78, emissive: 0xb8c2cf, emissiveIntensity: 0.45, side: THREE.DoubleSide });
const airbag = new THREE.Mesh(bagGeo, bagMat);
airbag.position.copy(WHEEL).add(V(-0.03, 0.0, 0)); airbag.rotation.z = -0.42; airbag.scale.setScalar(0.001); airbag.visible = false; car.add(airbag);

// ---------------------------------------------------------------- the body: rigged human, x-ray
const xray = (hex, base = 0.05, rimK = 0.75) => {
  const m = new THREE.MeshStandardMaterial({ color: hex, emissive: hex, emissiveIntensity: 0.06, transparent: true, opacity: 1, depthWrite: false, side: THREE.DoubleSide, roughness: 0.6 });
  m.userData.rimColor = new THREE.Color(hex);
  m.onBeforeCompile = (sh) => {
    sh.uniforms.rimColor = { value: m.userData.rimColor };
    m.userData.shader = sh;
    sh.fragmentShader = "uniform vec3 rimColor;\n" + sh.fragmentShader.replace("#include <dithering_fragment>",
      `#include <dithering_fragment>
       float fr = pow(1.0 - abs(dot(normalize(normal), normalize(vViewPosition))), 2.4);
       gl_FragColor = vec4(rimColor * (0.15 + fr * 1.25), ${base.toFixed(2)} + fr * ${rimK.toFixed(2)});`);
  };
  return m;
};
const skinX = xray(0x5fd8ff, 0.04, 0.9);
const jointX = xray(0x9fe9ff, 0.05, 0.5);
const gltf = await new GLTFLoader().loadAsync("/models/Xbot.glb");
const human = gltf.scene;
human.traverse(o => { if (o.isSkinnedMesh) { o.material = o.name.includes("Joints") ? jointX : skinX; o.frustumCulled = false; o.renderOrder = 2; } });
const body = new THREE.Group(); scene.add(body);
body.rotation.y = Math.PI / 2;                       // model faces +Z → occupant faces +X
body.position.set(-0.08, 0.06, 0);                   // hips sit on the cushion
human.position.y = -1.04; body.add(human);
const B = {}; human.traverse(o => { if (o.isBone) B[o.name.replace("mixamorig", "").replace(":", "")] = o; });
const REST = Object.fromEntries(Object.entries(B).map(([k, b]) => [k, b.quaternion.clone()]));

const _pq = new THREE.Quaternion(), _wq = new THREE.Quaternion(), _rq = new THREE.Quaternion();
function turn(name, axis, angle) {
  const b = B[name]; if (!b) return;
  b.parent.getWorldQuaternion(_pq); b.getWorldQuaternion(_wq);
  _rq.setFromAxisAngle(axis, angle);
  b.quaternion.copy(_pq.invert().multiply(_rq.multiply(_wq)));
  b.updateMatrixWorld(true);
}
const AX = { x: V(1, 0, 0), y: V(0, 1, 0), z: V(0, 0, 1) };
// rotate a bone (in world space) so its direction "from" becomes "to"
function aim(name, from, to) {
  const b = B[name];
  b.parent.getWorldQuaternion(_pq); b.getWorldQuaternion(_wq);
  _rq.setFromUnitVectors(from.clone().normalize(), to.clone().normalize());
  b.quaternion.copy(_pq.invert().multiply(_rq.multiply(_wq)));
  b.updateMatrixWorld(true);
}
const wpos = (n) => B[n].getWorldPosition(new THREE.Vector3());
// analytic two-bone IK: upper → lower → end reaches target, elbow/knee bends toward pole
function ik(upper, lower, end, target, pole) {
  const S = wpos(upper), E = wpos(lower), H = wpos(end);
  const a = E.distanceTo(S), bl = H.distanceTo(E);
  const toT = target.clone().sub(S); const d = Math.min(Math.max(toT.length(), Math.abs(a - bl) + 1e-3), a + bl - 1e-3);
  const u = toT.normalize();
  const cosA = (a * a + d * d - bl * bl) / (2 * a * d), sinA = Math.sqrt(Math.max(0, 1 - cosA * cosA));
  const n = pole.clone().sub(u.clone().multiplyScalar(pole.dot(u))).normalize();
  const Edes = S.clone().addScaledVector(u, a * cosA).addScaledVector(n, a * sinA);
  aim(upper, E.clone().sub(S), Edes.clone().sub(S));
  const H2 = wpos(end), E2 = wpos(lower);
  aim(lower, H2.clone().sub(E2), target.clone().sub(E2));
}
// where the hands and feet must be, in car coordinates
const GRIP = { L: V(0, 0.085, -0.147), R: V(0, 0.085, 0.147) };
const FEET = { L: V(0.47, -0.31, -0.13), R: V(0.52, -0.29, 0.13) };
const HIP = V(-0.13, 0.14, 0);
function pose({ lean = 0, head = 0, slide = 0 } = {}) {
  for (const [k, q] of Object.entries(REST)) B[k].quaternion.copy(q);
  body.position.set(HIP.x + slide, HIP.y, HIP.z);
  human.updateMatrixWorld(true);
  // torso: reclined against the seat back, then thrown forward by the crash
  turn("Spine", AX.z, 0.2 - lean * 0.45); turn("Spine1", AX.z, -lean * 0.3); turn("Spine2", AX.z, -lean * 0.25);
  turn("Neck", AX.z, -head * 0.45); turn("Head", AX.z, -head * 0.55 - 0.05);
  // legs: thighs on the cushion, feet on the floor / pedals
  ik("LeftUpLeg", "LeftLeg", "LeftFoot", FEET.L.clone().add(V(slide, 0, 0)), V(1, 0.7, -0.15));
  ik("RightUpLeg", "RightLeg", "RightFoot", FEET.R.clone().add(V(slide, 0, 0)), V(1, 0.7, 0.15));
  turn("LeftFoot", AX.z, 0.25); turn("RightFoot", AX.z, 0.25);
  // arms: hands locked on the rim at 10 and 2, elbows down and out
  wheel.updateMatrixWorld(true);
  // the palm (not the wrist) sits on the rim: aim the wrist a hand-width short of the grip point
  for (const [side, g, pole] of [["Left", GRIP.L, V(0, -1, -0.7)], ["Right", GRIP.R, V(0, -1, 0.7)]]) {
    const tgt = wheel.localToWorld(g.clone()), sh = wpos(side + "Arm");
    tgt.addScaledVector(tgt.clone().sub(sh).normalize(), -0.075);
    ik(side + "Arm", side + "ForeArm", side + "Hand", tgt, pole);
  }
  grip();
  human.updateMatrixWorld(true);
}
// fingers wrap around the rim
const GQ = new URLSearchParams(location.search);
const FAX = { x: V(1, 0, 0), y: V(0, 1, 0), z: V(0, 0, 1), nx: V(-1, 0, 0) }[GQ.get("fax") ?? "x"];
const FANG = +(GQ.get("fang") ?? 1.45), HROLL = +(GQ.get("hroll") ?? -0.8);
const _fq = new THREE.Quaternion();
function grip() {
  for (const side of ["Left", "Right"]) {
    const sgn = 1;
    if (HROLL) { const fw = wpos(side + "Hand").sub(wpos(side + "ForeArm")).normalize(); turn(side + "Hand", fw, HROLL * sgn); }
    for (const f of ["Index", "Middle", "Ring", "Pinky"]) for (let j = 1; j <= 3; j++) {
      const b = B[side + "Hand" + f + j]; if (!b) continue;
      b.quaternion.multiply(_fq.setFromAxisAngle(FAX, FANG * sgn));
    }
    for (let j = 2; j <= 3; j++) { const b = B[side + "HandThumb" + j]; if (b) b.quaternion.multiply(_fq.setFromAxisAngle(FAX, FANG * 0.6 * sgn)); }
  }
}

// x-ray skeleton: glowing segments that follow the rig every frame
const SEGS = [
  ["Hips", "Spine", null], ["Spine", "Spine1", null], ["Spine1", "Spine2", null], ["Spine2", "Neck", null],
  ["Neck", "Head", "cervical_spine"],
  ["LeftShoulder", "LeftArm", "arms"], ["LeftArm", "LeftForeArm", "arms"], ["LeftForeArm", "LeftHand", "arms"],
  ["RightShoulder", "RightArm", "arms"], ["RightArm", "RightForeArm", "arms"], ["RightForeArm", "RightHand", "arms"],
  ["Hips", "LeftUpLeg", "pelvis"], ["Hips", "RightUpLeg", "pelvis"],
  ["LeftUpLeg", "LeftLeg", "femur"], ["RightUpLeg", "RightLeg", "femur"],
  ["LeftLeg", "LeftFoot", "lower_leg"], ["RightLeg", "RightFoot", "lower_leg"], ["LeftFoot", "LeftToeBase", "lower_leg"], ["RightFoot", "RightToeBase", "lower_leg"],
];
const segMeshes = SEGS.map(([a, b, id]) => {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 0.85, 1, 10), boneMat.clone()); scene.add(m);
  if (id) tag(id, m);
  return { a, b, m, r: id === "femur" ? 0.02 : id === "lower_leg" ? 0.016 : id === "arms" ? 0.013 : 0.018 };
});
const _a = new THREE.Vector3(), _b = new THREE.Vector3(), _d = new THREE.Vector3(), UP = V(0, 1, 0);
function updateSkeleton() {
  for (const s of segMeshes) {
    B[s.a].getWorldPosition(_a); B[s.b].getWorldPosition(_b); _d.subVectors(_b, _a);
    s.m.position.copy(_a).addScaledVector(_d, 0.5); s.m.scale.set(s.r, _d.length(), s.r);
    s.m.quaternion.setFromUnitVectors(UP, _d.normalize());
  }
  for (const [bone, g] of anchorGroups) { B[bone].getWorldPosition(g.position); B[bone].getWorldQuaternion(g.quaternion); }
}

// organs ride on spine/head bones. Coordinates inside each anchor: x forward, y up, z right (meters)
const anchorGroups = [];
function anchor(bone) { const g = new THREE.Group(); scene.add(g); const inner = new THREE.Group(); inner.rotation.y = -Math.PI / 2; g.add(inner); anchorGroups.push([bone, g]); return inner; }
const chest = anchor("Spine2"), abdomen = anchor("Spine"), skullA = anchor("Head"), pelvisA = anchor("Hips");
for (let i = 0; i < 10; i++) {
  const r = 0.105 + Math.sin((i / 9) * Math.PI) * 0.03;
  const rib = new THREE.Mesh(new THREE.TorusGeometry(r, 0.0055, 6, 40, Math.PI * 1.75), boneMat.clone());
  rib.rotation.x = Math.PI / 2; rib.rotation.z = Math.PI * 0.125 + Math.PI; rib.scale.set(1, 1.3, 1);
  rib.position.set(-0.01, -0.17 + i * 0.024, 0); chest.add(tag("ribs", rib));
}
chest.add(tag("ribs", boneBetween(V(0.1, -0.12, 0), V(0.1, 0.05, 0), 0.01, boneMat.clone())));
for (const s of [-1, 1]) chest.add(tag("arms", boneBetween(V(0.07, 0.07, 0.02 * s), V(0.0, 0.09, 0.16 * s), 0.008, boneMat.clone())));
const lungsM = [-1, 1].map(s => { const l = organ(lungGeometry(s), 0.05, 0.1, 0.048, organMat(0x5fd8ff)); l.position.set(0.01, -0.04, 0.066 * s); return tag("lungs", l); });
chest.add(...lungsM);
const heart = organ(heartGeometry(), 0.04, 0.045, 0.036, organMat(0x5fd8ff)); heart.position.set(0.04, -0.08, -0.022); chest.add(tag("heart_aorta", heart));
const aortaCurve = new THREE.CatmullRomCurve3([V(0.04, -0.06, -0.02), V(0.0, 0.02, -0.01), V(-0.045, -0.02, -0.005), V(-0.045, -0.2, 0), V(-0.04, -0.3, 0)]);
chest.add(tag("heart_aorta", new THREE.Mesh(new THREE.TubeGeometry(aortaCurve, 40, 0.009, 8), organMat(0x5fd8ff))));
const liver = organ(liverGeometry(), 0.075, 0.05, 0.09, organMat(0x5fd8ff)); liver.position.set(0.03, 0.09, 0.05); liver.rotation.x = -0.3; abdomen.add(tag("liver", liver));
const spleen = ellipsoid(0.026, 0.045, 0.02, organMat(0x5fd8ff)); spleen.position.set(-0.02, 0.1, -0.085); abdomen.add(tag("spleen", spleen));
for (const s of [-1, 1]) { const k = organ(kidneyGeometry(), 0.03 * (s > 0 ? -1 : 1), 0.04, 0.03, organMat(0x5fd8ff)); k.position.set(-0.05, 0.03, 0.055 * s); abdomen.add(tag("kidneys", k)); }
const bowel = organ(intestineGeometry(), 0.055, 0.1, 0.07, organMat(0x5fd8ff)); bowel.rotation.y = Math.PI / 2; bowel.position.set(0.03, 0.0, 0); abdomen.add(tag("bowel", bowel));
const pelvisRing = new THREE.Mesh(new THREE.TorusGeometry(0.09, 0.02, 10, 36), boneMat.clone()); pelvisRing.rotation.x = Math.PI / 2; pelvisRing.scale.set(0.85, 1.2, 1); pelvisRing.position.set(0, -0.02, 0); pelvisA.add(tag("pelvis", pelvisRing));
const skull = new THREE.Mesh(new THREE.SphereGeometry(0.09, 28, 20), new THREE.MeshStandardMaterial({ color: 0xdfe8f0, emissive: 0x8fa3b8, emissiveIntensity: 0.4, transparent: true, opacity: 0.3, wireframe: true }));
skull.position.set(0.01, 0.09, 0); skull.scale.set(1.08, 1.05, 0.92); skullA.add(tag("skull_face", skull));
const brain = organ(brainGeometry(), 0.064, 0.064, 0.066, organMat(0x5fd8ff)); brain.rotation.y = Math.PI / 2; brain.position.set(-0.005, 0.11, 0); skullA.add(tag("brain", brain));
// seat belt: shoulder → hip diagonal + lap belt (follows the chest)
const beltMat = new THREE.MeshStandardMaterial({ color: 0x2a2f37, emissive: 0x1a1f26, roughness: 0.7 });
const sash = boneBetween(V(0.11, 0.12, -0.13), V(0.12, -0.28, 0.12), 0.012, beltMat); chest.add(sash);
const lap = boneBetween(V(0.1, 0.02, -0.15), V(0.1, 0.02, 0.15), 0.012, beltMat); pelvisA.add(lap);

pose(); updateSkeleton();

// labels
const labelObjs = {};
for (const id of Object.keys(ORGAN_NAMES)) {
  const el = document.createElement("div"); el.className = "label3d";
  const o = new CSS2DObject(el); o.visible = false; anchors[id]?.add(o); labelObjs[id] = { o, el };
}

// shockwave ring
const ring = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.215, 64), new THREE.MeshBasicMaterial({ color: 0xff2d3d, transparent: true, opacity: 0, side: THREE.DoubleSide }));
ring.rotation.y = Math.PI / 2; ring.position.set(0.3, 0.42, 0); scene.add(ring);

resize();

// ---------------------------------------------------------------- state & animation
let sim = simulate(readInputs());
let play = null;           // { start, dur }
let organState = {};       // id -> severity 0..1
const cur = { lean: 0, head: 0, slide: 0 };
let track = null;           // [{t, lean, bag}] per sampled frame of the driver camera     // forward-lean per frame, from Drex Max on the driver camera
const SLOW = 36;           // 200 ms of crash over 7.2 s

var camPhys = null; // from the driver camera: crash pulse length (car stiffness) + where the head went
function readInputs() {
  const base = { speedKmh: +$("i-speed").value, age: +$("i-age").value, belted: $("i-belt").checked, airbag: $("i-bag").checked };
  return camPhys ? { ...base, ...camPhys } : base;
}

function poseAt(i, vt = null) {
  const s = sim.series;
  const tx = s.torsoX[i] ?? 0, hx = s.headX[i] ?? 0;
  const maxLean = sim.input.belted ? 0.62 : 0.78;
  let lean = Math.min(maxLean, Math.atan2(Math.max(tx, 0) * (sim.input.belted ? 1.1 : 0.9), 0.42));
  // the driver camera drives the motion too: blend in how far Drex Max saw the body thrown
  let bagOverride = null;
  if (track && vt != null) {
    // exactly what the camera shows at this moment of the video
    let j = track.findIndex(f => f.t > vt); if (j <= 0) j = j === 0 ? 1 : track.length - 1;
    const A = track[j - 1], Bf = track[j], w = Math.min(1, Math.max(0, (vt - A.t) / (Bf.t - A.t)));
    const L = A.lean + (Bf.lean - A.lean) * w;
    lean = Math.min(maxLean * 1.15, (L / 4) * 1.0);
    bagOverride = A.bag + (Bf.bag - A.bag) * w;
  }
  const head = track && vt != null ? Math.min(0.5, lean * 0.55) : Math.max(-0.35, Math.min(0.55, Math.atan2(hx, 0.1)));
  const slide = sim.input.belted ? Math.min(0.02, tx * 0.05) : Math.min(0.07, Math.max(0, tx - 0.1) * 0.3);
  cur.lean += (lean - cur.lean) * 0.22; cur.head += (head - cur.head) * 0.3; cur.slide += (slide - cur.slide) * 0.35;
  pose({ lean: cur.lean, head: cur.head, slide: cur.slide });
  updateSkeleton();
  const bagT = s.t[i] ?? 0;
  const bag = bagOverride != null ? Math.min(1, Math.max(0, (bagOverride - 0.3) / 0.5)) : sim.input.airbag ? Math.min(1, Math.max(0, (bagT - 12) / 18)) * (bagT > 120 ? Math.max(0.35, 1 - (bagT - 120) / 120) : 1) : 0;
  const bs = Math.max(bag, 0.001); airbag.visible = bag > 0.03; airbag.scale.set(0.2 * bs, 0.2 * (0.55 + 0.45 * bs), 0.22 * (0.55 + 0.45 * bs));
  const load = Math.min(1, (s.chest[i] ?? 0) / 45);
  skinX.userData.rimColor.setRGB(0.37 + load * 0.63, 0.85 - load * 0.6, 1 - load * 0.8);
}

const clock = new THREE.Clock();
function frame() {
  const t = clock.getElapsedTime();
  if (play) {
    const vid = $("vid");
    let k = (performance.now() - play.start) / play.dur;
    if (play.video) k = vid.ended ? 1 : (vid.currentTime - play.t0) / (play.t1 - play.t0);
    k = Math.max(0, k);
    const i = Math.min(sim.series.t.length - 1, Math.floor(Math.min(k, 1) * sim.series.t.length));
    poseAt(i, play.video ? vid.currentTime : null);
    const ms = sim.series.t[i];
    $("clock").textContent = `T+${(ms / 1000).toFixed(3)} s`;
    const ph = $("phase");
    if (ms < 8) { ph.textContent = "CONTACT"; ph.className = ""; }
    else if (ms < sim.input.pulseMs) { ph.textContent = "IMPACT · " + Math.round(sim.series.car[i]) + " g"; ph.className = "hit"; }
    else { ph.textContent = "REBOUND"; ph.className = ""; }
    const peakI = sim.series.chest.indexOf(Math.max(...sim.series.chest));
    if (Math.abs(i - peakI) < 3) { ring.material.opacity = 0.9; ring.scale.setScalar(0.3); }
    drawChart(i); drawAnalysis(i); $("an-scrub").value = i;
    if (k >= 1) { play = null; $("phase").textContent = "ASSESSMENT"; $("phase").className = ""; }
  }
  if (ring.material.opacity > 0) { ring.scale.multiplyScalar(1.045); ring.material.opacity *= 0.95; }
  // organs pulse by severity
  for (const [id, meshes] of Object.entries(organs)) {
    const sev = organState[id] ?? 0;
    const c = sevColor(sev);
    for (const m of meshes) {
      m.material.color?.copy(c); m.material.emissive?.copy(c);
      m.material.emissiveIntensity = sev > 0.1 ? 0.35 + sev * 1.6 * (0.6 + 0.4 * Math.sin(t * 2.2)) : 0.3;
    }
  }
  controls.update();
  composer.render();
  labels.render(scene, camera);
  requestAnimationFrame(frame);
}
function sevColor(s) {
  if (s <= 0.1) return new THREE.Color(0x5fd8ff);
  const c = new THREE.Color();
  return s < 0.5 ? c.lerpColors(new THREE.Color(0xffd23f), new THREE.Color(0xff8a1f), s / 0.5) : c.lerpColors(new THREE.Color(0xff8a1f), new THREE.Color(0xff1030), (s - 0.5) / 0.5);
}
requestAnimationFrame(frame);

// ---------------------------------------------------------------- chart + metrics
function drawChart(cursor = -1) {
  const c = $("chart"), g = c.getContext("2d"), W = c.width, H = c.height, s = sim.series;
  g.clearRect(0, 0, W, H);
  const maxY = Math.max(60, ...s.head.map(v => Math.min(v, 150)), ...s.chest, ...s.car) * 1.1;
  const X = (ms) => 40 + (ms / 200) * (W - 52), Y = (v) => H - 22 - (Math.min(v, maxY) / maxY) * (H - 40);
  g.strokeStyle = "#161b22"; g.lineWidth = 1; g.font = "10px JetBrains Mono"; g.fillStyle = "#59616d";
  for (let ms = 0; ms <= 200; ms += 50) { g.beginPath(); g.moveTo(X(ms), 14); g.lineTo(X(ms), H - 22); g.stroke(); g.fillText(ms + " ms", X(ms) - 12, H - 8); }
  for (let v = 0; v <= maxY; v += 20) { g.fillText(v + "g", 6, Y(v) + 3); }
  const line = (arr, col, lw = 1.6) => { g.strokeStyle = col; g.lineWidth = lw; g.beginPath(); arr.forEach((v, i) => { const x = X(s.t[i]), y = Y(v); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); };
  line(s.car, "#5f6b7a", 1.2); line(s.chest, "#ffb020"); line(s.head, "#ff2d3d", 2);
  g.fillStyle = "#5f6b7a"; g.fillText("CAR", W - 150, 14); g.fillStyle = "#ffb020"; g.fillText("CHEST", W - 112, 14); g.fillStyle = "#ff2d3d"; g.fillText("HEAD", W - 62, 14);
  if (cursor >= 0) { g.strokeStyle = "#ffffff"; g.beginPath(); g.moveTo(X(s.t[cursor]), 14); g.lineTo(X(s.t[cursor]), H - 22); g.stroke(); }
}

function renderMetrics() {
  const m = sim.metrics, r = sim.risk;
  const tile = (k, v, unit, limit, risk) => {
    const frac = limit ? Math.min(1, v / limit) : 0;
    const cls = risk != null ? (risk > 0.25 ? "bad" : risk > 0.08 ? "warn" : "") : frac > 1 ? "bad" : "";
    return `<div class="m ${cls}"><div class="k">${k}</div><div class="v">${v.toFixed(v < 10 ? 2 : 0)}<small>${unit}</small></div>${limit ? `<div class="lim"><i style="width:${frac * 100}%"></i></div>` : ""}</div>`;
  };
  $("metrics").innerHTML = [
    tile("ΔV", m.deltaVKmh, "km/h"),
    tile("CAR PEAK", m.peakCarG, "g"),
    tile("HEAD · HIC₁₅", m.hic15, `/ 700 · ${(r.head * 100).toFixed(0)}%`, 700, r.head),
    tile("CHEST DEFLECTION", m.chestDeflMm, `mm / 63 · ${(r.chest * 100).toFixed(0)}%`, 63, r.chest),
    tile("NECK · Nij", m.nij, `/ 1.0 · ${(r.neck * 100).toFixed(0)}%`, 1, r.neck),
    tile("FEMUR LOAD", m.femurKN, `kN / 10 · ${(r.femur * 100).toFixed(0)}%`, 10, r.femur),
    tile("CHEST 3 ms", m.chestG3ms, "g / 60", 60),
    tile("ANY SERIOUS INJURY", r.joint * 100, "% (NHTSA curves)", null, r.joint),
  ].join("");
  $("formulas").innerHTML = `
    <div><b>HIC₁₅</b> = max (t₂−t₁)·[ (1/(t₂−t₁)) ∫ a(t) dt ]^2.5</div>
    <div><b>${CURVES.head.label}</b> ${CURVES.head.formula}</div>
    <div><b>${CURVES.chest.label}</b> ${CURVES.chest.formula}</div>
    <div><b>${CURVES.neck.label}</b> ${CURVES.neck.formula}</div>
    <div><b>Δv</b> = ∫ a(t) dt · half-sine pulse a(t) = A·sin(πt/T), T = ${sim.input.pulseMs} ms</div>`;
}

// ---------------------------------------------------------------- Drex results
const expected = (probs) => Object.entries(probs).reduce((s, [k, p]) => s + (+k) * p, 0);
const pSerious = (probs) => [3, 4, 5].reduce((s, k) => s + (probs[k] ?? probs[String(k)] ?? 0), 0);

function renderOrgans(drex, llm) {
  const rows = Object.keys(ORGAN_NAMES).map(id => {
    const a = drex?.answers?.[id];
    const p = a ? pSerious(a.probabilities) : 0;
    const e = a ? expected(a.probabilities) / 5 : 0;
    const l = llm?.answers?.[id];
    return { id, p, e, l: typeof l === "number" ? l / 5 : null };
  }).sort((a, b) => b.p - a.p || b.e - a.e);
  organState = Object.fromEntries(rows.map(r => [r.id, Math.max(r.p, r.e * 0.8)]));
  $("organs").innerHTML = rows.map(r => `
    <div class="organ ${r.p > 0.35 ? "hot" : ""}"><span class="n">${ORGAN_NAMES[r.id]}</span>
      <span class="track"><i style="width:${(r.p * 100).toFixed(1)}%;background:#${sevColor(Math.max(r.p, 0.05)).getHexString()}"></i>${r.l != null ? `<u style="left:${r.l * 100}%"></u>` : ""}</span>
      <span class="p">${(r.p * 100).toFixed(0)}%</span></div>`).join("") +
    `<div class="legend">bar = Drex P(serious, AIS 3+) · <u></u> = LLM's single guess (no odds)</div>`;
  // 3D labels for the top organs
  for (const { o } of Object.values(labelObjs)) o.visible = false;
  rows.slice(0, 4).filter(r => r.p > 0.08).forEach(r => {
    const L = labelObjs[r.id]; if (!L) return;
    L.o.visible = true; L.el.className = "label3d" + (r.p < 0.35 ? " amber" : "");
    L.el.textContent = `${ORGAN_NAMES[r.id].toUpperCase()} ${(r.p * 100).toFixed(0)}%`;
  });
  return rows;
}

function renderER(drex, rows) {
  const a = drex?.answers;
  if (!a) { $("er").querySelector(".er-body").innerHTML = "Drex unavailable — add NACE_API_KEY"; return; }
  const dest = a.destination;
  const name = { level1_trauma: "LEVEL I TRAUMA CENTER", level2_trauma: "LEVEL II TRAUMA CENTER", community_er: "COMMUNITY ER", urgent_care: "URGENT CARE / RELEASE" }[dest.choice];
  const p = sim.input;
  const flags = [["trauma_activation", "TRAUMA TEAM"], ["ct_head", "CT HEAD"], ["ct_cspine", "C-SPINE"], ["fast_exam", "FAST U/S"], ["blood_ready", "BLOOD BANK"]];
  const top = rows.filter(r => r.p > 0.1).slice(0, 3).map(r => `${ORGAN_NAMES[r.id]} ${(r.p * 100).toFixed(0)}%`).join(" · ") || "no serious injury expected";
  $("er").querySelector(".er-body").innerHTML = `
    INCOMING · ${p.age} y/o · ${p.belted ? "belted" : "UNBELTED"} · airbag ${p.airbag ? "yes" : "NO"} · ΔV ${sim.metrics.deltaVKmh.toFixed(0)} km/h
    <div class="dest">→ ${name} <small style="font-size:11px;color:#ff9aa3">${(dest.probabilities[dest.choice] * 100).toFixed(0)}%</small></div>
    SUSPECT: ${top}
    <div class="tags">${flags.map(([k, n]) => `<span class="tag ${a[k]?.noul > 0.5 ? "on" : ""}">${n} ${(a[k]?.noul * 100 || 0).toFixed(0)}%</span>`).join("")}</div>`;
}

// ---------------------------------------------------------------- race
let raceTimer = null;
function startRace(cmp) {
  const t0 = performance.now(); let dDone = null, lDone = null;
  $("bar-drex").style.width = "0"; $("bar-llm").style.width = "0"; $("t-drex").textContent = "deciding…"; $("t-llm").textContent = cmp ? "thinking…" : "off";
  const SCALE = 12000;
  clearInterval(raceTimer);
  raceTimer = setInterval(() => {
    const e = performance.now() - t0;
    $("bar-drex").style.width = Math.min(100, ((dDone ?? e) / SCALE) * 100) + "%";
    if (cmp) $("bar-llm").style.width = Math.min(100, ((lDone ?? e) / SCALE) * 100) + "%";
    if (dDone != null && (!cmp || lDone != null)) clearInterval(raceTimer);
  }, 30);
  return { drex: (ms) => (dDone = ms), llm: (ms) => (lDone = ms) };
}

// ---------------------------------------------------------------- actions
let lastLLM = null;
let reportNotes = [];      // extra patient facts read from a crash / EMS report by NDI
async function intake(file) {
  const out = $("ndi-out"); out.innerHTML = `<span class="chip">NDI<i>reading ${file.name}…</i></span>`;
  try {
    const r = await fetch("/api/intake?name=" + encodeURIComponent(file.name), { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file }).then(r => r.json());
    if (r.error) throw new Error(r.error);
    const i = r.input;
    if (i.age != null) { $("i-age").value = i.age; $("o-age").textContent = i.age; }
    if (i.speedKmh != null) { $("i-speed").value = Math.min(110, i.speedKmh); $("o-speed").textContent = $("i-speed").value + " km/h"; }
    if (i.belted != null) $("i-belt").checked = i.belted;
    if (i.airbag != null) $("i-bag").checked = i.airbag;
    reportNotes = r.notes ?? [];
    const chip = (k, v, hot) => `<span class="chip ${hot ? "hot" : ""}">${k}<i>${v}</i></span>`;
    const d = r.data;
    out.innerHTML = [chip("AGE", d.age), chip("BELT", d.belted ? "yes" : "NO", !d.belted), chip("AIRBAG", d.airbag_deployed ? "yes" : "NO"),
      d.estimated_speed_kmh != null ? chip("SPEED", Math.round(d.estimated_speed_kmh) + " km/h") : "", d.anticoagulants ? chip("BLOOD THINNERS", "yes", true) : "",
      d.loss_of_consciousness ? chip("LOC", "yes", true) : "", chip("NDI", `${r.fields?.filter(f => f.status === "found").length ?? 0} fields · ${(r.ms / 1000).toFixed(1)} s`)].join("");
    await run();
  } catch (e) { out.innerHTML = `<span class="chip">NDI error<i>${String(e.message).slice(0, 80)}</i></span>`; }
}
async function run() {
  const btn = $("btn-run"); btn.disabled = true;
  const input = readInputs();
  sim = simulate(input);
  renderMetrics();
  controls.autoRotate = false;
  const v = $("vid");
  if (track) {
    const t0 = Math.max(0, (window.__impactT ?? 0.4) - 0.3);
    v.currentTime = t0; v.playbackRate = 0.5; v.play().catch(() => {});
    play = { video: true, t0, t1: Math.min(v.duration || 9, t0 + 5.5), start: performance.now() };
  } else {
    v.currentTime = 0; v.playbackRate = 0.6; v.play().catch(() => {});
    play = { start: performance.now() + 900, dur: 200 * SLOW };
  }
  const cmp = $("i-cmp").checked;
  const race = startRace(cmp);
  try {
    const r = await fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ input, perception: window.__perception ?? {}, notes: reportNotes, compare: cmp }) }).then(r => r.json());
    race.drex(r.drex ? (r.drex.roundTripMs ?? r.drex.evaluation_time_ms) : 0);
    if (r.llm) { race.llm(r.llm.ms); lastLLM = r.llm; }
    A.drex = r.drex; A.llm = r.llm ?? A.llm;
    const rows = renderOrgans(r.drex, r.llm);
    renderAnalysisTables(); drawAnalysis();
    document.querySelector(".right").animate([{ background: "#ffe3e6" }, { background: "transparent" }], { duration: 900 });
    renderER(r.drex, rows);
    const dms = r.drex?.roundTripMs ?? 0;
    $("t-drex").textContent = r.drex ? `${(dms / 1000).toFixed(2)} s · $${r.drex.cost.toFixed(6)}${r.drex.cached ? " · cached" : ""}` : (r.drexError ?? "error").slice(0, 40);
    $("drex-meta").textContent = r.drex ? `${r.drex.model} · ${Object.keys(r.drex.answers).length} decisions · ${r.drex.evaluation_time_ms?.toFixed(0)} ms` : "Drex offline";
    if (cmp) {
      $("llm-name").textContent = (r.llm?.model ?? "FRONTIER LLM").toUpperCase();
      $("t-llm").textContent = r.llm ? `${(r.llm.ms / 1000).toFixed(2)} s · $${r.llm.cost.toFixed(4)}${r.llm.cached ? " · recorded run" : ""}` : (r.llmError ?? "error").slice(0, 40);
    }
    if (r.drex && r.llm) {
      const x = r.llm.ms / dms, c = r.llm.cost / Math.max(r.drex.cost, 1e-9);
      $("verdict").innerHTML = `DREX <b>${x.toFixed(0)}× FASTER</b> · <b>${c.toFixed(0)}× CHEAPER</b> · with real odds`;
    } else if (r.drex) $("verdict").innerHTML = `Triage ready in <b>${(dms / 1000).toFixed(2)} s</b>`;
  } catch (e) { console.error(e); $("verdict").textContent = "Error: " + e.message; }
  btn.disabled = false;
}

// ---------------------------------------------------------------- dashcam analysis
const SPEED_KMH = [0, 18, 45, 75, 105, 130];
function seek(v, t) { return new Promise(res => { const on = () => { v.removeEventListener("seeked", on); res(); }; v.addEventListener("seeked", on); v.currentTime = t; }); }

async function grabFrames(n = 24) {
  const v = $("vid");
  if (v.readyState < 1) await new Promise(r => v.addEventListener("loadedmetadata", r, { once: true }));
  v.pause();
  const dur = v.duration, c = document.createElement("canvas"); c.width = 640; c.height = 360;
  const g = c.getContext("2d"), frames = [];
  $("strip").innerHTML = "";
  for (let i = 0; i < n; i++) {
    const t = (i + 0.5) * (dur / n);
    await seek(v, t);
    g.drawImage(v, 0, 0, c.width, c.height);
    const url = c.toDataURL("image/jpeg", 0.72);
    const el = document.createElement("div"); el.className = "fr"; el.style.backgroundImage = `url(${url})`;
    el.innerHTML = `<b>${t.toFixed(1)}s</b><i></i>`; $("strip").appendChild(el);
    frames.push({ t, url, el });
  }
  return frames;
}

async function scan() {
  const b = $("btn-scan"); b.disabled = true; b.textContent = "◉ SAMPLING FRAMES…";
  resetResults();
  $("scan-out").innerHTML = "";
  try {
    const frames = await grabFrames(36);
    b.textContent = `◉ DREX MAX WATCHING ${frames.length} FRAMES…`;
    const t0 = performance.now(); let next = 0, tokens = 0;
    await Promise.all(Array.from({ length: 8 }, async () => {
      while (next < frames.length) {
        const f = frames[next++]; f.el.classList.add("scan");
        const r = await fetch("/api/frame", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image: f.url, t: f.t }) }).then(r => r.json()).catch(e => ({ error: e.message }));
        f.el.classList.remove("scan"); f.el.classList.add("done");
        if (r.answers) { f.a = r.answers; tokens += r.tokens; f.el.querySelector("i").style.height = (r.answers.violence.score / 5 * 100) + "%"; }
      }
    }));
    const wall = performance.now() - t0;
    const ok = frames.filter(f => f.a);
    if (!ok.length) throw new Error("Drex returned no frames");
    const P = (f, k) => f.a.phase.probabilities[k] ?? 0;
    // impact = first frame where the crash has started; peak = most thrown forward
    const impact = ok.find(f => P(f, "impact") + P(f, "peak") > 0.5) ?? ok[0];
    const peak = ok.reduce((m, f) => (f.a.lean.score + P(f, "peak") > m.a.lean.score + P(m, "peak") ? f : m));
    impact.el.classList.add("impact"); peak.el.classList.add("impact");
    const crash = ok.filter(f => f.t >= impact.t);
    const avg = (arr, fn) => arr.reduce((s, f) => s + fn(f), 0) / arr.length;
    const airbag = avg(crash, f => f.a.airbag.noul), belt = avg(ok, f => f.a.belt.noul);
    const contact = {};
    for (const f of crash) for (const [k, p] of Object.entries(f.a.head_contact.probabilities)) if (k !== "none") contact[k] = (contact[k] ?? 0) + p;
    const hc = Object.entries(contact).sort((x, y) => y[1] - x[1])[0] ?? ["none", 0];
    const hcP = hc[1] / (Object.values(contact).reduce((x, y) => x + y, 0) || 1);
    const violence = Math.max(...ok.map(f => f.a.violence.score));
    // a more violent body motion = a stiffer, shorter crash pulse (higher g for the same speed)
    const crashPhase = ok.filter(f => f.t >= impact.t);
    const meanViol = crashPhase.reduce((a, f) => a + f.a.violence.score, 0) / Math.max(1, crashPhase.length);
    camPhys = { pulseMs: Math.round(Math.max(70, Math.min(125, 128 - meanViol * 12))) };
    const lean = peak.a.lean.score;
    // ΔV is not visible from inside the cabin: estimate it from how hard the body is thrown
    const DV = [8, 18, 30, 42, 54, 70];
    const lo = Math.floor(violence), hi = Math.min(5, Math.ceil(violence));
    const kmh = Math.round(DV[lo] + (DV[hi] - DV[lo]) * (violence - lo) + lean);
    window.__perception = { crash_type: "frontal (seen from the driver camera)", head_contact: hc[0].replace("_", " "), airbag_seen: airbag > 0.5 ? "yes" : "no", intrusion: ["none", "mild", "moderate", "serious", "severe", "extreme"][Math.round(violence)] };
    window.__impactT = impact.t;
    track = ok.map(f => ({ t: f.t, lean: f.a.lean.score, bag: f.a.airbag.noul }));
    A.frames = ok;
    $("i-belt").checked = true; $("i-bag").checked = airbag > 0.5; // a belt is usually hidden from a rear camera: assume belted unless the user says otherwise
    $("i-speed").value = Math.max(15, Math.min(110, kmh)); $("o-speed").textContent = $("i-speed").value + " km/h";
    const chip = (k, v, hot) => `<span class="chip ${hot ? "hot" : ""}">${k}<i>${v}</i></span>`;
    $("scan-out").innerHTML = [
      chip("IMPACT", `@ ${impact.t.toFixed(2)} s`, true),
      chip("PEAK LOAD", `@ ${peak.t.toFixed(2)} s · lean ${["none", "slight", "moderate", "far", "extreme"][Math.round(lean)]}`, true),
      chip("HEAD HIT", `${hc[0].replace("_", " ")} ${(hcP * 100).toFixed(0)}%`, hc[0] !== "airbag"),
      chip("AIRBAG", `${(airbag * 100).toFixed(0)}%`),
      chip("BELT", belt > 0.5 ? `seen ${(belt * 100).toFixed(0)}%` : "not visible · assumed on"),
      chip("EST. ΔV", `~${kmh} km/h` + ($("src-name").textContent.includes("56 km/h") ? " · real test: 56" : "")),
      chip("DREX MAX", `${ok.length} frames · ${ok.length * 6} decisions · ${(wall / 1000).toFixed(1)} s · $${((tokens * 0.03) / 1e6).toFixed(5)}`),
    ].join("");
    await run();
  } catch (e) { $("scan-out").innerHTML = `<span class="chip">error<i>${String(e.message).slice(0, 80)}</i></span>`; }
  b.textContent = "◉ WATCH THE DRIVER · DREX MAX"; b.disabled = false;
}

async function batch() {
  $("batch").classList.remove("hidden");
  $("batch-head").innerHTML = `<div class="m"><div class="k">TRIAGING</div><div class="v">100<small>patients…</small></div></div>`;
  $("batch-grid").innerHTML = "";
  const r = await fetch("/api/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ n: 100 }) }).then(r => r.json());
  const ok = r.results.filter(x => !x.error);
  const l1 = ok.filter(x => x.destination === "level1_trauma").length;
  const llmMs = lastLLM?.ms ?? 9000, llmCost = lastLLM?.cost ?? 0.02;
  const m = (k, v, u) => `<div class="m"><div class="k">${k}</div><div class="v">${v}<small>${u}</small></div></div>`;
  $("batch-head").innerHTML = m("PATIENTS TRIAGED", ok.length, `/ ${r.n}`) + m("DREX WALL TIME", (r.wallMs / 1000).toFixed(1), `s · $${r.cost.toFixed(5)}`) +
    m("SAME ON LLM (est.)", ((llmMs * r.n) / 16 / 1000).toFixed(0), `s · $${(llmCost * r.n).toFixed(2)}`) + m("LEVEL I ALERTS", l1, "patients");
  ok.forEach((x, i) => setTimeout(() => {
    const d = document.createElement("div"); d.className = "pt " + x.destination;
    d.innerHTML = `<b>${x.input.age}y</b>${x.input.speedKmh}km/h<br>${x.input.belted ? "belt" : "NO BELT"}`;
    $("batch-grid").appendChild(d);
  }, i * 18));
}

// ---------------------------------------------------------------- wiring
for (const id of ["i-speed", "i-age", "i-belt", "i-bag"]) $(id).addEventListener("input", () => {
  $("o-speed").textContent = $("i-speed").value + " km/h"; $("o-age").textContent = $("i-age").value;
  sim = simulate(readInputs()); renderMetrics(); drawChart(); poseAt(0); drawAnalysis(); renderAnalysisTables(); $("an-scrub").max = sim.series.t.length - 1;
});
$("btn-run").onclick = run;
$("report").onchange = (e) => { const f = e.target.files[0]; if (f) intake(f); e.target.value = ""; };
document.querySelectorAll(".scen button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".scen button").forEach(x => x.classList.remove("on")); b.classList.add("on");
  const c = JSON.parse(b.dataset.s);
  $("i-speed").value = c.speedKmh; $("i-age").value = c.age; $("i-belt").checked = c.belted; $("i-bag").checked = c.airbag;
  $("o-speed").textContent = c.speedKmh + " km/h"; $("o-age").textContent = c.age;
  run();
});
$("btn-scan").onclick = scan;
function resetResults(msg = "Analyzing the new clip…") {
  camPhys = null; track = null; A.drex = null; A.frames = null; organState = {};
  $("organs").innerHTML = `<div class="legend">${msg}</div>`;
  $("er").querySelector(".er-body").textContent = "Waiting for a crash…";
  $("drex-meta").textContent = "Drex · per organ";
  for (const { o } of Object.values(labelObjs)) o.visible = false;
  sim = simulate(readInputs()); renderMetrics(); drawChart(); poseAt(0); drawAnalysis(); renderAnalysisTables();
}
document.querySelectorAll(".src[data-src]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".src").forEach(x => x.classList.remove("on")); b.classList.add("on");
  $("vid").src = b.dataset.src; $("src-name").textContent = b.dataset.name; $("strip").innerHTML = ""; $("scan-out").innerHTML = ""; window.__impactT = null;
  resetResults("Press WATCH THE DRIVER to analyze this car.");
});
$("file").onchange = (e) => {
  const f = e.target.files[0]; if (!f) return;
  document.querySelectorAll(".src").forEach(x => x.classList.remove("on")); document.querySelector(".src.up").classList.add("on");
  $("vid").src = URL.createObjectURL(f); $("src-name").textContent = f.name; $("strip").innerHTML = ""; $("scan-out").innerHTML = ""; window.__impactT = null;
};
$("btn-batch").onclick = batch;
$("batch-close").onclick = () => $("batch").classList.add("hidden");
fetch("/api/status").then(r => r.json()).then(s => {
  $("st-drex").className = "dot " + (s.nace ? "on" : "off");
  $("st-llm").className = "dot " + (s.claude ? "on" : "off");
  $("st-llm").textContent = s.llm.toUpperCase();
});
renderMetrics(); drawChart(); poseAt(0);

// debug cameras
{
  const c = new URLSearchParams(location.search).get("cam");
  const CAMS = { hands: [[-0.25, 0.75, -0.35], [0.42, 0.47, -0.1]], wheel: [[0.05, 0.62, -0.75], [0.42, 0.47, 0]], organs: [[0.75, 0.62, -0.75], [-0.08, 0.42, 0]], front: [[1.9, 0.6, 0], [0.1, 0.3, 0]], top: [[0.2, 2.6, 0.01], [0.2, 0, 0]], three: [[1.6, 1.2, -1.8], [0.2, 0.3, 0]] };
  if (CAMS[c]) { camera.position.set(...CAMS[c][0]); controls.target.set(...CAMS[c][1]); controls.update(); }
}
// debug: ?freeze=<ms>&belt=0 poses the body at that moment of the crash (for checking the 3D)
{
  const q = new URLSearchParams(location.search);
  if (q.has("freeze")) {
    if (q.get("belt") === "0") $("i-belt").checked = false;
    sim = simulate(readInputs()); renderMetrics();
    const i = Math.max(0, sim.series.t.findIndex(t => t >= +q.get("freeze")));
    for (let k = 0; k < 30; k++) poseAt(i);
    drawChart(i);
  }
}
// ?auto=1 runs the full driver-cam analysis on load (for screenshots); ?auto=run just reconstructs
{ const a = new URLSearchParams(location.search).get("auto"); if (a === "1") setTimeout(() => scan(), 800); if (a === "run") setTimeout(() => run(), 800); }

// ================================================================ analysis section (scroll down)
const A = { drex: null, llm: null, frames: null };
function lineChart(canvas, series, cursor, yUnit) {
  const g = canvas.getContext("2d"), W = canvas.width, H = canvas.height, t = sim.series.t;
  g.clearRect(0, 0, W, H);
  const max = Math.max(1e-6, ...series.flatMap(s => s.data.map(v => Math.min(v, s.cap ?? Infinity)))) * 1.12;
  const X = (ms) => 70 + (ms / 200) * (W - 100), Y = (v) => H - 50 - (Math.min(v, max) / max) * (H - 90);
  g.font = "20px JetBrains Mono"; g.fillStyle = "#6b6b6b"; g.strokeStyle = "#ececec"; g.lineWidth = 2;
  for (let ms = 0; ms <= 200; ms += 25) { g.beginPath(); g.moveTo(X(ms), 20); g.lineTo(X(ms), H - 50); g.stroke(); if (ms % 50 === 0) g.fillText(ms + " ms", X(ms) - 28, H - 18); }
  for (let k = 0; k <= 4; k++) { const v = (max / 4) * k; g.fillText(v.toFixed(v < 10 ? 1 : 0) + yUnit, 4, Y(v) + 6); }
  series.forEach(s => { g.strokeStyle = s.color; g.lineWidth = s.w ?? 4; g.beginPath(); s.data.forEach((v, i) => { const x = X(t[i]), y = Y(v); i ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); });
  let lx = W - 30; g.font = "bold 22px Montserrat";
  [...series].reverse().forEach(s => { const w = g.measureText(s.name).width; lx -= w + 30; g.fillStyle = s.color; g.fillText(s.name, lx, 40); });
  if (cursor != null) { g.strokeStyle = "#e3192b"; g.lineWidth = 3; g.beginPath(); g.moveTo(X(t[cursor]), 20); g.lineTo(X(t[cursor]), H - 50); g.stroke(); }
}
function drawAnalysis(cursor = null) {
  if (!$("an-acc")) return;
  const s = sim.series;
  lineChart($("an-acc"), [{ name: "CAR", data: s.car, color: "#b5b5b5" }, { name: "CHEST", data: s.chest, color: "#0a0a0a" }, { name: "HEAD", data: s.head, color: "#e3192b", cap: 160 }], cursor, "g");
  lineChart($("an-mov"), [{ name: "TORSO", data: s.torsoX.map(v => v * 100), color: "#0a0a0a" }, { name: "HEAD vs TORSO", data: s.headX.map(v => Math.abs(v) * 100), color: "#e3192b" }], cursor, "cm");
  lineChart($("an-knee"), [{ name: "KNEE / FEMUR LOAD", data: s.kneeF, color: "#0a0a0a" }], cursor, "kN");
  const i = cursor ?? 0;
  $("an-now").innerHTML = `<b>${(s.t[i] ?? 0).toFixed(0)} ms</b> · car ${(s.car[i] ?? 0).toFixed(0)} g · chest ${(s.chest[i] ?? 0).toFixed(0)} g · head ${(s.head[i] ?? 0).toFixed(0)} g · torso moved ${((s.torsoX[i] ?? 0) * 100).toFixed(0)} cm`;
}
function renderAnalysisTables() {
  const m = sim.metrics, r = sim.risk, p = sim.input;
  const pct = (v) => (v * 100).toFixed(0) + "%";
  const sev = (v) => v > 0.25 ? "bad" : v > 0.08 ? "warn" : "ok";
  $("an-crit").innerHTML = `<tr><th>Reading</th><th>Value</th><th>Limit</th><th>Risk</th><th>How it's computed</th></tr>` + [
    ["Head · HIC₁₅", m.hic15.toFixed(0), "700", r.head, "worst 15 ms of head acceleration, ^2.5"],
    ["Chest deflection", m.chestDeflMm.toFixed(0) + " mm", "63 mm", r.chest, "belt + airbag + wheel load on the sternum"],
    ["Neck · Nij", m.nij.toFixed(2), "1.0", r.neck, "neck tension + bending"],
    ["Femur load", m.femurKN.toFixed(1) + " kN", "10 kN", r.femur, "knees into the dash"],
    ["ΔV (speed change)", m.deltaVKmh.toFixed(0) + " km/h", "—", null, "∫ crash pulse, incl. rebound"],
    ["Any serious injury", pct(r.joint), "—", r.joint, "1 − Π(1 − Pᵢ)"],
  ].map(([k, v, l, risk, how]) => `<tr><td>${k}</td><td class="num">${v}</td><td class="num">${l}</td><td class="num ${risk == null ? "" : sev(risk)}">${risk == null ? "—" : pct(risk)}</td><td class="how">${how}</td></tr>`).join("");
  const d = A.drex?.answers, l = A.llm?.answers;
  if (d) {
    const LV = ["none", "AIS 1", "AIS 2", "AIS 3", "AIS 4", "AIS 5"];
    $("an-organs").innerHTML = `<tr><th>Organ</th>${LV.map(x => `<th>${x}</th>`).join("")}<th>Serious (3+)</th><th>LLM guess</th></tr>` +
      Object.keys(ORGAN_NAMES).map(id => {
        const pr = d[id]?.probabilities ?? {}; const ser = [3, 4, 5].reduce((s, k) => s + (pr[k] ?? 0), 0);
        return `<tr><td>${ORGAN_NAMES[id]}</td>${LV.map((_, k) => { const v = pr[k] ?? 0; return `<td class="heat" style="background:rgba(227,25,43,${Math.min(0.85, v)});color:${v > .45 ? "#fff" : "#0a0a0a"}">${(v * 100).toFixed(0)}</td>`; }).join("")}<td class="num ${sev(ser)}">${pct(ser)}</td><td class="num">${typeof l?.[id] === "number" ? LV[l[id]] : "—"}</td></tr>`;
      }).join("");
    const Q = [["destination", "Destination"], ["trauma_activation", "Activate trauma team"], ["ct_head", "CT head"], ["ct_cspine", "C-spine imaging"], ["fast_exam", "FAST ultrasound"], ["blood_ready", "Blood bank"]];
    $("an-er").innerHTML = `<tr><th>ER decision</th><th>Drex Max (odds)</th><th>LLM (one answer)</th></tr>` + Q.map(([k, n]) => {
      const a = d[k]; const dv = a?.type === "choice" ? `${a.choice} · ${pct(a.probabilities[a.choice])}` : pct(a?.noul ?? 0);
      const lv = l ? (typeof l[k] === "number" ? pct(l[k]) : String(l[k] ?? "—")) : "—";
      return `<tr><td>${n}</td><td class="num">${dv}</td><td class="num">${lv}</td></tr>`;
    }).join("");
    $("an-time").innerHTML = `<tr><th></th><th>Drex Max</th><th>${(A.llm?.model ?? "LLM").toUpperCase()}</th></tr>
      <tr><td>Round trip</td><td class="num">${((A.drex.roundTripMs ?? 0) / 1000).toFixed(2)} s</td><td class="num">${A.llm ? (A.llm.ms / 1000).toFixed(2) + " s" : "—"}</td></tr>
      <tr><td>Model time</td><td class="num">${(A.drex.evaluation_time_ms ?? 0).toFixed(0)} ms</td><td class="num">—</td></tr>
      <tr><td>Cost</td><td class="num">$${(A.drex.cost ?? 0).toFixed(6)}</td><td class="num">${A.llm ? "$" + A.llm.cost.toFixed(4) : "—"}</td></tr>
      <tr><td>Input tokens</td><td class="num">${A.drex.usage?.input_tokens ?? "—"}</td><td class="num">${A.llm?.usage?.input_tokens ?? "—"}</td></tr>`;
  }
  if (A.frames) {
    const PH = (f) => Object.entries(f.a.phase.probabilities).sort((x, y) => y[1] - x[1])[0][0];
    const HC = (f) => f.a.head_contact.choice;
    $("an-frames").innerHTML = `<tr><th>Video time</th><th>Frame</th><th>Phase</th><th>Airbag</th><th>Body lean</th><th>Head hits</th><th>Violence</th></tr>` +
      A.frames.map(f => `<tr class="${f.el?.classList.contains("impact") ? "imp" : ""}" data-t="${f.t}"><td class="num">${f.t.toFixed(2)} s</td><td><img src="${f.url}" /></td><td>${PH(f)}</td><td class="num">${pct(f.a.airbag.noul)}</td><td><span class="mini"><i style="width:${(f.a.lean.score / 4) * 100}%"></i></span></td><td>${HC(f).replace("_", " ")}</td><td><span class="mini red"><i style="width:${(f.a.violence.score / 5) * 100}%"></i></span></td></tr>`).join("");
    $("an-frames").querySelectorAll("tr[data-t]").forEach(tr => tr.onclick = () => { const v = $("vid"); v.pause(); v.currentTime = +tr.dataset.t; window.scrollTo({ top: 0, behavior: "smooth" }); });
  } else $("an-frames").innerHTML = `<tr><td class="how">Press “WATCH THE DRIVER · DREX MAX” to fill the frame-by-frame log.</td></tr>`;
}
// rewind: scrub the crash, the 3D, the charts and the video together
function scrub(i) {
  play = null;
  i = Math.max(0, Math.min(sim.series.t.length - 1, i));
  let vt = null;
  if (track) { const t0 = Math.max(0, (window.__impactT ?? 0.4) - 0.3); vt = t0 + (i / (sim.series.t.length - 1)) * 5.5; const v = $("vid"); v.pause(); v.currentTime = Math.min(v.duration || 9, vt); }
  for (let k = 0; k < 12; k++) poseAt(i, vt);
  drawChart(i); drawAnalysis(i);
  $("clock").textContent = `T+${(sim.series.t[i] / 1000).toFixed(3)} s`;
  $("an-scrub").value = i;
}
$("an-scrub").max = sim.series.t.length - 1;
$("an-scrub").oninput = (e) => scrub(+e.target.value);
let autoPlay = null;
$("an-play").onclick = () => {
  if (autoPlay) { clearInterval(autoPlay); autoPlay = null; $("an-play").textContent = "▶ PLAY"; return; }
  let i = +$("an-scrub").value; if (i >= sim.series.t.length - 1) i = 0;
  $("an-play").textContent = "❚❚ PAUSE";
  autoPlay = setInterval(() => { i += 1; scrub(i); if (i >= sim.series.t.length - 1) { clearInterval(autoPlay); autoPlay = null; $("an-play").textContent = "▶ PLAY"; } }, 40);
};
document.querySelectorAll("[data-jump]").forEach(b => b.onclick = () => { const ms = +b.dataset.jump; scrub(sim.series.t.findIndex(t => t >= ms)); });
drawAnalysis(); renderAnalysisTables();

// ---------------------------------------------------------------- resizable sidebar
{
  const sp = $("splitter"), root = document.documentElement;
  const setLeft = (px) => { const v = Math.max(280, Math.min(innerWidth * 0.72, px)); root.style.setProperty("--left", v + "px"); document.body.classList.toggle("bigvid", v > innerWidth * 0.42); resize(); };
  let dragging = false;
  sp.addEventListener("mousedown", (e) => { dragging = true; sp.classList.add("drag"); document.body.classList.add("dragging"); e.preventDefault(); });
  addEventListener("mousemove", (e) => { if (dragging) setLeft(e.clientX - 5); });
  addEventListener("mouseup", () => { if (!dragging) return; dragging = false; sp.classList.remove("drag"); document.body.classList.remove("dragging"); });
  sp.addEventListener("dblclick", () => { const cur = parseFloat(getComputedStyle(root).getPropertyValue("--left")); setLeft(cur > 500 ? 360 : innerWidth * 0.6); });
}
