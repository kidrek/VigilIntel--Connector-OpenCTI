FROM python:3.12-alpine

# Install build dependencies (needed for some pycti deps)
RUN apk add --no-cache gcc musl-dev libffi-dev

# Copy requirements first for better layer caching
COPY requirements.txt /opt/opencti-connector-vigilintel/
WORKDIR /opt/opencti-connector-vigilintel
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src /opt/opencti-connector-vigilintel
COPY entrypoint.sh /

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
