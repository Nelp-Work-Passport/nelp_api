# Deploying Nelp on RumptyCloud

The API runs as a **Web Service** built from the `Dockerfile`, using the managed
PostgreSQL database and S3 bucket in the Nelp workspace. RumptyCloud deploys from
GitHub, so the code has to be pushed first.

## 1. Connect GitHub

1. Commit and push this repository to `Nelp-Work-Passport/nelp_api`. Check that
   `git status` does not list `.env.production`.
2. In the [console](https://console.rumptycloud.com), go to **Settings → GitHub** and
   click **Connect GitHub App**. Choose **Only select repositories** and pick `nelp_api`.

## 2. Create the deployment

Go to **Compute → Deployments → New deployment**. Create it in the same workspace as
the database and bucket, because private networking blocks most other workspaces.

| Field | Value |
| --- | --- |
| Repository and branch | `nelp_api`, `main` |
| Root directory | Leave empty |
| Application type | Web Service/Backend |
| Build method | Dockerfile |
| Readiness/health check path | `/health` |
| Port | `8080` |
| Persistent storage | Leave off. Data lives in PostgreSQL and the bucket |
| Environment variables | Add every line from `.env.production` |

Do not add `PORT` as a variable, because the platform sets it from the Port field.
Variables are injected at runtime only, which suits this image.

Before saving, fill in `S3_SECRET_ACCESS_KEY` and replace `ADMIN_PHONE` with a number you
own. Seeding creates the admin only once, so changing these later will not update it.

## 3. Choose a plan

| Plan | Cost | Fit |
| --- | --- | --- |
| Free | $0 | Fine for building. It scales to zero, so the first request after a quiet spell is slow |
| Starter | About $4 a month | Use this for judging. It has 1 GB and stays awake |

## 4. Check it

Open the run under the **Runs** tab, then visit the app URL:

- `/health` should show `"status": "ok"` and `"storage_configured": true`.
- `/api/riders` should list six riders marked as demo.
- `/docs` should open the API docs.

If the build succeeds but it never goes active, read **Runtime Logs**. Check the table
below for the usual causes.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| App exits at start, mentioning secrets or PostgreSQL | A required variable is missing | Compare the Env tab with `.env.production` |
| Never active, timeouts in the logs | Deployment is in a different workspace from the database | Recreate it in the database's workspace |
| `access denied` TLS alert | A firewall policy on the database | Allow the client on port 5432 |
| `/health` shows `storage_configured: false` | An S3 variable is missing | Set it, then redeploy |
| Browser shows CORS errors | `CORS_ORIGINS` is not `*` on the deployment | Set it on the **Env** tab and redeploy |

## 5. Connect the front end

`CORS_ORIGINS=*` lets any site call the API, including local development. Once the front
end has a URL, set this on the **Env** tab and redeploy:

```
PUBLIC_BASE_URL=https://your-front-end-url
```

`PUBLIC_BASE_URL` builds the tracking links in SMS messages, and the front end needs a
page at `/t/{token}` that calls `GET /api/track/{token}`.

## Everyday use

- Every push to `main` deploys automatically. Turn this off during judging under
  **Settings → Auto deploy**, so a late push cannot change the live demo.
- **Rollback** on the deployment page redeploys the previous build without rebuilding.
- New tables appear at startup, but changes to existing tables need a migration first.

## Back up the database

RumptyCloud has no automatic backups, and deleting a database deletes its data for good.
Before the demo and before any risky change, run this with PostgreSQL 17 client tools:

```bash
pg_dump "$DATABASE_URL" > nelp-backup-$(date +%F).sql
```

Backups contain personal data, so keep them out of the repository.

## Before going live with real users

- Replace `CORS_ORIGINS=*` with your front-end URL.
- Set `SEED_ON_STARTUP=false` and remove the demo accounts, whose PIN is public.
- Turn off the demo inbox by finishing the ThriveVas adapter.
- The database password cannot be rotated from the console. If it has been shared
  widely, create a new database and move to it while it is still empty.
