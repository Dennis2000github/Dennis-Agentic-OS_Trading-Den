"""Unit tests for the provider-neutral Studio contracts.

Run:  python3 -m unittest tests.test_studio_contracts
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_contracts as c


MODEL = {
    "id": "test/model",
    "kind": "video",
    "mode": "text_to_video",
    "schema": {
        "type": "object",
        "required": ["prompt"],
        "additionalProperties": False,
        "properties": {
            "prompt": {"type": "string", "minLength": 1, "maxLength": 20},
            "duration": {"type": "integer", "minimum": 2, "maximum": 10, "default": 4},
            "resolution": {"type": "string", "enum": ["720p", "1080p"]},
            "generate_audio": {"type": "boolean"},
            "image_url": {"type": "string", "format": "uri"},
            "image_urls": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "minItems": 1,
                "maxItems": 2,
            },
            "seed": {"type": "integer"},
        },
    },
}


class ValidationTests(unittest.TestCase):
    def test_accepts_documented_parameters(self):
        clean = c.validate_params(MODEL, {
            "prompt": "a neon city",
            "duration": 6,
            "resolution": "1080p",
            "generate_audio": True,
            "image_urls": ["https://example.com/a.png"],
        })
        self.assertEqual(clean["duration"], 6)

    def test_rejects_unknown_field_instead_of_dropping_it(self):
        with self.assertRaises(c.ContractError) as ctx:
            c.validate_params(MODEL, {"prompt": "x", "mystery": 1})
        self.assertEqual(ctx.exception.code, "unknown_field")
        self.assertIn("mystery", ctx.exception.message)

    def test_rejects_wrong_types_and_booleans_as_numbers(self):
        for value in ("6", 6.5, True):
            with self.assertRaises(c.ContractError):
                c.validate_params(MODEL, {"prompt": "x", "duration": value})
        with self.assertRaises(c.ContractError):
            c.validate_params(MODEL, {"prompt": 7})

    def test_enforces_enum_bounds_length_and_items(self):
        cases = [
            {"prompt": "x", "resolution": "4k"},
            {"prompt": "x", "duration": 99},
            {"prompt": ""},
            {"prompt": "x" * 21},
            {"prompt": "x", "image_urls": []},
            {"prompt": "x", "image_urls": ["https://a/b.png", "https://a/c.png", "https://a/d.png"]},
            {"prompt": "x", "image_url": "file:///etc/passwd"},
        ]
        for params in cases:
            with self.assertRaises(c.ContractError, msg=str(params)):
                c.validate_params(MODEL, params)

    def test_required_field_message_names_the_field(self):
        with self.assertRaises(c.ContractError) as ctx:
            c.validate_params(MODEL, {})
        self.assertEqual(ctx.exception.code, "missing_field")
        self.assertIn("params.prompt", ctx.exception.message)

    def test_local_refs_and_nullable_anyof(self):
        model = {
            "id": "ref/model",
            "kind": "image",
            "schema": {
                "$defs": {
                    "Img": {
                        "type": "object",
                        "required": ["type", "image_url"],
                        "properties": {
                            "type": {"const": "image_url"},
                            "image_url": {"type": "string", "format": "uri"},
                        },
                    },
                    "Level": {"type": "string", "enum": ["v1", "v2"]},
                },
                "type": "object",
                "required": ["images"],
                "properties": {
                    "images": {
                        "type": "array",
                        "items": {"$ref": "#/$defs/Img"},
                        "minItems": 1,
                    },
                    "version": {"$ref": "#/$defs/Level"},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        }
        clean = c.validate_params(model, {
            "images": [{"type": "image_url", "image_url": "https://a/b.png"}],
            "version": "v2",
            "note": None,
        })
        self.assertEqual(clean["version"], "v2")
        with self.assertRaises(c.ContractError):
            c.validate_params(model, {
                "images": [{"type": "image_url", "image_url": "https://a/b.png", "extra": 1}],
            })

    def test_if_then_constraints(self):
        model = {
            "id": "cond/model",
            "kind": "video",
            "schema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["fast", "quality"]},
                    "duration": {"type": "integer"},
                },
                "if": {"properties": {"mode": {"const": "fast"}}, "required": ["mode"]},
                "then": {"properties": {"duration": {"maximum": 5}}},
            },
        }
        c.validate_params(model, {"mode": "fast", "duration": 5})
        with self.assertRaises(c.ContractError):
            c.validate_params(model, {"mode": "fast", "duration": 6})
        c.validate_params(model, {"mode": "quality", "duration": 6})


class DerivationTests(unittest.TestCase):
    def test_field_controls(self):
        fields = {f["name"]: f for f in c.derive_fields(MODEL)}
        self.assertEqual(fields["prompt"]["control"], "text")
        self.assertEqual(fields["duration"]["control"], "number")
        self.assertEqual(fields["resolution"]["control"], "select")
        self.assertEqual(fields["generate_audio"]["control"], "toggle")
        self.assertEqual(fields["image_url"]["control"], "url")
        self.assertEqual(fields["image_urls"]["control"], "url_list")
        self.assertTrue(fields["seed"]["advanced"])
        self.assertTrue(fields["prompt"]["required"])

    def test_summary_reports_documented_facts(self):
        summary = c.derive_summary(MODEL)
        self.assertTrue(summary["prompt"])
        self.assertTrue(summary["accepts"]["image"])
        self.assertEqual(summary["durations"], [])
        self.assertEqual(summary["duration_min"], 2)
        self.assertEqual(summary["duration_max"], 10)
        self.assertEqual(summary["aspect_ratios"], [])
        self.assertEqual(summary["max_images"], 2)

    def test_capabilities_never_claim_progress_or_estimates(self):
        caps = c.capabilities_for(MODEL)
        self.assertFalse(caps["progress"])
        self.assertFalse(caps["estimate"])
        self.assertEqual(caps["cancel"], "queued_only")


class NormalizationTests(unittest.TestCase):
    def test_maps_every_documented_state(self):
        expected = {
            "queued": "queued",
            "in_progress": "running",
            "completed": "succeeded",
            "canceled": "cancelled",
            "canceled ": "cancelled",
            "processing": "running",
            "success": "succeeded",
        }
        for raw, state in expected.items():
            self.assertEqual(c.map_state("higgsfield", raw)[0], state)

    def test_nsfw_is_a_terminal_content_failure(self):
        state, error = c.map_state("higgsfield", "nsfw")
        self.assertEqual(state, "failed")
        self.assertEqual(error["code"], "content_moderation")
        self.assertFalse(error["retryable"])

    def test_unknown_state_fails_instead_of_polling_forever(self):
        state, error = c.map_state("higgsfield", "teleporting")
        self.assertEqual(state, "failed")
        self.assertEqual(error["code"], "unknown_state")

    def test_failed_state_carries_redacted_message(self):
        state, error = c.map_state("higgsfield", "failed", "bad Key abc:def")
        self.assertEqual(state, "failed")
        self.assertNotIn("abc:def", error["message"])

    def test_collects_image_video_audio_and_artifact_outputs(self):
        payload = {
            "status": "completed",
            "images": [{"url": "https://cdn/a.png"}],
            "video": {"url": "https://cdn/b.mp4?x=1"},
            "audio": {"url": "https://cdn/c.mp3"},
            "artifacts": [{"url": "https://cdn/d.zip"}],
        }
        outputs = c.normalize_outputs(payload, "video")
        urls = {x["url"]: x["kind"] for x in outputs}
        self.assertEqual(urls["https://cdn/a.png"], "image")
        self.assertEqual(urls["https://cdn/b.mp4?x=1"], "video")
        self.assertEqual(urls["https://cdn/c.mp3"], "audio")
        self.assertEqual(urls["https://cdn/d.zip"], "file")

    def test_ignores_non_http_outputs(self):
        self.assertEqual(
            c.normalize_outputs({"video": {"url": "file:///tmp/x.mp4"}}, "video"), []
        )

    def test_error_mapping_and_retryability(self):
        err = c.error_from_response(401, {"detail": "Invalid credentials"})
        self.assertEqual(err["code"], "authentication_failed")
        self.assertFalse(err["retryable"])
        err = c.error_from_response(500, {"detail": "boom"})
        self.assertTrue(err["retryable"])
        err = c.error_from_response(403, {"detail": "Insufficient credits"})
        self.assertEqual(err["code"], "insufficient_credits")

    def test_request_hash_is_stable_and_parameter_sensitive(self):
        a = c.request_hash("higgsfield", "m", {"prompt": "x", "n": 1})
        b = c.request_hash("higgsfield", "m", {"n": 1, "prompt": "x"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c.request_hash("higgsfield", "m", {"prompt": "y"}))

    def test_redaction(self):
        data = c.redact({
            "api_key": "abc",
            "nested": {"webhook_secret": "shh", "url": "https://x?token=abc"},
            "text": "Authorization: Key id:secret",
        })
        self.assertEqual(data["api_key"], "[redacted]")
        self.assertEqual(data["nested"]["webhook_secret"], "[redacted]")
        self.assertNotIn("abc", data["nested"]["url"])
        self.assertNotIn("id:secret", data["text"])

    def test_filename_sanitizing(self):
        self.assertEqual(c.sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(c.sanitize_filename(""), "file")
        self.assertEqual(c.sanitize_filename("a b/c?.mp4"), "c_.mp4")


if __name__ == "__main__":
    unittest.main()
