FROM python:3.12-slim
WORKDIR /app
COPY app.py page.html /app/
ENV PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-u", "/app/app.py"]
