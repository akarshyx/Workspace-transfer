import asyncio
import unittest

import keno_game


class KenoMathTests(unittest.TestCase):
    def test_exact_hit_probabilities_sum_to_one(self):
        for spots in range(1, 11):
            probability = sum(
                keno_game.hit_probability(spots, hits)
                for hits in range(0, min(spots, 10) + 1)
            )
            self.assertAlmostEqual(probability, 1.0, places=12)

    def test_draw_is_ten_unique_numbers_in_range(self):
        draw = keno_game.generate_draw()
        self.assertEqual(len(draw), 10)
        self.assertEqual(len(set(draw)), 10)
        self.assertTrue(all(1 <= number <= 40 for number in draw))

    def test_payout_tables_are_deterministic_and_valid(self):
        for mode in keno_game.KENO_CONFIG["modes"]:
            spots = 10
            first = keno_game.build_payout_table(spots, mode)
            second = keno_game.build_payout_table(spots, mode)
            self.assertEqual(first, second)
            self.assertEqual(first[0], 0.0)
            self.assertTrue(all(value >= 0 for value in first.values()))
            self.assertTrue(
                all(value <= keno_game.KENO_MAX_PAYOUT_MULTIPLIER for value in first.values())
            )
            expected_rtp = sum(
                keno_game.hit_probability(spots, hits) * multiplier
                for hits, multiplier in first.items()
            )
            self.assertLessEqual(
                expected_rtp,
                keno_game.KENO_CONFIG["modes"][mode].target_rtp + 1e-12,
            )
            self.assertGreater(
                expected_rtp,
                keno_game.KENO_CONFIG["modes"][mode].target_rtp - 0.01,
            )
            for hits, multiplier in first.items():
                if multiplier > 0:
                    self.assertGreater(
                        keno_game.hit_probability(spots, hits), 0
                    )

    def test_top_payout_is_capped_at_1000x(self):
        self.assertEqual(keno_game.KENO_MAX_PAYOUT_MULTIPLIER, 1_000.0)
        for mode in keno_game.KENO_CONFIG["modes"]:
            self.assertLessEqual(
                max(keno_game.build_payout_table(10, mode).values()),
                1_000.0,
            )

    def test_number_buttons_use_blue_red_and_green_telegram_styles(self):
        session = {
            "session_id": "state-test",
            "selected_numbers": [7],
            "revealed_numbers": [],
            "hit_numbers": [],
            "status": "setup",
        }
        self.assertEqual(keno_game._number_button(7, session).to_dict()["style"], "primary")

        session.update({"revealed_numbers": [7], "status": "revealing"})
        self.assertEqual(keno_game._number_button(7, session).to_dict()["style"], "danger")

        session.update({"hit_numbers": [7]})
        self.assertEqual(keno_game._number_button(7, session).to_dict()["style"], "success")

    def test_result_keeps_revealed_board_and_action_buttons(self):
        class FakeServices:
            @staticmethod
            def get_currency(user_id):
                return "USD"

            @staticmethod
            def format_balance(amount, currency):
                return f"${amount:.2f}"

            @staticmethod
            def get_balance(user_id):
                return 18.0

        original = keno_game._services
        try:
            keno_game.configure(FakeServices())
            session = {
                "session_id": "result-test",
                "user_id": "123456",
                "mode": "easy",
                "bet_amount": 2.0,
                "selected_numbers": [7, 8],
                "revealed_numbers": [7, 8, 9],
                "hit_numbers": [7],
                "final_payout": 2.2,
                "current_multiplier": 1.1,
                "status": "finished",
            }
            text, markup = keno_game._render_result(session)
            self.assertIn("OUTCOME (EASY)", text)
            self.assertIn("Hits:</b> 1/2", text)
            labels = [button.text for row in markup.inline_keyboard for button in row]
            self.assertIn("Play Again", labels)
            self.assertIn("Double", labels)
            self.assertIn("Back", labels)
            self.assertIn("7", labels)
        finally:
            keno_game._services = original

    def test_reveal_board_removes_caption_and_setup_controls(self):
        session = {
            "session_id": "reveal-test",
            "selected_numbers": list(range(1, 11)),
            "revealed_numbers": [33],
            "hit_numbers": [],
            "status": "revealing",
        }
        markup = keno_game._render_draw_board(session)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(len(markup.inline_keyboard), 5)
        self.assertNotIn("Random Pick", labels)
        self.assertNotIn("Clear Table", labels)
        self.assertNotIn("Bet", labels)
        self.assertNotIn("Back", labels)
        self.assertEqual(
            next(button for row in markup.inline_keyboard for button in row if button.text == "33")
            .to_dict()["style"],
            "danger",
        )
        self.assertTrue(keno_game.KENO_REVEAL_TEXT)
        self.assertNotEqual(keno_game.KENO_REVEAL_TEXT.strip(), "")

    def test_expired_setup_round_does_not_block_new_keno_command(self):
        session_id, session = keno_game._new_session("123456", 77, 5.0)
        session["created_at"] = 1.0
        self.assertIsNone(keno_game._active_session_for_user("123456"))
        self.assertIsNone(keno_game._get_owned_session(session_id, "123456"))

    def test_invalid_payout_inputs_do_not_pay(self):
        self.assertEqual(keno_game.payout_multiplier(0, 0, "medium"), 0.0)
        self.assertEqual(keno_game.payout_multiplier(9, 9, "medium"), 0.0)
        self.assertEqual(keno_game.payout_multiplier(11, 11, "medium"), 0.0)
        self.assertEqual(keno_game.payout_multiplier(5, 6, "medium"), 0.0)

    def test_keno_requires_exactly_ten_numbers(self):
        self.assertEqual(keno_game.KENO_CONFIG["minimum_selection"], 10)
        self.assertEqual(keno_game.KENO_CONFIG["maximum_selection"], 10)


class KenoStateTests(unittest.TestCase):
    def setUp(self):
        keno_game.restore_state({})

    def tearDown(self):
        keno_game.restore_state({})

    def test_state_round_trip_preserves_independent_sessions(self):
        session_id, session = keno_game._new_session("123456", 77, 5.0)
        session["selected_numbers"] = [1, 4, 10]
        session["status"] = "setup"
        saved = keno_game.export_state()
        keno_game.restore_state(saved)
        restored = keno_game._get_owned_session(session_id, "123456")
        self.assertIsNotNone(restored)
        self.assertEqual(restored["selected_numbers"], [1, 4, 10])
        self.assertEqual(restored["game"], "keno")

    def test_restore_state_rejects_invalid_numbers_and_statuses(self):
        keno_game.restore_state({
            "valid": {
                "game": "keno",
                "status": "setup",
                "selected_numbers": [1, 1, 40, 41, "bad", -2],
                "draw_numbers": [2, 2, 41, 3],
                "revealed_numbers": [3, 40, 2],
                "hit_numbers": [1, 3, 999],
            },
            "invalid": {
                "game": "keno",
                "status": "not-a-status",
            },
        })

        restored = keno_game._sessions["valid"]
        self.assertEqual(restored["selected_numbers"], [1, 40])
        self.assertEqual(restored["draw_numbers"], [2, 3])
        self.assertEqual(restored["revealed_numbers"], [3, 2])
        self.assertEqual(restored["hit_numbers"], [])
        self.assertNotIn("invalid", keno_game._sessions)

    def test_interrupted_debit_is_refunded_once_on_recovery(self):
        credited = []

        class FakeServices:
            def credit_balance(self, user_id, amount, transaction_type):
                credited.append((user_id, amount, transaction_type))
                return amount

            def save(self):
                return None

            class logger:
                @staticmethod
                def exception(*args, **kwargs):
                    pass

        original = keno_game._services
        try:
            keno_game.configure(FakeServices())
            session_id, session = keno_game._new_session("123456", 77, 5.0)
            session["status"] = "revealing"
            session["bet_debited"] = True
            self.assertEqual(keno_game.recover_incomplete_sessions(), 1)
            self.assertEqual(keno_game.recover_incomplete_sessions(), 0)
            self.assertEqual(credited, [("123456", 5.0, "keno_recovery_refund")])
            self.assertEqual(
                keno_game._get_owned_session(session_id, "123456")["status"],
                "finished",
            )
        finally:
            keno_game._services = original


if __name__ == "__main__":
    unittest.main()