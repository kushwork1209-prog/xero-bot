"""
XERO Bot — Welcome Card Image Processor
Generates personalized welcome cards using Pillow.

Admin uploads ONE image (Discord file attachment).
Bot stores it per-guild in data/welcome_images/<guild_id>.png
On every join: overlays member name, avatar, member count onto that image.
Sends as discord.File — no external URLs.

Features:
- File upload (not URL) via /config welcome-upload
- Name overlay with drop shadow for any background
- Circular avatar with white border
- Member count sub-text
- Configurable: text position, color, size, show/hide elements
- Gradient or solid bar overlay for readability
- Preview before going live (admin sees exactly what members will see)
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import io, os, aiohttp, asyncio, logging

logger = logging.getLogger("XERO.WelcomeCard")

WELCOME_DIR = "data/welcome_images"
os.makedirs(WELCOME_DIR, exist_ok=True)

# Standard welcome card dimensions — perfect 16:5 ratio for Discord embeds
CARD_W, CARD_H = 1024, 320

# Font search paths (tries multiple, falls back to default)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

FONT_PATHS_REGULAR = [p.replace("Bold","").replace("-B.ttf",".ttf") for p in FONT_PATHS]


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    paths = FONT_PATHS if bold else FONT_PATHS_REGULAR
    for fp in paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


def _get_base_image(guild_id: int) -> bytes | None:
    """Get stored base image for this guild."""
    path = os.path.join(WELCOME_DIR, f"{guild_id}.png")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def save_base_image(guild_id: int, image_bytes: bytes) -> str:
    """Save uploaded image for a guild. Returns path."""
    path = os.path.join(WELCOME_DIR, f"{guild_id}.png")
    # Validate and normalize
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.save(path, "PNG", optimize=True)
    return path


def delete_base_image(guild_id: int):
    """Remove stored welcome image for a guild."""
    path = os.path.join(WELCOME_DIR, f"{guild_id}.png")
    if os.path.exists(path):
        os.remove(path)


def generate_welcome_card(
    guild_id: int,
    member_name: str,
    member_avatar_bytes: bytes | None = None,
    text_color: str = "#FFFFFF",
    text_position: str = "bottom_left",
    show_name: bool = True,
    show_avatar: bool = True,
    show_member_count: bool = True,
    member_count: int = 0,
    server_name: str = "",
    overlay_style: str = "gradient",
    font_size: int = 52,
    custom_text: str = "",
) -> bytes | None:
    """
    Generate a personalized welcome card for a joining member.

    Returns PNG bytes as discord.File, or None if no base image is set.

    What this does:
      1. Load admin's uploaded image (stored per guild)
      2. Smart-crop to 1024×320
      3. Apply readability overlay (gradient or bar)
      4. Overlay member's display name with drop shadow
      5. Draw circular avatar with white border
      6. Add member count sub-text
      7. Return as PNG bytes

    From the member's perspective: they see a beautiful personalized card
    with their name on it — the same image the server has, but with THEIR
    identity baked in. No two welcome messages look alike.
    """
    base_bytes = _get_base_image(guild_id)
    if not base_bytes:
        return None

    try:
        base = Image.open(io.BytesIO(base_bytes)).convert("RGBA")
        ow, oh = base.size

        # ── Smart crop to 1024×320 ─────────────────────────────────────────
        ratio = max(CARD_W / ow, CARD_H / oh)
        nw, nh = int(ow * ratio) + 1, int(oh * ratio) + 1
        base = base.resize((nw, nh), Image.LANCZOS)
        cx = (nw - CARD_W) // 2
        cy = (nh - CARD_H) // 2
        base = base.crop((cx, cy, cx + CARD_W, cy + CARD_H))
        w, h = CARD_W, CARD_H

        # ── Readability overlay ────────────────────────────────────────────
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if overlay_style == "gradient":
            # Smooth bottom gradient
            for y in range(h):
                frac  = max(0, (y - h * 0.35) / (h * 0.65))
                alpha = int(190 * (frac ** 1.5))
                for x in range(w):
                    overlay.putpixel((x, y), (0, 0, 0, alpha))
        elif overlay_style == "bar":
            bar_h = 100
            bar   = Image.new("RGBA", (w, bar_h), (0, 0, 0, 170))
            overlay.paste(bar, (0, h - bar_h))
        elif overlay_style == "full":
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 110))
        base = Image.alpha_composite(base, overlay)
        draw = ImageDraw.Draw(base)

        # ── Fonts ──────────────────────────────────────────────────────────
        font_name = _load_font(font_size, bold=True)
        font_sub  = _load_font(max(18, font_size // 2 + 2), bold=False)
        font_tiny = _load_font(max(14, font_size // 3), bold=False)

        def shadow_text(txt, x, y, fnt, color_hex, layers=3):
            r, g, b, _ = _hex_to_rgba(color_hex)
            for i in range(layers, 0, -1):
                alpha = int(200 / i)
                draw.text((x + i, y + i), txt, font=fnt, fill=(0, 0, 0, alpha))
            draw.text((x, y), txt, font=fnt, fill=(r, g, b, 255))

        # ── Layout: avatar on left, text to the right ──────────────────────
        AV_SIZE   = 84
        AV_BORDER = 4
        AV_TOTAL  = AV_SIZE + AV_BORDER * 2
        MARGIN    = 24
        TEXT_X    = MARGIN + AV_TOTAL + 14 if show_avatar else MARGIN

        # Name
        if show_name and member_name:
            name = member_name[:26] + "…" if len(member_name) > 26 else member_name
            nb   = draw.textbbox((0, 0), name, font=font_name)
            nw_, nh_ = nb[2] - nb[0], nb[3] - nb[1]

            if text_position == "bottom_left":
                nx = TEXT_X
                ny = h - nh_ - MARGIN - 4
            elif text_position == "bottom_center":
                nx = (w - nw_) // 2
                ny = h - nh_ - MARGIN - 4
            elif text_position == "bottom_right":
                nx = w - nw_ - MARGIN
                ny = h - nh_ - MARGIN - 4
            elif text_position == "center":
                nx = TEXT_X
                ny = (h - nh_) // 2
            elif text_position == "top_left":
                nx = TEXT_X
                ny = MARGIN
            else:
                nx, ny = TEXT_X, h - nh_ - MARGIN - 4

            shadow_text(name, nx, ny, font_name, text_color)

            # Sub-text: member count or custom
            if custom_text:
                sub = custom_text.replace("{count}", str(member_count)).replace("{server}", server_name)
            elif show_member_count and member_count:
                sub = f"Member #{member_count:,}  •  {server_name}" if server_name else f"Member #{member_count:,}"
            elif server_name:
                sub = f"Welcome to {server_name}"
            else:
                sub = ""

            if sub:
                sb   = draw.textbbox((0, 0), sub, font=font_sub)
                sw_  = sb[2] - sb[0]
                sub_x = nx if text_position in ("bottom_left","center","top_left") else (w - sw_) // 2
                sub_y = ny - (sb[3] - sb[1]) - 6
                shadow_text(sub, sub_x, sub_y, font_sub, "#D8D8D8", layers=2)

        # ── Circular avatar ────────────────────────────────────────────────
        if show_avatar and member_avatar_bytes:
            try:
                av = Image.open(io.BytesIO(member_avatar_bytes)).convert("RGBA")
                av = av.resize((AV_SIZE, AV_SIZE), Image.LANCZOS)

                # White circle border
                bd = Image.new("RGBA", (AV_TOTAL, AV_TOTAL), (0, 0, 0, 0))
                bd_draw = ImageDraw.Draw(bd)
                bd_draw.ellipse((0, 0, AV_TOTAL - 1, AV_TOTAL - 1), fill=(255, 255, 255, 255))

                # Circular mask for avatar
                av_mask = Image.new("L", (AV_SIZE, AV_SIZE), 0)
                ImageDraw.Draw(av_mask).ellipse((0, 0, AV_SIZE - 1, AV_SIZE - 1), fill=255)
                av.putalpha(av_mask)

                bd.paste(av, (AV_BORDER, AV_BORDER), av)

                av_x = MARGIN
                av_y = h - AV_TOTAL - MARGIN
                base.paste(bd, (av_x, av_y), bd)
            except Exception as e:
                logger.debug(f"Avatar overlay: {e}")

        # ── Convert to bytes ───────────────────────────────────────────────
        out = io.BytesIO()
        base.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()

    except Exception as e:
        logger.error(f"Welcome card generation: {e}")
        return None


async def fetch_avatar(url: str) -> bytes | None:
    """Download avatar bytes for card generation."""
    try:
        async with __import__("aiohttp").ClientSession() as s:
            async with s.get(url + "?size=128", timeout=__import__("aiohttp").ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception:
        pass
    return None
