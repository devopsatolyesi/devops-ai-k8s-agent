# 🎥 YouTube Video Paketi — AI Kubernetes Troubleshooting Agent

Bu klasör, projeyi anlatan YouTube videosunu çekmek için ihtiyacın olan her şeyi içerir.

## 📁 İçindekiler

| Dosya | Ne işe yarar |
|-------|--------------|
| **AI-K8s-Agent-Sunum.pptx** | 24 slaytlık PowerPoint sunumu (marka renkleriyle, koyu tema) |
| **01-Konusma-Notlari.md** | Slayt-slayt anlatım metni + zamanlama (sunum bölümü) |
| **02-Demo-Runbook.md** | Canlı demo senaryosu: komutlar, ekranlar, replikler |
| `build_deck.py` | Sunumu üreten script (slayt düzenlemek istersen) |

## ▶️ Önerilen Akış

1. **Sunum (~14 dk)** → `AI-K8s-Agent-Sunum.pptx` + `01-Konusma-Notlari.md`
   - Slayt 1–18: AI/DevOps bağlamı, mimari, iki katmanlı zeka, güvenlik, Kind, kod
2. **Canlı Demo (~10 dk)** → `02-Demo-Runbook.md`
   - Slayt 19'da terminale geç, runbook'u takip et, bitince Slayt 20'ye dön
3. **Kapanış (~2 dk)** → Slayt 20–24: senaryolar, genişletme, repo + Medium, CTA

Toplam hedef süre: **~26 dakika**.

## 🎨 Sunumu Düzenlemek İstersen

```bash
# Slayt metnini build_deck.py içinde değiştir, sonra yeniden üret:
../.pptx-venv/bin/python build_deck.py
```

## ⚠️ Senin Doldurman Gerekenler

Sunumda placeholder bıraktığım yerler (Find & Replace ile değiştir):
- `github.com/<repo>` → gerçek repo linkin
- Medium makale linki → gerçek link (şu an "açıklamada" yazıyor)
- Slayt 1 ve 24'teki repo/sosyal bilgiler

## 📝 Medium Makalesi

Konuşma notları ve sunum, Medium makaleni **referans veriyor** ama içeriğini henüz işlemedim
(taslak linki dışarıdan okunamıyor). Makaleyi bana **kopyala-yapıştır** ya da **PDF** olarak
verirsen:
- Sunuma makaleye özel 1–2 slayt ekleyebilirim (örn. "Makalede daha derin: …")
- Konuşma notlarına makaledeki spesifik tasarım kararlarını/anekdotları serpiştirebilirim
- Demo'da makaledeki ekran görüntüleriyle bağ kurabilirim

## 🧹 Not

`../.pptx-venv/` klasörü sunumu üretmek için kuruldu — repoya commit etmene gerek yok
(istersen `.gitignore`'a ekle ya da sil: `rm -rf ../.pptx-venv`).
