# AI Kubernetes Troubleshooting Agent - Kullanım Kılavuzu

Bu kılavuz, yerel Kind Kubernetes cluster'ınız üzerinde çalışan **AI Kubernetes Troubleshooting Agent** uygulamasını nasıl kullanacağınızı, hataları nasıl simüle edeceğinizi ve AI/yerel kurallar ile sorunları nasıl çözeceğinizi adım adım açıklar.

---

## 🎯 Temel Akış ve Yetenekler

Agent, cluster içindeki sorunları otomatik olarak algılar ve çözmenize yardımcı olur:
1. **Algılama (Detect)**: `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, `CreateContainerConfigError`, `ServiceNoEndpoints` ve `IngressBadBackend` gibi durumları izler.
2. **Yerel Analiz (Local Analysis)**: Deterministik kurallar sayesinde hata türüne göre hızlı kök neden tespiti yapar.
3. **AI Analizi (AI Analysis - İsteğe Bağlı)**: Pioneer AI (Claude) aktif olduğunda, derinlemesine analizler ve özel düzeltme adımları sunar.
4. **Kullanıcı Onaylı İyileştirme (Action Plan)**: Kullanıcı detay panelinde sunulan parametreleri onayladığında, agent cluster'a `kubectl patch` uygulayarak hatayı kendi kendine giderir (self-healing).

---

## 💻 Dashboard Kullanım Rehberi

Dashboard'a **http://127.0.0.1:18080** adresinden erişebilirsiniz. Arayüz şu temel bileşenlerden oluşur:

### 1. Üst Durum Çubuğu (Top Bar)
- **Cluster Name**: Bağlı olunan local cluster adı (örn: `ai-kube-agent-local`).
- **Resource Summary**: Sağlıklı ve sağlıksız pod sayıları.
- **AI Status Badge**: AI durumunu gösterir. Eğer API anahtarı girildiyse ve ayarlardan etkinleştirildiyse **AI Active** (yeşil), aksi takdirde **AI Inactive (Local Rules Only)** (gri) yazar.
- **Butonlar**: 
  - **Create Problem**: Hata simülasyon modalını açar.
  - **Run Scan**: Manuel tarama tetikler (normalde 60 saniyede bir otomatik çalışır).

### 2. Metrik Kartları
- **Active Findings**: Cluster'da şu anda çözülmemiş olan aktif sorun sayısı.
- **Critical / High / Medium / Low**: Hataların önem derecelerine göre dağılımı.
- **AI Requests**: Agent'ın Pioneer AI API'sine gönderdiği başarılı istek sayısı.
- **Last Scan**: Son tarama döngüsünün tam zaman damgası.

### 3. Bulgular Tablosu (Findings Table)
Cluster'da bulunan tüm aktif hataları listeler. Her satırda hata önemi (severity), namespace, kaynak adı, hata türü, AI kullanım durumu ve son görülme zamanı yer alır. 
- Satırın sonundaki **"Open"** butonuna basarak detay panelini açabilirsiniz.

---

## 🔬 Adım Adım Hata Simülasyonu ve Çözümü

Agent'ı test etmek için kasıtlı bir hata oluşturup çözme adımları aşağıda gösterilmiştir:

### Adım 1: Yeni Bir Hata Oluşturun
1. Dashboard'un sağ üst köşesindeki **Create Problem** butonuna tıklayın.
2. Açılan modalda simüle etmek istediğiniz hata tipini seçin (örneğin: **ImagePullBackOff**).
3. **Create Problem** butonuna tıklayın. Bu işlem arka planda `demo-broken-apps` namespace'ine hatalı bir deployment gönderecektir.

### Adım 2: Hatanın Algılanmasını İzleyin
1. Pod'un durumu cluster üzerinde bozulacaktır. Birkaç saniye içinde agent bunu algılar (veya hemen görmek için **Run Scan** butonuna basabilirsiniz).
2. Bulgular tablosunda yeni bir satır belirecektir (örn: `imagepull-demo`).

### Adım 3: Kök Nedeni İnceleyin
1. Hatalı satırın en sağındaki **Open** butonuna tıklayın.
2. Sağda açılan detay panelinde:
   - **Detected by Rules**: Yerel kuralların ürettiği tespiti inceleyin (örn: "Registry authentication failure or non-existing image").
   - **AI Analysis**: Eğer AI aktifse, Claude tarafından üretilen junior-friendly açıklamayı okuyun.
   - **Recommended Actions** ve **Commands to verify** alanlarını inceleyerek hatayı manuel nasıl doğrulayacağınızı öğrenin.

### Adım 4: Hatayı AI/Agent ile Çözün (Self-Healing)
1. Detay panelindeki **Action Plan** bölümüne gidin.
2. Agent'ın sizin için hazırladığı parametreleri inceleyin. Örneğin `ImagePullBackOff` hatası için doğru resim adını (örn: `nginx:alpine` veya `python:3.11-alpine`) içeren bir girdi kutusu göreceksiniz.
3. Parametreyi girin veya varsayılan öneriyi kabul edin, ardından **Confirm and Apply Fix** butonuna tıklayın.
4. Agent, Kubernetes deployment nesnesini patch'leyecektir. Satır durumu **Remediating** (sarı) olarak güncellenir.
5. Yaklaşık 30-60 saniye bekledikten sonra (pod yeniden başladığında), bir sonraki scan ile birlikte bulgu **Solved** (yeşil) durumuna geçecek ve dashboard'daki hata sayacından düşecektir.

---

## ⚙️ Arayüz Üzerinden Canlı Ayarlar (Runtime Settings)

Dashboard'un en altında yer alan **Runtime Agent Settings** panelinden agent'ın çalışma ayarlarını değiştirebilirsiniz:

- **Scan Interval (seconds)**: Taramaların kaç saniyede bir çalışacağını ayarlar. Local testler için `30` veya `60` idealdir.
- **AI Min Severity Filter**: Hangi seviyenin üzerindeki hataların AI analizine gönderileceğini seçer (örn: `High` seviyesi seçilirse, `Medium` veya `Low` hatalar yalnızca yerel kurallarla çözülür, AI API'sine gitmez. Bu sayede token tasarrufu sağlanır).
- **AI Rate Limit (per scan)**: Bir taramada en fazla kaç tane yeni hatanın AI'ye gönderilebileceğini sınırlar (varsayılan: 5).
- **Log Line Limit**: Analiz için AI'ye gönderilecek maksimum log satır limiti (varsayılan: 150).
- **Enable Pioneer AI Analysis**: AI analizi ve etkileşimli iyileştirme özelliklerini tamamen açıp kapatır. Kapalıyken agent yalnızca yerel deterministik analizleri sunar ve **sıfır token** tüketir.

---

## 🔍 Manuel CLI Doğrulama Komutları

Arayüz dışında, terminal üzerinden cluster'da ne olduğunu doğrulamak için aşağıdaki standart kubectl komutlarını kullanabilirsiniz:

### 1. CrashLoopBackOff Hataları İçin
Pod sürekli çöküp yeniden başlıyorsa loglarını ve durumunu inceleyin:
```bash
# Pod detaylarını görün (Eventleri kontrol edin)
kubectl describe pod <pod-adi> -n demo-broken-apps

# Bir önceki çöküşe ait logları çekin (çok kritik!)
kubectl logs <pod-adi> -n demo-broken-apps -c app --previous --tail=150
```

### 2. ImagePullBackOff / ErrImagePull Hataları İçin
```bash
# Image çekme hatasının detaylarını eventlerden inceleyin
kubectl describe pod <pod-adi> -n demo-broken-apps
```

### 3. OOMKilled (Out Of Memory) Hataları İçin
```bash
# Pod'un son çökme nedenini kontrol edin (Terminated Reason: OOMKilled)
kubectl describe pod <pod-adi> -n demo-broken-apps

# (Varsa) Konteyner bazlı anlık kaynak tüketimini izleyin
kubectl top pod <pod-adi> -n demo-broken-apps --containers
```

### 4. CreateContainerConfigError Hataları İçin
Genellikle eksik ConfigMap veya Secret kaynaklarından kaynaklanır:
```bash
# Eksik olan configmap/secret referansını bulun
kubectl describe pod <pod-adi> -n demo-broken-apps

# Namespace'teki mevcut configmap'leri listeleyin
kubectl get configmaps -n demo-broken-apps
```

### 5. Service has no endpoints Hataları İçin
Servislerin arkasındaki podlar hazır olmadığında veya label uyuşmazlığı olduğunda endpoints boş kalır:
```bash
# Servisin hangi selector'ı aradığını görün
kubectl describe service <service-adi> -n demo-broken-apps

# Endpoints listesini kontrol edin (boş veya <none> olmalı)
kubectl get endpoints <service-adi> -n demo-broken-apps

# Pod label'ları ile selector'ı karşılaştırın
kubectl get pods -n demo-broken-apps --show-labels
```
