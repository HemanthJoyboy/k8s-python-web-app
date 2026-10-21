# Simple Docker Web App

## 1. Build the image and run it
```bash
docker build -t simple-webapp:v1 .
docker run -d --name webapp -p 8080:5000 simple-webapp:v1
```
Open http://localhost:8080

## 2. Update the web app
Edit `app.py` (e.g. set VERSION = "2.0" and change MESSAGE), then:
```bash
docker build -t simple-webapp:v2 .       # rebuild image with a new tag
docker stop webapp && docker rm webapp   # remove the old container
docker run -d --name webapp -p 8080:5000 simple-webapp:v2
```
Refresh http://localhost:8080 to see the change.

## Useful commands
```bash
docker images                 # list images
docker ps                     # running containers
docker logs webapp            # view app logs
docker run -d --name webapp -p 8080:5000 simple-webapp:v1   # roll back to v1
```

## Optional: push to Docker Hub
```bash
docker tag simple-webapp:v2 YOUR_USERNAME/simple-webapp:v2
docker push YOUR_USERNAME/simple-webapp:v2
```
