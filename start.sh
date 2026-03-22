#!/bin/bash
cd /home/tinhn/repo/Personal_AI_OS
docker compose up -d
echo "✅ Application started!"
docker compose ps
