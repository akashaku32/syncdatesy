FROM node:20 AS builder
WORKDIR /app
# production = default Vite production build
# batch = vite --mode batch → loads .env.batch (gcp.datasyncher.com API/WS)
ARG VITE_BUILD_MODE=production
ARG CACHEBUST=1
COPY package*.json ./
RUN npm ci
COPY . .
RUN echo "cachebust=${CACHEBUST}"
RUN if [ "$VITE_BUILD_MODE" = "batch" ]; then npm run build:batch; else npm run build; fi

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
