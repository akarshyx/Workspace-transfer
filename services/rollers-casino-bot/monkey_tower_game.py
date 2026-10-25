"""Tower game logic for Monkey Tower Casino bot."""
import random


class TowerGame:
    FLOORS = 9

    # Tower always uses the four-column layout from the reference UI.
    # Difficulty still controls the payout profile, not the board width.
    COLS = {
        "easy":   4,
        "medium": 4,
        "hard":   4,
    }

    # One bomb is placed uniformly at random in every row.  The number of
    # columns changes the odds; no result is changed after the player clicks.
    BOMBS_PER_FLOOR = 1

    # Per-floor payouts are based on the safe probability with a small,
    # declared casino edge.  Medium matches the reference UI's ~1.27x first
    # step for 4 columns / 1 bomb.
    MULTIPLIERS = {
        "easy":   1.42,    # 2/3 safe, ~5% edge per floor
        "medium": 1.27,    # 3/4 safe, ~5% edge per floor
        "hard":   1.90,    # 1/2 safe, ~5% edge per floor
    }

    def __init__(self, user_id: int, mode: str = "medium", bet: float = 1.0, seed: str = None):
        self.user_id      = user_id
        self.mode         = mode
        self.bet          = bet
        self.floor        = 0
        self.finished     = False
        self.lost         = False
        self.user_choices = {}

        cols = self.COLS.get(mode, 4)

        # Build the layout: True = safe, False = bomb.
        # Every row has exactly one bomb, placed before the game starts.
        # If a provably-fair seed hash is provided, use a seeded RNG so the
        # layout is deterministic and verifiable; otherwise fall back to the
        # global random module (backwards-compat / non-PF games).
        rng = random.Random(seed) if seed else random
        self.layout = []
        for _ in range(self.FLOORS):
            row = [True] * cols
            bomb_col = rng.randrange(cols)
            row[bomb_col] = False
            self.layout.append(row)

    def num_cols(self) -> int:
        return self.COLS.get(self.mode, 3)

    def current_multiplier(self) -> float:
        m = self.MULTIPLIERS.get(self.mode, 1.28)
        return round(m ** self.floor, 4) if self.floor > 0 else 1.0

    def current_payout(self) -> float:
        return round(self.bet * self.current_multiplier(), 2)

    def click_tile(self, col: int) -> dict:
        """
        Click a tile on the current floor.
        Returns a dict:
          {
            'success': bool,   # True = safe, False = bomb
            'tile':    str,    # '🌴' or '💀'
            'won':     bool,   # True only when all floors completed safely
            'message': str,    # human-readable result
          }
        """
        if self.finished or self.lost:
            return {'success': False, 'tile': '💀', 'won': False,
                    'message': 'Game is already over.'}

        if not 0 <= col < self.num_cols():
            return {'success': False, 'tile': '💀', 'won': False,
                    'message': 'That tile is not available.'}

        safe = self.layout[self.floor][col]
        self.user_choices[self.floor] = col

        if not safe:
            self.lost     = True
            self.finished = True
            return {
                'success': False,
                'tile':    '💀',
                'won':     False,
                'message': '💀 You found the bomb and lost.',
            }

        self.floor += 1
        if self.floor >= self.FLOORS:
            # Reached the top!
            self.finished = True
            return {
                'success': True,
                'tile':    '🍌',
                'won':     True,
                'message': '🍌 You climbed to the top and won!',
            }

        return {
            'success': True,
            'tile':    '🌴',
            'won':     False,
            'message': '🌴 Safe! Keep climbing!',
        }

    def cashout(self) -> float:
        """Cash out and return the payout amount."""
        self.finished = True
        return self.current_payout()
