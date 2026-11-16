# eLiberia - going live

The admin console has a **Launch readiness** tab that checks each item below. Settings are environment
variables on the Render service (Dashboard > eliberia-backend > Environment), except `EXPOSE_OTP`,
`DEMO_MODE` and `TASKS_SECRET`, which live in `backend/render.yaml` - change those in the file and push,
because the blueprint overwrites dashboard edits.

1. **Lock the encryption key.** Copy the current `SECRET_KEY` value into a new `DATA_KEY` variable (same
   value). Store it in a password manager. If it is ever lost or changed, encrypted answers and files are
   unreadable; `/api/health` reports `data_key_ok: false` if that happens.
2. **Permanent file storage.** Create a Cloudflare R2 bucket (free tier) and set `STORAGE_BACKEND=s3`,
   `STORAGE_S3_BUCKET`, `STORAGE_S3_ENDPOINT` (`https://<account-id>.r2.cloudflarestorage.com`),
   `STORAGE_S3_ACCESS_KEY`, `STORAGE_S3_SECRET_KEY`. Files uploaded before the switch stay on the old disk.
3. **SMS.** Africa's Talking: `SMS_PROVIDER=africastalking`, `SMS_USERNAME`, `SMS_API_KEY`, optional
   `SMS_SENDER_ID` (`SMS_USERNAME=sandbox` uses their free sandbox). Or Twilio: `SMS_PROVIDER=twilio`,
   `SMS_ACCOUNT_SID`, `SMS_AUTH_TOKEN`, `SMS_FROM`. Then set `EXPOSE_OTP` to `"False"` in render.yaml.
4. **Payments.**
   - MTN MoMo (Collection API): `MTN_MOMO_SUBSCRIPTION_KEY`, `MTN_MOMO_API_USER`, `MTN_MOMO_API_KEY`,
     `MTN_MOMO_ENV` (`sandbox` or `production`), `MTN_MOMO_TARGET` (e.g. `mtnliberia`), `MTN_MOMO_CURRENCY`
     (`EUR` in the sandbox, `USD` or `LRD` live), `MTN_MOMO_CALLBACK_URL` = `<backend>/api/payments/mtn/callback`.
   - Orange Money or an aggregator: `PAYMENT_PROVIDER=live` and `PAYMENT_WEBHOOK_SECRET`; the provider posts
     `{txn_ref, status, amount, provider_ref}` to `<backend>/api/payments/webhook` with header
     `X-Signature` = hex HMAC-SHA256 of the body. Orange Money Liberia's merchant API needs a contract first.
5. **Your own administrator.** Set `BOOTSTRAP_ADMIN_PHONE`, `BOOTSTRAP_ADMIN_NAME`,
   `BOOTSTRAP_ADMIN_PASSWORD` (a temporary password), let it redeploy, sign in and choose a new password,
   then delete `BOOTSTRAP_ADMIN_PASSWORD` and set `DEMO_MODE` to `"False"` in render.yaml. The demo accounts
   below are then disabled.
6. **Daily job and backups.** In the backend GitHub repository add the secrets `DATABASE_URL` (the
   database's external URL), `BACKUP_KEY` (a long passphrase - keep a copy offline), `API_URL` and
   `TASKS_SECRET` (copy it from Render). `.github/workflows/daily.yml` then makes an encrypted backup and
   sends renewal reminders every night. Restore with `python backup.py restore --file ... --yes --replace`.
7. **A database that does not expire.** Render's free PostgreSQL expires 30 days after creation. Move to a
   paid plan, Neon or Supabase, then restore the latest backup into it.
8. **Legal review.** `/privacy` and `/terms` are drafts and must be reviewed before launch.
