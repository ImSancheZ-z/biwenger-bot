import unittest
from unittest.mock import Mock, patch
import requests
from biwenger.client import BiwengerClient, BiwengerCatalogError, CDN_BASE, AUTH_BASE


def response(status, payload=None):
    return Mock(status_code=status, json=Mock(return_value=payload))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = BiwengerClient("", "")
        self.payload = {"data": {"players": {"1": {"id": 1}}}}

    @patch("biwenger.client.requests.get")
    def test_fallback_and_no_private_headers(self, get):
        self.client._session.headers["Authorization"] = "private"
        get.side_effect = [response(403), response(200, self.payload)]
        self.assertEqual(self.client.get_competition_data(), self.payload)
        self.assertTrue(get.call_args_list[0].args[0].startswith(CDN_BASE))
        self.assertTrue(get.call_args_list[1].args[0].startswith(AUTH_BASE))
        for call in get.call_args_list:
            self.assertNotIn("Authorization", call.kwargs["headers"])
            self.assertEqual(call.kwargs["params"], {"lang": "es", "score": 5})

    @patch("biwenger.client.requests.get")
    def test_primary_success_does_not_duplicate_request(self, get):
        get.return_value = response(200, self.payload)
        self.assertEqual(self.client.get_competition_data(), self.payload)
        self.assertEqual(get.call_count, 1)

    @patch("biwenger.client.requests.get")
    def test_transient_network_and_invalid_payload(self, get):
        for first in (requests.Timeout(), response(503), response(200, {})):
            get.side_effect = [first, response(200, self.payload)]
            self.assertEqual(self.client.get_competition_data(), self.payload)

    @patch("biwenger.client.requests.get")
    def test_rate_limit_not_retried(self, get):
        get.return_value = response(429)
        with self.assertRaisesRegex(BiwengerCatalogError, "HTTP 429"):
            self.client.get_competition_data()
        self.assertEqual(get.call_count, 1)

    @patch("biwenger.client.requests.get")
    def test_all_hosts_fail_cleanly(self, get):
        get.side_effect = [response(403), response(502)]
        with self.assertRaisesRegex(BiwengerCatalogError, "HTTP 403.*HTTP 502"):
            self.client.get_competition_data()
