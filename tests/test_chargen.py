"""Offline tests (no ComfyUI/GPU). Run: character-generator's Python -m unittest discover -s tests"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chargen import pixelate, prompt_builder, spec as specmod, workflow  # noqa: E402
from chargen.config import load_models, load_profiles  # noqa: E402

ROGUE = ("Young female rogue, short dark brown hair, dark green hood, lightweight leather armor, small dagger, "
         "slim athletic body, confident expression, medieval fantasy game character.")


def fake_character(bg=(170, 170, 170), shadow=True, figures=1, size=1024):
    """A blocky 'character' on a plain background, optionally with a grey floor shadow."""
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    for i in range(figures):
        cx = size // 2 if figures == 1 else int(size * (i + 1) / (figures + 1))
        if shadow:
            d.ellipse([cx - 120, 880, cx + 120, 930], fill=(120, 120, 120))
        d.rectangle([cx - 60, 200, cx + 60, 600], fill=(30, 90, 40))     # body (green)
        d.rectangle([cx - 40, 100, cx + 40, 200], fill=(230, 180, 150))  # head
        d.rectangle([cx - 55, 600, cx - 10, 900], fill=(110, 60, 30))    # legs (brown)
        d.rectangle([cx + 10, 600, cx + 55, 900], fill=(110, 60, 30))
        d.rectangle([cx - 64, 196, cx + 64, 204], fill=(10, 10, 10))     # dark line
    return img


class SpecTests(unittest.TestCase):
    def test_parse_rogue(self):
        s = specmod.parse_description(ROGUE)
        self.assertEqual(s["name"], "Rogue")
        self.assertEqual((s["gender"], s["age"], s["role"]), ("female", "young", "rogue"))
        self.assertEqual(s["hair"], "short dark brown hair")
        self.assertEqual(s["clothing"], ["dark green hood"])
        self.assertEqual(s["armor"], ["lightweight leather armor"])
        self.assertEqual(s["weapons"], ["small dagger"])
        self.assertEqual(s["body_type"], "slim athletic")
        self.assertEqual(s["expression"], "confident")
        self.assertEqual(s["setting"], "medieval fantasy")
        self.assertEqual(s["details"], [])

    def test_explicit_name_and_defaults(self):
        s = specmod.normalize(specmod.parse_description("old dwarf blacksmith, big hammer", name="Borin"))
        self.assertEqual(s["name"], "Borin")
        self.assertEqual(s["species"], "dwarf")
        self.assertEqual(s["view"], "side")
        self.assertEqual(s["weapons"], ["big hammer"])

    def test_bad_view_rejected(self):
        with self.assertRaises(ValueError):
            specmod.normalize({"name": "x", "view": "top"})

    def test_safe_name(self):
        self.assertEqual(specmod.safe_name("Dark Knight!"), "Dark_Knight")
        with self.assertRaises(ValueError):
            specmod.safe_name("!!!")


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.profiles = load_profiles()
        self.spec = specmod.normalize(specmod.parse_description(ROGUE))

    def test_tags_prompt_order_and_content(self):
        pos, neg = prompt_builder.build(self.spec, self.profiles["quality"])
        self.assertTrue(pos.startswith("pixel art, 16-bit pixel art video game character"))
        for part in ["young female human rogue", "(side view:1.4)", "facing right", "short dark brown hair",
                     "wearing dark green hood", "holding (small dagger:1.3)", "plain white background"]:
            self.assertIn(part, pos)
        self.assertLess(pos.index("rogue"), pos.index("hair"))
        self.assertIn("sprite sheet", neg)
        self.assertIn("back view", neg)
        self.assertNotIn("{", pos + neg)

    def test_empty_fields_leave_no_dangling_words(self):
        pos, _ = prompt_builder.build(specmod.normalize({"name": "Blob", "role": "slime"}), self.profiles["quality"])
        self.assertNotIn("holding ,", pos)
        self.assertNotIn("wearing", pos)
        self.assertNotIn(", ,", pos)

    def test_avoid_goes_to_negative(self):
        spec = dict(self.spec, avoid=["long sword"])
        pos, neg = prompt_builder.build(specmod.normalize(spec), self.profiles["quality"])
        self.assertIn("long sword", neg)
        self.assertNotIn("long sword", pos)

    def test_natural_prompt(self):
        pos, neg = prompt_builder.build(self.spec, self.profiles["krea2"])
        self.assertIn("She has short dark brown hair.", pos)
        self.assertEqual(neg, "")

    def test_deterministic(self):
        a = prompt_builder.build(self.spec, self.profiles["quality"])
        b = prompt_builder.build(json.loads(json.dumps(self.spec)), self.profiles["quality"])
        self.assertEqual(a, b)


class ConfigTests(unittest.TestCase):
    def test_standalone_without_project(self):
        import os
        from chargen.config import TOOL_ROOT, load_config
        old = os.environ.pop("CHARGEN_PROJECT", None)
        try:
            cfg = load_config()
        finally:
            if old is not None:
                os.environ["CHARGEN_PROJECT"] = old
        self.assertIsNone(cfg.project_file)
        self.assertEqual(cfg.character_dir("Rogue"), TOOL_ROOT / "output" / "Rogue")

    def test_project_file_sets_output_and_defaults(self):
        import tempfile
        from chargen.config import PROJECT_FILE_NAME, load_config
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, PROJECT_FILE_NAME).write_text(json.dumps(
                {"name": "Demo", "output_root": "Sprites/Chars", "reference_sprite": "hero.png",
                 "defaults": {"size": 64, "colors": 16}}), encoding="utf-8")
            cfg = load_config(tmp)                       # a folder works, so does the file itself
            self.assertEqual(cfg.project_name, "Demo")
            self.assertEqual(cfg.character_dir("A"), Path(tmp).resolve() / "Sprites/Chars" / "A")
            self.assertEqual(cfg.reference_sprite, Path(tmp).resolve() / "hero.png")
            self.assertEqual((cfg.defaults["size"], cfg.defaults["colors"]), (64, 16))
            self.assertEqual(load_config(str(Path(tmp, PROJECT_FILE_NAME))).project_name, "Demo")
        with self.assertRaises(FileNotFoundError):
            load_config(tmp)                             # deleted folder


class RecipeTests(unittest.TestCase):
    def test_stored_prompt_wins_and_falls_back_to_metadata(self):
        import tempfile
        from chargen.generator import stored_prompt
        recipe = {"prompt": {"positive": "p", "negative": "n"}}
        self.assertEqual(stored_prompt(recipe, "."), {"positive": "p", "negative": "n"})
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "metadata.json").write_text(json.dumps({"generated_prompt": "old p", "negative_prompt": "old n"}))
            self.assertEqual(stored_prompt({}, tmp), {"positive": "old p", "negative": "old n"})
            self.assertIsNone(stored_prompt({}, Path(tmp, "missing")))


class GuideTests(unittest.TestCase):
    def test_build_choice(self):
        from chargen import guide
        cases = {"stocky": {"species": "dwarf"}, "small": {"species": "goblin"},
                 "broad": {"body_type": "huge muscular"}, "slim": {"body_type": "slender", "species": "elf"},
                 "robed": {"body_type": "thin", "clothing": ["long blue robe"]}, "normal": {"body_type": "athletic"}}
        for expected, spec in cases.items():
            self.assertEqual(guide.choose_build(spec), expected, spec)
        self.assertEqual(guide.choose_build({"species": "dwarf", "guide_build": "slim"}), "slim")

    def test_guide_is_deterministic(self):
        from chargen import guide
        a = guide.draw_guide("normal").tobytes()
        self.assertEqual(a, guide.draw_guide("normal").tobytes())
        self.assertNotEqual(a, guide.draw_guide("broad").tobytes())

    def test_player_proportions_option(self):
        from chargen.config import load_config
        from chargen.generator import build_request
        cfg = load_config()
        player = build_request(cfg, {"name": "A"}, "quality", seed=1, proportions="player")
        realistic = build_request(cfg, {"name": "A"}, "quality", seed=1)
        self.assertEqual(player["generation"]["guide_head_scale"], 1.45)
        self.assertIn("big head", player["spec"]["proportions"])
        self.assertNotIn("guide_head_scale", realistic["generation"])
        from chargen import guide
        self.assertNotEqual(guide.draw_guide("normal", head_scale=1.45).tobytes(), guide.draw_guide("normal").tobytes())

    def test_request_uses_guide_only_for_side_view(self):
        from chargen.config import load_config
        from chargen.generator import build_request
        cfg = load_config()
        side = build_request(cfg, {"name": "A", "species": "dwarf"}, "quality", seed=1)
        front = build_request(cfg, {"name": "A", "species": "dwarf", "view": "front"}, "quality", seed=1)
        off = build_request(cfg, {"name": "A"}, "quality", seed=1, guide=False)
        nova = build_request(cfg, {"name": "A"}, "nova", seed=1)
        self.assertEqual(side["generation"]["guide"], "stocky")
        self.assertIsNone(front["generation"]["guide"])
        self.assertIsNone(off["generation"]["guide"])
        self.assertIsNone(nova["generation"]["guide"])


def fake_side_figure():
    """A right-facing figure on a plain background with an arm in front of the torso, plus matching keypoints."""
    img = Image.new("RGB", (1024, 1024), (240, 240, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([450, 120, 570, 260], fill=(230, 180, 150))      # head (face right)
    d.rectangle([440, 120, 470, 230], fill=(90, 50, 20))         # hair at the back
    d.rectangle([440, 260, 580, 560], fill=(30, 90, 140))        # torso (blue)
    d.rectangle([520, 280, 560, 540], fill=(200, 60, 60))        # near arm hanging in front (red)
    d.rectangle([450, 560, 500, 880], fill=(60, 60, 60))         # far leg
    d.rectangle([520, 560, 570, 880], fill=(70, 70, 70))         # near leg
    d.rectangle([520, 880, 610, 910], fill=(110, 60, 30))        # near foot
    d.rectangle([450, 880, 540, 910], fill=(100, 55, 30))        # far foot
    k = [(0.0, 0.0, 0.0)] * 18
    def put(i, x, y, c):
        k[i] = (float(x), float(y), c)
    put(0, 560, 200, .95); put(1, 510, 265, .9)
    put(2, 540, 290, .9); put(3, 540, 410, .9); put(4, 540, 530, .9)      # right = near (visible), high confidence
    put(5, 480, 290, .5); put(6, 470, 410, .4); put(7, 470, 520, .4)      # left = far (hidden), low confidence
    put(8, 545, 560, .9); put(9, 545, 720, .9); put(10, 545, 870, .9)
    put(11, 475, 560, .6); put(12, 475, 720, .6); put(13, 475, 870, .6)
    put(14, 555, 190, .9); put(15, 545, 190, .9); put(16, 500, 200, .9); put(17, 510, 200, .4)
    return img, k


class RigTests(unittest.TestCase):
    def setUp(self):
        from chargen.config import load_config
        self.cfg = load_config()
        self.gen = {"size": 48, "colors": 16, "outline": True, "char_height": 45, "feet_margin": 1,
                    "outline_color": "black", "mirror": False, "guide": "normal"}

    def test_parts_encoding_round_trip(self):
        import tempfile
        from chargen import rig
        parts = np.array([[0, 1, 2], [256 | 8, 2048 | 256, 4]], dtype=np.uint16)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "parts.png")
            Image.fromarray(rig.encode_parts(parts, parts > 0), "RGBA").save(path)
            self.assertTrue((rig.decode_parts(path) == parts).all())

    def test_near_side_is_the_confident_side(self):
        from chargen import rig
        _, k = fake_side_figure()
        sk = rig.Skeleton(k, 1, 0.2)
        self.assertEqual(sk.near_arm_ids[0], rig.RSH)
        self.assertEqual(sk.near_leg_ids[0], rig.RHIP)

    def test_labels_and_layers_line_up_with_an_unchanged_sprite(self):
        import io
        from chargen import rig
        from chargen.generator import postprocess
        img, k = fake_side_figure()
        labels, cover, mask, sk, decisions, fg, torso = rig.analyse(self.cfg, img, k, {"clothing": []}, self.gen)
        B = rig.BIT
        self.assertTrue(labels[400, 540] & (B["ArmNearUpper"] | B["ArmNearLower"]))   # the red arm
        self.assertEqual(labels[200, 540], B["Head"])
        self.assertEqual(labels[200, 445], B["Hair"])
        self.assertEqual(labels[400, 460], B["Body"])                                 # torso behind
        self.assertTrue(labels[800, 545] & (B["LegNearLower"] | B["LegNearUpper"]))
        self.assertTrue(cover[400, 540])
        self.assertFalse(decisions["robed"])
        buf = io.BytesIO(); img.save(buf, format="PNG"); raw = buf.getvalue()
        plain, _ = postprocess(raw, self.gen)
        sprite, info = postprocess(raw, self.gen, layers={"parts": {"kind": "labels", "data": labels}})
        self.assertEqual(np.asarray(plain).tobytes(), np.asarray(sprite).tobytes())   # sprite unchanged
        parts = info["layers"]["parts"]
        alpha = np.asarray(sprite)[..., 3] > 0
        self.assertTrue((parts[alpha] > 0).all())                                     # every pixel has a part
        self.assertTrue((parts[~alpha] == 0).all())

    def test_robe_stays_body_except_the_feet(self):
        from chargen import rig
        img, k = fake_side_figure()
        labels, *_ = rig.analyse(self.cfg, img, k, {"clothing": ["long purple robe"]}, self.gen)
        self.assertEqual(labels[650, 545], rig.BIT["Body"])                           # leg area = robe = body
        self.assertTrue(labels[895, 560] & (rig.BIT["FootNear"] | rig.BIT["FootFar"] | rig.BIT["LegNearLower"] | rig.BIT["LegFarLower"]))

    def test_underlay_uses_only_torso_colours(self):
        from chargen import rig
        parts = np.full((4, 4), rig.BIT["Body"], dtype=np.uint16)
        parts[1:3, 1:3] = rig.BIT["ArmNearLower"]
        sprite = np.zeros((4, 4, 4), dtype=np.uint8); sprite[...] = (10, 20, 30, 255)
        sprite[1:3, 1:3] = (200, 150, 120, 255)                                       # the arm (skin)
        under = np.zeros_like(sprite); under[1:3, 1:3] = (201, 151, 121, 255)          # a kept hand colour
        out, n = rig.torso_colours_only(under, sprite, parts)
        self.assertEqual(n, 4)
        self.assertTrue((out[1:3, 1:3, :3] == (10, 20, 30)).all())


class HeldItemTests(unittest.TestCase):
    """Held items stay attached to the hand that moves them (sprite-grid labels, attach_held_items)."""
    FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rig"

    @staticmethod
    def grid(rows):
        from chargen import rig
        key = {".": 0, "B": rig.BIT["Body"], "U": rig.BIT["ArmNearUpper"], "L": rig.BIT["ArmNearLower"],
               "W": rig.BIT["Weapon"], "u": rig.BIT["ArmFarUpper"], "l": rig.BIT["ArmFarLower"]}
        return np.array([[key[c] for c in r] for r in rows], dtype=np.uint16)

    def test_gap_between_hand_and_item_is_bridged(self):
        from chargen import rig
        parts = self.grid(["BBBBBBB",
                           "BLLBBWW",     # two Body pixels between the hand and the item
                           "BLLBBWW",
                           "BBBBBBB"])
        self.assertFalse(rig.held_items_attached(parts))
        out, rep = rig.attach_held_items(parts, 3)
        self.assertTrue(rig.held_items_attached(out))
        self.assertEqual(rep["weapon_pieces_reattached"], 1)
        self.assertEqual(rep["bridged_pixels"], 2)
        self.assertEqual(int((out != parts).sum()), 2)                     # only the bridge changes
        self.assertTrue(((out == rig.BIT["Weapon"]) == (parts == rig.BIT["Weapon"])).all())
        self.assertTrue((out[out != parts] == rig.BIT["ArmNearLower"]).all())   # a bridge to the hand moves with the hand

    def test_item_split_in_pieces_is_joined_and_stray_pieces_drop_to_body(self):
        from chargen import rig
        parts = self.grid(["LLWWB",
                           "BBBWB",
                           "BBBBB",         # one Body row splits the item
                           "BBBWB",
                           "BBBWW",
                           "BBBBB", "BBBBB", "BBBBB", "BBBBB", "BBBBB", "BBBBB",
                           "WBBBB"])        # a stray piece far from everything: becomes Body, never floats
        out, rep = rig.attach_held_items(parts, 3)
        self.assertTrue(rig.held_items_attached(out))
        self.assertEqual(int((out[2] == rig.BIT["Weapon"]).sum()), 1)     # one joint pixel, moving with the item
        self.assertEqual(out[11, 0], rig.BIT["Body"])
        self.assertEqual(rep["weapon_pieces_to_body"], 1)

    def test_far_hand_piece_without_visible_arm_moves_with_the_body(self):
        from chargen import rig
        parts = self.grid(["BBBu.",
                           "BBBl.",         # far arm visible from the shoulder: stays
                           "BBBB.",
                           "BBBBl",         # a far-hand item with no visible arm: Body
                           "BBBBl"])
        out, rep = rig.attach_held_items(parts, 3)
        self.assertEqual(rep["far_pieces_to_body"], 1)
        self.assertEqual(out[1, 3], rig.BIT["ArmFarLower"])
        self.assertEqual(out[3, 4], rig.BIT["Body"])
        self.assertTrue(rig.held_items_attached(out))

    def test_attached_items_are_left_alone(self):
        from chargen import rig
        parts = self.grid(["BUUB.",
                           "BLLWW",
                           "BBBWW"])
        out, rep = rig.attach_held_items(parts, 3)
        self.assertTrue((out == parts).all())
        self.assertEqual(rep["bridged_pixels"] + rep["weapon_pieces_to_body"] + rep["far_pieces_to_body"], 0)

    def _real_case(self, name):
        from chargen import rig
        before = rig.decode_parts(self.FIXTURES / f"{name}_parts_v0.3.png")     # the part map 0.3.0 wrote
        after, rep = rig.attach_held_items(before, 3)
        self.assertFalse(rig.held_items_attached(before))                       # the bug: the item was not attached
        self.assertTrue(rig.held_items_attached(after))
        self.assertTrue(((after > 0) == (before > 0)).all())                     # same silhouette, every pixel labelled
        moving = (rig.BIT["ArmNearUpper"] | rig.BIT["ArmNearLower"] | rig.BIT["Weapon"] | rig.BIT["ArmFarUpper"]
                  | rig.BIT["ArmFarLower"])
        changed = after != before
        self.assertTrue((((before[changed] & moving) > 0) | (before[changed] == rig.BIT["Body"])).all())
        again, _ = rig.attach_held_items(after, 3)
        self.assertTrue((again == after).all())                                  # idempotent
        return before, after, rep

    def test_necromancer_hand_item_reattached(self):
        from chargen import rig
        before, after, rep = self._real_case("Necromancer")
        self.assertEqual(rep["weapon_pieces"], 2)
        self.assertEqual(rep["weapon_pieces_reattached"], 2)                      # both pieces of the hanging item
        self.assertLessEqual(rep["bridged_pixels"], 3)                            # a 2-3 pixel gap, nothing more
        self.assertEqual(rep["weapon_pieces_to_body"], 0)
        self.assertTrue(((before == rig.BIT["Weapon"]) <= (after == rig.BIT["Weapon"])).all())   # no item pixel lost

    def test_ninja_far_hand_item_no_longer_floats(self):
        from chargen import rig
        before, after, rep = self._real_case("Ninja")
        self.assertEqual(rep["far_pieces_to_body"], 1)
        self.assertEqual(int(((after & (rig.BIT["ArmFarUpper"] | rig.BIT["ArmFarLower"])) > 0).sum()), 0)
        self.assertTrue(((after == rig.BIT["Weapon"]) == (before == rig.BIT["Weapon"])).all())   # the katana was attached: unchanged


    def test_dwarfwarrior_stray_forearm_pixels_join_the_part_they_sit_on(self):
        from chargen import rig
        before = rig.decode_parts(self.FIXTURES / "DwarfWarrior_parts_v0.3.png")    # two forearm pixels up at the shoulder
        self.assertEqual(len(rig._pieces(before == rig.BIT["ArmFarLower"], rig._N4)), 2)
        after, rep = rig.attach_held_items(before, 3)
        self.assertEqual(rep["arm_fragments_relabelled"], 1)
        self.assertEqual(len(rig._pieces(after == rig.BIT["ArmFarLower"], rig._N4)), 1)
        changed = after != before
        self.assertEqual(int(changed.sum()), 2)                                        # only the two stray pixels
        self.assertTrue((after[changed] == rig.BIT["ArmFarUpper"]).all())             # they sit on the upper arm
        for name in ("ArmNearUpper", "ArmNearLower", "ArmFarUpper", "ArmFarLower"):
            self.assertLessEqual(len(rig._pieces(after == rig.BIT[name], rig._N4)), 1)

    def test_underlay_detached_from_the_body_is_dropped(self):
        from chargen import rig
        parts = self.grid(["BBBB.",
                           "BLLB.",
                           "BLLB.",
                           "..W..",       # an underlay pixel behind the item, not touching any visible Body pixel
                           "..W.."])
        under = np.zeros(parts.shape + (4,), dtype=np.uint8)
        under[1:3, 1:3] = (10, 20, 30, 255)                                           # behind the hand: continues the torso
        under[4, 2] = (10, 20, 30, 255)
        out, dropped = rig.drop_detached_underlay(under, parts)
        self.assertEqual(dropped, 1)
        self.assertEqual(out[4, 2, 3], 0)
        self.assertTrue((out[1:3, 1:3, 3] == 255).all())


class WeaponGripTests(unittest.TestCase):
    """The weapon turns where the hand holds it (WeaponPivot = centre of the item/hand contact), not around the SDPose
    wrist estimate, so a rotating item keeps touching the hand."""
    FIXTURES = HeldItemTests.FIXTURES

    def test_pivot_is_the_centre_of_the_contact(self):
        from chargen import rig
        parts = HeldItemTests.grid(["BBBBBB",
                                          "BLLW..",
                                          "BLLW..",
                                          "...WWW",
                                          "....WW"])
        pivot, tip = rig.weapon_grip(parts, hand=(1.0, 4.5))                          # a wrist estimate a few pixels off
        self.assertEqual(pivot, (3.5, 2.0))                                            # centre of (3,1) and (3,2), +0.5
        self.assertEqual(tip, (5.5, 4.5))

    def test_pivot_without_contact_is_the_item_pixel_nearest_to_the_hand(self):
        from chargen import rig
        parts = HeldItemTests.grid(["LL..W", "....W"])
        pivot, _ = rig.weapon_grip(parts, hand=(4.0, 0.0))
        self.assertEqual(pivot, (4.5, 0.5))
        self.assertIsNone(rig.weapon_grip(HeldItemTests.grid(["LLB"]), hand=(0, 0)))

    def test_elfarcher_pivot_sits_on_the_grip(self):
        from chargen import rig
        parts = rig.decode_parts(self.FIXTURES / "ElfArcher_parts_v0.3.png")
        hand = (24.47, 24.16)                                                          # its SDPose wrist
        pivot, _ = rig.weapon_grip(parts, hand)
        hy, hx = np.nonzero(parts & rig.BIT["ArmNearLower"])
        wy, wx = np.nonzero(parts & rig.BIT["Weapon"])
        to_hand = np.min(np.hypot(hx + 0.5 - pivot[0], hy + 0.5 - pivot[1]))
        to_item = np.min(np.hypot(wx + 0.5 - pivot[0], wy + 0.5 - pivot[1]))
        self.assertLessEqual(to_hand, 1.5)                                             # between hand and item pixels
        self.assertLessEqual(to_item, 1.0)
        self.assertGreater(abs(pivot[1] - 25.5), 0.5)                                  # not the old nearest-pixel pivot (26.5, 25.5)


class SpriteNameTests(unittest.TestCase):
    """Each character's sprite is <Name>.png, so anything named after the sprite file (an animation tool's output folder,
    e.g. Assets/Animations/Generated/<sprite name>/ in the AI Sprite Animation package) is unique per character."""

    @staticmethod
    def animation_folder(sprite):
        # the AI Sprite Animation package: <output root>/<file name without extension, invalid characters and spaces -> _>/
        stem = Path(sprite).stem
        for c in '<>:"/\\|?*':
            stem = stem.replace(c, "_")
        return "Assets/Animations/Generated/" + stem.replace(" ", "_")

    def fake_generate(self, out_root, names):
        """Runs generator.generate with the ComfyUI step replaced by a fixed image (no GPU)."""
        import io
        from unittest import mock
        from chargen import generator
        from chargen.config import load_config
        cfg = load_config()
        buf = io.BytesIO(); fake_side_figure()[0].save(buf, format="PNG"); raw = buf.getvalue()
        details = {"positive": "p", "negative": "n", "models": [], "workflow": "w.json", "workflow_sha256_16": "0" * 16,
                   "params": {}, "comfyui_version": None, "device": None, "seconds_total": 0.0, "seconds_execution": None,
                   "vram": None, "quality_check": None, "guide": None}
        results = []
        with mock.patch.object(generator, "_render_checked", lambda *a, **k: (raw, details, Path(out_root), [])):
            for name in names:
                spec = specmod.normalize(specmod.new_spec(name=specmod.safe_name(name), role="rogue"))
                request = generator.build_request(cfg, spec, "quality", seed=1, guide=False)
                results.append(generator.generate(cfg, request, output_dir=Path(out_root) / spec["name"]))
        return results

    def test_two_characters_never_share_an_animation_folder(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.fake_generate(tmp, ["Ninja", "Necromancer"])
            self.assertEqual(Path(a["sprite"]).name, "Ninja.png")
            self.assertEqual(Path(b["sprite"]).name, "Necromancer.png")
            self.assertNotEqual(self.animation_folder(a["sprite"]), self.animation_folder(b["sprite"]))
            self.assertEqual(self.animation_folder(a["sprite"]), "Assets/Animations/Generated/Ninja")
            self.assertEqual(self.animation_folder(b["sprite"]), "Assets/Animations/Generated/Necromancer")
            self.assertFalse(list(Path(tmp).rglob("character.png")))

    def test_names_are_deterministic_and_unique_for_many_characters(self):
        from chargen.generator import sprite_file
        names = ["Ninja", "Necromancer", "DwarfSide", "KnightWoman", "Old Wizard", "Goblin-Thief", "Rogue_2"]
        files = [sprite_file(n) for n in names]
        self.assertEqual(files, [sprite_file(n) for n in names])                              # deterministic
        self.assertEqual(files[:4], ["Ninja.png", "Necromancer.png", "DwarfSide.png", "KnightWoman.png"])
        folders = [self.animation_folder(f).lower() for f in files]
        self.assertEqual(len(set(folders)), len(folders))                                     # unique, even ignoring case

    def test_names_differing_only_in_case_are_refused(self):
        import tempfile
        from chargen.generator import GenerationError
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_generate(tmp, ["Ninja"])
            with self.assertRaises(GenerationError):
                self.fake_generate(tmp, ["NINJA"])

    def test_regenerating_one_character_leaves_the_other_alone(self):
        import tempfile
        from chargen import generator
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self.fake_generate(tmp, ["Ninja", "Necromancer"])
            before = Path(b["sprite"]).read_bytes(), Path(b["sprite"]).stat().st_mtime_ns
            with self.assertRaises(generator.GenerationError):     # an existing sprite is never replaced silently
                self.fake_generate(tmp, ["Ninja"])
            self.assertEqual((Path(b["sprite"]).read_bytes(), Path(b["sprite"]).stat().st_mtime_ns), before)

    def test_legacy_character_png_is_found_and_migrated_with_its_meta(self):
        import tempfile
        from chargen import generator
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "Ninja"); (folder / "rig").mkdir(parents=True)
            Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(folder / "character.png")
            (folder / "character.png.meta").write_text("guid: 0123456789abcdef0123456789abcdef\n")
            (folder / "metadata.json").write_text(json.dumps({"files": {"sprite": "Assets/X/Ninja/character.png"}}))
            (folder / "rig" / "rig.json").write_text(json.dumps({"sprite": {"file": "character.png"}}))
            self.assertEqual(generator.sprite_path(folder).name, "character.png")              # old folders still work
            new = generator.migrate_sprite_name(folder)
            self.assertEqual(new.name, "Ninja.png")
            self.assertEqual(generator.sprite_path(folder), new)
            self.assertIn("0123456789abcdef", (folder / "Ninja.png.meta").read_text())         # asset id kept
            self.assertFalse((folder / "character.png").exists())
            self.assertEqual(json.loads((folder / "rig" / "rig.json").read_text())["sprite"]["file"], "Ninja.png")
            self.assertEqual(json.loads((folder / "metadata.json").read_text())["files"]["sprite"], "Assets/X/Ninja/Ninja.png")
            self.assertIsNone(generator.migrate_sprite_name(folder))                         # idempotent


class WorkflowTests(unittest.TestCase):
    def test_every_profile_fills_completely(self):
        models = {m["id"] for m in load_models()}
        for name, profile in load_profiles().items():
            with self.subTest(profile=name):
                self.assertTrue(set(profile["models"]) <= models)
                template, _ = workflow.load(profile["workflow"])
                values = dict(profile["params"], positive="p", negative="n", seed=5, width=1024, height=1024,
                              prefix="chargen/x", guide="chargen/guide.png")
                filled = workflow.fill(template, values)
                self.assertNotIn("{{", json.dumps(filled))
                self.assertEqual(workflow.save_image_nodes(filled), ["9"])
                seeds = [n["inputs"]["seed"] for n in filled.values() if "seed" in n["inputs"]]
                self.assertEqual(seeds, [5])  # typed int, not "5"

    def test_missing_value_is_an_error(self):
        template, _ = workflow.load("sdxl_pixelart.json")
        with self.assertRaises(ValueError):
            workflow.fill(template, {"positive": "p"})


class PixelateTests(unittest.TestCase):
    def test_sprite_properties(self):
        for size in (64, 96, 128):
            with self.subTest(size=size):
                sprite, info = pixelate.pixelate(fake_character(), size=size, colors=16)
                a = np.asarray(sprite)
                self.assertEqual(sprite.size, (size, size))
                self.assertEqual(sprite.mode, "RGBA")
                self.assertTrue(set(np.unique(a[..., 3])) <= {0, 255})       # hard alpha, no AA
                self.assertTrue(a[-1, :, 3].any())                          # feet on the bottom row
                self.assertFalse(a[0, :, 3].any())                          # head below the top margin
                self.assertLessEqual(info["palette_size"], 16)
                # shadow removed: nothing grey (120,120,120) left
                opaque = a[a[..., 3] > 0][:, :3].astype(int)
                self.assertFalse((np.abs(opaque - 120).max(axis=1) < 15).any())

    def test_enclosed_background_removed_small_highlight_kept(self):
        img = fake_character(shadow=False)
        d = ImageDraw.Draw(img)
        d.rectangle([472, 300, 552, 380], fill=(170, 170, 170))  # enclosed hole in the body (bg colour)
        d.rectangle([500, 450, 507, 457], fill=(170, 170, 170))  # 8x8 highlight, smaller than one sprite pixel area
        sprite, _ = pixelate.pixelate(img, size=64)
        a = np.asarray(sprite)
        mask, _ = pixelate.background_mask(img)
        self.assertTrue(mask[340, 512])        # hole -> background
        self.assertFalse(mask[453, 503])       # highlight stays part of the character
        self.assertTrue((a[..., 3] == 0).any())

    def test_fixed_height_layout_like_the_player(self):
        from chargen.metrics import sprite_metrics
        sprite, info = pixelate.pixelate(fake_character(), size=48, char_height=45, feet_margin=1,
                                         outline=True, outline_color="black")
        m = sprite_metrics(sprite)
        self.assertEqual(sprite.size[1], 48)
        self.assertEqual((m["char_height"], m["top_row"], m["feet_row"]), (45, 2, 46))  # player: 45, 2, 46
        self.assertEqual(m["edge_dark"], 1.0)
        self.assertTrue(m["alpha_binary"])
        self.assertEqual(m["halo"], 0.0)

    def test_wide_character_widens_canvas_instead_of_shrinking(self):
        img = fake_character(shadow=False)
        ImageDraw.Draw(img).rectangle([20, 400, 1000, 420], fill=(200, 200, 210))  # very long spear
        sprite, info = pixelate.pixelate(img, size=48, char_height=45, feet_margin=1, outline=True)
        self.assertEqual(info["character_size_px"][1], 45)   # height (scale) unchanged
        self.assertGreater(sprite.size[0], 48)               # canvas grew instead
        self.assertEqual(sprite.size[0] % 2, 0)

    def test_no_palette_mode(self):
        sprite, info = pixelate.pixelate(fake_character(), size=48, colors=0, char_height=45)
        self.assertGreater(info["palette_size"], 0)

    def test_deterministic(self):
        a, _ = pixelate.pixelate(fake_character(), size=64)
        b, _ = pixelate.pixelate(fake_character(), size=64)
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_outline(self):
        sprite, info = pixelate.pixelate(fake_character(shadow=False), size=64, outline=True)
        self.assertTrue(info["outline"])

    def test_mirror(self):
        from chargen.generator import postprocess
        import io
        buf = io.BytesIO()
        fake_character().save(buf, format="PNG")
        base = dict(size=64, colors=16, outline=False)
        a, _ = postprocess(buf.getvalue(), dict(base, mirror=False))
        b, info = postprocess(buf.getvalue(), dict(base, mirror=True))
        self.assertTrue(info["mirrored"])
        self.assertEqual(np.asarray(a)[:, ::-1].tobytes(), np.asarray(b).tobytes())

    def test_side_view_gate_rejects_symmetric_figures(self):
        front = Image.new("RGB", (1024, 1024), (170, 170, 170))   # symmetric and wide = front view
        d = ImageDraw.Draw(front)
        d.rectangle([382, 250, 642, 600], fill=(30, 90, 40))       # torso 0.31 of the height wide
        d.rectangle([452, 110, 572, 250], fill=(230, 180, 150))
        d.rectangle([392, 600, 497, 900], fill=(110, 60, 30))
        d.rectangle([527, 600, 632, 900], fill=(110, 60, 30))
        self.assertFalse(pixelate.analyze(front, expect_side=True)["ok"])
        self.assertTrue(pixelate.analyze(front, expect_side=False)["ok"])
        thin = fake_character(shadow=False)                        # symmetric but narrow: a straight side view
        self.assertTrue(pixelate.analyze(thin, expect_side=True)["ok"])
        side = Image.new("RGB", (1024, 1024), (170, 170, 170))     # profile: head forward, foot pointing right
        d = ImageDraw.Draw(side)
        d.rectangle([450, 250, 560, 600], fill=(30, 90, 40))
        d.rectangle([470, 120, 590, 250], fill=(230, 180, 150))
        d.rectangle([590, 170, 625, 200], fill=(230, 180, 150))     # nose
        d.rectangle([460, 600, 520, 900], fill=(110, 60, 30))
        d.rectangle([460, 860, 640, 900], fill=(110, 60, 30))       # foot
        self.assertTrue(pixelate.analyze(side, expect_side=True)["ok"])

    def test_quality_gate(self):
        self.assertTrue(pixelate.analyze(fake_character())["ok"])
        bad = pixelate.analyze(fake_character(figures=3))
        self.assertFalse(bad["ok"])
        self.assertIn("separate figures", bad["reasons"][0])
        noisy = Image.fromarray(np.random.default_rng(0).integers(0, 255, (512, 512, 3), dtype=np.uint8))
        self.assertFalse(pixelate.analyze(noisy)["ok"])


if __name__ == "__main__":
    unittest.main()
