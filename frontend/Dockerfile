# AutoSchnell Frontend — React (Vite) Build, ausgeliefert von nginx
FROM node:20-slim AS build
WORKDIR /app
COPY package.json yarn.lock* ./
# Streng nach Lockfile — der fruehere '|| yarn install'-Ausweg umging den
# Schutz genau dann, wenn er gebraucht wurde (abweichende Abhaengigkeiten).
RUN yarn install --frozen-lockfile
COPY . .
# Backend-URL wird zur Build-Zeit eingebacken (vite.config.mjs liest REACT_APP_*).
# LEER ist der richtige Standard: lib/api.js baut daraus `${BACKEND}/api`.
# Stand hier "/api", rief ein ohne Build-Argument gebautes Image
# "/api/api/..." auf und die ganze Oberflaeche waere tot. docker-compose
# setzt den Wert ohnehin ausdruecklich auf leer — aber ein Image, das man
# von Hand baut, darf nicht kaputt sein (Pruefbericht 09/2026).
ARG REACT_APP_BACKEND_URL=
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
# Runde 31: Fassungs-Stempel "<Commit-Zeit>-<Kurz-SHA>" (deploy/rollout.sh).
# Der Bau-Kontext ist ./frontend — dort gibt es kein .git, deshalb kommt der
# Wert als Build-Argument. Leer = die Oberflaeche prueft nicht auf neue Fassungen.
ARG APP_FASSUNG=
ENV APP_FASSUNG=$APP_FASSUNG
# Vite bricht bei Fehlern ab; CI=true bleibt fuer Werkzeuge, die es auswerten.
RUN CI=true yarn build

# --- Auslieferung ---
FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
# SPA-Routing: alle Pfade auf index.html (React Router uebernimmt).
# Audit 09/2026 (Punkt 10): Sicherheits-Header auch hier (Verteidigung in der
# Tiefe — der aeussere Proxy setzt sie ebenfalls), gehashte Assets lange
# cachen, index.html nie. Installierbare App (09/2026): Service Worker,
# boot.js und Manifest haben keinen Hash im Namen — "no-cache", sonst halten
# Cloudflare (cacht .js standardmaessig) oder der Browser nach einem Update
# die alte Fassung fest.
RUN printf 'server {\n\
  listen 80;\n\
  server_tokens off;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  add_header X-Content-Type-Options "nosniff" always;\n\
  add_header X-Frame-Options "DENY" always;\n\
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n\
  add_header Content-Security-Policy "frame-ancestors '"'"'none'"'"'" always;\n\
  gzip on;\n\
  gzip_types text/plain text/css application/json application/javascript text/javascript image/svg+xml;\n\
  location /static/ { expires 1y; add_header Cache-Control "public, max-age=31536000, immutable" always; try_files $uri /alt$uri @fehlt; }\n\
  location @fehlt { add_header Cache-Control "no-store" always; return 404; }\n\
  location /alt/ { internal; }\n\
  location /fonts/ { expires 30d; add_header Cache-Control "public, max-age=2592000" always; try_files $uri @fehlt; }\n\
  location = /index.html { add_header Cache-Control "no-store" always; }\n\
  location = /service-worker.js { add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options "nosniff" always; }\n\
  location = /boot.js { add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options "nosniff" always; }\n\
  location = /manifest.json { add_header Cache-Control "no-cache" always; add_header X-Content-Type-Options "nosniff" always; }\n\
  location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
