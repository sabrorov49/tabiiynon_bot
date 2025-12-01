import os
import json
import logging
import aiosqlite
from pathlib import Path
from typing import Dict, Any, List

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter

class NameState(StatesGroup):
    waiting_for_name = State()

# --- SETTINGS ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "8428424328:AAEzKaMTCIYfiSmsfwtB7zy9iB3Qut6mW2Y"
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID") or "7880534797")

DATA_DIR = Path(__file__).parent
MENU_FILE = DATA_DIR / "menu.json"
IMAGES_DIR = DATA_DIR / "images"
DB_FILE = DATA_DIR / "orders.db"

# LOGGING
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- LOAD MENU ---
def load_menu() -> List[Dict[str, Any]]:
    try:
        with open(MENU_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading menu.json: {e}")
        return []

MENU = load_menu()
MENU_BY_ID = {item["id"]: item for item in MENU}

# --- DATABASE ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                user_name TEXT,
                phone TEXT,
                address TEXT,
                total INTEGER,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                product_id INTEGER,
                name TEXT,
                price INTEGER,
                qty INTEGER
            );
        """)
        await db.commit()

# --- CARTS ---
carts: Dict[int, Dict[int, int]] = {}

# --- ORDER COUNTER ---
order_counter = 1   # <<< BUYURTMA RAQAM TIZIMI SHU YERGA QO‘YILADI

# --- FSM ----
class CheckoutStates(StatesGroup):
    awaiting_phone = State()
    awaiting_address = State()
    confirm = State()


# --- LOCATION KEYBOARD ---
location_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# --- KEYBOARD (Reply Keyboard) ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍞 Menyu")],
        [KeyboardButton(text="🛒 Savat"), KeyboardButton(text="📦 Buyurtma")],
        [KeyboardButton(text="❓ Yordam")]
    ],
    resize_keyboard=True
)


# --- HELPERS ---
def format_price(summa: int) -> str:
    return f"{summa:,} UZS".replace(",", " ")

def cart_total(cart: Dict[int, int]) -> int:
    return sum(MENU_BY_ID[pid]["price"] * qty for pid, qty in cart.items())

def cart_text(cart: Dict[int, int]) -> str:
    if not cart:
        return "Sizning savatingiz bo'sh."
    lines = []
    for pid, qty in cart.items():
        it = MENU_BY_ID[pid]
        lines.append(f"{it['name']} x{qty} — {format_price(it['price'] * qty)}")
    lines.append(f"\nJami: {format_price(cart_total(cart))}")
    return "\n".join(lines)

# ------------------------------------------------------
#                   ROUTER
# ------------------------------------------------------
router = Router()

# START
@router.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.set_state(NameState.waiting_for_name)
    await message.answer("👋 Assalomu alaykum!\n\nIsmingizni kiriting:")
@router.message(NameState.waiting_for_name)
async def get_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name)

    await state.set_state("waiting_for_start_location")

    await message.answer(
        f"😊 Xush kelibsiz, {name}!\n\n📍 Endi lokatsiyani yuboring:",
        reply_markup=location_kb
    )
@router.message(F.location, StateFilter("waiting_for_start_location"))
async def save_start_location(message: types.Message, state: FSMContext):
    lat = message.location.latitude
    lon = message.location.longitude

    await state.update_data(start_location=f"{lat}, {lon}")

    await state.clear()

    await message.answer(
        "Rahmat! Lokatsiyangiz qabul qilindi ✅\n\nEndi menyudan tanlashingiz mumkin 👇",
        reply_markup=main_kb
    )





# LOCATION RECEIVED
@router.message(F.location)
async def location_received(message: types.Message, state: FSMContext):
    current = await state.get_state()

    # Agar buyurtma jarayonida bo‘lsa → lokatsiya manzil sifatida saqlanadi
    if current == CheckoutStates.awaiting_address.state:
        lat = message.location.latitude
        lon = message.location.longitude

        await state.update_data(address=f"Lokatsiya: {lat}, {lon}")

        uid = message.from_user.id
        cart = carts.get(uid, {})
        data = await state.get_data()

        total = cart_total(cart)  # <<< --- MUHIM --- jami summa

        text = (
            f"📦 *Buyurtma tafsilotlari:*\n\n"
            f"{cart_text(cart)}\n"
            f"💰 *Jami summa:* {format_price(total)}\n\n"
            f"📞 Telefon: {data['phone']}\n"
            f"📍 Lokatsiya: {lat}, {lon}\n\n"
            f"Tasdiqlaysizmi?"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Tasdiqlayman ✅", callback_data="confirm_order"),
                    InlineKeyboardButton(text="Bekor ❌", callback_data="cancel_order")
                ]
            ]
        )

        await state.set_state(CheckoutStates.confirm)
        await message.answer(text, parse_mode="Markdown", reply_markup=kb)
        return

    # Aks holda oddiy lokatsiya
    await message.answer(
        "Rahmat! Lokatsiyangiz qabul qilindi ✅\n\n"
        "Endi menyudan tanlashingiz mumkin 👇",
        reply_markup=main_kb
    )


# HELP
@router.message(Command("help"))
@router.message(F.text == "❓ Yordam")
async def help_cmd(message: types.Message):
    await message.answer(
        "/menu — menyu\n"
        "/cart — savat\n"
        "/clear — savatni tozalash\n"
        "/checkout — buyurtma\n"
        "/cancel — bekor qilish"
    ) 

# MENU command
# ============================
#   MENYU BUTTON (reply)
# ============================
@router.message(F.text == "🍞 Menyu")
async def menu_btn(message: types.Message):
    await menu_cmd(message)
# ============================
#        /menu COMMAND
# ============================
@router.message(Command("menu"))
async def menu_cmd(message: types.Message):
    bot = message.bot

    for item in MENU:

        caption = (
            f"*{item['name']}*\n"
            f"{item['description']}\n\n"
            f"Narx: {format_price(item['price'])}"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="➖", callback_data=f"decmenu|{item['id']}"),
                    InlineKeyboardButton(text="Qo‘shish", callback_data=f"add_{item['id']}"),
                    InlineKeyboardButton(text="➕", callback_data=f"incmenu|{item['id']}")
                ]
            ]
        )

        img_path = IMAGES_DIR / item["image"]

        if img_path.exists():
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=types.FSInputFile(img_path),
                caption=caption,
                parse_mode="Markdown",
                reply_markup=kb
            )
        else:
            await message.answer(caption, parse_mode="Markdown")
            await message.answer("Tanlang:", reply_markup=kb)

# Savat uchun faqat tozalash tugmasi
def cart_only_clean_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Tozalash", callback_data="clear_cart")]
    ])


# ADD TO CART
@router.callback_query(F.data.startswith("add_"))
async def add_to_cart(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[1])
    uid = callback.from_user.id

    cart = carts.setdefault(uid, {})
    cart[pid] = cart.get(pid, 0) + 1

    # Обновляем карточку товара (если это карточка товара)
    try:
        await refresh_menu_item(callback, pid)
    except Exception:
        # если обновить не получилось, просто игнорируем (в лог можно вывести)
        logger.exception("refresh_menu_item failed on add_to_cart")

    await callback.answer("Savatingizga qo‘shildi!")
    # Не удаляем клавиатуру — добавляем информативный ответ
    # Сообщение про /cart можно оставить, но лучше не спамить:
    # await callback.message.answer("🛒 Mahsulot savatga qo‘shildi!\n/cart — savatni ko‘rish")


# SAVAT (Reply button)
@router.message(F.text == "🛒 Savat")
async def cart_btn(message: types.Message):
    cart = carts.get(message.from_user.id, {})
    if not cart:
        return await message.answer("Savat bo‘sh ❗️")

    text = cart_text(cart)
    await message.answer(text, reply_markup=cart_only_clean_kb())

# SAVAT - CALLBACK BUTTON
@router.callback_query(F.data == "cart")
async def open_cart(callback: types.CallbackQuery):
    await cart_cmd(callback.message)
    await callback.answer()
# Cart Total
def cart_total(cart):
    total = 0
    for pid, count in cart.items():
        item = MENU_BY_ID[pid]
        total += item["price"] * count
    return total

# --- CART TEXT ---
def cart_text(cart):
    if not cart:
        return "Savat bo‘sh 🛒"

    text = "🛒 Savatingiz:\n\n"
    for pid, count in cart.items():
        item = MENU_BY_ID[pid]
        text += f"• {item['name']} — {count} dona\n"
    return text


# --- CART COMMAND ---
@router.message(Command("cart"))
async def cart_cmd(message: types.Message):
    uid = message.from_user.id
    cart = carts.get(uid, {})

    await send_cart(message, cart)


# --- SEND CART (universal refresh function) ---
async def send_cart(msg_or_cb, cart):
    kb_list = []

    for pid, count in cart.items():
        item = MENU_BY_ID[pid]

        kb_list.append([
            InlineKeyboardButton(text="➖", callback_data=f"dec|{pid}"),
            InlineKeyboardButton(text=f"{item['name']} — {count}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"inc|{pid}")
        ])

    kb_list.append([
        InlineKeyboardButton(text="🗑️ Tozalash", callback_data="clear_cart"),
        InlineKeyboardButton(text="✅ Buyurtma", callback_data="go_checkout")
    ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_list)

    text = cart_text(cart)

    if isinstance(msg_or_cb, types.CallbackQuery):
        await msg_or_cb.message.edit_text(text, reply_markup=kb)
    else:
        await msg_or_cb.answer(text, reply_markup=kb, parse_mode="Markdown")


# --- INCREASE (+) ---
@router.callback_query(F.data.startswith("inc|"))
async def increase_item(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _, pid = callback.data.split("|")
    pid = int(pid)

    carts.setdefault(uid, {})
    carts[uid][pid] = carts[uid].get(pid, 0) + 1

    await callback.answer("Qo‘shildi ➕")
    await send_cart(callback, carts[uid])


# --- DECREASE (-) ---
@router.callback_query(F.data.startswith("dec|"))
async def decrease_item(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _, pid = callback.data.split("|")
    pid = int(pid)

    if pid in carts.get(uid, {}):
        carts[uid][pid] -= 1
        if carts[uid][pid] <= 0:
            del carts[uid][pid]

    await callback.answer("Kamaytirildi ➖")
    await send_cart(callback, carts[uid])
async def refresh_menu_item(callback: types.CallbackQuery, pid: int):
    item = MENU_BY_ID[pid]

    # формируем описание заново
    caption = (
        f"*{item['name']}*\n"
        f"{item['description']}\n\n"
        f"Narx: {format_price(item['price'])}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖", callback_data=f"decmenu|{pid}"),
                InlineKeyboardButton(text="Qo‘shish", callback_data=f"add_{pid}"),
                InlineKeyboardButton(text="➕", callback_data=f"incmenu|{pid}")
            ]
        ]
    )

    try:
        await callback.message.edit_caption(
            caption=caption,
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except Exception as e:
        print("refresh_menu_item error:", e)

# --- INSERT THIS FUNCTION RIGHT HERE ---
async def refresh_menu_item(callback: types.CallbackQuery, pid: int):
    uid = callback.from_user.id
    item = MENU_BY_ID[pid]
    count = carts.get(uid, {}).get(pid, 0)

    caption = (
        f"*{item['name']}*\n"
        f"{item['description']}\n\n"
        f"Narx: {format_price(item['price'])}\n"
        f"Savatda: {count} dona"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➖", callback_data=f"decmenu|{pid}"),
                InlineKeyboardButton(text=f"{count} dona", callback_data="none"),
                InlineKeyboardButton(text="➕", callback_data=f"incmenu|{pid}")
            ]
        ]
    )

    try:
        await callback.message.edit_caption(
            caption=caption,
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except:
        pass



# --- MENU + ---
@router.callback_query(F.data.startswith("incmenu|"))
async def inc_menu_item(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _, pid = callback.data.split("|")
    pid = int(pid)

    carts.setdefault(uid, {})
    carts[uid][pid] = carts[uid].get(pid, 0) + 1

    await refresh_menu_item(callback, pid)
    await callback.answer("Qo‘shildi ➕")



# --- MENU - ---
@router.callback_query(F.data.startswith("decmenu|"))
async def dec_menu_item(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _, pid = callback.data.split("|")
    pid = int(pid)

    if pid in carts.setdefault(uid, {}):
        carts[uid][pid] -= 1
        if carts[uid][pid] <= 0:
            del carts[uid][pid]

    await refresh_menu_item(callback, pid)
    await callback.answer("Kamaytirildi ➖")





# --- CLEAR CART ---
@router.callback_query(F.data == "clear_cart")
async def clear_cart(callback: types.CallbackQuery):
    carts[callback.from_user.id] = {}
    await callback.answer("Savat tozalandi 🗑️")
    await send_cart(callback, {})

# SHOW IMAGES
@router.callback_query(F.data == "show_cart_images")
async def show_images(callback: types.CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    cart = carts.get(uid, {})

    media = []
    for pid, qty in cart.items():
        item = MENU_BY_ID[pid]
        path = IMAGES_DIR / item["image"]
        if path.exists():
            media.append(InputMediaPhoto(media=types.FSInputFile(path), caption=f"{item['name']} x{qty}"))

    if media:
        await bot.send_media_group(callback.message.chat.id, media)
    else:
        await callback.message.answer("Rasmlar topilmadi.")

    await callback.answer()


# PAYMENT KEYBOARD
def payment_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
    
        [
            InlineKeyboardButton(text="📤 Chekni yuborish", callback_data="send_check")
        ]
    ])
    return kb


# CHECKOUT (Reply button)
@router.message(F.text == "📦 Buyurtma")
async def checkout_btn(message: types.Message, state: FSMContext):
    await checkout_start(message, state)

# CHECKOUT command
@router.callback_query(F.data == "go_checkout")
@router.message(Command("checkout"))
async def checkout_start(target, state: FSMContext):
    message = target.message if isinstance(target, types.CallbackQuery) else target

    uid = message.from_user.id
    if not carts.get(uid):
        await message.answer("Savat bo‘sh!")
        return

    await state.set_state(CheckoutStates.awaiting_phone)
    await message.answer("📞 Telefon raqamingizni kiriting:")

# PHONE
@router.message(CheckoutStates.awaiting_phone)
async def phone_input(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await state.set_state(CheckoutStates.awaiting_address)
    await message.answer("📍 Manzilni kiriting:", reply_markup=location_kb)


# ADDRESS
@router.message(CheckoutStates.awaiting_address)
async def address_input(message: types.Message, state: FSMContext):

    # Agar foydalanuvchi lokatsiya yuborgan bo'lsa — shu manzil sifatida saqlanadi
    if message.location:
        lat = message.location.latitude
        lon = message.location.longitude
        await state.update_data(address=f"Lokatsiya: {lat}, {lon}")
    else:
        await state.update_data(address=message.text.strip())

    uid = message.from_user.id
    cart = carts.get(uid, {})
    data = await state.get_data()

    text = (
        f"📦 *Buyurtma tafsilotlari:*\n\n"
        f"{cart_text(cart)}\n\n"
        f"📞 {data['phone']}\n"
        f"📍 {data['address']}\n\n"
        f"Tasdiqlaysizmi?"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Tasdiqlayman ✅", callback_data="confirm_order"),
                InlineKeyboardButton(text="Bekor ❌", callback_data="cancel_order")
            ]
        ]
    )

    await state.set_state(CheckoutStates.confirm)
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)


# CANCEL ORDER
@router.callback_query(F.data == "confirm_order")
async def confirm_order(callback: types.CallbackQuery, state: FSMContext):
    print("🚀 Tasdiqlayman tugmasi bosildi!")  # TEST

    global order_counter   # <<< BUYURTMA RAQAMINI ISHLATISH
    current_order_id = order_counter
    order_counter += 1

    data = await state.get_data()
    uid = callback.from_user.id

    cart = carts.get(uid, {})
    total = cart_total(cart)

    phone = data.get("phone", "Noma'lum")
    address = data.get("address", "Noma'lum")

    text = (
        f"🆔 Buyurtma raqami: *#{current_order_id}*\n"
        "📦 *Yangi buyurtma!*\n\n"
        f"{cart_text(cart)}\n\n"
        f"💰 *Jami:* {format_price(total)}\n"
        f"📞 {phone}\n"
        f"📍 {address}\n\n"
        f"👤 @{callback.from_user.username}"
    )


    # Lokatsiya ajratish
    lat = lon = None
    if address.startswith("Lokatsiya:"):
        clean = address.replace("Lokatsiya:", "").strip()
        lat, lon = map(float, clean.split(","))

    admin_id = ADMIN_CHAT_ID

    # Adminlarga yuborish
    await callback.bot.send_message(admin_id, text)

    if lat and lon:
        await callback.bot.send_location(admin_id, latitude=lat, longitude=lon)

    await callback.message.edit_text(
    f"✅ Buyurtmangiz qabul qilindi!\n\n"
    f"🆔 Buyurtma raqami: #{current_order_id}\n\n"
    "💳 To‘lov uchun karta raqami:\n"
    "5614 6821 1714 8884\n\n"
    "To‘lov qilgandan so‘ng chekni yuboring.\n"
    "Quyidagi tugmani bosing:",
    reply_markup=payment_kb()
)


    await state.clear()
    carts[uid] = {}


# --- PAYMENT NOW ---
@router.callback_query(F.data == "pay_now")
async def pay_now(callback: types.CallbackQuery):
    msg = (
        "💳 *To‘lov uchun karta raqami:*\n"
        "`5614 6821 1714 8884`\n\n"
        "To‘lov qilgandan so‘ng Chek yuboring."
    )
    await callback.message.edit_text(msg, parse_mode="Markdown", reply_markup=payment_kb())
    await callback.answer()

# --- SEND CHECK (ask image) ---
@router.callback_query(F.data == "send_check")
async def ask_check(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📤 To‘lov chekini yuboring (rasm ko‘rinishida).")
    await state.set_state("waiting_for_check")
    await callback.answer()

# --- RECEIVE CHECK (PHOTO or DOCUMENT) ---
from aiogram.types import ReplyKeyboardRemove

@router.message(StateFilter("waiting_for_check"), F.photo | F.document)
async def process_check(message: types.Message, state: FSMContext):

    admin_id = ADMIN_CHAT_ID

    # ADMIN GA CHEKNI YUBORISH
    if message.photo:
        file_id = message.photo[-1].file_id
        await message.bot.send_photo(
            admin_id,
            file_id,
            caption="📥 Yangi to‘lov cheki!"
        )
    else:
        file_id = message.document.file_id
        await message.bot.send_document(
            admin_id,
            file_id,
            caption="📥 Yangi to‘lov cheki!"
        )

    # FOYDALANUVCHIGA JAVOB
    await message.answer(
        "✅ Chek qabul qilindi!\n"
        "👤 Operatorlar tekshiradi!\n"
        "😊 Buyurtmangiz uchun Rahmat!",
        reply_markup=ReplyKeyboardRemove()
    )

    # 🏠 Asosiy menyu qaytarish
    await message.answer(
        "🏠 Asosiy menyu:",
        reply_markup=main_kb
    )

    # STATE tozalash
    await state.clear()









    # SAVE TO DB
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id, user_name, phone, address, total, status) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, callback.from_user.username, data["phone"], data["address"], total, "new")

        )
        order_id = cur.lastrowid

        for pid, qty in cart.items():
            item = MENU_BY_ID[pid]
            await db.execute(
                "INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?, ?, ?, ?, ?)",
                (order_id, pid, item["name"], item["price"], qty)
            )

        await db.commit()

    carts[uid] = {}
    await state.clear()

    await callback.answer("Buyurtma tasdiqlandi!")
    await callback.message.edit_text("✅ Buyurtmangiz qabul qilindi! Operatorlarimiz tez orada siz bilan bog‘lanadi.")

    # SEND TO ADMIN
    await callback.bot.send_message(
        ADMIN_CHAT_ID,
        f"📦 Yangi buyurtma #{order_id}\n"
        f"👤 @{user.username}\n"
        f"{cart_text(cart)}\n\n"
        f"📞 {data['phone']}\n📍 {data['address']}"
    )

# ------------------------------------------------------
#                   BOT START
# ------------------------------------------------------
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print("🤖 Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
