"""Unit tests for the Fashion Video workflow.

Run:  python3 -m unittest tests.test_studio_fashion
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_catalog as catalog
from backend import studio_contracts
from backend import studio_fashion as fashion


def draft(**overrides):
    base = {
        "model": "https://example.com/model.jpg",
        "outfits": [{"name": "Look 1", "images": ["https://example.com/top.jpg"]}],
        "reference_video": "https://example.com/ref.mp4",
    }
    base.update(overrides)
    return base


MODEL = {
    "id": "test/fashion-r2v",
    "label": "Fashion R2V",
    "kind": "video",
    "mode": "reference_to_video",
    "schema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "minLength": 1},
            "image_urls": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "minItems": 1,
                "maxItems": 9,
            },
            "video_urls": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "minItems": 1,
                "maxItems": 3,
            },
            "resolution": {"type": "string", "enum": ["480p", "720p", "1080p"]},
            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "adaptive"]},
            "duration": {"type": "integer", "minimum": 4, "maximum": 15},
            "generate_audio": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
}


class DraftTests(unittest.TestCase):
    def test_normalizes_upload_objects_and_defaults(self):
        clean = fashion.normalize_draft(draft(
            model={"url": "https://example.com/m.jpg"},
            reference_video={"public_url": "https://example.com/r.mp4"},
        ))
        self.assertEqual(clean["model"], "https://example.com/m.jpg")
        self.assertEqual(clean["reference_video"], "https://example.com/r.mp4")
        self.assertEqual(clean["outfits"][0]["name"], "Look 1")
        self.assertTrue(clean["generate_audio"])

    def test_model_is_required(self):
        with self.assertRaises(fashion.FashionError):
            fashion.normalize_draft({
                "outfits": [{"images": ["https://a/b.jpg"]}],
                "reference_video": "https://a/r.mp4",
            })

    def test_outfits_and_reference_are_skippable(self):
        clean = fashion.normalize_draft({
            "model": "https://a/m.jpg",
            "prompt": "model walks toward camera",
        })
        self.assertEqual(clean["outfits"], [])
        self.assertEqual(clean["reference_video"], "")

    def test_invalid_outfits_shape_is_rejected(self):
        with self.assertRaises(fashion.FashionError):
            fashion.normalize_draft({"model": "https://a/m.jpg", "outfits": "nope"})

    def test_enforces_outfit_and_image_limits(self):
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.normalize_draft(draft(
                outfits=[{"images": ["https://a/b.jpg"]}] * (fashion.MAX_OUTFITS + 1)
            ))
        self.assertEqual(ctx.exception.code, "too_many_outfits")

        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.normalize_draft(draft(outfits=[{
                "images": ["https://a/b.jpg"] * (fashion.MAX_OUTFIT_IMAGES + 1)
            }]))
        self.assertEqual(ctx.exception.code, "too_many_outfit_images")

    def test_rejects_non_http_assets_and_unknown_templates(self):
        for payload in (
            draft(model="/local/model.png"),
            draft(reference_video="file:///etc/passwd"),
            draft(template="made-up"),
        ):
            with self.assertRaises(fashion.FashionError):
                fashion.normalize_draft(payload)

    def test_image_order_is_model_scene_then_outfits(self):
        clean = fashion.normalize_draft(draft(
            scene_image="https://example.com/scene.jpg",
            outfits=[
                {"images": ["https://example.com/a.jpg", "https://example.com/b.jpg"]},
                {"images": ["https://example.com/c.jpg"]},
            ],
        ))
        self.assertEqual(fashion.image_urls(clean), [
            "https://example.com/model.jpg",
            "https://example.com/scene.jpg",
            "https://example.com/a.jpg",
            "https://example.com/b.jpg",
            "https://example.com/c.jpg",
        ])


class PromptTests(unittest.TestCase):
    def test_prompt_numbers_outfits_without_a_scene(self):
        clean = fashion.normalize_draft(draft(outfits=[
            {"images": ["https://a/1.jpg", "https://a/2.jpg"]},
            {"images": ["https://a/3.jpg"]},
        ]))
        prompt = fashion.build_prompt(clean)
        self.assertIn("@Image2-@Image3 are the clothing references for outfit 1", prompt)
        self.assertIn("@Image4 is outfit 2", prompt)
        self.assertIn("@Video1 is a MOTION REFERENCE ONLY", prompt)
        self.assertIn("There are 2 complete outfits in total", prompt)

    def test_prompt_reserves_image_two_for_the_scene(self):
        clean = fashion.normalize_draft(draft(
            scene_image="https://a/scene.jpg",
            outfits=[{"images": ["https://a/1.jpg"]}],
        ))
        prompt = fashion.build_prompt(clean)
        self.assertIn("@Image2 is a SCENE REFERENCE ONLY", prompt)
        self.assertIn("@Image3 is outfit 1", prompt)

    def test_prompt_appends_template_and_owner_direction(self):
        clean = fashion.normalize_draft(draft(template="dance", prompt="end on a spin"))
        prompt = fashion.build_prompt(clean, fashion._template_hint(clean))
        self.assertIn("Template direction:", prompt)
        self.assertIn("short dance clip", prompt)
        self.assertIn("Additional creative direction: end on a spin", prompt)


class PresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options = fashion.preset_options()
        cls.videos = cls.options["videos"]
        cls.models = cls.options["models"]
        cls.outfits = cls.options["outfits"]

    def test_pinned_catalog_is_available(self):
        if not self.videos:
            self.skipTest("fashion-presets catalog has not been synced")
        self.assertTrue(self.models)
        self.assertTrue(self.outfits)
        for video in self.videos[:10]:
            self.assertTrue(video["video"].startswith("https://"))
            self.assertTrue(1 <= video["outfits"] <= 5)
        filters = self.options["filters"]
        self.assertIn("Try-on", filters["type"])
        self.assertIn("Set", filters["style"])
        self.assertIn("Women", filters["model"])
        self.assertTrue(set(filters["engine"]) <= set(fashion.ENGINE_MODEL_MAP))

    def _video(self, predicate):
        for video in self.videos:
            if predicate(video):
                return video
        self.skipTest("no preset video matches the test condition")

    def test_preset_backed_draft_resolves_assets_from_the_catalog(self):
        video = self._video(lambda item: item["outfits"] == 1 and item["type"])
        garment = self.outfits[0]
        model = self.models[0]
        resolved = fashion.resolve_draft(fashion.normalize_draft({
            "preset": video["id"],
            "model_preset": model["id"],
            "outfits": [{"preset": garment["id"]}],
        }))
        self.assertEqual(resolved["reference_video"], video["video"])
        self.assertEqual(resolved["model"], model["cover"])
        self.assertEqual(resolved["outfits"][0]["images"], [garment["cover"]])
        self.assertEqual(resolved.get("duration"), video["duration"])
        self.assertEqual(fashion.preset_template_id(resolved),
                         fashion.PRESET_TYPE_TEMPLATE[video["type"][0].lower()])

    def test_explicit_assets_win_over_presets(self):
        video = self._video(lambda item: item["outfits"] == 1)
        garment = self.outfits[0]
        resolved = fashion.resolve_draft(fashion.normalize_draft({
            "preset": video["id"],
            "model": "https://example.com/my-model.jpg",
            "outfits": [{"preset": garment["id"], "images": ["https://example.com/my-top.jpg"]}],
        }))
        self.assertEqual(resolved["model"], "https://example.com/my-model.jpg")
        self.assertEqual(resolved["outfits"][0]["images"], ["https://example.com/my-top.jpg"])

    def test_preset_outfit_count_is_enforced(self):
        video = self._video(lambda item: item["outfits"] == 2)
        garments = self.outfits[:2]
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.resolve_draft(fashion.normalize_draft({
                "preset": video["id"],
                "model_preset": self.models[0]["id"],
                "outfits": [{"preset": garments[0]["id"]}],
            }))
        self.assertEqual(ctx.exception.code, "preset_outfit_count")

        resolved = fashion.resolve_draft(fashion.normalize_draft({
            "preset": video["id"],
            "model_preset": self.models[0]["id"],
            "outfits": [{"preset": garments[0]["id"]}, {"preset": garments[1]["id"]}],
        }))
        self.assertEqual(len(resolved["outfits"]), 2)

    def test_unknown_preset_ids_are_rejected(self):
        video = self.videos[0]
        base = {"preset": self.videos[0]["id"],
                "model_preset": self.models[0]["id"],
                "outfits": [{"preset": self.outfits[0]["id"]} if self.outfits else {"images": ["https://a/b.jpg"]}]}
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.resolve_draft(fashion.normalize_draft(dict(base, preset="NOPE")))
        self.assertEqual(ctx.exception.code, "unknown_preset")
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.resolve_draft(fashion.normalize_draft(dict(base, model_preset="NOPE")))
        self.assertEqual(ctx.exception.code, "unknown_model_preset")

    def test_preset_suggests_a_matching_studio_model(self):
        available = {"bytedance/seedance-2.0/reference-to-video",
                     "alibaba/wan-3.0/reference-to-video"}
        seedance = self._video(lambda item: "seedance" in (item.get("engines") or []))
        self.assertEqual(
            fashion.suggest_model_id(seedance, available),
            "bytedance/seedance-2.0/reference-to-video",
        )


class ModelAndPlanTests(unittest.TestCase):
    def test_model_filter_keeps_reference_models_with_image_and_video(self):
        models = [
            {"id": "ok", "kind": "video", "mode": "reference_to_video",
             "summary": {"accepts": {"image": True, "video": True}, "prompt": True,
                         "image_fields": ["image_urls"], "max_images": 9}},
            {"id": "edit", "kind": "video", "mode": "video_edit",
             "summary": {"accepts": {"image": True, "video": True}, "prompt": True,
                         "image_fields": ["image_urls"]}},
            {"id": "no-video", "kind": "video", "mode": "reference_to_video",
             "summary": {"accepts": {"image": True, "video": False}, "prompt": True,
                         "image_fields": ["image_urls"]}},
            {"id": "single-image", "kind": "video", "mode": "reference_to_video",
             "summary": {"accepts": {"image": True, "video": True}, "prompt": True,
                         "image_fields": ["image_url"], "max_images": 1}},
            {"id": "still", "kind": "image", "mode": "reference_to_video",
             "summary": {"accepts": {"image": True, "video": True}, "prompt": True,
                         "image_fields": ["image_urls"]}},
        ]
        picked = fashion.fashion_models(models)
        self.assertEqual([item["id"] for item in picked], ["ok"])

    def test_production_pool_matches_referenceless_generation(self):
        models = [
            {"id": "i2v", "kind": "video", "mode": "image_to_video",
             "summary": {"accepts": {"image": True, "video": False}, "prompt": True,
                         "image_fields": ["image_url", "end_image_url"]}},
            {"id": "t2v", "kind": "video", "mode": "text_to_video",
             "summary": {"accepts": {"image": False, "video": False}, "prompt": True,
                         "image_fields": []}},
            {"id": "r2v", "kind": "video", "mode": "reference_to_video",
             "summary": {"accepts": {"image": True, "video": True}, "prompt": True,
                         "image_fields": ["image_urls"]}},
        ]
        with_image = fashion.production_models(models, has_image=True)
        self.assertEqual([item["id"] for item in with_image], ["i2v"])
        without_image = fashion.production_models(models, has_image=False)
        self.assertEqual([item["id"] for item in without_image], ["t2v"])

    def test_plan_maps_supported_fields_only(self):
        clean = fashion.normalize_draft(draft(
            resolution="720p", aspect_ratio="9:16", duration=8,
            generate_audio=False,
        ))
        params = fashion.plan_job(MODEL, clean)
        self.assertEqual(params["resolution"], "720p")
        self.assertEqual(params["aspect_ratio"], "9:16")
        self.assertEqual(params["duration"], 8)
        self.assertFalse(params["generate_audio"])
        self.assertEqual(params["video_urls"], ["https://example.com/ref.mp4"])

    def test_plan_rejects_invalid_and_oversized_inputs(self):
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.plan_job(MODEL, fashion.normalize_draft(draft(resolution="4k")))
        self.assertEqual(ctx.exception.code, "invalid_resolution")

        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.plan_job(MODEL, fashion.normalize_draft(draft(duration=99)))
        self.assertEqual(ctx.exception.code, "invalid_duration")

        # Nine images fit; a tenth does not.
        outfits = [{"images": ["https://a/%d.jpg" % i for i in range(5)]},
                   {"images": ["https://a/%d.jpg" % i for i in range(5, 10)]}]
        with self.assertRaises(fashion.FashionError) as ctx:
            fashion.plan_job(MODEL, fashion.normalize_draft(draft(outfits=outfits)))
        self.assertEqual(ctx.exception.code, "too_many_images")

    def test_prepare_job_uses_the_requested_model(self):
        class Provider:
            id = "test"

            def models(self, kind=None):
                return [dict(MODEL, summary={
                    "accepts": {"image": True, "video": True},
                    "prompt": True,
                    "image_fields": ["image_urls"],
                    "max_images": 9,
                })]

            def model(self, model_id):
                return MODEL

        plan = fashion.prepare_job(Provider(), {"provider": "test", "draft": draft()})
        self.assertEqual(plan["model_id"], "test/fashion-r2v")
        self.assertEqual(plan["image_count"], 2)
        self.assertIn("@Image1 is the fashion model", plan["params"]["prompt"])

    def test_prepare_job_without_reference_uses_image_to_video(self):
        i2v = {
            "id": "test/i2v",
            "label": "Test I2V",
            "kind": "video",
            "mode": "image_to_video",
            "schema": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "minLength": 1},
                    "image_url": {"type": "string", "format": "uri"},
                    "resolution": {"type": "string", "enum": ["720p"]},
                    "duration": {"type": "integer", "minimum": 4, "maximum": 12},
                },
                "additionalProperties": False,
            },
        }

        class Provider:
            id = "test"

            def models(self, kind=None):
                return [dict(i2v, summary={
                    "accepts": {"image": True, "video": False},
                    "prompt": True,
                    "image_fields": ["image_url"],
                })]

            def model(self, model_id):
                return i2v

        plan = fashion.prepare_job(Provider(), {"provider": "test", "draft": {
            "model": "https://a/m.jpg",
            "prompt": "slow turn toward camera",
        }})
        self.assertEqual(plan["model_id"], "test/i2v")
        self.assertEqual(plan["params"]["image_url"], "https://a/m.jpg")
        self.assertNotIn("video_urls", plan["params"])
        self.assertIn("No reference video was provided", plan["params"]["prompt"])

    def test_every_real_reference_model_accepts_the_planned_params(self):
        models = catalog.list_models("higgsfield", kind="video")
        clean = fashion.normalize_draft(draft())
        picked = fashion.fashion_models(models)
        for option in picked:
            raw = catalog.get_model(option["id"])
            params = fashion.plan_job(raw, clean)
            # Raises ContractError if a planned field is unknown or invalid.
            studio_contracts.validate_params(raw, params)

    def test_real_catalog_offers_reference_models(self):
        models = catalog.list_models("higgsfield", kind="video")
        picked = fashion.fashion_models(models)
        self.assertTrue(picked)
        ids = [item["id"] for item in picked]
        self.assertIn("bytedance/seedance-2.0/reference-to-video", ids)
        config = fashion.build_config(models, "higgsfield")
        self.assertEqual(config["workflow"], "fashion_video")
        self.assertTrue(config["defaults"]["model_id"])
        self.assertTrue(config["defaults"]["resolution"])
        self.assertTrue(config["defaults"]["aspect_ratio"])
        self.assertIsNotNone(config["defaults"]["duration"])
        self.assertEqual(config["limits"]["outfits"], fashion.MAX_OUTFITS)


if __name__ == "__main__":
    unittest.main()
