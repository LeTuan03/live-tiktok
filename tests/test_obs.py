"""OBS client tests — connection failures must never look like success."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.integrations.obs.client import OBSClient


def test_defaults():
    client = OBSClient()
    assert (client.host, client.port, client.password) == ("localhost", 4455, "")
    assert client.is_connected is False and client.req_client is None


def test_disconnected_client_returns_no_fake_data():
    client = OBSClient()
    assert client.get_scenes() == []
    assert client.set_current_scene("Any") is False
    assert client.is_streaming() is None
    assert client.set_media_source_file("LiveFlow Video", "x.mp4") is False
    assert client.set_text("LiveFlow Text", "hi") is False
    status = client.get_status()
    assert status.connected is False and status.scenes == []
    ok, msg = client.start_stream()
    assert ok is False and "Chưa kết nối" in msg
    ok, msg = client.ensure_liveflow_setup("S", "V", "A")
    assert ok is False


@patch("obsws_python.ReqClient")
def test_test_connection_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    version = MagicMock(obs_version="30.0.2", obs_web_socket_version="5.3.0", platform="Windows")
    mock_client.get_version.return_value = version
    scenes = MagicMock(scenes=[{"sceneName": "Scene Live"}, {"sceneName": "Scene Product"}], current_program_scene_name="Scene Live")
    mock_client.get_scene_list.return_value = scenes

    success, message, details = OBSClient(password="pass").test_connection()
    assert success and "30.0.2" in message and "v5.3.0" in message
    assert details["scenes"] == ["Scene Product", "Scene Live"]  # OBS order reversed to match UI
    mock_client.disconnect.assert_called()


@patch("obsws_python.ReqClient")
def test_test_connection_refused_has_hint(mock_cls):
    mock_cls.side_effect = ConnectionRefusedError("[WinError 10061] No connection could be made")
    success, message, details = OBSClient().test_connection()
    assert not success and details == {}
    assert "10061" in message and "WebSocket" in message


@patch("obsws_python.ReqClient")
def test_test_connection_auth_failure(mock_cls):
    mock_cls.side_effect = Exception("Authentication failed: invalid password")
    success, message, _ = OBSClient(password="wrong").test_connection()
    assert not success and "Mật khẩu" in message


@patch("obsws_python.ReqClient")
def test_connect_verifies_handshake_and_disconnect(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    client = OBSClient()
    assert client.connect() is True and client.is_connected
    mock_client.get_version.assert_called()  # round-trip executed
    assert client.set_current_scene("A") is True
    mock_client.set_current_program_scene.assert_called_with("A")
    client.disconnect()
    assert not client.is_connected and client.req_client is None
    mock_client.disconnect.assert_called()


@patch("obsws_python.ReqClient")
def test_connect_failure_sets_error(mock_cls):
    mock_cls.side_effect = ConnectionRefusedError("refused")
    client = OBSClient()
    assert client.connect() is False
    assert "refused" in client.last_error


@patch("obsws_python.ReqClient")
def test_start_stream_requires_obs_confirmation(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    stream = MagicMock(output_active=False)
    mock_client.get_stream_status.return_value = stream
    client = OBSClient()
    client.connect()
    with patch("app.integrations.obs.client.time.sleep"):
        ok, msg = client.start_stream()
    assert ok is False and "chưa xác nhận" in msg.lower()
    stream.output_active = True
    ok, msg = client.start_stream()
    assert ok is True


@patch("obsws_python.ReqClient")
def test_ensure_setup_only_creates_missing(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value = mock_client
    mock_client.get_scene_list.return_value = MagicMock(scenes=[{"sceneName": "LiveFlow AI"}])
    mock_client.get_input_list.return_value = MagicMock(inputs=[{"inputName": "LiveFlow Video"}])
    mock_client.get_input_kind_list.return_value = MagicMock(input_kinds=["text_gdiplus_v3", "image_source", "ffmpeg_source"])
    client = OBSClient()
    client.connect()
    ok, msg = client.ensure_liveflow_setup("LiveFlow AI", "LiveFlow Video", "LiveFlow Audio", "LiveFlow Image", "LiveFlow Text")
    assert ok
    mock_client.create_scene.assert_not_called()
    created = [c.args[1] for c in mock_client.create_input.call_args_list]
    assert "LiveFlow Video" not in created
    assert {"LiveFlow Audio", "LiveFlow Image", "LiveFlow Text"} <= set(created)
