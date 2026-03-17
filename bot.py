"""NFT Mint Alarm Bot - Main Entry Point"""
import logging
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler
)
from config import BOT_TOKEN
from handlers.admin import (
    start, dashboard, handle_callback, handle_reply_button,
    add_mint_start, add_mint_link, waiting_supply,
    add_channel_start,
    pb_first_time, pb_first_name, pb_next_interval, pb_next_name, pb_price,
    pb_add_cb, pb_done_cb,
    step_first_time, step_phase_names, step_interval, step_prices, step_limits,
    handle_text_input, cancel,
    # State constants — single source of truth in admin.py
    WAITING_LINK, WAITING_FIRST_TIME, WAITING_PHASE_NAMES, WAITING_INTERVAL,
    WAITING_PRICES, WAITING_LIMITS, WAITING_EDIT_VALUE, WAITING_CHANNEL,
    WAITING_SUPPLY, WAITING_CONTRACT, PB_FIRST_NAME, PB_FIRST_TIME, PB_NEXT_INTERVAL,
    PB_NEXT_NAME, PB_PRICE, EDIT_PHASE_VAL,
)
from handlers.alerts import setup_scheduler
from handlers.commands import help_command, status_command

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TEXT = filters.TEXT & ~filters.COMMAND

def main():
    from database import init_db
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("help",      help_command))
    app.add_handler(CommandHandler("status",    status_command))

    # ── Add Mint conversation ──
    mint_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_mint_start, pattern="^add_mint$")],
        states={
            WAITING_LINK:     [MessageHandler(TEXT, add_mint_link)],
            WAITING_SUPPLY:   [MessageHandler(TEXT, waiting_supply)],
            PB_FIRST_TIME:    [MessageHandler(TEXT, pb_first_time)],
            PB_FIRST_NAME:    [MessageHandler(TEXT, pb_first_name)],
            PB_NEXT_INTERVAL: [MessageHandler(TEXT, pb_next_interval)],
            PB_NEXT_NAME:     [MessageHandler(TEXT, pb_next_name)],
            PB_PRICE:         [MessageHandler(TEXT, pb_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    # ── Add Phase continuation ──
    add_phase_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(pb_add_cb, pattern="^pb_add_")],
        states={
            PB_NEXT_INTERVAL: [MessageHandler(TEXT, pb_next_interval)],
            PB_NEXT_NAME:     [MessageHandler(TEXT, pb_next_name)],
            PB_PRICE:         [MessageHandler(TEXT, pb_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    # ── Rebuild Phases ──
    rebuild_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_callback, pattern="^rebuild_phases_"),
            CallbackQueryHandler(handle_callback, pattern="^redo_phases_"),
        ],
        states={
            WAITING_FIRST_TIME:  [MessageHandler(TEXT, step_first_time)],
            WAITING_PHASE_NAMES: [MessageHandler(TEXT, step_phase_names)],
            WAITING_INTERVAL:    [MessageHandler(TEXT, step_interval)],
            WAITING_PRICES:      [MessageHandler(TEXT, step_prices)],
            WAITING_LIMITS:      [MessageHandler(TEXT, step_limits)],
            PB_FIRST_TIME:       [MessageHandler(TEXT, pb_first_time)],
            PB_FIRST_NAME:       [MessageHandler(TEXT, pb_first_name)],
            PB_NEXT_INTERVAL:    [MessageHandler(TEXT, pb_next_interval)],
            PB_NEXT_NAME:        [MessageHandler(TEXT, pb_next_name)],
            PB_PRICE:            [MessageHandler(TEXT, pb_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    # ── Add Channel ──
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_channel_start, pattern="^add_channel$")],
        states={
            WAITING_CHANNEL: [MessageHandler(TEXT, handle_text_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    # ── Edit Field ──
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback, pattern="^ef_"),
                      CallbackQueryHandler(handle_callback, pattern="^ep_field_"),
                      CallbackQueryHandler(handle_callback, pattern="^get_markets_")],
        states={
            WAITING_EDIT_VALUE: [MessageHandler(TEXT, handle_text_input)],
            EDIT_PHASE_VAL:     [MessageHandler(TEXT, handle_text_input)],
            WAITING_CONTRACT:   [MessageHandler(TEXT, handle_text_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )

    app.add_handler(mint_conv)
    app.add_handler(add_phase_conv)
    app.add_handler(rebuild_conv)
    app.add_handler(channel_conv)
    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(pb_done_cb,  pattern="^pb_done_"))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── Reply keyboard button: "➕ Add Mint" needs conversation support ──
    reply_add_mint_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^➕ Add Mint$"), handle_reply_button)],
        states={
            WAITING_LINK:     [MessageHandler(TEXT, add_mint_link)],
            WAITING_SUPPLY:   [MessageHandler(TEXT, waiting_supply)],
            PB_FIRST_TIME:    [MessageHandler(TEXT, pb_first_time)],
            PB_FIRST_NAME:    [MessageHandler(TEXT, pb_first_name)],
            PB_NEXT_INTERVAL: [MessageHandler(TEXT, pb_next_interval)],
            PB_NEXT_NAME:     [MessageHandler(TEXT, pb_next_name)],
            PB_PRICE:         [MessageHandler(TEXT, pb_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True, per_user=True, per_message=False,
    )
    app.add_handler(reply_add_mint_conv)

    # ── Reply keyboard buttons (non-conversation ones) ──
    REPLY_BUTTONS_OTHER = filters.Regex(
        r"^(📋 All Mints|📅 Today's Mints|📢 Channels|🎛 Dashboard|ℹ️ Help)$"
    )
    app.add_handler(MessageHandler(REPLY_BUTTONS_OTHER, handle_reply_button))

    async def post_init(application):
        await setup_scheduler(application)

        # Start the API server
        from api_server import start_api_server, set_telegram_app
        set_telegram_app(application)
        await start_api_server()

        logging.getLogger(__name__).info("🚀 NFT Mint Alarm Bot started!")

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
