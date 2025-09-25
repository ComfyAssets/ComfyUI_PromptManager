#!/usr/bin/env python3
"""
Generate favicon and app icons for PromptManager
Creates various sizes needed for web app manifest and browser support
"""

import os
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path


def create_base_icon(size=512):
    """Create a base icon with PM letters"""
    # Create a new image with dark background
    img = Image.new('RGBA', (size, size), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)

    # Draw a rounded rectangle background
    margin = size // 10
    radius = size // 8

    # Draw background shape
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(0, 102, 204, 255),  # Professional blue
        outline=(0, 82, 184, 255),
        width=max(1, size // 100)
    )

    # Draw "PM" text
    text = "PM"
    font_size = size // 3

    # Try to use a nice font, fallback to default if not available
    try:
        # Try to find a good font on the system
        font_paths = [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:\\Windows\\Fonts\\Arial.ttf"
        ]

        font = None
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except:
                    continue

        if not font:
            # Use default font if no system font found
            font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    # Get text bounding box for centering
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Center the text
    x = (size - text_width) // 2
    y = (size - text_height) // 2 - size // 20  # Slightly higher for visual balance

    # Draw text with shadow for depth
    shadow_offset = max(2, size // 100)
    draw.text((x + shadow_offset, y + shadow_offset), text, fill=(0, 0, 0, 128), font=font)
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)

    return img


def generate_icons():
    """Generate all required icon sizes"""
    # Create images directory if it doesn't exist
    base_dir = Path(__file__).parent.parent
    images_dir = base_dir / "web" / "images"
    images_dir.mkdir(exist_ok=True)

    # Create base icon at high resolution
    base_icon = create_base_icon(512)

    # Icon sizes needed for various purposes
    icon_sizes = [
        # PWA manifest icons
        (72, "icon-72x72.png"),
        (96, "icon-96x96.png"),
        (128, "icon-128x128.png"),
        (144, "icon-144x144.png"),
        (152, "icon-152x152.png"),
        (192, "icon-192x192.png"),
        (384, "icon-384x384.png"),
        (512, "icon-512x512.png"),

        # Favicon sizes
        (16, "favicon-16x16.png"),
        (32, "favicon-32x32.png"),
        (48, "favicon-48x48.png"),

        # Apple Touch Icon
        (180, "apple-touch-icon.png"),

        # Shortcut icons for manifest
        (96, "dashboard-96x96.png"),
        (96, "gallery-96x96.png"),
        (96, "collections-96x96.png"),
    ]

    # Generate each size
    for size, filename in icon_sizes:
        # For shortcut icons, create variations
        if filename.startswith("dashboard"):
            icon = create_icon_variant(size, "D", (0, 102, 204))
        elif filename.startswith("gallery"):
            icon = create_icon_variant(size, "G", (0, 153, 51))
        elif filename.startswith("collections"):
            icon = create_icon_variant(size, "C", (153, 51, 255))
        else:
            # Resize base icon
            icon = base_icon.resize((size, size), Image.Resampling.LANCZOS)

        # Save the icon
        output_path = images_dir / filename
        icon.save(output_path, "PNG")
        print(f"Created: {output_path}")

    # Create ICO file with multiple sizes
    create_favicon_ico(base_icon, base_dir / "web" / "favicon.ico")

    # Create placeholder image
    create_placeholder_image(images_dir / "placeholder.png")

    # Create OG and Twitter card images
    create_social_images(images_dir)

    print("\n✅ All icons generated successfully!")


def create_icon_variant(size, letter, color):
    """Create icon variant with different letter and color"""
    img = Image.new('RGBA', (size, size), (10, 10, 10, 255))
    draw = ImageDraw.Draw(img)

    margin = size // 10
    radius = size // 8

    # Draw background shape with custom color
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(*color, 255),
        outline=tuple(int(c * 0.8) for c in color) + (255,),
        width=max(1, size // 100)
    )

    # Draw letter
    font_size = size // 2
    try:
        font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), letter, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (size - text_width) // 2
    y = (size - text_height) // 2 - size // 20

    draw.text((x, y), letter, fill=(255, 255, 255, 255), font=font)

    return img


def create_favicon_ico(base_icon, output_path):
    """Create ICO file with multiple sizes"""
    # ICO sizes
    sizes = [(16, 16), (32, 32), (48, 48)]
    icons = []

    for size in sizes:
        icon = base_icon.resize(size, Image.Resampling.LANCZOS)
        icons.append(icon)

    # Save as ICO
    icons[0].save(output_path, format='ICO', sizes=sizes)
    print(f"Created: {output_path}")


def create_placeholder_image(output_path):
    """Create a placeholder image for missing images"""
    img = Image.new('RGBA', (400, 300), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)

    # Draw border
    draw.rectangle([0, 0, 399, 299], outline=(60, 60, 60, 255), width=2)

    # Draw text
    text = "No Image"
    try:
        font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = (400 - text_width) // 2
    y = (300 - text_height) // 2

    draw.text((x, y), text, fill=(100, 100, 100, 255), font=font)

    img.save(output_path, "PNG")
    print(f"Created: {output_path}")


def create_social_images(images_dir):
    """Create Open Graph and Twitter card images"""
    # OG Image (1200x630)
    og_img = Image.new('RGBA', (1200, 630), (10, 10, 10, 255))
    draw = ImageDraw.Draw(og_img)

    # Draw large PM logo on left
    draw.rounded_rectangle(
        [100, 165, 400, 465],
        radius=50,
        fill=(0, 102, 204, 255),
        outline=(0, 82, 184, 255),
        width=3
    )

    # Draw PM text
    try:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()
    except:
        font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    draw.text((210, 270), "PM", fill=(255, 255, 255, 255), font=font)

    # Draw title and description
    draw.text((500, 250), "PromptManager", fill=(255, 255, 255, 255), font=title_font)
    draw.text((500, 320), "Professional Suite", fill=(200, 200, 200, 255), font=font)
    draw.text((500, 360), "AI Image Generation Workflow Management", fill=(150, 150, 150, 255), font=font)

    og_img.save(images_dir / "og-image.png", "PNG")
    print(f"Created: {images_dir / 'og-image.png'}")

    # Twitter Card (similar but could be different)
    og_img.save(images_dir / "twitter-card.png", "PNG")
    print(f"Created: {images_dir / 'twitter-card.png'}")


if __name__ == "__main__":
    generate_icons()