# -*- coding: utf-8 -*-
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
"""
سازگاری عقب‌رو: این فایل قبلاً یک کپی کامل از miniapp/server.py بود.
برای جلوگیری از drift (دو کپی که یکی آپدیت و دیگری قدیمی بماند)،
الان فقط یک shim نازک است که همه‌چیز را از miniapp.server می‌گیرد.

هر جا قبلاً `import server` یا `uvicorn server:app` استفاده می‌شد،
باید به `miniapp.server` تغییر کند. این فایل فقط برای سازگاری باقی مانده
و در آینده حذف خواهد شد — مستقیم از miniapp.server استفاده کنید.
"""
import warnings
warnings.warn(
    "server.py در ریشه منسوخ شده؛ از miniapp.server استفاده کنید "
    "(uvicorn miniapp.server:app). این shim در نسخه‌ی بعد حذف می‌شود.",
    DeprecationWarning,
    stacklevel=2,
)
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍

from miniapp.server import *  # noqa: F401,F403
from miniapp.server import app  # noqa: F401 — برای uvicorn server:app
#‍​‌‌​​​‌‌​‌‌​​‌​‌​‌‌​‌‌​​​‌‌​​‌​‌​‌‌​‌‌‌​​‌‌​‌‌‌‌​‌‌‌​​‌​‍
# 		   		 		  	 	 		 		   		  	 	 		 			  		 				 			  	 
