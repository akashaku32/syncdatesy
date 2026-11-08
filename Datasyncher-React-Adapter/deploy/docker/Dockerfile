# Build arguments from .env file
ARG PYTHON_VERSION=3.9
ARG ENVIRONMENT=development

FROM python:${PYTHON_VERSION}

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=${ENVIRONMENT}

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy everything to the container
COPY . /app/

# Shared connection library (optional direct Snowflake listing from Django)
RUN pip install --no-cache-dir /app/backend/datasyncher_connections

# Install Django requirements (must include channels for ASGI).
RUN pip install --no-cache-dir -r datasyncher/requirements.txt && \
    pip install django==4.2.26 gunicorn

# Create directories
RUN mkdir -p /app/static /app/media /app/logs

# Set permissions
RUN chown -R www-data:www-data /app && \
    chmod -R 755 /app

# Expose port
EXPOSE 8000

# Set Django settings module
RUN echo "export DJANGO_SETTINGS_MODULE=datasyncher.settings.developer" >> /etc/environment

# Switch to www-data user
USER www-data

# Start dev or prod
CMD bash -c '\
    python manage.py migrate --noinput && \
    if [ "$ENVIRONMENT" = "development" ]; then \
        python manage.py runserver --settings=datasyncher.settings.developer 0.0.0.0:8000; \
    else \
        python manage.py collectstatic --noinput && \
        gunicorn datasyncher.wsgi:application \
            --bind 0.0.0.0:8000 \
            --workers ${GUNICORN_WORKERS:-5} \
            --timeout 259200; \
    fi'
