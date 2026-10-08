# Chronos

Server locale per orologi virtuali indipendenti. Ogni timeline ha nome, data virtuale, velocità da **0.01× a 1000×** e stato Running/Paused. La dashboard si aggiorna ogni secondo tramite API REST; tutti i calcoli temporali avvengono sul server.

Python 3.13, FastAPI, SQLite e una pagina HTML/CSS/JavaScript senza framework o compilazione. Un solo servizio Docker, con un solo processo Uvicorn. Non genera eventi e non modifica l'orologio di sistema. Al primo avvio l'elenco è vuoto.

## Avvio sul server LAN

Prerequisiti: Docker Engine con plugin Docker Compose, indirizzo LAN statico o prenotato `192.168.1.57`, porta TCP libera e orologio dell'host sincronizzato tramite NTP. Sul server, dalla cartella del progetto:

```sh
cp .env.example .env
```

In PowerShell usare `Copy-Item .env.example .env`. Modificare `.env` se necessario:

```dotenv
CHRONOS_BIND_IP=192.168.1.57
CHRONOS_PORT=8080
CHRONOS_VOLUME_NAME=chronos_data
```

```sh
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 chronos
```

Aprire **http://192.168.1.57:8080** dal server o da un altro dispositivo della LAN. Controllare **http://192.168.1.57:8080/api/health**: HTTP 200 indica servizio e database disponibili; HTTP 503 segnala un problema del database. La porta interna resta 8000.

Il bind richiede che l'IP configurato appartenga all'host Docker. Per una prova su un altro PC impostare il suo IP LAN, oppure `127.0.0.1` per accesso soltanto locale. Se la porta è occupata, cambiare `CHRONOS_PORT` e applicare con `docker compose up -d`. Consentire la porta scelta nel firewall esclusivamente dalla rete privata. Non impostare il bind su `0.0.0.0` per risolvere un IP errato senza valutare le interfacce esposte.

## Utilizzo

- **Nuova timeline:** nome, data e ora iniziali in UTC, velocità e stato iniziale.
- **Pausa/Riprendi:** ferma o riprende l'orologio mantenendo la velocità. Le chiamate ripetute sono idempotenti.
- **Modifica:** rinomina e cambia velocità senza discontinuità; il riposizionamento della data conserva velocità e stato.
- **Elimina:** richiede conferma nell'interfaccia e rimuove soltanto la timeline selezionata.
- **Fuso orario:** la visualizzazione predefinita è Europe/Rome, con passaggio a UTC. I campi di inserimento data/ora sono sempre UTC e sono etichettati come tali.

La dashboard mostra secondi, velocità e stato. In caso di problemi di rete segnala il mancato aggiornamento; il tempo continua ad essere calcolato dal backend alla richiesta successiva.

## Persistenza e modello temporale

Il volume Docker nominato `chronos_data` contiene `/data/timelines.db` e gli eventuali file SQLite `-wal` e `-shm`. Il nome è esplicito e resta stabile anche cambiando la cartella del progetto. Non cambiarlo negli aggiornamenti: un nome nuovo individua un database diverso.

Ogni timeline conserva due ancoraggi UTC in millisecondi, velocità e stato:

```text
running: virtual_now = anchor_virtual_ms + (real_now_ms - anchor_real_ms) × speed
paused:  virtual_now = anchor_virtual_ms
```

Il risultato è arrotondato al millisecondo. Velocità, pausa, ripresa e riposizionamento aggiornano gli ancoraggi in una transazione atomica. SQLite usa WAL e `synchronous=FULL`; database e schema vengono creati solo se mancanti. Le letture non riscrivono continuamente gli orologi.

Le timeline Running includono il tempo reale trascorso a servizio spento, anche se il container viene ricreato. Quelle Paused restano ferme. Non c'è un azzeramento al riavvio. La continuità dipende dall'orologio UTC dell'host: correzioni improvvise dell'ora si riflettono sul tempo virtuale. Configurare NTP sull'host, non nel container.

Le API accettano date ISO 8601 con `Z` o offset esplicito e le normalizzano in UTC, nel calendario gregoriano prolettico, anni 0001–9999. Le operazioni che imposterebbero una data fuori intervallo vengono rifiutate. Se una timeline Running supera l'intervallo con il trascorrere del tempo, le letture restituiscono `virtual_now: null` e una spiegazione in `time_error`: le altre timeline restano consultabili e gli ancoraggi non vengono alterati automaticamente. Per recuperarla, impostare una data rappresentabile tramite `PUT /time` o `PATCH` con `virtual_time`. Mesi e anni non vengono trattati come durate fisse; gli anni bisestili derivano dal calendario. Non è previsto un modello dei leap second.

L'immagine esegue Chronos come UID/GID **10001**; `/data` viene predisposta con questo proprietario durante la build e copiata nel nuovo volume vuoto da Docker. Un volume preesistente mantiene i suoi dati e permessi. Non sostituirlo con un bind mount senza predisporre permessi di scrittura per 10001. Vedere la documentazione sui [volumi Docker](https://docs.docker.com/engine/storage/volumes/).

## Arresto, riavvio e aggiornamento

```sh
docker compose stop
docker compose start
docker compose restart chronos
```

Prima di aggiornare, salvare un backup esterno del database e conservare `.env`. Copiare la nuova versione del codice nella cartella, quindi:

```sh
docker compose up -d --build
docker compose ps
```

Per ricreare il container senza eliminare il volume:

```sh
docker compose up -d --force-recreate
```

Anche `docker compose down` seguito da `docker compose up -d` conserva il volume nominato. **Non usare `docker compose down -v`, `docker volume rm chronos_data` o pulizie dei volumi contenenti i dati:** eliminerebbero la persistenza. Un volume persistente protegge dalle ricreazioni, ma non sostituisce un backup esterno in caso di guasto del disco o eliminazione accidentale.

## Backup coerente con SQLite

La routine usa l'API di backup SQLite, che produce uno snapshot coerente anche mentre il servizio è attivo; non copia soltanto il file `.db` ignorando il WAL. Vedere [SQLite `Connection.backup`](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup).

Scegliere un nome nuovo per ogni backup. Esempio con nome `chronos-backup-01.db`:

```sh
mkdir backups
docker compose exec -T chronos python -m app.backup /data/backups/chronos-backup-01.db
docker compose cp chronos:/data/backups/chronos-backup-01.db ./backups/chronos-backup-01.db
```

In PowerShell creare la cartella con `New-Item -ItemType Directory -Force backups`. La routine crea le directory di destinazione mancanti. Il primo snapshot è nello stesso volume del database: copiarlo sull'host e poi fuori dal server, perché un guasto del volume colpirebbe entrambe le copie. La routine non sovrascrive un file esistente. Occorre spazio libero per un ulteriore database; gestire periodicamente la conservazione dei backup già esportati. Per uno sviluppo locale:

```sh
python -m app.backup backups/chronos-backup-01.db
```

### Ripristino manuale

Il ripristino sostituisce lo stato corrente con lo snapshot scelto. Verificare il file prima di procedere e conservare un backup dello stato corrente. Le timeline Running ripristinate recuperano anche il tempo reale trascorso dagli ancoraggi del backup; quelle Paused mantengono l'istante salvato.

1. Verificare l'integrità del backup con Python locale:

   ```sh
   python -c "import sqlite3; c=sqlite3.connect('file:backups/chronos-backup-01.db?mode=ro', uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
   ```

   Il risultato deve essere `ok`. Usare esclusivamente un backup creato da Chronos con schema compatibile.

2. Arrestare il servizio e copiare il backup nel volume con nome temporaneo. Il container di manutenzione è temporaneo, usa la stessa immagine e non avvia un secondo server:

   ```sh
   docker compose stop chronos
   docker compose run --rm -T --no-deps --entrypoint python -v ./backups:/backup:ro chronos -c "import shutil; shutil.copyfile('/backup/chronos-backup-01.db', '/data/timelines.restore.db')"
   ```

3. Con il servizio ancora fermo, sostituire il database e rimuovere solo gli eventuali file WAL/SHM appartenenti al vecchio database:

   ```sh
   docker compose run --rm -T --no-deps --entrypoint python chronos -c "from pathlib import Path; import os; p=Path('/data'); os.replace(p/'timelines.restore.db', p/'timelines.db'); [(p/n).unlink(missing_ok=True) for n in ('timelines.db-wal','timelines.db-shm')]"
   docker compose up -d
   docker compose ps
   ```

4. Controllare `/api/health`, nomi, velocità e stati nella dashboard. Non copiare backup o rimuovere file WAL/SHM mentre Chronos è attivo.

## Avvio automatico dell'host

`restart: always` fa ripartire il container quando il daemon Docker torna disponibile. Non installa Docker né abilita il daemon all'avvio. Un healthcheck fallito marca il container come `unhealthy`; da solo non provoca un riavvio automatico. Vedere le [politiche di riavvio Docker](https://docs.docker.com/engine/containers/start-containers-automatically/).

Su un host Linux con systemd, un amministratore può abilitare i servizi e verificare la sincronizzazione temporale:

```sh
sudo systemctl enable --now docker.service containerd.service
systemctl is-enabled docker.service
timedatectl status
```

Se NTP è disabilitato, configurare il servizio temporale previsto dalla distribuzione (per esempio `timedatectl set-ntp true` dove supportato), poi verificarne lo stato. La configurazione dell'host è un passo operativo separato. Riferimento: [Docker Engine al boot su Linux](https://docs.docker.com/engine/install/linux-postinstall/#configure-docker-to-start-on-boot-with-systemd).

Su Windows con Docker Desktop, **Start Docker Desktop when you sign in to your computer** avvia Docker al login dell'utente, non garantisce l'avvio dopo un riavvio senza login. Per il requisito di servizio sempre disponibile prima del login, usare un host Linux con Docker Engine o una VM Linux configurata per avviarsi automaticamente. La sola politica del container non può garantire tale requisito su Docker Desktop. Riferimento: [impostazioni Docker Desktop](https://docs.docker.com/desktop/settings-and-maintenance/settings/#general).

## API REST

Base `/api`; richieste e risposte JSON. Gli identificatori sono UUID. Velocità finite tra 0.01 e 1000 incluse; `status` vale `running` o `paused`.

| Metodo | Percorso | Operazione |
|---|---|---|
| GET | `/timelines` | Elenco con ora virtuale corrente |
| POST | `/timelines` | Crea, HTTP 201 |
| GET | `/timelines/{id}` | Dettaglio |
| GET | `/timelines/{id}/now` | `virtual_now`, `real_now`, velocità e stato |
| PATCH | `/timelines/{id}` | Cambia atomicamente `name`, `speed` e/o `virtual_time` |
| PUT | `/timelines/{id}/time` | Imposta `virtual_time` |
| POST | `/timelines/{id}/pause` | Pausa idempotente |
| POST | `/timelines/{id}/resume` | Ripresa idempotente |
| DELETE | `/timelines/{id}` | Elimina, HTTP 204 |
| GET | `/health` | Servizio e database, HTTP 200/503 |

Esempio di creazione:

```http
POST /api/timelines
Content-Type: application/json

{"name":"Anno 2000","start_time":"2000-01-01T00:00:00Z","speed":3,"status":"running"}
```

```json
{"speed":10}
```

Il secondo JSON è un esempio di body per `PATCH /api/timelines/{id}`. Per riposizionare, inviare a `PUT /api/timelines/{id}/time`:

```json
{"virtual_time":"2000-02-29T12:00:00Z"}
```

La dashboard può salvare nome, velocità e nuova data in un unico `PATCH`, così la modifica completa è atomica. `virtual_time` è opzionale: omettendolo, il cambio di velocità preserva l'ora virtuale corrente. Un body vuoto, campi sconosciuti e valori `null` vengono rifiutati.

Date di risposta ISO 8601 UTC, con suffisso `Z`; `virtual_now` può essere `null` nel caso fuori intervallo descritto sopra. `time_error` è `null` quando il calcolo riesce. Dati non validi: HTTP 400/422; timeline inesistente: 404; errore interno: 500. Non c'è cache dell'ora corrente.

## Sviluppo e test

Richiede Python 3.13. Non sono necessari Node.js o strumenti di build frontend.

```sh
python -m venv .venv
```

Attivare con `.venv\Scripts\Activate.ps1` in PowerShell o `source .venv/bin/activate` su Linux, quindi:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Aprire http://127.0.0.1:8000. Il database locale predefinito è `data/timelines.db`; `CHRONOS_DB_PATH` permette di scegliere un altro file. La configurazione `.env` di Compose non viene caricata automaticamente nell'avvio Python diretto.

I test temporali usano un orologio controllato: verificano ore di avanzamento e periodi di spegnimento senza attendere nel tempo reale. Comprendono indipendenza, cambi di velocità, pausa/ripresa, riposizionamento, persistenza su file, bisestili e input non validi. Le prove Docker e sull'host richiedono il server destinazione.

Le dipendenze principali sono fissate a versioni precise in `requirements.txt`; httpx è riservato ai test. Compatibilità Python 3.13 verificabile nelle schede ufficiali [FastAPI 0.135.1](https://pypi.org/project/fastapi/0.135.1/), [Starlette 0.52.1](https://pypi.org/project/starlette/0.52.1/), [Pydantic 2.12.5](https://pypi.org/project/pydantic/2.12.5/) e [Uvicorn 0.42.0](https://pypi.org/project/uvicorn/0.42.0/). Le dipendenze transitive sono risolte da pip; non è incluso un lock completo con hash.

## Verifica sul server destinazione

Questa è una checklist operativa da eseguire sull'host; il codice e i test locali non attestano da soli un reboot fisico o la raggiungibilità LAN.

- [ ] Creare tre timeline con date e velocità diverse; modificare una e verificare le altre.
- [ ] Verificare 3×, cambio da 3× a 10×, pausa/ripresa e riposizionamento (coperti anche da test con orologio controllato).
- [ ] Annotare stati e ancoraggi, eseguire `docker compose restart chronos` e verificare continuità.
- [ ] Arrestare per un intervallo noto: Running recupera intervallo × velocità, Paused resta invariata.
- [ ] Eseguire `docker compose down` e `docker compose up -d`, poi verificare gli stessi UUID e configurazioni.
- [ ] Eseguire un backup esterno e provare un ripristino con dati di prova prima dell'uso operativo.
- [ ] Verificare il passaggio febbraio/marzo in un anno bisestile (anche nei test automatizzati).
- [ ] Riavviare fisicamente il server e verificare Docker e Chronos senza avvio manuale; su Linux anche prima di un login interattivo.
- [ ] Aprire la dashboard da un altro dispositivo LAN e verificare aggiornamento dei secondi e comandi.

## Sicurezza e limiti MVP

**Chiunque raggiunga la porta HTTP può leggere, modificare ed eliminare tutte le timeline.** Non è presente autenticazione. Usare esclusivamente una LAN privata; non pubblicare direttamente il servizio su Internet. Per accesso remoto, prevedere VPN o reverse proxy HTTPS con autenticazione.

Debug e reload sono disabilitati nel container. Il servizio non usa database, font, script o altri servizi esterni per funzionare. Log su stdout/stderr con rotazione Docker: 3 file da 10 MB. File system del container in sola lettura, salvo volume dati e `/tmp` temporanea.

La v1 non include utenti, notifiche, eventi, scheduler, grafici, timeline collegate, velocità negative, WebSocket o sincronizzazione fra server. Non esiste un limite applicativo al numero di timeline; RAM, disco e costo del polling dipendono comunque dall'hardware disponibile.
