"""Unit tests for ComfyUI integration utilities.

Tests the utility functions for getting ComfyUI server configuration
that respects --listen and --port command-line arguments.
"""

import sys
from unittest.mock import MagicMock, patch
import pytest


class TestGetComfyUIServerURL:
    """Test get_comfyui_server_url function."""

    def test_with_default_config(self):
        """Test with default localhost:8188 configuration."""
        # Mock PromptServer with default config
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'http://localhost:8188'

    def test_with_custom_port(self):
        """Test with custom port (--port 6006)."""
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 6006
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'http://localhost:6006'

    def test_with_lan_access(self):
        """Test with LAN access (--listen 0.0.0.0)."""
        mock_server = MagicMock()
        mock_server.address = '0.0.0.0'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            # 0.0.0.0 should be displayed as localhost for user
            assert result == 'http://localhost:8188'

    def test_with_ipv6_all_interfaces(self):
        """Test with IPv6 all interfaces (::)."""
        mock_server = MagicMock()
        mock_server.address = '::'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            # :: should be displayed as localhost for user
            assert result == 'http://localhost:8188'

    def test_with_specific_ip(self):
        """Test with specific IP address (--listen 192.168.1.100)."""
        mock_server = MagicMock()
        mock_server.address = '192.168.1.100'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            # Specific IP should be used as-is
            assert result == 'http://192.168.1.100:8188'

    def test_with_https(self):
        """Test with HTTPS/TLS enabled."""
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 8188
        mock_server.ssl_context = MagicMock()  # Non-None = TLS enabled

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'https://localhost:8188'

    def test_with_custom_port_and_ip(self):
        """Test with both custom IP and port."""
        mock_server = MagicMock()
        mock_server.address = '10.0.0.5'
        mock_server.port = 9000
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'http://10.0.0.5:9000'

    def test_fallback_when_server_not_available(self):
        """Test fallback to default when PromptServer not available."""
        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = None

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'http://127.0.0.1:8188'

    def test_fallback_when_import_fails(self):
        """Test fallback when server module can't be imported."""
        with patch('utils.comfyui_utils.PromptServer', side_effect=ImportError):
            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            assert result == 'http://127.0.0.1:8188'

    def test_fallback_when_missing_attributes(self):
        """Test fallback when PromptServer instance lacks attributes."""
        mock_server = MagicMock()
        # Simulate missing attributes
        delattr(mock_server, 'address')
        delattr(mock_server, 'port')

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            result = get_comfyui_server_url()

            # Should use defaults from getattr fallbacks
            assert result == 'http://localhost:8188'


class TestGetComfyUIAddressAndPort:
    """Test get_comfyui_address_and_port function."""

    def test_with_default_config(self):
        """Test with default configuration."""
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 8188

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_address_and_port
            address, port = get_comfyui_address_and_port()

            assert address == '127.0.0.1'
            assert port == 8188

    def test_with_custom_config(self):
        """Test with custom configuration."""
        mock_server = MagicMock()
        mock_server.address = '192.168.1.50'
        mock_server.port = 9090

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_address_and_port
            address, port = get_comfyui_address_and_port()

            assert address == '192.168.1.50'
            assert port == 9090

    def test_with_all_interfaces(self):
        """Test with 0.0.0.0 (all interfaces)."""
        mock_server = MagicMock()
        mock_server.address = '0.0.0.0'
        mock_server.port = 8188

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_address_and_port
            address, port = get_comfyui_address_and_port()

            assert address == '0.0.0.0'
            assert port == 8188

    def test_fallback_when_server_not_available(self):
        """Test fallback when PromptServer not available."""
        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = None

            from utils.comfyui_utils import get_comfyui_address_and_port
            address, port = get_comfyui_address_and_port()

            assert address == '127.0.0.1'
            assert port == 8188

    def test_fallback_when_import_fails(self):
        """Test fallback when import fails."""
        with patch('utils.comfyui_utils.PromptServer', side_effect=ImportError):
            from utils.comfyui_utils import get_comfyui_address_and_port
            address, port = get_comfyui_address_and_port()

            assert address == '127.0.0.1'
            assert port == 8188


class TestFormatServerAddress:
    """Test format_server_address function."""

    def test_default_address_and_port(self):
        """Test formatting default address and port."""
        from utils.comfyui_utils import format_server_address
        result = format_server_address('127.0.0.1', 8188)
        assert result == '127.0.0.1:8188'

    def test_custom_address_and_port(self):
        """Test formatting custom address and port."""
        from utils.comfyui_utils import format_server_address
        result = format_server_address('192.168.1.100', 6006)
        assert result == '192.168.1.100:6006'

    def test_all_interfaces(self):
        """Test formatting 0.0.0.0 address."""
        from utils.comfyui_utils import format_server_address
        result = format_server_address('0.0.0.0', 8188)
        assert result == '0.0.0.0:8188'

    def test_ipv6_address(self):
        """Test formatting IPv6 address."""
        from utils.comfyui_utils import format_server_address
        result = format_server_address('::', 8188)
        assert result == ':::8188'

    def test_localhost(self):
        """Test formatting localhost."""
        from utils.comfyui_utils import format_server_address
        result = format_server_address('localhost', 8000)
        assert result == 'localhost:8000'


class TestIntegrationScenarios:
    """Test real-world integration scenarios."""

    def test_default_comfyui_startup(self):
        """Test default ComfyUI startup scenario."""
        # Simulate: python main.py (no args)
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            assert url == 'http://localhost:8188'

    def test_custom_port_scenario(self):
        """Test custom port scenario."""
        # Simulate: python main.py --port 6006
        mock_server = MagicMock()
        mock_server.address = '127.0.0.1'
        mock_server.port = 6006
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            assert url == 'http://localhost:6006'

    def test_lan_access_scenario(self):
        """Test LAN access scenario."""
        # Simulate: python main.py --listen 0.0.0.0
        mock_server = MagicMock()
        mock_server.address = '0.0.0.0'
        mock_server.port = 8188
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            # Should show localhost for user-friendliness
            assert url == 'http://localhost:8188'

    def test_https_with_custom_port_scenario(self):
        """Test HTTPS with custom port scenario."""
        # Simulate: python main.py --port 8443 --tls-keyfile key.pem --tls-certfile cert.pem
        mock_server = MagicMock()
        mock_server.address = '0.0.0.0'
        mock_server.port = 8443
        mock_server.ssl_context = MagicMock()  # TLS enabled

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            assert url == 'https://localhost:8443'

    def test_specific_ip_scenario(self):
        """Test specific IP address scenario."""
        # Simulate: python main.py --listen 192.168.1.100 --port 9000
        mock_server = MagicMock()
        mock_server.address = '192.168.1.100'
        mock_server.port = 9000
        mock_server.ssl_context = None

        with patch('utils.comfyui_utils.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            assert url == 'http://192.168.1.100:9000'

    def test_running_outside_comfyui(self):
        """Test running outside ComfyUI context (e.g., tests)."""
        # Simulate: Running tests or standalone mode
        with patch('utils.comfyui_utils.PromptServer', side_effect=ImportError):
            from utils.comfyui_utils import get_comfyui_server_url
            url = get_comfyui_server_url()

            # Should gracefully fall back to default
            assert url == 'http://127.0.0.1:8188'
