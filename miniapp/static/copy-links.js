'use strict';
const field = document.getElementById('links');
const button = document.getElementById('copy');
const status = document.getElementById('status');
try {
  const raw = location.hash.slice(1);
  if (!raw || raw.length > 32000 || !/^[A-Za-z0-9_-]+$/.test(raw)) throw new Error('payload');
  const padded = raw.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - raw.length % 4) % 4);
  const bytes = Uint8Array.from(atob(padded), c => c.charCodeAt(0));
  const value = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
  if (!value.trim()) throw new Error('empty');
  field.value = value;
  button.disabled = false;
} catch (_) {
  status.textContent = 'لینک‌ها قابل خواندن نیستند؛ این صفحه را دوباره از دکمهٔ ربات باز کنید.';
}
button.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(field.value);
    status.textContent = '✅ همهٔ لینک‌ها کپی شدند؛ به برنامهٔ اتصال برگردید.';
  } catch (_) {
    field.focus();
    field.select();
    field.setSelectionRange(0, field.value.length);
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (_) { /* Manual selection remains available. */ }
    status.textContent = copied ? '✅ همهٔ لینک‌ها کپی شدند.' : 'متن انتخاب شد؛ از گزینهٔ کپی گوشی استفاده کنید.';
  }
});
