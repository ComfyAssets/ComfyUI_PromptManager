"""Professional dark theme design system.

Provides consistent colors, typography, spacing, and component styling
for the ComfyUI PromptManager interface.
"""

from typing import Dict, Any


class ThemeColors:
    """Professional dark theme color palette."""
    
    # Background colors (darkest to lightest)
    BG_PRIMARY = "#0a0a0a"      # Main background
    BG_SECONDARY = "#1a1a1a"    # Card backgrounds  
    BG_TERTIARY = "#2a2a2a"     # Input backgrounds
    BG_HOVER = "#3a3a3a"        # Hover states
    
    # Text colors
    TEXT_PRIMARY = "#ffffff"     # Primary text
    TEXT_SECONDARY = "#888888"   # Secondary text
    TEXT_TERTIARY = "#666666"    # Muted text
    TEXT_DISABLED = "#444444"    # Disabled text
    
    # Accent colors (minimal, professional)
    ACCENT_PRIMARY = "#0066cc"   # Primary actions
    ACCENT_SUCCESS = "#00cc66"   # Success states
    ACCENT_WARNING = "#cc6600"   # Warning states  
    ACCENT_ERROR = "#cc0000"     # Error states
    
    # Border colors
    BORDER_PRIMARY = "#333333"   # Standard borders
    BORDER_SECONDARY = "#555555" # Focused borders
    BORDER_ACCENT = "#0066cc"    # Accent borders
    
    # Utility colors
    OVERLAY = "rgba(0, 0, 0, 0.8)"    # Modal overlays
    SHADOW = "rgba(0, 0, 0, 0.3)"     # Drop shadows
    TRANSPARENT = "transparent"


class ThemeTypography:
    """Typography scale and font definitions."""
    
    # Font families
    FONT_SYSTEM = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    FONT_MONO = "'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace"
    
    # Font sizes (rem units for scalability)
    SIZE_XS = "0.75rem"   # 12px
    SIZE_SM = "0.875rem"  # 14px  
    SIZE_BASE = "1rem"    # 16px
    SIZE_LG = "1.125rem"  # 18px
    SIZE_XL = "1.25rem"   # 20px
    SIZE_2XL = "1.5rem"   # 24px
    SIZE_3XL = "1.875rem" # 30px
    
    # Line heights
    LINE_TIGHT = "1.25"
    LINE_NORMAL = "1.5"
    LINE_RELAXED = "1.75"
    
    # Font weights
    WEIGHT_NORMAL = "400"
    WEIGHT_MEDIUM = "500"
    WEIGHT_SEMIBOLD = "600"
    WEIGHT_BOLD = "700"


class ThemeSpacing:
    """Consistent spacing scale."""
    
    XS = "0.25rem"    # 4px
    SM = "0.5rem"     # 8px
    BASE = "1rem"     # 16px
    LG = "1.5rem"     # 24px
    XL = "2rem"       # 32px
    XXL = "3rem"      # 48px
    XXXL = "4rem"     # 64px


class ThemeShadows:
    """Shadow definitions for depth and elevation."""
    
    NONE = "none"
    SM = "0 1px 2px rgba(0, 0, 0, 0.3)"
    BASE = "0 1px 3px rgba(0, 0, 0, 0.3), 0 1px 2px rgba(0, 0, 0, 0.3)"
    MD = "0 4px 6px rgba(0, 0, 0, 0.3), 0 2px 4px rgba(0, 0, 0, 0.3)"
    LG = "0 10px 15px rgba(0, 0, 0, 0.3), 0 4px 6px rgba(0, 0, 0, 0.3)"
    XL = "0 20px 25px rgba(0, 0, 0, 0.3), 0 10px 10px rgba(0, 0, 0, 0.3)"


class ThemeBorders:
    """Border radius and width definitions."""
    
    # Border radius
    RADIUS_NONE = "0"
    RADIUS_SM = "0.25rem"    # 4px
    RADIUS_BASE = "0.375rem" # 6px
    RADIUS_MD = "0.5rem"     # 8px  
    RADIUS_LG = "0.75rem"    # 12px
    RADIUS_FULL = "9999px"   # Fully rounded
    
    # Border widths
    WIDTH_DEFAULT = "1px"
    WIDTH_THICK = "2px"
    WIDTH_NONE = "0"


class ThemeTransitions:
    """Animation and transition definitions."""
    
    # Duration
    FAST = "150ms"
    BASE = "300ms"
    SLOW = "500ms"
    
    # Easing
    EASE_DEFAULT = "cubic-bezier(0.4, 0, 0.2, 1)"
    EASE_IN = "cubic-bezier(0.4, 0, 1, 1)"
    EASE_OUT = "cubic-bezier(0, 0, 0.2, 1)"
    EASE_IN_OUT = "cubic-bezier(0.4, 0, 0.2, 1)"
    
    # Common transitions
    ALL = f"all {BASE} {EASE_DEFAULT}"
    COLORS = f"background-color {BASE} {EASE_DEFAULT}, border-color {BASE} {EASE_DEFAULT}, color {BASE} {EASE_DEFAULT}"
    OPACITY = f"opacity {BASE} {EASE_DEFAULT}"
    TRANSFORM = f"transform {BASE} {EASE_DEFAULT}"


def get_css_variables() -> Dict[str, str]:
    """Get CSS custom properties for the theme.
    
    Returns:
        Dictionary of CSS variable names and values
    """
    return {
        # Colors
        "--color-bg-primary": ThemeColors.BG_PRIMARY,
        "--color-bg-secondary": ThemeColors.BG_SECONDARY,
        "--color-bg-tertiary": ThemeColors.BG_TERTIARY,
        "--color-bg-hover": ThemeColors.BG_HOVER,
        
        "--color-text-primary": ThemeColors.TEXT_PRIMARY,
        "--color-text-secondary": ThemeColors.TEXT_SECONDARY,
        "--color-text-tertiary": ThemeColors.TEXT_TERTIARY,
        "--color-text-disabled": ThemeColors.TEXT_DISABLED,
        
        "--color-accent-primary": ThemeColors.ACCENT_PRIMARY,
        "--color-accent-success": ThemeColors.ACCENT_SUCCESS,
        "--color-accent-warning": ThemeColors.ACCENT_WARNING,
        "--color-accent-error": ThemeColors.ACCENT_ERROR,
        
        "--color-border-primary": ThemeColors.BORDER_PRIMARY,
        "--color-border-secondary": ThemeColors.BORDER_SECONDARY,
        "--color-border-accent": ThemeColors.BORDER_ACCENT,
        
        "--color-overlay": ThemeColors.OVERLAY,
        "--color-shadow": ThemeColors.SHADOW,
        
        # Typography
        "--font-system": ThemeTypography.FONT_SYSTEM,
        "--font-mono": ThemeTypography.FONT_MONO,
        
        "--text-xs": ThemeTypography.SIZE_XS,
        "--text-sm": ThemeTypography.SIZE_SM,
        "--text-base": ThemeTypography.SIZE_BASE,
        "--text-lg": ThemeTypography.SIZE_LG,
        "--text-xl": ThemeTypography.SIZE_XL,
        "--text-2xl": ThemeTypography.SIZE_2XL,
        "--text-3xl": ThemeTypography.SIZE_3XL,
        
        "--line-tight": ThemeTypography.LINE_TIGHT,
        "--line-normal": ThemeTypography.LINE_NORMAL,
        "--line-relaxed": ThemeTypography.LINE_RELAXED,
        
        "--weight-normal": ThemeTypography.WEIGHT_NORMAL,
        "--weight-medium": ThemeTypography.WEIGHT_MEDIUM,
        "--weight-semibold": ThemeTypography.WEIGHT_SEMIBOLD,
        "--weight-bold": ThemeTypography.WEIGHT_BOLD,
        
        # Spacing
        "--space-xs": ThemeSpacing.XS,
        "--space-sm": ThemeSpacing.SM,
        "--space-base": ThemeSpacing.BASE,
        "--space-lg": ThemeSpacing.LG,
        "--space-xl": ThemeSpacing.XL,
        "--space-xxl": ThemeSpacing.XXL,
        "--space-xxxl": ThemeSpacing.XXXL,
        
        # Shadows
        "--shadow-sm": ThemeShadows.SM,
        "--shadow-base": ThemeShadows.BASE,
        "--shadow-md": ThemeShadows.MD,
        "--shadow-lg": ThemeShadows.LG,
        "--shadow-xl": ThemeShadows.XL,
        
        # Borders
        "--radius-sm": ThemeBorders.RADIUS_SM,
        "--radius-base": ThemeBorders.RADIUS_BASE,
        "--radius-md": ThemeBorders.RADIUS_MD,
        "--radius-lg": ThemeBorders.RADIUS_LG,
        "--radius-full": ThemeBorders.RADIUS_FULL,
        
        "--border-width": ThemeBorders.WIDTH_DEFAULT,
        "--border-thick": ThemeBorders.WIDTH_THICK,
        
        # Transitions
        "--transition-fast": ThemeTransitions.FAST,
        "--transition-base": ThemeTransitions.BASE,
        "--transition-slow": ThemeTransitions.SLOW,
        
        "--ease-default": ThemeTransitions.EASE_DEFAULT,
        "--ease-in": ThemeTransitions.EASE_IN,
        "--ease-out": ThemeTransitions.EASE_OUT,
        "--ease-in-out": ThemeTransitions.EASE_IN_OUT,
        
        "--transition-all": ThemeTransitions.ALL,
        "--transition-colors": ThemeTransitions.COLORS,
        "--transition-opacity": ThemeTransitions.OPACITY,
        "--transition-transform": ThemeTransitions.TRANSFORM,
    }


def generate_css_root() -> str:
    """Generate CSS :root declaration with theme variables.
    
    Returns:
        CSS string with :root selector and custom properties
    """
    variables = get_css_variables()
    css_props = [f"  {name}: {value};" for name, value in variables.items()]
    
    return f":root {{\n" + "\n".join(css_props) + "\n}"


def get_component_styles() -> Dict[str, str]:
    """Get pre-defined component styles using theme variables.
    
    Returns:
        Dictionary of component class names and CSS styles
    """
    return {
        # Base styles
        "pm-base": f"""
            font-family: var(--font-system);
            font-size: var(--text-base);
            line-height: var(--line-normal);
            color: var(--color-text-primary);
            background-color: var(--color-bg-primary);
        """,
        
        # Layout containers
        "pm-container": f"""
            width: 100%;
            max-width: 1200px;
            margin: 0 auto;
            padding: var(--space-base);
        """,
        
        "pm-card": f"""
            background-color: var(--color-bg-secondary);
            border: var(--border-width) solid var(--color-border-primary);
            border-radius: var(--radius-base);
            padding: var(--space-lg);
            box-shadow: var(--shadow-sm);
            transition: var(--transition-all);
        """,
        
        "pm-card:hover": f"""
            box-shadow: var(--shadow-md);
            border-color: var(--color-border-secondary);
        """,
        
        # Buttons
        "pm-button": f"""
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: var(--space-sm);
            padding: var(--space-sm) var(--space-base);
            border: var(--border-width) solid var(--color-border-primary);
            border-radius: var(--radius-base);
            background-color: var(--color-bg-tertiary);
            color: var(--color-text-primary);
            font-size: var(--text-sm);
            font-weight: var(--weight-medium);
            text-decoration: none;
            cursor: pointer;
            transition: var(--transition-colors);
        """,
        
        "pm-button:hover": f"""
            background-color: var(--color-bg-hover);
            border-color: var(--color-border-secondary);
        """,
        
        "pm-button-primary": f"""
            background-color: var(--color-accent-primary);
            border-color: var(--color-accent-primary);
            color: white;
        """,
        
        "pm-button-primary:hover": f"""
            background-color: color-mix(in srgb, var(--color-accent-primary) 80%, white);
        """,
        
        # Form elements
        "pm-input": f"""
            width: 100%;
            padding: var(--space-sm) var(--space-base);
            background-color: var(--color-bg-tertiary);
            border: var(--border-width) solid var(--color-border-primary);
            border-radius: var(--radius-base);
            color: var(--color-text-primary);
            font-size: var(--text-sm);
            transition: var(--transition-colors);
        """,
        
        "pm-input:focus": f"""
            outline: none;
            border-color: var(--color-border-accent);
            box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-accent-primary) 20%, transparent);
        """,
        
        # Typography
        "pm-heading": f"""
            color: var(--color-text-primary);
            font-weight: var(--weight-semibold);
            line-height: var(--line-tight);
            margin: 0 0 var(--space-base) 0;
        """,
        
        "pm-heading-xl": f"""
            font-size: var(--text-3xl);
        """,
        
        "pm-heading-lg": f"""
            font-size: var(--text-2xl);
        """,
        
        "pm-heading-md": f"""
            font-size: var(--text-xl);
        """,
        
        "pm-text-secondary": f"""
            color: var(--color-text-secondary);
        """,
        
        "pm-text-mono": f"""
            font-family: var(--font-mono);
        """,
        
        # Utilities
        "pm-hidden": "display: none;",
        "pm-sr-only": f"""
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        """,
    }


def generate_complete_css() -> str:
    """Generate complete CSS including theme variables and component styles.
    
    Returns:
        Complete CSS string ready for use
    """
    css_parts = [
        "/* ComfyUI PromptManager Professional Theme */",
        "",
        generate_css_root(),
        "",
        "/* Component Styles */",
    ]
    
    component_styles = get_component_styles()
    for selector, styles in component_styles.items():
        # Clean up multiline styles
        clean_styles = " ".join(line.strip() for line in styles.strip().split("\n"))
        css_parts.append(f".{selector} {{ {clean_styles} }}")
    
    return "\n".join(css_parts)