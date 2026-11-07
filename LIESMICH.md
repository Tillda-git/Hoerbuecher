# Hörbücher-App fürs iPhone

Eine eigene Hörbuch-App ohne Abo und ohne Werbung. Sie zeigt nur **vollständige**, legal und kostenlos anhörbare deutsche Hörbücher und Hörspiele, geordnet nach Kategorien.

| Quelle | Inhalt | In der App |
|---|---|---|
| LibriVox | gemeinfreie Klassiker, von Freiwilligen gelesen | abspielbar |
| ARD Sounds (früher Audiothek) | Lesungen, Hörspiele, Hörspielreihen wie „Kein Mucks!“, „Radio Tatort“, „Das Portal“ | abspielbar |
| Deutschlandfunk | Hörspiel, Kriminalhörspiel, Professor van Dusen, Kakadu-Kinderhörspiel | abspielbar |
| Vorleser.net | Klassiker mit professionellen Sprechern | nur verlinkt* |
| Ohrka | Kinderhörbücher mit bekannten Stimmen | nur verlinkt* |

\* Diese Anbieter erlauben keine öffentliche Wiedergabe im Internet. Die Titel erscheinen deshalb im Katalog, ein Tipp öffnet sie aber auf der Seite des Anbieters. Mit „Nur in der App abspielbar“ blendest du sie aus. Bei Mehrteilern („1/3, 2/3, 3/3“) wird ein Titel nur aufgenommen, wenn alle Teile verfügbar sind.

**Funktionen:** Entdecken nach Kategorien und Zufallsfunden · Suche · Bibliothek (Weiterhören, Gemerkt, Gehört) · setzt automatisch an der letzten Stelle fort · Tempo 0,8× bis 2× · Schlaf-Timer (auch „Ende des Kapitels“) · 15 s zurück / 30 s vor · Bedienung vom Sperrbildschirm und mit AirPods · Dunkelmodus.

Die App wohnt kostenlos bei GitHub. Dort sucht ein automatischer Job jeden Montag neue Hörbücher und sortiert Titel aus, die nicht mehr vollständig sind. Dein PC muss dafür nicht laufen.

---

## Einrichtung (einmalig, ca. 15 Minuten, am PC)

**1. GitHub-Konto anlegen**
Auf <https://github.com> auf „Sign up“ klicken. Das Konto ist kostenlos.

**2. Neues Repository anlegen**
Oben rechts auf **+** und dann **New repository** klicken.
- Repository name: `hoerbuecher`
- **Public** auswählen (GitHub Pages ist nur für öffentliche Repositories kostenlos. Hochgeladen wird nur der Katalog, keine persönlichen Daten.)
- Auf **Create repository** klicken.

**3. Dateien hochladen**
Auf der neuen, leeren Seite den Link **„uploading an existing file“** anklicken. Die ZIP-Datei entpacken und **den gesamten Inhalt** (Ordner `docs`, Ordner `.github`, Datei `hoerbuch_finder.py`, diese Anleitung) in das Browserfenster ziehen. Am besten mit Chrome oder Edge. Dann auf **Commit changes** klicken.

> Prüfen: In der Dateiliste muss der Ordner `.github` auftauchen. Falls nicht: **Add file** und dann **Create new file** wählen, als Namen `.github/workflows/katalog.yml` eintippen, den Inhalt der gleichnamigen Datei aus der ZIP einfügen und speichern.

**4. Schreibrechte für den Automatik-Job erlauben**
Zu **Settings**, dann **Actions**, dann **General** gehen. Ganz unten unter „Workflow permissions“ **Read and write permissions** auswählen und auf **Save** klicken.

**5. Ersten Suchlauf starten**
Auf den Reiter **Actions** gehen, links **Katalog aktualisieren** wählen, rechts auf **Run workflow** und dann auf den grünen Knopf klicken. Der erste Lauf dauert ca. 15–40 Minuten. Er ist fertig, wenn ein grüner Haken erscheint.

**6. Webseite einschalten**
Zu **Settings** und dann **Pages** gehen. Unter „Branch“ **main** und als Ordner **/docs** wählen und auf **Save** klicken. Nach 1–2 Minuten steht oben die Adresse, z. B.
`https://DEIN-NAME.github.io/hoerbuecher/`

## Aufs iPhone holen

1. Die Adresse in **Safari** öffnen. Es muss Safari sein, nicht Chrome.
2. Auf das **Teilen-Symbol** tippen (das Quadrat mit dem Pfeil nach oben).
3. Nach unten scrollen, **„Zum Home-Bildschirm“** wählen und auf **Hinzufügen** tippen.

Danach startet die App über ihr Icon wie jede andere App.

---


## Podcasts

Im Reiter **Podcasts** findest du die neuesten Folgen deiner Podcasts und für jeden Podcast eine eigene Seite. Die App merkt sich, wie weit du gehört hast. Neue Folgen holt GitHub **alle 2 Stunden** ab.

**Welche Podcasts?** Das steht in der Datei `podcasts.txt` im Repository, eine Zeile pro Podcast. Dort kann der Name stehen (wie in Apple Podcasts) oder die Feed-Adresse.

**Podcast hinzufügen:** In der App unter Podcasts auf **＋ Podcast hinzufügen** tippen, suchen und **Hinzufügen** wählen. Die App kopiert die Adresse und öffnet `podcasts.txt` bei GitHub. Dort fügst du die Adresse als neue Zeile ein und tippst auf **Commit changes**. Nach ein paar Minuten ist der Podcast da. Dafür musst du in Safari bei GitHub angemeldet sein.

**Podcast entfernen:** Die Zeile in `podcasts.txt` löschen. Wenn du ihn nur auf dem iPhone nicht sehen willst, wählst du auf der Podcastseite **Ausblenden**.

## Gut zu wissen

- **Internet nötig:** Die Hörbücher werden gestreamt, nicht heruntergeladen. Das sind je nach Quelle etwa 30–60 MB pro Stunde. Für lange Fahrten ohne Netz bietet LibriVox auf der Buchseite („Bei LibriVox ansehen“) ZIP-Downloads an.
- **Fortschritt** wird nur auf diesem iPhone gespeichert. Wenn du Safari-Websitedaten löschst, ist er weg.
- **Erster Start einer Wiedergabe:** iOS verlangt, dass Ton durch Tippen gestartet wird. Falls ein Kapitel nicht sofort losläuft, einfach noch einmal auf Play tippen.
- **ARD Audiothek:** Die ARD-Schnittstelle ist inoffiziell und kann sich ändern. Außerdem laufen die GitHub-Server in den USA. Sollten dort keine ARD-Titel abrufbar sein, zeigt das Protokoll unter *Actions* einen Hinweis. Die App enthält dann vorerst nur LibriVox. Die ARD nimmt Sendungen nach einiger Zeit offline. Unvollständige Titel verschwinden beim nächsten Montagslauf automatisch.
- **Sofort aktualisieren:** Unter *Actions*, *Katalog aktualisieren* und *Run workflow* jederzeit möglich.
- **Am PC nutzen:** Die Adresse funktioniert auch in jedem Browser am PC. Die lokale Variante läuft weiterhin mit `python hoerbuch_finder.py`.

## Anpassen

Kategorien, Mindestlängen, ARD-Suchbegriffe, ARD-Hörspielreihen und Podcast-Feeds stehen oben in `hoerbuch_finder.py` (`CONFIG`, `CATEGORY_RULES`). Weitere Podcast-Feeds trägst du einfach in die Liste `feeds` ein. Bearbeiten geht direkt auf GitHub über das Stift-Symbol. Beim nächsten Lauf gilt die neue Einstellung.
