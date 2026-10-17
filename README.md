# Diario pannolino

Web app condivisa (mobile-first) per registrare e monitorare cacca/pipì della bimba:
tipo, consistenza, colore, note, orario, chi ha inserito. Grafico 14 giorni, tempo
dall'ultima cacca, avviso per colori da segnalare (rosso/nero/bianco), export CSV.

Accesso: una password di famiglia + il nome di chi usa l'app (mostrato su ogni voce).
Flask + SQLite, un solo container.

## Deploy su Railway
1. Pubblica questa cartella su un repository GitHub **privato**.
2. Su railway.com: **New Project → Deploy from GitHub repo** e scegli il repository.
   Railway usa il `Dockerfile` e `railway.json` (healthcheck su `/healthz`).
3. Nel servizio, tab **Variables**, aggiungi:
   - `APP_PASSWORD` — password di famiglia, almeno 8 caratteri (meglio lunga)
   - `SECRET_KEY` — stringa casuale: `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `BABY_NAME` — nome mostrato nel titolo (opzionale)
   - `RAILWAY_RUN_UID` = `0` — necessario perché l'app possa scrivere sul volume
4. **Volume (obbligatorio, senza i dati si perdono a ogni deploy):** tasto destro sul
   servizio → **Attach Volume** → mount path `/data`.
5. Tab **Settings → Networking → Generate Domain**: ottieni il link `*.up.railway.app`
   (HTTPS incluso). Aprilo dal telefono e usa "Aggiungi a schermata Home".

## Avvio con Docker Compose (self-hosted)
    cp .env.example .env && chmod 600 .env   # compila APP_PASSWORD e SECRET_KEY
    docker compose up -d --build
Ascolta solo su 127.0.0.1:8123: mettilo dietro un reverse proxy con HTTPS
(`COOKIE_SECURE=0` solo per prove in locale senza HTTPS).

## Dati e backup
SQLite in `/data/tracker.db`. Il link "Scarica tutto (CSV)" nella pagina è un backup
leggibile: scaricalo ogni tanto.

L'app non è un dispositivo medico; l'avviso sui colori è solo un promemoria.
