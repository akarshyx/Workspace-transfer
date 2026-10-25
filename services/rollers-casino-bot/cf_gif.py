"""
Generate an animated coin-flip GIF in memory.
Returns a BytesIO ready to be sent as a Telegram animation.
"""
import math
import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter

SIZE   = 280          # coin diameter in the GIF
CANVAS = 300          # total frame size (adds a little breathing room)
FPS_MS = 40           # ms per frame  (~25 fps)

HEADS_IMG = os.path.join("attached_assets",
            "0D734113-7A6F-4AB5-8CBD-A57FF10F5EA0_1775855352527.png")
TAILS_IMG = os.path.join("attached_assets",
            "6D307923-9F39-4928-AEEF-1BE46B1F32F3_1775855352527.png")

def _circle_crop(img: Image.Image, size: int) -> Image.Image:
    """Resize image to `size×size` and clip to a circle with RGBA."""
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, mask=mask)
    return result


def _make_frame(face: Image.Image, squeeze: float, canvas: int, size: int) -> Image.Image:
    """
    squeeze  : 0.0 (edge-on) → 1.0 (full face)
    Returns a canvas×canvas RGBA frame.
    """
    frame = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 255))

    if squeeze < 0.01:
        return frame

    w = max(1, int(size * squeeze))
    h = size
    scaled = face.resize((w, h), Image.LANCZOS)

    ox = (canvas - w) // 2
    oy = (canvas - h) // 2
    frame.paste(scaled, (ox, oy), scaled)

    # subtle shadow under coin
    shadow_w = max(1, int(w * 0.85))
    shadow_h = max(1, int(h * 0.08))
    sx = (canvas - shadow_w) // 2
    sy = oy + h + 4
    if sy + shadow_h < canvas:
        shd = Image.new("RGBA", (shadow_w, shadow_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(shd)
        draw.ellipse((0, 0, shadow_w - 1, shadow_h - 1),
                     fill=(0, 0, 0, int(120 * squeeze)))
        frame.paste(shd, (sx, sy), shd)

    return frame


def generate_cf_gif(result: str) -> BytesIO:
    """
    result: 'heads' or 'tails'
    Returns BytesIO containing an animated GIF.
    """
    heads = _circle_crop(Image.open(HEADS_IMG), SIZE)
    tails = _circle_crop(Image.open(TAILS_IMG), SIZE)

    # Build angle sequence:
    #   4 full spins (1440°) then settle to result face
    #   heads → final angle 0° (mod 360)  → cos = 1, heads visible
    #   tails → final angle 180° (mod 360) → cos = -1 flipped = tails visible

    total_spins  = 4          # full 360° spins during flight
    settle_steps = 10         # slow-down frames at the end
    spin_steps   = total_spins * 18   # 18 frames per rotation = smooth

    angles = []

    # Spin phase – accelerate then slow
    for i in range(spin_steps):
        t = i / spin_steps
        # ease-in-out then ease-out
        eased = t * t * (3 - 2 * t)
        angles.append(eased * total_spins * 360)

    # Settle phase – approach final angle smoothly
    start_angle = angles[-1] % 360
    end_angle   = 0.0 if result == 'heads' else 180.0

    # Make sure we always spin forward into the landing
    if end_angle <= start_angle:
        end_angle += 360

    for i in range(1, settle_steps + 1):
        t = i / settle_steps
        eased = 1 - (1 - t) ** 3   # ease-out cubic
        angles.append(angles[-1] + eased * (start_angle + end_angle - angles[-1] % 360
                                             + 360 - start_angle) % 360)

    # Recalculate settle cleanly
    angles = []
    for i in range(spin_steps):
        t = i / spin_steps
        eased = t * t * (3 - 2 * t)
        angles.append(eased * total_spins * 360)

    base = angles[-1]
    land = 0.0 if result == 'heads' else 180.0
    # advance to next 'land' angle
    cur_mod = base % 360
    delta = (land - cur_mod) % 360
    if delta == 0:
        delta = 0

    for i in range(1, settle_steps + 1):
        t = i / settle_steps
        eased = 1 - (1 - t) ** 3
        angles.append(base + eased * (delta + 1))   # tiny overshoot then settle

    # Build frames
    frames: list[Image.Image] = []
    durations: list[int]      = []

    for idx, angle in enumerate(angles):
        deg    = angle % 360
        cos_v  = math.cos(math.radians(deg))
        squeeze = abs(cos_v)

        # Which face?  cos > 0 → heads side, cos < 0 → tails side
        face = heads if cos_v >= 0 else tails

        frame = _make_frame(face, squeeze, CANVAS, SIZE)
        # Convert to P mode for GIF with transparency
        bg = Image.new("RGB", (CANVAS, CANVAS), (0, 0, 0))
        bg.paste(frame, mask=frame.split()[3])
        frames.append(bg)

        # Slower at start/end, faster in middle
        if idx < 5 or idx >= len(angles) - settle_steps:
            durations.append(FPS_MS * 2)
        else:
            durations.append(FPS_MS)

    # Hold the final frame longer so player can see the result
    durations[-1] = 1200

    buf = BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    buf.seek(0)
    return buf
