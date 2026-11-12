"""Tests for the FreeLLMAPI catalog parser (stdlib unittest only).

Run:  python3 -m unittest tests.test_freellm_catalog -v
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import freellm_catalog


README = """# awesome-free-llm-apis

<p><strong>505+ free LLM APIs from 30 providers</strong></p>
Last updated: 2026-10-08

<!-- BEGIN_PERMANENT_FREE -->
| Provider | Free Models | Credit Card? | Max Context | Modalities | Get API Key |
|---|---|---|---|---|---|
| Groq | 12 | No | 262K | image, reasoning, text | <a href="https://console.groq.com/keys" target="_blank">→</a> |
| Cerebras | 6 | No | 131K | reasoning, text | <a href="https://cloud.cerebras.ai/" target="_blank">→</a> |
| Broken row | only two |
<!-- END_PERMANENT_FREE -->

<!-- BEGIN_RENEWABLE -->
| Provider | Free Models | Credit Model | Max Context | Modalities | Get API Key |
|---|---|---|---|---|---|
| OpenRouter | 32 | Free tier + $10 topup | 1M | text, vision | <a href="https://openrouter.ai/keys" target="_blank">→</a> |
<!-- END_RENEWABLE -->

<!-- BEGIN_QUICK_REF -->
| Provider | Base URL | Get API Key | Credit Card? |
|---|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | <a href="https://console.groq.com/keys">Get Key →</a> | No |
| OpenRouter | `https://openrouter.ai/api/v1` | <a href="https://openrouter.ai/keys">Get Key →</a> | Registration |
<!-- END_QUICK_REF -->

<!-- BEGIN_BEST_MODELS -->
| Provider | Best Free Model | Model ID | Max Context | Rate Limit |
|---|---|---|---|---|
| Groq | <a href="https://freellm.net/models/groq/kimi/">Kimi K2</a> | `moonshotai/kimi-k2-instruct` | 131K | 30 RPM |
|  | <a href="https://freellm.net/models/groq/llama/">Llama 3.3</a> | `llama-3.3-70b` | 128K | See provider |
| OpenRouter | <a href="https://freellm.net/models/openrouter/nemotron/">Nemotron</a> | `nvidia/nemotron:free` | 1M | See provider |
<!-- END_BEST_MODELS -->

<!-- BEGIN_TOP_MODELS -->
| Model | Provider | Context | Weekly Usage |
|---|---|---|---|
| <a href="https://freellm.net/models/openrouter/nemotron/">Nemotron</a> | OpenRouter | 1M | 5947B tokens |
<!-- END_TOP_MODELS -->
"""


class FreeLLMCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-freellm-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.readme = os.path.join(self.repo, "README.md")
        with open(self.readme, "w", encoding="utf-8") as fh:
            fh.write(README)
        self.patch = mock.patch.object(
            freellm_catalog, "candidate_paths", return_value=[self.repo]
        )
        self.patch.start()
        freellm_catalog._CACHE.update({"path": None, "mtime": None, "data": None})

    def tearDown(self):
        self.patch.stop()
        freellm_catalog._CACHE.update({"path": None, "mtime": None, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_catalog_merges_directory_quick_reference_and_models(self):
        data = freellm_catalog.catalog()
        self.assertTrue(data["ok"])
        self.assertEqual(data["updated"], "2026-10-08")
        self.assertEqual(data["repo_kind"], "checkout")
        self.assertEqual(data["counts"]["providers"], 3)
        self.assertEqual(data["counts"]["models"], 50)
        self.assertEqual(data["counts"]["listed_models"], 3)
        self.assertEqual(data["counts"]["no_card_providers"], 2)
        self.assertEqual(data["counts"]["top_models"], 1)
        self.assertIn("vision", data["modalities"])

        by_name = {provider["name"]: provider for provider in data["providers"]}
        groq = by_name["Groq"]
        self.assertEqual(groq["id"], "groq")
        self.assertEqual(groq["free_models"], 12)
        self.assertEqual(groq["base_url"], "https://api.groq.com/openai/v1")
        self.assertEqual(groq["key_url"], "https://console.groq.com/keys")
        self.assertEqual(groq["max_context"], "262K")
        self.assertEqual(groq["context_tokens"], 262000)
        self.assertEqual(len(groq["models"]), 2)
        self.assertEqual(groq["models"][0]["id"], "moonshotai/kimi-k2-instruct")
        self.assertEqual(groq["models"][1]["provider_id"], "groq")

        openrouter = by_name["OpenRouter"]
        self.assertEqual(openrouter["tier"], "renewable")
        self.assertEqual(openrouter["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(openrouter["models"][0]["id"], "nvidia/nemotron:free")

        self.assertEqual(data["top_models"][0]["provider"], "OpenRouter")
        self.assertEqual(data["top_models"][0]["usage"], "5947B tokens")

    def test_missing_repository_returns_an_empty_catalog(self):
        with mock.patch.object(freellm_catalog, "candidate_paths", return_value=[os.path.join(self.tmp, "nope")]):
            data = freellm_catalog.catalog(force=True)
        self.assertFalse(data["ok"])
        self.assertFalse(data["repo_ready"])
        self.assertIn("not found", data["error"])
        self.assertEqual(data["providers"], [])

    def test_empty_readme_is_not_a_catalog(self):
        with open(self.readme, "w", encoding="utf-8") as fh:
            fh.write("# empty\n")
        data = freellm_catalog.catalog(force=True)
        self.assertFalse(data["ok"])
        self.assertEqual(data["counts"]["providers"], 0)

    def test_catalog_reloads_when_the_readme_changes(self):
        first = freellm_catalog.catalog()
        self.assertEqual(first["counts"]["providers"], 3)
        with open(self.readme, "w", encoding="utf-8") as fh:
            fh.write(
                "# minimal\n\n"
                "<!-- BEGIN_PERMANENT_FREE -->\n"
                "| Provider | Free Models | Credit Card? | Max Context | Modalities | Get API Key |\n"
                "|---|---|---|---|---|---|\n"
                "| Added | 1 | No | 8K | text | <a href=\"https://example.com\">→</a> |\n"
                "<!-- END_PERMANENT_FREE -->\n"
            )
        os.utime(self.readme, None)
        second = freellm_catalog.catalog()
        self.assertEqual(second["counts"]["providers"], 1)
        self.assertEqual(second["providers"][0]["name"], "Added")


if __name__ == "__main__":
    unittest.main()
