# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# Copy project files for dependency installation
COPY pyproject.toml .
COPY src/ src/

# Install the package and its dependencies to /install prefix
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy source code (needed for the entry point to find the module)
COPY src/ src/

# Create data directory for SQLite database and logs
RUN mkdir -p /app/data

# Mount volume for persistent data (SQLite + logs)
VOLUME ["/app/data"]

# Expose the API port
EXPOSE 19210

# Run the keeplink entry point
CMD ["keeplink"]
