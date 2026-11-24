# Freya · Sesli kart teslimat asistanı

Freya FDE assignment’ının ana görevi, ilk teslimat denemesinde ulaşılamayan sekiz müşteriye Türkçe sesli görüşmeyle yeni bir kart teslimat randevusu ayarlamaktır. Asistan görüşme izni alır, doğum tarihini banka servisiyle doğrular ve müşterinin ilçesindeki uygun kurye aralıklarını sunar. Müşteri seçtiği aralığı ayrıca onaylayınca randevu SQLite’a yazılır.

Görüşme konsolu macOS’ta çalışır. Giden arama yerel olarak simüle edilir. Konsoldan mikrofonla görüşebilir, her turun işlem izini ve kurye kapasitesindeki değişimi inceleyebilirsin.

## Kart teslimat asistanını çalıştırma

Proje klasöründe:

```sh
./run.sh
```

[Yerel konsolu aç](http://127.0.0.1:8765). Tarayıcıya mikrofon izni ver. Projede bulunan sanal ortam ve modeller kullanılır. Başlatıcı gerekirse yerel Ollama’yı açar. Panel tek FastAPI worker ile çalışır. Durdurmak için çalıştırdığın terminalde Ctrl+C kullan.

### İlk görüşme

1. Ayşe Kaya’yı seç ve **Görüşmeyi başlat** düğmesine bas. Açılış sesini duyamazsan hoparlör düğmesiyle tekrar çal.
2. Her yanıtta **Mikrofonu başlat**, konuş, ardından **Kaydı bitir ve gönder** düğmesine bas. Görüşme izni için “Evet, uygunum” diyebilirsin.
3. Kimlik sorusunda vaka PDF’indeki seçili müşterinin doğum tarihini gün, ay adı ve yıl olarak söyle. İki başarısız doğrulamada görüşme kapanır. Testi hızlandırmak için **Doğum tarihini atla** düğmesini kullanabilirsin. Atlama işlem izine yazılır. Kimlik turunda müşterinin ses dökümü ve çıkarılan tarih demo ekranında görünür.
4. “Perşembe olmaz, cuma öğleden sonra uygunum” de. Konuşma balonuna tıklayıp Whisper metnini, Qwen’e gönderilen seçenekleri, modelin JSON çıktısını ve bankanın kontrolünü incele. Cuma satırında kalan yer aynı kalır ve **Geçici ayrım** sütununda **Teklifte** görünür.
5. Asistan cuma aralığını okuduğunda ayrı bir turda “Evet” de. Randevular sekmesindeki satırı ve kurye kapasitesindeki değişimi kontrol et.
6. Başka bir müşteriyle devam ederek doluluk durumunu test edebilirsin. **Veritabanını sıfırla** ile sekiz müşteri, beş aralık, yedi boş yer ve sıfır randevu geri gelir. Açık görüşmeler yeni bir görüşme olarak 0. turdan başlar. İptal edilen randevunun sonucu hâlâ panelde açıksa o panel de yeni görüşmeye geçer. Asistan önceki onayın geçersiz olduğunu ve kurye beklememek gerektiğini yazılı ve sesli bildirir.

**Görüşme geçmişi** düğmesiyle kapanmış bir görüşmeyi açabilirsin. Bir tura tıklayarak Whisper metnini, Qwen’in yorumunu, uygulanan kararı ve varsa farklı kararın gerekçesini görürsün. Metin ve işlem izleri SQLite’ta saklanır, uygulama yeniden başladığında da açılabilir. Veritabanı sıfırlanınca görüşme geçmişi silinir.

Teklif ayrımını denemek için iki bağımsız sekme aç. Birinci Kadıköy müşterisine perşembe için teyit sorulduğunda ikinci Kadıköy müşterisini doğrula. İkinci müşteriye cuma sunulur. Birinci görüşme onay almadan biterse perşembe yeniden teklif edilebilir. Aynı müşteriye açık bir görüşme varken ikinci görüşme girişimi reddedilir.

## Kaynak koddan kurulum

Apple Silicon macOS, Python 3.13, `ffmpeg`, `whisper-cli` (whisper.cpp) ve Ollama gerekir. Modeller ve çalışma verileri kaynak paketine dahil edilmez.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

| Bileşen | Varsayılan konum / ayar |
|---|---|
| Whisper | Varsayılan çok dilli model `.local/models/ggml-medium.bin`. Farklı bir model için `WHISPER_MODEL` ortam değişkenini kullan. İşlem izinde kullanılan model dosyası görünür. [whisper.cpp modelleri](https://huggingface.co/ggerganov/whisper.cpp) |
| Antalia | `tr_TR-antalia-medium.onnx` ve aynı dizinde `tr_TR-antalia-medium.onnx.json`. Varsayılan klasör `.local/models`. Alternatif ONNX yolu için `PIPER_MODEL`. [Ses modeli ve atıf bilgisi](https://huggingface.co/ozgurguler/piper-tr-antalia-medium) |
| Qwen | Ollama model adı `qwen3:4b`. Hazır bir Ollama servisi kullanıyorsan o serviste `ollama pull qwen3:4b` çalıştır. Başlatıcının açtığı servisin model deposu `.local/ollama` veya `OLLAMA_MODELS` ile seçilen klasördür. [Qwen3](https://ollama.com/library/qwen3) |
| Yerel servis | Panel `127.0.0.1:8765`, Ollama `127.0.0.1:11434`. Panel portu `FREYA_PORT`, SQLite yolu `FREYA_DB` ile değiştirilebilir. Varsayılan veri dosyası `.runtime/bank.sqlite`. |

## İşlem ve veri akışı

- `web/` tarayıcıda MediaRecorder ile ses alır ve asistanın WAV yanıtını çalar. Etkin turun olaylarını sıra numarasıyla sorgular. Sekme yenilendiğinde aynı sunucu sürecindeki görüşmeyi tekrar yükler.
- `freya/voice.py` sesi ffmpeg ile dönüştürür. Whisper Türkçe transkript üretir. Önce konuşma adımına uygun ifade kuralları denenir. Qwen düşünme modu kapalı çalışır. Eşleşme bulunamazsa yerel Qwen görüşme izni, zaman tercihi veya bekleyen randevunun onayı için yanıtı sınıflandırır. Piper/Antalia, banka sonucu ve konuşma durumu üzerinden hazırlanmış yanıtı seslendirir.
- `freya/engine.py` görüşme iznini, doğrulamayı, tercihi ve ayrı onay turunu yönetir. Açık ret veya belirsizlik ile model önerisi çelişirse işlem izinde gerekçe gösterilir. Koşullu onay yeni teklife döner. Yanıt anlaşılamazsa asistan aynı randevuyu tekrar teyit eder. Açık sıra tercihleri müşteriye okunan seçenek sırasına bağlanır. Qwen, bekleyen teklife verilen doğal onayları bağlamıyla yorumlar. Modelin onayı yazıya dökülen yanıttaki kabul ifadesiyle desteklenmiyorsa teklif korunur ve onay yeniden istenir. Bir soru veya gerçek bir koşul, model onay dese bile rezervasyona dönüşmez.
- `freya/bank.py` aynı müşteri için bir açık görüşmeye ve demo haftasında bir kesin randevuya izin verir. Teklif edilen saat aralığının tamamı bir görüşmeye ayrılır. Tercih alınınca diğer aralıklar serbest kalır. Teyit olumluysa ayrılan aralık için randevu yazılır. Ret, görüşmenin kapanması veya sıfırlama ilgili ayrımları kaldırır. Teklif ve onay kontrolleri kendi SQLite yazma işlemlerinde yapılır.
- `freya/app.py` tarayıcıya HTTP servislerini sunar. Sıfırlamada eski tur yeni görüşmeye yazamaz. Model veya ses üretimi beklenirken izleme istekleri yanıtlanabilir. Sunucu yeniden başlatıldığında randevular ve işlem izleri korunur, açık görüşmeler kapanır ve geçici ayrımlar kaldırılır.
- Demo operatörü sentetik müşterileri görebilir. Telefonlar maskelidir. Kimlik turunun transkripti demo işlem geçmişinde görünür. Tarih doğrudan ayrıştırılamazsa yerel Qwen transkriptten tarihi çıkarmayı dener. Banka servisi tarihi kayıtla birebir karşılaştırır. Giriş ses dosyaları geçicidir. Asistanın son ses yanıtı tekrar oynatmak için bellekte tutulur.

## Doğrulama

```sh
.venv/bin/python -m pytest -q
node --test tests/test_panel.mjs
PYTHONPATH=. .venv/bin/python -u tests/smoke_voice.py
```

İlk komut geçici SQLite dosyalarıyla servis, görüşme ve HTTP sınırlarını test eder. Node testleri panelin gerçek JavaScript kodunu taklit edilen tarayıcı ve HTTP yanıtlarıyla çalıştırır. Son komut çalışan yerel Ollama, ses modelleri ve macOS’un Türkçe Yelda sesiyle dört sesli HTTP turu yürütür. Yelda müşteriyi seslendirir. Girdi WebM/Opus ve parçalı MP4/AAC biçimlerine dönüşür. Asistan yanıtında gerçek Piper sesi üretilir. Bu test kendi geçici veritabanını açar.

| 24 Kasım 2026, Whisper small ile ilk yerel deneme | Gözlenen sonuç |
|---|---|
| Otomatik regresyonlar | 21 geçti. Son yere eşzamanlı iki istek, tekrar, koşullu onay, doğrulama sınırı, model ve ses hataları, yeniden başlatma ve üretim sürerken reset kapsandı. FastAPI test istemcisi geçiş uyarısı verdi. |
| Sentetik sesle tam görüşme | Dört tur sonunda Ayşe Kaya için Kadıköy cuma 14.00–18.00 aralığına bir kayıt yazıldı. Son onaydan önce randevu oluşmaması ayrı regresyon testinde doğrulandı. |
| Ses dosyasının tesliminden yanıt WAV’ının hazır olmasına kadar | Tur süreleri 0,96 / 0,83 / 2,69 / 0,76 sn. Dört turun medyanı yaklaşık 0,90 sn. Bu değer tarayıcıdan ilk sesin duyulma gecikmesini içermez. |
| Qwen doğrudan tercih sınıflandırması | 12 denemenin 9’u doğru. Dört ifadenin üçü 3/3 doğru. “Belki cuma gelebilirim, kesin değil” ifadesinde model 0/3 yaptı. Konuşma yöneticisinin belirsizlikte yeniden sorduğu ayrıca test edildi. |
| Tarayıcı kontrolü | Yerel ekran, başlangıç tabloları, açılış sesi üretimi ve oynatma olayları, reset onayı ve başlangıç sayılarına dönüş görüldü. Fiziksel mikrofonla insan konuşması ve ortam gürültüsü ayrıca denenmeli. |

### Denemelerde değişen kararlar

- İlk tercihler sayısal teklif kimlikleriyle modele sorulduğunda model iki günü de uygun işaretledi. Gün adlarına bağlı model çıktısı ve ayrı bir servis kimliği eşlemesi kullanıldı.
- Kısa ses kayıtlarına sessizlik payı eklendi. Gerçekçi ayrım için testte asistan ve müşteri farklı sentetik seslerle konuştu.
- Aynı tarih WAV’da “14 Mart 1988”, AAC denemesinde “14 March 1988” olarak çözüldü. Ay adı normalizasyonuna İngilizce karşılıklar eklendi. Karşılaştırma takvim tarihinin üç alanında kesin eşleşme gerektirir.

### Demo sınırları

Sistem tek bilgisayarda, tek demo haftasıyla çalışır. Kimlik turu vaka verisine dayalıdır. Gerçek bir banka için güvenilir dış arama doğrulaması, yetkilendirme, kayıt saklama politikası, telefon ve kurye bağlantısı, operasyon sorumluluğu ve gürültülü gerçek çağrılarla pilot gerekir. Kart sahibinin kapıda kimlik göstermesi zorunludur. Operasyon için ek kapasite talebi ertelenmiştir.

## Ek özellik · Freya Piyasa (Nasdaq takibi)

Assignment’ın açık sorusu için geliştirilen bu özellik, müşterinin seçtiği fiyat eşiğine ulaşıldığında bir piyasa özeti sunan sesli görüşmeyi simüle eder.

### Neden bu özellik eklendi?

ABD Merkez Bankası’nın Survey of Consumer Finances araştırmasına göre doğrudan veya dolaylı hisse yatırımı olan ABD’li ailelerin oranı 2019’da **%53**, 2022’de **%58** ve 2025’te **%56** idi. Katılım son ölçümde gerilese de 2019’un **3 yüzde puan** üzerinde kaldı. [Federal Reserve, 2019–2022](https://www.federalreserve.gov/publications/october-2023-changes-in-us-family-finances-from-2019-to-2022.htm) · [Federal Reserve, 2025 anketi](https://www.federalreserve.gov/newsevents/pressreleases/other20261009a.htm)

Freya’nın ürün hipotezi şu yönde. Yatırımla ilgilenen potansiyel müşteriler, takip ettikleri hisseler için kendi fiyat alarmını kurabildikleri ve eşik gerçekleştiğinde kısa bir Türkçe sesli özet alabildikleri bir bankayı denemeye daha istekli olabilir. Freya Piyasa bu nedenle yeni kullanıcıları çekebilecek bir özellik olarak eklendi. ABD verileri hisse yatırımına katılımı ölçüyor. Freya’nın müşteri kazanımına etkisi bu demoda ölçülmedi.

FINRA Investor Education Foundation’ın 2024 ABD yatırımcı anketinde katılımcıların **%29’u** yatırım kararlarında sosyal medya gruplarından veya forumlardan yararlandığını bildirdi. Bu oran 18–34 yaş grubunda **%60** idi. Proje açısından bu bulgu, bankanın müşteriye takip ettiği bir hisse hakkında fiyatı, eşik olayını ve mevcut analist görüşlerinin dağılımını tek bir kısa özette sunması için bir gerekçe sağlıyor. [FINRA Foundation araştırma notu, 2026](https://finrafoundation.org/sites/finrafoundation/files/2026-03/FINRA_Foundation_Research_Brief_Social_Media_Finfluencers.pdf)

Bank of England ve FCA’nın 2026 anketinde, ankete yanıt veren finansal kuruluşların **%86’sı** yapay zekâ kullandığını bildirdi. Küçük kuruluşlara yönelik kısa formu yanıtlayanların **%36’sı** müşteri hizmetlerini mevcut veya planlanan bir yapay zekâ kullanım alanı olarak gösterdi. Bu eğilim, kart teslimatında kullanılan sesli görüşme yaklaşımını müşteriye zamanında bilgi sunan ikinci bir banka senaryosunda denemeyi destekliyor. [Bank of England ve FCA, 2026](https://www.bankofengland.co.uk/report/2026/artificial-intelligence-in-uk-financial-services-2026)

Freya Piyasa’da izlenecek hisseyi, fiyatı ve eşik yönünü müşteri seçer. Sunucu eşiği kontrol eder, görüşme de tetiklenme nedenini ve mevcut piyasa verilerini sesli olarak aktarır. Böylece müşteri fiyatı sürekli kontrol etmeden seçtiği hareketten haberdar olabilir. Anketler özellik için bağlam sağlıyor. Sesli alarmın kaç müşteri tarafından tercih edileceği ve ne kadar zaman kazandıracağı bu demoda ölçülmedi.

### Freya Piyasa’yı çalıştırma

Piyasa sitesi kendi adresinde ve veritabanında çalışır. Görüşme sesi için Piper, Türkçe Antalia modelini ilk seslendirmede yükler. Banka konsolu ve Ollama gerekmez.

```sh
./run-nasdaq.sh
```

[Freya Piyasa’yı aç](http://127.0.0.1:8767). Bir hisse seçerek fiyat grafiğini ve analist dağılımını incele. **Alarm kur** ile USD hedefini ve yönünü seç. **Alarmı tetikle** düğmesiyle akışı hemen dene. **Çağrıyı başlat** açıldığında **Evet, müsaitim** yanıtını ver. Asistan alarm raporunu okur, **Başka sorunuz var mı?** diye sorar ve **Hayır** yanıtıyla iyi günler dileyip görüşmeyi bitirir. Simüle görüşmenin tüm asistan cümleleri Türkçe Antalia sesiyle üretilir. Tarayıcı otomatik oynatmayı engellerse **Sesi yeniden çal** düğmesine bas. Mikrofonla yanıt tarayıcı desteğine bağlıdır. Yanıt düğmeleri her tarayıcıda kullanılabilir.

| Ortam değişkeni | Kullanım |
|---|---|
| `MASSIVE_API_KEY` | Nasdaq kotasyonları, hacim sıralaması, fiyat geçmişi ve otomatik alarm için Massive erişim anahtarı. Hacim sıralaması için Full Market Snapshot erişimi gerekir. Anahtar yoksa site açıkça etiketlenen örnek veriyle ve elle tetiklemeyle açılır. |
| `FMP_API_KEY` | FMP Grades Summary erişim anahtarı. Güçlü Al/Al ve Güçlü Sat/Sat aynı grupta toplanır. |
| `NASDAQ_DATA_DELAY_MINUTES` | Veri planının gecikmesi. Varsayılan `15`. Anlık veriye yetkili plan için `0`. |
| `NASDAQ_POLL_SECONDS` | Sunucunun hedef kontrol sıklığı. Varsayılan `60` saniye. |
| `NASDAQ_DB` | Varsayılan `.runtime/nasdaq.sqlite`. Alarmlar aynı tarayıcı oturumuna bağlıdır ve sunucu yeniden başlasa da saklanır. |
| `NASDAQ_PORT`, `NASDAQ_BIND` | Varsayılan `8767` ve `127.0.0.1`. Harici dağıtımda HTTPS proxy kullanarak `NASDAQ_PUBLIC_HOST` değerini port belirtmeden alan adıyla ayarla. |

Liste Nasdaq ana kotasyonlu adi hisseleri ve depo sertifikalarını seans pay hacmine göre sıralar. Geçerli seans hacmi henüz oluşmamışsa son işlem seansı gösterilir. 1 günlük değişim önceki seans kapanışına, 1 hafta/ay/yıl değişimi ilgili takvim tarihindeki veya en yakın önceki işlem günündeki kapanışa göredir. Masaüstünde fareyle, klavyede sağ ve sol oklarla grafikte fiyat noktalarını inceleyebilirsin.

Canlı veri modunda site sunucusu açıkken, tarayıcı sekmesi kapalı olsa bile, alarm oluşturulduktan sonraki tamamlanmış dakika barlarını yönüne göre kontrol eder. Alarm tek sefer tetiklenir. Elle tetiklenen alarm görüşmede deneme olarak belirtilir. [Massive fiyat verisi](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot), [FMP analist verisi](https://site.financialmodelingprep.com/developer/docs/stable/grades-summary).
