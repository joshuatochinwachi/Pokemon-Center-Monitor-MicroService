# Use Microsoft's official Playwright image which has all browsers and dependencies
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Ensure playwright is installed (though it should be in the base image)
RUN playwright install chromium

# Run the monitor
CMD ["python", "pokemon_monitor.py"]
