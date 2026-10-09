FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    FOOD_CLASSIFIER_BIND=0.0.0.0 FOOD_CLASSIFIER_DATA_DIR=/var/lib/food-classifier PORT=8011
WORKDIR /app
COPY food-classifier/requirements.txt /app/food-classifier/requirements.txt
RUN pip install --no-cache-dir -r /app/food-classifier/requirements.txt \
    && useradd --uid 10001 --create-home foodtest \
    && mkdir -p /var/lib/food-classifier /app/references \
    && chown -R foodtest:foodtest /var/lib/food-classifier
COPY food-classifier/*.py /app/food-classifier/
COPY food-classifier/web /app/food-classifier/web
COPY food-classifier/resources/inspection-bridges.json /app/food-classifier/resources/inspection-bridges.json
USER foodtest
EXPOSE 8011
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8011')+'/api/health', timeout=3)"
CMD ["python", "-X", "utf8", "food-classifier/server.py"]
