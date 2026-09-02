FROM python:3.12-slim
WORKDIR /app
COPY feeds.py cross.py history.py app.py index.html ./
ENV PORT=8787
EXPOSE 8787
CMD ["python", "app.py"]
