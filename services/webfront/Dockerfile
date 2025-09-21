# services/webfront/Dockerfile
FROM nginx:alpine

# Cloud Run expects the app to listen on 8080
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Your static site (index.html plus images)
COPY . /usr/share/nginx/html

# nginx:alpine's default CMD is fine