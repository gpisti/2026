# Projekt Dokumentáció: "Nagyágyú" Politikai Elemző Rendszer
**Verzió:** 1.0
**Dátum:** 2025. október 30.
**Státusz:** Tervezés lezárva, implementációra kész

## 1. 📜 Projekt Áttekintés és Célkitűzések

### 1.1. Projekt Célja (Executive Summary)

A "Nagyágyú" projekt célja egy olyan adatalapú, gépi tanulási rendszer felépítése, amely képes megbecsülni és előrejelezni a 2026-os magyarországi választások kimenetelét, kiemelt fókusszal **Orbán Viktor (OV)** és **Magyar Péter (MP)** politikai dinamikájára.

A rendszer nem csupán a média-megjelenések volumenét méri, hanem egy hibrid modellt alkalmaz, amely a **valós közvéleménykutatási (KK) adatokat** (mint "baseline") kombinálja a **súlyozott média-momentummal** (mint "iránytű"), hogy valósághű és torzításoktól megtisztított predikciót adjon.

### 1.2. A Probléma: Torzítás és Propaganda

Egy egyszerű "cikk-számláló" vagy "sentiment elemző" modell azonnal megbukna a magyar médiakörnyezetben. A predikciós modellnek aktívan kezelnie kell a következő kritikus torzítási faktorokat:

1.  **"Szőnyegbombázás" (Astroturfing):** A propaganda-hálózatok képesek ugyanazt a narratívát (pl. "tisza adó") több tucat portálon egyidejűleg leközölni, mesterségesen felnagyítva annak "hangerősségét".
2.  **Hitelességi Aszimmetria:** Egy alacsony elérésű, magas torzítású portálról érkező hír nem bírhat azonos súllyal, mint egy magas hitelességű, független portálról érkező.
3.  **Irónia és Szarkazmus:** A politikai nyelv tele van rejtett hangulattal, ami a hagyományos NLP modelleket megtéveszti.

### 1.3. A Megoldás: A Hibrid Modell

A rendszerünk egy kétpilléres hibrid modellen alapul:

1.  **A "Szent Grál" (Baseline Modell):** Ez a modell a valós, publikus **közvéleménykutatási adatokat** (`Polls` tábla) használja. Ez adja a rendszer "valósághoz kötött" alapját, megmutatva a fix szavazói bázisok és a bizonytalanok arányát.
2.  **Az "Iránytű" (Média Momentum Modell):** Ez a mi teljes adat-pipeline-unk, ami a `DailyAggregates` táblába dolgozik. Azt méri, hogy az elmúlt 24 óra **súlyozott és torzítás-kezelt** médiaeseményei merre és milyen mértékben mozdítják el a bizonytalan szavazókat.

A végső predikció a **Baseline (KK adat) + Momentum (Média-analízis)** összege, amelyet a rendszer minden új KK adat publikálásakor **automatikusan újrakalibrál**.

---

## 2. 🏛️ Rendszer Architektúra

A rendszer egy **skálázható, robusztus és hibatűrő mikroszolgáltatási architektúrára** épül, amelynek komponensei egy központi `Job Queue`-n (üzenetsoron) keresztül kommunikálnak. Ez lehetővé teszi, hogy az egyes feladatok (scrapelés, feldolgozás) egymástól függetlenül fussanak és skálázódjanak.



### 2.1. Fő Komponensek

1.  **Backend Services (Workers):**
    * **`Scheduler`:** Időzítő, ami a feladatokat (scrapelés, aggregálás) a `Job Queue`-ba helyezi.
    * **`Scraper Service`:** Felelős az RSS feedek olvasásáért, a cikkek letöltéséért és a `RawArticles` táblába írásáért.
    * **`Processing Service`:** A rendszer "agya". NLP modelleket futtat a nyers szövegen, elvégzi a sentiment analízist, irónia-detekciót, narratíva-klaszterezést, és beírja az eredményt a `ProcessedArticles` táblába.
    * **`Aggregator Service`:** A rendszer "szíve". Naponta lefut, kiszámolja a `cbi_score`-t (Torzítási Index), súlyozza a napi cikkeket, aggregálja az adatokat a `DailyAggregates` táblába, és futtatja a Hibrid Predikciós Modellt (összevetve a `Polls` és a `DailyAggregates` adatokat).
2.  **Data & Messaging Layer:**
    * **`PostgreSQL DB`:** A rendszer fő adattárolója (lásd 3. fejezet).
    * **`Job Queue (RabbitMQ / Redis)`:** Az üzenetsor, ami a mikroszolgáltatások közötti kommunikációt és feladatkiosztást kezeli.
3.  **Presentation & API Layer:**
    * **`Web/API Service`:** Kiszolgálja a dashboardot a felhasználó felé, és egy API-t biztosít a `DailyAggregates` tábla adatainak lekérdezéséhez.
4.  **External Systems:**
    * **`User`:** A dashboardot megtekintő felhasználó.
    * **`Internet (RSS Feeds)`:** A nyers adatforrás.
    * **`LLM API`:** A napi aggregált adatokból szöveges elemzést generáló külső szolgáltatás.

---

## 3. 🗄️ Adatbázis Séma (ERD)

Az adatbázis 6 fő táblára épül, amelyek szétválasztják az OLTP (adatgyűjtés) és az OLAP (elemzés) feladatokat.



1.  **`Portals`:**
    * **Cél:** Az adatforrások (hírportálok) listája.
    * **Kulcs Mező:** `cbi_score` (Calculated Bias Index) – A `Aggregator Service` által hetente frissített, számított torzítási/hitelességi pontszám, ami a súlyozáshoz kell.
2.  **`RawArticles`:**
    * **Cél:** A beérkező, feldolgozatlan cikkek "visszavonhatatlan logja".
    * **Kulcs Mező:** `raw_article_text` (a nyers, teljes szöveg).
3.  **`ProcessedArticles`:**
    * **Cél:** A "dúsított" adattábla, a `Processing Service` kimenete.
    * **Kulcs Mezők:**
        * `sentiment_ov`, `sentiment_mp`: A szereplőkre vonatkozó hangulat.
        * **`sentiment_confidence_score`:** Az NLP modell magabiztossága (az irónia-szűréshez).
        * **`narrative_hash`:** A cikk tartalmának "ujjlenyomata" (a "szőnyegbombázás" szűréséhez).
4.  **`Polls`:**
    * **Cél:** A "Szent Grál". A valós közvéleménykutatási adatok tárolója, a Baseline Modell alapja. Manuálisan vagy célzott scraperrel töltendő.
5.  **`DailyAggregates`:**
    * **Cél:** Az OLAP tábla, a dashboard motorja. Napi egy sor, ami mindent összefoglal.
    * **Kulcs Mezők:** `share_of_voice_ov`, `avg_sentiment_ov` (már súlyozva a `cbi_score`-ral), `sentiment_std_dev_mp` (polarizáció mérése), `topic_distribution_json`.
6.  **`DailyPortalAggregates`:**
    * **Cél:** "Drill-down" tábla a dashboard "Nagyító" funkciójához. Napi bontás portálonként.

---

## 4. 🧠 Adatfolyam és Torzításkezelési Logika

Az adat útja a nyers hírtől a súlyozott predikcióig:

1.  **Gyűjtés (Ingestion):** A `Scheduler` indítja a `Scraper`-t, ami a `Portals` táblából olvas, letölti a cikkeket, és beírja azokat a `RawArticles` táblába. Ezután üzenetet küld a `Queue`-ba ("Új cikk érkezett").
2.  **Feldolgozás (Processing):** A `Processing Service` felveszi az üzenetet, kiolvassa a cikket a `RawArticles`-ból, és elvégzi az NLP elemzést:
    * Kiszámolja a `sentiment_ov` és `sentiment_mp` értékeket.
    * Kiszámolja a **`sentiment_confidence_score`**-t (ha alacsony, a modell iróniát sejt).
    * Kiszámolja a **`narrative_hash`**-t (pl. SimHash segítségével), hogy azonosítsa a duplikált narratívákat.
    * Az eredményt a `ProcessedArticles` táblába menti.
3.  **Aggregálás és Súlyozás (Aggregation):** A `Scheduler` naponta egyszer indítja az `Aggregator`-t:
    * **Torzítás Számítás:** Először frissíti a `Portals` tábla `cbi_score` mezőit az elmúlt X nap adatai alapján.
    * **Súlyozott Aggregálás:** Végigolvassa az aznapi `ProcessedArticles` adatokat. A napi átlag sentiment (`avg_sentiment_ov`) számításakor minden cikk sentimentjét **súlyozza** a cikk portáljának `cbi_score`-ával.
    * **Narratíva Számolás:** A `narrative_hash` alapján megszámolja az *egyedi narratívák* számát (nem az összes cikket).
    * Az eredményt a `DailyAggregates` táblába menti.
4.  **Predikció és Kalibrálás (Prediction):**
    * Az `Aggregator` beolvassa a legfrissebb adatot a `Polls` táblából (ez a **Baseline**).
    * Beolvassa a frissen számolt `DailyAggregates` adatot (ez a **Momentum**).
    * A kettő kombinációjából kiszámítja a napi predikciót, amit szintén a `DailyAggregates`-be (vagy egy külön predikciós táblába) ment.

---

## 5. 🖥️ Vizuualizációs Terv (Dashboard Kijelzők)

A `Web/API Service` a `DailyAggregates` és `DailyPortalAggregates` táblákból építi fel a dashboardot, amely a következő kulcsfontosságú kijelzőket tartalmazza:

1.  **A "Kötélhúzás-mérő" (Fő Predikció):** A Hibrid Modell aktuális állását mutató fő kijelző (pl. OV: 48% vs. MP: 52%).
2.  **Az LLM "Napi Elemző":** Az `Aggregator` által az LLM API-nak küldött adatok alapján generált, 150 szavas semleges szöveges összefoglaló a nap trendjeiről.
3.  **Sentiment Vonaldiagram ("Pulzusmérő"):** A két szereplő súlyozott átlag-sentimentjének alakulása az időben.
4.  **Interaktív Téma-Sentiment Hőtérkép:** Megmutatja, hogy az egyes témákban (pl. 'gazdaság') mekkora volt a hangerő és milyen volt a sentiment (pl. 'gazdaság' = nagy piros téglalap MP-nél).
5.  **Média-Terjeszkedés Térkép:** A `DailyPortalAggregates` alapján megmutatja, mely portál-típusokon (buborékokon) tudott "áttörni" egy adott narratíva.

---

## 6. 📋 Implementációs Fázisok (Epics)

A projekt végrehajtása a "Project Board"-on definiált 5 fő fázisban (Epic) történik:

1.  **Epic 1: Foundation (Alapozás):** Az adatbázis séma létrehozása, a `Job Queue` beállítása és a mikroszolgáltatások vázának (skeleton) felállítása.
2.  **Epic 2: Data Ingestion (Adatgyűjtés):** A `Scheduler` és a `Scraper Service` teljes funkcionalitásának megépítése.
3.  **Epic 3: Processing (Feldolgozás):** Az NLP pipeline, a sentiment analízis, az irónia-szűrő és a narratíva-klaszterező implementálása.
4.  **Epic 4: The "Nagyágyú" (Aggregálás):** A `cbi_score` súlyozás, a Hibrid (KK+Média) Modell logikájának és az LLM kapcsolatnak a megírása.
5.  **Epic 5: Showcase (Dashboard):** A `Web/API Service` és a vizualizációs réteg (Dashboard kijelzők) felépítése.