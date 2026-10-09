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
| الإدخال النصي عبر Clipboard + Ctrl+V فقط | لا يُنسخ إلا كود Redeem مطابق للصيغة (25 حرفًا). بعد اللصق يُتحقق بـ OCR/Vision أن النص ظهر في الحقل قبل ضغط A، ثم يُمسح الـ clipboard. |
| لا حذف بروفايل بدون تأكيد صريح | يُصدر الوكيل توكنًا لمرة واحدة ويرسله مع الـ gamertag. الحذف لا يتم إلا بأمر `confirm_delete_profile` يحمل نفس التوكن. توكن خاطئ يُتجاهل، و`deny_delete_profile` أو انتهاء المهلة يعني لا حذف. عند الحذف يُختار حساب مطابق تمامًا للـ gamertag، ويُتحقق من نص نافذة التأكيد قبل الضغط. |
| Redeem الرسمي فقط | عبر شاشة Redeem داخل الكونسول. لا يشتري الوكيل ولا يثبّت لعبة أبدًا (يتوقف عند زر Install/Buy). |
| عند الشك: توقف + صورة + تقرير + انتظار | أي استثناء غير متوقع يتحول إلى `Escalation`: لقطة + نص OCR + تصنيف (`network/auth/code_invalid/code_used/game_stuck/stream_lost/unknown`) إلى الـ Dashboard، ثم انتظار `retry` / `skip` / `abort_session`. |
| النافذة نشطة قبل كل إجراء | `SetForegroundWindow` قبل كل ضغط وكل لقطة. إن فشل التفعيل لا يُرسل أي إدخال. |
| صورة سوداء/متجمدة | إطار شبه أسود، أو 4 إطارات متطابقة أثناء التنقل، يعني `stream_lost`: يعيد الوكيل تحميل الصفحة (F5) حتى 3 مرات ثم يصعّد للمشرف. |

الأكواد الكاملة تبقى في الذاكرة فقط. في SQLite والتقارير والـ stdout يظهر القناع `XXXXX-…-EEEEE`.
تنبيه: ملفات PNG في `shots/` (ولقطات الأخطاء المرسلة للـ Dashboard) قد تُظهر الكود إن التُقطت وهو على شاشة Redeem، فاحمِ هذا المجلد.

## التشغيل والتجربة

الدليل الكامل خطوة بخطوة: [`TRIAL.md`](TRIAL.md). باختصار على Windows 10/11 (Python 3.11+):

1. ثبّت **ViGEmBus** و**Tesseract OCR**.
2. `xbox_agent\scripts\setup.bat`، ثم املأ `xbox_agent\.env` (انسخه من `.env.example`).
3. `xbox_agent\scripts\run_dashboard.bat` ثم افتح http://127.0.0.1:8000
4. `xbox_agent\scripts\run_agent.bat --check` ثم `--inspect` ثم `run_agent.bat`.

الإعدادات تُقرأ من `xbox_agent/.env` (أو من متغيرات البيئة، وهي تتغلب عليه). أهمها:

| المتغير | الافتراضي |
|---|---|
| `XBOX_AGENT_DASHBOARD_URL` | `http://127.0.0.1:8000` |
| `XBOX_AGENT_DASHBOARD_TOKEN` | سرّ الوكيل مع الداشبورد (Bearer) |
| `DASHBOARD_ADMIN_TOKEN` | سرّ المشرف للوحة الويب (يجب أن يختلف عن السابق) |
| `XBOX_AGENT_VISION_PROVIDER` | `auto`؛ القيم: `auto` / `gemini` / `deepseek` / `claude` (انظر «مزوّد الرؤية») |
| `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` | حسب المزوّد (Claude يقبل أيضًا `ant auth login`) |
| `XBOX_AGENT_DEEPSEEK_MODEL` / `_BASE_URL` / `_THINKING` | `deepseek-flash` / `https://api.deepseek.com` / `off` |
| `XBOX_AGENT_CLAUDE_MODEL` / `_EFFORT` / `_FALLBACKS` | `claude-opus-5-5` / `low` / `1` |
| `XBOX_AGENT_WINDOW_TITLE` | `Xbox` (جزء من عنوان نافذة المتصفح) |
| `TESSERACT_CMD` | مسار `tesseract.exe` إن لم يكن في PATH |
| `XBOX_AGENT_ALLOW_DELETE` | `0`؛ حذف البروفايل معطّل حتى تضع `1` (ويبقى تأكيد الداشبورد مطلوبًا) |
| `XBOX_AGENT_PAUSE_ON_CODE_ERROR` | `0`؛ ضعه `1` ليتوقف عند كل كود خاطئ/مستخدم بدل المتابعة |
| `XBOX_AGENT_LOGIN_LABELS`, `_REDEEM_PATH`, `_LIBRARY_LABELS`, `_DELETE_PATH`, `_GAME_MATCH`, `_GAME_EXCLUDE`, `_LOGIN_CODE_REGEX` | أسماء القوائم؛ تُضبط بـ `--inspect` |
| `XBOX_AGENT_ADMIN_LOGIN_TIMEOUT_S`, `..._GAME_READY_TIMEOUT_S`, `..._DELETE_CONFIRM_TIMEOUT_S`, `..._SETTLE_S` | 900 / 600 / 1800 / 1.2 |
| `XBOX_AGENT_DB`, `XBOX_AGENT_SHOTS_DIR` | `xbox_agent.sqlite3`, `shots` |

أوضاع التشغيل: `python -m xbox_agent` (حلقة دائمة)، `--once` (جلسة واحدة)، `--check` (يفحص كل مكوّن ويجري استدعاء رؤية واحدًا)،
`--inspect` (يعرض ما يقرؤه OCR والنموذج على الشاشة الحالية). الوضعان الأخيران لا يضغطان أي زر.

## مزوّد الرؤية (Vision)

الرؤية غير مربوطة بمزوّد معيّن: الوكيل يتعامل مع واجهة `Vision.ask(...)` فقط، ولكل مهمة (تصنيف الشاشة، قراءة القائمة،
نتيجة Redeem، …) مخطط JSON في `vision_tasks.py`. المتاح حاليًا:

| المزوّد | الملف | ملاحظات |
|---|---|---|
| `gemini` | `tools/gemini_vision.py` | `gemini-2.5-flash` في JSON mode |
| `deepseek` | `tools/deepseek_vision.py` | `deepseek-flash` عبر `/chat/completions` بـ `httpx` مباشرة (بدون حزمة OpenAI) |
| `claude` | `tools/claude_vision.py` | Anthropic SDK، مخرجات مقيّدة بالمخطط (`output_config.format`)، النموذج الافتراضي `claude-opus-5-5` |

- `auto` (الافتراضي): أول مفتاح موجود بالترتيب: Gemini ثم DeepSeek وإلا Claude. عند وجود أكثر من مفتاح حدّد المزوّد صراحة.
- **DeepSeek:** لا يقبل الصور إلا نموذج واحد قادر على الرؤية؛ الافتراضي `deepseek-flash` (ويُقبل `deepseek-v4-flash-vision-exp` إن كان حسابك ما زال يوجّه إليه). مع الصور يعمل وضع `json_object` فقط، لذلك يُرسَل مخطط JSON داخل الطلب ويتحقق الوكيل من الإجابة بنفسه. وضع التفكير قد يكون مفعّلًا افتراضيًا ويستهلك حد الرموز قبل أي نص، فيطلب الوكيل إيقافه (`_THINKING=off`) ويتراجع تلقائيًا إن رفض الخادم الحقل. هذه التفاصيل من وثائق وتقارير عامة (موقع الوثائق الرسمي محجوب من بيئتي)، ولم أستدعِ الـ API فعليًا، فأول `--check` عندك هو الاختبار الحقيقي.
- **خصوصية:** اللقطات تُرسل كاملة لمزوّد الرؤية، وشاشة Redeem قد تُظهر الكود كاملًا وشاشات الدخول تُظهر بيانات الحساب. هذا ينطبق على كل المزوّدين؛ تأكد أن سياسة بيانات المزوّد الذي تختاره مقبولة لك.
- مع Claude: الجهد `low` افتراضيًا لأن كل استدعاء قراءة بسيطة لشاشة واحدة والوكيل يكرّرها كثيرًا. لتوفير الكلفة غيّر
  `XBOX_AGENT_CLAUDE_MODEL` (مثلًا `claude-haiku-5-5`)، وارفع `XBOX_AGENT_CLAUDE_EFFORT` إن كانت القراءة غير دقيقة.
- مع Claude يُفعَّل الـ fallback من جهة الخادم (`server-side-fallback-2026-07-01`): لو رفض مصنّف الأمان طلبًا يُعاد تشغيله
  على نموذج بديل داخل نفس الاستدعاء. هو beta ومتاح على Claude API (وClaude Platform on AWS) فقط؛ عطّله بـ `XBOX_AGENT_CLAUDE_FALLBACKS=0`.
  أي رفض أو إجابة غير صالحة تتحول إلى تصعيد للمشرف كباقي الأخطاء، ولا يخمّن الوكيل.
- OpenAI غير مدعوم عمدًا (حسب الـ prompt الأصلي). لإضافة مزوّد آخر: صنف يطبّق `ask(png, task, required_keys, schema)`
  ويعيد `dict`، ثم أضفه في `tools/vision.py`.

## لوحة الويب (Dashboard)

`python -m xbox_agent.dashboard` (أو `run_dashboard.bat`) يشغّل لوحة FastAPI جاهزة في `xbox_agent/dashboard/`
(`pip install -r xbox_agent/requirements-dashboard.txt`). منها يتم **كل** التحكم:

| ما تفعله | أين |
|---|---|
| بدء جلسة بقائمة أكواد (يُتحقق من صيغتها وتكرارها)، مع خيار «أنهِ بعد مرحلة معيّنة» للتجربة | بطاقة «بدء جلسة» |
| رؤية كود الدخول على الكونسول وتأكيد أنك سجّلت الدخول | بطاقة كود الدخول |
| رؤية الأخطاء (صورة + نص OCR + تصنيف) والرد: إعادة / تخطي / إيقاف | البطاقة الحمراء |
| إغلاق اللعبة عند الانتهاء | بطاقة «اللعبة جاهزة» |
| تأكيد حذف بروفايل بكتابة اسم الحساب بالضبط، أو رفضه | بطاقة الحذف |
| متابعة الأكواد والأحداث والتقرير النهائي، وإيقاف الجلسة | بقية اللوحة |

أمان اللوحة: سرّان مختلفان وإلزاميان (وكيل ومشرف)، العنوان الافتراضي `127.0.0.1` (غيّره بـ `DASHBOARD_HOST` فقط خلف HTTPS)،
CSP صارم بلا سكربت مضمّن، وكل نص قادم من الشاشة يُعرض نصًا لا HTML، والأكواد الكاملة تُمسح من سجل اللوحة بعد تسليمها للوكيل،
وتوكن حذف البروفايل يبقى في الخادم ولا يصل المتصفح.

## الدمج مع لوحتك وبوتك

الوكيل لا يعرف شيئًا عن لوحتك سوى هذا العقد (HTTP)، وهو بالضبط ما تنفّذه اللوحة المرجعية، فيمكن نقله إلى أي backend:
كل الطلبات بـ `Authorization: Bearer <XBOX_AGENT_DASHBOARD_TOKEN>`.

- `GET /api/agent/commands?wait=<s>` يعيد قائمة `[{ "id", "type", "payload" }]` (long-poll؛ كل أمر يُسلَّم مرة واحدة).
- `POST /api/agent/commands/{id}/ack`
- `POST /api/agent/events` بصيغة form/multipart: حقل `data` = JSON `{ "kind", "payload" }`، وملف اختياري `image` (PNG).

الأوامر التي يفهمها الوكيل (`payload.session_id` يربطها بالجلسة، وأوامر جلسة أخرى تُهمل):

| `type` | الغرض |
|---|---|
| `start_session` | `{ session_id, codes: [{ id, code }], stop_after? }` |
| `admin_login_confirmed` | المشرف أنهى الدخول بالكود |
| `close_game` | إغلاق اللعبة وبدء الخروج |
| `confirm_delete_profile` / `deny_delete_profile` | `{ token }` = التوكن الذي وصل في `delete_profile_request` |
| `retry` / `resume` / `skip` / `abort_session` | ردود المشرف بعد التصعيد (`skip` متاح لكود Redeem فقط) |

أنواع الأحداث الصادرة (`kind`): `step`, `login_code`, `redeem_result`, `error`, `game_ready`,
`delete_profile_request`, `session_report`. المنطق الذي يجب أن تحافظ عليه لوحتك (ملف `dashboard/state.py`): التحقق من صيغة
الأكواد، رفض أزرار لا شيء لها (مثل إغلاق لعبة غير جاهزة)، ومنع حذف البروفايل إلا بتوكن الطلب نفسه.

لتشغيل الوكيل داخل تطبيقك بدل عملية منفصلة: `XboxAgent(tools, cfg).run_forever()` من أي خيط؛ المخزن آمن للخيوط
ويجب أن يملك الوكيل نافذة المتصفح واليد الوهمية وحده.

## الاختبارات

```
python -m unittest discover -s xbox_agent/tests -t .
```

تعمل على أي نظام: كونسول وهمي (`tests/fakes.py`) يحاكي القوائم وشاشات Redeem واللعبة وحذف الحساب،
وتغطي المسار الكامل ومسارات الأمان (شاشة كلمة المرور، توكن خاطئ، لصق فاشل، لعبة غير مثبتة، إطار أسود…).
مع تثبيت `requirements-dashboard.txt` و`httpx` تعمل أيضًا اختبارات اللوحة، ومنها اختبار يشغّل الوكيل الحقيقي ضد
اللوحة الحقيقية عبر HTTP بينما "مشرف" آلي يضغط الأزرار؛ وبدونها تُتخطى (`skipped`).

## حدود معروفة

- **لم تُجرَّب على Xbox حقيقي ولا على Windows**، وسكربتات `.bat` لم تُشغَّل. تنفيذات `vgamepad` و`mss` و`pytesseract` و`windows_input.py` ومزوّدا الرؤية مع شبكتهما الفعلية كُتبت لكن لم تُشغَّل هنا (لا مفاتيح API)؛ المختبَر هو منطق الوكيل عبر الأدوات الوهمية، وشكل الطلب إلى Claude عبر عميل وهمي.
- مسارات القوائم الافتراضية (`redeem_path`, `library_labels`, `delete_path`, `login_labels`) افتراضات عن واجهة Xbox بالإنجليزية، وتُضبط من `.env` بعد `--inspect`. حين لا يجد الوكيل العنصر أو يجده بشكل ملتبس يصعّد ولا يخمّن.
- إعادة الاتصال تستخدم F5 (مفتاح تنقل، لا إدخال نصي). لا يُستخدم الماوس إطلاقًا.
- استخدمه فقط على حسابات وكونسولات تملكها أو مخوّل بتشغيلها.
