# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
اسکنر خودکار «متن‌های ربات» (قابلیت ۵۰: تغییر همه متن‌های ربات از طریق سایت).

با ast فایل‌های پایتونیِ خودِ پردازش بات را می‌خواند و هر فراخوانی
``db.get_text("کلید", "متن پیش‌فرض")`` را پیدا می‌کند؛ نتیجه در جدول
bot_text_registry هر Database همگام می‌شود (نگاه کن: Database.init_db /
Database._sync_text_registry در database.py).

یعنی از این به بعد، هر متن جدیدی که به ربات اضافه شود (چه در دستورهای این
فایل، چه در قابلیت‌های بعدی) اگر با ``db.get_text(...)`` نوشته شده باشد،
با اولین اجرای init_db (یعنی هر بار بالا آمدن بات) خودکار در پنل وب زیر تب
«متن‌های ربات» ظاهر می‌شود - بدون این‌که لازم باشد جایی دستی ثبتش کنیم.

قرارداد الزامی برای نویسنده‌ی کد (بات اصلی + بات‌های نمایندگی):
  db.get_text("category.name", "متن ثابت با {placeholder} احتمالی")
مقدار پیش‌فرض همیشه باید یک رشته‌ی ادبی (literal) ساده باشد، نه f-string و
نه رشته‌ی ساخته‌شده در زمان اجرا - چون این اسکنر با ast.literal_eval آن را
می‌خواند. برای متن‌های داینامیک، مقدار خروجی get_text را با .format(...)
پر کن، مثال:
  db.get_text("order.confirmed", "سفارش شما با کد {order_id} ثبت شد.").format(order_id=oid)
"""

import ast
import logging
import os

logger = logging.getLogger(__name__)

# فقط فایل‌های خودِ پردازش بات (ریشه‌ی پروژه + panel_providers) اسکن می‌شوند؛
# admin_panel/ و miniapp/ و api/ برنامه‌های وب جدا هستند و «متن ربات» محسوب‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# نمی‌شوند.‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_SCAN_SUBDIRS = (".", "panel_providers")
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
_EXCLUDE_FILES = {"text_scanner.py", "test_config_provision.py"}


def _iter_target_files(root: str):
    for sub in _SCAN_SUBDIRS:
        dirpath = os.path.join(root, sub)
        if not os.path.isdir(dirpath):
            continue
        try:
            names = sorted(os.listdir(dirpath))
        except OSError:
            continue
        for name in names:
            if name.endswith(".py") and name not in _EXCLUDE_FILES:
                yield os.path.join(dirpath, name)


def _extract_from_call(node: ast.Call):
    func = node.func
    is_get_text = (isinstance(func, ast.Attribute) and func.attr == "get_text") or (
        isinstance(func, ast.Name) and func.id == "get_text"
    )
    if not is_get_text or len(node.args) < 2:
        return None
    try:
        key = ast.literal_eval(node.args[0])
        default = ast.literal_eval(node.args[1])
    except (ValueError, TypeError):
        return None
    if not isinstance(key, str) or not isinstance(default, str) or not key.strip():
        return None
    return key, default


def scan_bot_texts(root: str = None) -> list:
    """همه‌ی فراخوانی‌های get_text(key, default) را در فایل‌های بات پیدا می‌کند.

    خروجی: [{"key":..., "default_text":..., "category":...}, ...] یکتا بر
    اساس key (اگر یک کلید در چند فایل تعریف شده باشد، آخرین مورد می‌ماند).
    """
    root = root or os.path.dirname(os.path.abspath(__file__))
    found = {}
    for path in _iter_target_files(root):
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
        except (OSError, SyntaxError):
            logger.warning("اسکن متن‌های ربات: خواندن/پارس %s ناموفق بود.", path)
            continue
        category = os.path.splitext(os.path.basename(path))[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                result = _extract_from_call(node)
                if result:
                    key, default = result
                    found[key] = {"key": key, "default_text": default, "category": category}
    return list(found.values())
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
