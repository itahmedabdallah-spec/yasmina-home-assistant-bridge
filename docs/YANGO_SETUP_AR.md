# إعداد Yango / Yasmina من الصفر

هذا الفصل يشرح **المسار المستخدم فعليًا في الإصدار العام**. لا يحتاج Root للسماعة، ولا SSH، ولا ADB على Yasmina نفسها.

## الصورة العامة

```text
صوت المستخدم
   ↓
Yasmina
   ↓
Yango Smart Home
   ↓
Smart Home Skill / Provider
   ↓ HTTPS
هذا المشروع
   ↓
Home Assistant
   ↓
الإضاءة / Android TV
```

## 1. تفعيل Yasmina بالطريقة العادية

1. وصّل سماعة Yasmina بالكهرباء.
2. افتح تطبيق Yango Play وسجّل الدخول.
3. افتح **Smart home with Yasmina**.
4. أضف السماعة واتبع خطوات الإعداد داخل التطبيق.
5. تأكد أن السماعة تستجيب للأوامر العادية قبل بدء أي تكامل مخصص.

لا يحتاج هذا المشروع إلى تعديل Firmware أو Root أو فتح SSH على Yasmina.

## 2. تجهيز Home Assistant

1. تأكد أن الأجهزة التي تريد التحكم بها موجودة داخل Home Assistant.
2. جهّز Long-Lived Access Token مخصص للمشروع.
3. احفظه في `bridge/secrets/ha_token` بصلاحية ملف `0600`.
4. لا تضع التوكن في `.env` ولا GitHub.
5. للتلفزيون: يجب أن يكون Android TV مقترنًا مسبقًا بـADB مع Home Assistant.

## 3. تجهيز عنوان HTTPS عام

Yango يحتاج Provider يمكن الوصول إليه عبر HTTPS.

- شغّل `bridge/native_http.py` على localhost.
- ضع أمامه reverse proxy مثل Caddy أو Nginx.
- استخدم Domain خاص بك.
- لا تنشر عنوان البيت الداخلي أو IP خاص.

مثال Caddy موجود في:
`examples/caddy/Caddyfile`.

## 4. إنشاء Smart Home Skill / Provider

من بوابة مطوري Yango أنشئ تكاملًا من نوع **Smart Home**.

المشروع يوفّر المسارات الأساسية التالية:

- `GET /v1.0/user/devices` — اكتشاف الأجهزة.
- `POST /v1.0/user/devices/query` — قراءة الحالة.
- `POST /v1.0/user/devices/action` — تنفيذ أمر.
- `POST /v1.0/user/unlink` — فك الربط.
- `GET/POST /oauth/authorize` — بدء/إكمال ربط الحساب.
- `POST /oauth/token` — إصدار/تجديد access token.

استخدم عنوان الـHTTPS الخاص بك عند إعداد Provider URLs.

## 5. OAuth وربط الحساب

المشروع يستخدم Authorization Code Flow لحساب مالك واحد:

1. انسخ `examples/native_config.example.json` إلى `bridge/native_config.json`.
2. اضبط `YASMINA_OAUTH_CLIENT_ID` و`YASMINA_OAUTH_REDIRECT`.
3. عند أول تشغيل، المشروع ينشئ بيانات الربط محليًا داخل `bridge/secrets/`.
4. هذه البيانات **سرية** ولا تُرفع إلى GitHub.
5. من Yango Play افتح Smart Home، اختر التكامل الذي أنشأته واضغط Link.
6. يفتح Yango صفحة OAuth الخاصة بمشروعك.
7. بعد نجاح الربط، اطلب **Refresh/Update device list** من Yango.
8. تأكد أن الأجهزة ظهرت قبل تفعيل الكتابة.

## 6. تفعيل الأوامر تدريجيًا

النسخة العامة تبدأ وكل بوابات التنفيذ مقفولة.

افتحها بهذا الترتيب بعد التحقق:

1. Skill verified.
2. Account linked.
3. Device discovery verified.
4. Speaker/account verified.
5. Bundle published.
6. Bundle active.
7. Actions enabled.

لا تفعّل الكل مرة واحدة في تركيب جديد.

## 7. اختبار حقيقي

اختبر أمرًا واحدًا في كل مرة:

1. قل الأمر بصوتك إلى Yasmina.
2. تأكد أن Provider استقبل الـaction المطلوب.
3. تأكد أن Home Assistant نفذ الأمر.
4. تأكد من النتيجة على الجهاز نفسه.

مجرد ظهور التلفزيون على تطبيق معين لا يثبت أن الأمر الصوتي هو الذي فتحه.

## ما لم نفعله

- لم نعمل Root لـYasmina.
- لم نغيّر Firmware السماعة.
- لم نفتح SSH عليها.
- لم ننشر أو نستخدم ميكروفونها كـraw audio stream.
- لا يحتاج المسار النهائي إلى Glagol أو أي بروتوكول داخلي غير موثق.
