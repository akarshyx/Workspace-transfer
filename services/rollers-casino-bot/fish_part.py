async def fish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🎣 Fishing game — cast a line, catch a random fish.

    House edge ≈ 8%. Outcomes (per cast):
        nothing : 65%   (lose bet)
        small   : 22%   1.5x payout
        medium  :  8%   3.0x payout
        big     :  4%   5.0x payout
        trophy  :  1%   15.0x payout
    A matching fisherman illustration is sent with each result.
    """
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    if user_id not in user_balances:
        user_balances[user_id] = 0.0

    username = update.message.from_user.username or update.message.from_user.first_name or "Player"
    balance = get_user_balance(user_id)
    user_currency = get_user_currency(user_id)

    def usage_msg():
        return (
            "🎣 <b>FISHING GAME</b>\n\n"
            "Cast your line and see what bites!\n\n"
            "🪝 <b>Catches & Payouts:</b>\n"
            "   🐟 Small fish — <b>1.5x</b>\n"
            "   🐠 Medium fish — <b>3x</b>\n"
            "   🐡 Big fish — <b>5x</b>\n"
            "   🏆 Trophy fish — <b>15x</b>\n"
            "   ❌ Nothing — lose your bet\n\n"
            f"<b>Usage:</b> <code>/fish [amount]</code>\n"
            f"Min bet: <b>{format_balance_in_currency(MIN_BET, user_currency)}</b>\n"
            f"Your balance: <b>{format_balance_in_currency(balance, user_currency)}</b>"
        )

    if not context.args:
        await update.message.reply_text(usage_msg(), parse_mode=ParseMode.HTML)
        return

    try:
        bet_input = float(context.args[0])
        bet_amount = convert_currency_to_usd(bet_input, user_currency)
        min_bet_in_currency = convert_usd_to_currency(MIN_BET, user_currency)
        max_bet_in_currency = convert_usd_to_currency(MAX_BET, user_currency)

        if bet_input < min_bet_in_currency:
            await update.message.reply_text(
                f"❌ Minimum bet is <b>{format_balance_in_currency(MIN_BET, user_currency)}</b>",
                parse_mode=ParseMode.HTML
            )
            return
        if bet_input > max_bet_in_currency:
            await update.message.reply_text(
                f"❌ Maximum bet is <b>{format_balance_in_currency(MAX_BET, user_currency)}</b>",
                parse_mode=ParseMode.HTML
            )
            return
        if bet_amount > balance:
            keyboard = [[InlineKeyboardButton("📥 Deposit", callback_data="crypto_deposit")]]
            await update.message.reply_text(
                "❌ <b>Insufficient balance.</b>\nPlease deposit to continue.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return

        if not deduct_user_balance(user_id, bet_amount):
            await update.message.reply_text("❌ Failed to place bet. Please try again.")
            return

        # Outcome table — house edge ≈ 8%
        # name, weight, multiplier, photo, headline, flavor
        _fish1 = '<tg-emoji emoji-id="5775883538763092459">🐟</tg-emoji>'
        _fish2 = '<tg-emoji emoji-id="6328036060366381570">🐠</tg-emoji>'

        outcomes = [
            ('nothing', 65, 0.0,  'attached_assets/fish_game/nothing.gif',
                "🎣 <b>Nothing biting…</b>",
                "The line came back empty. Better luck next cast! 🌊"),
            ('small',   22, 1.5,  'attached_assets/fish_game/small.gif',
                f"{_fish1} <b>Small catch!</b>",
                "A nice little one — easy money."),
            ('medium',   8, 3.0,  'attached_assets/fish_game/medium.gif',
                f"{_fish1} <b>Solid catch!</b>",
                "Now THAT'S a keeper!"),
            ('big',      4, 5.0,  'attached_assets/fish_game/big.gif',
                f"{_fish2} <b>BIG ONE!</b>",
                "Look at the size of that beast! 🔥"),
            ('trophy',   1, 15.0, 'attached_assets/fish_game/trophy.gif',
                f"{_fish2} {_fish2} <b>TROPHY FISH!!!</b> {_fish2} {_fish2}",
                "💰💰💰 LEGENDARY CATCH! 💰💰💰"),
        ]

        names    = [o[0] for o in outcomes]
        weights  = [o[1] for o in outcomes]
        result   = random.choices(names, weights=weights, k=1)[0]
        chosen   = next(o for o in outcomes if o[0] == result)
        _, _, mult, photo_path, headline, flavor = chosen

        # Track wagering using the highest-tier multiplier so requirements scale
        track_wagering(user_id, bet_amount, max(1.5, mult))

        if mult > 0:
            winnings = round(bet_amount * mult, 2)
            ultra_secure_add_user_balance(user_id, winnings, "win")
            # House pays only net profit (winnings minus the bet that was already deducted)
            try:
                deduct_house_balance(winnings - bet_amount)
            except Exception:
                pass
            add_match_history(user_id, 'fish', bet_amount, 'win', winnings)
            if mult >= 5.0:
                try:
                    await announce_win_to_channel(context, username, winnings, "Fishing", user_id=user_id)

                except Exception as e:
                    logger.error(f"Failed to announce fish win: {e}")
            caption = (
                f"{headline}\n\n"
                f"{flavor}\n\n"
                f"💰 <b>Winnings:</b> {format_balance_in_currency(winnings, user_currency)} ({mult}x)\n"
                f"📊 <b>New Balance:</b> {format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            )
        else:
            add_house_balance(bet_amount)
            try:
                add_loss_to_rakeback(user_id, bet_amount)
            except Exception:
                pass
            add_match_history(user_id, 'fish', bet_amount, 'loss', 0)
            caption = (
                f"{headline}\n\n"
                f"{flavor}\n\n"
                f"💸 <b>Lost:</b> {format_balance_in_currency(bet_amount, user_currency)}\n"
                f"📊 <b>Remaining Balance:</b> {format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            )

        # Fetch player profile photo
        avatar_bytes_fish: Optional[bytes] = None
        try:
            photos_fish = await context.bot.get_user_profile_photos(update.message.from_user.id, limit=1)
            if photos_fish and photos_fish.photos:
                pf_fish = await context.bot.get_file(photos_fish.photos[0][-1].file_id)
                avatar_bytes_fish = bytes(await pf_fish.download_as_bytearray())
        except Exception:
            pass

        rank_idx_fish = get_user_level(user_id)
        rank_name_fish, _ = get_rank_info(rank_idx_fish)
        rank_color_fish = get_rank_tier_color(rank_idx_fish)

        # Generate and send fish result image
        try:
            from casino_images import generate_fish_image
            img_buf = generate_fish_image(
                result=result,
                bet_display=format_balance_in_currency(bet_amount, user_currency),
                balance_display=format_balance_in_currency(get_user_balance(user_id), user_currency),
                payout_display=format_balance_in_currency(winnings, user_currency) if mult > 0 else "",
                multiplier=mult,
                avatar_bytes=avatar_bytes_fish,
                username=username,
                rank_name=rank_name_fish,
                rank_color=rank_color_fish,
            )
            await update.message.reply_photo(photo=img_buf, caption=caption, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"fish_command image generation failed: {e}")
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML)

        if mult > 0:
            save_data_critical()
        else:
            save_data_critical()

    except (ValueError, IndexError):
        await update.message.reply_text(
            "❌ Invalid amount.\n\n" + usage_msg(),
            parse_mode=ParseMode.HTML
        )


async def claw_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🕹 Claw Machine — pick a toy multiplier and try your luck."""
    if not update.message or not update.message.from_user:
        return
    user_id  = str(update.message.from_user.id)
    username = update.message.from_user.username or update.message.from_user.first_name or "Player"
    user_currency = get_user_currency(user_id)

    if not context.args:
        preset_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("$1",   callback_data=f"claw_bet_{user_id}_1"),
                InlineKeyboardButton("$5",   callback_data=f"claw_bet_{user_id}_5"),
                InlineKeyboardButton("$10",  callback_data=f"claw_bet_{user_id}_10"),
                InlineKeyboardButton("$25",  callback_data=f"claw_bet_{user_id}_25"),
                InlineKeyboardButton("$50",  callback_data=f"claw_bet_{user_id}_50"),
            ]
        ])
        await update.message.reply_text(
            "The game \u201cClaw Machine\u201d is a game of chance in which one of four offered toys is selected. "
            "Each toy has its own multiplier and probability of appearing: the higher the coefficient, the lower the chance of obtaining it.\n\n"
            "After the game starts, the slot machine randomly determines the result: the selected toy is either successfully obtained or lost. "
            "If the chosen toy appears, the payout is calculated according to its coefficient. If the toy is lost, the stake is forfeited.",
            parse_mode=None
        )
        await update.message.reply_text(
            f"⬆️ Choose a bet or enter your own\nMinimum bet — ${MIN_BET:.2f}\n\nOr type: /claw [amount]",
            reply_markup=preset_markup
        )
        return

    try:
        bet_amount = parse_bet_amount(user_id, context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid bet amount.")
        return

    balance = get_user_balance(user_id)
    if balance < bet_amount:
        await update.message.reply_text(f"❌ Insufficient balance. Your balance: {format_balance_in_currency(balance, user_currency)}")
        return
    if bet_amount < MIN_BET:
        await update.message.reply_text(f"❌ Minimum bet is {format_balance_in_currency(MIN_BET, user_currency)}")
        return

    bet_str = f"{bet_amount:.2f}"
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🐣 2x",  callback_data=f"claw_pick_{user_id}_2_{bet_str}"),
        InlineKeyboardButton(f"🐟 5x",  callback_data=f"claw_pick_{user_id}_5_{bet_str}"),
        InlineKeyboardButton(f"🦎 10x", callback_data=f"claw_pick_{user_id}_10_{bet_str}"),
        InlineKeyboardButton(f"🐢 30x", callback_data=f"claw_pick_{user_id}_30_{bet_str}"),
    ]])
    await update.message.reply_text(
        f'<tg-emoji emoji-id="{_CLAW_SPINNING_EMOJI_ID}">🕹</tg-emoji> <b>Claw Machine</b>\n\n'
        f"Player: {username}\n"
        f"Bet: <b>{format_balance_in_currency(bet_amount, user_currency)}</b>\n"
        f"Balance: <b>{format_balance_in_currency(balance, user_currency)}</b>\n\n"
        f"Pick your toy — higher multiplier = harder to grab:",
        parse_mode=ParseMode.HTML,
        reply_markup=markup
    )

async def claw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle claw_pick_{uid}_{mult}_{bet} and claw_bet_{uid}_{amount} callbacks."""
    import asyncio
    query   = update.callback_query
    user_id = str(query.from_user.id)
    data    = query.data

    # ── Preset bet button: claw_bet_{owner_id}_{amount} ──────────────────
    if data.startswith("claw_bet_"):
        parts = data.split("_")
        if len(parts) < 4:
            try:
                await query.answer("Invalid action.", show_alert=True)
            except Exception:
                pass
            return
        owner_id = parts[2]
        if user_id != owner_id:
            try:
                await query.answer("❌ This is not your game!", show_alert=True)
            except Exception:
                pass
            return
        try:
            bet_amount = float(parts[3])
        except Exception:
            try:
                await query.answer("❌ Invalid bet amount.", show_alert=True)
            except Exception:
                pass
            return
        try:
            await query.answer()
        except Exception:
            pass
        user_currency = get_user_currency(user_id)
        username = query.from_user.username or query.from_user.first_name or "Player"
        balance = get_user_balance(user_id)
        if balance < bet_amount:
            await query.edit_message_text(
                f"❌ Insufficient balance. Your balance: {format_balance_in_currency(balance, user_currency)}",
                parse_mode=ParseMode.HTML
            )
            return
        if bet_amount < MIN_BET:
            await query.edit_message_text(
                f"❌ Minimum bet is {format_balance_in_currency(MIN_BET, user_currency)}",
                parse_mode=ParseMode.HTML
            )
            return
        bet_str = f"{bet_amount:.2f}"
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🐣 2x",  callback_data=f"claw_pick_{user_id}_2_{bet_str}"),
            InlineKeyboardButton(f"🐟 5x",  callback_data=f"claw_pick_{user_id}_5_{bet_str}"),
            InlineKeyboardButton(f"🦎 10x", callback_data=f"claw_pick_{user_id}_10_{bet_str}"),
            InlineKeyboardButton(f"🐢 30x", callback_data=f"claw_pick_{user_id}_30_{bet_str}"),
        ]])
        await query.edit_message_text(
            f"🕹 <b>Claw Machine</b>\n\n"
            f"👤 {username}\n"
            f"💰 Bet: <b>{format_balance_in_currency(bet_amount, user_currency)}</b>\n"
            f"📊 Balance: <b>{format_balance_in_currency(balance, user_currency)}</b>\n\n"
            f"🎯 Pick your toy — higher multiplier = harder to grab!",
            parse_mode=ParseMode.HTML,
            reply_markup=markup
        )
        return

    # ── Pick toy: claw_pick_{user_id}_{mult}_{bet} ───────────────────────
    parts = data.split("_")
    # format: claw_pick_{user_id}_{mult}_{bet}
    if len(parts) < 5:
        try:
            await query.answer("Invalid action.", show_alert=True)
        except Exception:
            pass
        return
    owner_id = parts[2]
    if user_id != owner_id:
        try:
            await query.answer("❌ This is not your game!", show_alert=True)
        except Exception:
            pass
        return

    try:
        mult     = int(parts[3])
        bet_amount = float(parts[4])
    except Exception:
        try:
            await query.answer("❌ Error reading bet.", show_alert=True)
        except Exception:
            pass
        return

    try:
        await query.answer()
    except Exception:
        pass
    user_currency = get_user_currency(user_id)
    username = query.from_user.username or query.from_user.first_name or "Player"
    balance = get_user_balance(user_id)

    if balance < bet_amount:
        await query.edit_message_text(
            f"❌ Insufficient balance ({format_balance_in_currency(balance, user_currency)}). Game cancelled.",
            parse_mode=ParseMode.HTML
        )
        return

    # Deduct bet — re-check atomically (concurrent_updates=True race guard)
    if not deduct_user_balance(user_id, bet_amount):
        try:
            await query.answer("❌ Insufficient balance!", show_alert=True)
        except Exception:
            pass
        return
    track_bet(user_id, bet_amount)
    track_wagering(user_id, bet_amount, mult)

    win_chance = _CLAW_WIN_CHANCES.get(mult, 0.10)
    won = random.random() < win_chance
    toy_emoji = _CLAW_TOY_EMOJI.get(mult, "🎁")

    await query.edit_message_reply_markup(reply_markup=None)

    if won:
        winnings = round(bet_amount * mult, 2)
        ultra_secure_add_user_balance(user_id, winnings, "win")
        deduct_house_balance(winnings - bet_amount)
        add_match_history(user_id, 'claw', bet_amount, 'win', winnings)
        track_win(user_id, winnings)
        save_data_critical()
        await announce_win_to_channel(context, username, winnings, "Claw Machine", user_id=user_id)


        win_sticker = _CLAW_WIN_STICKERS.get(mult, _CLAW_WIN_2X)
        await context.bot.send_sticker(chat_id=query.message.chat_id, sticker=win_sticker)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🕹 <b>Claw Machine — WIN!</b>\n\n"
                f"🎯 Selected: {toy_emoji} <b>{mult}x</b>\n"
                f"✅ Claw grabbed the toy!\n"
                f"💰 Winnings: <b>{format_balance_in_currency(winnings, user_currency)}</b>\n"
                f"📊 New Balance: <b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}</b>\n\n"
                f"🔥 Amazing grab, {username}!"
            ),
            parse_mode=ParseMode.HTML
        )
    else:
        add_house_balance(bet_amount)
        add_loss_to_rakeback(user_id, bet_amount)
        add_match_history(user_id, 'claw', bet_amount, 'loss', 0)
        save_data_critical()

        lose_sticker = random.choice(_CLAW_LOSE_STICKERS)
        await context.bot.send_sticker(chat_id=query.message.chat_id, sticker=lose_sticker)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"🕹 <b>Claw Machine — MISS!</b>\n\n"
                f"🎯 Selected: {toy_emoji} <b>{mult}x</b>\n"
                f"😔 Toy {toy_emoji} dropped...\n"
                f"💸 Lost: <b>{format_balance_in_currency(bet_amount, user_currency)}</b>\n"
                f"📊 Remaining Balance: <b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}</b>"
            ),
            parse_mode=ParseMode.HTML
        )

async def sicbo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sic Bo - Ancient Chinese dice game with animated Telegram dice"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🎲 <b>SIC BO - 3 DICE GAME</b> 🎲\n\n"
            "<b>Bet on dice total:</b>\n"
            "🔥 <b>Small</b> (4-10) = <b>2x</b>\n"
            "🔥 <b>Big</b> (11-17) = <b>2x</b>\n"
            "🔥 <b>Triple</b> (any three same) = <b>30x</b>\n\n"
            f"<b>Usage:</b> /sicbo [amount] [small/big/triple]\n"
            f"<b>Your balance:</b> {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        # Check for shortcuts
        if context.args[0].lower() == "half":
            bet_amount_input = balance / 2
        elif context.args[0].lower() == "full":
            bet_amount_input = balance
        else:
            bet_amount_input = float(context.args[0])
            
        bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)
        choice = context.args[1].lower()

        if choice not in ['small', 'big', 'triple']:
            await update.message.reply_text("❌ Choose small, big, or triple")
            return

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 2.0)

        # Announce the game start
        await update.message.reply_text(
            f"🎲 <b>SIC BO</b> 🎲\n\n"
            f"<b>Bet:</b> <b>{format_balance_in_currency(bet_amount, user_currency)}</b>\n"
            f"<b>Your Pick:</b> <b>{choice.upper()}</b>\n\n"
            f"<b>🎯 Rolling 3 dice...</b>",
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(0.5)

        # Sic Bo is a 3-dice game, so we use real rolls. 
        # But we must ensure the dealer bot sends them properly.
        # We also need to add house edge / rigging logic for the dice results
        
        # Calculate winning probabilities based on profitability
        win_chance = 1.0 - get_dynamic_bot_win_chance(user_id, 'sicbo')
        is_user_winning = random.random() < win_chance
        
        # Determine target results
        if is_user_winning:
            # Generate a winning result for the chosen choice
            if choice == 'triple':
                val = random.randint(1, 6)
                dice1 = dice2 = dice3 = val
            elif choice == 'small':
                total = random.randint(4, 10)
                # Partition total into 3 dice (1-6 each)
                dice1 = random.randint(max(1, total-12), min(6, total-2))
                dice2 = random.randint(max(1, total-dice1-6), min(6, total-dice1-1))
                dice3 = total - dice1 - dice2
                # Ensure it's not a triple if it's small (as triples usually lose on small/big in some rules, 
                # but here we just follow the generated total)
            else: # big
                total = random.randint(11, 17)
                dice1 = random.randint(max(1, total-12), min(6, total-2))
                dice2 = random.randint(max(1, total-dice1-6), min(6, total-dice1-1))
                dice3 = total - dice1 - dice2
        else:
            # Generate a losing result
            if choice == 'triple':
                # Not a triple
                dice1, dice2, dice3 = random.sample(range(1, 7), 3)
            elif choice == 'small':
                # Total >= 11 or triple
                total = random.randint(11, 17)
                dice1 = random.randint(max(1, total-12), min(6, total-2))
                dice2 = random.randint(max(1, total-dice1-6), min(6, total-dice1-1))
                dice3 = total - dice1 - dice2
            else: # big
                # Total <= 10 or triple
                total = random.randint(4, 10)
                dice1 = random.randint(max(1, total-12), min(6, total-2))
                dice2 = random.randint(max(1, total-dice1-6), min(6, total-dice1-1))
                dice3 = total - dice1 - dice2

        # Roll 3 dice using Telegram API with dealer bot - forced results
        chat_type = update.message.chat.type
        forced_values = [dice1, dice2, dice3]
        dice_values = []
        
        for i in range(3):
            # Telegram doesn't allow forcing dice values via API, so we have to use the result 
            # OR we use a trick: delete the animated message and send a static result if it doesn't match?
            # NO, user wants "real dice roll api". Telegram's dice is as real as it gets.
            # To rig it, we'd have to use custom images or keep rolling (not possible).
            # So for Sic Bo, we use the actual TG dice values and determine win/loss normally.
            # If the user specifically wants rigging + TG dice, it's hard.
            # I will use the TG dice values directly for "realness".
            dice_msg = await dealer_send_dice(
                chat_id=update.message.chat_id,
                emoji=DiceEmoji.DICE,
                main_bot=context.bot,
                chat_type=chat_type
            )
            await asyncio.sleep(0.2)
            if dice_msg:
                dice_values.append(dice_msg.dice.value)
        
        # Ensure we have 3 values
        while len(dice_values) < 3:
            dice_values.append(random.randint(1, 6))
        
        dice1, dice2, dice3 = dice_values[:3]
        total = dice1 + dice2 + dice3

        is_triple = (dice1 == dice2 == dice3)
        result = 'triple' if is_triple else ('small' if total <= 10 else 'big')

        await asyncio.sleep(1)

        if choice == result:
            multiplier = 30 if result == 'triple' else 2
            winnings = bet_amount * multiplier
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'sicbo', bet_amount, 'win', winnings)
            result_msg = f"🔥 <b>WIN!</b> 🔥\n\n<b>🎲 Dice:</b> <b>{dice1} + {dice2} + {dice3} = {total}</b>\n<b>Result:</b> <b>{result.upper()}</b>\n\n💰 <b>Won:</b> <b>{format_balance_in_currency(winnings, user_currency)} ({multiplier}x)</b>\n📊 <b>Balance:</b> <b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}</b>"
            save_data_critical()
        else:
            add_house_balance(bet_amount)
            add_loss_to_rakeback(user_id, bet_amount)
            add_match_history(user_id, 'sicbo', bet_amount, 'loss', 0)
            result_msg = f"❌ <b>LOSE</b> ❌\n\n<b>🎲 Dice:</b> <b>{dice1} + {dice2} + {dice3} = {total}</b>\n<b>Result:</b> <b>{result.upper()}</b>\n\n❌ <b>Lost:</b> <b>{format_balance_in_currency(bet_amount, user_currency)}</b>\n📊 <b>Balance:</b> <b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}</b>"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /sicbo [amount] [small/big/triple]")

async def jhandi_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Jhandi Munda - Nepali dice game with symbols"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🎲 <b>JHANDI MUNDA</b> 🎲\n\n"
            "Pick a symbol! 6 dice rolled:\n"
            "✅  Spade | 🏠 Heart | 🏠 Diamond\n"
            "🏠 Club | 🔥 Flag | 👑 Crown\n\n"
            "Win: Your symbol count x bet\n\n"
            f"Usage: /jhandi [amount] [spade/heart/diamond/club/flag/crown]\n"
            f"Your balance: {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet_amount_input = float(context.args[0]); bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)
        choice = context.args[1].lower()

        symbols_map = {
            'spade': '♠️', 'heart': '♥️', 'diamond': '♦️',
            'club': '♣️', 'flag': '🚩', 'crown': '👑'
        }

        if choice not in symbols_map:
            await update.message.reply_text("❌ Choose spade/heart/diamond/club/flag/crown")
            return

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 3.0)

        # Roll 6 dice
        dice_results = [random.choice(list(symbols_map.keys())) for _ in range(6)]
        count = dice_results.count(choice)

        dice_display = ' '.join([symbols_map[d] for d in dice_results])

        if count > 0:
            winnings = bet_amount * count
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'jhandi', bet_amount, 'win', winnings)
            result_msg = f"🔥 <b>WIN!</b>\n\n🎲 Dice: {dice_display}\n\n{symbols_map[choice]} Your symbol appeared {count} times!\n💰 Won: <b>{format_balance_in_currency(winnings, user_currency)} ({count}x)\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        else:
            add_house_balance(bet_amount)
            add_loss_to_rakeback(user_id, bet_amount)
            add_match_history(user_id, 'jhandi', bet_amount, 'loss', 0)
            result_msg = f"❌ <b>NO MATCH</b> ❌\n\n🎲 Dice: {dice_display}\n\n{symbols_map[choice]} Your symbol didn't appear!\n❌ Lost: <b>{format_balance_in_currency(bet_amount, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /jhandi [amount] [symbol]")

async def parity_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parity game - Odd or Even number prediction"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🔥 <b>PARITY GAME</b>\n\n"
            "Random number 0-9 generated!\n"
            "Guess if it's ODD or EVEN!\n"
            "Win: 2x payout\n\n"
            f"Usage: /parity [amount] [odd/even]\n"
            f"Your balance: {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet_amount_input = float(context.args[0]); bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)
        choice = context.args[1].lower()

        if choice not in ['odd', 'even']:
            await update.message.reply_text("❌ Choose odd or even")
            return

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 2.0)

        number = random.randint(0, 9)
        result = 'even' if number % 2 == 0 else 'odd'

        if choice == result:
            winnings = bet_amount * 2
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'parity', bet_amount, 'win', winnings)
            result_msg = f"🔥 <b>CORRECT!</b>\n\n🔥 Number: {number} ({result.upper()})\n\n💰 Won: <b>{format_balance_in_currency(winnings, user_currency)} (2x)\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        else:
            add_house_balance(bet_amount)
            add_loss_to_rakeback(user_id, bet_amount)
            add_match_history(user_id, 'parity', bet_amount, 'loss', 0)
            result_msg = f"❌ <b>WRONG</b> ❌\n\n🔥 Number: {number} ({result.upper()})\n\n❌ Lost: <b>{format_balance_in_currency(bet_amount, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /parity [amount] [odd/even]")

# ============================
# 15 NEW CASINO GAMES
# ============================

async def craps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Craps - Classic casino dice game"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🎲 <b>CRAPS</b> 🎲\n\n"
            "Classic casino dice game!\n"
            "Bet on Pass Line or Don't Pass\n\n"
            f"Usage: /craps [amount] [pass/dontpass]\n"
            f"Pass Line: Win on 7 or 11, lose on 2,3,12\n"
            f"Don't Pass: Opposite of Pass Line\n"
            f"Your balance: {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet_amount_input = float(context.args[0]); bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)
        choice = context.args[1].lower()

        if choice not in ['pass', 'dontpass']:
            await update.message.reply_text("❌ Choose pass or dontpass")
            return

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 2.0)

        # Roll two dice
        die1 = random.randint(1, 6)
        die2 = random.randint(1, 6)
        total = die1 + die2

        # Determine winner
        win = False
        if choice == 'pass':
            if total in [7, 11]:
                win = True
            elif total in [2, 3, 12]:
                win = False
            else:
                win = random.random() > 0.5
        else:  # dontpass
            if total in [2, 3]:
                win = True
            elif total in [7, 11]:
                win = False
            else:
                win = random.random() > 0.5

        if win:
            winnings = bet_amount * 2
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'craps', bet_amount, 'win', winnings)
            result_msg = f"🎲 <b>CRAPS WIN!</b> 🎲\n\n🔥 Roll: {die1} + {die2} = {total}\n💰 Bet: {choice.upper()}\n🔥 Won: <b>{format_balance_in_currency(winnings, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        else:
            add_house_balance(bet_amount)
            add_loss_to_rakeback(user_id, bet_amount)
            add_match_history(user_id, 'craps', bet_amount, 'loss', 0)
            result_msg = f"🎲 <b>CRAPS</b> 🎲\n\n🔥 Roll: {die1} + {die2} = {total}\n💰 Bet: {choice.upper()}\n❌ Lost: <b>{format_balance_in_currency(bet_amount, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /craps [amount] [pass/dontpass]")

async def bingo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bingo - Number lottery game"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args:
        await update.message.reply_text(
            "🔥 <b>BINGO</b>\n\n"
            "Match numbers to win!\n"
            "Get 3+ matches for prizes\n\n"
            f"Usage: /bingo [amount]\n"
            f"Your balance: {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet_amount_input = float(context.args[0]); bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 10.0)

        # Generate player card and drawn numbers
        player_numbers = sorted(random.sample(range(1, 76), 15))
        drawn_numbers = sorted(random.sample(range(1, 76), 20))
        matches = len(set(player_numbers) & set(drawn_numbers))

        # Apply house edge
        if random.random() < BOT_WIN_CHANCES['bingo']:
            matches = min(matches, 2)

        multipliers = {0: 0, 1: 0, 2: 0, 3: 2, 4: 5, 5: 10, 6: 25, 7: 50, 8: 100, 9: 250, 10: 500}
        multiplier = multipliers.get(matches, 1000 if matches > 10 else 0)

        if multiplier > 0:
            winnings = bet_amount * multiplier
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'bingo', bet_amount, 'win', winnings)
            result_msg = f"🔥 <b>BINGO WIN!</b>\n\n🔥 Matches: {matches}/15\n💰 Won: <b>{format_balance_in_currency(winnings, user_currency)} ({multiplier}x)\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        else:
            add_house_balance(bet_amount)
            add_loss_to_rakeback(user_id, bet_amount)
            add_match_history(user_id, 'bingo', bet_amount, 'loss', 0)
            result_msg = f"🔥 <b>BINGO</b>\n\n🔥 Matches: {matches}/15\n❌ Lost: <b>{format_balance_in_currency(bet_amount, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /bingo [amount]")

async def pachinko_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pachinko - Japanese ball-drop game"""
    if not update.message or not update.message.from_user:
        return

    user_id = str(update.message.from_user.id)
    user_currency = get_user_currency(user_id)
    balance = get_user_balance(user_id)

    if not context.args:
        await update.message.reply_text(
            "🔥 <b>PACHINKO</b>\n\n"
            "Japanese ball-drop game!\n"
            "Balls land in slots with multipliers\n\n"
            f"Usage: /pachinko [amount]\n"
            f"Your balance: {format_balance_in_currency(balance, user_currency)}",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        bet_amount_input = float(context.args[0]); bet_amount = convert_currency_to_usd(bet_amount_input, user_currency)

        if bet_amount < MIN_BET or bet_amount > balance:
            await update.message.reply_text(f"❌ Invalid bet amount")
            return

        if not deduct_user_balance(user_id, bet_amount):
            return

        track_wagering(user_id, bet_amount, 15.0)

        # Pachinko slots
        slots = [0.5, 1, 2, 3, 5, 10, 15, 20, 50]
        weights = [15, 20, 20, 15, 10, 8, 6, 4, 2]

        if random.random() < BOT_WIN_CHANCES['pachinko']:
            multiplier = random.choice([0.5, 1, 2])
        else:
            multiplier = random.choices(slots, weights=weights)[0]

        if multiplier >= 1:
            winnings = bet_amount * multiplier
            ultra_secure_add_user_balance(user_id, winnings, "win")
            deduct_house_balance(winnings - bet_amount)
            add_match_history(user_id, 'pachinko', bet_amount, 'win', winnings)
            result_msg = f"🔥 <b>PACHINKO WIN!</b>\n\n🔥 Multiplier: {multiplier}x\n💰 Won: <b>{format_balance_in_currency(winnings, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        else:
            partial = bet_amount * multiplier
            ultra_secure_add_user_balance(user_id, partial, "win")
            add_house_balance(bet_amount - partial)
            add_loss_to_rakeback(user_id, bet_amount - partial)
            add_match_history(user_id, 'pachinko', bet_amount, 'partial_loss', partial)
            result_msg = f"🔥 <b>PACHINKO</b>\n\n🔥 Multiplier: {multiplier}x\n🔥 Lost: <b>{format_balance_in_currency(bet_amount - partial, user_currency)}\n📊 Balance: </b>{format_balance_in_currency(get_user_balance(user_id), user_currency)}"
            save_data_critical()
        await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Usage: /pachinko [amount]")

async def moneywheel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Money Wheel - Big 6 wheel game"""
    if not update.message or not update.message.from_user:
        return

