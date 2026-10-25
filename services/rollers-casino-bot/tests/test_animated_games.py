import asyncio
from html import unescape
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main


class _FakeDiceBot:
    def __init__(self, token, *, error=None):
        self.token = token
        self.error = error
        self.calls = []

    async def send_dice(self, *, chat_id, emoji):
        self.calls.append((chat_id, emoji))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(dice=SimpleNamespace(value=4))


class AnimatedGameReliabilityTests(unittest.TestCase):
    PREMIUM_EMOJI_IDS = {
        "⬆️": "6305310112797959113",
        "🎲": "6305357340258345030",
        "🤖": "6305141307698323992",
        "👑": "6305484278016778054",
        "👎": "6305111165617840318",
        "🔄": "6305349222770155394",
        "💰": "6305469894171305786",
    }

    def test_reference_animated_game_copy_uses_pack_premium_emoji_and_no_promo_link(self):
        with patch.dict(main.CUSTOM_EMOJI_MAP, self.PREMIUM_EMOJI_IDS):
            start = main._rc_started_text(
                "dice", "123", "Player One", "$1.07", 2, 3, bot_id=456
            )
            self.assertIn("<tg-emoji emoji-id=\"6305310112797959113\">⬆️</tg-emoji>", start)
            self.assertIn("<tg-emoji emoji-id=\"6305357340258345030\">🎲</tg-emoji>", start)
            self.assertIn("Player 1:", start)
            self.assertIn("Player 2:", start)
            self.assertIn("Bet: $1.07", start)
            self.assertIn("Mode: Double - 3 points", start)
            self.assertIn("Roll the dice", start)
            self.assertNotIn("https://t.me", start)

            turn_button = main._rc_player_turn_keyboard(
                "dice", "123"
            ).inline_keyboard[0][0]
            self.assertEqual(turn_button.text, "Send an emoji")
            self.assertEqual(turn_button.icon_custom_emoji_id, self.PREMIUM_EMOJI_IDS["🎲"])

            end_buttons = main._rc_end_keyboard(
                "dice", "1d3w", 1.07, "n"
            ).inline_keyboard
            self.assertEqual(
                [[button.text for button in row] for row in end_buttons],
                [["Repeat", "×2 Double"], ["Change mode"]],
            )
            self.assertEqual(
                end_buttons[0][0].icon_custom_emoji_id,
                self.PREMIUM_EMOJI_IDS["🔄"],
            )
            self.assertIn(
                "tg://user?id=456",
                main._rc_same_values_text(
                    "dice", "123", "Player One", 4, bot_id=456
                ),
            )
            self.assertNotIn("https://t.me", main._rc_same_values_text(
                "dice", "123", "Player One", 4, bot_id=456
            ))

    def test_final_game_result_has_reference_winnings_copy_and_no_promo_preview(self):
        class _FakeBot:
            id = 456

            def __init__(self):
                self.sent = None

            async def send_message(self, **kwargs):
                self.sent = kwargs

        bot = _FakeBot()
        context = SimpleNamespace(bot=bot)
        with patch.dict(main.CUSTOM_EMOJI_MAP, self.PREMIUM_EMOJI_IDS):
            with patch.object(
                main, "format_balance_in_currency", return_value="$2.05"
            ):
                asyncio.run(
                    main._send_game_result_msg(
                        context,
                        chat_id=-100,
                        player_won=True,
                        player_name="Player One",
                        player_id="123",
                        winner_pts=3,
                        loser_pts=1,
                        amount=2.05,
                        user_currency="USD",
                        reply_markup=main._rc_end_keyboard(
                            "dice", "1d3w", 1.07, "n"
                        ),
                    )
                )

        text = bot.sent["text"]
        self.assertIn("The game has ended", unescape(text))
        self.assertIn("Winnings - $2.05", unescape(text))
        self.assertIn("<tg-emoji emoji-id=\"6305310112797959113\">⬆️</tg-emoji>", text)
        self.assertIn("<tg-emoji emoji-id=\"6305484278016778054\">👑</tg-emoji>", text)
        self.assertIn("<tg-emoji emoji-id=\"6305111165617840318\">👎</tg-emoji>", text)
        self.assertNotIn("https://t.me", text)
        self.assertNotIn("Rollers Casino", text)

    def test_dealer_failure_does_not_create_duplicate_fallback_roll(self):
        original_dealers = dict(main.dealer_bots)
        failing_dealer = _FakeDiceBot("dealer-1", error=RuntimeError("network error"))
        fallback_dealer = _FakeDiceBot("dealer-2")
        main.dealer_bots.clear()
        main.dealer_bots.update(
            {"dealer_1": failing_dealer, "dealer_2": fallback_dealer}
        )

        try:
            result = asyncio.run(
                main.dealer_send_dice(
                    chat_id=123,
                    emoji="🎲",
                    main_bot=None,
                    chat_type="group",
                    dealer_name="dealer_1",
                )
            )
        finally:
            main.dealer_bots.clear()
            main.dealer_bots.update(original_dealers)

        self.assertIsNone(result)
        self.assertEqual(len(failing_dealer.calls), 1)
        self.assertEqual(
            fallback_dealer.calls,
            [],
            "a failed request must not be replayed through another bot",
        )

    def test_group_dice_uses_main_bot_when_available_even_with_dealer_configured(self):
        original_dealers = dict(main.dealer_bots)
        dealer = _FakeDiceBot("dealer")
        main.dealer_bots.update(
            {"dealer_1": dealer, "dealer_2": None, "dealer_3": None}
        )
        main_bot = _FakeDiceBot("main")

        try:
            result = asyncio.run(
                main.dealer_send_dice(
                    chat_id=-100123,
                    emoji="🎲",
                    main_bot=main_bot,
                    chat_type="supergroup",
                    dealer_name="dealer_1",
                )
            )
        finally:
            main.dealer_bots.clear()
            main.dealer_bots.update(original_dealers)

        self.assertIsNotNone(result)
        self.assertEqual(main_bot.calls, [(-100123, "🎲")])
        self.assertEqual(dealer.calls, [])

    def test_missing_group_dealer_uses_main_bot(self):
        original_dealers = dict(main.dealer_bots)
        main.dealer_bots.update(
            {"dealer_1": None, "dealer_2": None, "dealer_3": None}
        )
        main_bot = _FakeDiceBot("main")

        try:
            result = asyncio.run(
                main.dealer_send_dice(
                    chat_id=-100123,
                    emoji="🎲",
                    main_bot=main_bot,
                    chat_type="supergroup",
                    dealer_name="dealer_1",
                )
            )
        finally:
            main.dealer_bots.clear()
            main.dealer_bots.update(original_dealers)

        self.assertIsNotNone(result)
        self.assertEqual(result.dice.value, 4)
        self.assertEqual(main_bot.calls, [(-100123, "🎲")])

    def test_visible_dice_serializes_same_chat_but_not_other_chats(self):
        async def scenario():
            original_interval = main._DICE_CHAT_MIN_INTERVAL_SECONDS
            main._DICE_CHAT_MIN_INTERVAL_SECONDS = 0
            main._dice_chat_locks.clear()
            main._dice_chat_last_send.clear()
            active_by_chat = {}
            active_total = 0
            max_by_chat = {}
            max_total = 0

            async def fake_send_dice(*, chat_id, **kwargs):
                nonlocal active_total, max_total
                active_by_chat[chat_id] = active_by_chat.get(chat_id, 0) + 1
                active_total += 1
                max_by_chat[chat_id] = max(
                    max_by_chat.get(chat_id, 0), active_by_chat[chat_id]
                )
                max_total = max(max_total, active_total)
                await asyncio.sleep(0.01)
                active_by_chat[chat_id] -= 1
                active_total -= 1
                return SimpleNamespace(dice=SimpleNamespace(value=4))

            try:
                with patch.object(
                    main, "dealer_send_dice", side_effect=fake_send_dice
                ):
                    await asyncio.gather(
                        main._send_visible_dice(
                            chat_id=1,
                            emoji="🎲",
                            main_bot=None,
                            chat_type="group",
                        ),
                        main._send_visible_dice(
                            chat_id=1,
                            emoji="🎲",
                            main_bot=None,
                            chat_type="group",
                        ),
                        main._send_visible_dice(
                            chat_id=2,
                            emoji="🎲",
                            main_bot=None,
                            chat_type="group",
                        ),
                    )
                return max_by_chat, max_total
            finally:
                main._DICE_CHAT_MIN_INTERVAL_SECONDS = original_interval
                main._dice_chat_locks.clear()
                main._dice_chat_last_send.clear()

        max_by_chat, max_total = asyncio.run(scenario())
        self.assertEqual(max_by_chat[1], 1)
        self.assertEqual(max_by_chat[2], 1)
        self.assertGreaterEqual(
            max_total,
            2,
            "different chats should be able to animate concurrently",
        )

    def test_rapid_dice_updates_wait_for_the_same_game_lock(self):
        async def scenario():
            user_id = "animated-user"
            original_game = main.active_games.get(user_id)
            main.active_games[user_id] = {"type": "dice"}
            main._animated_roll_locks.clear()
            running = 0
            max_running = 0
            calls = 0

            async def fake_locked(update, context):
                nonlocal calls, running, max_running
                calls += 1
                running += 1
                max_running = max(max_running, running)
                await asyncio.sleep(0.01)
                running -= 1

            message = SimpleNamespace(
                chat_id=1,
                from_user=SimpleNamespace(id=user_id),
                dice=SimpleNamespace(value=4),
            )
            update = SimpleNamespace(message=message)
            try:
                with patch.object(
                    main, "_handle_dice_message_locked", side_effect=fake_locked
                ):
                    await asyncio.gather(
                        main.handle_dice_message(update, None),
                        main.handle_dice_message(update, None),
                    )
                return calls, max_running
            finally:
                main._animated_roll_locks.clear()
                if original_game is None:
                    main.active_games.pop(user_id, None)
                else:
                    main.active_games[user_id] = original_game

        calls, max_running = asyncio.run(scenario())
        self.assertEqual(calls, 2)
        self.assertEqual(
            max_running,
            1,
            "rapid rolls must queue instead of racing shared game state",
        )


if __name__ == "__main__":
    unittest.main()