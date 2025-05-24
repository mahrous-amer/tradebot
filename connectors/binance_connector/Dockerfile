FROM python:3.11-slim

WORKDIR /app

# Create a non-root user
RUN useradd --create-home appuser
# USER appuser # Defer changing user until after all files are copied and permissions set if needed

# Set PYTHONPATH to include /app so common_models can be found
ENV PYTHONPATH="/app:${PYTHONPATH}"
ENV PATH="/home/appuser/.local/bin:${PATH}" # Keep existing PATH modification

# Copy only requirements to leverage Docker cache
COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy common_models package first
COPY --chown=appuser:appuser ../../common_models /app/common_models

# Copy the connector's own application code
COPY --chown=appuser:appuser . .

# Ensure scripts are executable if any are added later
# RUN chmod +x /app/scripts/*

# Switch to the non-root user before running the application
USER appuser

EXPOSE 8001 # Port the Uvicorn will run on inside the container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
