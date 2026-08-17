# Deploy Backend

Production: PM2 uvicorn `127.0.0.1:8093` + Nginx `:80`. SSL di GCP Load Balancer.

```bash
cp deploy/.env.production.example .env
chmod +x deploy/*.sh
bash deploy/deploy-prod.sh --migrate --all
```

Ganti `server_name` di `nginx-prod.conf.example`. Lihat README root untuk peta konstanta dan multi-DB.
