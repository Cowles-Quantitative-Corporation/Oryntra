import unittest

from fastapi.testclient import TestClient

from backend.main import app


class QuantPublicRouteTests(unittest.TestCase):
    def test_authenticated_browser_upload_route_is_available_without_public_admin_routes(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/api/quant/run-upload", paths)
        self.assertNotIn("/api/quant/run", paths)

    def test_public_quant_route_rejects_internal_model_configuration(self):
        with TestClient(app) as client:
            response = client.post(
                "/api/quant/run-upload",
                json={"tickers": ["SPY", "QQQ"], "model": "universal_v2"},
            )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
