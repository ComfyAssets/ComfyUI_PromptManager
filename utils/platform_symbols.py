"""Cross-platform symbol utilities for terminal output.

Windows terminals don't render emojis well, so we provide ASCII alternatives.
"""
import platform
import sys


def is_windows():
    """Detect if running on Windows."""
    return platform.system() == 'Windows' or sys.platform == 'win32'


# Symbol mappings: emoji -> ASCII alternative
SYMBOLS = {
    # Status symbols
    '✅': '[✓]' if is_windows() else '✅',
    '❌': '[✗]' if is_windows() else '❌',
    '⚠️': '[!]' if is_windows() else '⚠️',
    '🚀': '[>>]' if is_windows() else '🚀',
    '📍': '[*]' if is_windows() else '📍',
    '🔍': '[?]' if is_windows() else '🔍',
    '⏳': '[~]' if is_windows() else '⏳',
    '⏭️': '[>>]' if is_windows() else '⏭️',
    '🫶': '[+]' if is_windows() else '🫶',

    # Node type symbols
    '🧠': '[PM]' if is_windows() else '🧠',
    '📝': '[TR]' if is_windows() else '📝',
    '🖼️': '[IMG]' if is_windows() else '🖼️',
}


def get_symbol(emoji):
    """Get platform-appropriate symbol for an emoji.

    Args:
        emoji: The emoji character to convert

    Returns:
        Platform-appropriate symbol (emoji on Mac/Linux, ASCII on Windows)
    """
    return SYMBOLS.get(emoji, emoji)


def format_with_symbols(text):
    """Replace all emojis in text with platform-appropriate symbols.

    Args:
        text: Text containing emojis

    Returns:
        Text with emojis replaced by platform-appropriate symbols
    """
    result = text
    for emoji, symbol in SYMBOLS.items():
        result = result.replace(emoji, symbol)
    return result


# Convenience functions for common symbols
def check():
    """Return platform-appropriate checkmark symbol."""
    return SYMBOLS['✅']


def cross():
    """Return platform-appropriate error/cross symbol."""
    return SYMBOLS['❌']


def warning():
    """Return platform-appropriate warning symbol."""
    return SYMBOLS['⚠️']


def rocket():
    """Return platform-appropriate rocket symbol."""
    return SYMBOLS['🚀']


def pin():
    """Return platform-appropriate location pin symbol."""
    return SYMBOLS['📍']


def search():
    """Return platform-appropriate search symbol."""
    return SYMBOLS['🔍']


def hourglass():
    """Return platform-appropriate hourglass symbol."""
    return SYMBOLS['⏳']


def skip():
    """Return platform-appropriate skip symbol."""
    return SYMBOLS['⏭️']


def hands():
    """Return platform-appropriate hands symbol."""
    return SYMBOLS['🫶']


def brain():
    """Return platform-appropriate brain symbol (for Prompt Manager)."""
    return SYMBOLS['🧠']


def tracker():
    """Return platform-appropriate tracker symbol."""
    return SYMBOLS['📝']


def image():
    """Return platform-appropriate image symbol."""
    return SYMBOLS['🖼️']
