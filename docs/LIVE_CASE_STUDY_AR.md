# دراسة حالة حقيقية منزوعة البيانات — من مساعد محلي إلى Yasmina + Home Assistant

> هذه دراسة حالة مبنية على تركيب منزلي حقيقي. كل الأسماء الحساسة، المعرّفات، عناوين IP، MAC، IDs، domains، tokens، وأي بيانات حساب تم حذفها أو استبدالها بأمثلة عامة.

## الهدف

كان عندنا منزل يعمل بالفعل بـHome Assistant ومساعد صوتي محلي قديم. المطلوب لم يكن بناء مساعد ذكي جديد من الصفر، بل:

1. جعل **Yasmina** هي الواجهة الصوتية الأساسية في البيت.
2. الإبقاء على **Home Assistant** كطبقة التحكم الفعلية.
3. تشغيل الإضاءة والتلفزيون بطريقة طبيعية من الصوت.
4. عدم فتح أي ميكروفون خام أو إرسال تسجيلات.
5. عدم كسر الأتمتات القديمة أثناء الانتقال.
6. عدم اعتبار أي أمر "ناجح" إلا بعد وجود دليل على التنفيذ الفعلي.

---

# قبل المشروع

البيت كان يحتوي على:

- Home Assistant server يعمل بالفعل.
- عدة لمبات Wi‑Fi داخل غرفة واحدة.
- Android TV متصل بـHome Assistant.
- مساعد Home Assistant Voice قديم مستخدم سابقًا.
- بعض الأتمتات القديمة التي لم نرد كسرها.

المساعد القديم تم الإبقاء عليه كـfallback، لكن **ميكروفونه تم كتمه** أثناء الاستخدام الطبيعي حتى لا يستمع مساعدان إلى نفس الكلام.

هذا الجهاز الثاني **ليس Requirement** للمشروع. هو مجرد جزء من تركيب البيت المرجعي.

---

# المرحلة 1 — جرد الموجود قبل أي تعديل

أول خطوة لم تكن كتابة كود.

عملنا حصرًا كاملًا لـ:

- كل اللمبات الفعلية.
- أسماء Home Assistant entities.
- المجموعة التي تمثل نور الغرفة.
- كيان Android TV.
- التطبيقات المثبتة فعليًا على التلفزيون.
- الأوامر القديمة الموجودة.
- ما يجب الحفاظ عليه وما يمكن استبداله.

## لماذا كانت الخطوة مهمة؟

لأن أسوأ طريقة لبناء Smart Home bridge هي أن يبدأ بافتراضات عن أسماء الأجهزة أو عددها.

النتيجة التي اعتمدناها لاحقًا كانت قائمة allowlist ثابتة:

```text
Configured light group
Configured light members
Configured Android TV
Configured application packages
```

ولم نسمح للـpublic provider أن يكتشف أو يتحكم في أجهزة عشوائية خارج هذه القائمة.

---

# المرحلة 2 — فصل "فهم الصوت" عن "تنفيذ الجهاز"

في البداية كان من السهل الوقوع في فكرة:

> "نحتاج أن نأخذ كلام المستخدم كاملًا من Yasmina ونرسله إلى Home Assistant."

لكن اتضح أن هذا غير مطلوب للتحكم المنزلي.

Yasmina/Yango Smart Home بالفعل يقومان بفهم الأمر وتحويله إلى **typed device action**.

إذن المعمارية أصبحت:

```text
User voice
   ↓
Yasmina
   ↓
Yango Smart Home NLU
   ↓
Typed action
   ↓
Our provider
   ↓
Home Assistant
   ↓
Physical device
```

هذه كانت نقطة التحول الأساسية في المشروع.

بدل بناء speech bridge، بنينا **device provider آمن ومحدد**.

---

# المرحلة 3 — إنشاء Provider وربط الحساب

أنشأنا Smart Home provider وربطناه بعنوان HTTPS خاص بالخدمة.

الـprovider يدعم المسارات القياسية اللازمة:

```text
GET  /v1.0/user/devices
POST /v1.0/user/devices/query
POST /v1.0/user/devices/action
POST /v1.0/user/unlink
GET/POST /oauth/authorize
POST /oauth/token
```

ثم أضفنا OAuth Authorization Code Flow.

## مبدأ أمان مهم

لم نضع Home Assistant token داخل أي public endpoint.

الـpublic service يحتفظ فقط بربط حساب Smart Home، بينما Home Assistant credential يبقى ملفًا محليًا بصلاحيات مقيدة.

---

# المرحلة 4 — الإضاءة أولًا

بدأنا بأبسط شيء يمكن التحقق منه بصريًا: الإضاءة.

بدل كشف كل شيء دفعة واحدة، بدأنا بمجموعة واحدة.

بعد نجاح التشغيل والإطفاء، توسعنا إلى:

- المجموعة.
- اللمبات الفردية.
- السطوع.
- درجة الأبيض.
- تشغيل عدد محدد من اللمبات.
- زيادة/تقليل عدد اللمبات.

## درس مهم

لم نعتبر أن "HTTP 200" يعني نجاح الأمر.

النجاح الحقيقي كان:

```text
Voice request
→ provider action
→ Home Assistant write
→ readback
→ physical state matches requested state
```

---

# المرحلة 5 — التلفزيون كشف مشكلة أصعب

التلفزيون كان أصعب من الإضاءة لسببين:

1. Android TV قد يكون نائمًا وغير متصل بـADB.
2. Yango ينتظر رد provider خلال وقت قصير.

في أول تصميم، عند تشغيل التلفزيون وهو مطفأ:

- provider أرسل wake.
- التلفزيون بدأ يستيقظ.
- لكن الاستيقاظ استغرق أطول من مهلة الطلب.
- provider رجع خطأ رغم أن التلفزيون استيقظ بعد ذلك.

هذا يعني أن **الفعل الفيزيائي حصل لكن الرد المنطقي كان خاطئًا**.

---

# المرحلة 6 — حل cold start بدون إعادة إرسال الأمر

الحل لم يكن زيادة retries.

بل أنشأنا worker محلي دائم داخل بيئة Home Assistant:

```text
Public provider
   ↓
Home Assistant API
   ↓
Fixed typed dispatcher
   ↓
Unix socket
   ↓
Persistent TV worker
   ↓
ADB / Wake-on-LAN
```

## لماذا Unix socket؟

لأننا لم نرد فتح TCP service جديدة داخل الشبكة.

## لماذا worker دائم؟

حتى نحافظ على ADB connection ونتجنب تكلفة إعادة الاتصال في كل أمر.

## لماذا لا نعيد إرسال الأمر؟

لأن retry غير منضبط ممكن ينفذ نفس الفعل مرتين.

لذلك:

- كل request له identity.
- كل write يُسجل مرة واحدة.
- الأمر غير المؤكد لا يعاد تلقائيًا.
- Cold start يتحول إلى sequence واحدة bounded.
- worker هو الذي يتحقق من النتيجة لاحقًا.

---

# المرحلة 7 — Wake-on-LAN + ADB verification

عند طلب تشغيل التلفزيون أو فتح تطبيق والتلفزيون نائم:

1. يتم فحص هل ADB reachable.
2. إذا لم يكن reachable يتم إرسال Wake-on-LAN مرة واحدة.
3. يتم انتظار الشبكة داخل deadline محدود.
4. يتم فتح ADB.
5. يتم تنفيذ الأمر مرة واحدة.
6. يتم قراءة foreground package / power state.
7. لا يعتبر التطبيق مفتوحًا إلا إذا ظهر package المتوقع.

مثال عام:

```text
Requested: open YouTube
Expected package: com.google.android.youtube.tv

Success requires:
power == true
AND foreground_app == expected package
```

---

# المرحلة 8 — التطبيقات: التنفيذ شيء وفهم الاسم شيء آخر

تم ربط مصادر التلفزيون بأربع تطبيقات:

| Mode | Application |
| --- | --- |
| one | YouTube |
| two | Shahid |
| three | Watch It |
| four | Yango Play |

## ما الذي حدث عمليًا؟

### Shahid

الأمر الطبيعي تم فهمه جيدًا ووصل كـopen-app action.

**تم التحقق صوتيًا وتنفيذيًا.**

### YouTube

ظهر تعارض في NLU.

بعض الجمل مثل "افتحي يوتيوب على التلفزيون" كان من الممكن أن تُفهم كأمر عام لتشغيل التلفزيون بدل فتح التطبيق.

الحل كان إضافة voice scenario بعبارة أكثر تمييزًا.

**تم التحقق صوتيًا وتنفيذيًا.**

### Yango Play

الانتقال من الشاشة الرئيسية إلى Yango Play وصل كـopen-app action صحيح.

**تم التحقق صوتيًا وتنفيذيًا.**

### Watch It

هنا ظهر درس مهم جدًا.

فتح التطبيق نفسه من backend نجح وتم التحقق من foreground package.

لكن محاولات نطق الاسم بعدة صيغ لم تكن موثوقة على الحساب المرجعي.

إذن الحالة الصحيحة هي:

```text
Backend capability: VERIFIED
Voice phrase recognition: NOT RELIABLE
```

ولم نحول ذلك إلى "نجاح" في التوثيق.

---

# المرحلة 9 — Home / Back / playback / volume

بعد التطبيقات أضفنا:

- Home.
- Back.
- Next.
- Pause.
- Resume.
- Mute.
- Unmute.
- Volume up/down.

تم استخدام Android key events أو typed actions، مع readback عندما تكون الحالة قابلة للقراءة.

Home كان مهمًا لأنه يرجع إلى launcher الفعلي الموجود على التلفزيون، وليس إلى تطبيق ثابت مفترض.

---

# المرحلة 10 — محاولة جعل الأوامر أكثر طبيعية

Yango Smart Home scenarios تقبل عددًا محدودًا من العبارات لكل scenario.

لذلك بدل الاعتماد على جملة واحدة، استخدمنا عدة صيغ لنفس المعنى:

```text
"شغلي التلفزيون"
"افتحي التلفزيون"
"اطفي التلفزيون"
"اقفلي التلفزيون"
...
```

لكن ظهر درس مهم:

> وجود الجملة في Scenario لا يساوي أن الـASR/NLU سيطابقها دائمًا كما كتبناها.

لذلك المشروع يفرق بين:

- **Configured phrase**
- **Provider action observed**
- **Physical result observed**

---

# المرحلة 11 — الموسيقى والقرآن

كان الهدف أن أي طلب موسيقى أو قرآن يفتح YouTube على التلفزيون بدل تشغيل الصوت من Yasmina نفسها.

تمكنا من إنشاء scenarios ثابتة مثل:

```text
شغلي أغاني
شغلي قرآن
افتحي يوتيوب للموسيقى
افتحي يوتيوب للقرآن
```

لكن هذا لا يساوي تغيير default media provider.

القيد الحالي:

- يمكن فتح YouTube بعبارة ثابتة.
- لا نحصل على اسم أغنية/قارئ متغير من Smart Home action.
- لذلك لا نستطيع تحويل أي جملة حرة إلى YouTube search عام من هذا المسار وحده.

هذه نقطة موثقة كـKnown Limitation وليست bug مخفيًا.

---

# المرحلة 12 — لماذا لم نكمل مسار الكلام الحر؟

تم عمل بحث منفصل لمعرفة هل يمكن استخراج transcript حر من Yasmina.

النتيجة:

- التحكم المنزلي لا يحتاج هذا المسار.
- المسار العام للـSmart Home لا يرسل لنا raw transcript.
- الاعتماد على interfaces داخلية غير موثقة سيزيد المخاطر والصيانة.

لذلك **الإصدار العام v1.0 لا يعتمد على أي interface داخلي غير رسمي داخل السماعة**.

---

# المرحلة 13 — لماذا أبقينا المساعد القديم والميك مقفول؟

في البيت المرجعي كان هناك Home Assistant Voice device قديم.

قررنا:

```text
Yasmina = primary microphone / wake interface
Old HA Voice = fallback / legacy device
Old HA Voice microphone = muted
```

الهدف منع:

- wake مزدوج.
- ردين في نفس الوقت.
- تنفيذ الأتمتة مرتين.
- التباس أثناء الاختبارات.

مرة أخرى: هذا **ليس Requirement للمستخدمين الآخرين**.

---

# ما تم التحقق منه في التجربة المرجعية

| Capability | Verification |
| --- | --- |
| Lighting group on/off | Live voice + physical |
| Individual/multi-light backend | Provider + HA |
| Brightness / white temperature | Backend + live validation |
| TV power | Live provider + physical readback |
| Cold TV wake | Worker + physical readback |
| Shahid | Live voice + foreground package |
| YouTube | Live voice using distinct scenario + foreground package |
| Yango Play | Live voice + foreground package |
| TV Home | Live voice + launcher readback |
| Mute / unmute | Provider + readback |
| Watch It backend | Direct backend + foreground package |
| Watch It voice name | Not reliable |
| Free-form transcript bridge | Not part of v1.0 |
| Global music/Quran → YouTube provider override | Not supported |

---

# ما الذي لم ننشره من التجربة الحقيقية؟

لم يتم وضع أي من التالي في GitHub:

- عنوان IP الحقيقي.
- MAC address حقيقي.
- Home Assistant entity IDs الحقيقية.
- Yango/Yasmina account IDs.
- Skill ID.
- OAuth secrets.
- Access/refresh tokens.
- Cookies.
- ADB private key.
- Scenario IDs.
- Domains الحقيقية.
- Logs الحقيقية.
- Screenshots تحتوي بيانات حساب.
- تسجيلات صوتية.
- Runtime databases.

كل الأمثلة الموجودة في المشروع تستخدم أسماء وعناوين عامة أو documentation ranges.

---

# النتيجة النهائية

النسخة التي خرجنا بها ليست "Hack للسماعة".

هي طبقة تكامل واضحة:

```text
Yasmina
  ↓
Yango Smart Home
  ↓
OAuth protected provider
  ↓
Home Assistant
  ↓
Lights / Android TV
```

وأكبر درس من التجربة كان:

> لا تعتبر الاستجابة الكلامية أو HTTP status دليلًا على نجاح جهاز فعلي.  
> اربط كل claim بدليل provider + readback + حالة الجهاز متى كان ذلك ممكنًا.

هذا المبدأ هو السبب في وجود ledgers، typed actions، deadlines، no-retry-on-uncertain-write، وphysical readback داخل المشروع.
