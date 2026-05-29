docker run -d --name searxng \
  -p 8080:8080 \
  --restart unless-stopped \
  searxng/searxng:latest