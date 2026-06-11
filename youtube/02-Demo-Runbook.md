# 🧪 Demo Runbook — Canlı Kayıt Senaryosu

> Bu dosya, videodaki **canlı demo** kısmı içindir.
> Her adımda: **ne yapacaksın**, **hangi ekranı göstereceksin** ve **ne söyleyeceksin**.
> Hedef demo süresi: **~10 dakika**.

---

## ✅ ÇEKİMDEN ÖNCE HAZIRLIK (kayda ALMA)

Demoyu akıcı çekmek için cluster'ı **kayıttan önce bir kez** kur — `local_test.sh` ilk seferinde Docker image build ettiği için 3–5 dk sürebilir. Bunu izleyiciye canlı izletme.

```bash
cd /Users/hakan/devopsatolyesi/devops-ai-k8s-agent

# (Opsiyonel) AI'ı canlı göstereceksen anahtarı ver:
export PIONEER_API_KEY="sk-..."          # yoksa boş bırak → sadece kural modu
export PIONEER_MODEL="<saglayici-model>" # opsiyonel
export PIONEER_ENDPOINT="<endpoint>"     # opsiyonel

chmod +x scripts/local_test.sh
./scripts/local_test.sh
```

Bu script sırayla şunları yapar (izleyiciye anlatabileceğin adımlar):
1. Ön gereksinimleri kontrol eder (Docker, kind, kubectl)
2. (Varsa) API anahtarını doğrular
3. `ai-kube-agent-local` adlı Kind cluster'ını oluşturur
4. Docker image'ı build edip Kind'a yükler
5. Namespace + Secret + ajan manifest'lerini uygular
6. AI'ı **kapalı** başlatır (ConfigMap `AI_ENABLED=false`)
7. Demo bozuk workload'larını deploy eder
8. `18080` portunu forward eder ve tarayıcıyı açar
9. İlk taramayı tetikleyip terminalde doğrulama özeti basar

**Kayıttan önce kontrol et:**
```bash
kubectl --context kind-ai-kube-agent-local get pods -A | grep -E "ai-kube-agent|demo-broken"
curl -s http://127.0.0.1:18080/healthz   # "ok" dönmeli
```

> 💡 **Temiz başlangıç ister misin?** Bozuk pod'ların önceden çökmüş olmasını istersen cluster'ı hazır bırak. Ama "arızayı canlı üretme" efekti istiyorsan, demoyu **boş** dashboard'la başlat ve "Create Problem" ile canlı arıza üret (aşağıdaki Adım 3). İkincisi videoda daha etkileyici.

---

## 🎬 DEMO ADIMLARI (kayıt sırasında)

### Adım 1 — Tek komutla kurulumu göster (~1 dk)

[EKRAN: Terminal]

Eğer kurulumu canlı göstermek istiyorsan (önerilmez, uzun sürer) script'i çalıştır; **önerilen** yol: önceden kurduğun terminali göster ve şu komutla sağlığı doğrula:

```bash
kubectl --context kind-ai-kube-agent-local get pods -n ai-kube-agent
kubectl --context kind-ai-kube-agent-local get pods -n demo-broken-apps
```

> 🎙️ "Gördüğünüz gibi tek bir script — `local_test.sh` — Kind cluster'ını kuruyor, ajanı deploy ediyor ve demo uygulamalarını yerleştiriyor. Ajan `Running`, demo pod'larının bir kısmı zaten `CrashLoopBackOff` ya da `Error` durumunda. Mükemmel — tam da test etmek istediğimiz şey."

---

### Adım 2 — Dashboard'ı aç ve tanıt (~1 dk)

[EKRAN: Tarayıcı → http://127.0.0.1:18080]

Göster:
- **Üst bar**: Cluster adı, "Resources: X/Y Healthy", AI durumu → **"Disabled (Local Only)"**
- **Sayaç kartları**: Active Findings, Critical, High, Medium, Low, AI Requests, Last Scan
- **Tablo**: Active Problems sekmesi

> 🎙️ "İşte dashboard. Üstte cluster sağlığını ve AI durumunu görüyoruz — şu an 'Disabled, Local Only' yazıyor, çünkü ajan güvenlik gereği AI'ı kapalı başlatır. Aşağıda önem derecesine göre sayaçlar ve aktif bulgu tablosu var. Şu an gördüğümüz tüm bu bulgular **sadece kural motoruyla** tespit edildi — tek bir AI çağrısı bile yapılmadan. AI Requests sayacı bunu kanıtlıyor."

📸 *Bu, slayt 18'de gösterdiğin dashboard ekranının canlı hali — bağ kur.*

---

### Adım 3 — Canlı arıza üret: "Create Problem" (~1.5 dk)

[EKRAN: Sağ üstte **"Create Problem"** düğmesine tıkla]

Açılan modalda senaryoları göster (slayt 20 ile bağ kur), sonra **"Memory Limit Exceeded"** (OOMKilled) yanındaki **Deploy**'a bas.

> 🎙️ "Şimdi canlı bir arıza üretelim. 'Create Problem' menüsünde hazır senaryolar var: crash loop'lar, geçersiz image, eksik ConfigMap, bellek aşımı… Ben 'Memory Limit Exceeded'ı seçiyorum — bu, belleği kasıtlı aşıp **OOMKilled** olan bir pod deploy ediyor. Deploy diyorum… ve bu pod birazdan çökecek."

İstersen terminalde paralel göster:
```bash
kubectl --context kind-ai-kube-agent-local get pods -n demo-broken-apps -w
```
> 🎙️ "Terminalde pod'un `OOMKilled` → `CrashLoopBackOff` döngüsüne girdiğini canlı görüyoruz."

---

### Adım 4 — Tara: "Run Scan" (~1 dk)

[EKRAN: Sağ üstte **"Run Scan"** düğmesine tıkla]

> 🎙️ "Şimdi 'Run Scan' diyorum. Ajan cluster'ı yeniden tarıyor: pod'ları, event'leri, logları topluyor, kural motorundan geçiriyor. Ve işte — yeni OOMKilled bulgusu listeye düştü. Last Scan zamanı güncellendi, Active Findings sayacı arttı."

Bulgunun tabloda göründüğünü göster: namespace `demo-broken-apps`, problem `OOMKilled`, severity badge.

---

### Adım 5 — Bulguyu incele: kanıt + kural analizi (~1.5 dk)

[EKRAN: Tablodaki OOMKilled satırına tıkla → sağ detay paneli açılır]

Göster (slayt 11 ve 13 ile bağ kur):
- **Finding / Status / Severity / Confidence**
- **"Detected by Rules"** bölümü: Rule ID, Rule Confidence, Safe Auto Fix, Needs AI
- **Evidence**: pod adı, container, son termination reason = OOMKilled, resource limit'leri

> 🎙️ "Bulguya tıklayınca sağda detay paneli açılıyor. Burada en önemli kısım: **'Detected by Rules'**. Yani bu tespit AI'dan gelmiyor — deterministik kural motorundan. Kural ID'sini, güven skorunu ve toplanan kanıtı görüyoruz: son termination reason 'OOMKilled', container'ın bellek limiti şu kadar. Henüz hiç AI kullanmadık ve sorunu zaten biliyoruz. İşte birinci katmanın gücü bu."

---

### Adım 6 — AI'ı aç ve plan üret (~2 dk) ⭐

> ⚠️ **Sadece geçerli bir `PIONEER_API_KEY` varsa bu adımı canlı yap.** Anahtar yoksa: bu adımı anlat, slayt 12'deki JSON şemasını göster ve "anahtarınız olduğunda şu çıktıyı alırsınız" de.

[EKRAN: Ayarlar/Settings → **"Enable Pioneer AI Analysis"** toggle'ını aç]

> 🎙️ "Şimdi ikinci katmanı devreye sokalım. Ayarlardan 'Enable Pioneer AI Analysis'i açıyorum. Dikkat: üst bardaki AI durumu artık 'active' oldu."

[EKRAN: Bulgu detayında **"Generate AI Plan"** (AI plan üret) düğmesine tıkla]

> 🎙️ "Bulguya geri dönüp 'Generate AI Plan' diyorum. Arka planda ajan **maskelenmiş** kanıtı Pioneer AI'a gönderiyor — secret'lar, token'lar maskelendi, dışarı çıkmadı. Ve işte yapılandırılmış cevap geldi: muhtemel kök neden, junior dostu açıklama, adım adım plan ve önerilen patch. OOMKilled için öneri: Deployment'taki bellek limitini artırmak."

Göster:
- `probable_root_cause`
- `junior_friendly_explanation`
- `action_plan` (adımlar)
- `proposed_fix` → patch hedefi ve patch verisi

---

### Adım 7 — Patch'i onayla ve uygula (~1 dk) ⭐ İNSAN DÖNGÜDE

[EKRAN: Önerilen patch'i göster → **onay** düğmesine bas]

> 🎙️ "Ve işte projenin kalbi: bu patch **otomatik uygulanmıyor**. Önce ben inceliyorum. Önerilen değişiklik mantıklı — bellek limitini artırıyor, başka hiçbir şeye dokunmuyor. Onaylıyorum. Şimdi backend bu patch'i RBAC sınırları içinde, sadece bu namespace'e uyguluyor."

Paralel terminalde doğrula:
```bash
kubectl --context kind-ai-kube-agent-local get deploy -n demo-broken-apps -o yaml | grep -A3 resources
```

---

### Adım 8 — Çözümü doğrula (~1 dk)

[EKRAN: Tekrar **"Run Scan"** → **"Solved Problems"** sekmesi]

> 🎙️ "Patch uygulandı, pod yeniden başladı. Bir kez daha 'Run Scan' diyorum. Ve bulgu artık 'Active'ten 'Solved Problems' sekmesine geçti. Döngü tamamlandı: arıza tespit edildi → kanıt toplandı → AI plan önerdi → ben onayladım → sistem düzeltti → çözüm doğrulandı. Hepsi tek bir dashboard'dan."

```bash
kubectl --context kind-ai-kube-agent-local get pods -n demo-broken-apps
# OOMKilled pod artık Running olmalı
```

---

## 🧹 ÇEKİM SONRASI TEMİZLİK

```bash
# Sadece demo workload'larını sıfırla (cluster'ı koru):
kubectl --context kind-ai-kube-agent-local delete -f demo/ --ignore-not-found

# Ya da tüm cluster'ı sil:
kind delete cluster --name ai-kube-agent-local
```

---

## 🆘 DEMO KURTARMA (canlı sorun çıkarsa)

| Sorun | Hızlı çözüm |
|-------|-------------|
| Dashboard açılmıyor | `lsof -ti:18080 \| xargs kill -9` sonra port-forward'ı yeniden başlat: `kubectl --context kind-ai-kube-agent-local -n ai-kube-agent port-forward svc/ai-kube-agent 18080:80` |
| Bulgu görünmüyor | Birkaç saniye bekle, pod'ların çökmesi zaman alır; sonra "Run Scan" |
| AI "invalid key" diyor | Anahtarsız devam et, AI adımını slaytla anlat |
| Pod `ImagePull` ile takılı | `kind load docker-image ai-kube-agent:local --name ai-kube-agent-local` |
| Ajan pod'u CrashLoop | `kubectl --context kind-ai-kube-agent-local logs -l app.kubernetes.io/name=ai-kube-agent -n ai-kube-agent` |

---

## 📺 ÇEKİM İPUÇLARI

- **Tarayıcı zoom'unu %110–125 yap** — yazılar küçük, izleyici okuyabilsin.
- Terminal fontunu büyüt (en az 16pt).
- OOMKilled senaryosu en görsel olanı (deploy → çök → düzelt net görünür). İkinci bir senaryo göstereceksen **ImagePullBackOff** hızlı ve nettir.
- AI adımında cevap gelirken **2–3 saniye sus**, izleyici JSON'u görsün; sonra özetle.
- Her büyük adımda slayttaki ilgili kavrama geri referans ver ("hatırlarsanız iki katmanlı zekâ demiştik — işte ikinci katman bu").
- Demo sonunda mutlaka **"Solved"** sekmesini göster — kapanış hissi verir.
