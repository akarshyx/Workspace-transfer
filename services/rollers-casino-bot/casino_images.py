"""
casino_images.py — Pillow-based image generators for Rollers Casino bot.
Blue Rollersgame brand theme with logo watermark on cards.
"""

from __future__ import annotations
from io import BytesIO
from typing import Optional
import os
import re
import textwrap

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# ── Rollersgame brand palette ──────────────────────────────────────────────────
BRAND_BLUE      = (29,  98, 180)      # Rollersgame signature blue
BRAND_BLUE_DARK = (18,  60, 120)      # darker blue for gradients
BRAND_BLUE_MID  = (35, 115, 200)      # mid blue
BG_COLOR        = (12,  35,  72)      # deep navy background
CARD_BG         = (255, 255, 255)     # white card face
CARD_BG_TINT    = (240, 246, 255)     # very slight blue tint on card
CARD_BORDER     = (29,  98, 180)      # blue card border
CARD_BORDER_GLOW= (80, 150, 240)      # lighter glow border
CARD_HIDDEN_BG  = (20,  60, 130)      # blue card back
CARD_HIDDEN_PAT = (25,  75, 155)      # pattern on hidden card
RED_SUIT        = (210,  30,  50)     # hearts / diamonds
BLACK_SUIT      = (15,   20,  45)     # spades / clubs
GOLD_ACCENT     = (255, 200,  50)     # gold accent details
TEXT_PRIMARY    = (255, 255, 255)     # white labels
TEXT_SECONDARY  = (160, 195, 245)     # light-blue dim text
TEXT_WIN        = (60,  220,  90)     # green win
TEXT_LOSE       = (230,  60,  60)     # red lose
TEXT_TIE        = (240, 200,  50)     # yellow tie
TEXT_BUST       = (240, 120,  40)     # orange bust
SEPARATOR_CLR   = (40,  90, 170)      # separator line

SUIT_IS_RED = {'♥', '♦'}

# Logo path
_LOGO_PATH = os.path.join(os.path.dirname(__file__), "attached_assets", "IMG_7491_1785879040452.png")


def _load_logo(size: int = 40) -> Optional[Image.Image]:
    """Load the Rollersgame logo, resize to square, return RGBA image."""
    if not os.path.exists(_LOGO_PATH):
        return None
    try:
        logo = Image.open(_LOGO_PATH).convert("RGBA")
        logo = logo.resize((size, size), Image.LANCZOS)
        return logo
    except Exception:
        return None


def _hand_value(hand: list) -> int:
    total, aces = 0, 0
    for card in hand:
        if card is None:
            continue
        try:
            rank = card[0]
        except (TypeError, IndexError):
            continue
        if rank in ('J', 'Q', 'K'):
            total += 10
        elif rank == 'A':
            total += 11
            aces += 1
        else:
            try:
                total += int(rank)
            except Exception:
                total += 10
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


# ── Font helpers ───────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    import glob
    candidates_bold = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
    ]
    paths = candidates_bold if bold else candidates
    for pattern in paths:
        for path in glob.glob(pattern):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _image_text(value: object) -> str:
    """Convert Telegram text to a Pillow-safe single-line string.

    Telegram custom emoji are markup/entities, not characters that the
    bundled fonts can draw.  Keeping them in an image label raises a
    UnicodeEncodeError and makes the caller fall back unexpectedly.
    """
    text = re.sub(r"<[^>]*>", "", str(value or ""))
    text = text.replace("\n", " ").replace("\r", " ")
    return text.encode("ascii", "ignore").decode("ascii") or "-"


def _draw_bg_gradient(img: Image.Image) -> None:
    """Paint a top-to-bottom blue gradient background."""
    W, H = img.size
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(BG_COLOR[0] + (BRAND_BLUE_DARK[0] - BG_COLOR[0]) * t * 0.6)
        g = int(BG_COLOR[1] + (BRAND_BLUE_DARK[1] - BG_COLOR[1]) * t * 0.6)
        b = int(BG_COLOR[2] + (BRAND_BLUE_DARK[2] - BG_COLOR[2]) * t * 0.6)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


# ── Card constants ─────────────────────────────────────────────────────────────

CARD_W = 72
CARD_H = 96
CARD_R = 10


def _rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(list(xy), radius=radius, fill=fill,
                           outline=outline, width=width)


def _draw_card_face(draw: ImageDraw.ImageDraw, x: int, y: int, rank: str, suit: str):
    """Draw a white card with blue border, rank and suit in brand colours."""
    x1, y1 = x + CARD_W, y + CARD_H

    # Card shadow (offset)
    _rounded_rect(draw, (x + 3, y + 4, x1 + 3, y1 + 4), CARD_R,
                  fill=(8, 25, 60, 120))

    # Card body
    _rounded_rect(draw, (x, y, x1, y1), CARD_R,
                  fill=CARD_BG_TINT, outline=CARD_BORDER, width=2)

    # Thin gold inner line
    _rounded_rect(draw, (x + 4, y + 4, x1 - 4, y1 - 4), CARD_R - 3,
                  fill=None, outline=GOLD_ACCENT, width=1)

    suit_color = RED_SUIT if suit in SUIT_IS_RED else BLACK_SUIT

    # Top-left rank + suit
    f_small = _font(16, bold=True)
    draw.text((x + 7, y + 5), rank, fill=suit_color, font=f_small)
    f_suit_sm = _font(14, bold=True)
    draw.text((x + 7, y + 23), suit, fill=suit_color, font=f_suit_sm)

    # Bottom-right rank + suit (rotated simulation — just mirror)
    rw, _ = _text_size(draw, rank, f_small)
    sw, _ = _text_size(draw, suit, f_suit_sm)
    draw.text((x1 - rw - 7, y1 - 38), rank, fill=suit_color, font=f_small)
    draw.text((x1 - sw - 7, y1 - 20), suit, fill=suit_color, font=f_suit_sm)

    # Large centre suit
    f_big = _font(36, bold=True)
    bw, bh = _text_size(draw, suit, f_big)
    draw.text((x + (CARD_W - bw) // 2, y + (CARD_H - bh) // 2 - 2),
              suit, fill=suit_color, font=f_big)


def _draw_card_hidden(draw: ImageDraw.ImageDraw, x: int, y: int):
    """Draw a face-down card with Rollersgame blue back pattern."""
    x1, y1 = x + CARD_W, y + CARD_H

    # Shadow
    _rounded_rect(draw, (x + 3, y + 4, x1 + 3, y1 + 4), CARD_R,
                  fill=(5, 15, 40))

    # Blue back
    _rounded_rect(draw, (x, y, x1, y1), CARD_R,
                  fill=CARD_HIDDEN_BG, outline=CARD_BORDER_GLOW, width=2)

    # Inner diamond pattern
    for i in range(6):
        for j in range(8):
            cx = x + 10 + i * 10
            cy = y + 10 + j * 10
            draw.ellipse([(cx - 2, cy - 2), (cx + 2, cy + 2)],
                         fill=CARD_HIDDEN_PAT)

    # Gold border detail
    _rounded_rect(draw, (x + 5, y + 5, x1 - 5, y1 - 5), CARD_R - 4,
                  fill=None, outline=GOLD_ACCENT, width=1)

    # Question mark
    f_q = _font(28, bold=True)
    qw, qh = _text_size(draw, "?", f_q)
    draw.text((x + (CARD_W - qw) // 2, y + (CARD_H - qh) // 2),
              "?", fill=CARD_BORDER_GLOW, font=f_q)


def _draw_hand(draw: ImageDraw.ImageDraw, hand: list, x: int, y: int,
               hide_second: bool = False) -> int:
    gap = 10
    cx = x
    for i, card in enumerate(hand):
        if card is None:
            continue
        try:
            rank, suit = card[0], card[1]
        except (TypeError, IndexError):
            continue
        if hide_second and i == 1:
            _draw_card_hidden(draw, cx, y)
        else:
            _draw_card_face(draw, cx, y, rank, suit)
        cx += CARD_W + gap
    return cx - x - gap


# ── Blackjack game state image ─────────────────────────────────────────────────

def generate_blackjack_image(
    username: str,
    player_hand: list,
    dealer_hand: list,
    bet_display: str,
    balance_display: str,
    status: str,
    avatar_bytes: Optional[bytes] = None,
) -> Optional[BytesIO]:
    """Render a blackjack game state as a branded Rollersgame PNG."""
    if not _PIL_OK:
        return None

    try:
        # Sanitize every user/display string before any Pillow draw call.
        username = _image_text(username)
        bet_display = _image_text(bet_display)
        balance_display = _image_text(balance_display)
        status = _image_text(status)
        W, H = 500, 440
        img = Image.new("RGB", (W, H), BG_COLOR)
        _draw_bg_gradient(img)
        draw = ImageDraw.Draw(img)

        # ── Top brand bar ─────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (W, 5)], fill=GOLD_ACCENT)
        draw.rectangle([(0, 5), (W, 54)], fill=BRAND_BLUE_DARK)

        # Logo in header
        logo = _load_logo(size=38)
        logo_x = 14
        if logo:
            img.paste(logo, (logo_x, 8), logo)
            title_x = logo_x + 46
        else:
            title_x = 16

        f_title = _font(20, bold=True)
        draw.text((title_x, 14), "Rollersgame Blackjack", fill=GOLD_ACCENT, font=f_title)

        # Status badge on right
        STATUS_COLORS = {
            'playing':      TEXT_SECONDARY,
            'blackjack':    GOLD_ACCENT,
            'you win':      TEXT_WIN,
            'bust':         TEXT_BUST,
            'dealer wins':  TEXT_LOSE,
            'push':         TEXT_TIE,
            'tie':          TEXT_TIE,
        }
        res_color = TEXT_SECONDARY
        for key, col in STATUS_COLORS.items():
            if key in status.lower():
                res_color = col
                break

        if status and 'playing' not in status.lower():
            f_status = _font(15, bold=True)
            sw, sh = _text_size(draw, status, f_status)
            # Badge background
            pad = 6
            bx = W - sw - pad * 2 - 12
            by = 17
            _rounded_rect(draw, (bx - pad, by - 3, bx + sw + pad, by + sh + 3),
                          6, fill=(0, 0, 0, 100))
            draw.text((bx, by), status, fill=res_color, font=f_status)

        y_cursor = 66

        # ── Dealer section ────────────────────────────────────────────────────
        hide_dealer = 'playing' in status.lower()

        d_visible = [dealer_hand[0]] if (hide_dealer and dealer_hand) else dealer_hand
        d_val = _hand_value(d_visible)
        d_label = f"Dealer  {d_val}" + (" + ?" if hide_dealer and len(dealer_hand) > 1 else "")

        f_label = _font(14, bold=True)
        draw.text((16, y_cursor), d_label, fill=TEXT_SECONDARY, font=f_label)
        y_cursor += 22
        _draw_hand(draw, dealer_hand, 16, y_cursor, hide_second=hide_dealer)
        y_cursor += CARD_H + 20

        # ── Divider ───────────────────────────────────────────────────────────
        draw.rectangle([(16, y_cursor), (W - 16, y_cursor + 1)], fill=SEPARATOR_CLR)
        y_cursor += 12

        # ── Player section ────────────────────────────────────────────────────
        p_val = _hand_value(player_hand)
        bust = p_val > 21
        p_label = f"Your hand  {p_val}" + ("  BUST" if bust else "")
        p_color = TEXT_BUST if bust else TEXT_SECONDARY
        draw.text((16, y_cursor), p_label, fill=p_color, font=f_label)
        y_cursor += 22
        _draw_hand(draw, player_hand, 16, y_cursor)
        y_cursor += CARD_H + 18

        # ── Info bar ──────────────────────────────────────────────────────────
        draw.rectangle([(0, y_cursor), (W, y_cursor + 1)], fill=SEPARATOR_CLR)
        y_cursor += 10

        f_info = _font(13, bold=True)
        f_val  = _font(13)

        draw.text((16, y_cursor), "Bet", fill=TEXT_SECONDARY, font=f_val)
        bw, _ = _text_size(draw, "Bet", f_val)
        draw.text((16 + bw + 4, y_cursor), bet_display, fill=GOLD_ACCENT, font=f_info)

        bal_label = "Balance"
        bl_x = W // 2
        draw.text((bl_x, y_cursor), bal_label, fill=TEXT_SECONDARY, font=f_val)
        blw, _ = _text_size(draw, bal_label, f_val)
        draw.text((bl_x + blw + 4, y_cursor), balance_display, fill=TEXT_PRIMARY, font=f_info)
        y_cursor += 22

        # ── Result phrase ─────────────────────────────────────────────────────
        if status and 'playing' not in status.lower():
            result_phrases = {
                'you win':     f"🎉  You won {bet_display}!",
                'blackjack':   f"🏆  Blackjack! You won {bet_display}!",
                'bust':        f"💥  Bust! Better luck next time.",
                'dealer wins': f"📉  Dealer wins. Try again!",
                'push':        f"🤝  Push — your bet is returned.",
                'tie':         f"🤝  Push — your bet is returned.",
            }
            for key, phrase in result_phrases.items():
                if key in status.lower():
                    f_res = _font(14, bold=True)
                    # Keep generated artwork clean and readable even when
                    # legacy status strings still contain Telegram emoji.
                    import re as _re
                    phrase = _re.sub(r"[^\x00-\x7F]+", " ", phrase).strip()
                    draw.text((16, y_cursor), phrase, fill=res_color, font=f_res)
                    break

        # ── Username watermark bottom-right ───────────────────────────────────
        f_wm = _font(11)
        wm_text = f"@{username}"
        wmw, _ = _text_size(draw, wm_text, f_wm)
        draw.text((W - wmw - 12, H - 18), wm_text, fill=(70, 110, 170), font=f_wm)

        # ── Bottom gold bar ───────────────────────────────────────────────────
        draw.rectangle([(0, H - 4), (W, H)], fill=BRAND_BLUE)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf

    except Exception:
        import traceback
        traceback.print_exc()
        return None


# ── Standalone card image (used by HiLo, etc.) ────────────────────────────────

def generate_single_card_image(rank: str, suit: str) -> Optional[BytesIO]:
    """Generate a single branded playing card as PNG (for /hilo etc.)."""
    if not _PIL_OK:
        return None
    try:
        PAD = 24
        W = CARD_W + PAD * 2
        H = CARD_H + PAD * 2 + 10

        img = Image.new("RGB", (W, H), BG_COLOR)
        _draw_bg_gradient(img)
        draw = ImageDraw.Draw(img)

        _draw_card_face(draw, PAD, PAD, rank, suit)

        # Small logo watermark below card
        logo = _load_logo(size=22)
        if logo:
            lx = (W - 22) // 2
            ly = PAD + CARD_H + 5
            img.paste(logo, (lx, ly), logo)

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf
    except Exception:
        return None


# ── Stub helpers for other generators ─────────────────────────────────────────

def generate_ref_image(*args, **kwargs):
    return None

def format_member_since(*args, **kwargs):
    return ""

def generate_streak_image(*args, **kwargs):
    return None

def generate_jackpot_image(*args, **kwargs):
    return None

def generate_maxbet_image(*args, **kwargs):
    return None

def generate_surprise_drop_image(*args, **kwargs):
    return None

def generate_baccarat_image(*args, **kwargs):
    return None
