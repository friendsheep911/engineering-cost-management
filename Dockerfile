FROM python:3.12-alpine
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
RUN python tools/build_database.py --out /app/database/info_prices.sqlite
EXPOSE 8080
ENV PORT=8080 INFO_PRICE_DB=/app/database/info_prices.sqlite PROJECT_DB=/app/storage/projects.sqlite
CMD ["python", "server.py"]
