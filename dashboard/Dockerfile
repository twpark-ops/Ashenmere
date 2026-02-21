# =============================================================================
# Stage 1: Build React application
# =============================================================================
FROM node:22-alpine AS builder

WORKDIR /app

# Copy dependency files first (layer cache)
COPY dashboard/package.json dashboard/package-lock.json* ./

# Install dependencies
RUN npm ci --ignore-scripts

# Copy source code
COPY dashboard/ .

# Build static assets
RUN npm run build

# =============================================================================
# Stage 2: Serve with nginx
# =============================================================================
FROM nginx:1-alpine AS runtime

# Remove default nginx static content
RUN rm -rf /usr/share/nginx/html/*

# Copy built assets from builder
COPY --from=builder /app/dist /usr/share/nginx/html

# Custom nginx config template for SPA routing + API proxy
COPY dashboard/nginx.conf /etc/nginx/templates/default.conf.template

# Default backend host (override in docker-compose or K8s)
ENV BACKEND_HOST=agentburg-server.agentburg.svc

# Generate default config from template and set permissions
RUN envsubst '${BACKEND_HOST}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

# Non-root user (nginx alpine runs as nginx user by default)
RUN chown -R nginx:nginx /usr/share/nginx/html \
    && chown -R nginx:nginx /var/cache/nginx \
    && chown -R nginx:nginx /var/log/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid \
    && chown -R nginx:nginx /etc/nginx/conf.d \
    && chown -R nginx:nginx /etc/nginx/templates

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/health || exit 1

# Re-run envsubst at startup to pick up runtime BACKEND_HOST overrides, then start nginx
CMD ["/bin/sh", "-c", "envsubst '${BACKEND_HOST}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
