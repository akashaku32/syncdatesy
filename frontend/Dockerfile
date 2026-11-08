FROM node:20 AS builder
WORKDIR /app
# production = default bundle (not QA-specific — tag in GCR decides cluster).
# batch = vite --mode batch → loads data-syncher-ui/.env.batch (gcp.datasyncher.com API/WS).
ARG VITE_BUILD_MODE=production
COPY data-syncher-ui/package*.json ./
RUN npm ci
COPY data-syncher-ui/ ./
RUN if [ "$VITE_BUILD_MODE" = "batch" ]; then npm run build:batch; else npm run build; fi

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
