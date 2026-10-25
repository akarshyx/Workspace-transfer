from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from gift_purchase import (
    DuplicatePurchaseError,
    GiftPurchaseError,
    PENDING_TTL_SECONDS,
    PurchaseStore,
    _market_listing_price,
    _payment_form_total,
    format_stars,
    parse_collectible_link,
    stars_decimal,
)


class GiftPurchaseTests(unittest.TestCase):
    def test_only_official_nft_gift_links_are_accepted(self):
        self.assertEqual(
            parse_collectible_link("https://t.me/nft/PlushPepe_123?single"),
            "PlushPepe_123",
        )
        self.assertEqual(parse_collectible_link("https://telegram.me/nft/Slug-1"), "Slug-1")
        for bad_url in (
            "http://t.me/nft/Slug",
            "https://not-t.me/nft/Slug",
            "https://t.me/c/123/456",
            "https://t.me/nft/bad%2Fslug",
            "https://t.me:8443/nft/Slug",
        ):
            with self.subTest(url=bad_url), self.assertRaises(GiftPurchaseError):
                parse_collectible_link(bad_url)

    def test_stars_amounts_keep_fractional_units_and_format_cleanly(self):
        self.assertEqual(stars_decimal(SimpleNamespace(amount=12, nanos=500_000_000)), Decimal("12.5"))
        self.assertEqual(
            stars_decimal(SimpleNamespace(amount=4, nanostar_amount=250_000_000)),
            Decimal("4.25"),
        )
        self.assertEqual(format_stars(Decimal("12.500000")), "12.5")

    def test_market_price_requires_one_stars_listing_and_rejects_ton_only(self):
        gift = SimpleNamespace(
            burned=False,
            resale_ton_only=False,
            resell_amount=[SimpleNamespace(amount=28, nanos=0)],
        )
        self.assertEqual(_market_listing_price(gift), Decimal("28"))

        with self.assertRaises(GiftPurchaseError):
            _market_listing_price(
                SimpleNamespace(burned=False, resale_ton_only=True, resell_amount=[])
            )
        with self.assertRaises(GiftPurchaseError):
            _market_listing_price(
                SimpleNamespace(
                    burned=False,
                    resale_ton_only=False,
                    resell_amount=[
                        SimpleNamespace(amount=28, nanos=0),
                        SimpleNamespace(amount=30, nanos=0),
                    ],
                )
            )

    def test_checkout_total_must_be_exact_and_in_stars(self):
        form = SimpleNamespace(
            invoice=SimpleNamespace(
                currency="XTR",
                prices=[
                    SimpleNamespace(amount=12),
                    SimpleNamespace(amount=3),
                ],
            )
        )
        self.assertEqual(_payment_form_total(form), Decimal("15"))
        for bad_form in (
            SimpleNamespace(invoice=SimpleNamespace(currency="TON", prices=[SimpleNamespace(amount=15)])),
            SimpleNamespace(invoice=SimpleNamespace(currency="XTR", prices=[])),
        ):
            with self.subTest(form=bad_form), self.assertRaises(GiftPurchaseError):
                _payment_form_total(bad_form)

    def test_confirmation_claim_is_one_shot_and_unknown_requests_block_duplicates(self):
        with TemporaryDirectory() as temp:
            store = PurchaseStore(Path(temp) / "purchases.sqlite3")
            request_id = store.create_pending(
                source="mtproto",
                owner_id=9,
                chat_id=9,
                item_key="gift-slug",
                item_title="Collectible #1",
                recipient="me",
                recipient_name="My account",
                listing_price=Decimal("20"),
                total=Decimal("20"),
                currency="Stars",
                fee_amount=Decimal("0"),
                details={"slug": "gift-slug", "recipient_spec": "me", "recipient_id": "9"},
                now=100,
            )
            self.assertEqual(store.claim(request_id, now=101)[0], "claimed")
            self.assertEqual(store.claim(request_id, now=102)[0], "processing")
            store.finish(request_id, "unknown", "Network result uncertain")
            with self.assertRaises(DuplicatePurchaseError):
                store.create_pending(
                    source="mtproto",
                    owner_id=9,
                    chat_id=9,
                    item_key="gift-slug",
                    item_title="Collectible #1",
                    recipient="me",
                    recipient_name="My account",
                    listing_price=Decimal("20"),
                    total=Decimal("20"),
                    currency="Stars",
                    fee_amount=Decimal("0"),
                    details={"slug": "gift-slug"},
                    now=103,
                )

    def test_expired_confirmation_cannot_be_claimed(self):
        with TemporaryDirectory() as temp:
            store = PurchaseStore(Path(temp) / "purchases.sqlite3")
            request_id = store.create_pending(
                source="bot_api",
                owner_id=9,
                chat_id=9,
                item_key="gift-id",
                item_title="Gift",
                recipient="123",
                recipient_name="Telegram user 123",
                listing_price=Decimal("5"),
                total=Decimal("5"),
                currency="Stars",
                fee_amount=Decimal("0"),
                details={"gift_id": "gift-id"},
                now=100,
            )
            status, _ = store.claim(
                request_id,
                now=100 + PENDING_TTL_SECONDS + 1,
            )
            self.assertEqual(status, "expired")


if __name__ == "__main__":
    unittest.main()
