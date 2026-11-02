# YouCam terms check

Read on 19 October 2026 from YouCam's public pages. These notes paraphrase the sources; follow the links for the exact wording, which can change.

## Sources

- [File Retention Period](https://docs.perfectcorp.com/develop/file_retention_period)
- [Rate Limit](https://docs.perfectcorp.com/develop/rate_limit)
- [FAQ](https://docs.perfectcorp.com/develop/faq) and [Error Codes](https://docs.perfectcorp.com/develop/error_codes)
- [Unit System API](https://docs.perfectcorp.com/reference/unit_system)
- [AI Clothes V4 reference](https://docs.perfectcorp.com/reference/ai_clothes/v4.0)
- [API Privacy Policy](https://www.makeupar.com/perfectbeauty/youcam/privacy-policy-api)
- [API Terms of Service](https://www.makeupar.com/perfectbeauty/youcam/terms-of-service-api)

## Retention

- Uploaded files and their file IDs are kept for 30 days. A task ID stays valid for 30 days.
- A result download link lasts 2 hours. Within the 30 days, polling the task ID again gives a fresh link.
- All uploaded and generated images are removed automatically after 30 days.
- The privacy policy says data is generally kept for 30 days after each session, longer only where the law requires it.
- Deletion is offered on request, and "via API where applicable". The developer docs list no deletion endpoint, so assume uploads stay with YouCam for up to 30 days.
- Deleting Rechoice's own copies after 24 hours does not delete YouCam's copies.

## Use of uploaded images

- The privacy policy promises not to use personal information or user content beyond its stated purposes, and names model training as an example.
- It also claims a legitimate interest in product development, internal analytics and content moderation.
- Data is stored on servers in the USA, with support and moderation teams worldwide.
- Face data may be processed to provide the service, but not for facial recognition or identification.
- The terms make the API customer responsible for collecting consent from every person in a photo, because some features read facial geometry.
- The terms make the API customer responsible for not infringing portrait, copyright or trademark rights in uploads. That is why the four labelled catalog photos are sent only in their retouched form.

## Units and billing

- Units are consumed per task. Once used, they cannot be restored under any circumstances.
- Pay-as-you-go units last one year and are non-refundable. Subscription units can be refunded pro rata within 7 days of purchase.
- Neither the docs nor the terms say whether a task that ends in `error` consumes units. Failed-task billing is unconfirmed.
- Rechoice therefore counts failed tasks as charged.
- The read-only unit history endpoint, `GET /s2s/v1.0/client/credit/history`, records each charge with a `target_id` equal to the task ID. After a run, `manage.py run_feasibility --reconcile FILE` replaces the assumption with what was actually charged.
- In the pilot run on 19 October 2026, each successful V4 task appeared in the history as `api_cloth_v4`, charging 2 units. The `target_id` was the task ID followed by a colon and 12 more characters, not the bare task ID. No task failed, so failed-task billing is still unobserved.
- The V4 page lists unit costs only for V2 and V3, 2 units each. The 2-unit V4 price comes from the account's read-only feature-cost check on 18 October 2026, so recheck it before any run.

## Limits and errors

- The API answered HTTP 415 to a GET without `Content-Type: application/json`, even with no body. Every adapter call now sends that header.

- The API allows 250 requests per 300 seconds, per IP address and per API key, with about 5 requests per second recommended. A ten-pair run done one pair at a time stays far below that.
- Polling a task that has timed out returns `InvalidTaskId`, so poll until the task reaches `success` or `error`. The adapter treats a 400 while polling as uncertain and keeps the units reserved.
- Engine errors such as `error_pose`, `error_no_shoulder` and `error_multiple_people` describe unusable inputs. They are recorded as the job's error code.
