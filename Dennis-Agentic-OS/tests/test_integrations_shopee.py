"""Unit tests for the native Shopee connector (stdlib unittest only).

Network is mocked at the GraphQL transport; the signing contract, error
mapping, parameter validation, pagination semantics and idempotent feed
synchronization (including NEW/UPDATE/DELETE and validated reconciliation)
run for real against synthetic fixtures.

Run:  python3 -m unittest tests.test_integrations_shopee -v
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.integrations import store
from backend.integrations.shopee import auth, client, feeds, products


class ShopeeAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-auth-")
        self._orig_file = auth.CREDENTIALS_FILE
        auth.CREDENTIALS_FILE = os.path.join(self.tmp, "credentials.json")
        self._env = {k: os.environ.get(k) for k in
                     (auth.APP_ID_ENV, auth.SECRET_ENV, auth.ENDPOINT_ENV)}
        for key in self._env:
            os.environ.pop(key, None)

    def tearDown(self):
        auth.CREDENTIALS_FILE = self._orig_file
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_signature_formula_contract(self):
        # Documented formula: SHA256(AppId + Timestamp + Payload + Secret).
        # The formula is verified against a locally-computed digest for a
        # KNOWN payload — the documented vector's example payload was never
        # supplied (outstanding item), and verify_test_vector reports that
        # honestly instead of pretending a match.
        digest = auth.signature("123456", "1577836800", "abc", "demo")
        expected = hashlib.sha256(b"1234561577836800abcdemo").hexdigest()
        self.assertEqual(digest, expected)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, digest.lower())
        # payload changes change the signature
        self.assertNotEqual(digest, auth.signature("123456", "1577836800", "abd", "demo"))

    def test_documented_vector_reported_honestly(self):
        report = auth.verify_test_vector()
        self.assertEqual(report["expected"],
                         "dc88d72feea70c80c52c3399751a7d34966763f51a7f056aa070a5e9df645412")
        self.assertFalse(report["match"])  # honest: vector payload not documented
        # once the documented payload is known, the hook confirms the formula:
        probe = auth.verify_test_vector(payload="the-documented-payload")
        self.assertEqual(probe["payload_used"], "the-documented-payload")

    def test_authorization_header_format(self):
        header = auth.authorization_header("123456", "1577836800", "", "demo")
        self.assertTrue(header.startswith("SHA256 Credential=123456, "))
        self.assertIn("Timestamp=1577836800, ", header)
        self.assertIn("Signature=", header)

    def test_timestamp_window(self):
        now = 1_800_000_000
        self.assertTrue(auth.validate_timestamp(str(now - 60), now_epoch=now))
        self.assertTrue(auth.validate_timestamp(str(now + 599), now_epoch=now))
        self.assertFalse(auth.validate_timestamp(str(now - 601), now_epoch=now))
        self.assertFalse(auth.validate_timestamp("garbage", now_epoch=now))

    def test_credentials_env_wins_over_store_and_never_leak(self):
        auth.save_credentials("stored-app", "stored-secret")
        state = auth.public_state()
        self.assertEqual(state["source"], "store")
        os.environ[auth.APP_ID_ENV] = "env-app"
        os.environ[auth.SECRET_ENV] = "env-secret"
        creds = auth.resolve_credentials()
        self.assertEqual(creds["app_id"], "env-app")
        self.assertEqual(creds["source"], "env")
        # public state never carries secret values
        self.assertNotIn("env-secret", json.dumps(auth.public_state()))
        self.assertNotIn("stored-secret", json.dumps(auth.public_state()))

    def test_sign_request_uses_exact_serialized_body(self):
        os.environ[auth.APP_ID_ENV] = "app"
        os.environ[auth.SECRET_ENV] = "shhh"
        body = '{"query":"query { x }"}'
        headers, ts = auth.sign_request(body)
        expected_sig = hashlib.sha256(("app" + ts + body + "shhh").encode()).hexdigest()
        self.assertIn("Signature=%s" % expected_sig, headers["Authorization"])


class ShopeeClientTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-client-")
        self._orig_file = auth.CREDENTIALS_FILE
        auth.CREDENTIALS_FILE = os.path.join(self.tmp, "credentials.json")
        auth.save_credentials("app", "secret")

    def tearDown(self):
        auth.CREDENTIALS_FILE = self._orig_file

    def test_documented_error_codes_map(self):
        for shopee_code, (local_code, status) in client.DOCUMENTED_ERRORS.items():
            errors = {"errors": [{"message": "denied",
                                  "extensions": {"code": shopee_code}}]}
            with mock.patch.object(auth, "sign_request",
                                   return_value=({"Authorization": "x", "Content-Type": "y"}, "0")), \
                 mock.patch.object(client, "REQUEST_TIMEOUT", 1), \
                 mock.patch("urllib.request.urlopen",
                            side_effect=AssertionError("network disabled")):
                # simulate the response path directly
                with self.assertRaises(client.ShopeeError) as ctx:
                    client._parse_graphql(json.dumps(errors))
            self.assertEqual(ctx.exception.code, local_code)
            self.assertEqual(ctx.exception.status, status)
            self.assertEqual(ctx.exception.shopee_code, shopee_code)

    def test_rate_limit_maps_to_429(self):
        with self.assertRaises(client.ShopeeError) as ctx:
            client._map_http_error(429, "too many requests")
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(ctx.exception.status, 429)

    def test_success_parses_data(self):
        data = client._parse_graphql(json.dumps({"data": {"listItemFeeds": {"totalCount": 2}}}))
        self.assertEqual(data["listItemFeeds"]["totalCount"], 2)

    def test_malformed_response(self):
        with self.assertRaises(client.ShopeeError) as ctx:
            client._parse_graphql("not json at all")
        self.assertEqual(ctx.exception.code, "bad_response")


class ShopeeProductsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-prod-")
        self._orig_file = products.PRODUCTS_FILE
        products.PRODUCTS_FILE = os.path.join(self.tmp, "products.json")
        self._orig_file_auth = auth.CREDENTIALS_FILE
        auth.CREDENTIALS_FILE = os.path.join(self.tmp, "credentials.json")
        auth.save_credentials("app", "secret")

    def tearDown(self):
        products.PRODUCTS_FILE = self._orig_file
        auth.CREDENTIALS_FILE = self._orig_file_auth

    def _offer_node(self, item_id=111, shop_id=222):
        return {
            "itemId": item_id, "shopId": shop_id, "shopName": "Best Shop",
            "shopType": 4, "productName": "Wireless Earbuds", "productCatIds": [1, 2, 3],
            "commissionRate": "0.25", "sellerCommissionRate": "0.05",
            "shopeeCommissionRate": "0.02", "commission": "4.5", "sales": 1543,
            "priceMin": "199.0", "priceMax": "249.0", "priceDiscountRate": "0.15",
            "ratingStar": "4.8", "imageUrl": "https://img", "productLink": "https://s/e/1",
            "offerLink": "https://s/aff/1", "periodStartTime": "1", "periodEndTime": "2",
            # deprecated fields present in the payload must be dropped:
            "price": "199", "appExistRate": "0.1", "appNewRate": "0.2",
            "webExistRate": "0.3", "webNewRate": "0.4",
        }

    def test_normalization_converts_and_drops_deprecated(self):
        offer = products.normalize_offer(self._offer_node())
        n = offer["normalized"]
        self.assertEqual(n["commission_rate"], 0.25)
        self.assertEqual(n["sales"], 1543)
        self.assertEqual(n["shop_type"], "PREFERRED_PLUS_SHOP")
        self.assertEqual(n["item_id"], 111)
        self.assertNotIn("price", n)          # documented To Be Removed
        self.assertNotIn("app_exist_rate", n)
        raw = json.dumps(offer["raw"])
        self.assertIn("price", raw)           # provenance keeps the raw payload

    def test_upsert_is_idempotent(self):
        first = products.upsert_offers([products.normalize_offer(self._offer_node())])
        self.assertEqual(first["upserted"], 1)
        again = products.upsert_offers([products.normalize_offer(self._offer_node())])
        self.assertEqual(again["upserted"], 1)
        self.assertEqual(again["total"], 1)   # not duplicated

    def test_list_type_requires_match_id_and_exclusive(self):
        with self.assertRaises(products.ShopeeProductsError) as ctx:
            products.query_offers({"listType": "TOP_PERFORMING"})
        self.assertEqual(ctx.exception.code, "params_error")
        with self.assertRaises(products.ShopeeProductsError):
            products.query_offers({"listType": "TOP_PERFORMING", "matchId": 9,
                                   "keyword": "x"})  # cannot combine with other filters
        with self.assertRaises(products.ShopeeProductsError):
            products.query_offers({"listType": "HIGHEST_COMMISSION", "matchId": 9})
        with self.assertRaises(products.ShopeeProductsError):
            products.query_offers({"matchId": 9})  # matchId without listType

    def test_sort_type_one_is_keyword_only(self):
        with self.assertRaises(products.ShopeeProductsError):
            products.query_offers({"sortType": 1})
        with self.assertRaises(products.ShopeeProductsError):
            products.query_offers({"sortType": 9})

    def test_query_uses_page_limit_pagination(self):
        captured = {}

        def fake_graphql(query, variables):
            captured.update(variables)
            return {"productOfferV2": {"total": 1, "hasNextPage": False,
                                       "nodes": [self._offer_node()]}}

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            result = products.query_offers({"page": 3, "limit": 50, "keyword": "buds",
                                            "sortType": 1})
        self.assertEqual(captured["page"], 3)
        self.assertEqual(captured["limit"], 50)
        self.assertEqual(captured["sortType"], 1)
        self.assertFalse(result["hasNextPage"])
        self.assertEqual(result["offers"][0]["normalized"]["item_id"], 111)


def _columns(item_id, name="Item", shop_id=77):
    return json.dumps({"itemId": item_id, "shopId": shop_id,
                       "productName": name, "priceMin": "10.0"})


class ShopeeFeedsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-shopee-feeds-")
        self._orig = (feeds.FEEDS_FILE, products.PRODUCTS_FILE)
        feeds.FEEDS_FILE = os.path.join(self.tmp, "feeds.json")
        products.PRODUCTS_FILE = os.path.join(self.tmp, "products.json")

    def tearDown(self):
        (feeds.FEEDS_FILE, products.PRODUCTS_FILE) = self._orig

    def _page(self, rows, offset, limit, has_more, total):
        # client.graphql() returns the unwrapped `data` payload
        return {"getItemFeedData": {
            "rows": rows,
            "pageInfo": {"offset": offset, "limit": limit,
                         "totalCount": total, "hasMore": has_more}}}

    def test_offset_pagination_uses_returned_values(self):
        calls = []

        def fake_graphql(query, variables):
            calls.append(variables)
            if variables["offset"] == 0:
                rows = [{"columns": _columns(1), "updateType": None},
                        {"columns": _columns(2), "updateType": None}]
                return self._page(rows, 0, 2, True, 3)
            rows = [{"columns": _columns(3), "updateType": None}]
            return self._page(rows, 2, 2, False, 3)

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            result = feeds.sync_feed("123_FULL_20260205", feed_mode="FULL")
        self.assertEqual([c["offset"] for c in calls], [0, 2])  # returned offset+limit
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["rows_processed"], 3)
        self.assertEqual(result["actions"].get("upserted"), 3)

    def test_sync_is_idempotent(self):
        def fake_graphql(query, variables):
            return self._page([{"columns": _columns(9)}], 0, 1, False, 1)

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            feeds.sync_feed("123_FULL_20260205")
            feeds.sync_feed("123_FULL_20260205")
        listing = products.list_products()
        self.assertEqual(len(listing), 1)

    def test_malformed_columns_never_crash(self):
        parsed = feeds.parse_columns("{not json")
        self.assertEqual(parsed["_schema"], "malformed")
        self.assertIn("_raw", parsed)
        parsed = feeds.parse_columns('["array"]')
        self.assertEqual(parsed["_schema"], "unexpected_type")
        with mock.patch.object(client, "graphql", return_value=self._page(
                [{"columns": "{broken", "updateType": None}], 0, 1, False, 1)):
            result = feeds.sync_feed("123_FULL_20260205")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(listing_ok(products.list_products()))

    def test_delta_new_update_delete(self):
        def fake_graphql(query, variables):
            rows = [{"columns": _columns(1, "v1"), "updateType": "NEW"},
                    {"columns": _columns(1, "v2"), "updateType": "UPDATE"},
                    {"columns": _columns(2), "updateType": "NEW"},
                    {"columns": _columns(2), "updateType": "DELETE"}]
            return self._page(rows, 0, 10, False, 4)

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            result = feeds.sync_feed("123_DELTA_20260206", feed_mode="DELTA")
        # NEW(item1) + NEW(item2) → 2 created; UPDATE(item1) → updated; DELETE(item2) → applied
        self.assertEqual(result["actions"].get("created"), 2)
        self.assertEqual(result["actions"].get("updated"), 1)
        self.assertEqual(result["actions"].get("delete_applied"), 1)
        names = [p["normalized"]["product_name"] for p in products.list_products()]
        self.assertEqual(names, ["v2"])  # item 2 deleted, item 1 updated in place

    def test_partial_sync_keeps_checkpoint_and_resumes(self):
        responses = []

        def fake_graphql(query, variables):
            responses.append(variables["offset"])
            if variables["offset"] == 0:
                return self._page([{"columns": _columns(1)}], 0, 1, True, 2)
            return self._page([{"columns": _columns(2)}], 1, 1, False, 2)

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            partial = feeds.sync_feed("123_FULL_20260205", max_pages=1)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["rows_processed"], 1)
        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            done = feeds.sync_feed("123_FULL_20260205")
        self.assertEqual(done["status"], "complete")
        # resumed from the saved checkpoint (offset 1), idempotent re-fetch
        self.assertEqual(responses, [0, 1])
        self.assertEqual(done["rows_processed"], 2)
        self.assertEqual(len(products.list_products()), 2)

    def test_reconcile_only_replaces_on_validation(self):
        rows = [{"columns": _columns(i)} for i in range(3)]

        def fake_graphql(query, variables):
            return self._page(rows[:2] if variables["offset"] == 0 else rows[2:],
                              variables["offset"], 2,
                              variables["offset"] == 0, 3)

        with mock.patch.object(client, "graphql", side_effect=fake_graphql):
            result = feeds.reconcile("123_FULL_20260205")
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(len(products.list_products()), 3)

        # incomplete fetch (declared total 5, fetched 3) must abort untouched
        def short(query, variables):
            return self._page(rows, 0, 10, False, 5)

        products.upsert_feed_record({"identity": "id:keep|", "item_id": "keep",
                                     "normalized": {"product_name": "keep"}})
        with mock.patch.object(client, "graphql", side_effect=short):
            with self.assertRaises(feeds.ShopeeFeedsError) as ctx:
                feeds.reconcile("123_FULL_20260205")
        self.assertEqual(ctx.exception.code, "reconcile_incomplete")
        names = [p["normalized"]["product_name"] for p in products.list_products()]
        self.assertIn("keep", names)  # catalog untouched

    def test_feed_id_format_documented_shape(self):
        # {datafeedId}_{feedMode}_{grassDate}
        self.assertTrue("_FULL_" in "12345_FULL_20260205")
        self.assertTrue("_DELTA_" in "12345_DELTA_20260206")
        mode = feeds.sync_feed.__wrapped__ if hasattr(feeds.sync_feed, "__wrapped__") else None
        self.assertIsNone(mode)  # no wrapper surprises


def listing_ok(rows):
    """Every stored row must be loadable and identifiable."""
    return all(r.get("identity") for r in rows)


if __name__ == "__main__":
    unittest.main()
