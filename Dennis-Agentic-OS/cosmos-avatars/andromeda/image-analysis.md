# Image Analysis — ANDROMEDA avatar (spiral galaxy emblem)

Reference: `references/andromeda.png` (512×512 crop of the six-panel emblems sheet).
Intended use: real-time browser avatar (~30–96 px display, rendered offscreen at 128–256 px),
idle-animating in a dashboard rail. Not a hero render.

## Layer 1 — Identification & classification
- Observe: a barred spiral galaxy seen at moderate inclination, rendered as space art.
  Work type: *galaxy emblem*; classification: *volumetric astronomical scene (particle-dominant)*.
  `primaryDomain: object` (no character anatomy). Confidence 0.95.
- Inventory before meaning: luminous central bulge; two dominant spiral arms plus fainter
  outer arm fragments; embedded star points; dark voids between arms; scattered field stars.

## Layer 2 — Overall form & silhouette
- Observe: overall bounding volume approximates a flattened **oblate ellipsoid / disc**
  (thickness ≈ 0.25–0.35 × diameter) tilted toward the viewer; outer arms extend past the
  disc into an irregular silhouette. Radial symmetry broken into a **two-arm logarithmic
  spiral** (asymmetric chirality visible: arms trail counter-clockwise outward).
- Shape language: organic (spiral), not geometric. Aspect: disc diameter ≈ 0.85 of frame width.

## Layer 3 — Macro → meso → micro decomposition
- Macro: (a) galactic disc/spiral system, (b) central bulge+core, (c) star field backdrop.
- Meso: disc → individual arms (2 dominant + ~2 faint outer); bulge → core point + inner glow
  halo; backdrop → near-field bright stars vs faint dust stars.
- Micro: arm → dust-lane streaks (dark filaments), H-region hotspots (bright knots along arms),
  embedded star points; core → cross-shaped diffraction bloom; field → 4-point star sprites.

## Layer 4 — Spatial relationships
- `<bulge, center-of, disc>` — bulge sits at disc center, embedded (contact: embed).
- `<arms, trail-from, bulge>` — arms originate at the bulge ends of a short bar and wind
  outward (contact: overlap into disc plane).
- `<hotspots, embedded-in, arms>` — bright knots inside arm volume (contact: embed).
- `<star sprites, surround, disc>` — backdrop layer both behind and in front (overlap).

## Layer 5 — Materials & surface (PBR)
- Arms: emissive particulate medium — additive-blended point/patch sprites; albedo n/a
  (emissive), hue ramp cyan-blue → violet with the disc plane.
- Bulge/core: high-emission warm gradient — white-yellow center → amber → deep orange rim
  (emissive, additive). Not metalness/roughness shading; pure emission.
- Dust lanes: dark absorptive filaments over the arms (multiply/normal-blend dark patches).
- Stars: tiny emissive points, white-blue, some with 4-point diffraction cross.
- Background: near-black deep space (#050510–#0a0a18), slight vignette.

## Layer 6 — Color & finish
- Core: #fff7d9 → #ffc46b → #ff8a3c (ordered stops, center→rim of bulge).
- Arms: #7fd4ff (cyan-blue) → #4f7dff (blue) → #9b5cff (violet) along outer winding.
- Hotspots: white-cyan #eaffff. Dust: #101226 semi-opaque.
- Finish: additive glow throughout (no matte/metallic surfaces — emission-dominated scene).

## Layer 7 — Identity-defining features
1. Two-arm spiral winding with visible chirality (arms trail counter-clockwise outward).
2. Warm golden core contrasting the cool blue/violet disc.
3. Blue→violet hue ramp along the arms (cyan inner, violet outer).
4. Bright arm hotspots (knots) tracing the arms.
5. Scattered 4-point diffraction stars (foreground sparkle).
(Each maps to a `component.localFeatures` entry and a `featureReviewTarget`.)

## Layer 8 — Uncertainty & single-image limits
- Rear side of the disc plane occluded — arm count behind the bulge uncertain (could be 2
  arms wrapping, rendered as 2; acceptable for an emblem).
- Exact dust-lane topology is artistic, not physical — treat as stylization, state it.
- Single view: no depth order between foreground/background star layers beyond size/brightness
  heuristics. Undetermined → fixed by deterministic seed, documented as stylized.

## Consequences for the build
- Geometry strategy: particle systems (THREE.Points + generated sprite texture) for arms and
  stars; additive-blended plane sprites for core bloom and diffraction spikes; short dark
  streak sprites for dust lanes; NO dense triangle mesh — `performanceBudget.targetTriangles`
  tier *low* (≤6k; most identity lives in particles/shaders).
- Animation: slow rigid disc rotation (spiral chirality preserved), subtle core pulse.
- Deterministic seed for all placement noise.
