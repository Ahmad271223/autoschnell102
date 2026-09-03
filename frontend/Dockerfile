# AutoSchnell Frontend — React (craco) Build, ausgeliefert von nginx
FROM node:20-slim AS build
WORKDIR /app
COPY package.json yarn.lock* ./
# Streng nach Lockfile — der fruehere '|| yarn install'-Ausweg umging den
# Schutz genau dann, wenn er gebraucht wurde (abweichende Abhaengigkeiten).
RUN yarn install --frozen-lockfile
COPY . .
# Backend-URL wird zur Build-Zeit eingebacken (CRA/craco liest REACT_APP_*).
ARG REACT_APP_BACKEND_URL=/api
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
# CI=true: Warnungen sind Fehler (Audit 09/2026, Punkt 50).
RUN CI=true yarn build

# --- Auslieferung ---
FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
# SPA-Routing: alle Pfade auf index.html (React Router uebernimmt).
# Audit 09/2026 (Punkt 10): Sicherheits-Header auch hier (Verteidigung in der
# Tiefe — der aeussere Proxy setzt sie ebenfalls), gehashte Assets lange
# cachen, index.html nie.
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
  location /static/ { expires 1y; add_header Cache-Control "public, max-age=31536000, immutable" always; try_files $uri =404; }\n\
  location /fonts/ { expires 30d; add_header Cache-Control "public, max-age=2592000" always; try_files $uri =404; }\n\
  location = /index.html { add_header Cache-Control "no-store" always; }\n\
  location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
