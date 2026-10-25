import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main


class _Message:
    def __init__(self, user_id, text=""):
        self.from_user = SimpleNamespace(
            id=int(user_id),
            username=None,
            first_name="Test Player",
        )
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class BonusCodeTests(unittest.TestCase):
    def test_owner_command_creates_direct_code_for_one_player(self):
        message = _Message(9000)
        context = SimpleNamespace(args=[
            "VIPCASH", "50", "1", "0", "0", "0", "0",
            "12345", "0", "direct",
        ])
        codes = {}
        with (
            patch.object(main, "bonus_codes", codes),
            patch.object(main, "is_owner_or_manager", return_value=True),
            patch.object(main, "save_data") as save,
        ):
            asyncio.run(
                main.create_code_command(SimpleNamespace(message=message), context)
            )

        self.assertEqual(codes["VIPCASH"]["payout_mode"], "direct")
        self.assertEqual(codes["VIPCASH"]["specific_user"], "12345")
        self.assertEqual(codes["VIPCASH"]["max_uses"], 1)
        self.assertEqual(codes["VIPCASH"]["min_deposit"], 0)
        self.assertEqual(codes["VIPCASH"]["min_wager_total"], 0)
        self.assertEqual(codes["VIPCASH"]["wager_multiplier"], 0)
        save.assert_called_once()
        self.assertIn("Direct to cash balance", message.replies[-1])

    def test_owner_wizard_creates_personal_direct_code_without_wager_steps(self):
        owner_id = "9000"
        codes = {}
        wizard = {owner_id: {"step": 0}}
        states = {owner_id: main.UserState.OWNER_BONUS_CREATE}
        answers = ["VIPONLY", "25", "9", "0", "0", "0", "12345", "direct"]

        with (
            patch.object(main, "bonus_codes", codes),
            patch.object(main, "owner_bonus_wizard", wizard),
            patch.object(main, "user_states", states),
            patch.object(main, "save_data") as save,
        ):
            for answer in answers:
                message = _Message(owner_id, answer)
                handled = asyncio.run(
                    main.handle_bonus_wizard_message(
                        SimpleNamespace(message=message), SimpleNamespace()
                    )
                )
                self.assertTrue(handled)

        code = codes["VIPONLY"]
        self.assertEqual(code["payout_mode"], "direct")
        self.assertEqual(code["specific_user"], "12345")
        self.assertEqual(code["max_uses"], 1)
        self.assertEqual(code["min_deposit"], 0)
        self.assertEqual(code["min_wager_total"], 0)
        self.assertEqual(code["wager_multiplier"], 0)
        self.assertEqual(code["wager_hours"], 0)
        save.assert_called_once()
        self.assertNotIn(owner_id, wizard)
        self.assertEqual(states[owner_id], main.UserState.NONE)

    def test_direct_claim_credits_cash_immediately_and_rejects_other_players(self):
        code_data = {
            "reward": 25.0,
            "max_uses": 1,
            "uses": 0,
            "expires": None,
            "min_deposit": 0,
            "min_wager_total": 0,
            "wager_multiplier": 0,
            "wager_hours": 0,
            "payout_mode": "direct",
            "specific_user": "12345",
        }
        codes = {"VIPONLY": code_data}
        claims = {}
        pending = {}
        message = _Message(12345)
        context = SimpleNamespace(args=["VIPONLY"])

        with (
            patch.object(main, "bonus_codes", codes),
            patch.object(main, "user_claimed_codes", claims),
            patch.object(main, "user_pending_bonus_claims", pending),
            patch.object(main, "user_bonus_wager_data", {"12345": {"expires_at": 9999999999}}),
            patch.object(main, "user_deposit_totals", {}),
            patch.object(main, "user_wagering_totals", {}),
            patch.object(main, "get_user_currency", return_value="USD"),
            patch.object(main, "get_real_user_balance", return_value=10.0),
            patch.object(main, "ultra_secure_add_user_balance", return_value=35.0) as credit,
            patch.object(main, "save_data_critical") as save,
        ):
            asyncio.run(
                main.claim_code_command(SimpleNamespace(message=message), context)
            )

        credit.assert_called_once_with("12345", 25.0, "bonus_code_direct")
        save.assert_called_once()
        self.assertEqual(code_data["uses"], 1)
        self.assertEqual(claims["12345"], ["VIPONLY"])
        self.assertEqual(pending, {})
        self.assertIn("added directly to your cash balance", message.replies[-1])

        wrong_player_message = _Message(67890)
        with (
            patch.object(main, "bonus_codes", codes),
            patch.object(main, "user_claimed_codes", {}),
            patch.object(main, "user_pending_bonus_claims", {}),
            patch.object(main, "user_deposit_totals", {}),
            patch.object(main, "user_wagering_totals", {}),
            patch.object(main, "get_user_currency", return_value="USD"),
            patch.object(main, "ultra_secure_add_user_balance") as wrong_player_credit,
        ):
            asyncio.run(
                main.claim_code_command(
                    SimpleNamespace(message=wrong_player_message), context
                )
            )
        wrong_player_credit.assert_not_called()
        self.assertIn("not available for your account", wrong_player_message.replies[-1])

    def test_direct_claim_enforces_configured_deposit_and_wager_thresholds(self):
        code_data = {
            "reward": 25.0,
            "max_uses": 1,
            "uses": 0,
            "expires": None,
            "min_deposit": 10.0,
            "min_wager_total": 100.0,
            "payout_mode": "direct",
            "specific_user": "12345",
        }
        codes = {"VIPONLY": code_data}

        for deposits, wagered, expected_text in (
            (5.0, 200.0, "total deposits"),
            (10.0, 50.0, "total wagering"),
        ):
            message = _Message(12345)
            with (
                patch.object(main, "bonus_codes", codes),
                patch.object(main, "user_claimed_codes", {}),
                patch.object(main, "user_pending_bonus_claims", {}),
                patch.object(main, "user_deposit_totals", {"12345": deposits}),
                patch.object(main, "user_wagering_totals", {"12345": wagered}),
                patch.object(main, "get_user_currency", return_value="USD"),
                patch.object(main, "ultra_secure_add_user_balance") as credit,
            ):
                asyncio.run(
                    main.claim_code_command(
                        SimpleNamespace(message=message),
                        SimpleNamespace(args=["VIPONLY"]),
                    )
                )
            credit.assert_not_called()
            self.assertIn(expected_text, message.replies[-1])
            self.assertEqual(code_data["uses"], 0)

    def test_existing_wagered_code_still_waits_for_a_deposit(self):
        code_data = {
            "reward": 20.0,
            "max_uses": 1,
            "uses": 0,
            "expires": None,
            "min_deposit": 0,
            "wager_multiplier": 2.0,
            "wager_hours": 24.0,
            "specific_user": None,
        }
        codes = {"OLDSTYLE": code_data}
        claims = {}
        pending = {}
        message = _Message(12345)

        with (
            patch.object(main, "bonus_codes", codes),
            patch.object(main, "user_claimed_codes", claims),
            patch.object(main, "user_pending_bonus_claims", pending),
            patch.object(main, "user_bonus_wager_data", {}),
            patch.object(main, "user_deposit_totals", {}),
            patch.object(main, "user_wagering_totals", {}),
            patch.object(main, "get_user_currency", return_value="USD"),
            patch.object(main, "ultra_secure_add_user_balance") as credit,
            patch.object(main, "save_data_critical") as save,
        ):
            asyncio.run(
                main.claim_code_command(
                    SimpleNamespace(message=message),
                    SimpleNamespace(args=["OLDSTYLE"]),
                )
            )

        credit.assert_not_called()
        save.assert_called_once()
        self.assertEqual(code_data["uses"], 1)
        self.assertEqual(claims["12345"], ["OLDSTYLE"])
        self.assertEqual(pending["12345"]["code"], "OLDSTYLE")
        self.assertIn("BONUS CODE RESERVED", message.replies[-1])


if __name__ == "__main__":
    unittest.main()
