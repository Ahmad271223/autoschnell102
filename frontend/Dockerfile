# AutoSchnell Frontend — React (craco) Build, ausgeliefert von nginx
FROM node:20-slim AS build
WORKDIR /app
COPY package.json yarn.lock* ./
RUN yarn install --frozen-lockfile || yarn install
COPY . .
# Backend-URL wird zur Build-Zeit eingebacken (CRA/craco liest REACT_APP_*).
ARG REACT_APP_BACKEND_URL=/api
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
RUN yarn build

# --- Auslieferung ---
FROM nginx:alpine
COPY --from=build /app/build /usr/share/nginx/html
# SPA-Routing: alle Pfade auf index.html (React Router uebernimmt).
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  location / { try_files $uri /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
