# Use the official Python image (adjust the version if you are using something other than 3.11)
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install the exact system dependency your bot needs for Pillow RAQM support
RUN apt-get update && apt-get install -y \
    libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

# Copy your requirements first (this optimizes Docker caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your bot's source code into the container
COPY . .

# Run the script that starts your bot (change "main.py" to your actual entry file)
CMD ["python", "main.py"]
