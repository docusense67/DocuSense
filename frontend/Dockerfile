FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Static files + HTTPS reverse proxy. Config is mounted from deploy/nginx (see docker-compose.yml).
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
