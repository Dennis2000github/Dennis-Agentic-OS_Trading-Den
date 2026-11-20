// Generic harness entry for one emblem factory.
//
// Evaluation render contract (from the generated factory):
//   - plain renderer, NO composer (bloom/DOF corrupt the deterministic gates)
//   - configure<Name>Renderer() sets ACES tone mapping + sRGB output
//   - frame<Name>Camera() frames the model at a given azimuth/elevation
//
// Query params: ?emblem=<id>&view=front|tilted|edge&size=<px>&spin=<deg>
// Flags window.__avatarReady after the scene has settled (>= 24 rendered
// frames) so the CDP capture script can screenshot a live, settled frame.

import * as THREE from 'three';
import {
  createModel,
  frameCamera,
  configureRenderer,
  lookDevLights,
  environment,
} from './factory.js';

const params = new URLSearchParams(window.location.search);
const size = Math.max(64, Math.min(1024, parseInt(params.get('size') || '512', 10) || 512));
const views: Record<string, { azimuthDeg: number; elevationDeg: number }> = {
  front: { azimuthDeg: 0, elevationDeg: 22 },
  tilted: { azimuthDeg: 30, elevationDeg: 28 },
  edge: { azimuthDeg: 90, elevationDeg: 62 },
  rear: { azimuthDeg: 180, elevationDeg: 22 },
  left: { azimuthDeg: 270, elevationDeg: 22 },
  right: { azimuthDeg: 90, elevationDeg: 22 },
};
const view = views[params.get('view') || 'front'] || views.front;
const spinDeg = parseFloat(params.get('spin') || '0') || 0;

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, preserveDrawingBuffer: true });
renderer.setSize(size, size);
renderer.setPixelRatio(1);
document.body.appendChild(renderer.domElement);
configureRenderer(renderer);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05050a);

const model = createModel({ qualityPriority: 'balanced' });
if (spinDeg) model.rotation.y = (spinDeg * Math.PI) / 180;
scene.add(model);

// Blockout/structural passes build PBR placeholder materials that need the
// factory's look-dev light rig; emissive layers are unaffected by lights.
scene.add(lookDevLights('reference'));
scene.environment = environment(renderer);

const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
frameCamera(camera, model, { azimuthDeg: view.azimuthDeg, elevationDeg: view.elevationDeg, margin: 1.15 });

// Ambient self-emissive scene: no lights required, but the look-dev lights
// helper is imported by the factory module if the spec declares them.

let frames = 0;
// setInterval, not rAF: headless Chrome throttles requestAnimationFrame for
// background/occluded pages, which stalls the settle counter.
const timer = setInterval(() => {
  renderer.render(scene, camera);
  frames += 1;
  if (frames === 12 && !(window as any).__avatarReady) {
    (window as any).__avatarReady = 'ready';
    (window as any).__avatarMeta = {
      frames,
      size,
      view: params.get('view') || 'front',
      children: model.children.length,
    };
  }
  if (frames > 600) clearInterval(timer);
}, 33);
