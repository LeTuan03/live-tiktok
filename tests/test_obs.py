"""Unit tests for OBS integration and test_connection."""

import unittest
from unittest.mock import MagicMock, patch

from app.integrations.obs.client import OBSClient, HAS_OBSWS


class TestOBSIntegration(unittest.TestCase):
    """Test suite for OBSClient and OBS connection testing."""

    def test_obs_client_init_defaults(self):
        client = OBSClient()
        self.assertEqual(client.host, "localhost")
        self.assertEqual(client.port, 4455)
        self.assertEqual(client.password, "")
        self.assertFalse(client.is_connected)
        self.assertIsNone(client.req_client)

    def test_obs_client_init_custom(self):
        client = OBSClient(host="192.168.1.100", port=4444, password="secret_password")
        self.assertEqual(client.host, "192.168.1.100")
        self.assertEqual(client.port, 4444)
        self.assertEqual(client.password, "secret_password")

    @patch("obsws_python.ReqClient")
    def test_test_connection_success(self, mock_req_client_cls):
        # Mock client instance
        mock_client = MagicMock()
        mock_req_client_cls.return_value = mock_client

        # Mock get_version response
        mock_version = MagicMock()
        mock_version.obs_version = "30.0.2"
        mock_version.obs_web_socket_version = "5.3.0"
        mock_version.platform = "Windows"
        mock_client.get_version.return_value = mock_version

        # Mock get_scene_list response
        mock_scene_list = MagicMock()
        mock_scene_list.scenes = [{"sceneName": "Scene Live"}, {"sceneName": "Scene Product"}]
        mock_scene_list.current_program_scene_name = "Scene Live"
        mock_client.get_scene_list.return_value = mock_scene_list

        client = OBSClient(host="localhost", port=4455, password="pass")
        success, message, details = client.test_connection()

        self.assertTrue(success)
        self.assertIn("30.0.2", message)
        self.assertIn("v5.3.0", message)
        self.assertIn("Scene Live", message)
        self.assertEqual(details["obs_version"], "30.0.2")
        self.assertEqual(details["ws_version"], "5.3.0")
        self.assertEqual(len(details["scenes"]), 2)
        mock_client.disconnect.assert_called()

    @patch("obsws_python.ReqClient")
    def test_test_connection_refused(self, mock_req_client_cls):
        mock_req_client_cls.side_effect = ConnectionRefusedError("[WinError 10061] No connection could be made")

        client = OBSClient(host="localhost", port=4455)
        success, message, details = client.test_connection()

        self.assertFalse(success)
        self.assertEqual(details, {})
        self.assertIn("10061", message)
        self.assertIn("OBS Studio", message)
        self.assertIn("WebSocket Server", message)

    @patch("obsws_python.ReqClient")
    def test_test_connection_auth_failure(self, mock_req_client_cls):
        mock_req_client_cls.side_effect = Exception("Authentication failed: invalid password")

        client = OBSClient(host="localhost", port=4455, password="wrong_password")
        success, message, details = client.test_connection()

        self.assertFalse(success)
        self.assertIn("Mật khẩu", message)

    @patch("obsws_python.ReqClient")
    def test_connect_and_disconnect(self, mock_req_client_cls):
        mock_client = MagicMock()
        mock_req_client_cls.return_value = mock_client

        client = OBSClient(host="localhost", port=4455)
        result = client.connect()

        self.assertTrue(result)
        self.assertTrue(client.is_connected)
        self.assertEqual(client.req_client, mock_client)

        client.disconnect()
        self.assertFalse(client.is_connected)
        self.assertIsNone(client.req_client)
        mock_client.disconnect.assert_called()

    def test_mock_fallback_scenes_when_disconnected(self):
        client = OBSClient()
        scenes = client.get_scenes()
        self.assertIsInstance(scenes, list)
        self.assertGreaterEqual(len(scenes), 1)


if __name__ == "__main__":
    unittest.main()
