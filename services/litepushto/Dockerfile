# services/litepushto/Dockerfile
FROM nginx:alpine

# Serve on 8080 for Cloud Run compatibility
# Build with context at repo root so we can copy the Messier catalog
COPY services/litepushto/nginx.conf /etc/nginx/conf.d/default.conf

# Copy the LitePushTo app to web root
COPY services/litepushto/ /usr/share/nginx/html/

# Also copy the Messier catalog so the app can fetch /data/messier_catalog.json
COPY data/messier_catalog.json /usr/share/nginx/html/data/messier_catalog.json
