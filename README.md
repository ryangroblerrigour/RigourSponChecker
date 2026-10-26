# RigourSponCheck

Codes spontaneous awareness open ends against a code frame. Send the answer text, get back a code per brand mentioned, with NULL for anything not in the frame.

## Request

`POST /code` with header `X-API-Key: <your key>`

```json
{
  "project": "P12345",
  "respid": "1001",
  "text": "DPD, royal mial, dhl and yodel"
}
```

`text` is the one big text box. You can send `answers` (a list of up to 10) instead, or as well. `codeframe` is optional and defaults to `parcel_carriers_uk`.

## Response

```json
{
  "project": "P12345",
  "respid": "1001",
  "codeframe": "parcel_carriers_uk",
  "results": [
    {"verbatim": "DPD",        "code": 1,    "label": "DPD",        "match": "exact"},
    {"verbatim": "royal mial", "code": 3,    "label": "Royal Mail", "match": "fuzzy"},
    {"verbatim": "dhl",        "code": 5,    "label": "DHL E-commerce", "match": "exact"},
    {"verbatim": "yodel",      "code": null, "label": null,         "match": "none"}
  ],
  "codes": [1, 3, 5],
  "codes_string": "1,3,5",
  "unmatched_count": 1
}
```

`codes` is de-duplicated, in the order mentioned. `codes_string` is the same list as text, handy for writing straight into a Confirmit variable.

`match` values: `exact`, `fuzzy` (typo tolerated), `none`.

Purpose: this is a consistency check, not an autocoder. It only returns codes for brands on the listed frame, so spontaneous mentions can be compared against prompted awareness. Brands not on the list (including former names of listed brands) return NULL. "DHL" on its own is coded 5 (DHL E-commerce).

## How matching works

1. The text is split on commas, full stops, slashes, new lines and joining words like "and" / "or".
2. Within each piece, the longest phrase that matches a brand or alias wins ("DPD Local" beats "DPD").
3. Typos are tolerated on a strict basis. 3 letters or fewer must be exact (DPD, UPS, DX, APC). 4 to 5 letters allow 1 typo, 6 or more allow 2. The first letter must always match. If a typo could fit two codes equally, it returns NULL.
4. Anything left over becomes a NULL row ("don't know", "none", "yodel"). Filler words and list numbers are dropped.

## Code frames

Code frames live in `app/codeframes/*.json`. Each code has a label and a list of aliases. Aliases should only be spelling or spacing variants of the listed brand (e.g. "royalmail", "parcel force"), never other brand names. To add a new frame, copy the file, change the codes, and pass its file name as `codeframe` in the request.

## Other endpoints (all need the API key)

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Health check, no key needed |
| `GET /codeframes` | List available code frames |
| `GET /codeframes/{name}` | Show codes in a frame |
| `GET /unmatched?project=P12345` | Most common NULL verbatims, to spot missing aliases |
| `GET /respondent/{project}/{respid}` | Everything logged for a respondent |

## Logging

Every request is stored in two tables: `sponcheck_requests` (one row per call, with the full response) and `sponcheck_mentions` (one row per verbatim). Uses `DATABASE_URL`; without it, falls back to a local SQLite file, which Render wipes on each deploy.

## Deploy on Render

1. Push this folder to a new GitHub repo.
2. In Render, choose New > Blueprint and select the repo. `render.yaml` creates the web service and a Postgres database, and generates an `API_KEY`.
3. Copy the `API_KEY` value from the service's Environment tab.

If you would rather reuse an existing Postgres database, delete the `databases` block and the `fromDatabase` entry, and set `DATABASE_URL` by hand. Tables are prefixed `sponcheck_` so they will not clash.

## Confirmit

Call it from JavaScript on the page after the open end, then write the result into hidden variables:

```javascript
fetch("https://rigoursponcheck.onrender.com/code", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": "YOUR_KEY" },
  body: JSON.stringify({
    project: "P12345",
    respid: respid,        // piped in from the survey
    text: spontAnswer      // the open end text
  })
})
  .then(r => r.json())
  .then(d => { /* write d.codes_string into a hidden question */ });
```

Note: a key used in browser JavaScript is visible to anyone who views the page source. Fine for a POC; for production, restrict `CORS_ORIGINS` to the Confirmit domain and rotate the key per project.

## Run locally

```bash
pip install -r requirements.txt
API_KEY=dev uvicorn app.main:app --reload
pytest
```
