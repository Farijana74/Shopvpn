# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
تغییر یوزرنیم و/یا پسورد یک حساب موجودِ پنل وب مستقل (بدون نیاز به ورود به پنل).

استفاده:
    python -m admin_panel.change_admin <یوزرنیم_فعلی> <یوزرنیم_جدید> <پسورد_جدید>

- برای عوض نکردن یوزرنیم، همان یوزرنیم فعلی را به‌عنوان آرگومان دوم بده.
- اگر حسابی با یوزرنیم فعلی پیدا نشود، خطا می‌دهد.
- اگر یوزرنیم جدید قبلاً توسط حساب دیگری استفاده شده باشد، خطا می‌دهد.
"""

import sys

from config import DB_PATH
from database import Database
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
from admin_panel.security import hash_password


def main():
    if len(sys.argv) != 4:
        print("استفاده: python -m admin_panel.change_admin <یوزرنیم فعلی> <یوزرنیم جدید> <پسورد جدید>")
        sys.exit(1)

    current_username = sys.argv[1].strip().lower()
    new_username = sys.argv[2].strip().lower()
    new_password = sys.argv[3]

    if len(new_username) < 3:
        print("یوزرنیم جدید باید حداقل ۳ کاراکتر باشد.")
        sys.exit(1)
    if len(new_password) < 8:
        print("پسورد جدید باید حداقل ۸ کاراکتر باشد.")
        sys.exit(1)

    db = Database(DB_PATH)
    admin = db.get_web_admin_by_username(current_username)
    if not admin:
        print(f"حسابی با یوزرنیم «{current_username}» پیدا نشد.")
        sys.exit(1)

    if new_username != current_username:
        ok = db.set_web_admin_username(admin["id"], new_username)
        if not ok:
            print(f"یوزرنیم «{new_username}» قبلاً توسط یک حساب دیگر استفاده شده؛ چیزی تغییر نکرد.")
            sys.exit(1)

    db.set_web_admin_password(admin["id"], hash_password(new_password))

    if new_username != current_username:
        print(f"یوزرنیم «{current_username}» به «{new_username}» تغییر کرد و پسورد آن آپدیت شد.")
    else:
        print(f"پسورد حساب «{current_username}» آپدیت شد.")


if __name__ == "__main__":
    main()
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
