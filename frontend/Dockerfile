FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ARG APP_VERSION=v1.0.0
ARG GIT_SHA=local
ENV VITE_APP_VERSION=${APP_VERSION} VITE_GIT_SHA=${GIT_SHA}
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
RUN mkdir -p /data/breakglass
EXPOSE 80
