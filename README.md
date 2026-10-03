# Aktien-Radar

Regelbasierter Signal-Bot für US-Aktien, DE/EU-Aktien, ETFs und Krypto. Jede Stunde berechnet er, welche Werte
sich kurzfristig und langfristig lohnen könnten, begründet das in Klartext und prüft die Strategien dahinter per
Backtest.

**Live-Seite: https://maximilianfeix.github.io/aktien-radar/**

> Keine Anlageberatung. Der Bot handelt nicht selbst und kennt keine Garantie auf Gewinne. Vergangene Renditen
> sagen die Zukunft nicht voraus; Verluste bis zum Totalverlust sind möglich.

## Was die Seite kann

- **Top-Kandidaten** kurzfristig und langfristig, jeweils mit Einstieg, Stop, Kursziel und Begründung
- **Kernstrategie**: was die im Backtest beste Strategie gerade hält
- **Heatmap** aller Werte über 1 Tag, 1 Monat und 12 Monate
- **Signalwechsel**: was hoch- oder herabgestuft wurde, auch als Feed (`feed.xml`)
- **Live-Bilanz**: jedes Kaufsignal wird ab Erscheinen mitgeschrieben und beim Ende abgerechnet
- **Detailansicht** je Wert: Kurschart mit 50- und 200-Tage-Linie, Kennzahlen, Quartalstermin, Rechner für die Positionsgröße
- **Watchlist und Depot**: eigene Positionen eintragen und das Urteil des Radars dazu sehen (bleibt im Browser)
- **Backtests** mit getrennter Auswertung bis 2020 und seit 2021
- Suche mit `/`, heller und dunkler Modus, CSV-Export, offline nutzbar und als App installierbar

## Was jede Stunde passiert

1. GitHub Actions lädt Tageskurse für rund 150 Werte von Yahoo Finance (ein Lauf rechnet etwa 5 Sekunden).
2. Jeder Wert bekommt einen Kurzfrist- und einen Langfrist-Score samt Signal (Kaufen, Halten, Beobachten, Meiden).
3. Signalwechsel und Live-Bilanz werden fortgeschrieben, die Backtests neu gerechnet.
4. Das Ergebnis geht als Pull Request ins Repo und wird automatisch gemergt.
5. Der Tagesbericht (Issue mit Label `tagesbericht`) wird aktualisiert; ändern sich die Top-Kandidaten, kommt ein Kommentar dazu.
6. Die GitHub Page wird neu veröffentlicht.

## Benachrichtigungen (optional)

Ändern sich die Top-Kandidaten, schickt der Lauf eine Nachricht an jeden Kanal, dessen Secret gesetzt ist:

| Kanal | Secrets |
|---|---|
| Handy-Push über [ntfy](https://ntfy.sh) (ohne Konto) | `NTFY_TOPIC` |
| Discord | `DISCORD_WEBHOOK_URL` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

```bash
gh secret set NTFY_TOPIC --repo maximilianfeix/aktien-radar   # frei gewählter, schwer zu erratender Name
```

Zusätzlich: Repo beobachten (Watch → Custom → Issues) für Mails zum Tagesbericht, oder `feed.xml` im Feedreader abonnieren.

## Die Strategie

Die Recherche hat die bekannten Open-Source-Bots (Freqtrade, Hummingbot, NautilusTrader, LEAN, Qlib, FinRL,
TradingAgents, ai-hedge-fund) und die am besten belegten Regeln aus der Finanzforschung verglichen. Das Ergebnis in
Kurzform:

| Ansatz | Befund | Im Radar |
|---|---|---|
| Momentum (12 Monate ohne den letzten) | Über Jahrzehnte und Märkte hinweg belegt, auch nach Veröffentlichung | Kern des Langfrist-Scores |
| Trendfilter (200-Tage-Linie) | Senkt vor allem die großen Rückgänge, kostet in Bullenmärkten Rendite | Bedingung für jedes Kaufsignal |
| Absolutes Momentum (Dual Momentum) | Verhindert, den „Besten unter Verlierern" zu kaufen | Bedingung für Langfrist-Käufe |
| Gewichtung nach Schwankung | Glättet Momentum-Strategien und dämpft Crashs | In allen Rotationsstrategien |
| Rücksetzer im Aufwärtstrend (RSI 2) | Kleine, aber stabile kurzfristige Prämie; kostenempfindlich | Teil des Kurzfrist-Scores |
| Qualität und Bewertung | Sinnvoll als Ergänzung, kostenlos nicht sauber rückrechenbar | 20 % des Langfrist-Scores bei Aktien, nicht im Backtest |
| Market Making, Arbitrage, Hochfrequenz | Für Privatanleger ohne Infrastruktur nicht gewinnbar | Nicht umgesetzt |
| LLM-Agenten als Entscheider | Ergebnisse schwanken stark, kein Modell gewinnt verlässlich | Claude erklärt nur, entscheidet nicht |

Wichtigster Grund, warum private Bots Geld verlieren: Strategien werden so lange an alte Daten angepasst, bis der
Backtest gut aussieht. Deshalb gilt hier:

- Alle Parameter stammen aus der Literatur und wurden nicht auf diese Daten optimiert.
- Jeder Backtest zeigt die Zeit bis 2020 und die Zeit seit 2021 getrennt.
- Handelskosten sind eingerechnet (0,1 % je Umschichtung, Krypto 0,2 %).
- Ein Signal vom Tagesschluss wirkt erst am nächsten Tag.
- Renditen sind in Handelswährung gerechnet; Wechselkurseffekte (etwa USD/EUR) fehlen.
- Tests auf der heutigen Aktien- und Coin-Auswahl sind als geschönt markiert (Survivorship Bias) und werden bei der
  Wahl der Kernstrategie ignoriert.

Die aktuellen Zahlen stehen auf der Live-Seite. Sie zeigen auch die unbequeme Seite: Kaufen und Halten eines
S&P-500-ETFs war in den letzten zehn Jahren bei der reinen Rendite schwer zu schlagen. Die Trendstrategien liefern
weniger Rendite, aber deutlich kleinere Rückgänge.

## Lokal ausführen

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m radar run           # schreibt docs/data/*.json und out/report.md
python -m http.server 8000 --directory docs
pytest && ruff check .
```

## KI-Kommentar (optional)

Liegt im Repo das Secret `ANTHROPIC_API_KEY`, schreibt Claude zu jedem Lauf mit geänderten Top-Kandidaten eine kurze
Einordnung. Die Zahlen kommen immer aus den Regeln; Claude erklärt sie nur. Das Modell lässt sich über die
Repo-Variable `RADAR_MODEL` ändern (Standard: `claude-opus-5-5`).

```bash
gh secret set ANTHROPIC_API_KEY --repo maximilianfeix/aktien-radar
```

## Aufbau

| Pfad | Inhalt |
|---|---|
| `radar/universe.json` | Beobachtete Werte je Markt |
| `radar/scoring.py` | Scores, Signale, Begründungen |
| `radar/backtest.py` | Strategien und Kennzahlen |
| `radar/regime.py` | Marktlage (Risk-on / Risk-off) |
| `radar/fundamentals.py` | Qualität und Bewertung, 24 Stunden zwischengespeichert |
| `radar/track.py` | Signalwechsel, Live-Bilanz, Feed |
| `radar/notify.py` | Push-Nachrichten |
| `radar/ai.py` | Optionaler Claude-Kommentar |
| `docs/` | GitHub Page und die erzeugten Daten |
| `.github/workflows/radar.yml` | Stündlicher Lauf |
