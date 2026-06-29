# ============================================================
# Financial Ledger Mock — Multi-stage Docker Build
# ============================================================

# --- Stage 1: Build ---
FROM eclipse-temurin:21-jdk-jammy AS builder

WORKDIR /build

# Copy Maven wrapper and project definition first (dependency caching)
COPY mvnw mvnw.cmd ./
COPY .mvn/ .mvn/
COPY pom.xml ./
COPY settings-docker.xml /root/.m2/settings.xml

# Download dependencies (cached unless pom.xml changes)
RUN chmod +x mvnw && ./mvnw dependency:go-offline -B

# Copy source code
COPY src/ src/

# Build (skip tests in Docker)
RUN ./mvnw -B package -DskipTests

# --- Stage 2: Runtime ---
FROM eclipse-temurin:21-jre-jammy

WORKDIR /app

# Copy the built JAR
COPY --from=builder /build/target/*.jar app.jar

# Create a non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
