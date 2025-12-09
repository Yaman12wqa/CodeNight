# CampuSupport - Kampus Destek & Ticket Yonetim Sistemi

FastAPI tabanli ticket uygulamasi. Ticket CRUD, roller, yorumlar, departman atamasi, haftalik rapor; AI destekli oneri/ozet stubu; agent servis ile otomatik kategori/oncelik/unit karari ve ara mesaj; bildirim webhook; frontend demo; CI ve testler.

## Ozellikler
- Auth: JWT (student/support/department/admin), register/login.
- Ticket CRUD + duzenleme/silme; durumlar: open/in_progress/resolved/closed; oncelik: low/medium/high.
- Departman: ticket listeleme, support'a atama, departman destek listesi, haftalik rapor.
- Destek: durum guncelleme, yorum ekleme, kendi departman ticket'larini goruntuleme.
- Yorum thread; ogrenci kendi ticket yanitlarini gorebilir.
- Filtreleme/siralama; haftalik destek raporu.
- AI: `/ai/suggest` (kategori/oncelik oneri), `/tickets/{id}/ai-insights` (ozet + draft reply). Gercek API yoksa stub/heuristic calisir.
- Agent-service (ayri servis): `/process/{ticket_id}` ajan akisi (AI + calendar stub + ticket update + ara mesaj), `/health`.
- Internal endpointler (shared secret): `/internal/health`, `/internal/tickets/{id}`, `/internal/users/{user_id}/tickets/summary`, `/internal/tickets/{id}/agent-update`.
- Healthcheck: her serviste `/health`.
- Bildirim: ticket resolved oldugunda opsiyonel webhook denemesi (JSON payload).
- Frontend demo (`/frontend`): login, ticket ac/listele/detay, yorum, durum/duzenleme, AI oneri/ozet butonlari.
- Logging: ticket olusturma, AI cagri, bildirim denemesi.
- CI: GitHub Actions (pytest).

## Kurulum ve Calistirma
```bash
python -m venv .venv
.venv\Scripts\activate          # PowerShell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Ortam degiskenleri (ornegin `.env`):
- `SECRET_KEY` (JWT icin, varsayilan dev)
- `DATABASE_URL` (varsayilan `sqlite:///./campusupport.db`)
- `ACCESS_TOKEN_EXPIRE_MINUTES` (varsayilan 1440)
- `AI_API_KEY` ve `AI_API_BASE` (opsiyonel; yoksa stub)
- `NOTIFY_WEBHOOK_URL` (opsiyonel; yoksa bildirim skip edilir)
- `INTERNAL_SECRET` (ticket-service icin internal/agent cagrilar)
- Agent-service icin: `AGENT_SHARED_SECRET`, `TICKET_SERVICE_URL`, `INTERNAL_SECRET`, opsiyonel `CALENDAR_API_BASE`, `AI_API_KEY`, `AI_API_BASE`.

Uygulamayi baslat:
```bash
uvicorn app.main:app --reload
```
- API: http://127.0.0.1:8000/docs
- Frontend: http://127.0.0.1:8000/frontend

## Hizli Akis
1) Kayit:
```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"student@campus.edu\",\"password\":\"Pass1234\"}"
```
2) Token:
```bash
curl -X POST http://127.0.0.1:8000/auth/token \
  -d "username=student@campus.edu&password=Pass1234"
```
3) Ticket ac:
```bash
curl -X POST http://127.0.0.1:8000/tickets \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Wi-Fi cekmiyor\",\"description\":\"Kuzey yurdu 3. kat\",\"department_id\":1,\"priority\":\"high\"}"
```
4) Departman atama:
```bash
curl -X PATCH http://127.0.0.1:8000/tickets/1/assign \
  -H "Authorization: Bearer <DEPARTMENT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"support_user_id\":5}"
```
5) Destek durum/yorum:
```bash
curl -X PATCH http://127.0.0.1:8000/tickets/1/status \
  -H "Authorization: Bearer <SUPPORT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"status\":\"in_progress\"}"
```
```bash
curl -X POST http://127.0.0.1:8000/tickets/1/comments \
  -H "Authorization: Bearer <SUPPORT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"content\":\"Modemi yeniden baslattim, tekrar dener misin?\"}"
```
6) Rapor:
```bash
curl -H "Authorization: Bearer <DEPARTMENT_TOKEN>" \
  "http://127.0.0.1:8000/departments/1/report"
```
7) Duzenle/Sil:
```bash
curl -X PATCH http://127.0.0.1:8000/tickets/1 \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Guncel baslik\",\"priority\":\"medium\"}"
```
```bash
curl -X DELETE http://127.0.0.1:8000/tickets/1 \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```
8) AI oneri/ozet:
```bash
curl -X POST http://127.0.0.1:8000/ai/suggest \
  -H "Content-Type: application/json" \
  -d "{\"description\":\"Yurtta wifi surekli kopuyor\"}"
```
```bash
curl -X GET http://127.0.0.1:8000/tickets/1/ai-insights \
  -H "Authorization: Bearer <TOKEN>"
```
9) Agent calistir (shared secret ile):
```bash
curl -X POST http://127.0.0.1:8001/process/1 \
  -H "X-Agent-Key: agent-secret"
```

## Docker (ticket-service + agent-service + db)
```bash
docker compose up --build
```
- ticket-service: http://localhost:8000 (API/Frontend)
- agent-service: http://localhost:8001 (agent API)
- db: Postgres (campus/campus)

Mimari notlar:
- ticket-service: core CRUD, auth, rapor, AI stub, webhook, internal endpointler, health.
- agent-service: agentic workflow (AI + calendar stub + ticket update + ara mesaj), health, shared secret ile koruma.
- Servisler arasi auth: `INTERNAL_SECRET` (ticket-service internal) ve `X-Agent-Key` (agent-service).

## Klasorler
- `app/` - Ticket-service FastAPI
- `agent_service/` - Agent FastAPI
- `frontend/` - Demo arayuz
- `tests/` - Pytest senaryolari
- `requirements.txt` - Bagimliliklar
- `.github/workflows/ci.yml` - CI pipeline
- `Dockerfile.ticket`, `Dockerfile.agent`, `docker-compose.yml`

## Test ve CI
- Lokal test: `pytest` (ENV `DATABASE_URL` ile test db ayarlanabilir).
- CI: GitHub Actions (install + pytest). Main/dev PR'larinda calisir.

## Notlar
- Varsayilan departmanlar startup'ta eklenir.
- AI ve bildirim entegrasyonu opsiyonel; env yoksa heuristic stub + log.
