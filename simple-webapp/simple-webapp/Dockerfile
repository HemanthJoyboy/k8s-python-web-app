# Small official Python base image
FROM python:3.12-slim

# Work inside /app in the container
WORKDIR /app

# Install dependencies first (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code (changes most often, so it comes last)
COPY app.py .

# Document the port the app listens on
EXPOSE 5000

# Start the app
CMD ["python", "app.py"]
