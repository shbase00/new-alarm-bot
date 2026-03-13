# إعداد Railway Volume (لحفظ الداتا)

## ليه محتاج Volume؟
Railway بيمسح كل الملفات لما البوت يتعمله redeploy.
الـ Volume بيخلي الـ database بتقى محفوظة.

## خطوات إعداد الـ Volume

### 1. افتح مشروعك في Railway
### 2. اضغط على الـ service بتاع البوت
### 3. روح تبويب "Volumes"
### 4. اضغط "+ Add Volume"
   - Mount Path: `/data`
   - Size: 1 GB (كفاية)
### 5. اضغط "Deploy"

### 6. روح تبويب "Variables" وأضف:
```
DATABASE_PATH=/data/mints.db
```

### 7. اعمل Redeploy

## بعد كده
الداتا بتاعتك (كل المينتات والقنوات) هتتحفظ حتى لو البوت اتعمله restart أو redeploy.
