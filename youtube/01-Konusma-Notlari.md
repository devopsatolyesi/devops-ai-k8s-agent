# 🎙️ Konuşma Notları — AI Kubernetes Troubleshooting Agent

> YouTube videosu için slayt-slayt anlatım metni.
> Toplam hedef süre: **~22–28 dakika** (sunum ~14 dk + canlı demo ~10 dk).
> `[EKRAN]` = o anda ne göstereceksin. Metinleri kelime kelime okuman gerekmez; doğal konuş.

---

## 🎬 0. AÇILIŞ KANCASI (Slayt 1) — ~45 sn

[EKRAN: Slayt 1 — Başlık]

> "Bir pod'un `CrashLoopBackOff`'a düştüğünü gördün. Logları aç, event'lere bak, describe et, endpoint'leri kontrol et… Bu zinciri kaç kez tekrarladın?
>
> Bugün, bu işin büyük kısmını senin yerine yapan bir asistan kuracağız. Kendi makinende, gerçek bir Kubernetes cluster'ında çalışan, arızaları tespit eden ve **senin onayınla** düzelten yapay zeka destekli bir DevOps ajanı.
>
> Ama dikkat: bu 'AI her şeyi halleder' videosu değil. Tam tersine, AI'ı **ne zaman** ve **nasıl güvenli** kullanacağımızı konuşacağız. Başlayalım."

💡 *İpucu: İlk 10 saniyede konuyu söyle, izleyici kalsın.*

---

## 📋 1. AJANDA (Slayt 2) — ~40 sn

[EKRAN: Slayt 2 — Ajanda]

> "Şu yol haritasını izleyeceğiz: önce neden AI ve DevOps'un kesiştiğini, sonra Kubernetes'te sorun gidermenin neden zor olduğunu konuşacağız. Ardından ajanın mimarisine, iki katmanlı zekâ tasarımına ve güvenlik kontrollerine bakacağız. Neden Kind seçtiğimizi, kod yapısını göreceğiz. Sonra **canlı demo**: gerçekten bir arıza üretip ajanın tespit edip düzeltmesini izleyeceğiz. En sonda da projeyi nasıl kendin genişletebileceğini anlatacağım."

---

## 🧠 2. NEDEN ŞİMDİ? AI + DEVOPS (Slayt 3) — ~1.5 dk

[EKRAN: Slayt 3]

> "Modern sistemler dağıtık, geçici ve gözlemlenmesi zor. Bir Kubernetes arızasını anlamak için aynı anda birçok yere bakman gerekir: pod durumu, event'ler, loglar, service selector'ları, endpoint'ler, ingress kablolaması… Bu parçaları kafanda birleştirmek hem zaman hem tecrübe ister.
>
> İşte AI tam burada devreye giriyor — ama 'sihir' olarak değil. AI'ı, bu kanıtları senin için hızlıca okuyup yorumlayan bir **asistan** olarak düşün. Bir junior mühendisin yanındaki tecrübeli SRE gibi.
>
> Ancak AI tek başına yeterli değil. Maliyeti var, halüsinasyon görebilir, ve ona körü körüne güvenemezsin. Bu yüzden bu projenin kalbinde şu felsefe var: **önce deterministik kurallar, sonra gerektiğinde AI.** Birazdan bunu detaylandıracağız."

---

## ❗ 3. PROBLEM (Slayt 4) — ~1 dk

[EKRAN: Slayt 4]

> "Somutlaştıralım. Bir pod CrashLoopBackOff'a girdi. Klasik refleks: `kubectl get pods`, sonra `describe`, sonra `logs --previous`, sonra `get events`, belki endpoint ve ingress kontrolü… Her arıza tipi için ayrı bir zincir.
>
> Sağda en sık karşılaştığımız arıza tiplerini görüyorsun: CrashLoop, ImagePullBackOff, OOMKilled, ConfigError, Pending, servisin endpoint bulamaması, ingress'in yanlış backend'e bakması. Junior bir mühendis için bu liste göz korkutucu. Senior için bile tekrarlayan, sıkıcı, zaman alan bir iş. İşte ajanı bunun için yazdık."

---

## ✅ 4. ÇÖZÜM (Slayt 5) — ~1 dk

[EKRAN: Slayt 5]

> "Çözüm şu: cluster'ın **içinde** bir pod olarak çalışan bir FastAPI uygulaması. Bu ajan periyodik olarak pod'ları, servisleri, endpoint'leri ve ingress'leri tarıyor. Sağlıksız bir kaynak bulduğunda kanıt topluyor — durum, restart sayısı, loglar, event'ler. Yerel kural motoru bunu sınıflandırıyor. Gerekiyorsa, **maskelenmiş** kanıtı AI'a gönderiyor. Sonuçları bir web dashboard'da gösteriyor. Ve en kritik nokta: **hiçbir düzeltme senin onayın olmadan uygulanmıyor.** İnsan her zaman döngüde."

---

## ⭐ 5. TEMEL ÖZELLİKLER (Slayt 6) — ~45 sn

[EKRAN: Slayt 6]

> "Özetle altı temel yetenek: yerel izleme, deterministik kurallar, opsiyonel AI, insan onayı, demo senaryoları ve secret maskeleme. Bu kartların her biri bir tasarım kararını temsil ediyor — ve videonun geri kalanında her birini açacağız."

---

## 🏗️ 6. MİMARİ (Slayt 7) — ~2 dk

[EKRAN: Slayt 7 — Mimari diyagramı]

> "Şimdi mimariye bakalım. En dışta **Local Machine** var — yani her şey kendi makinende. Bulut maliyeti yok, kimlik yönetimiyle uğraşmıyorsun.
>
> İçinde **Kind** ile kurulmuş bir Kubernetes cluster'ı var. Kind, 'Kubernetes in Docker' demek — yani Docker konteynerleri içinde çalışan gerçek bir Kubernetes.
>
> İki namespace görüyorsun. Soldaki `ai-kube-agent`: burada FastAPI ajanımız ve bulguları sakladığı SQLite cache var. Sağdaki `demo-broken-apps`: kasıtlı olarak bozduğumuz test uygulamaları.
>
> Ajan, Kubernetes API'sine sorgu atıyor — pod'ları, servisleri okuyor. Kullanıcı tarayıcıdan dashboard'a bağlanıyor. Ve sadece **gerektiğinde**, dışarıya HTTPS ile Pioneer AI API'sine istek gidiyor. Dikkat: AI'a giden ok tek yönlü ve opsiyonel."

---

## 🗂️ 7. NAMESPACE'LER VE BİLEŞENLER (Slayt 8) — ~1 dk

[EKRAN: Slayt 8]

> "Biraz daha detay. `ai-kube-agent` namespace'inde ajanın Deployment'ı, ClusterIP Service'i, ayarları tutan ConfigMap, API key'i tutan Secret, ve yetkilendirme için ServiceAccount ile ClusterRole var.
>
> `demo-broken-apps` namespace'inde ise her biri farklı bir arıza tipini temsil eden workload'lar: crashloop, imagepull, oomkilled, bad-config, servisin endpoint bulamadığı senaryo, ingress hatası, network policy ve AI analiz demosu. Bunları demo sırasında tek tıkla üreteceğiz."

---

## 🔄 8. NASIL ÇALIŞIR — TARAMA DÖNGÜSÜ (Slayt 9) — ~1.5 dk

[EKRAN: Slayt 9 — Numaralı adımlar]

> "Tarama döngüsü şöyle işliyor. Bir, zamanlayıcı ya da arayüzdeki 'Run Scan' düğmesi taramayı tetikliyor. İki, scanner Kubernetes API'sinden pod, servis, ingress, log ve event'leri çekiyor. Üç, kural motoru deterministik kontrolleri çalıştırıp bulguları döndürüyor.
>
> Dört — bu kritik: her bulgu için bir **fingerprint** üretiliyor ve cache kontrol ediliyor. Aynı arıza daha önce görüldüyse, tekrar AI çağrısı yapılmıyor. Bu hem maliyeti hem gürültüyü azaltıyor.
>
> Beş, bulgu yeniyse ve AI izinliyse, maskelenmiş kanıt Pioneer AI'a gidiyor. Altı, sonuç ve opsiyonel patch önerisi SQLite'a kaydediliyor. Yedi, dashboard ve Prometheus metrikleri güncelleniyor."

---

## 🧩 9. İKİ KATMANLI ZEKA (Slayt 10) — ~1.5 dk ⭐ ÖNEMLİ

[EKRAN: Slayt 10 — İki kart]

> "Bu, projenin en önemli tasarım kararı, o yüzden burada biraz duralım.
>
> **Birinci katman: Rule Engine.** Deterministik, açıklanabilir, hızlı ve **ücretsiz**. İnternet bağlantısı olmadan çalışır. Bilinen arıza tiplerini kapsar. Ve her zaman ilk devreye giren katman bu.
>
> **İkinci katman: Pioneer AI.** Sadece karmaşık ya da bilinmeyen durumlar için. Kök nedeni bulur, insan dostu bir açıklama yazar, adım adım plan ve patch önerisi üretir. Ama sadece **gerektiğinde** tetiklenir — yani maliyet ve risk kontrol altında.
>
> Buradaki ana fikir şu: AI'ı her şeye koşturmuyoruz. Ucuz ve kesin olanı önce deniyoruz; pahalı ve olasılıksal olanı en sona saklıyoruz. Bu, üretim sistemlerinde AI kullanmanın doğru yolu."

---

## 🔍 10. RULE ENGINE DETAY (Slayt 11) — ~1.5 dk

[EKRAN: Slayt 11 — Kural listesi]

> "Birinci katman tam olarak ne kontrol ediyor? CrashLoopBackOff için: restart sayısı, back-off event'leri ve loglardaki ipuçları — 'connection refused', 'timeout', 'panic' gibi. ImagePull hataları için: image adı, tag ve imagePullSecrets. OOMKilled için: resource limit'leri ve son termination reason. Pending için: node baskısı, taint/toleration uyumsuzluğu, PVC binding. ConfigError için: eksik ConfigMap ya da Secret referansı. ServiceNoEndpoints için: service selector'ı ile pod label'larının eşleşip eşleşmediği. Ve IngressBadBackend için: referans verilen backend servis ve port'un gerçekten var olup olmadığı.
>
> Dikkat edin — bunların hepsi **kural**, AI değil. Yani deterministik, tekrar edilebilir ve ücretsiz. Bu kontrollerin kodu `app/rule_engine.py` içinde, isteyen inceleyebilir."

---

## 🤖 11. PIONEER AI ENTEGRASYONU (Slayt 12) — ~1.5 dk

[EKRAN: Slayt 12 — JSON şema]

> "İkinci katman devreye girdiğinde ne oluyor? Maskelenmiş kanıt, sohbet tamamlama endpoint'ine gönderiliyor. Sistem prompt'u net: 'Sen bir Kubernetes SRE'sin, SADECE verilen kanıtı kullan, uydurma.' Bu, halüsinasyonu sınırlamak için kritik.
>
> Model çıktısı serbest metin değil, **yapılandırılmış JSON**. Ekranda görüyorsunuz: özet, muhtemel kök neden, önem derecesi, önerilen aksiyonlar, **junior dostu bir açıklama**, adım adım plan, ve en altta `proposed_fix` — yani bir patch hedefi ve patch verisi. Bu yapılandırılmış çıktı sayesinde sonucu dashboard'da düzgün gösterebiliyor ve gerektiğinde patch'i uygulayabiliyoruz.
>
> Not: repodaki varsayılan model adı `pioneer-fast` — nötr bir placeholder. Kendi sağlayıcınız varsa `PIONEER_MODEL` ve `PIONEER_ENDPOINT`'i değiştirebilirsiniz. Bunu sonda göstereceğim."

---

## 🛡️ 12. GÜVENLİK VE MALİYET KONTROLLERİ (Slayt 13) — ~1.5 dk ⭐

[EKRAN: Slayt 13]

> "AI'ı üretimde kullanacaksanız, bu slayt en önemlisi. Beş kontrol var.
>
> Bir: **secret maskeleme**. Authorization başlıkları, DATABASE_URL, password, token gibi hassas değerler dışarı çıkmadan maskeleniyor. AI sağlayıcısı sırlarınızı asla görmüyor.
>
> İki: **fingerprint cache**. Aynı bulgu için tekrar tekrar AI çağrısı yapılmıyor.
>
> Üç: **rate limit**. Tarama başına AI çağrı sayısı sınırlı — sliding-window bir limiter ile.
>
> Dört: **minimum önem filtresi**. Düşük öncelikli bulgular dışarı hiç gönderilmiyor.
>
> Beş: ajan **soğuk başlangıçta AI'ı kapalı** açıyor. Operatör her oturumda AI'ı bilinçli olarak açmak zorunda. Yani kazara maliyet oluşmuyor. Bu kontroller `app/masking.py`, `ai_client.py` ve `config.py` içinde."

---

## 🔁 13. DÜZELTME AKIŞI — İNSAN DÖNGÜDE (Slayt 14) — ~1 dk ⭐

[EKRAN: Slayt 14 — Numaralı adımlar, 4. adım turuncu]

> "Düzeltme akışı altı adım. Scanner bulguyu tespit eder. Kullanıcı dashboard'da açar. AI'dan bir aksiyon planı ister. Ve burada — dördüncü adıma dikkat — üretilen patch **incelenir ve onaylanır**. Backend patch'i SADECE bu onaydan sonra uygular. Sonraki tarama iyileşmeyi doğrularsa bulgu 'resolved' olur.
>
> Yani AI önerir, **insan karar verir, sistem uygular.** Otomasyonu istiyoruz ama körü körüne değil. Bu güvenli otomasyonun özü."

---

## 🔐 14. RBAC SINIRLARI (Slayt 15) — ~1 dk

[EKRAN: Slayt 15]

> "Peki ya ajanın kendisi çok güçlü olursa? Onu da düşündük. ServiceAccount bilinçli olarak kısıtlı. Okuma yetkisi cluster genelinde — pod, servis, endpoint, ingress, event. Ama **yazma** yetkisi sadece seçili demo namespace'indeki seçili kaynaklarla sınırlı. kube-system gibi hassas namespace'ler düzeltme kapsamının tamamen dışında.
>
> Üstelik `AI_REMEDIATION_NAMESPACES` ile yazma alanını daha da daraltabilir, `AI_REMEDIATION_MODE` ile 'sadece öneri' moduna geçip hiçbir şeyin otomatik uygulanmamasını sağlayabilirsiniz. Yani güvenlik hem RBAC hem uygulama seviyesinde iki kez sağlanıyor."

---

## 📦 15. NEDEN KIND + GEREKSİNİMLER (Slayt 16) — ~1.5 dk

[EKRAN: Slayt 16]

> "Sık gelen soru: neden Kind? Çünkü Kind, Docker içinde **gerçek** bir Kubernetes veriyor. Saniyeler içinde kurulup siliniyor. Bulut maliyeti ve kimlik yönetimi derdi yok. Demo için izole ve tek seferlik. Üstelik CI/CD pipeline'ında da aynı şekilde çalışıyor. Yani öğrenmek, denemek ve bu tür bir projeyi göstermek için ideal. minikube ya da k3d de kullanılabilirdi ama Kind en yaygın, en hafif ve CI dostu seçenek.
>
> Gereksinimler de minimal: Docker, Kind, kubectl, Git ve bir tarayıcı. AI özelliğini denemek isterseniz bir de Pioneer API anahtarı — ama o opsiyonel, AI olmadan da kural motoru tek başına çalışıyor."

---

## 💻 16. KOD YAPISI (Slayt 17) — ~1.5 dk

[EKRAN: Slayt 17 — Modül haritası]

> "Kod tarafına geçelim. Proje temiz bir şekilde modüllere ayrılmış. `main.py` FastAPI uygulaması ve route'lar. `scanner.py` taramayı orkestre ediyor — kuralları ve AI'ı birleştiren yer burası. `rule_engine.py` deterministik analizörler. `ai_client.py` Pioneer client'ı, prompt'lar ve rate limiter. `k8s_client.py` Kubernetes API erişimi. `masking.py` maskeleme. `storage.py` SQLite kalıcılık. `models.py` Pydantic modeller ve fingerprint üretimi. `config.py` env tabanlı ayarlar. Ve `metrics.py` Prometheus metrikleri.
>
> Bu ayrım önemli çünkü projeyi genişletmek istediğinizde tam olarak hangi dosyaya dokunacağınızı biliyorsunuz. Yeni bir AI sağlayıcı mı? `ai_client.py`. Yeni bir kural mı? `rule_engine.py`. Sonda buna döneceğiz."

---

## 🖥️ 17. DASHBOARD (Slayt 18) — ~1 dk

[EKRAN: Slayt 18 — Dashboard ekran görüntüsü]

> "İşte dashboard. Üst barda cluster adı, kaynak sağlığı ve AI durumunu görüyorsun. Altında sayaçlar: aktif bulgular ve önem derecelerine göre dağılım — Critical, High, Medium, Low. Ortadaki tabloda aktif ve çözülmüş bulgular. Bir bulguya tıkladığında sağda detay paneli açılıyor: toplanan kanıt, kural analizi ve varsa AI analizi. Sağ üstte 'Run Scan' ile manuel tarama tetikliyorsun, 'Create Problem' ile demo arıza üretiyorsun. Birazdan bunların hepsini canlı göreceğiz."

---

## ▶️ 18. CANLI DEMO PLANI (Slayt 19) — geçiş, ~30 sn

[EKRAN: Slayt 19 → sonra terminale/tarayıcıya geç]

> "Tamam, yeterince anlattık — şimdi gerçek iş. Şu sekiz adımı canlı yapacağız: tek komutla cluster'ı ve ajanı kuracağız, dashboard'ı açacağız, bir OOMKilled arızası üreteceğiz, tarayacağız, bulguyu inceleyeceğiz, AI planını üreteceğiz, patch'i onaylayıp uygulayacağız ve arızanın çözülmesini izleyeceğiz. **Demo runbook dosyasındaki komutları sırayla takip edeceğim.**"

👉 **Bu noktada `02-Demo-Runbook.md` dosyasına geç. Demo bittikten sonra Slayt 20'ye dön.**

---

## 🧪 19. DEMO SENARYOLARI (Slayt 20) — ~45 sn (demo dönüşü)

[EKRAN: Slayt 20]

> "Demo sırasında 'Create Problem' menüsünde gördüğünüz senaryolar bunlar: Python ve Go crash loop'ları, geçersiz container image, eksik ConfigMap, bellek aşımı, ve servis/ingress hataları. Her birini ayrı ayrı deploy edip ajanın nasıl tepki verdiğini test edebilirsiniz. Videoda OOMKilled'ı gösterdik ama hepsini denemenizi tavsiye ederim — her biri farklı bir kural yolunu tetikliyor."

---

## 🚀 20. PROJEYİ GENİŞLETME (Slayt 21) — ~1.5 dk

[EKRAN: Slayt 21 — İki kart]

> "Ve işte 'sizin sıranız' kısmı. Bu projeyi iki yönde kolayca genişletebilirsiniz.
>
> Soldaki: **yeni bir AI sağlayıcı**. `ai_client.py`'a yeni bir client ekleyin, `config.py`'a gerekli env değişkenlerini koyun, ve endpoint ile model'i yönlendirin. Böylece OpenAI, AWS Bedrock ya da yerel bir LLM bağlayabilirsiniz.
>
> Sağdaki: **yeni bir deterministik kural**. `rule_engine.py`'daki analizörleri genişletin, yeni arıza tipini sınıflandırın, ve `tests/test_rule_engine.py`'a bir test ekleyin. Örneğin HPA, PodDisruptionBudget ya da sertifika kontrolleri ekleyebilirsiniz.
>
> İsterseniz şu modülleri de ekleyebilirsiniz: Slack/Discord bildirimi, Grafana dashboard, ya da çoklu cluster desteği. Repo bunların hepsine açık."

---

## 📚 21. KAYNAKLAR — REPO + MEDIUM (Slayt 22) — ~45 sn

[EKRAN: Slayt 22]

> "İki kaynak bırakıyorum. Birincisi **GitHub repo** — tüm kod, manifest'ler, demo'lar ve scriptler burada. `git clone` yapıp `./scripts/local_test.sh` çalıştırmanız yeterli. İkincisi, bu proje hakkında yazdığım **Medium makalesi** — orada tasarım kararlarını ve detayları daha derinlemesine anlattım. İkisinin linki de açıklamada. Tavsiyem: önce repoyu çalıştırın, ellerinizi kirletin, sonra makaleyle derinleşin. Kod ve yazı birlikte en iyi öğretir."

---

## 🎯 22. ÖZET (Slayt 23) — ~1 dk

[EKRAN: Slayt 23]

> "Toparlayalım. AI, DevOps'ta sihir değil; kanıtı hızlı okuyan bir asistan. Önce deterministik kurallar kullanıyoruz — hız, ücretsizlik, açıklanabilirlik için. AI'ı sadece gerektiğinde devreye sokuyoruz — maliyet ve risk kontrol altında. Maskeleme, rate-limit ve insan onayı bir araya gelince **güvenli otomasyon** elde ediyoruz. Kind sayesinde her şey lokalde, maliyetsiz ve tekrar edilebilir. Ve mimari genişletilebilir — yeni sağlayıcı, yeni kural eklemek kolay."

---

## 👋 23. KAPANIŞ / CTA (Slayt 24) — ~30 sn

[EKRAN: Slayt 24]

> "Eğer buraya kadar geldiyseniz, gerçekten teşekkürler. Yapacağınız tek şey: repoyu klonlayın, `local_test.sh`'i çalıştırın, ve kendi kuralınızı ekleyin. Beğendiyseniz abone olmayı ve yorumda hangi modülü eklememi istediğinizi yazmayı unutmayın. Repo ve Medium linki açıklamada. Bir sonraki videoda görüşmek üzere — hoşça kalın!"

---

## ⏱️ Süre Özeti

| Bölüm | Slayt | Süre |
|-------|-------|------|
| Açılış + Ajanda | 1–2 | ~1.5 dk |
| Bağlam + Problem + Çözüm | 3–5 | ~3.5 dk |
| Mimari + Akış | 6–9 | ~5 dk |
| İki katman + Kurallar + AI | 10–12 | ~4.5 dk |
| Güvenlik + RBAC | 13–15 | ~3.5 dk |
| Kind + Kod + Dashboard | 16–18 | ~4 dk |
| **CANLI DEMO** | (runbook) | **~10 dk** |
| Senaryolar + Genişletme + Kapanış | 20–24 | ~4 dk |
| **TOPLAM** | | **~26 dk** |

> 📌 Video uzun gelirse: Slayt 8, 11, 15'i kısaltabilir veya atlayabilirsin. Demo en değerli kısım — onu asla kısma.
