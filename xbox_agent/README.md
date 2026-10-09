# XboxAgent

وكيل Python يدير جلسة Xbox Remote Play داخل Chrome/Edge على Windows عبر Gamepad وهمي (`vgamepad`).
النص الأصلي للـ prompt محفوظ في [`SYSTEM_PROMPT.md`](SYSTEM_PROMPT.md). هذه الحزمة تنفّذه كآلة حالات فعلية.
الحزمة مستقلة تمامًا عن مشروع TypeScript في جذر المستودع.

## ما الذي يحدث في كل خطوة

كل خطوة تُطبع على **stdout** كسطر JSON واحد بالصيغة المطلوبة
(`state, action, params, screenshot_id, window_focused, message, need_confirmation, next_expected_event`)،
وتُحفظ في SQLite وتُرسل للـ Dashboard. السجلات العادية تذهب إلى stderr.

```
IDLE → START_SESSION → REACH_LOGIN → EXTRACT_CODE → SEND_CODE → WAIT_ADMIN_LOGIN → DETECT_LOGGED_IN
→ REDEEM_CODES → OPEN_LIBRARY → LAUNCH_COD_MW → WAIT_GAME_READY → NOTIFY_GAME_READY → WAIT_CLOSE_CMD
→ CLOSE_GAME → LOGOUT → REQUEST_DELETE_PROFILE → DELETE_PROFILE → REPORT → IDLE
```

## القواعد الصارمة: أين تُفرض في الكود

| القاعدة | التنفيذ |
|---|---|
| لا تجاوز للمصادقة، لا تخمين OTP/كلمات مرور | أي شاشة `password_prompt` / `otp_prompt` / `captcha` ترفع `Escalation(auth)` فورًا (`vision_tasks.AUTH_BLOCKING`). لا توجد أداة تكتب نصًا حرًا أصلًا. |
| الإدخال النصي عبر Clipboard + Ctrl+V فقط | لا يُنسخ إلا كود Redeem مطابق للصيغة (25 حرفًا). بعد اللصق يُتحقق بـ OCR/Gemini أن النص ظهر في الحقل قبل ضغط A، ثم يُمسح الـ clipboard. |
| لا حذف بروفايل بدون تأكيد صريح | يُصدر الوكيل توكنًا لمرة واحدة ويرسله مع الـ gamertag. الحذف لا يتم إلا بأمر `confirm_delete_profile` يحمل نفس التوكن. توكن خاطئ يُتجاهل، و`deny_delete_profile` أو انتهاء المهلة يعني لا حذف. عند الحذف يُختار حساب مطابق تمامًا للـ gamertag، ويُتحقق من نص نافذة التأكيد قبل الضغط. |
| Redeem الرسمي فقط | عبر شاشة Redeem داخل الكونسول. لا يشتري الوكيل ولا يثبّت لعبة أبدًا (يتوقف عند زر Install/Buy). |
| عند الشك: توقف + صورة + تقرير + انتظار | أي استثناء غير متوقع يتحول إلى `Escalation`: لقطة + نص OCR + تصنيف (`network/auth/code_invalid/code_used/game_stuck/stream_lost/unknown`) إلى الـ Dashboard، ثم انتظار `retry` / `skip` / `abort_session`. |
| النافذة نشطة قبل كل إجراء | `SetForegroundWindow` قبل كل ضغط وكل لقطة. إن فشل التفعيل لا يُرسل أي إدخال. |
| صورة سوداء/متجمدة | إطار شبه أسود، أو 4 إطارات متطابقة أثناء التنقل، يعني `stream_lost`: يعيد الوكيل تحميل الصفحة (F5) حتى 3 مرات ثم يصعّد للمشرف. |

الأكواد الكاملة تبقى في الذاكرة فقط. في SQLite والتقارير والـ stdout يظهر القناع `XXXXX-…-EEEEE`.
تنبيه: ملفات PNG في `shots/` (ولقطات الأخطاء المرسلة للـ Dashboard) قد تُظهر الكود إن التُقطت وهو على شاشة Redeem، فاحمِ هذا المجلد.

## التثبيت (Windows 10/11، Python 3.11+)

1. ثبّت **ViGEmBus** (مطلوب لـ vgamepad) و**Tesseract OCR**.
2. `pip install -r xbox_agent/requirements.txt`
3. افتح Xbox Remote Play في Chrome/Edge بحجم نافذة ثابت.
4. اضبط المتغيرات:

| المتغير | الافتراضي |
|---|---|
| `GEMINI_API_KEY` | (مطلوب) |
| `XBOX_AGENT_DASHBOARD_URL` | `http://127.0.0.1:8000` |
| `XBOX_AGENT_DASHBOARD_TOKEN` | فارغ (Bearer) |
| `XBOX_AGENT_WINDOW_TITLE` | `Xbox` (جزء من عنوان نافذة المتصفح) |
| `TESSERACT_CMD` | مسار `tesseract.exe` إن لم يكن في PATH |
| `XBOX_AGENT_DB`, `XBOX_AGENT_SHOTS_DIR` | `xbox_agent.sqlite3`, `shots` |
| `XBOX_AGENT_ADMIN_LOGIN_TIMEOUT_S`, `..._GAME_READY_TIMEOUT_S`, `..._DELETE_CONFIRM_TIMEOUT_S` | 900 / 600 / 1800 |
| `XBOX_AGENT_PAUSE_ON_CODE_ERROR` | `0`; ضعه `1` ليتوقف عند كل كود خاطئ/مستخدم بدل المتابعة |

5. من جذر المستودع:
   - `python -m xbox_agent --check` يتحقق من النافذة والالتقاط وOCR
   - `python -m xbox_agent` حلقة دائمة، أو `--once` لجلسة واحدة

## عقد الـ Dashboard (يجب أن يوفّره الـ FastAPI)

كل الطلبات بـ `Authorization: Bearer <token>`.

- `GET /api/agent/commands?wait=<s>` يعيد قائمة `[{ "id", "type", "payload" }]` (long-poll).
- `POST /api/agent/commands/{id}/ack`
- `POST /api/agent/events` بصيغة multipart: حقل `data` = JSON `{ "kind", "payload" }`، وملف اختياري `image` (PNG).

الأوامر التي يفهمها الوكيل (`payload.session_id` يربطها بالجلسة، وأوامر جلسة أخرى تُهمل):

| `type` | الغرض |
|---|---|
| `start_session` | `{ session_id, codes: [{ id, code }] }` |
| `admin_login_confirmed` | المشرف أنهى الدخول بالكود |
| `close_game` | إغلاق اللعبة وبدء الخروج |
| `confirm_delete_profile` / `deny_delete_profile` | `{ token }` = التوكن الذي وصل في `delete_profile_request` |
| `retry` / `resume` / `skip` / `abort_session` | ردود المشرف بعد التصعيد (`skip` متاح لكود Redeem فقط) |

أنواع الأحداث الصادرة (`kind`): `step`, `login_code`, `redeem_result`, `error`, `game_ready`,
`delete_profile_request`, `session_report`.

## الاختبارات

```
python -m unittest discover -s xbox_agent/tests -t .
```

تعمل على أي نظام: كونسول وهمي (`tests/fakes.py`) يحاكي القوائم وشاشات Redeem واللعبة وحذف الحساب،
وتغطي المسار الكامل ومسارات الأمان (شاشة كلمة المرور، توكن خاطئ، لصق فاشل، لعبة غير مثبتة، إطار أسود…).

## حدود معروفة

- **لم تُجرَّب على Xbox حقيقي ولا على Windows.** تنفيذات `vgamepad` و`mss` و`pytesseract` و`google-genai` وملف `windows_input.py` كُتبت لكن لم تُشغَّل هنا؛ المختبَر هو منطق الوكيل عبر الأدوات الوهمية.
- مسارات القوائم في `config.py` (`redeem_path`, `library_labels`, `delete_path`, `login_labels`) افتراضات عن واجهة Xbox بالإنجليزية، وستحتاج ضبطًا على الكونسول الفعلي. حين لا يجد الوكيل العنصر أو يجده بشكل ملتبس يصعّد ولا يخمّن.
- إعادة الاتصال تستخدم F5 (مفتاح تنقل، لا إدخال نصي). لا يُستخدم الماوس إطلاقًا.
- استخدمه فقط على حسابات وكونسولات تملكها أو مخوّل بتشغيلها.
