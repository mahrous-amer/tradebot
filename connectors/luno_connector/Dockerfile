FROM python:3.11-slim

WORKDIR /app

# Create a non-root user
RUN useradd --create-home appuser
# USER appuser # Defer USER instruction until after files are copied with root permissions

# Copy common models first
COPY --chown=appuser:appuser ../../common_models /app/common_models

# Copy requirements and install them as the appuser
COPY --chown=appuser:appuser requirements.txt .
# USER appuser # Switch to appuser before pip install if installing to user site-packages
RUN pip install --no-cache-dir -r requirements.txt # If installing system-wide as root
# If installing as appuser:
# USER appuser
# RUN pip install --no-cache-dir --user -r requirements.txt
# ENV PATH="/home/appuser/.local/bin:${PATH}" # Add user's local bin to PATH

# Copy the rest of the application code
COPY --chown=appuser:appuser . .

USER appuser # Switch to appuser for running the CMD
ENV PYTHONPATH="/app:${PYTHONPATH}" # Ensure common_models is findable

EXPOSE 8002 # Port for Luno connector API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
