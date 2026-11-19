---
name: reference-video-recreation
description: "Rebuilds an observed reference video as a shot-accurate recreation package — chronological analysis, a copy-ready generation prompt, provider generation, and comparison-driven iteration. Use when you must reproduce a video's wardrobe, motion, camera and cut rhythm with Seedance 2.5 or a compatible image/video model, optionally swapping in a supplied character."
---

# Reference-Video-to-Recreation

Convert one accessible reference video into a verified shot breakdown, a generation prompt, generated takes, and a saved iteration log — without inventing anything the footage does not show.

## When to use

- A user supplies a reference video (URL or file) and asks to "make this again", "recreate this reel", "same outfits/cuts but my character".
- A style, wardrobe, or edit rhythm must be reproduced rather than merely described.
- An existing generated clip must be diagnosed against a reference and corrected.
- A character reference image must replace the person in an otherwise faithful recreation.

Do not use for: pure caption/transcript extraction, trend research without a target video, or cloning a creator's identity, voice, or licensed music.

## Inputs

| Input | Required | Notes |
|---|---|---|
| Reference video (URL or local file) | Yes | Publicly accessible or user-provided. Access must respect platform terms and access controls. |
| Access evidence | Yes | Confirmation the media was retrieved (file present, duration/resolution readable). Metadata alone is not retrieval. |
| Transcript / on-screen text | Optional | If a transcript tool is used, keep it separate from visual claims. |
| Character reference image | Optional | Triggers the character replacement branch. |
| Target provider + model | Yes for generation | Seedance 2.5 or compatible text-to-video / image-to-video endpoint. |
| Constraints | Optional | Duration, aspect ratio, brand-safety limits, no-audio requirement, budget/take limit. |

## Outputs

1. `evidence.md` — what was observed, transcribed, and left unknown, with timestamps.
2. `shotlist.md` — chronological scene table (wardrobe, action, camera, timing, transition).
3. `prompt_v1.md` — first-pass generation prompt using the master template.
4. `storyboard/` — reference stills at each cut point (if media was acquired) or a text-only storyboard.
5. `takes/` — generated clips, named by shot, provider, model, seed, and timestamp.
6. `comparison.md` — scorecard of take vs. reference per shot, with the single next change.
7. `refinement.md` — the three highest-impact weaknesses and their fixes.
8. `library/` — winning prompt + settings + accepted take, plus a one-line "why it worked".

## Tool requirements

| Capability | Needed for | Constraint |
|---|---|---|
| Media retrieval (public downloader or user upload) | Acquire | Must honour platform terms, robots/ToS, and access controls; no circumvention |
| `ffmpeg` (or equivalent) | Frame sampling, cut detection, duration/probe, assembly | Local, non-destructive; keep original untouched |
| Transcript/subtitle extraction | TRANSCRIPT-derived facts only | Never infer dialogue from visuals |
| Vision-capable model | Scene analysis, comparison scoring | Label its output as interpretation, not fact |
| Generation endpoint (Seedance 2.5 or compatible) | Generate | Record model, version, seed, duration, aspect ratio, reference-image usage |
| Editor or `ffmpeg` concat | Assembly, overlay text, audio bed | Text and music are post-production, not model output |
| Notes/spreadsheet | Iteration log | One row per take |

If any generation capability is unavailable, deliver the prompt + storyboard + provider settings as a manual handoff (see Failure handling) and state clearly that no clip was generated.

## Evidence labels

Apply one label to every claim in every artifact:

- **OBSERVED** — visible in the footage (wardrobe, pose, set dressing, cut points, framing).
- **TRANSCRIPT** — from subtitles, captions, or on-screen text captured by a transcript tool.
- **INFERRED** — a reasoned deduction (e.g. "eye-level" from a level horizon); must say what it is inferred from.
- **UNKNOWN** — not established by the material. Never fill with plausible detail.
- **PROVENANCE** — retrieval method, tool, timestamp, file hash. Required before saying "downloaded" or "generated".

Rule: absence of evidence is recorded as UNKNOWN, not as a creative choice. If you did not generate a clip, do not describe one as generated.

## Core methods

1. **Acquire lawfully and prove it.** Retrieve only public or user-supplied media; capture duration, resolution, frame rate, and aspect ratio from the file itself (`ffprobe`). Write a provenance line. If retrieval fails, stop and request the file — never proceed from a thumbnail or description.
2. **Index the timeline before interpreting.** Detect hard cuts; list candidate scene boundaries with timestamps. Reconcile detected cuts with the supplied analysis; where they disagree, mark the boundary UNCERTAIN and re-inspect frames.
3. **Describe each scene in a fixed field order.** Subject → wardrobe/props → setting → position → action/expression → camera → lighting/colour → composition → transition → timing. Fixed order prevents omissions and keeps scenes comparable.
4. **Separate the three fact classes.** Keep OBSERVED, TRANSCRIPT and INFERRED in distinct columns or tags; list UNKNOWNs explicitly at the end of the shot list.
5. **Extract generation-grade detail.** Convert descriptions into controllable parameters: garment colour name + silhouette + rise + drape, accessory list, heel type, hand positions, body orientation, gaze, cut durations, camera height/angle/movement, key/fill quality, colour temperature, set-dressing positions (left/right).
6. **Build the prompt from the shot list, not from memory.** One block per scene, chronological, with continuity and provider constraints last. No adjectives that are not traceable to an observation.
7. **Adapt the character only when an image is supplied.** Keep wardrobe, pose, motion, camera and timing from the reference; replace identity attributes described from the image only. Do not blend identities unless asked.
8. **Refine exactly three weaknesses.** Rank by visual impact on the first three seconds and on cut-to-cut coherence; fix the top three in one pass, one variable at a time.
9. **Generate through the named provider.** Prefer one clip per shot over one long clip when the reference has hard cuts — cuts are cheaper to control in the edit than in the model. Record every setting.
10. **Compare mechanically.** Sample the same timestamps in reference and take; score each rubric row; produce a side-by-side contact sheet. Do not accept on vibes.
11. **Iterate with a single change per cycle.** Hold seed and all other parameters constant; change one line of the prompt, one reference frame, or one duration. Cap at 2–3 cycles per issue, then reassess approach.
12. **Save the winners.** Persist prompt text, negative constraints, seed, model/version, aspect ratio, durations, reference images, accepted take, and the comparison that justified acceptance.

## Workflow (default path)

`Acquire → Analyze → Extract → Adapt → Refine → Generate → Compare → Iterate → Save`

1. **Acquire** — retrieve media; probe technicals; write provenance. Gate: file readable, duration matches expectation.
2. **Analyze** — cut detection + chronological scene table with evidence labels. Gate: every second of runtime assigned to a scene or marked UNKNOWN.
3. **Extract** — fill the generation-parameter fields per scene; pull reference stills at cut points and mid-shot. Gate: no field left blank without "UNKNOWN".
4. **Adapt** — assemble the master prompt; insert character reference if supplied; add provider constraint block. Gate: every prompt clause traceable to the shot list.
5. **Refine** — choose the three highest-impact weaknesses before generating, not after (identity continuity, wardrobe accuracy, cut rhythm are typical). Gate: each priority has a concrete fix.
6. **Generate** — produce takes per shot with recorded settings. Gate: settings logged; failures logged, not hidden.
7. **Compare** — score take vs. reference on the rubric; produce contact sheets. Gate: every rubric row scored with evidence.
8. **Iterate** — apply one change, regenerate, re-score. Gate: improvement measured, or approach changed after 3 cycles.
9. **Save** — archive prompt, settings, takes, comparison, and reuse note. Gate: a second operator could regenerate the accepted result from the archive.

## Scene-by-scene analysis

Reference: YouTube `FxGqIFlAuzc`, upload date 2025-09-22, runtime ≈18 s (from supplied timestamps). Media not acquired in the source session; the table below is the supplied analysis, re-labelled by evidence class. Verify against frames before generating.

| # | Time | Subject & wardrobe | Action | Camera | Transition | Confidence |
|---|---|---|---|---|---|---|
| 1 | 00:00–00:05 | Young woman, shoulder-length dark brown wavy hair, light makeup (OBSERVED); light blue pantsuit over white inner top, white pointed-toe heels (OBSERVED); holds folded stack of white/beige/grey trousers (OBSERVED) | Holds stack, lowers it, smiles, gestures at outfit, confident pose (OBSERVED); overlay "MUST HAVE PANTS that are not black" (TRANSCRIPT) | Medium-full, static (OBSERVED); centred (OBSERVED) | Hard cut into outfit 1 (OBSERVED) | High |
| 2 | 00:05–00:08 | Same woman; black sleeveless high-neck top, wide-leg beige trousers, thin black belt with gold buckle, black shoulder bag, black pointed-toe heels (OBSERVED) | Hands in pockets, direct gaze, confident (OBSERVED) | Medium-full, static (OBSERVED) | Jump cut (OBSERVED) | High |
| 3 | 00:08–00:11 | Same top/accessories; crisp white wide-leg trousers (OBSERVED) | Slight pose shift, hands in pockets, direct gaze (OBSERVED) | Medium-full, static (OBSERVED) | Jump cut on beat/lyric change (OBSERVED cut; beat claim INFERRED) | Medium-high |
| 4 | 00:11–00:13 | Same top/accessories; taupe/dark beige wide-leg trousers (OBSERVED) | Posing, subtle hip shift (OBSERVED) | Medium-full, static (OBSERVED) | Jump cut (OBSERVED) | Medium-high |
| 5 | 00:13–00:18 | Same top/accessories; grey-blue wide-leg trousers (OBSERVED) | Shows trouser sides with hands, then turns/steps slightly off-camera, smiling (OBSERVED) | Medium-full, static (OBSERVED) | Jump cut ending on movement (OBSERVED) | Medium |

**Set (all scenes, OBSERVED):** white wall; pink wall sconce left; green potted cactus/plant; pink tufted couch right. Bright indoor light, cool blue tones against pink accents.

**Inferred:** eye-level camera and vertical 9:16 framing (INFERRED from reel format and the supplied prompt skeleton — confirm from file metadata).

**UNKNOWN — do not invent:**
- Audio track, lyrics, BPM, exact cut-to-beat alignment; no dialogue transcript captured.
- Exact resolution, frame rate, aspect ratio, camera height, lens, distance.
- Text-overlay font, size, colour, position, animation.
- Whether the trouser stack contains exactly the colours listed; count of items.
- Whether cuts are frame-accurate jump cuts or trims of one continuous take.
- Identity of creator/model, garment brands beyond the four product links, prices, fabric.
- Final frame content after the subject moves off-camera.

## Recreation workflow

Phase detail for the pipeline above; each phase ends with a saved artifact.

**1. Acquire.** Probe with `ffprobe`; hash the file; write `evidence.md` header (URL, title, upload date, retrieval tool, timestamp, hash). If only metadata is available, record "media not acquired" and pause generation.

**2. Analyze.** Extract a frame every 0.5 s plus every detected cut (±2 frames). Build the shot table in fixed field order. Where durations are inferred from a supplied breakdown rather than measured, tag `INFERRED`.

**3. Extract.** For each shot record: subject description; garment colour/silhouette/rise/drape; accessories; hand and body position; gaze; set-dressing left/right; camera height/angle/movement; lighting quality and colour temperature; shot duration; transition type. Copy frames into `storyboard/shotN_first.png` and `shotN_mid.png`.

**4. Adapt.** Write the prompt per shot using the master template. Order clauses: style → subject → wardrobe → setting → action (chronological) → motion → camera → lighting/colour → composition → timing/transitions → continuity → provider constraints. If a character image is supplied, insert the identity block and keep everything else fixed.

**5. Refine.** Fix the top three weaknesses before spending takes (see Refinement priorities). Convert each into a prompt constraint, a reference image, or a post-production step.

**6. Generate.** Preferred: five clips at 5 s / 3 s / 3 s / 2 s / 5 s, then assemble — this preserves cut control and isolates failures. Fallback: one 18 s multi-shot prompt, accepting weaker cut discipline. Log provider, model, seed, aspect ratio, duration, reference images, and cost per take.

**7. Compare.** For each shot, pull the take frame at the same relative timestamp as the reference still; place side by side; score the rubric. Record the single largest delta.

**8. Iterate.** Change one thing (wardrobe wording, reference still, duration, pose cue), regenerate only the failing shot, re-score. Keep the best take per shot in `takes/accepted/`.

**9. Save.** Archive `prompt_final.md`, settings JSON, accepted clips, comparison sheet, and a reuse note ("static medium-full + jump cuts + one wardrobe variable per shot"). Strip or replace any unlicensed audio and on-screen text before publication.

## Initial generation prompt

Status: **not generated.** This is `prompt_v1.md`, derived only from labelled observations. Replace bracketed UNKNOWNs before use.

```text
ROLE: Generate a short vertical fashion styling reel that recreates a reference edit.

STYLE: Bright, clean indoor fashion reel; crisp colour grading; cool-neutral whites with pink accents; realistic skin and fabric texture; no fantasy or cinematic stylisation. [UNKNOWN: exact aspect ratio — confirm; assume 9:16 vertical until verified]

SUBJECT (OBSERVED): A young woman with shoulder-length dark brown wavy hair and light natural makeup. Slim-to-average build. Calm, confident expression, direct eye contact with camera. [If a character image is supplied, replace this paragraph with the character block in the Character replacement section.]

SET (OBSERVED, identical in every shot): Minimalist indoor room. White wall background. Pink wall sconce on the left. Small green potted cactus/plant. Pink tufted couch on the right. No other furniture. Bright, even indoor lighting.

CAMERA (OBSERVED + INFERRED): Locked-off static camera, no pan, no zoom, no handheld motion. Medium-full framing from mid-thigh to above head, subject centred. Eye-level height (INFERRED). Consistent framing and distance across all shots.

SHOT 1 — 0.0–5.0 s
Action (chronological): Woman stands centred facing camera wearing a light blue pantsuit over a white inner top and white pointed-toe heels. She holds a folded stack of trousers (white, beige, grey) in her arms at chest height. She lowers the stack to her side, smiles, and gestures toward her outfit with one hand, settling into a confident pose.
Motion: natural, unhurried; fabric settles; hair moves slightly.
Post-production only: text overlay "MUST HAVE PANTS that are not black" [TRANSCRIPT]. Do not ask the model to render text.

SHOT 2 — 5.0–8.0 s
Wardrobe: black sleeveless high-neck top tucked into wide-leg beige high-waisted trousers; thin black belt with gold buckle; black shoulder bag; black pointed-toe heels.
Action: standing centred, both hands in pockets, weight even, direct gaze, slight shoulder relax.

SHOT 3 — 8.0–11.0 s
Wardrobe: same black top, belt, bag and heels; crisp white wide-leg high-waisted trousers.
Action: same stance, subtle pose shift — small hip adjustment, hands in pockets, direct gaze.

SHOT 4 — 11.0–13.0 s
Wardrobe: same black top, belt, bag and heels; taupe/dark beige wide-leg high-waisted trousers.
Action: posing with a subtle hip shift; hands in pockets; direct gaze.

SHOT 5 — 13.0–18.0 s
Wardrobe: same black top, belt, bag and heels; grey-blue wide-leg high-waisted trousers.
Action: holds the trouser sides briefly to show the drape, then turns and steps slightly toward frame edge, smiling, ending mid-movement.

TIMING / TRANSITIONS: Hard jump cuts between shots 1→2→3→4→5. Each cut lands on the same pose family with only wardrobe changed (classic jump-cut continuity). Shot durations 5/3/3/2/5 s.

CONTINUITY: Identical subject, hair, makeup, set, camera position, framing, lighting and colour grade in every shot. Only trouser colour and the shot-1 suit change. No camera drift, no background rearrangement, no new props.

PROVIDER CONSTRAINTS: [Seedance 2.5 or compatible] — generate each shot as a separate clip at the listed duration; static camera; no on-screen text; no watermark; no added music; anatomically correct hands and feet; consistent face and hair across clips; [aspect ratio, resolution, fps per provider limits].

NEGATIVE: no camera movement, no zoom, no extra people, no changing background, no morphing hands, no distorted feet, no text artefacts, no outfit colour drift between shots, no cartoon rendering.
```

## Refinement priorities

Fix these first — they dominate perceived fidelity. Pick exactly three per pass.

1. **Identity and wardrobe continuity across cuts.** Highest impact because five jump cuts expose any face/hair/garment drift. Fix: one character reference image reused for every clip; repeat the exact subject and wardrobe strings verbatim per shot; generate shots in one session with the same seed; reject any take where the face or hair changes between shots.
2. **Trouser silhouette and colour accuracy.** The video's payload is "wide-leg, high-waisted, non-black trousers"; a straight-leg or grey-beige misread destroys the concept. Fix: specify rise, leg width, drape, hem length, belt, and heels; include "fabric falls straight from the hip, breaks once at the shoe"; name colours precisely (beige / crisp white / taupe / grey-blue) and forbid substitutions.
3. **Cut rhythm and locked frame.** A drifting camera or loose cut timing reads as a different edit. Fix: explicit static-camera clause; per-shot durations; assemble to the measured cut points in the editor rather than trusting the model's internal pacing.

Deferred (post-production, not generation): text overlay, music bed, colour-grade match, final trim.

## Character replacement (optional)

Trigger only when the user supplies a character image.

- **Keep from the reference:** set, camera, framing, shot count, durations, wardrobe, accessories, poses, motion, cut rhythm.
- **Replace:** face, hair, skin tone, body proportions — described from the supplied image only, with no assumptions about identity, ethnicity, or age beyond what is visible.
- **Method:** use the provider's image-conditioned / character-reference mode; attach the image to every clip, not just the first; keep the reference image at consistent crop and lighting.
- **Prompt block:** "Use the attached character reference for face, hair and body. Preserve identity exactly. Do not blend with the reference video's original subject. Same person in all shots."
- **Checks:** face match at 100 % zoom on the first and last frame of each clip; hair length/parting; hand and neck skin continuity; no age regression or beautification drift.
- **Rights:** only proceed where the user has permission to use the likeness; do not recreate a real person from the reference video without consent.

## Iteration and comparison

**Comparison rubric (score 0–2 per row, per shot):**

| Row | Pass condition |
|---|---|
| Shot count and order | 5 shots, same sequence |
| Durations | Within ±0.3 s of 5/3/3/2/5 |
| Wardrobe colourway | Each shot's trouser colour matches the reference label |
| Silhouette | Wide leg, high waist, correct hem and drape |
| Accessories | Black top, belt + gold buckle, shoulder bag, pointed-toe heels present where observed |
| Pose and action | Matches the described stance and movement per shot |
| Camera | Static, same framing and height, no drift |
| Set | White wall, pink sconce left, plant, pink couch right |
| Lighting/grade | Bright indoor, cool-neutral with pink accents |
| Cut behaviour | Hard jump cuts, wardrobe-only change |
| Artifacts | No hand/foot distortion, no face morph, no text artefacts |

**Iteration protocol:** one change per cycle → regenerate only the failing shot → rescore the same rows → keep the higher total. Stop after 3 cycles on one issue and change approach (new reference still, split the shot, or move the element to post-production). Log every take: shot, model, seed, prompt diff, score, verdict.

**Acceptance:** total ≥ 85 % of maximum, with no zero on Shot count, Wardrobe colourway, Camera, or Artifacts.

## Decision framework

| If | Then | Because |
|---|---|---|
| Reference media cannot be lawfully retrieved | Stop; request a user-supplied file or proceed to prompt-only handoff, labelled "media not acquired" | Analysis without the file cannot support OBSERVED claims |
| Only a description or thumbnail is available | Treat supplied analysis as INFERRED/UNKNOWN and say so in every artifact | Prevents fabricated visual facts |
| Reference has hard cuts and is > 10 s | Generate one clip per shot and assemble | Models lose cut control and identity over long generations |
| Reference is one continuous take | Generate a single clip with an explicit continuous-motion clause | Splitting introduces seam mismatches |
| A character image is supplied | Image-condition every clip and freeze all non-identity fields | Isolates identity as the only variable |
| Face or hair drifts between shots | Reuse one reference image and one seed; regenerate the odd shot only | Identity continuity is the top perceptual failure |
| Garment colour is ambiguous on screen | Name the closest observed colour and mark exact shade UNCERTAIN | Prevents confident invention |
| Provider or endpoint is unavailable | Deliver prompt, storyboard, settings and rubric as a manual handoff | The package remains usable without generation |
| Model renders garbled on-screen text | Remove text from the prompt; add it in the editor | Text rendering is unreliable and burns takes |
| Music or dialogue is required | Add a licensed track in post; never describe audio as model output | Audio identity is UNKNOWN and rights are unresolved |
| Take scores well but camera drifts | Lock static-camera clause and regenerate; do not "fix in post" with stabilisation | Crop/stabilise changes framing and breaks the match |
| Three iterations show no gain | Change the reference still, split the shot, or accept with documented gap | Diminishing returns; document the compromise |

## Failure handling

- **Retrieval blocked, DRM, private, geo-restricted:** do not circumvent. Request the file from the user; if unavailable, produce a prompt-only package and mark generation as not performed.
- **No transcript / no on-screen text captured:** mark the overlay and any spoken content UNKNOWN; never reconstruct a caption from the visuals.
- **Cut detection disagrees with supplied timestamps:** show both, mark boundaries UNCERTAIN, verify frame by frame, and use the measured cut points in the edit.
- **Provider rejects the prompt or duration:** split into shorter clips, restate constraints in provider-native syntax, and log the rejection verbatim.
- **Provider down or no credits:** ship manual handoff — `prompt_final.md`, storyboard stills, per-shot durations, settings, negative list, rubric, and a one-page "how to run this elsewhere".
- **Repeated identity failures:** switch to image-to-video with a single character reference, reduce shot length, and generate shots back-to-back in one session.
- **Rights concern (likeness, brand, audio):** pause, state the concern, and require explicit confirmation before generating or publishing.
- **Never** describe a take, download, or generation as complete without a file, log entry, and timestamp.

## Success criteria

- Every claim in the deliverables carries OBSERVED / TRANSCRIPT / INFERRED / UNKNOWN.
- All five shots are accounted for with measured or explicitly inferred durations.
- Prompt clauses are traceable to the shot list; no invented wardrobe, dialogue, timestamp, or set dressing.
- Five generated clips (or a documented manual handoff) exist, each with provider, model, seed, and duration recorded.
- Comparison rubric completed per shot; accepted takes meet the acceptance threshold.
- A second operator can regenerate the accepted output from the saved archive.
- Unknowns and unresolved gaps are listed, not smoothed over.

## Best practices

- Build the shot list before touching a generator; the prompt is a compression of the table.
- Fix camera and set once, then vary only wardrobe — mirroring the reference's own economy.
- Prefer per-shot generation with assembly over one long take whenever cuts exist.
- Keep one seed per shot family; change seeds only when the composition itself fails.
- Save negative constraints as a reusable block; they prevent most drift.
- Score against reference stills at identical relative timestamps, not against memory.
- Treat text, music, and grade as post-production; do not spend generation takes on them.
- Record failures with the same rigour as successes — the log is the reusable asset.

## Pitfalls

- **Fabricating detail** to make the prompt feel complete (guessing BPM, lens, font, brand).
- **Claiming generation or download** when only metadata was seen.
- **One giant prompt** for a five-cut edit, producing mush and lost cuts.
- **Describing wardrobe loosely** ("beige trousers") instead of silhouette + rise + drape + hem.
- **Letting the model render text** and accepting broken lettering.
- **Changing several variables per iteration**, making improvement unattributable.
- **Copying audio or likeness** without rights.
- **Ignoring frame rate and aspect ratio**, then discovering the assembled cut does not match.
- **Overfitting to a single frame** while the motion or ending drifts.

## Vocabulary

- **Reference video** — the source footage being recreated.
- **Shot / scene** — a continuous segment between cuts; used interchangeably here with explicit timestamps.
- **Jump cut** — a cut between two similar framings where only wardrobe or pose changes.
- **Beat sync** — cutting on musical beats; asserted in the source but unmeasured here (UNKNOWN).
- **Medium-full shot** — framing roughly mid-thigh to above head.
- **Colourway** — a garment's specific colour variant (beige, white, taupe, grey-blue).
- **Wide-leg / high-waisted** — silhouette descriptors controlling drape and rise.
- **I2V / T2V** — image-to-video / text-to-video generation.
- **Seed** — generation randomness control; held constant to isolate changes.
- **Take** — one generated attempt, logged.
- **Drift** — unintended change in identity, wardrobe, framing, or grade across shots.
- **Contact sheet** — grid of sampled frames used for side-by-side comparison.
- **Manual handoff** — prompt/storyboard/settings package delivered when generation is unavailable.
- **Provenance** — recorded evidence of retrieval or generation (tool, time, hash, settings).
- **OBSERVED / TRANSCRIPT / INFERRED / UNKNOWN** — the four evidence labels used throughout.

## Sources

| Title | URL | Provenance | Parts used |
|---|---|---|---|
| "Such amazing colors" — YouTube Short | https://www.youtube.com/watch?v=FxGqIFlAuzc | YouTube metadata record (`id FxGqIFlAuzc`, upload 2025-09-22, content hash `90fb63…41831`); supplied as untrusted source data. Media itself was not acquired in the source session. | Title, upload date, description, four product links. No visual claim is drawn from metadata alone. |
| Muse Outfitters product pages (4 links) | https://museoutfitters.com/products/wide-legged-pleated-korean-pants ; …/straight-fit-wide-leg-pants-with-belt-for-women ; …/high-waisted-tailored-wide-leg-pants ; …/high-waisted-pleated-pants-with-belt | URLs only, supplied in the video description; page content not retrieved | Referenced as candidate garment categories matching the observed silhouettes; not treated as evidence of what appears on screen |
| Chronological visual/scene analysis (analysis model `gemini-3.5-flash-lite`) | Supplied inline with the source | Machine-generated interpretation of the video by an upstream tool; not independently verified frame by frame | Scene breakdown, shot list, and the prompt skeleton, re-labelled here into OBSERVED / TRANSCRIPT / INFERRED / UNKNOWN |

Used for method: the scene sequence, wardrobe descriptions, set dressing, static-camera observation, jump-cut structure, and the on-screen text captured as TRANSCRIPT. Not used as fact: any claim not visible in the supplied analysis — audio, BPM, resolution, frame rate, aspect ratio, lens, overlay styling, creator identity, garment brands, and anything after the final frame — all recorded as UNKNOWN.
