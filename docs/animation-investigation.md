# Investigation: preparing generated characters for rig animation (2026-10-04)

Question: how can a generated 48 px side-view character be prepared automatically for the existing rig/animation
workflow (the AI Sprite Animation package, `com.limpo.ai-sprite-animation`), keeping its pixels, outline, identity and
proportions in every frame? Should we rig the sprite automatically, generate a dedicated rig representation alongside
it, or use another intermediate representation?

The package was **not modified**. All experiments used its own rig engine through `riglab` (the package's standalone
harness that compiles `RigDefinition`/`RigAutoBuilder`/`SpriteRig` outside Unity), on the ten acceptance-test
characters. The experiment scripts and the Phase 2/3 prototype harness live outside this repository
(`C:\AI\riglab-proto` on the development machine) because they depend on the animation package.

## What the package's rig needs

* A side-view sprite (facing left or right).
* **15 joints** in sprite pixels: Neck, HairPivot, Hip, Shoulder/Elbow/Hand near and far, Knee/Foot near and far,
  WeaponPivot/WeaponTip, plus a ground line.
* A **per-pixel part map** (14 parts: Body, Head, Hair, near/far upper and lower arm, Weapon, near/far upper and lower
  leg and foot). `RigAutoBuilder.AssignParts` derives it from the joints alone: above the neck -> head/hair; a column
  below the hip -> legs (duplicated into near and far legs); pixels within 5 % of the height of the arm lines -> arms;
  a protrusion near the hand -> weapon; the rest -> body.
* The renderer moves only original pixels (majority vote at 4x), so identity, palette and the black outline are kept
  by construction. Where an arm leaves the torso it fills the hole from neighbouring body colours (`FillVacatedBody`).
* Already supported input: a **joints file** (`-aiRigJoints <file> -aiRigOnly 1` in batch mode, or the Rig Editor) ->
  `<Sprite>_Rig.asset` -> every preset (Idle, Walk, Run, Attack, Jump, Crouch, CrouchWalk, ...).

## Experiments

### 1. The package's auto-rig, unchanged
Joints from fixed body proportions (neck at 33 %, hip at 65 % of the height).
**Works, but the proportions do not fit generated characters**: their heads are ~19 % of the height, so the head part
swallows the shoulders and upper chest, and the arm parts claim most of the torso; attack frames tear chunks out of
the body ([part maps](images/anim-parts-auto.png), first row per character in
[the attack comparison](images/anim-attack-auto-guide-sdpose.jpg)).

### 2. Joints from the generator's guide
Heights (neck, hip, knee) from the side-view mannequin build each character was generated from; horizontal positions
measured on the sprite. The guide predicts the **global layout** well (head top and feet line match; silhouette IoU
guide vs figure 0.59-0.71, mean 0.67, [overlay](images/anim-guide-overlay.jpg)) but **not the arms**: characters hold
weapons, bows and staffs where the model put them. Head and legs improve; arms stay approximate (second row).

### 3. Joints from a pose estimator (SDPose) on the 1024 px source
SDPose (already installed for the package's AI Pose mode; ComfyUI node `SDPoseKeypointExtractor`) on
`references/source_raw.png`, mapped into sprite pixels with the post-processing record in `metadata.json`.
* **8/8 characters detected, all 18 body keypoints above 0.3 confidence**, about 1 s per image after the model load
  ([keypoints](images/anim-sdpose-keypoints.jpg)); hands are found where the arms really are.
* Near/far limbs: the limb further forward is near (consistent with the guide's stride).
* Result: the most accurate joints; arm parts are thin and follow the real arms
  ([part maps](images/anim-parts-sdpose.png), third row of [the attack comparison](images/anim-attack-auto-guide-sdpose.jpg)).
* **Walk and idle are usable as they are** for strict side views ([walk/idle, five characters](images/anim-sdpose-walk-idle.jpg)):
  legs stride cleanly, arms swing slightly, pixels and outline unchanged.

### 4. Remaining problems (all three joint sources)
* **Hidden torso under the arm.** A single flat sprite has no pixels behind the near arm. When the arm swings
  (attack, run), the vacated area is filled from neighbours, which on outlined sprites are often outline-black: dark
  patches on the torso. This is the package's documented "hidden arms" limitation; generated characters show it more
  because their arms lie on the torso.
* **Far limbs are copies** of the near ones (duplicated leg column, guessed far arm): fine for walking legs, wrong for
  clothing details.
* **Robes and long coats** are cut into two leg columns that scissor ([robed walk](images/anim-hard-cases-walk.jpg),
  top row).
* **Three-quarter views** do not rig (legs and arms tangle; [bottom row](images/anim-hard-cases-walk.jpg)): such
  characters must be re-rolled before rigging (they are ~1 in 10).

## Options considered

| Option | Identity across frames | Automation | Verdict |
|---|---|---|---|
| A. Package auto-rig on the sprite | exact | full | works, wrong proportions; fallback only |
| **B. Generator exports a rig hint (joints from SDPose, guide fallback) -> package rig** | exact | full | **recommended now**; no package change |
| **C. Generator also exports a part map + reconstructed hidden pixels (layered character)** | exact for visible pixels; hidden pixels synthesised once, palette-snapped | full | **recommended next**; needs an additive package import |
| D. Generate every frame with the image model (per-frame guided img2img, AnimateDiff) | approximate: the package measured 10-30 % pixel survival; SDXL at denoise 0.92 redraws faces/clothes per frame | full | rejected for game sprites (flicker, palette drift) |
| E. Generate separate limb images | inconsistent parts, no reliable occlusion order | partial | rejected |
| F. Mesh skinning (Unity 2D Animation) | deforms/resamples pixels (rotated texels at 48 px) | needs layered art | rejected; the package's pixel-exact renderer is better |
| G. AI Pose + Rig (package beta) for the motion | exact (same renderer) | needs a rig first | complementary, after B/C |

## Final approach (decided 2026-10-04)

**The generator produces a dedicated rig representation next to every approved character; the animation project keeps
its pixel-exact rig renderer and consumes that data.** No animation frame is ever generated by the image model.

```
Local Character Generator (this repository)            Animation project (e.g. AI Sprite Animation package)
  character.png + character.json + metadata.json   ->   rig asset from joints + part map (+ underlay)
  rig/joints.txt, rig/parts.png, rig/underlay.png,       pixel-exact rendering: idle, walk, run, attack, ...
  rig/rig.json (format chargen-rig/1)                     identity = the sprite's own pixels in every frame
```

| Phase | Where | Status |
|---|---|---|
| 1. Joints, part map, hidden-pixel underlay, robe hints | this generator (`character-generator rig`) | **done** (v0.3.0); format in [rig-data.md](rig-data.md) |
| 2. Import the part map | animation package, additive, backward compatible | **done** (package 1.7.0; generator 0.4.0 follow-ups) |
| 3. Draw the underlay under the arms | animation package, additive, backward compatible | **done** (package 1.7.0; generator 0.4.0 follow-ups) |
| 4. Robes: small leg swing from `animation_hints.leg_swing_scale` | animation package, additive | **done** (package 1.7.0; generator 0.4.0 follow-ups) |

### What Phase 1 changed compared with the first plan
* **Near/far side**: "the limb further forward is near" was wrong for hanging arms (SDPose guesses the hidden arm in
  front). The near side is now the side SDPose sees best (higher confidence of shoulder, elbow, wrist and ear); near arm
  and near leg are on the same body side. Correct on all 12 characters.
* **Three-quarter detection** by shoulder spread was dropped: the hidden shoulder is a guess, so the spread flagged
  strict side views. Three-quarter characters are re-rolled by review (about 1 in 10).
* **Hidden pixels**: plain inpainting at denoise 0.75-0.9 redrew the arm. Working recipe: pre-fill the arm area from
  the surrounding torso colours (background outside the torso), inpaint at **denoise 0.55** with weighted negatives
  against arms/hands, restrict to the torso silhouette (per-row span of the non-arm body pixels), and allow only
  colours that occur on the visible torso. Reproducible byte for byte from the recipe (`rig --verify`).
* **Weapons held by the far hand** (a katana carried behind) move with the far arm; legs take only pixels near the legs.

## Prototype: does the hidden-pixel layer solve the arm/torso artifact?

Before changing the package, its renderer was copied into a throw-away harness with the two additive inputs (part
map import, underlay drawn as Body pixels beneath the arms) and an optional leg-swing scale. Representative characters:
normal humanoid (Merchant), weapon-holding (Ninja with katana, Knight with sword and shield), robed (Necromancer),
stocky (a strict side-view Dwarf generated for this, `DwarfSide`). Three variants each:

1. **today**: what the package does now with Phase 1 joints alone (its own geometric part guess, no underlay);
2. **+ part map**: the generated `parts.png`;
3. **+ part map + underlay**: also `underlay.png` (robed: also `leg_swing_scale` 0.3).

| | Result |
|---|---|
| [Ninja attack](images/proto-Ninja-attack.png), [Ninja run](images/proto-Ninja-run.png) | today: the swing tears the torso, sword pieces scatter. Part map: the arm moves as one piece, a dark smear stays where it was. Underlay: the sash and tunic continue under the arm. |
| [Knight attack](images/proto-KnightWoman-attack.png) | underlay: armour continues under the arm instead of a dark patch (her "weapon" is a cape strip the weapon search picked up) |
| [Merchant attack](images/proto-Merchant-attack.png) | first underlay kept part of the hand (a ghost arm); the torso-colour rule removed it: shirt and apron continue |
| [Dwarf attack](images/proto-DwarfSide-attack.png) | part map alone leaves a skin-coloured hole on the back; the underlay fills it with leather/armour |
| [Necromancer walk](images/proto-Necromancer-walk.png) | today: the robe splits into two scissoring halves. Robe as Body + full leg swing: feet swing out from under the robe. **Robe as Body + leg swing x0.3: one continuous robe, the feet step subtly underneath** |

Interior dark-pixel count (outline-black pixels appearing inside the body, summed over the frames, minus the rest
pose; noisy because a moving arm's own outline also counts):

| Character / animation | today | + part map | + part map + underlay |
|---|---|---|---|
| Ninja attack / run | 174 / 195 | 58 / 64 | **33 / 19** |
| Knight attack / run | 132 / 171 | 67 / 103 | **58 / 94** |
| Dwarf attack / run | 245 / 321 | 76 / 135 | **73 / 106** |
| Necromancer attack / walk | 325 / 308 | 161 / 76 | 128 / 65 (x0.3 legs: 72 / 114) |
| Merchant attack | 127 | 139 | 104 |

Conclusion: the part map removes most tearing; the underlay removes the remaining holes where the arm was. Both
are worth integrating. Residual issues: misdetected "weapons" (cape strips), some reconstructed pixels look flatter
than the original torso, and the far arm has no hidden pixels (it is mostly behind the body anyway).

## Phase 2/3 integration (AI Sprite Animation package 1.7.0)

What was built (all in the package, additive; the generator did not change):

| Piece | How |
|---|---|
| Import | new `ChargenRigImporter`: reads `rig/rig.json` (checks format `chargen-rig/1`, sprite size, sprite sha256 and the part-bit names), joints, `ground_y`, `parts.png`, `underlay.png` and `animation_hints.leg_swing_scale` straight from the files, and writes them into the sprite's ordinary rig asset (`character_Rig.asset`) |
| When | automatically when a sprite has **no rig asset** and `rig/rig.json` lies next to it (Generate Animation / Generate Poses); explicitly via *Assets > AI > Import Generated Rig Data*, a Rig Editor button, or batch `-aiRigImport <rig.json\|folder\|auto> [-aiRigOnly 1]`. Data that does not fit is refused with a warning and the old path (auto rig / dialog) runs |
| Part map | copied 1:1 into the rig's existing per-pixel part mask (what the Rig Editor paints) |
| Underlay | stored on the rig (`underlayBytes`, empty = none); `SpriteRig.Render` draws those pixels as extra Body pixels with the underlay colours, beneath the arms: exactly the prototype |
| Robes | `legSwingScale` on the rig (default 1); `SpriteRig.ApplyLegSwingScale` multiplies the six leg angles of the generated poses (procedural and AI poses, and the Rig Editor previews). Applied to the poses, not inside the renderer, so pose fitting/kinematics are unaffected |

Differences from the plan: no separate `-aiRigParts` switch (one import of `rig.json` sets everything); the underlay is
stored as bytes on the rig asset, not as a texture reference (the rig stays one self-contained asset, like the mask);
the leg scale applies to every animation, so Jump/Sit/Crouch also fold a robe less.

### Validation

Harness: the package's rig sources at git HEAD and in the working tree compiled side by side outside Unity, plus Unity
batch runs on a throw-away copy of the game with the HEAD package and with the new one.

**Existing characters (no new data) - unchanged.** Old vs new renderer, every procedural animation (Idle, Walk, Run,
Attack, Jump, Sit, Crouch, CrouchWalk, Rotate; 84 frames), both facings: byte-identical for the player with its
hand-made rig asset, the player with an auto-built rig, the riglab example rig, and all 12 generated characters with
their rig data ignored (joints only). Fallback values are identical too: leg scale 1, 0 (an asset saved before 1.7.0),
-2 (invalid) and an all-transparent underlay. The package's `tools/posetests` pass unchanged.
In Unity (copy of the game, HEAD package vs 1.7.0): the player's Idle, Walk, Run, Attack, Jump, Sit, Crouch,
CrouchWalk and Rotate, a sprite without a rig (auto-built) and a generated character with its `rig/` folder removed
produce byte-identical frames and clips (146 files). A rig asset saved by 1.6.0 loads with leg scale 1 and no underlay.

**Import in Unity:** for all 12 generated characters the imported part map and underlay equal the PNGs pixel for pixel
(leg scale 0.3 for the two robed ones); with no rig asset, *Generate Animation* imported the data automatically for
six characters (Idle/Walk/Run/Attack all succeeded) and the saved asset reloads with the data; a sprite changed after
the rig step is refused (sha256) and generation falls back to the auto-built rig; importing over an existing rig keeps
its GUID. The 204 frames Unity produced for the six characters equal the harness renders used below.

**Port fidelity.** With the generated data the package renders byte-identically to the approved prototype (six
characters, Idle/Walk/Run/Attack).

**Generated characters with the data** (facing right as generated; sums over the frames):

| Character | Kind | exposed outline pixels walk / run / attack: joints only -> all data | small tears (enclosed gaps <= 4 px, all 84 frames): joints only -> all data |
|---|---|---|---|
| Merchant | normal humanoid | 237 / 275 / 377 -> **119 / 180 / 173** | 11 -> 9 |
| KnightWoman | sword | 278 / 331 / 342 -> **241 / 276 / 308** | 4 -> 5 |
| Ninja | katana + far-hand item | 411 / 468 / 572 -> **278 / 255 / 387** | 10 -> 15 |
| Necromancer | robed | 324 / 361 / 365 -> **130 / 124 / 106** | 12 -> 3 |
| OldWizard | robed + staff | 280 / 329 / 369 -> **142 / 217 / 143** | 11 -> 22 |
| DwarfSide | stocky + axe | 326 / 417 / 365 -> **120 / 130 / 160** | 16 -> 21 |

* **No unexpected pixel changes:** every frame uses only colours of the sprite; the rest pose renders exactly as the
  same rig without the new data (the renderer's existing seam fill touches the same 1-4 pixels either way).
* **Arms and weapons** ([attacks: joints only vs all data](images/phase2-attacks.png)): the near arm now moves as one
  piece with its weapon (Knight's sword arcs, the dwarf's axe and the Merchant's arm extend); with joints only the
  weapon barely moved and the torso tore. Weapons sit on the near arm; far-hand items (Ninja, Dwarf) move with the far arm.
* **Hidden torso pixels:** the torso continues under the swinging arm instead of a hole or outline (the column above).
* **Robes, no scissoring** ([Necromancer walk](images/phase2-Necromancer-walk.png),
  [OldWizard walk](images/phase2-OldWizard-walk.png)): with `leg_swing_scale` 0.3 the robe stays one silhouette and
  the feet shuffle underneath. Lower-body rows split into two runs beyond the rest pose, walk / run: Necromancer
  29 / 35 (part map, full swing) -> 6 / 16; OldWizard 44 / 47 -> 13 / 14.
* **Gaps:** larger enclosed transparent areas are negative space (legs apart with touching feet, a staff swung away
  from the robe), present with joints only too. Small tears stay in the range the renderer already had; the OldWizard
  and Dwarf have a few more (1-2 px between a staff/axe shaft and the arm).

### Follow-up fixes (generator 0.4.0)

The first validation found pieces that broke off during animation and a shared output folder. Both were fixed in the
generator; the package did not change.

**Pieces that broke off.** Counting floating pieces (opaque 8-connected components apart from the figure, rendered
with the underlay, as the game sees it) over Idle, Walk, Run, Attack, Jump, Sit, Crouch and CrouchWalk (66 frames per
character, facing right):

| Character | 0.3.0 rig data | 0.4.0 | cause | rule that fixed it |
|---|---|---|---|---|
| Necromancer | 50 held item | **0** | item split from the hand and into two pieces by 2-3 Body pixels on the 48 px grid | bridge gaps of up to 3 px; edge contact only |
| KnightWoman | 17 held item | **0** | a cape strip (taken as the weapon) 1 px from the hand, the gap labelled leg | bridges may cross leg pixels |
| Barbarian | 6 held item | **0** | axe handle crossing the thigh: 1 leg pixel between hand and blade | bridges may cross leg pixels |
| ElfArcher | 4 held item (Jump 3-6) | **0** | Jump counter-rotates the bow around a pivot 1 px off the grip | WeaponPivot = centre of the hand/item contact |
| DwarfSide | 2 held item | **0** | a 7 px item piece touching only the upper arm | joins the upper arm |
| Ninja | 10 far arm | **0** | the far-hand item hangs from a hand hidden behind the legs: no visible arm | far piece without visible upper arm -> Body |
| SkeletonWarrior | 14 far arm | **0** | same | same |
| DwarfWarrior | 8 far arm + 2 Body | **0** | two forearm pixels up at the shoulder; one underlay pixel behind the axe touching no visible body | stray arm fragment -> neighbour label; detached underlay dropped |
| Merchant, GoblinThief, OldWizard, Rogue | 0 | 0 | | |
| Necromancer | 2 legs/feet (Run 6-7) | 2 | see below | not a data problem |

The rules (generic, no per-character cases) are in [rig-data.md](rig-data.md#how-the-parts-are-decided), step 8;
regression tests use the 0.3.0 part maps of the Necromancer, Ninja, DwarfWarrior and ElfArcher as fixtures
(`tests/fixtures/rig/`). Moving the weapon pivot to the grip changed the item's position by 0.2-0.9 px on average per
frame (at most 2.7 px) and created no new floating pieces; hand and item touch in the same frames as before.
Rebuilding needed no GPU (`rig --from-saved`); the sprites are unchanged. The four characters the rules do not touch
(ElfArcher, GoblinThief, Merchant, OldWizard) keep byte-identical part maps and underlays; their only change is the
weapon pivot (and tip) moving to the grip. Elsewhere only the relabelled pixels changed (2-45 per character), and the
underlay only at those pixels. In Unity all 352 frames generated for eight
characters (Idle/Walk/Run/Attack/Jump) equal the harness renders, and only the Necromancer's Run 6-7 contain a
floating piece. Quality numbers are within noise of the first validation (exposed outline pixels walk / run / attack:
Necromancer 146 / 147 / 114, Ninja 292 / 277 / 378, DwarfSide 85 / 133 / 135, all far below "joints only").

**Unique output names.** Every sprite is now `<Name>.png` (deterministic: the character name, `A-Z a-z 0-9 _ -`),
so the package's output `Assets/Animations/Generated/<sprite name>/` is unique per character; names that differ only
in letter case are refused. `migrate-names` renamed the 11 existing sprites (and DwarfSide) with their `.meta` files,
so Unity kept their GUIDs; pixels are unchanged. In Unity: eight characters x five animations went to 40 distinct
folders, nothing to `Generated/character/`, and regenerating Ninja/Walk left Necromancer/Walk untouched (same files,
bytes and write times) while reproducing its own frames byte for byte.

### Robed feet in Run (package 1.7.0: smaller Run lean for robed rigs)

In Run frames 6-7 the Necromancer's front foot (32-35 px) stepped out from under the hem. Switching off either the
torso lean or the thigh swing made it disappear: Run leans the torso 11 degrees (Walk 2), and the renderer rotates the
whole Body, which for a robed character includes the robe down to the hem, about the hip, so the hem swung about
3.5 px back while the front foot stepped about 4-5 px forward (leg swing x0.3). The data is as intended (robe = Body,
feet = legs, leg swing 0.3), so this was fixed in the package, generically: a rig with leg swing scale < 1 leans its
torso half as much in Run (5.5 degrees), with head and upper arms counter-rotated so their world angles stay as
posed. `leg_swing_scale` stays 0.3; Walk, Attack and all other animations, and every rig with scale 1, are
byte-identical to before (checked for all 12 generated characters and the existing characters; only the Necromancer's
and OldWizard's Run changed).

| Run, facing right | floating pieces per frame | weakest foot-to-robe contact (edge pixels), frames 0-7 |
|---|---|---|
| Necromancer, lean 11 | 0 0 0 0 0 0 **1 1** | 21 13 7 9 9 17 **0 0** |
| Necromancer, lean 5.5 | 0 0 0 0 0 0 0 0 | 20 14 9 11 12 16 1 3 |
| Necromancer, lean 0 (for reference) | 0 0 0 0 0 0 0 0 | 15 12 10 12 13 15 3 3 |
| OldWizard, lean 11 / 5.5 | 0 everywhere | 12 11 11 3 2 11 11 13 / 12 12 14 4 2 10 13 14 |

At 5.5 degrees nothing floats and the OldWizard is unaffected; in frames 6-7 the Necromancer's front foot joins the hem
by only 1-3 pixels. Leaning less does not improve that (3 pixels even at 0 degrees): it comes from the 0.3 leg swing
itself, the foot stepping ahead of the hem's front edge, which reads as a foot showing under a running robe. In Unity,
the 352 frames of eight characters (Idle/Walk/Run/Attack/Jump) have no floating piece.

Also: robed characters' Jump/Sit/Crouch fold the robe less (the leg scale applies to every animation).
