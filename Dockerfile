FROM python:3.12-slim
RUN pip install --no-cache-dir yt-dlp
WORKDIR /app
ENV MOVIES_BIND=0.0.0.0
EXPOSE 8787
CMD ["python3", "serve.py", "8787"]
