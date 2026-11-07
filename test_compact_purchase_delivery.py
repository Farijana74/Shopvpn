import unittest
from unittest.mock import AsyncMock, patch

from config_delivery import deliver_config_to_user


class CompactDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_sends_qr_then_guide_with_back_button(self):
        bot = AsyncMock()
        with patch('config_delivery.prepare_delivery', new_callable=AsyncMock, return_value=('اطلاعات و لینک', [])), \
             patch('config_delivery._build_qr_photo', return_value='photo'), \
             patch('config_delivery.get_post_delivery_text', return_value='راهنمای اتصال'):
            await deliver_config_to_user(bot, 5, 'سرویس', 'vless://example', final_price=100)
        bot.send_photo.assert_awaited_once()
        bot.send_message.assert_awaited_once()
        args = bot.send_message.await_args
        self.assertIn('راهنمای اتصال', args.args[1])
        self.assertEqual(args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'trial:main_menu')
