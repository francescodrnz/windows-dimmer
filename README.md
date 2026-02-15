# 🌙 NightDimmer

Overlay click-through per Windows che riduce la luminosità percepita e aggiunge una tinta calda al monitor. Utile la sera su monitor che non scendono abbastanza di luminosità o per filtrare la luce blu senza alterare i profili colore del sistema.

Funziona applicando un layer semitrasparente (nero e/o rosso) davanti a tutto lo schermo. Il layer è **trasparente agli input**: puoi cliccare e digitare attraverso di esso come se non esistesse.

---

## Requisiti

- **OS**: Windows 10 / 11
- **Runtime**: Python 3.9+ ([python.org](https://www.python.org/downloads/))
- **Dipendenze**: Installabili via pip

---

## Installazione Rapida

1. **Clona** o scarica questo repository.
2. Apri un terminale nella cartella del progetto.
3. Installa le librerie necessarie:

``` bash
pip install -r requirements.txt
```

---

## Utilizzo

Avvia il programma con:

``` bash
pythonw dimmer.py
```

*(L'uso di `pythonw` esegue lo script in background senza finestre di terminale).*

Troverai un'icona a forma di luna 🌙 nella **System Tray** (in basso a destra, vicino all'orologio).

### Controlli
Cliccando l'icona nella tray si apre il pannello di controllo temporaneo.

| Elemento | Funzione |
|---|---|
| **Oscuramento** | Intensità del nero (0% - 100%). |
| **Tinta Rossa** | Intensità del filtro luce blu. |
| **⚙ (Ingranaggio)** | Apre il pannello **Impostazioni** (Posizione, Orari, Avvio automatico). |
| **⏸ Pausa** | Disattiva temporaneamente l'overlay (non si riattiverà finché non riprendi manualmente). |
| **✕ Esci** | Termina completamente il processo e chiude l'applicazione. |

### Avvio Automatico
Disponibile solo nelle modalità **tramonto** o **orario fisso**.
1. Apri il pannello (click sulla tray).
2. Clicca sull'icona **Impostazioni** (⚙).
3. Spunta la casella **"🚀 Avvia NightDimmer con Windows"**.
4. Clicca **Salva**.

### Hotkeys Globali
Puoi controllare l'overlay da qualsiasi applicazione:

| Combinazione | Azione |
|---|---|
| `Ctrl` + `Alt` + `↑` | Aumenta oscuramento |
| `Ctrl` + `Alt` + `↓` | Diminuisce oscuramento |
| `Ctrl` + `Alt` + `D` | Toggle On/Off immediato |

---

## Logica di Attivazione (Scheduler)

NightDimmer include un sistema automatico basato sulla tua posizione geografica:

1. **Modalità Tramonto (Default):** Calcola alba e tramonto locali. L'overlay si attiva *N* minuti dopo il tramonto e si spegne *N* minuti dopo l'alba.
2. **Modalità Fissa:** Imposti tu l'orario di ON e OFF (es. 21:00 - 07:00).
3. **Manuale:** Lo scheduler è disattivato; accendi e spegni tu l'overlay.

*Nota: Le coordinate e gli offset si configurano direttamente dal pannello Impostazioni.*

---

## Struttura del Progetto

- `dimmer.py`: Core logic, UI (Tkinter), Scheduler e gestione System Tray.
- `config.toml`: File di configurazione (generato automaticamente al primo avvio).
- `requirements.txt`: Elenco dipendenze.

## Note Tecniche

- **Layering**: L'overlay usa le API Win32 (`WS_EX_TRANSPARENT`, `WS_EX_LAYERED`) per essere invisibile al mouse.
- **Limitazioni Windows**: Alcuni elementi di sistema con priorità "Topmost" assoluta (es. Menu Start, Task Manager, Tooltip di sistema) potrebbero apparire sopra l'overlay. È un comportamento by-design di Windows per garantire la sicurezza.
