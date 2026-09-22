# Sicherheits-Kopfzeilen fuer ALLE Antworten (API und Oberflaeche) — "always",
# damit sie auch bei Fehlerseiten gesetzt werden. HSTS ist richtig, weil der
# Besucher die Seite immer ueber HTTPS erreicht (nginx selbst oder der Load
# Balancer terminiert). frame-ancestors laesst sich nur per Header setzen
# (nicht per <meta>).
#
# Pruefbericht 20.09.2026 (DP-14): nginx vererbt add_header NICHT in einen
# Block, der selbst add_header setzt — unter /static/ (Cache-Control) und in
# den Nachbar-Stationen fehlten deshalb Permissions-Policy und Co. Die Zeilen
# stehen jetzt EINMAL hier und werden von default.conf.template und
# hinter-loadbalancer.conf.template im server-Block UND in jeder Location mit
# eigenem add_header eingebunden (include /etc/nginx/sicherheitskopf.inc).
# Eingehaengt ueber docker-compose.yml (proxy.volumes). nginx liest die Datei
# nur beim Start — deploy/rollout.sh rechnet sie deshalb zum Stand der
# Vorlage und erzeugt den Proxy neu, wenn sie sich aendert.
#
# K-20: Stripe ist seit 14.09.2026 entfernt — payment=() statt payment=(self).
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;
add_header Content-Security-Policy "frame-ancestors 'none'" always;
