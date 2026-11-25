"""Tests for the COSMOS connection unblock patch.

Covers the static-file guard, owner-gated Shopee credential save, fresh
per-retry signing with server-time validation, multi-offer upsert correctness,
COSMOS shared-capability projection, and YouTube caller attribution.

Network is mocked; no provider secret is printed or written outside a temp dir.

Run:  python3 -m unittest tests.test_integrations_unblock -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
from email.utils import formatdate
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_api
from backend import cosmos_registry
from backend import static_guard
from backend.integrations import executions
from backend.integrations import registry
from backend.integrations import store
from backend.integrations import youtube_api
from backend.integrations.shopee import api as shopee_api
from backend.integrations.shopee import auth
from backend.integrations.shopee import client
from backend.integrations.shopee import products


class StaticGuardTestCase(unittest.TestCase):
    def test_private_paths_are_denied(self):
        private = (
            "/data/integrations/shopee/credentials.json",
            "/data",
            "/backend/config.py",
            "/tests/test_integrations_unblock.py",
            "/.env",
            "/.git/config",
            "/vault/.claude/settings.json",
            "/%2e%2e/data/integrations/shopee/credentials.json",
        )
        for path in private:
            self.assertTrue(static_guard.is_private(path), path)

    def test_public_app_paths_are_allowed(self):
        public = (
            "/",
            "/index.html",
            "/js/integrationsPanel.js",
            "/vendor/marked.min.js",
            "/favicon.ico",
            "/api/integrations",
        )
        for path in public:
            self.assertFalse(static_guard.is_private(path), path)


class ShopeeCredentialsRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-unblock-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._cred_file = auth.CREDENTIALS_FILE
        self._overrides = registry.OVERRIDES_FILE
        self._executions = executions.EXECUTIONS_FILE
        auth.CREDENTIALS_FILE = os.path.join(self.tmp, "credentials.json")
        registry.OVERRIDES_FILE = os.path.join(self.tmp, "registry.json")
        executions.EXECUTIONS_FILE = os.path.join(self.tmp, "executions.json")
        self._env = {k: os.environ.get(k) for k in
                     (auth.APP_ID_ENV, auth.SECRET_ENV, auth.ENDPOINT_ENV)}
        for key in self._env:
            os.environ.pop(key, None)

    def tearDown(self):
        auth.CREDENTIALS_FILE = self._cred_file
        registry.OVERRIDES_FILE = self._overrides
        executions.EXECUTIONS_FILE = self._executions
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_credentials_post_saves_locally_without_leaking_secret(self):
        status, payload = shopee_api.handle(
            "POST", "/api/integrations/shopee/credentials", {},
            {"app_id": "app-123", "secret": "super-secret-value",
             "endpoint": "https://example.test/graphql"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["credentials"]["configured"])
        self.assertEqual(payload["credentials"]["source"], "store")
        self.assertNotIn("super-secret-value", json.dumps(payload))
        creds = auth.resolve_credentials()
        self.assertEqual(creds["app_id"], "app-123")
        self.assertEqual(creds["secret"], "super-secret-value")
        self.assertEqual(creds["endpoint"], "https://example.test/graphql")
        self.assertEqual(os.stat(auth.CREDENTIALS_FILE).st_mode & 0o777, 0o600)
        rows = executions.list(limit=20)
        self.assertTrue(any(row["action"] == "credentials.save" for row in rows))
        self.assertNotIn("super-secret-value", json.dumps(rows))

    def test_credentials_post_rejects_missing_fields(self):
        status, payload = shopee_api.handle(
            "POST", "/api/integrations/shopee/credentials", {},
            {"app_id": "app-123"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(os.path.isfile(auth.CREDENTIALS_FILE))
        self.assertTrue(payload["code"])


class ShopeeClientPatchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-client-patch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._cred_file = auth.CREDENTIALS_FILE
        auth.CREDENTIALS_FILE = os.path.join(self.tmp, "credentials.json")
        auth.save_credentials("app", "secret")

    def tearDown(self):
        auth.CREDENTIALS_FILE = self._cred_file

    @staticmethod
    def _response(payload, date_epoch):
        class Response(object):
            def __init__(self):
                self.headers = {"Date": formatdate(date_epoch, usegmt=True)}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        return Response()

    def test_server_time_validation(self):
        now = int(time.time())
        date = formatdate(now, usegmt=True)
        self.assertTrue(auth.validate_server_time(date, now))
        self.assertFalse(auth.validate_server_time(date, now - 1000))
        self.assertIsNone(auth.validate_server_time("not-a-date", now))

    def test_retry_uses_fresh_timestamp_and_signature(self):
        now = int(time.time())
        first_headers = {"Authorization": "sig-first", "Content-Type": "application/json"}
        second_headers = {"Authorization": "sig-second", "Content-Type": "application/json"}
        transient = urllib.error.HTTPError(
            "https://example.test/graphql", 500, "transient",
            {}, io.BytesIO(b"busy"),
        )
        with mock.patch.object(auth, "sign_request", side_effect=[
            (first_headers, str(now - 5)), (second_headers, str(now)),
        ]) as sign_request, \
                mock.patch("urllib.request.urlopen", side_effect=[
                    transient, self._response({"data": {"x": 1}}, now),
                ]), \
                mock.patch.object(client.time, "sleep", return_value=None):
            data = client.graphql("query { x }", retries=1)
        self.assertEqual(data, {"x": 1})
        self.assertEqual(sign_request.call_count, 2)
        self.assertEqual(sign_request.call_args_list[0][0][0],
                         sign_request.call_args_list[1][0][0])
        self.assertEqual(sign_request.call_args_list[0][0][1],
                         sign_request.call_args_list[1][0][1])

    def test_server_time_skew_fails_closed(self):
        now = int(time.time())
        headers = {"Authorization": "sig", "Content-Type": "application/json"}
        with mock.patch.object(auth, "sign_request",
                               return_value=(headers, str(now - 1000))), \
                mock.patch("urllib.request.urlopen",
                           return_value=self._response({"x": 1}, now)):
            with self.assertRaises(client.ShopeeError) as ctx:
                client.graphql("query { x }", retries=0)
        self.assertEqual(ctx.exception.code, "clock_skew")


class ShopeeUpsertPatchTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-upsert-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._products_file = products.PRODUCTS_FILE
        products.PRODUCTS_FILE = os.path.join(self.tmp, "products.json")

    def tearDown(self):
        products.PRODUCTS_FILE = self._products_file

    @staticmethod
    def _offer(item_id, title):
        return {"normalized": {"item_id": item_id, "shop_id": "9", "title": title},
                "raw": {"itemId": item_id}}

    def test_multi_insert_then_update_keeps_correct_rows(self):
        products.upsert_offers([self._offer("1", "A"), self._offer("2", "B")])
        products.upsert_offers([self._offer("1", "A2")])
        rows = store.load_store(products.PRODUCTS_FILE, "products")
        by_key = {(row["item_id"], row["shop_id"]): row for row in rows}
        self.assertEqual(by_key[("1", "9")]["normalized"]["title"], "A2")
        self.assertEqual(by_key[("2", "9")]["normalized"]["title"], "B")


class CosmosSharedPatchTestCase(unittest.TestCase):
    @staticmethod
    def _sample():
        return {
            "providers": [
                {"provider": "shopee", "status": "not_configured", "enabled": True},
            ],
            "capabilities": [
                {"id": "shopee.catalog", "provider": "shopee", "label": "Catalog",
                 "permission": "read", "approval_required": False,
                 "ready": False, "summary": "Catalog read"},
                {"id": "composio.youtube.write", "provider": "composio",
                 "label": "YouTube write", "permission": "write",
                 "approval_required": True, "ready": False, "summary": "Upload"},
                {"id": "internal.hidden", "provider": "composio", "label": "Hidden",
                 "permission": "read", "approval_required": False,
                 "ready": True, "summary": "Not a shared capability"},
            ],
        }

    def test_projection_only_exposes_shared_capabilities(self):
        with mock.patch.object(registry, "registry", return_value=self._sample()):
            view = cosmos_registry.shared_integrations("andromeda", "ads")
        self.assertTrue(view["available"])
        self.assertEqual(view["agent"], "andromeda")
        self.assertEqual(view["division"], "ads")
        self.assertEqual(view["router"], "/api/integrations")
        self.assertEqual(
            [row["id"] for row in view["capabilities"]],
            ["shopee.catalog", "composio.youtube.write"],
        )

    def test_cosmos_api_route_exposes_projection(self):
        with mock.patch.object(registry, "registry", return_value=self._sample()):
            status, payload = cosmos_api.handle(
                "GET", "/api/cosmos/integrations",
                {"agent_id": ["polaris"], "division": ["youtube"]}, None,
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["router"], "/api/integrations")
        self.assertEqual(payload["integrations"]["agent"], "polaris")
        self.assertEqual(payload["integrations"]["division"], "youtube")

    def test_projection_degrades_honestly(self):
        with mock.patch.object(registry, "registry", side_effect=RuntimeError("boom")):
            view = cosmos_registry.shared_integrations()
        self.assertFalse(view["available"])
        self.assertIn("boom", view["error"])
        self.assertEqual(view["capabilities"], [])


class YoutubeAttributionPatchTestCase(unittest.TestCase):
    def test_get_read_passes_attribution_without_polluting_arguments(self):
        with mock.patch.object(youtube_api.youtube, "run",
                               return_value={"status": "ok"}) as run:
            status, _ = youtube_api.handle(
                "GET", "/api/integrations/youtube/run/list_channels",
                {"mine": ["true"], "part": ["snippet"],
                 "agent": ["andromeda"], "module": ["COSMOS ads"]},
                None,
            )
        self.assertEqual(status, 200)
        args = run.call_args[0][1]
        self.assertEqual(args, {"mine": "true", "part": "snippet"})
        self.assertEqual(run.call_args[1],
                         {"agent": "andromeda", "module": "COSMOS ads"})

    def test_post_passes_attribution_from_body(self):
        with mock.patch.object(youtube_api.youtube, "run",
                               return_value={"status": "ok"}) as run:
            status, _ = youtube_api.handle(
                "POST", "/api/integrations/youtube/run/list_channels", {},
                {"arguments": {"part": "snippet", "agent": "nested",
                               "module": "nested"},
                 "agent": "polaris", "module": "COSMOS youtube"},
            )
        self.assertEqual(status, 200)
        args = run.call_args[0][1]
        self.assertEqual(args, {"part": "snippet"})
        self.assertEqual(run.call_args[1],
                         {"agent": "polaris", "module": "COSMOS youtube"})


if __name__ == "__main__":
    unittest.main()
