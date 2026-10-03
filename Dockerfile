FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

# Google Chrome stable from the official Google apt repo, plus Xvfb/X11 utils
# and basic fonts so headful rendering (and Turnstile's canvas/font checks)
# works the same way it did in the spike (see docs/spike_turnstile.md).
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget \
        gnupg \
        ca-certificates \
        xvfb \
        xauth \
        x11-utils \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-dejavu-core \
        tesseract-ocr \
        tesseract-ocr-spa \
        libgl1 \
    && wget -q -O /usr/share/keyrings/google-chrome.gpg.key https://dl.google.com/linux/linux_signing_key.pub \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /usr/share/keyrings/google-chrome.gpg.key \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* /usr/share/keyrings/google-chrome.gpg.key

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

COPY src ./src
COPY data ./data
COPY tests ./tests

ENTRYPOINT ["./entrypoint.sh"]
