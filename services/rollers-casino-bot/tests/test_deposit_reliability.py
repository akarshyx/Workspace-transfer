import asyncio
import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from blockchain_deposit_monitor import AddressScan, ChainTransaction, _normalise_scan_asset


class DepositReliabilityTests(unittest.TestCase):
    """Provider-facing deposit tests with no live credentials or blockchain calls."""

    def setUp(self):
        self.pending = dict(main.nowpayments_pending_deposits)
        self.processed = set(main.processed_payment_ids)
        self.txids = set(main.processed_deposit_txids)
        main.nowpayments_pending_deposits.clear()
        main.processed_payment_ids.clear()
        main.processed_deposit_txids.clear()

    def tearDown(self):
        main.nowpayments_pending_deposits.clear()
        main.nowpayments_pending_deposits.update(self.pending)
        main.processed_payment_ids.clear()
        main.processed_payment_ids.update(self.processed)
        main.processed_deposit_txids.clear()
        main.processed_deposit_txids.update(self.txids)

    @staticmethod
    def _pending(payment_id="p-1", address="TEXACT", currency="usdttrc20"):
        return {
            "user_id": "123456",
            "crypto": currency,
            "pay_currency": currency,
            "network": "TRX",
            "order_id": "dep_123456_1",
            "payment_id": payment_id,
            "pay_address": address,
            "amount_usd": 10.0,
            "expected_coin_amount": 10.0,
            "created_at": time.time() - 5,
            "expires_at": time.time() + 3600,
            "status": "waiting",
            "state": "WAITING_FOR_PAYMENT",
        }

    @staticmethod
    def _provider(
        payment_id="p-1",
        address="TEXACT",
        currency="usdttrc20",
        network="TRX",
        usd=10.0,
        status="finished",
    ):
        return {
            "payment_id": payment_id,
            "order_id": "dep_123456_1",
            "payment_status": status,
            "pay_address": address,
            "pay_currency": currency,
            "network": network,
            "actually_paid": 1.0,
            "pay_amount": 10.0,
            "outcome": {"amount_received_usd": usd, "txid": "tx-1"},
            "payin_hash": "tx-1",
        }

    def test_received_amount_uses_actual_under_and_overpayment(self):
        under = main._nowpayments_received_amount(
            self._provider(usd=4.25), self._pending()
        )
        over = main._nowpayments_received_amount(
            self._provider(usd=25.75), self._pending()
        )
        self.assertEqual(under[0], 4.25)
        self.assertEqual(over[0], 25.75)

        no_amount = dict(self._provider(usd=0))
        no_amount["actually_paid"] = 0
        no_amount["outcome"] = {"txid": "tx-1"}
        self.assertEqual(
            main._nowpayments_received_amount(no_amount, self._pending())[0], 0.0
        )
        self.assertFalse(main._nowpayments_has_received_transaction(no_amount))

    def test_legacy_coin_selection_registers_complete_tracking_record(self):
        class FakeMessage:
            chat_id = 123456

            async def reply_photo(self, **kwargs):
                return None

            async def delete(self):
                return None

        class FakeQuery:
            from_user = SimpleNamespace(id=123456, username="player")
            message = FakeMessage()

            async def answer(self):
                return None

        payment = {
            "payment_id": "legacy-p-1",
            "pay_address": "bc1qexact",
            "pay_amount": 0.0001,
            "price_amount": 1.0,
        }
        with patch.object(
            main, "nowpayments_create_payment", return_value=(payment, None)
        ), patch.object(main, "save_data_critical"):
            asyncio.run(
                main.handle_crypto_deposit_selection(
                    FakeQuery(), SimpleNamespace(), "BTC"
                )
            )

        tracked = main.nowpayments_pending_deposits["legacy-p-1"]
        self.assertEqual(tracked["user_id"], "123456")
        self.assertEqual(tracked["pay_currency"], "btc")
        self.assertEqual(tracked["network"], "BTC")
        self.assertEqual(tracked["expected_coin_amount"], 0.0001)
        self.assertGreater(tracked["expires_at"], tracked["created_at"])
        self.assertEqual(tracked["state"], "WAITING_FOR_PAYMENT")

    def test_exact_poll_credits_confirmed_payment_once_and_persists_state(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        calls = []
        provider = self._provider(usd=12.0)

        with patch.object(main, "nowpayments_get_payment_status", return_value=(provider, None)), \
             patch.object(main, "_process_confirmed_deposit", side_effect=lambda **kwargs: calls.append(kwargs) or True), \
             patch.object(main, "_tg_send_deposit_processing_notification", return_value=True), \
             patch.object(main, "save_data_critical"):
            first = asyncio.run(main._run_nowpayments_monitor_once())
            main.processed_payment_ids.add("p-1")
            second = asyncio.run(main._run_nowpayments_monitor_once())

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["user_id"], "123456")
        self.assertEqual(calls[0]["usd_amount"], 12.0)
        self.assertEqual(main.nowpayments_pending_deposits["p-1"]["state"], "CREDITED")

    def test_poll_timeout_leaves_payment_retryable(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        with patch.object(
            main, "nowpayments_get_payment_status", return_value=(None, "timeout")
        ), patch.object(main, "_process_confirmed_deposit") as processor, patch.object(
            main, "save_data_critical"
        ):
            self.assertEqual(asyncio.run(main._run_nowpayments_monitor_once()), 0)
        processor.assert_not_called()
        self.assertEqual(main.nowpayments_pending_deposits["p-1"]["status"], "waiting")

    def test_poll_rejects_address_and_network_mismatches(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        wrong_address = self._provider(address="TWRONG")
        with patch.object(main, "nowpayments_get_payment_status", return_value=(wrong_address, None)), \
             patch.object(main, "_process_confirmed_deposit") as processor, \
             patch.object(main, "save_data_critical"):
            asyncio.run(main._run_nowpayments_monitor_once())
        processor.assert_not_called()
        self.assertEqual(
            main.nowpayments_pending_deposits["p-1"]["status"], "address_mismatch"
        )

        main.nowpayments_pending_deposits["p-1"] = self._pending()
        wrong_network = self._provider(network="ETH")
        with patch.object(main, "nowpayments_get_payment_status", return_value=(wrong_network, None)), \
             patch.object(main, "_process_confirmed_deposit") as processor, \
             patch.object(main, "save_data_critical"):
            asyncio.run(main._run_nowpayments_monitor_once())
        processor.assert_not_called()
        self.assertEqual(
            main.nowpayments_pending_deposits["p-1"]["status"], "network_mismatch"
        )

    def test_signed_ipn_still_requires_provider_and_saved_record_match(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        original_secret = main.NOWPAYMENTS_IPN_SECRET
        main.NOWPAYMENTS_IPN_SECRET = "test-secret"
        provider = self._provider(usd=9.5)
        payload = {
            "payment_id": "p-1",
            "order_id": "dep_123456_1",
            "payment_status": "finished",
            # Deliberately false values: callback must ignore these.
            "pay_currency": "BTC",
            "actually_paid": 999999,
        }
        signature = hmac.new(
            main.NOWPAYMENTS_IPN_SECRET.encode(),
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha512,
        ).hexdigest()
        calls = []
        try:
            with patch.object(main, "nowpayments_get_payment_status", return_value=(provider, None)), \
                 patch.object(main, "_process_confirmed_deposit", side_effect=lambda **kwargs: calls.append(kwargs) or True), \
                 patch.object(main, "save_data_critical"):
                response = main.app.test_client().post(
                    "/nowpayments_callback",
                    json=payload,
                    headers={"x-nowpayments-sig": signature},
                )
        finally:
            main.NOWPAYMENTS_IPN_SECRET = original_secret

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["user_id"], "123456")
        self.assertEqual(calls[0]["pay_currency"], "USDTTRC20")
        self.assertEqual(calls[0]["usd_amount"], 9.5)

    def test_legacy_order_key_is_migrated_to_payment_key(self):
        pending = self._pending()
        pending.pop("payment_id")
        main.nowpayments_pending_deposits[pending["order_id"]] = pending

        found = main._find_pending_nowpayments_deposit(
            "p-1", pending["order_id"]
        )

        self.assertIs(found, pending)
        self.assertNotIn(pending["order_id"], main.nowpayments_pending_deposits)
        self.assertIs(main.nowpayments_pending_deposits["p-1"], pending)
        self.assertEqual(pending["payment_id"], "p-1")

    def test_confirmation_notification_retries_without_recrediting(self):
        pending = self._pending()
        pending.update(
            {
                "status": "credited",
                "confirmation_notification": {
                    "usd_amount": 12.0,
                    "fee_amount": 0.0,
                    "pay_currency": "USDTTRC20",
                    "coin_amount": 1.0,
                    "txid": "tx-1",
                },
            }
        )
        main.nowpayments_pending_deposits["p-1"] = pending

        with patch.object(
            main, "_tg_send_deposit_notification", return_value=True
        ) as send_confirmation, patch.object(
            main, "_process_confirmed_deposit"
        ) as processor:
            self.assertTrue(main._retry_pending_deposit_notifications(pending))
            self.assertFalse(main._retry_pending_deposit_notifications(pending))

        send_confirmation.assert_called_once()
        processor.assert_not_called()
        self.assertTrue(pending.get("confirmation_notified_at"))

    def test_chain_scan_is_preferred_when_provider_status_is_unavailable(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending(
            address="TEXACT" * 7
        )
        chain_tx = ChainTransaction(
            txid="chain-tx-1",
            address="TEXACT" * 7,
            amount=1.0,
            coin="USDT",
            network="TRX",
            confirmations=20,
            confirmed=True,
        )
        calls = []
        with patch.object(
            main,
            "scan_deposit_address",
            return_value=AddressScan((chain_tx,)),
        ), patch.object(
            main,
            "nowpayments_get_payment_status",
            side_effect=AssertionError("provider status should not be required"),
        ), patch.object(
            main,
            "_process_confirmed_deposit",
            side_effect=lambda **kwargs: calls.append(kwargs) or True,
        ), patch.object(
            main, "_tg_send_deposit_processing_notification", return_value=True
        ), patch.object(
            main, "_tg_send_deposit_notification", return_value=True
        ), patch.object(
            main, "save_data_critical"
        ):
            self.assertEqual(asyncio.run(main._run_nowpayments_monitor_once()), 1)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["txid"], "chain-tx-1")
        self.assertEqual(calls[0]["pay_currency"], "USDTTRC20")
        self.assertEqual(main.nowpayments_pending_deposits["p-1"]["state"], "CREDITED")

    def test_processing_precedes_credit_and_confirmation(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending(
            address="TEXACT" * 7
        )
        events = []
        with patch.object(
            main,
            "_tg_send_deposit_processing_notification",
            side_effect=lambda *args, **kwargs: events.append("processing") or True,
        ), patch.object(
            main,
            "ultra_secure_add_user_balance",
            side_effect=lambda *args, **kwargs: events.append("credit"),
        ), patch.object(
            main,
            "_tg_send_deposit_notification",
            side_effect=lambda *args, **kwargs: events.append("confirmed") or True,
        ), patch.object(
            main, "_persist_payment_id"
        ), patch.object(
            main, "_persist_deposit_txid"
        ), patch.object(
            main, "save_data_critical"
        ), patch.object(
            main, "add_house_balance"
        ), patch.object(
            main, "add_deposit_wagering_requirement"
        ), patch.object(
            main, "_ensure_deposit_consistency"
        ), patch.object(
            main, "_EVENTS_OK", False
        ), patch.object(
            main, "player_data", {"123456": {}}
        ), patch.object(
            main, "user_profiles", {"123456": {"username": "player"}}
        ), patch.object(
            main, "user_deposit_totals", {}
        ), patch.object(
            main, "user_deposit_history", {}
        ), patch.object(
            main, "user_last_deposit_ts", {}
        ), patch.object(
            main, "user_tip_received", {}
        ), patch.object(
            main, "user_pending_bonus_claims", {}
        ), patch.object(
            main, "user_wagering_requirements", {}
        ):
            self.assertTrue(
                main._process_confirmed_deposit(
                    user_id="123456",
                    usd_amount=12.50,
                    credited_amount=12.50,
                    fee_amount=0.0,
                    pay_currency="USDTTRC20",
                    payment_id="p-1",
                    source="chain_monitor",
                    coin_amount=12.50,
                    txid="tx-1",
                )
            )

        self.assertEqual(events, ["processing", "credit", "confirmed"])
        self.assertIn("p-1", main.processed_payment_ids)
        self.assertIn("tx-1", main.processed_deposit_txids)

    def test_processing_and_confirmation_match_compact_reference_templates(self):
        class FakeResponse:
            status_code = 200
            text = ""

        posted = []

        def fake_post(_url, json=None, **_kwargs):
            posted.append(json or {})
            return FakeResponse()

        with patch.dict(main.os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}, clear=False), \
             patch.object(main.requests, "post", side_effect=fake_post), \
             patch.object(main, "CASINO_GROUP_CHAT_ID", None), \
             patch.object(main, "CASINO_GROUP_ID", None):
            self.assertTrue(
                main._tg_send_deposit_processing_notification(
                    "123456",
                    "gram",
                    coin_amount=0.3572,
                    txid="gram-tx-123",
                    network="TON",
                )
            )
            self.assertTrue(
                main._tg_send_deposit_notification(
                    "123456",
                    20.0,
                    "gram",
                    coin_amount=0.3572,
                    fee_amount=0.50,
                    txid="gram-tx-123",
                    network="TON",
                )
            )

        processing_text = posted[0]["text"]
        confirmation_text = posted[1]["text"]
        self.assertEqual(
            processing_text,
            '<tg-emoji emoji-id="5386367538735104399">🔄</tg-emoji> '
            '<b>Processing payment Of 0.3572 GRAM</b>',
        )
        self.assertNotIn("Amount detected", processing_text)
        self.assertNotIn("Waiting for blockchain", processing_text)
        self.assertNotIn("TxID", processing_text)
        self.assertIn("<b>Deposit confirmed</b>", confirmation_text)
        self.assertIn("<b>Currency:</b> <b>GRAM</b>", confirmation_text)
        self.assertIn("<b>Amount :</b> <b>0.3572</b>", confirmation_text)
        self.assertIn("<b>IN USD:</b> <b>20.00$</b>", confirmation_text)
        self.assertIn('emoji-id="6305056190036451310"', confirmation_text)
        self.assertIn('emoji-id="6235568867637207626"', confirmation_text)
        self.assertIn('emoji-id="6305442397790674704"', confirmation_text)
        self.assertIn("https://tonviewer.com/transaction/gram-tx-123", confirmation_text)
        self.assertNotIn("New Balance", confirmation_text)
        self.assertNotIn("Credited", confirmation_text)
        self.assertNotIn("reply_markup", posted[1])

    def test_referrals_register_once_and_count_from_source_mapping(self):
        original_data = dict(main.referral_data)
        original_profiles = dict(main.user_profiles)
        original_daily = dict(main.daily_referral_counts)
        original_balances = dict(main.user_balances)
        try:
            main.referral_data.clear()
            main.user_profiles.clear()
            main.user_balances.clear()
            main.user_profiles["900"] = {"username": "referrer"}
            self.assertTrue(main._register_direct_referral("901", "900"))
            self.assertFalse(main._register_direct_referral("901", "900"))
            self.assertEqual(main._referral_count("900"), 1)
            self.assertEqual(main.user_profiles["900"]["referrals"], 1)
        finally:
            main.referral_data.clear()
            main.referral_data.update(original_data)
            main.user_profiles.clear()
            main.user_profiles.update(original_profiles)
            main.user_balances.clear()
            main.user_balances.update(original_balances)
            main.daily_referral_counts.clear()
            main.daily_referral_counts.update(original_daily)

    def test_group_owner_gets_forty_percent_of_positive_and_negative_results(self):
        original_groups = dict(main.chat_owner_groups)
        original_pending = dict(main.pending_referral_commissions)
        original_wager = dict(main.referral_wager_earnings)
        original_house = getattr(main, "house_balance", 1022.0)
        original_casino = getattr(main, "casino_balance_usd", original_house)
        original_crypto = dict(main.crypto_house_balances)
        token = main._group_revenue_context_cv.set(("901", "-100"))
        try:
            main.chat_owner_groups.clear()
            main.chat_owner_groups["-100"] = {"owner_id": "900"}
            main.pending_referral_commissions.clear()
            main.referral_wager_earnings.clear()
            main.house_balance = 100.0
            main.casino_balance_usd = 100.0
            main.crypto_house_balances.clear()
            main.crypto_house_balances["USDT"] = 100.0
            with patch.object(main, "save_data_critical"):
                main.add_house_balance(10.0)
                main.deduct_house_balance(5.0)
            self.assertEqual(main.pending_referral_commissions["900"], 2.0)
            self.assertEqual(main.referral_wager_earnings["900"], 2.0)
            self.assertEqual(main.house_balance, 103.0)
        finally:
            main._group_revenue_context_cv.reset(token)
            main.chat_owner_groups.clear()
            main.chat_owner_groups.update(original_groups)
            main.pending_referral_commissions.clear()
            main.pending_referral_commissions.update(original_pending)
            main.referral_wager_earnings.clear()
            main.referral_wager_earnings.update(original_wager)
            main.house_balance = original_house
            main.casino_balance_usd = original_casino
            main.crypto_house_balances.clear()
            main.crypto_house_balances.update(original_crypto)

    def test_referral_withdrawal_requires_three_dollars(self):
        original_pending = dict(main.pending_referral_commissions)
        try:
            main.pending_referral_commissions.clear()
            main.pending_referral_commissions["900"] = 2.99
            self.assertLess(main.pending_referral_commissions["900"], 3.0)
            main.pending_referral_commissions["900"] = 3.0
            self.assertGreaterEqual(main.pending_referral_commissions["900"], 3.0)
        finally:
            main.pending_referral_commissions.clear()
            main.pending_referral_commissions.update(original_pending)

    def test_referral_withdrawal_credits_wallet_without_second_house_debit(self):
        class FakeQuery:
            from_user = SimpleNamespace(id=900)

            def __init__(self):
                self.alerts = []
                self.edited = None

            async def answer(self, text=None, show_alert=False):
                self.alerts.append((text, show_alert))

            async def edit_message_text(self, text=None, **kwargs):
                self.edited = {"text": text, **kwargs}

        original_pending = dict(main.pending_referral_commissions)
        original_earnings = dict(main.referral_earnings)
        query = FakeQuery()
        try:
            main.pending_referral_commissions.clear()
            main.pending_referral_commissions["900"] = 3.25
            with patch.object(main, "get_user_currency", return_value="USD"), \
                 patch.object(main, "get_user_balance", return_value=8.25), \
                 patch.object(main, "ultra_secure_add_user_balance") as credit, \
                 patch.object(main, "save_data_critical") as save:
                asyncio.run(main.handle_claim_referral_commission(query, SimpleNamespace()))

            credit.assert_called_once_with("900", 3.25, "referral_commission")
            save.assert_called_once()
            self.assertEqual(main.pending_referral_commissions["900"], 0.0)
            self.assertEqual(main.referral_earnings.get("900", 0.0), 3.25)
            self.assertIn("Referral balance withdrawn", query.edited["text"])
            self.assertEqual(query.alerts, [])
        finally:
            main.pending_referral_commissions.clear()
            main.pending_referral_commissions.update(original_pending)
            main.referral_earnings.clear()
            main.referral_earnings.update(original_earnings)

    def test_referral_screens_have_no_animated_emoji_or_detailed_stats(self):
        class FakeBot:
            async def get_me(self):
                return SimpleNamespace(username="RollersGambleBot")

        class FakeQuery:
            from_user = SimpleNamespace(id=900)
            message = None

            def __init__(self):
                self.edited = None

            async def edit_message_text(self, **kwargs):
                self.edited = kwargs

        original_groups = dict(main.chat_owner_groups)
        query = FakeQuery()
        context = SimpleNamespace(bot=FakeBot())
        try:
            main.chat_owner_groups.clear()
            main.chat_owner_groups["-100"] = {
                "owner_id": "900",
                "title": "Owners & Players",
            }
            with patch.object(main, "get_user_currency", return_value="USD"):
                asyncio.run(main._show_referral_screen(query, context))
            self.assertIn("Revshare - 30%", query.edited["text"])
            self.assertNotIn("<tg-emoji", query.edited["text"])
            self.assertNotIn("Detailed stats", query.edited["text"])
            buttons = [
                button.text
                for row in query.edited["reply_markup"].inline_keyboard
                for button in row
            ]
            self.assertFalse(any("Detailed" in button for button in buttons))

            asyncio.run(main._show_chat_owner_referral_screen(query, context))
            self.assertIn("Owners &amp; Players", query.edited["text"])
            self.assertIn("40%", query.edited["text"])
            self.assertNotIn("<tg-emoji", query.edited["text"])
            self.assertNotIn("Detailed stats", query.edited["text"])
        finally:
            main.chat_owner_groups.clear()
            main.chat_owner_groups.update(original_groups)

    def test_referral_message_context_bypasses_animated_emoji_conversion(self):
        async def verify_context():
            self.assertTrue(main._should_auto_render_premium_emoji("💰"))

            async def inspect_inside_send():
                return main._should_auto_render_premium_emoji("💰")

            should_animate = await main._await_with_plain_emojis(
                inspect_inside_send()
            )
            self.assertFalse(should_animate)
            self.assertTrue(main._should_auto_render_premium_emoji("💰"))

        with patch.object(main, "CUSTOM_EMOJI_MAP", {"💰": "test-emoji-id"}):
            asyncio.run(verify_context())

    def test_confirmed_processor_passes_gross_usd_to_confirmation_renderer(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        with patch.object(main, "_tg_send_deposit_processing_notification", return_value=True), \
             patch.object(main, "_tg_send_deposit_notification", return_value=True) as send_confirmation, \
             patch.object(main, "ultra_secure_add_user_balance"), \
             patch.object(main, "_persist_payment_id"), \
             patch.object(main, "_persist_deposit_txid"), \
             patch.object(main, "save_data_critical"), \
             patch.object(main, "add_house_balance"), \
             patch.object(main, "add_deposit_wagering_requirement"), \
             patch.object(main, "_ensure_deposit_consistency"), \
             patch.object(main, "_EVENTS_OK", False), \
             patch.object(main, "player_data", {"123456": {}}), \
             patch.object(main, "user_profiles", {"123456": {}}), \
             patch.object(main, "user_deposit_totals", {}), \
             patch.object(main, "user_deposit_history", {}), \
             patch.object(main, "user_last_deposit_ts", {}), \
             patch.object(main, "user_tip_received", {}), \
             patch.object(main, "user_pending_bonus_claims", {}), \
             patch.object(main, "user_wagering_requirements", {}):
            self.assertTrue(
                main._process_confirmed_deposit(
                    user_id="123456",
                    usd_amount=20.0,
                    credited_amount=19.5,
                    fee_amount=0.5,
                    pay_currency="GRAM",
                    payment_id="p-1",
                    source="chain_monitor",
                    coin_amount=0.3572,
                    txid="gram-tx-123",
                )
            )

        self.assertEqual(send_confirmation.call_args.args[1], 20.0)
        self.assertEqual(send_confirmation.call_args.kwargs["fee_amount"], 0.5)

    def test_nowpayments_network_currency_alias_is_normalized_for_chain_scan(self):
        self.assertEqual(
            _normalise_scan_asset(
                {"pay_currency": "usdttrc20", "network": "TRX"}
            ),
            ("USDT", "TRX"),
        )
        self.assertEqual(
            _normalise_scan_asset(
                {"pay_currency": "avaxc", "network": "CCHAIN"}
            ),
            ("AVAX", "AVAX"),
        )

    def test_ipn_processor_failure_does_not_clear_post_credit_dedup(self):
        main.nowpayments_pending_deposits["p-1"] = self._pending()
        original_secret = main.NOWPAYMENTS_IPN_SECRET
        main.NOWPAYMENTS_IPN_SECRET = "test-secret"
        provider = self._provider(usd=9.5)
        payload = {
            "payment_id": "p-1",
            "order_id": "dep_123456_1",
            "payment_status": "finished",
        }
        signature = hmac.new(
            main.NOWPAYMENTS_IPN_SECRET.encode(),
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha512,
        ).hexdigest()

        def processor(**kwargs):
            main.processed_payment_ids.add("p-1")
            return False

        try:
            with patch.object(
                main, "nowpayments_get_payment_status", return_value=(provider, None)
            ), patch.object(
                main, "_process_confirmed_deposit", side_effect=processor
            ):
                response = main.app.test_client().post(
                    "/nowpayments_callback",
                    json=payload,
                    headers={"x-nowpayments-sig": signature},
                )
        finally:
            main.NOWPAYMENTS_IPN_SECRET = original_secret

        self.assertEqual(response.status_code, 200)
        self.assertIn("p-1", main.processed_payment_ids)


if __name__ == "__main__":
    unittest.main()