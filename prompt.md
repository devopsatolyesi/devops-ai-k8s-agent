Sen senior DevOps Engineer, Kubernetes Engineer, Python Backend Developer, AI Agent Architect ve teknik yazar gibi davran.

Benim için production-ready bir GitHub repository projesi oluşturmanı istiyorum.

Proje adı:
ai-kubernetes-troubleshooting-agent

Projenin amacı:
Kubernetes üzerinde çalışan bir AI destekli troubleshooting agent geliştirmek.

Bu agent, Kubernetes cluster içinde bir Pod olarak çalışacak.
Agent her 2 dakikada bir cluster’ı kontrol edecek.
Pod hataları, Kubernetes events, kubectl describe pod benzeri bilgiler, container logs, service, ingress, endpoint ve trafik akışı ile ilgili verileri toplayacak.
Önce local rule-based analiz yapacak.
Eğer problem karmaşık görünüyorsa Pioneer AI API’ye analiz için güvenli ve maskelenmiş bir prompt gönderecek.
Dönen sonucu dashboard üzerinde gösterecek.

Bu proje özellikle junior DevOps mühendislerinin Kubernetes troubleshooting süresini azaltmak için geliştirilecek.

ÖNEMLİ:
- Bana sadece örnek kod parçaları verme.
- Tam çalışan bir repository yapısı üret.
- Tüm dosyaları tek tek yaz.
- Her dosyanın path bilgisini ver.
- Kodlar çalıştırılabilir olsun.
- Eksik bırakma.
- README, Kubernetes manifestleri, GitHub Actions workflow’ları, test workload’ları ve Medium makalesi dahil olacak.
- LOKALDE PYTHON KOMUTU ÇALIŞTIRILMAYACAKTIR. Proje ve tüm testler/geliştirmeler Kubernetes ortamında (EKS veya yerel Kind) çalıştırılmak üzere tasarlanmıştır.
- Kod geliştirilirken ve test edilirken her şey dockerize edilmelidir. Eğer test yapılacaksa, bu testler Docker konteyneri içinde çalıştırılmalıdır (docker run ile test target'ı).
- Projenin nihai hedefi tamamen Kubernetes üzerinde çalışmaktır (zira programın asıl amacı Kubernetes sorunlarını çözmektir).

==================================================

1. GENEL MİMARİ
==================================================

Aşağıdaki mimariyi kur:

Kubernetes Cluster
 ├── ai-kube-agent namespace
 │    ├── ai-kube-agent Deployment
 │    ├── ai-kube-agent Service
 │    ├── ConfigMap
 │    ├── Secret
 │    ├── ServiceAccount
 │    ├── ClusterRole
 │    ├── ClusterRoleBinding
 │    └── Dashboard Service
 │
 ├── demo-broken-apps namespace
 │    ├── crashloop-demo
 │    ├── imagepull-demo
 │    ├── oomkilled-demo
 │    ├── bad-config-demo
 │    ├── service-no-endpoints-demo
 │    ├── ingress-bad-backend-demo
 │    └── network-policy-demo

Agent workflow:

1. Cluster içindeki podları listeler.
2. Problemli podları tespit eder:
   - CrashLoopBackOff
   - ImagePullBackOff
   - ErrImagePull
   - OOMKilled
   - Pending
   - CreateContainerConfigError
   - CreateContainerError
   - RunContainerError
   - FailedScheduling
   - Readiness probe failed
   - Liveness probe failed
   - Back-off restarting failed container
   - Service has no endpoints
   - Ingress backend service not found
   - DNS/service connection problem
3. Problemli kaynaklar için evidence toplar:
   - Pod status
   - Container statuses
   - Restart count
   - Last termination reason
   - Recent events
   - Last 100-200 log lines
   - Service selector
   - Endpoints
   - Ingress backend
   - Namespace
   - Node
   - Resource requests/limits
4. Önce local rules ile hızlı analiz yapar.
5. Gerekiyorsa Pioneer AI API’ye gider.
6. AI sonucu JSON olarak saklar.
7. Dashboard’da gösterir.
8. Daha önce aynı problem analiz edildiyse cache kullanır.
9. Gereksiz AI request atmaz.
10. Rule engine finding için güvenli auto-fix olup olmadığını belirler.
11. Sadece rule'ün güvenli şekilde çözemediği finding'ler `needs_ai_analysis=true` olarak AI'ye gider.
12. Dashboard detail paneli üç bölümden oluşur:
   - Detected by Rules
   - AI Analysis
   - Action Plan (interactive AI remediation form)

> [!NOTE] CANLI MİMARİ NOTU (2026-05)
> Rule engine **hiçbir zaman otomatik patch uygulamaz** (`proposed_fix=null`).
> Tüm iyileştirmeler kullanıcı onaylı AI etkileşimli akış üzerinden gerçekleşir.

==================================================
2. TEKNOLOJİ SEÇİMİ
==================================================

Backend:

- Python 3.11+
- FastAPI
- Kubernetes Python Client
- Requests veya HTTPX
- Pydantic
- Uvicorn
- Jinja2 veya basit HTML template
- SQLite veya JSON file storage

Dashboard:
Basit ama güzel bir dashboard oluştur.
İlk aşamada ayrı React uygulaması istemiyorum.
FastAPI içinde HTML/CSS/JS ile dashboard üret.
Dashboard modern görünsün.
Karanlık tema olabilir.
Dashboard şu bilgileri göstersin:

- Cluster health summary
- Toplam problem sayısı
- Namespace bazlı problem dağılımı
- Severity: Critical / High / Medium / Low
- Problemli pod listesi
- Problem türü
- İlk görülme zamanı
- Son görülme zamanı
- AI analysis status
- Root cause tahmini
- Recommended action
- Evidence alanı
- Local rule sonucu
- AI sonucu
- “What happened?”
- “Why it happened?”
- “How to fix?”
- “Commands to verify”
- “Prevention recommendations”

Container:

- Dockerfile
- Multi-stage olmasa da temiz olmalı
- Non-root user ile çalışmalı
- Minimal image kullanılmalı

Kubernetes:

- Namespace
- Deployment
- Service
- ConfigMap
- Secret example
- ServiceAccount
- ClusterRole
- ClusterRoleBinding
- NetworkPolicy optional
- Resource requests/limits
- Liveness/readiness probes

CI/CD:
GitHub Actions workflow’ları oluştur:

Workflow 1:
.github/workflows/ci.yml

- Python lint/test
- Docker build test
- Security scan için Trivy ekle
- Unit test çalıştır

Workflow 2:
.github/workflows/create-eks-and-deploy-agent.yml

- AWS credentials GitHub Secrets üzerinden alınacak
- EKS cluster oluşturulacak
- Cluster kurulumunda eksctl veya Terraform kullanılabilir
- Maliyet kontrolü için küçük node group kullanılacak
- Agent Kubernetes’e deploy edilecek
- Dashboard erişimi için port-forward veya LoadBalancer notu eklenecek

Workflow 3:
.github/workflows/deploy-broken-workloads.yml

- demo-broken-apps namespace oluşturulacak
- Bilerek hatalı Kubernetes manifestleri deploy edilecek
- crashloop, imagepull, oom, bad config, service endpoint, ingress backend problemleri oluşturulacak
- Workflow sonunda kubectl get pods/events çıktısı alınacak

Workflow 4:
.github/workflows/validate-agent-findings.yml

- Agent dashboard veya API endpoint kontrol edilecek
- Agent’ın problem bulup bulmadığı test edilecek
- /api/findings endpointinden veri çekilecek
- En az 3 farklı problem yakalandıysa workflow başarılı olacak

Workflow 5:
.github/workflows/destroy-eks.yml

- EKS cluster silinecek
- Maliyet oluşmasını önlemek için manuel tetiklemeli olacak

==================================================
3. PIONEER AI API ENTEGRASYONU
==================================================

Pioneer AI API kullanılacak.

API endpoint:
<https://api.pioneer.ai/v1/chat/completions>

Auth:
Authorization: Bearer $PIONEER_API_KEY

Environment variables:
PIONEER_API_KEY
PIONEER_MODEL
PIONEER_MAX_TOKENS
PIONEER_TEMPERATURE
AI_ENABLED
AI_MIN_SEVERITY
SCAN_INTERVAL_SECONDS

Varsayılan değerler:
PIONEER_MODEL=claude-haiku-4-5
PIONEER_MAX_TOKENS=1500
PIONEER_TEMPERATURE=0.2
AI_ENABLED=false
AI_MIN_SEVERITY=High
SCAN_INTERVAL_SECONDS=600
AI_RATE_LIMIT_PER_SCAN=5
AI_TIMEOUT_SECONDS=30.0
LOG_LINE_LIMIT=150

Çok önemli:
API key hiçbir şekilde kod içine yazılmayacak.
Kubernetes Secret olarak verilecek.
GitHub Actions içinde GitHub Secrets kullanılacak.

Model API key içinde seçilmez.
Model request body içinde seçilir.

AI request body örneği:
{
  "model": "...",
  "messages": [
    {
      "role": "system",
      "content": "You are a senior Kubernetes SRE assistant..."
    },
    {
      "role": "user",
      "content": "Analyze this Kubernetes evidence..."
    }
  ],
  "stream": false,
  "max_tokens": 700,
  "temperature": 0.2
}

AI cevabı mümkünse JSON formatında istenecek:
{
  "summary": "",
  "probable_root_cause": "",
  "severity": "",
  "confidence": "",
  "recommended_actions": [],
  "commands_to_verify": [],
  "prevention": [],
  "junior_friendly_explanation": "",
  "action_plan": [],
  "manual_fix_summary": "",
  "should_auto_apply": false,
  "proposed_fix": null
}

==================================================
4. GÜVENLİK VE MALİYET KONTROLÜ
==================================================

Bu proje güvenli olmalı.

Şunları mutlaka ekle:

- Secret masking
- Token, password, api_key, authorization header, connection string masking
- DATABASE_URL gibi değerlerin maskelenmesi
- Pod env value’larının AI’ye doğrudan gönderilmemesi
- Sadece gerekli evidence gönderilmesi
- Log satır sayısı limiti
- max_tokens limiti
- AI request cache
- Aynı finding için tekrar tekrar AI çağrısı yapılmaması
- Rate limit
- AI disabled mode
- Local-only mode
- AI error handling
- Pioneer API timeout
- Retry ama kontrollü retry
- Usage/cost best practices README içinde anlatılsın

Masking örnekleri:
password=******
token=******
Authorization: Bearer ******
DATABASE_URL=postgres://user:****@host/db
AWS_SECRET_ACCESS_KEY=******

==================================================
5. LOCAL RULE ENGINE
==================================================

Agent sadece AI’ye bağlı kalmasın.
Önce local rule engine çalışsın.

Rule örnekleri:

CrashLoopBackOff:

- Restart count yüksekse
- Last state terminated reason varsa
- Logs içinde “connection refused”, “timeout”, “permission denied”, “module not found”, “cannot connect”, “panic”, “segmentation fault” gibi ifadeler varsa
- Öneri üret:
  - logs kontrol et
  - env/config kontrol et
  - secret/configmap kontrol et
  - readiness/liveness probe kontrol et
  - dependency bağlantılarını kontrol et

ImagePullBackOff:

- Image adı/tag hatalı olabilir
- Registry auth secret eksik olabilir
- Private registry erişimi olmayabilir
- imagePullSecrets kontrol edilir

OOMKilled:

- Last termination reason OOMKilled ise
- Memory limit düşük olabilir
- Memory leak olabilir
- requests/limits kontrol edilir
- metrics-server varsa memory usage önerilir

Pending / FailedScheduling:

- Node resource yetersiz olabilir
- taints/tolerations uyumsuz olabilir
- nodeSelector/affinity hatalı olabilir
- PVC bound olmayabilir

CreateContainerConfigError:

- ConfigMap veya Secret eksik olabilir
- EnvFrom referansı hatalı olabilir

Service has no endpoints:

- Service selector pod label ile eşleşmiyor olabilir
- Pod Ready değil olabilir
- TargetPort yanlış olabilir

Ingress bad backend:

- Ingress service name yanlış olabilir
- Service port yanlış olabilir
- Ingress controller yok olabilir

Probe failed:

- path yanlış olabilir
- port yanlış olabilir
- startup süresi yetersiz olabilir
- initialDelaySeconds artırılabilir

Her rule sonucu şu formatta olsun:
{
  "rule_id": "",
  "problem_type": "",
  "severity": "",
  "reason": "",
  "evidence": [],
  "recommended_actions": [],
  "commands_to_verify": [],
  "safe_auto_fix": false,
  "needs_ai_analysis": false,
  "ai_can_auto_apply": false
}

==================================================
6. BACKEND API ENDPOINTLERİ
==================================================

FastAPI içinde şu endpointleri oluştur:

GET /
Dashboard HTML sayfası

GET /healthz
Agent health check

GET /readyz
Ready check

GET /api/findings
Tüm aktif findings JSON

GET /api/findings/resolved
15 dakikadan eski çözülmüş findings arşivi

GET /api/findings/{finding_id}
Tek finding detayı

GET /api/findings/{finding_id}/ai-plan
AI interaktif iyileştirme planı getir (ai_enabled=true gerektirir)
CrashLoopBackOff, OOMKilled, ImagePullBackOff, ConfigError için parametrik form döner

POST /api/findings/{finding_id}/ai-execute
Kullanıcının onayladığı parametrelerle cluster'a patch uygula

POST /api/scan
Manuel scan tetikler

GET /api/summary
Cluster summary

GET /api/config
Public-safe config bilgisi döner, secret dönmez

POST /api/config
Runtime ayarları güncelle (ai_enabled, scan_interval_seconds, ai_min_severity, vb.)

POST /api/demo/reset
Demo broken apps'leri sıfırla ve anlık scan tetikle

GET /api/metrics
Basit Prometheus formatında metrics üret:

- kube_ai_agent_findings_total
- kube_ai_agent_ai_requests_total
- kube_ai_agent_scan_duration_seconds
- kube_ai_agent_last_scan_timestamp
- kube_ai_agent_ai_errors_total

==================================================
7. VERİ MODELİ
==================================================

Finding modeli oluştur:

id
cluster_name
namespace
resource_kind
resource_name
pod_name
container_name
problem_type
severity
status
first_seen
last_seen
restart_count
local_analysis
ai_analysis
evidence
recommended_actions
commands_to_verify
confidence
fingerprint
ai_used
ai_error
resolved

Fingerprint:
Aynı problem tekrar ederse aynı finding güncellensin.
Fingerprint şu alanlardan üretilebilir:
namespace + pod_name + container_name + problem_type + reason

Storage:
Basitlik için SQLite kullan.
Alternatif olarak JSON file olabilir ama SQLite daha iyi olur.

==================================================
8. PROJE DOSYA YAPISI
==================================================

Repository şu yapıda olsun:

ai-kubernetes-troubleshooting-agent/
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── app/
│   ├── main.py
│   ├── config.py
│   ├── scanner.py
│   ├── k8s_client.py
│   ├── rule_engine.py
│   ├── ai_client.py
│   ├── masking.py
│   ├── storage.py
│   ├── models.py
│   ├── metrics.py
│   ├── templates/
│   │   └── dashboard.html
│   └── static/
│       ├── style.css
│       └── app.js
├── k8s/
│   ├── namespace.yaml
│   ├── serviceaccount.yaml
│   ├── rbac.yaml
│   ├── configmap.yaml
│   ├── secret.example.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   └── kustomization.yaml
├── demo/
│   ├── namespace.yaml
│   ├── crashloop-demo.yaml
│   ├── imagepull-demo.yaml
│   ├── oomkilled-demo.yaml
│   ├── bad-config-demo.yaml
│   ├── service-no-endpoints-demo.yaml
│   ├── ingress-bad-backend-demo.yaml
│   └── network-policy-demo.yaml
├── terraform/
│   └── eks/
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── versions.tf
├── scripts/
│   ├── local_run.sh
│   ├── local_test.sh
│   ├── deploy_agent.sh
│   ├── deploy_demo_broken_apps.sh
│   ├── port_forward_dashboard.sh
│   └── cleanup_demo.sh
├── tests/
│   ├── test_rule_engine.py
│   ├── test_masking.py
│   ├── test_ai_prompt.py
│   └── test_fingerprint.py
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── create-eks-and-deploy-agent.yml
│       ├── deploy-broken-workloads.yml
│       ├── validate-agent-findings.yml
│       └── destroy-eks.yml
└── docs/
    ├── architecture.md
    ├── troubleshooting-scenarios.md
    ├── cost-control.md
    ├── security.md
    ├── medium-tr.md
    └── screenshots.md

==================================================
9. README İÇERİĞİ
==================================================

README çok ayrıntılı olsun.
Türkçe yaz.
Aşağıdaki bölümleri içersin:

Başlık:
AI Kubernetes Troubleshooting Agent

Alt başlık:
Kubernetes hatalarını local rules ve Pioneer AI desteğiyle analiz eden DevOps troubleshooting agent.

Bölümler:

1. Projenin amacı
2. Hangi problemi çözüyor?
3. Kimler için faydalı?
4. Mimari
5. Agent nasıl çalışır?
6. Local rule engine nasıl çalışır?
7. AI ne zaman devreye girer?
8. Pioneer API entegrasyonu
9. API key güvenliği
10. Maliyet kontrolü
11. Kubernetes RBAC açıklaması
12. Local Kubernetes (Kind) Testi ve Çalıştırma
13. Kubernetes’e deploy
14. EKS üzerinde deploy
15. GitHub Actions ile test akışı
16. Demo broken workloads
17. Dashboard kullanımı
18. API endpointleri
19. Prometheus metrics
20. Test senaryoları
21. Güvenlik notları
22. Limitasyonlar
23. Geliştirme fikirleri
24. Medium makalesi için özet

README içinde komutlar olsun:

Local Kubernetes (Kind) Testi:
./scripts/local_test.sh
(PIONEER_API_KEY environment variable is dynamically read or prompted securely with read -s, and then injected into a Kubernetes Secret. No local files contain plain-text keys.)

Kubernetes:
kubectl apply -k k8s/
kubectl get pods -n ai-kube-agent
kubectl port-forward svc/ai-kube-agent 8080:80 -n ai-kube-agent

Demo:
kubectl apply -f demo/
kubectl get pods -n demo-broken-apps
kubectl get events -n demo-broken-apps --sort-by=.lastTimestamp

==================================================
10. TÜRKÇE MEDIUM MAKALESİ
==================================================

docs/medium-tr.md içinde ayrıntılı Türkçe Medium makalesi yaz.

Makale başlığı:
AI Kubernetes Troubleshooting Agent: Kubernetes Hatalarını Pioneer AI ile Analiz Eden Gerçekçi DevOps Projesi

Makale dili:
Türkçe, akıcı, Medium formatına uygun.

Makale şunları içersin:

1. Giriş

- AI’ı sadece chatbot olarak değil, DevOps operasyonlarına yardımcı analiz motoru olarak kullanma fikri.
- Junior DevOps mühendislerinin Kubernetes troubleshooting yaparken yaşadığı sorunlar.
- CrashLoopBackOff, ImagePullBackOff, OOMKilled gibi hataların çoğu zaman log, event ve describe çıktılarının birlikte yorumlanmasını gerektirmesi.

1. Proje fikri

- Kubernetes içinde çalışan agent.
- 2 dakikada bir cluster taraması.
- Local rule engine.
- Pioneer AI entegrasyonu.
- Dashboard.

1. Neden önce local rule engine?

- Her şeyi AI’ye göndermek maliyetli ve gereksiz.
- Basit problemler kurallarla çözülebilir.
- AI sadece karmaşık yorum gerektiğinde çalışmalı.

1. Pioneer AI entegrasyonu

- API key environment variable ile alınır.
- Model request body içinde seçilir.
- max_tokens kullanılır.
- Usage ekranı takip edilir.
- Secretlar maskelenir.

1. Mimari anlatımı

- Agent pod
- RBAC
- Kubernetes API
- Scanner
- Rule engine
- AI client
- Storage
- Dashboard

1. Demo senaryolar

- CrashLoopBackOff
- ImagePullBackOff
- OOMKilled
- Bad ConfigMap/Secret
- Service no endpoints
- Ingress backend error

1. GitHub Actions ile test

- EKS cluster oluşturma
- Agent deploy
- Bozuk workload deploy
- Agent findings validate
- Cluster destroy

1. Dashboard

- Problem listesi
- Root cause
- Recommended action
- Commands to verify
- Junior friendly explanation

1. Güvenlik ve maliyet

- API key saklama
- Secret masking
- Token limit
- Cache
- Rate limit
- EKS maliyet uyarısı
- Destroy workflow önemi

1. Gerçek hayatta nasıl geliştirilebilir?

- Slack alert
- Jira ticket
- Argo CD entegrasyonu
- Prometheus Alertmanager entegrasyonu
- Grafana dashboard
- Postmortem generator
- RCA history
- Multi-cluster support

1. Sonuç

- Bu proje portföy için güçlüdür.
- DevOps + Kubernetes + AI + Observability + CI/CD konularını birleştirir.
- Gerçek dünyaya yakın bir AI DevOps projesidir.

Makale içinde kod blokları, komutlar ve açıklamalar olsun.
Ama çok uzun kodların tamamını makaleye koyma.
Repository’ye yönlendiren açıklamalar yaz.

==================================================
11. TEST SENARYOLARI
==================================================

Unit test yaz:

- Secret masking doğru çalışıyor mu?
- CrashLoopBackOff rule doğru finding üretiyor mu?
- ImagePullBackOff rule doğru finding üretiyor mu?
- OOMKilled rule doğru finding üretiyor mu?
- Fingerprint aynı problemde aynı geliyor mu?
- AI prompt içinde secret sızmıyor mu?

Integration test mantığı:

- Demo manifestleri deploy edilir.
- Agent scan yapar.
- /api/findings endpointinden problem listesi alınır.
- En az 3 problem varsa test başarılı olur.

==================================================
12. KUBERNETES RBAC
==================================================

Agent read-only yetkiyle çalışmalı.

İzinler:
pods: get, list, watch
pods/log: get, list
events: get, list, watch
services: get, list, watch
endpoints: get, list, watch
ingresses: get, list, watch
configmaps: get, list
namespaces: get, list
nodes: get, list

Secret okumayı varsayılan olarak verme.
Secret içeriği AI’ye gönderilmemeli.
Eğer Secret var/yok kontrolü gerekiyorsa sadece metadata kontrolü yapılabilir.
README’de bu güvenlik yaklaşımını açıkla.

==================================================
13. DASHBOARD TASARIMI
==================================================

Dashboard profesyonel görünsün.

Kartlar:

- Total Findings
- Critical
- High
- Medium
- Low
- AI Requests
- Last Scan Time

Tablo:
Columns:

- Severity
- Namespace
- Resource
- Problem Type
- Root Cause
- AI Used
- Last Seen
- Action

Detay paneli:

- Summary
- Evidence
- Local Analysis
- AI Analysis
- Recommended Actions
- Commands to Verify
- Prevention

CSS temiz ve modern olsun.
Responsive tasarım olsun.

==================================================
14. AI PROMPT TASARIMI
==================================================

AI’ye gönderilecek system prompt şöyle güçlü olmalı:

You are a senior Kubernetes SRE and DevOps troubleshooting assistant.
Your job is to analyze Kubernetes evidence and help junior DevOps engineers understand the likely root cause.
Do not invent facts.
Use only the provided evidence.
If evidence is not enough, say what additional command should be checked.
Return valid JSON.
Do not include secrets.
Be practical, concise, and production-oriented.

User prompt içinde şu bilgiler olsun:

- Namespace
- Pod
- Container
- Status
- Restart count
- Events
- Logs
- Service/Endpoint/Ingress details
- Local rule result
- Question:
  - What happened?
  - Why did it happen?
  - How can we verify?
  - How can we fix?
  - How can we prevent it?

AI cevabı JSON dönmeli.

==================================================
15. GITHUB SECRETS
==================================================

README ve workflow içinde şu secrets açıklansın:

AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
PIONEER_API_KEY
PIONEER_MODEL

Opsiyonel:
ECR_REPOSITORY
CLUSTER_NAME

==================================================
16. EKS MALİYET KONTROLÜ
==================================================

EKS test ortamı küçük olmalı.
README’de maliyet uyarısı yaz.

Öneri:

- Tek küçük node group
- t3.small veya t3.medium gibi küçük instance
- Minimum node sayısı 1
- Test bitince destroy workflow çalıştır
- NAT Gateway kullanımı maliyetli olabilir, mümkünse public subnet test yaklaşımı açıkla
- Uzun süre açık bırakma

Destroy workflow mutlaka olsun.

==================================================
17. KOD KALİTESİ
==================================================

Kod:

- Temiz
- Modüler
- Type hints içeren
- Hata yönetimi olan
- Loglama yapan
- Test edilebilir
- Gereksiz karmaşık olmayan
- Junior DevOps mühendisinin okuyabileceği kadar anlaşılır
- Production mantığına yakın

Logging:

- Structured log kullan
- API key loglama
- Secret loglama
- AI request body içindeki sensitive alanları loglama

==================================================
18. ÇIKTI FORMATI
==================================================

Bana çıktıyı şu şekilde ver:

1. Önce kısa mimari özeti
2. Sonra repository tree
3. Sonra her dosyayı tek tek üret

Her dosya için format:

### File: path/to/file

```language
dosya içeriği
Hiçbir dosyayı “buraya gelecek” diye boş bırakma.
Eksik dosya bırakma.
Workflow YAML dosyalarını tam yaz.
Kubernetes manifestlerini tam yaz.
Python kodlarını tam yaz.
README ve Medium makalesini tam yaz.

Eğer tek cevapta çok uzun olursa parçalar halinde devam et.
Ama her parçada kaldığın yerden devam et.
Aynı dosyayı tekrar üretme.
