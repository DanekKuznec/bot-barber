from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb
from ..db import Database
from ..texts import CONTACTS, WELCOME, price_list

router = Router(name="common")
fallback_router = Router(name="fallback")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await db.save_client(message.from_user.id, message.from_user.full_name)
    await message.answer(WELCOME, reply_markup=kb.main_menu())


@router.message(Command("prices"))
@router.message(F.text == kb.BTN_PRICES)
async def prices(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(price_list(), reply_markup=kb.main_menu())


@router.message(Command("contacts"))
@router.message(F.text == kb.BTN_CONTACTS)
async def contacts(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(CONTACTS, reply_markup=kb.main_menu())


@fallback_router.message()
async def unknown(message: Message) -> None:
    await message.answer("Я понимаю только кнопки меню. Выберите действие внизу экрана.", reply_markup=kb.main_menu())
