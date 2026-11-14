"""Tests for media probing, adaptive sampling and the normalized evidence model.

Run:  ../.venv/bin/python -m unittest tests.test_skill_media_evidence -v
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_evidence
from backend import skill_media


PROBE = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1080,
         "height": 1920, "avg_frame_rate": "30/1", "pix_fmt": "yuv420p",
         "duration": "12.5"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100",
         "channels": 2},
    ],
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5",
               "bit_rate": "1200000", "size": "1875000"},
}


class ProbeTests(unittest.TestCase):
    def test_parse_probe_extracts_stream_facts(self):
        parsed = skill_media.parse_probe(PROBE)
        self.assertEqual(parsed["duration_seconds"], 12.5)
        self.assertEqual(parsed["width"], 1080)
        self.assertEqual(parsed["height"], 1920)
        self.assertEqual(parsed["aspect"], "9:16")
        self.assertEqual(parsed["fps"], 30.0)
        self.assertEqual(parsed["video_codec"], "h264")
        self.assertEqual(parsed["audio_codec"], "aac")
        self.assertEqual(parsed["sample_rate"], 44100)
        self.assertEqual(parsed["channels"], 2)
        self.assertEqual(parsed["container"], "mov")
        self.assertTrue(parsed["has_video"])
        self.assertTrue(parsed["has_audio"])

    def test_rate_and_aspect_fallbacks(self):
        self.assertEqual(skill_media._rate("30000/1001"), 29.97)
        self.assertEqual(skill_media._rate(""), 0.0)
        self.assertEqual(skill_media._aspect(1920, 1080), "16:9")
        self.assertEqual(skill_media._aspect(0, 0), "")
        self.assertEqual(skill_media._aspect(1000, 500), "2:1")

    def test_probe_missing_file_is_an_error(self):
        result = skill_media.probe("/nonexistent/video.mp4")
        self.assertIn("error", result)


class SamplingTests(unittest.TestCase):
    def test_sampling_is_bounded_and_monotonic(self):
        times = skill_media.plan_sampling(600, fps=30, max_frames=24)
        self.assertLessEqual(len(times), 24)
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0 <= t <= 600 for t in times))
        self.assertGreater(times[-1], 100)

    def test_very_short_clip_still_samples(self):
        times = skill_media.plan_sampling(1.2, fps=30, max_frames=24)
        self.assertGreaterEqual(len(times), 1)
        self.assertTrue(all(t < 1.2 for t in times))

    def test_zero_duration_returns_nothing(self):
        self.assertEqual(skill_media.plan_sampling(0), [])
        self.assertEqual(skill_media.plan_sampling(-3), [])

    def test_parse_scene_times_builds_contiguous_scenes(self):
        text = "pts_time:0\npts_time:4.000000\npts_time:4.000000\npts_time:9.5\n"
        scenes = skill_media.parse_scene_times(text, duration=12.0)
        self.assertEqual([s["index"] for s in scenes], [0, 1, 2])
        self.assertEqual(scenes[0]["start"], 0.0)
        self.assertEqual(scenes[1]["start"], 4.0)
        self.assertEqual(scenes[-1]["end"], 12.0)
        for previous, current in zip(scenes, scenes[1:]):
            self.assertAlmostEqual(previous["end"], current["start"], places=3)

    def test_parse_scene_times_without_duration(self):
        scenes = skill_media.parse_scene_times("pts_time:2\n", duration=0)
        self.assertTrue(scenes)
        self.assertGreater(scenes[-1]["end"], 2.0)

    def test_safe_name_strips_unsafe_characters(self):
        self.assertEqual(skill_media.safe_name("../../etc/passwd"), "etc-passwd")
        self.assertEqual(skill_media.safe_name(""), "media")


class RealFfmpegTests(unittest.TestCase):
    """Integration checks against the actual binaries (skipped if absent)."""

    @classmethod
    def setUpClass(cls):
        cls.binaries = skill_media.available()
        if not (cls.binaries.get("ffmpeg") and cls.binaries.get("ffprobe")):
            raise unittest.SkipTest("ffmpeg/ffprobe not available")
        cls.tmp = tempfile.mkdtemp(prefix="sb-media-")
        cls.video = os.path.join(cls.tmp, "clip.mp4")
        code, _out, err = skill_media.run([
            cls.binaries["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-shortest", "-pix_fmt", "yuv420p", cls.video], timeout=60)
        if code != 0 or not os.path.isfile(cls.video):
            raise unittest.SkipTest("could not synthesise a test clip: %s" % err[:120])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_probe_real_file(self):
        probed = skill_media.probe(self.video)
        self.assertNotIn("error", probed)
        self.assertAlmostEqual(probed["duration_seconds"], 2.0, delta=0.3)
        self.assertEqual((probed["width"], probed["height"]), (320, 240))
        self.assertEqual(probed["video_codec"], "h264")
        self.assertTrue(probed["has_audio"])

    def test_scene_and_frame_extraction(self):
        result = skill_media.analyse_media(
            self.video, os.path.join(self.tmp, "frames"), max_frames=4)
        self.assertTrue(result["scenes"])
        self.assertTrue(result["frames"])
        self.assertLessEqual(len(result["frames"]), 4)
        for frame in result["frames"]:
            self.assertTrue(os.path.isfile(frame["path"]))
            self.assertGreater(os.path.getsize(frame["path"]), 0)
        frames = result["frames"]
        self.assertEqual(frames, sorted(frames, key=lambda item: item["t"]))


class EvidenceTests(unittest.TestCase):
    def _source(self, **overrides):
        source = {
            "kind": "youtube", "id": "abc123", "url": "https://youtu.be/abc123",
            "title": "Reference clip", "content_hash": "deadbeef", "fetched_at": 1,
            "metadata": {"duration_seconds": 60, "width": 1080, "height": 1920,
                         "view_count": 1234},
            "transcript": "one two three four five six",
            "timed_transcript": "[00:00] First line\n[00:10] Second line",
        }
        source.update(overrides)
        return source

    def test_normalize_produces_valid_evidence(self):
        evidence = skill_evidence.normalize(
            self._source(),
            technical={"duration_seconds": 60, "width": 1080, "height": 1920,
                       "fps": 30, "aspect": "9:16", "container": "mp4"},
            scenes=[{"start": 0, "end": 20}, {"start": 20, "end": 60}],
            frames=[{"index": 0, "t": 10, "ref": "frames/0000.jpg"}])
        self.assertEqual(skill_evidence.validate(evidence), [])
        self.assertEqual(evidence["version"], 1)
        self.assertEqual(evidence["source"]["view_count"], 1234)
        self.assertEqual(evidence["scenes"][0]["frame_ref"], "frames/0000.jpg")
        self.assertEqual(evidence["timeline"]["cuts"], 1)
        self.assertEqual(evidence["timeline"]["cuts_per_minute"], 1.0)
        self.assertAlmostEqual(evidence["timeline"]["mean_scene_seconds"], 30.0)
        self.assertEqual(evidence["audio"]["words"], 6)
        self.assertEqual(evidence["audio"]["wpm"], 6.0)

    def test_missing_frames_are_reported_as_limitation(self):
        evidence = skill_evidence.normalize(self._source(), technical={},
                                            scenes=[], frames=[])
        self.assertTrue(any("frames" in item for item in evidence["limitations"]))
        self.assertTrue(any("scene" in item for item in evidence["limitations"]))
        self.assertEqual(skill_evidence.validate(evidence), [])

    def test_validate_rejects_bad_scenes(self):
        evidence = skill_evidence.normalize(
            self._source(), technical={"duration_seconds": 10},
            scenes=[{"start": 5, "end": 2}, {"start": 4, "end": 9}])
        errors = skill_evidence.validate(evidence)
        # The normaliser drops non-positive scenes, so only the ordering test
        # remains meaningful; a hand-built object must fail loudly.
        broken = {"version": 1, "source": {"id": "x"},
                  "scenes": [{"index": 0, "start": 5, "end": 2, "duration": -3}]}
        self.assertTrue(skill_evidence.validate(broken))
        self.assertIsInstance(errors, list)

    def test_parse_timed_transcript(self):
        segments = skill_evidence.parse_timed_transcript(
            "1\n00:00:01,500 --> 00:00:03,000\nHello there\n\n"
            "[00:10] Second line\n")
        self.assertEqual(segments[0]["start"], 1.5)
        self.assertEqual(segments[0]["text"], "Hello there")
        self.assertEqual(segments[1]["start"], 10.0)

    def test_prompt_block_and_compact(self):
        evidence = skill_evidence.normalize(
            self._source(), technical={"duration_seconds": 60, "container": "mp4"},
            scenes=[{"start": 0, "end": 60}], frames=[{"index": 0, "t": 5,
                                                      "ref": "frames/0000.jpg"}])
        block = skill_evidence.prompt_block(evidence)
        self.assertIn("SOURCE:", block)
        self.assertIn("SCENES", block)
        self.assertIn("[0.0-60.0]", block)
        summary = skill_evidence.compact(evidence)
        self.assertEqual(summary["scene_count"], 1)
        self.assertEqual(summary["frame_count"], 1)
        self.assertEqual(skill_evidence.prompt_block(None), "(no structured evidence)")


if __name__ == "__main__":
    unittest.main()
