"""Generate the draft Rollers Blackjack blue custom-emoji artwork.

This creates transparent 512px WebP sticker candidates and two review images.
It intentionally does not call Telegram or upload anything.
"""

from __future__ import annotations

import base64
import html
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "blackjack_blue_draft"
CARDS = OUT / "cards"
ACTIONS = OUT / "actions"
PREVIEWS = OUT / "previews"

W = H = 512
NAVY = "#07152F"
NAVY_2 = "#102A52"
BLUE = "#1EA7FF"
CYAN = "#7DE5FF"
WHITE = "#F4FBFF"
MUTED = "#9CC9E7"
PURPLE = "#8067FF"


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def data_uri(path: Path) -> str:
    return "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode()


def svg_to_webp(svg: str, destination: Path) -> None:
    source = destination.with_suffix(".svg")
    source.write_text(svg, encoding="utf-8")
    subprocess.run(
        [
            "magick",
            "-background",
            "none",
            str(source),
            "-define",
            "webp:lossless=true",
            str(destination),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    source.unlink()


def card_svg(rank: str, suit: str, suit_name: str) -> str:
    # The reference uses compact vertical card tiles rather than full playing
    # card illustrations: one rank and one suit, with no pip grid.
    suit_color = "#F4FBFF" if suit_name in {"spades", "clubs"} else "#FF3B60"
    rank_size = 92 if rank != "10" else 76
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="face" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{NAVY_2}"/>
      <stop offset="0.52" stop-color="{NAVY}"/>
      <stop offset="1" stop-color="#061026"/>
    </linearGradient>
    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
      <feDropShadow dx="0" dy="12" stdDeviation="12" flood-color="#000814" flood-opacity=".7"/>
    </filter>
  </defs>
  <rect x="126" y="42" width="260" height="428" rx="28" fill="url(#face)" stroke="{BLUE}" stroke-width="10" filter="url(#shadow)"/>
  <text x="256" y="218" text-anchor="middle" fill="{WHITE}" font-family="DejaVu Sans" font-size="{rank_size}" font-weight="900">{esc(rank)}</text>
  <text x="256" y="366" text-anchor="middle" fill="{suit_color}" font-family="DejaVu Sans" font-size="106" font-weight="700">{esc(suit)}</text>
  <text x="256" y="440" text-anchor="middle" fill="{CYAN}" font-family="DejaVu Sans" font-size="25" font-weight="900" letter-spacing="3">ROLLERS</text>
</svg>"""


def hidden_svg() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">
  <defs>
    <linearGradient id="back" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#0A2C59"/><stop offset=".5" stop-color="#07152F"/><stop offset="1" stop-color="#123A70"/>
    </linearGradient>
  </defs>
  <rect x="126" y="42" width="260" height="428" rx="28" fill="url(#back)" stroke="{BLUE}" stroke-width="10"/>
  <rect x="140" y="56" width="232" height="400" rx="18" fill="#0B2A4F" stroke="{CYAN}" stroke-width="4" opacity=".9"/>
  <path d="M154 130H358 M154 190H358 M154 250H358 M154 310H358" stroke="{BLUE}" stroke-width="3" opacity=".24"/>
  <text x="256" y="440" text-anchor="middle" fill="{CYAN}" font-family="DejaVu Sans" font-size="25" font-weight="900" letter-spacing="3">ROLLERS</text>
</svg>"""


def action_svg(kind: str) -> str:
    common = f"""
  <defs>
    <linearGradient id="action" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#154E8D"/><stop offset=".48" stop-color="#0B2C5B"/><stop offset="1" stop-color="{NAVY}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="0" dy="10" stdDeviation="10" flood-color="#000814" flood-opacity=".65"/></filter>
  </defs>
  <rect x="26" y="26" width="460" height="460" rx="92" fill="url(#action)" stroke="{BLUE}" stroke-width="12" filter="url(#shadow)"/>
  <circle cx="256" cy="256" r="182" fill="none" stroke="{CYAN}" stroke-width="3" opacity=".3"/>
"""
    if kind == "hit":
        art = f"""
  <rect x="154" y="118" width="128" height="208" rx="18" transform="rotate(-16 218 222)" fill="#0B1838" stroke="{WHITE}" stroke-width="10"/>
  <path d="M256 106V394M190 330L256 402L322 330" fill="none" stroke="{CYAN}" stroke-width="28" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M256 402V332" stroke="{BLUE}" stroke-width="8"/>"""
    elif kind == "stand":
        art = f"""
  <path d="M178 318V190C178 172 204 168 210 187L224 238V128C224 108 252 106 256 128V232V106C256 86 286 86 286 108V236V126C286 106 316 108 316 128V244V164C316 146 344 148 344 166V284C344 354 298 390 240 390H218C194 390 178 362 178 318Z" fill="{PURPLE}" stroke="{CYAN}" stroke-width="8" stroke-linejoin="round"/>"""
    elif kind == "double":
        art = f"""
  <rect x="114" y="156" width="124" height="190" rx="16" transform="rotate(-15 176 251)" fill="#0B1838" stroke="{CYAN}" stroke-width="9"/>
  <rect x="274" y="156" width="124" height="190" rx="16" transform="rotate(15 336 251)" fill="#0B1838" stroke="{BLUE}" stroke-width="9"/>
  <text x="256" y="292" text-anchor="middle" fill="{WHITE}" font-family="DejaVu Sans" font-size="112" font-weight="900">×2</text>"""
    elif kind == "split":
        art = f"""
  <rect x="128" y="126" width="126" height="212" rx="18" transform="rotate(-12 191 232)" fill="#0B1838" stroke="{CYAN}" stroke-width="9"/>
  <rect x="258" y="126" width="126" height="212" rx="18" transform="rotate(12 321 232)" fill="#0B1838" stroke="{BLUE}" stroke-width="9"/>
  <path d="M256 164V352M220 200L256 164L292 200M220 316L256 352L292 316" fill="none" stroke="{WHITE}" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>"""
    elif kind == "replay":
        art = f"""
  <path d="M360 188C332 136 276 112 222 128C158 146 122 206 136 268C150 330 204 370 266 360C318 352 356 316 366 268" fill="none" stroke="{CYAN}" stroke-width="26" stroke-linecap="round"/>
  <path d="M360 126V208H278" fill="none" stroke="{WHITE}" stroke-width="24" stroke-linecap="round" stroke-linejoin="round"/>"""
    else:
        raise ValueError(kind)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">{common}{art}</svg>'


def make_interface_preview() -> None:
    # The interface preview is intentionally a composition of the same designs,
    # with the message proportions based on the supplied Telegram screenshot.
    card_paths = [
        CARDS / "7_hearts.webp",
        CARDS / "hidden.webp",
        CARDS / "3_diamonds.webp",
        CARDS / "4_clubs.webp",
    ]
    action_paths = [ACTIONS / "hit.webp", ACTIONS / "stand.webp", ACTIONS / "double.webp"]
    cards = []
    for x, y, p in [(72, 184, card_paths[0]), (190, 184, card_paths[1]), (72, 420, card_paths[2]), (190, 420, card_paths[3])]:
        cards.append(f'<image href="{data_uri(p)}" x="{x}" y="{y}" width="108" height="108"/>')
    actions = []
    for x, p in zip((52, 410, 768), action_paths):
        actions.append(f'<image href="{data_uri(p)}" x="{x}" y="738" width="92" height="92"/>')
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1180" height="940">
  <defs>
    <linearGradient id="chat" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#070A16"/><stop offset="1" stop-color="#020611"/></linearGradient>
    <linearGradient id="bubble" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#29253A"/><stop offset="1" stop-color="#1E1E2E"/></linearGradient>
  </defs>
  <rect width="1180" height="940" fill="url(#chat)"/>
  <text x="78" y="58" fill="#8B74FF" font-family="DejaVu Sans" font-size="27">/bj 1</text>
  <rect x="24" y="82" width="1132" height="608" rx="32" fill="url(#bubble)"/>
  <text x="74" y="142" fill="{WHITE}" font-family="DejaVu Sans" font-size="34">Dealer's hand 7</text>
  <text x="74" y="382" fill="{WHITE}" font-family="DejaVu Sans" font-size="34">Player's hand 7</text>
  {''.join(cards)}
  <rect x="72" y="604" width="58" height="58" rx="16" fill="#19C99A"/>
  <text x="101" y="645" text-anchor="middle" fill="white" font-family="DejaVu Sans" font-size="37">$</text>
  <text x="150" y="648" fill="{WHITE}" font-family="DejaVu Sans" font-size="34">Bet: 159</text>
  <image href="{data_uri(CARDS / "A_spades.webp")}" x="330" y="596" width="76" height="76"/>
  <text x="1050" y="648" fill="#A5A2B1" font-family="DejaVu Sans" font-size="25">9:26 PM</text>
  <rect x="24" y="712" width="1132" height="148" rx="28" fill="#272438"/>
  {''.join(actions)}
  <text x="188" y="805" text-anchor="middle" fill="{WHITE}" font-family="DejaVu Sans" font-size="32" font-weight="700">Hit</text>
  <text x="546" y="805" text-anchor="middle" fill="{WHITE}" font-family="DejaVu Sans" font-size="32" font-weight="700">Stand</text>
  <text x="902" y="805" text-anchor="middle" fill="{WHITE}" font-family="DejaVu Sans" font-size="32" font-weight="700">Double</text>
</svg>"""
    svg_to_webp(svg, PREVIEWS / "rollers_blackjack_blue_interface.webp")


def make_contact_sheet() -> None:
    """Create a labeled review sheet containing every card and action asset."""
    items = [
        ("ACTION", ACTIONS / kind)
        for kind in ("hit.webp", "stand.webp", "double.webp", "split.webp", "replay.webp")
    ]
    items += [
        ("CARD", path)
        for path in sorted(CARDS.glob("*.webp"))
    ]

    columns = 8
    cell_w, cell_h = 170, 190
    header_h = 86
    rows = (len(items) + columns - 1) // columns
    width = columns * cell_w
    height = header_h + rows * cell_h
    cells = []

    for index, (kind, path) in enumerate(items):
        column = index % columns
        row = index // columns
        x = column * cell_w + (cell_w - 142) // 2
        y = header_h + row * cell_h + 6
        label = path.stem.replace("_", " ").upper()
        cells.append(
            f'<image href="{data_uri(path)}" x="{x}" y="{y}" width="142" height="142"/>'
            f'<text x="{column * cell_w + cell_w / 2}" y="{y + 164}" text-anchor="middle" '
            f'fill="{WHITE}" font-family="DejaVu Sans" font-size="13" font-weight="700">{esc(label)}</text>'
        )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
  <rect width="100%" height="100%" fill="#020611"/>
  <text x="34" y="38" fill="{CYAN}" font-family="DejaVu Sans" font-size="25" font-weight="800">ROLLERS BLACKJACK — BLUE PACK</text>
  <text x="34" y="64" fill="{MUTED}" font-family="DejaVu Sans" font-size="15">5 action emojis · 53 card emojis · transparent WebP · review draft</text>
  {''.join(cells)}
</svg>"""
    svg_to_webp(svg, PREVIEWS / "rollers_blackjack_blue_contact_sheet.webp")


def main() -> None:
    for directory in (CARDS, ACTIONS, PREVIEWS):
        directory.mkdir(parents=True, exist_ok=True)

    suits = [("spades", "♠"), ("hearts", "♥"), ("diamonds", "♦"), ("clubs", "♣")]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    for suit_name, suit in suits:
        for rank in ranks:
            svg_to_webp(card_svg(rank, suit, suit_name), CARDS / f"{rank}_{suit_name}.webp")
    svg_to_webp(hidden_svg(), CARDS / "hidden.webp")

    for kind in ("hit", "stand", "double", "split", "replay"):
        svg_to_webp(action_svg(kind), ACTIONS / f"{kind}.webp")

    make_interface_preview()
    make_contact_sheet()
    print(
        f"Generated {len(list(CARDS.glob('*.webp')))} card assets and "
        f"{len(list(ACTIONS.glob('*.webp')))} action assets in {OUT}"
    )


if __name__ == "__main__":
    main()