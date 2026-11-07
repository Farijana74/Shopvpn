"""Customer-safe error presentation; technical reasons stay in server logs."""
import logging
import re

UNAVAILABLE='این سرویس فعلاً در دسترس نیست. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.'
TRIAL_USED='شما قبلاً تست رایگان دریافت کرده‌اید. هر کاربر فقط یک بار می‌تواند تست بگیرد.'


def customer_error(error):
    text=str(error).strip()
    logging.getLogger(__name__).warning('Customer operation failed (%s): %s',type(error).__name__,text)
    if ('تست' in text and any(word in text for word in ('قبلاً','فقط یک','سهمیه','یک بار','یک تست'))) and 'برگردانده' not in text:
        if 'پردازش' in text and 'قبلاً' not in text:return 'درخواست تست شما در حال پردازش است. لطفاً کمی صبر کنید.'
        return TRIAL_USED
    if 'پردازش' in text and 'تست' in text:return 'درخواست تست شما در حال پردازش است. لطفاً کمی صبر کنید.'
    if re.fullmatch(r'حجم باید بین [\d۰-۹٬,]+ تا [\d۰-۹٬,]+ گیگ باشد\.',text):return text
    private=('نمایند','بالادست','تأمین','تامین','ربات اصلی','زنجیره','پنل','managed_','sqlite','Traceback','token','http://','https://','/root/')
    if any(word in text for word in private):
        if 'قیمت' in text or 'نرخ' in text:return 'قیمت یا شرایط این سرویس تغییر کرده است. لطفاً سفارش را دوباره از منو ثبت کنید.'
        return UNAVAILABLE
    # Preserve useful, explicit customer validation; never pass arbitrary provider errors through.
    if text.startswith(('نام کاربری','نام باید','حجم باید','کد تخفیف','موجودی کیف پول شما','مبلغ باید','این سفارش متعلق','محصول یافت نشد','سرویس یافت نشد')) and not re.search(r'[A-Za-z]',text):return text
    return UNAVAILABLE
